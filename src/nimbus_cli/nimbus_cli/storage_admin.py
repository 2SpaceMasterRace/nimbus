"""Administrative storage operations: bulk key renames, etc.

This module contains one-shot admin tooling that lives outside the runtime hot
path. It talks to S3 via boto3 directly (server-side ``CopyObject``) rather
than going through the ``CloudStorageClient`` protocol — see the system-design
discussion in DESIGN.md / SYSTEM_DESIGN.md §4.x for rationale. The short
version: until the protocol grows a ``copy_object`` method, the production
hot path uses download→upload and admin tools use boto3 directly.

For objects > 5 GiB the single-part ``CopyObject`` is rejected by AWS; we fall
back to ``CreateMultipartUpload`` + ``UploadPartCopy`` automatically.  The
bucket should carry an ``AbortIncompleteMultipartUpload`` lifecycle rule
(``DaysAfterInitiation: 1``) so orphaned parts from crashes are cleaned up
automatically.

The boto3 client is configured in adaptive retry mode so ``503 SlowDown``
responses are handled transparently without extra application-level logic.
SSE-KMS keys are propagated from source to destination so the copy does not
silently downgrade encryption.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:  # pragma: no cover - hint-only
    from collections.abc import Iterable, Mapping

    from nimbus_cli.config import NimbusProfile


storage_app = typer.Typer(help="Administrative storage operations.")


# Slack legacy ID pattern. Slack workspace IDs start with T, conversation
# IDs with C/G/D, file IDs with F. New-format readable paths use
# lowercase slugs that won't accidentally match the prefix-by-letter rule.
_LEGACY_KEY_PATTERN = re.compile(
    r"^slack/(?P<team>T[A-Z0-9]+)/(?P<channel>[CGD][A-Z0-9]+)/"
    r"(?P<file_id>F[A-Z0-9]+)/(?P<filename>.+)$"
)


@dataclass(frozen=True, slots=True)
class LegacyKey:
    """Parsed legacy ID-based Slack object key."""

    full: str
    team_id: str
    channel_id: str
    file_id: str
    filename: str


@dataclass(frozen=True, slots=True)
class IdMapping:
    """User-provided mapping from Slack IDs to readable names."""

    teams: Mapping[str, str]
    channels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RenamePlan:
    """One proposed rename: source key → destination key, with rationale."""

    source: str
    destination: str
    reason: str  # "ok" | "no_team_in_mapping" | "no_channel_in_mapping"


def parse_legacy_key(key: str) -> LegacyKey | None:
    """Parse a legacy Slack S3 key. Returns None if the key isn't legacy-shaped."""
    match = _LEGACY_KEY_PATTERN.match(key)
    if match is None:
        return None
    return LegacyKey(
        full=key,
        team_id=match.group("team"),
        channel_id=match.group("channel"),
        file_id=match.group("file_id"),
        filename=match.group("filename"),
    )


def _safe_segment(name: str, *, fallback: str) -> str:
    """Mirror of nimbus_slack._safe_path_segment so prefixes match new uploads."""
    cleaned = "".join("_" if c in "/\\\r\n\t" else c for c in name).strip()
    return cleaned or fallback


def build_readable_key(legacy: LegacyKey, mapping: IdMapping) -> RenamePlan:
    """Build the readable key for one legacy key, or describe why we can't."""
    team_name = mapping.teams.get(legacy.team_id)
    channel_name = mapping.channels.get(legacy.channel_id)
    if team_name is None:
        return RenamePlan(
            source=legacy.full, destination=legacy.full, reason="no_team_in_mapping"
        )
    if channel_name is None:
        return RenamePlan(
            source=legacy.full,
            destination=legacy.full,
            reason="no_channel_in_mapping",
        )
    safe_team = _safe_segment(team_name, fallback=legacy.team_id)
    safe_channel = _safe_segment(channel_name, fallback=legacy.channel_id)
    safe_file = _safe_segment(legacy.filename, fallback=legacy.file_id)
    new_key = f"slack/{safe_team}/{safe_channel}/{legacy.file_id}/{safe_file}"
    return RenamePlan(source=legacy.full, destination=new_key, reason="ok")


def load_mapping(path: Path) -> IdMapping:
    """Load a teams/channels mapping from a JSON or TOML file."""
    raw = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(raw)
    elif suffix == ".toml":
        data = tomllib.loads(raw.decode("utf-8"))
    else:
        msg = f"unsupported mapping format {suffix!r}; use .json or .toml"
        raise ValueError(msg)
    teams = data.get("teams", {})
    channels = data.get("channels", {})
    if not isinstance(teams, dict) or not isinstance(channels, dict):
        msg = "mapping must contain 'teams' and 'channels' tables of strings"
        raise ValueError(msg)  # noqa: TRY004 - ValueError matches loader contract
    return IdMapping(
        teams={str(k): str(v) for k, v in teams.items()},
        channels={str(k): str(v) for k, v in channels.items()},
    )


def plan_renames(
    keys: Iterable[str], mapping: IdMapping
) -> tuple[list[RenamePlan], list[str], list[str]]:
    """Classify every key into renameable / skipped-incomplete / non-legacy."""
    actionable: list[RenamePlan] = []
    skipped: list[str] = []
    not_legacy: list[str] = []
    for key in keys:
        legacy = parse_legacy_key(key)
        if legacy is None:
            not_legacy.append(key)
            continue
        plan = build_readable_key(legacy, mapping)
        if plan.reason == "ok":
            actionable.append(plan)
        else:
            skipped.append(key)
    return actionable, skipped, not_legacy


def _render_summary(  # noqa: PLR0913 - reporting helper; flat kwargs are fine
    console: Console,
    *,
    bucket: str,
    actionable: list[RenamePlan],
    skipped: list[str],
    not_legacy: list[str],
    apply_changes: bool,
) -> None:
    console.print(
        f"[bold]Bucket[/] s3://{bucket}/  "
        f"legacy={len(actionable) + len(skipped)}  "
        f"already-readable={len(not_legacy)}"
    )
    if actionable:
        table = Table(
            title=("Renamed" if apply_changes else "Would rename"),
            show_lines=False,
            pad_edge=False,
        )
        table.add_column("from", overflow="fold")
        table.add_column("to", overflow="fold")
        for plan in actionable:
            table.add_row(plan.source, plan.destination)
        console.print(table)
    if skipped:
        console.print(
            f"[yellow]Skipped {len(skipped)} keys[/] — missing mapping entries. "
            "Add team/channel IDs to your mapping file and re-run."
        )
    if not apply_changes and actionable:
        console.print("[dim]Dry run. Re-run with --apply to perform the renames.[/]")


def _list_s3_keys(*, s3: Any, bucket: str, prefix: str) -> list[str]:  # noqa: ANN401 - boto3 client has no precise stub
    """Yield every object key under prefix using a paginator."""
    keys: list[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


_GIB_5 = 5 * 1024**3  # S3 single-part CopyObject ceiling in bytes
_PART_SIZE = 100 * 1024**2  # 100 MiB per part → 1 TiB max at 10 000 parts


def _chunk_ranges(size: int, part_size: int = _PART_SIZE) -> list[tuple[int, int]]:
    """Return inclusive (start, end) byte ranges for UploadPartCopy."""
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < size:
        end = min(start + part_size - 1, size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def _single_part_copy(
    *,
    s3: Any,  # noqa: ANN401 - boto3 client has no precise stub
    bucket: str,
    plan: RenamePlan,
    kms_key_id: str | None,
) -> None:
    """Issue one CopyObject request for objects at or below the 5 GiB ceiling."""
    kwargs: dict[str, Any] = {
        "Bucket": bucket,
        "Key": plan.destination,
        "CopySource": {"Bucket": bucket, "Key": plan.source},
        "MetadataDirective": "COPY",
    }
    if kms_key_id:
        # Explicitly re-specify the source KMS key so the destination is not
        # silently re-encrypted with the bucket default (or downgraded to
        # SSE-S3 if no bucket default is set).
        kwargs["ServerSideEncryption"] = "aws:kms"
        kwargs["SSEKMSKeyId"] = kms_key_id
    s3.copy_object(**kwargs)


def _multipart_copy(
    *,
    s3: Any,  # noqa: ANN401 - boto3 client has no precise stub
    bucket: str,
    plan: RenamePlan,
    size: int,
    kms_key_id: str | None,
) -> None:
    """Copy an object above the 5 GiB CopyObject ceiling via multipart copy.

    Always aborts the in-progress upload on failure so parts do not
    accumulate; the bucket lifecycle rule is a belt-and-suspenders backstop.
    """
    create_kwargs: dict[str, Any] = {"Bucket": bucket, "Key": plan.destination}
    if kms_key_id:
        create_kwargs["ServerSideEncryption"] = "aws:kms"
        create_kwargs["SSEKMSKeyId"] = kms_key_id

    mpu = s3.create_multipart_upload(**create_kwargs)
    upload_id: str = mpu["UploadId"]
    parts: list[dict[str, Any]] = []
    try:
        for part_no, (start, end) in enumerate(_chunk_ranges(size), 1):
            resp = s3.upload_part_copy(
                Bucket=bucket,
                Key=plan.destination,
                UploadId=upload_id,
                PartNumber=part_no,
                CopySource={"Bucket": bucket, "Key": plan.source},
                CopySourceRange=f"bytes={start}-{end}",
            )
            parts.append(
                {"PartNumber": part_no, "ETag": resp["CopyPartResult"]["ETag"]}
            )
        s3.complete_multipart_upload(
            Bucket=bucket,
            Key=plan.destination,
            UploadId=upload_id,
            MultipartUpload={"Parts": parts},
        )
    except Exception:
        s3.abort_multipart_upload(
            Bucket=bucket,
            Key=plan.destination,
            UploadId=upload_id,
        )
        raise


def _server_side_rename(
    *,
    s3: Any,  # noqa: ANN401 - boto3 client has no precise stub
    bucket: str,
    plan: RenamePlan,
) -> None:
    """Server-side rename: copy then delete source. No bytes transit through us.

    Branches on object size: single-part CopyObject for objects ≤ 5 GiB,
    multipart UploadPartCopy above that.  Propagates the source SSE-KMS key
    to the destination so encryption is not silently downgraded.
    """
    head: dict[str, Any] = s3.head_object(Bucket=bucket, Key=plan.source)
    size: int = head["ContentLength"]
    kms_key_id: str | None = head.get("SSEKMSKeyId")

    if size <= _GIB_5:
        _single_part_copy(s3=s3, bucket=bucket, plan=plan, kms_key_id=kms_key_id)
    else:
        _multipart_copy(
            s3=s3, bucket=bucket, plan=plan, size=size, kms_key_id=kms_key_id
        )

    s3.delete_object(Bucket=bucket, Key=plan.source)


def _resolve_bucket(profile_obj: NimbusProfile, override: str | None) -> str:
    if override:
        return override
    if profile_obj.storage_container:
        return profile_obj.storage_container
    msg = (
        "no bucket configured. Set storage_container on the profile, pass "
        "--bucket, or set AWS_BUCKET_NAME in the environment."
    )
    raise typer.BadParameter(msg)


def _build_boto3_client(profile_obj: NimbusProfile) -> Any:  # noqa: ANN401 - boto3
    """Build a boto3 s3 client using profile credentials + env fallbacks.

    Uses adaptive retry mode so ``503 SlowDown`` responses from S3 prefix
    partition throttling are handled transparently; the SDK backs off at the
    request rate rather than requiring application-level retry logic.
    """
    import boto3  # noqa: PLC0415 - lazy import keeps cli startup cheap
    from botocore.config import Config  # noqa: PLC0415

    from nimbus_cli.secrets import NimbusSecrets  # noqa: PLC0415

    home_env = os.environ.get("NIMBUS_HOME")
    home = Path(home_env) if home_env else Path.home() / ".nimbus"
    secrets = NimbusSecrets(home)
    access = os.environ.get("AWS_ACCESS_KEY_ID") or secrets.get(
        profile=profile_obj.name, kind="aws_access_key_id"
    )
    secret = os.environ.get("AWS_SECRET_ACCESS_KEY") or secrets.get(
        profile=profile_obj.name, kind="aws_secret_access_key"
    )
    token = os.environ.get("AWS_SESSION_TOKEN") or secrets.get(
        profile=profile_obj.name, kind="aws_session_token"
    )
    region = (
        profile_obj.aws_region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    return boto3.client(
        "s3",
        region_name=region,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        aws_session_token=token,
        config=Config(retries={"mode": "adaptive", "max_attempts": 10}),
    )


@storage_app.command("rename-readable")
def storage_rename_readable(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    bucket: Annotated[
        str | None, typer.Option("--bucket", help="Override the profile's bucket.")
    ] = None,
    mapping_file: Annotated[
        Path | None,
        typer.Option(
            "--mapping",
            help=(
                "TOML or JSON file with [teams] and [channels] tables mapping "
                "Slack IDs to readable names."
            ),
        ),
    ] = None,
    prefix: Annotated[
        str, typer.Option("--prefix", help="Restrict scan to this key prefix.")
    ] = "slack/",
    apply_changes: Annotated[  # noqa: FBT002 - typer flag-style signature
        bool,
        typer.Option(
            "--apply", help="Actually rename. Without this flag, prints a dry run."
        ),
    ] = False,
) -> None:
    """Rename legacy ID-based Slack S3 keys to human-readable folder names.

    Old: slack/T089A399PQT/C0B1XKBS5UP/F0B2P7UNGJH/VarshaXH.java
    New: slack/nimbus-team/general/F0B2P7UNGJH/VarshaXH.java

    Uses server-side ``s3:CopyObject`` — no bytes transit through this process.
    """
    from nimbus_cli.cli import (  # noqa: PLC0415
        _exit,
        _load_dotenv_best_effort,
        _resolve_profile,
    )

    _load_dotenv_best_effort()
    console = Console()
    profile_obj = _resolve_profile(profile)

    if mapping_file is None:
        missing_msg = (
            "--mapping <path> is required. The file must contain [teams] and "
            "[channels] tables mapping Slack workspace/channel IDs to readable "
            "names."
        )
        raise _exit(missing_msg, code=2)
    try:
        mapping = load_mapping(mapping_file)
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        load_msg = f"could not load mapping {mapping_file}: {exc}"
        raise _exit(load_msg, code=2) from exc

    bucket_name = _resolve_bucket(profile_obj, bucket)
    s3 = _build_boto3_client(profile_obj)

    console.print(f"[dim]Listing objects under s3://{bucket_name}/{prefix} …[/]")
    keys = _list_s3_keys(s3=s3, bucket=bucket_name, prefix=prefix)
    actionable, skipped, not_legacy = plan_renames(keys, mapping)

    _render_summary(
        console,
        bucket=bucket_name,
        actionable=actionable,
        skipped=skipped,
        not_legacy=not_legacy,
        apply_changes=apply_changes,
    )

    if not apply_changes or not actionable:
        return

    from nimbus_cli.ui import make_progress_bar  # noqa: PLC0415

    failures: list[tuple[str, str]] = []
    progress = make_progress_bar()
    task_id = progress.add_task(
        "Renaming…",
        total=len(actionable),
        detail="",
    )
    with progress:
        for plan in actionable:
            try:
                _server_side_rename(s3=s3, bucket=bucket_name, plan=plan)
            except Exception as exc:  # noqa: BLE001 - boto3 raises ClientError + many subtypes
                failures.append((plan.source, str(exc)))
                progress.update(task_id, detail=f"✗ {plan.source}")
            progress.advance(task_id)

    succeeded = len(actionable) - len(failures)
    console.print(
        f"[bold green]Renamed {succeeded} of {len(actionable)}.[/]"
        if not failures
        else f"[bold yellow]Renamed {succeeded} of {len(actionable)}; "
        f"{len(failures)} failed.[/]"
    )
    for src, exc_msg in failures:
        console.print(f"[red]  failed[/] {src}: {exc_msg}")
    if failures:
        raise typer.Exit(code=1)
