"""Python-only Nimbus CLI for local runtime and remote server profiles."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, NoReturn, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping
    from datetime import datetime

    from cloud_storage_api.client import CloudStorageClient

import httpx
import typer
from nimbus_runtime.capabilities import (
    CapabilitySpec,
    CapabilityStatus,
    CapabilitySurface,
    all_capabilities,
    get_capability,
)
from nimbus_runtime.cleanup import build_cleanup_plan_candidates
from nimbus_runtime.domain import (
    ApprovalChoice,
    Artifact,
    Generation,
    GenerationManifest,
    MigrationDecisionPacket,
    ObjectPointer,
    Plan,
    PlanStatus,
    PlanTransition,
    ProofReceipt,
    ProviderHealthReport,
    Task,
    TaskStatus,
    TaskTransition,
    TenantIdentity,
    VerifiedActor,
)
from nimbus_runtime.evidence import (
    compact_evidence_records,
    evidence_bundle_to_json,
    evidence_preview_to_json,
    evidence_record_to_json,
    export_artifact_payload,
    preview_artifact,
)
from nimbus_runtime.generations import (
    FileGenerationStore,
    FileProtectedRootStore,
    create_generation,
    diff_generation_manifests,
    verify_generation_manifest,
)
from nimbus_runtime.healing import (
    ReplicaLane,
    apply_missing_replica_repairs,
    evaluate_replica_lane,
    replica_lane_id,
)
from nimbus_runtime.learning import (
    CapabilityDelta,
    CapabilityDeltaKind,
    LearningSignalOutcome,
    LearningSignalSource,
    PolicyPatch,
    PolicyPatchProposal,
    PolicyVersionBinding,
    propose_policy_patch,
    record_learning_signal,
)
from nimbus_runtime.learning_store import FilePolicyPatchStore
from nimbus_runtime.models import ChatTurnInput, ChatTurnResult
from nimbus_runtime.proof import digest_value, to_jsonable, validate_proof_receipt_links
from nimbus_runtime.provider_capabilities import (
    ProviderCapability,
    discover_provider_capabilities,
)
from nimbus_runtime.provider_health import (
    create_provider_health_artifact,
    run_provider_health_probes,
)
from nimbus_runtime.replay import export_trace, replay_trace, runtime_status_spec
from nimbus_runtime.runtime import load_session_usage
from nimbus_runtime.stacks import FileStorageStackStore, StorageStackState
from nimbus_runtime.stores import (
    FileApprovalStore,
    FileArtifactStore,
    FilePlanStore,
    FileSessionEventStore,
    FileTaskStore,
)
from rich.console import Console, Group
from rich.live import Live
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from ai_client_api import AIClientError
from nimbus_cli import ui
from nimbus_cli.auth import (
    UNSUPPORTED_BEARER_REMOTE_AUTH,
    encode_json_body,
    remote_auth_headers,
)
from nimbus_cli.config import (
    DEFAULT_MODEL,
    DEFAULT_OPENROUTER_BASE_URL,
    DEFAULT_PROFILE,
    DEFAULT_REMOTE_PATH,
    ConfigStore,
    NimbusConfig,
    NimbusProfile,
    RemoteAuthKind,
    SessionRecord,
    default_session_dir,
)
from nimbus_cli.runtime import build_local_runtime
from nimbus_cli.secrets import NimbusSecrets

_APP_HELP = "Nimbus — local and remote AI system CLI."
_ERROR_STYLE = "bold red"
_INFO_STYLE = "dim"
_ACCENT_STYLE = "cyan"
_REMOTE_TIMEOUT_SECONDS = 60.0
_HTTP_SERVER_ERROR_MIN = 500
_BALANCED_QUOTE_MIN_LENGTH = 2
_TASK_INTENT_PREVIEW_CHARS = 60
_OPENROUTER_SECRET_KIND = "openrouter_api_key"  # noqa: S105 - Key name, not a value.
_AWS_ACCESS_KEY_SECRET_KIND = "aws_access_key_id"  # noqa: S105 - Key name only.
_AWS_SECRET_KEY_SECRET_KIND = "aws_secret_access_key"  # noqa: S105 - Key name only.
_AWS_SESSION_TOKEN_SECRET_KIND = "aws_session_token"  # noqa: S105 - Key name only.
_OPENROUTER_ENV = "OPENROUTER_API_KEY"
_AWS_ACCESS_KEY_ENV = "AWS_ACCESS_KEY_ID"
_AWS_SECRET_KEY_ENV = "AWS_SECRET_ACCESS_KEY"  # noqa: S105 - Env var name only.
_AWS_SESSION_TOKEN_ENV = "AWS_SESSION_TOKEN"  # noqa: S105 - Env var name only.
_AWS_REGION_ENV = "AWS_REGION"
_AWS_DEFAULT_REGION_ENV = "AWS_DEFAULT_REGION"
_NIMBUS_CONTAINER_ENV = "NIMBUS_CONTAINER"
_AWS_BUCKET_ENV = "AWS_BUCKET_NAME"
_NIMBUS_ENV_FILE_ENV = "NIMBUS_ENV_FILE"
_RECOGNIZED_CREDENTIAL_ENV_NAMES = (
    _OPENROUTER_ENV,
    _AWS_ACCESS_KEY_ENV,
    _AWS_SECRET_KEY_ENV,
    _AWS_SESSION_TOKEN_ENV,
    _AWS_REGION_ENV,
    _AWS_DEFAULT_REGION_ENV,
    _NIMBUS_CONTAINER_ENV,
    _AWS_BUCKET_ENV,
)
_DOTENV_PRIORITY_NAMES = (
    "credentials.env",
    ".env",
)


@dataclass(frozen=True, slots=True)
class _ProfileSpan:
    """One measured CLI operation."""

    name: str
    start_ns: int
    end_ns: int
    detail: Mapping[str, object]

    @property
    def duration_ms(self) -> float:
        return (self.end_ns - self.start_ns) / 1_000_000


@dataclass(slots=True)
class _ProfileTrace:
    """Per-command timing trace rendered when --profile-timing is enabled."""

    enabled: bool
    mode: str = "half"
    started_ns: int = 0
    ended_ns: int = 0
    spans: list[_ProfileSpan] = field(default_factory=list)

    def __post_init__(self) -> None:
        now = time.perf_counter_ns()
        self.started_ns = now
        self.ended_ns = now

    @contextmanager
    def span(self, name: str, **detail: object) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        start_ns = time.perf_counter_ns()
        try:
            yield
        finally:
            end_ns = time.perf_counter_ns()
            self.spans.append(
                _ProfileSpan(
                    name=name,
                    start_ns=start_ns,
                    end_ns=end_ns,
                    detail=detail,
                )
            )
            self.ended_ns = end_ns

    @property
    def total_ms(self) -> float:
        end_ns = self.ended_ns or time.perf_counter_ns()
        return (end_ns - self.started_ns) / 1_000_000


def _render_profile_trace(*, console: Console, trace: _ProfileTrace) -> None:
    """Print a timing breakdown table when --profile-timing is enabled."""
    if not trace.enabled or not trace.spans:
        return
    if trace.mode == "hud":
        _render_profile_hud(console=console, trace=trace)
        return
    if trace.mode == "waterfall":
        _render_profile_waterfall(console=console, trace=trace)
        return
    table = Table(
        title=f"Profile {trace.mode.upper()}  •  {trace.total_ms:.1f} ms total",
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
    )
    table.add_column("span", style="cyan", no_wrap=True)
    table.add_column("ms", justify="right")
    if trace.mode == "full":
        table.add_column("kind")
    table.add_column("detail", style=_INFO_STYLE)
    for span in trace.spans:
        detail_str = "  ".join(f"{k}={v}" for k, v in span.detail.items())
        if trace.mode == "full":
            table.add_row(
                span.name,
                f"{span.duration_ms:.1f}",
                _profile_span_kind(span),
                detail_str,
            )
        else:
            table.add_row(span.name, f"{span.duration_ms:.1f}", detail_str)
    console.print(table)
    if trace.mode == "full":
        console.print(
            Text(
                "opaque = provider/network internals not directly visible to Python",
                style=ui.MUTED,
            )
        )


def _render_profile_hud(*, console: Console, trace: _ProfileTrace) -> None:
    """Render a compact game-style profiler HUD."""
    total = max(trace.total_ms, 1.0)
    table = Table(
        title=f"Profile HUD  •  {trace.total_ms:.1f} ms total",
        show_header=False,
        box=None,
        padding=(0, 1),
    )
    table.add_column("span", style="cyan", no_wrap=True)
    table.add_column("bar")
    table.add_column("ms", justify="right")
    for span in trace.spans[:8]:
        table.add_row(
            span.name,
            _profile_bar(span.duration_ms / total),
            f"{span.duration_ms:.0f} ms",
        )
    bottleneck = max(trace.spans, key=lambda span: span.duration_ms)
    console.print(table)
    console.print(
        Text(
            f"bottleneck: {bottleneck.name} ({bottleneck.duration_ms:.1f} ms)",
            style=ui.MUTED,
        )
    )


def _render_profile_waterfall(*, console: Console, trace: _ProfileTrace) -> None:
    """Render spans as offsets from command start."""
    table = Table(
        title=f"Profile WATERFALL  •  {trace.total_ms:.1f} ms total",
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
    )
    table.add_column("offset", justify="right")
    table.add_column("duration", justify="right")
    table.add_column("span", style="cyan")
    for span in trace.spans:
        offset_ms = (span.start_ns - trace.started_ns) / 1_000_000
        table.add_row(f"{offset_ms:.1f} ms", f"{span.duration_ms:.1f} ms", span.name)
    console.print(table)


def _profile_span_kind(span: _ProfileSpan) -> str:
    if "remote" in span.name or "runtime.run_chat_turn" in span.name:
        return "opaque"
    return "measured"


def _profile_bar(ratio: float) -> str:
    width = 16
    filled = max(1, min(width, round(ratio * width)))
    return "[" + ("#" * filled) + ("." * (width - filled)) + "]"


def _normalize_profile_timing_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"full", "hud", "waterfall", "half"}:
        return normalized
    return "half"


app = typer.Typer(
    name="nimbus",
    help=_APP_HELP,
    add_completion=False,
    rich_markup_mode="rich",
)
setup_app = typer.Typer(help="Create or update Nimbus profiles.")
auth_app = typer.Typer(help="Configure and inspect Nimbus auth state.")
auth_profile_app = typer.Typer(help="Manage Nimbus auth profiles.")
task_app = typer.Typer(help="Inspect and manage background Nimbus tasks.")
workspace_app = typer.Typer(help="Inspect workspace state at any past timestamp.")
tools_app = typer.Typer(help="Inspect Nimbus runtime capabilities.")
root_app = typer.Typer(help="Protect S3-backed storage roots for snapshots.")
generation_app = typer.Typer(help="Create, list, and diff immutable root snapshots.")
manifest_app = typer.Typer(help="List and verify snapshot manifest artifacts.")
migration_app = typer.Typer(help="Evaluate S3 replica or region migration packets.")
heal_app = typer.Typer(help="Verify protected-root health and recommend repair steps.")
stack_app = typer.Typer(help="Review, restack, and apply storage change stacks.")
policy_app = typer.Typer(help="Review learning-derived runtime policy patches.")
policy_patch_app = typer.Typer(help="Create, inspect, accept, and reject patches.")
trace_app = typer.Typer(help="Export and replay deterministic runtime traces.")
provider_app = typer.Typer(help="Inspect storage provider health from live probes.")
spec_app = typer.Typer(help="Inspect executable Nimbus runtime specifications.")
evidence_app = typer.Typer(help="Export, preview, and compact evidence payloads.")
app.add_typer(setup_app, name="setup")
app.add_typer(auth_app, name="auth")
auth_app.add_typer(auth_profile_app, name="profile")
app.add_typer(task_app, name="task")
app.add_typer(workspace_app, name="workspace")
app.add_typer(tools_app, name="tools")
app.add_typer(root_app, name="root")
app.add_typer(generation_app, name="generation")
app.add_typer(manifest_app, name="manifest")
app.add_typer(migration_app, name="migration")
app.add_typer(heal_app, name="heal")
app.add_typer(stack_app, name="stack")
app.add_typer(policy_app, name="policy")
policy_app.add_typer(policy_patch_app, name="patch")
app.add_typer(trace_app, name="trace")
app.add_typer(provider_app, name="provider")
app.add_typer(spec_app, name="spec")
app.add_typer(evidence_app, name="evidence")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Start the default local REPL when the bare ``nimbus`` command is used."""
    if ctx.invoked_subcommand is not None:
        return
    _load_dotenv_and_announce()
    _run_chat_command(
        message=None,
        profile_name=None,
        session_external_id=None,
        resume_last=False,
        no_tools=False,
        profile_timing=False,
        profile_timing_mode="half",
    )


@auth_app.callback(invoke_without_command=True)
def auth(ctx: typer.Context) -> None:
    """Run the default local auth flow when ``nimbus auth`` has no subcommand."""
    if ctx.invoked_subcommand is not None:
        return
    _load_dotenv_and_announce()
    _configure_local_profile(
        profile=DEFAULT_PROFILE,
        api_key=None,
        model=None,
        fallback_model=None,
        container=None,
        aws_region=None,
        session_dir=None,
        configure_aws=_env_has_aws_keypair(),
        aws_access_key_id=None,
        aws_secret_access_key=None,
        aws_session_token=None,
    )


@setup_app.command("local")
def setup_local(
    profile: Annotated[str, typer.Option("--profile", "-p")] = "local",
    api_key: Annotated[
        str | None,
        typer.Option("--openrouter-key", help="OpenRouter API key to store."),
    ] = None,
    model: Annotated[str, typer.Option("--model")] = DEFAULT_MODEL,
    fallback_model: Annotated[
        str | None,
        typer.Option("--fallback", help="Fallback model, or 'none'."),
    ] = None,
    container: Annotated[
        str | None,
        typer.Option("--container", help="Pinned cloud-storage container."),
    ] = None,
    aws_region: Annotated[
        str | None,
        typer.Option("--aws-region", help="AWS region for local storage tools."),
    ] = None,
    session_dir: Annotated[
        Path | None,
        typer.Option("--session-dir", help="Local Nimbus session directory."),
    ] = None,
) -> None:
    """Onboard a local profile that runs ``NimbusRuntime`` in-process."""
    _load_dotenv_best_effort()
    _configure_local_profile(
        profile=profile,
        api_key=api_key,
        model=model,
        fallback_model=fallback_model,
        container=container,
        aws_region=aws_region,
        session_dir=session_dir,
        configure_aws=False,
        aws_access_key_id=None,
        aws_secret_access_key=None,
        aws_session_token=None,
    )


@auth_app.command("local")
def auth_local(
    profile: Annotated[str, typer.Option("--profile", "-p")] = "local",
    api_key: Annotated[
        str | None,
        typer.Option("--openrouter-key", help="OpenRouter API key to store."),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the local model for this profile."),
    ] = None,
    fallback_model: Annotated[
        str | None,
        typer.Option("--fallback", help="Fallback model, or 'none'."),
    ] = None,
    container: Annotated[
        str | None,
        typer.Option("--container", help="Pinned S3 bucket/container for tools."),
    ] = None,
    aws_region: Annotated[
        str | None,
        typer.Option("--aws-region", help="AWS region for local storage tools."),
    ] = None,
    aws_access_key_id: Annotated[
        str | None,
        typer.Option("--aws-access-key-id", help="AWS access key ID to store."),
    ] = None,
    aws_secret_access_key: Annotated[
        str | None,
        typer.Option(
            "--aws-secret-access-key",
            help="AWS secret access key to store.",
        ),
    ] = None,
    aws_session_token: Annotated[
        str | None,
        typer.Option("--aws-session-token", help="Optional AWS session token."),
    ] = None,
    configure_aws: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--aws/--no-aws",
            help="Prompt for and store AWS credentials for storage tools.",
        ),
    ] = True,
    session_dir: Annotated[
        Path | None,
        typer.Option("--session-dir", help="Local Nimbus session directory."),
    ] = None,
) -> None:
    """Store local CLI credentials for OpenRouter and optional AWS S3 tools."""
    _load_dotenv_best_effort()
    _configure_local_profile(
        profile=profile,
        api_key=api_key,
        model=model,
        fallback_model=fallback_model,
        container=container,
        aws_region=aws_region,
        session_dir=session_dir,
        configure_aws=configure_aws,
        aws_access_key_id=aws_access_key_id,
        aws_secret_access_key=aws_secret_access_key,
        aws_session_token=aws_session_token,
    )


@auth_app.command("paste")
def auth_paste(
    credentials: Annotated[
        str | None,
        typer.Argument(
            metavar="CREDENTIALS",
            help=(
                "Optional KEY=value payload. Omit it to paste multiline "
                "credentials on stdin."
            ),
        ),
    ] = None,
    profile: Annotated[str, typer.Option("--profile", "-p")] = DEFAULT_PROFILE,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Override the local model for this profile."),
    ] = None,
    fallback_model: Annotated[
        str | None,
        typer.Option("--fallback", help="Fallback model, or 'none'."),
    ] = None,
    session_dir: Annotated[
        Path | None,
        typer.Option("--session-dir", help="Local Nimbus session directory."),
    ] = None,
) -> None:
    """Import a dotenv-style credential paste without printing secret values."""
    _load_dotenv_best_effort()
    values = _parse_credentials_payload(_credential_payload(credentials))
    imported = _recognized_credential_names(values)
    if not imported:
        raise _exit(
            "credential paste did not contain any recognized Nimbus fields",
            code=2,
        )
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    openrouter_key = values.get(_OPENROUTER_ENV)
    if (
        _secret_value(
            explicit=openrouter_key,
            existing=secrets.get(profile=profile, kind=_OPENROUTER_SECRET_KIND),
            env_names=(_OPENROUTER_ENV,),
        )
        is None
    ):
        raise _exit(
            "pasted credentials did not include OPENROUTER_API_KEY "
            "and no existing key is configured",
            code=2,
        )

    _configure_local_profile(
        profile=profile,
        api_key=openrouter_key,
        model=model,
        fallback_model=fallback_model,
        container=values.get(_NIMBUS_CONTAINER_ENV) or values.get(_AWS_BUCKET_ENV),
        aws_region=values.get(_AWS_REGION_ENV) or values.get(_AWS_DEFAULT_REGION_ENV),
        session_dir=session_dir,
        configure_aws=_payload_has_aws_keypair(values),
        aws_access_key_id=values.get(_AWS_ACCESS_KEY_ENV),
        aws_secret_access_key=values.get(_AWS_SECRET_KEY_ENV),
        aws_session_token=values.get(_AWS_SESSION_TOKEN_ENV),
    )
    Console().print(
        f"[{_ACCENT_STYLE}]imported credential fields[/] {', '.join(imported)}"
    )


def _configure_local_profile(
    *,
    profile: str,
    api_key: str | None,
    model: str | None,
    fallback_model: str | None,
    container: str | None,
    aws_region: str | None,
    session_dir: Path | None,
    configure_aws: bool,
    aws_access_key_id: str | None,
    aws_secret_access_key: str | None,
    aws_session_token: str | None,
) -> None:
    """Create or update a local profile and its local credential material."""
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    config = store.load()
    existing = _existing_local_profile(config=config, profile=profile)
    selected_key = _secret_value(
        explicit=api_key,
        existing=secrets.get(profile=profile, kind=_OPENROUTER_SECRET_KIND),
        env_names=(_OPENROUTER_ENV,),
    )
    if selected_key is None:
        selected_key = Prompt.ask("OpenRouter API key", password=True, default="")
    selected_region = (
        aws_region
        or (existing.aws_region if existing else None)
        or _env_first(_AWS_REGION_ENV, _AWS_DEFAULT_REGION_ENV)
    )
    selected_container = (
        container
        or (existing.storage_container if existing else None)
        or _env_first(_NIMBUS_CONTAINER_ENV, _AWS_BUCKET_ENV)
    )
    profile_obj = NimbusProfile(
        name=profile,
        mode="local",
        model=model or (existing.model if existing else DEFAULT_MODEL),
        fallback_model=_resolve_fallback_model(
            fallback_model=fallback_model,
            existing=existing,
        ),
        openrouter_base_url=DEFAULT_OPENROUTER_BASE_URL,
        storage_container=selected_container,
        aws_region=selected_region,
        session_dir=str(session_dir.expanduser())
        if session_dir
        else (existing.session_dir if existing else None),
    )
    config = config.with_profile(profile_obj)
    store.save(config)
    if selected_key:
        secrets.set(profile=profile, kind=_OPENROUTER_SECRET_KIND, value=selected_key)
    if configure_aws:
        _store_aws_credentials(
            profile=profile,
            secrets=secrets,
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
        )
    Console().print(f"[{_ACCENT_STYLE}]saved local profile[/] {profile!r}")


@setup_app.command("remote")
def setup_remote(
    profile: Annotated[str, typer.Option("--profile", "-p")] = "remote",
    base_url: Annotated[str, typer.Option("--base-url")] = "",
    auth: Annotated[RemoteAuthKind, typer.Option("--auth")] = "hmac",
    token: Annotated[
        str | None,
        typer.Option(
            "--token",
            help="Deprecated; bearer auth is not accepted by /ai/chat/turn.",
        ),
    ] = None,
    signing_secret: Annotated[
        str | None,
        typer.Option("--signing-secret", help="HMAC secret for --auth hmac."),
    ] = None,
) -> None:
    """Onboard a remote/self-hosted Nimbus server profile."""
    if not base_url:
        raise _exit("remote profiles require --base-url", code=2)
    if auth == "bearer":
        raise _exit(UNSUPPORTED_BEARER_REMOTE_AUTH, code=2)
    if token:
        raise _exit(
            "--token is only for bearer auth; use --signing-secret for HMAC",
            code=2,
        )
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    secret_value = signing_secret
    if secret_value is None:
        secret_value = Prompt.ask("HMAC signing secret", password=True, default="")
    profile_obj = NimbusProfile(
        name=profile,
        mode="remote",
        remote_base_url=base_url.rstrip("/"),
        remote_auth=auth,
    )
    config = store.load().with_profile(profile_obj)
    store.save(config)
    if secret_value:
        secrets.set(profile=profile, kind="remote_signing_secret", value=secret_value)
    Console().print(f"[{_ACCENT_STYLE}]saved remote profile[/] {profile!r}")


@auth_app.command("status")
def auth_status() -> None:
    """Show configured profiles and whether required secrets exist."""
    _load_dotenv_best_effort()
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    config = store.load()
    table = Table(title="Nimbus profiles")
    table.add_column("profile", style=f"bold {_ACCENT_STYLE}")
    table.add_column("mode")
    table.add_column("target")
    table.add_column("auth")
    table.add_column("provider secret")
    table.add_column("storage")
    for profile in config.profiles.values():
        table.add_row(
            profile.name,
            profile.mode,
            _profile_target(profile),
            _profile_auth_label(profile),
            _profile_secret_status(profile, secrets),
            _profile_storage_status(profile, secrets),
        )
    Console().print(table)


@auth_app.command("doctor")
def auth_doctor(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
) -> None:
    """Run the same end-to-end profile checks as top-level ``nimbus doctor``."""
    _run_doctor_command(profile=profile)


@auth_profile_app.command("list")
def auth_profile_list() -> None:
    """List configured profiles and show which one is active."""
    _load_dotenv_best_effort()
    store = ConfigStore()
    config = store.load()
    console = Console()
    if not config.profiles:
        ui.info(console, "no Nimbus profiles configured")
        console.print("Run `uv run nimbus auth` to create a local profile.")
        return

    table = Table(title="Nimbus profile selection")
    table.add_column("active")
    table.add_column("profile", style=f"bold {_ACCENT_STYLE}")
    table.add_column("mode")
    table.add_column("target")
    for profile in config.profiles.values():
        table.add_row(
            "yes" if profile.name == config.active_profile else "",
            profile.name,
            profile.mode,
            _profile_target(profile),
        )
    console.print(table)


@auth_profile_app.command("use")
def auth_profile_use(
    profile: Annotated[str, typer.Argument(metavar="PROFILE")],
) -> None:
    """Set the active Nimbus profile used when ``--profile`` is omitted."""
    store = ConfigStore()
    config = store.load()
    try:
        updated = config.with_active_profile(profile)
    except KeyError as exc:
        raise _exit(str(exc), code=2) from exc
    store.save(updated)
    ui.success(Console(), f"active profile -> {profile}")


@app.command()
def chat(
    message: Annotated[str | None, typer.Argument()] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="External readable session id to use."),
    ] = None,
    resume_last: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--resume-last",
            help="Resume the profile's last CLI session instead of starting fresh.",
        ),
    ] = False,
    no_tools: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--no-tools/--with-tools", help="Disable storage tools locally."),
    ] = False,
    profile_timing: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--profile-timing/--no-profile-timing",
            help="Print a precise operation timing breakdown after each turn.",
        ),
    ] = False,
    profile_timings: Annotated[
        str,
        typer.Option(
            "--profile-timings",
            help="Profiler mode: off, half, full, hud, or waterfall.",
        ),
    ] = "off",
) -> None:
    """Send one message, or start a small REPL when no message is supplied."""
    _load_dotenv_best_effort()
    _run_chat_command(
        message=message,
        profile_name=profile,
        session_external_id=session,
        resume_last=resume_last,
        no_tools=no_tools,
        profile_timing=profile_timing or profile_timings.lower() != "off",
        profile_timing_mode=_normalize_profile_timing_mode(profile_timings),
    )


@app.command()
def resume(
    message: Annotated[str | None, typer.Argument()] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    no_tools: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--no-tools/--with-tools", help="Disable storage tools locally."),
    ] = False,
    profile_timing: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--profile-timing/--no-profile-timing",
            help="Print a precise operation timing breakdown after each turn.",
        ),
    ] = False,
    profile_timings: Annotated[
        str,
        typer.Option(
            "--profile-timings",
            help="Profiler mode: off, half, full, hud, or waterfall.",
        ),
    ] = "off",
) -> None:
    """Resume the active profile's last session."""
    _load_dotenv_best_effort()
    _run_chat_command(
        message=message,
        profile_name=profile,
        session_external_id=None,
        resume_last=True,
        no_tools=no_tools,
        profile_timing=profile_timing or profile_timings.lower() != "off",
        profile_timing_mode=_normalize_profile_timing_mode(profile_timings),
    )


@app.command(name="doctor")
def doctor(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
) -> None:
    """Diagnose a Nimbus profile end-to-end.

    Checks, in order:
      1. Profile exists and the basic fields are populated.
      2. OpenRouter API key is present in the configured secret store.
      3. Session directory is writable (for local profiles).
      4. AWS credentials and bucket access (when a storage container is set).
      5. Reachability of the remote server (for remote profiles).

    Each check prints a single ✓/✗ line so failures are easy to spot. Exit
    code is 0 when all checks pass, 1 otherwise.
    """
    _run_doctor_command(profile=profile)


def _run_doctor_command(*, profile: str | None) -> None:
    """Run profile diagnostics and render the shared doctor output."""
    _load_dotenv_best_effort()
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    console = Console()
    config = store.load()

    try:
        target = config.profile(profile)
    except KeyError:
        _print_welcome_panel(console=console, requested=profile)
        raise typer.Exit(1) from None

    console.print(ui.card(_render_doctor_intro(target), title="Nimbus  •  doctor"))

    failed = 0
    for line, ok in _run_doctor_checks(profile=target, secrets=secrets):
        if ok:
            console.print(Text(f"  {ui.ICON_OK} ", style=ui.SUCCESS) + line)
        else:
            console.print(Text(f"  {ui.ICON_FAIL} ", style=ui.DANGER) + line)
            failed += 1

    console.print()
    if failed:
        ui.error(
            console,
            f"{failed} check(s) failed",
            hint=(
                "run `nimbus auth status` to inspect, or `nimbus auth local` to repair."
            ),
        )
        raise typer.Exit(1)
    ui.success(console, "all checks passed")


def _render_doctor_intro(profile: NimbusProfile) -> Text:
    """One-line intro at the top of the doctor card."""
    intro = Text()
    intro.append(f"profile  {profile.name}", style="bold")
    intro.append(f"   mode  {profile.mode}\n", style=ui.MUTED)
    if profile.mode == "local":
        intro.append(f"model  {profile.model}\n", style=ui.MUTED)
        if profile.storage_container:
            intro.append(
                f"storage  {profile.storage_container}\n",
                style=ui.MUTED,
            )
    else:
        intro.append(f"server  {profile.remote_base_url or '—'}\n", style=ui.MUTED)
    return intro


def _run_doctor_checks(
    *,
    profile: NimbusProfile,
    secrets: NimbusSecrets,
) -> list[tuple[Text, bool]]:
    """Run the diagnostic checks and return (label, ok) pairs in display order."""
    results: list[tuple[Text, bool]] = []

    # 1. Profile fields look valid.
    if profile.mode == "local":
        results.append((Text(f"profile {profile.name!r} is local"), True))
    elif profile.mode == "remote":
        ok = bool(profile.remote_base_url)
        label = (
            Text("remote profile has base URL")
            if ok
            else Text(
                "remote profile is missing --base-url",
                style=ui.DANGER,
            )
        )
        results.append((label, ok))

    # 2. OpenRouter key (local profiles only).
    if profile.mode == "local":
        api_key = secrets.get(profile=profile.name, kind="openrouter_api_key")
        env_fallback = bool(os.environ.get("OPENROUTER_API_KEY"))
        label = (
            Text("OpenRouter API key (keyring)")
            if api_key
            else (
                Text("OpenRouter API key (env)")
                if env_fallback
                else Text("OpenRouter API key missing", style=ui.DANGER)
            )
        )
        results.append((label, bool(api_key) or env_fallback))

    # 3. Session dir is writable.
    session_dir = Path(profile.session_dir or str(default_session_dir())).expanduser()
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        probe = session_dir / ".doctor-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        results.append((Text(f"session dir writable  {session_dir}"), True))
    except OSError as exc:
        results.append(
            (Text(f"session dir not writable: {exc}", style=ui.DANGER), False)
        )

    # 4. Storage container, when configured.
    if profile.mode == "local" and profile.storage_container:
        has_keys = bool(
            secrets.get(profile=profile.name, kind="aws_access_key_id")
            or os.environ.get("AWS_ACCESS_KEY_ID")
        )
        if not has_keys:
            results.append(
                (
                    Text(
                        f"storage container set but AWS credentials missing  "
                        f"({profile.storage_container})",
                        style=ui.DANGER,
                    ),
                    False,
                )
            )
        else:
            results.append(
                (
                    Text(f"AWS credentials present  ({profile.storage_container})"),
                    True,
                )
            )

    # 5. Remote profile reachability — opt-in HEAD probe so doctor stays fast.
    if profile.mode == "remote" and profile.remote_base_url:
        results.append(_check_remote_reachability(profile))

    return results


def _check_remote_reachability(profile: NimbusProfile) -> tuple[Text, bool]:
    """Probe the remote Nimbus server's /health endpoint (5s timeout)."""
    if profile.remote_base_url is None:
        return Text("remote profile is missing base URL", style=ui.DANGER), False
    url = f"{profile.remote_base_url.rstrip('/')}/health"
    try:
        response = httpx.get(url, timeout=5.0)
    except httpx.HTTPError as exc:
        return Text(f"remote {url} unreachable: {exc}", style=ui.DANGER), False
    else:
        ok = response.status_code < _HTTP_SERVER_ERROR_MIN
        label = (
            Text(f"remote {url} reachable ({response.status_code})")
            if ok
            else Text(f"remote {url} returned {response.status_code}", style=ui.DANGER)
        )
        return label, ok


@app.command(name="model")
def pick_model(
    new_model: Annotated[
        str | None,
        typer.Argument(
            metavar="MODEL_ID",
            help="Set the model directly without the picker.",
        ),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
) -> None:
    """Switch the model for a local profile (interactive picker by default).

    With no MODEL_ID, opens an arrow-key picker grouped by Free / Paid /
    Custom. Pass a model ID to set it directly without the UI, e.g.::

        nimbus model anthropic/claude-3-5-sonnet
        nimbus model openai/gpt-4o --profile prod
    """
    _load_dotenv_best_effort()
    store = ConfigStore()
    config = store.load()
    console = Console()

    try:
        target = config.profile(profile)
    except KeyError:
        _print_welcome_panel(console=console, requested=profile)
        raise typer.Exit(0) from None

    if target.mode != "local":
        ui.error(
            console,
            f"profile {target.name!r} is remote — model is set server-side",
            hint="Use `nimbus setup remote` to change auth, or pick a local profile.",
        )
        raise typer.Exit(1)

    if new_model is None:
        new_model = _interactive_model_picker(console=console, current=target.model)
    if new_model is None:
        ui.info(console, "model unchanged")
        return

    updated = NimbusProfile(
        name=target.name,
        mode=target.mode,
        model=new_model,
        fallback_model=target.fallback_model,
        openrouter_base_url=target.openrouter_base_url,
        storage_container=target.storage_container,
        aws_region=target.aws_region,
        session_dir=target.session_dir,
        remote_base_url=target.remote_base_url,
        remote_auth=target.remote_auth,
    )
    config = config.with_profile(updated)
    store.save(config)
    ui.success(console, f"model updated → {new_model}  (profile {target.name!r})")


def _interactive_model_picker(*, console: Console, current: str) -> str | None:
    """Show the curated model catalogue and return the chosen model ID.

    Returns ``None`` when the user cancels or selects the current model.
    Returns the custom-entered string when the user picks ``Enter model ID…``.
    """
    from nimbus_cli import picker
    from nimbus_cli.models_catalog import (
        CUSTOM_VALUE,
        MODEL_CATALOG,
    )

    options: list[ui.SelectOption] = [
        ui.SelectOption(
            label=m.label,
            value=m.id,
            description=m.description,
            group=m.group,
        )
        for m in MODEL_CATALOG
    ]
    options.append(
        ui.SelectOption(
            label="Enter model ID…",
            value=CUSTOM_VALUE,
            description="any OpenRouter model identifier",
            group="Custom",
        )
    )

    # Default selection: the current model if it's in the catalogue.
    default_index = next(
        (i for i, opt in enumerate(options) if opt.value == current),
        0,
    )
    title = f"Switch model — current: {current}"
    chosen = picker.select_one(
        options,
        title=title,
        console=console,
        default_index=default_index,
    )
    if chosen is None:
        return None
    if chosen == CUSTOM_VALUE:
        custom = Prompt.ask(
            "Model ID (e.g. openai/gpt-4o)",
            default="",
            console=console,
        ).strip()
        return custom or None
    if chosen == current:
        return None
    return chosen


def _run_chat_command(
    *,
    message: str | None,
    profile_name: str | None,
    session_external_id: str | None,
    resume_last: bool,
    no_tools: bool,
    profile_timing: bool,
    profile_timing_mode: str,
) -> None:
    trace = _ProfileTrace(enabled=profile_timing, mode=profile_timing_mode)
    with trace.span("cli.config_store.init"):
        store = ConfigStore()
        secrets = NimbusSecrets(store.home)
    console = Console()
    with trace.span("cli.config.load"):
        config = store.load()
    with trace.span("cli.profile.bootstrap_env", requested_profile=profile_name or ""):
        config = _bootstrap_default_local_profile_from_env(
            config=config,
            profile_name=profile_name,
        )
    try:
        with trace.span("cli.profile.resolve", requested_profile=profile_name or ""):
            profile = config.profile(profile_name)
        with trace.span(
            "cli.session.resolve",
            profile=profile.name,
            resume_last=resume_last,
            explicit_session=bool(session_external_id),
        ):
            config, session = config.resolve_session(
                profile_name=profile.name,
                external_id=session_external_id,
                resume_last=resume_last,
            )
    except KeyError:
        _print_welcome_panel(console=console, requested=profile_name)
        raise typer.Exit(0) from None
    except ValueError as exc:
        raise _exit(str(exc), code=2) from exc
    with trace.span("cli.config.save"):
        store.save(config)
    with trace.span("cli.render.session_banner", profile=profile.name):
        _print_session_banner(console=console, profile=profile, session=session)
    if message is not None:
        _run_one_message(
            console=console,
            profile=profile,
            secrets=secrets,
            session=session,
            message=message,
            no_tools=no_tools,
            trace=trace,
        )
        _render_profile_trace(console=console, trace=trace)
        return
    _setup_readline_history(history_path=store.home / "repl_history")
    while True:
        try:
            prompt_text = Text("nimbus", style=f"bold {_ACCENT_STYLE}")
            if profile.mode == "local" and profile.model:
                short_model = profile.model.split("/")[-1]
                prompt_text.append(f" [{short_model}]", style=_INFO_STYLE)
            line = Prompt.ask(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not line:
            continue
        if line.startswith("/"):
            outcome = _handle_slash_command(
                console=console,
                line=line,
                profile=profile,
                session=session,
                config=config,
                store=store,
            )
            if outcome.status == "exit":
                return
            if outcome.session is not None:
                session = outcome.session
            if outcome.profile is not None:
                profile = outcome.profile
            if outcome.status != "passthrough":
                continue
        _run_one_message(
            console=console,
            profile=profile,
            secrets=secrets,
            session=session,
            message=line,
            no_tools=no_tools,
            trace=trace,
        )
        _render_profile_trace(console=console, trace=trace)
        trace = _ProfileTrace(enabled=profile_timing, mode=profile_timing_mode)


def _run_one_message(
    *,
    console: Console,
    profile: NimbusProfile,
    secrets: NimbusSecrets,
    session: SessionRecord,
    message: str,
    no_tools: bool,
    trace: _ProfileTrace,
) -> None:
    with trace.span(
        "cli.turn.build",
        profile=profile.name,
        mode=profile.mode,
        message_chars=len(message),
    ):
        turn = _turn_input(profile=profile, session=session, message=message)
    if profile.mode == "local":
        with trace.span(
            "local.runtime.build",
            profile=profile.name,
            model=profile.model,
            storage_tools=not no_tools,
        ):
            runtime = build_local_runtime(
                profile=profile,
                secrets=secrets,
                no_tools=no_tools,
            )
        try:
            asyncio.run(
                _run_local_turn(
                    console=console,
                    runtime=runtime,
                    turn=turn,
                    trace=trace,
                )
            )
        except AIClientError as exc:
            raise _exit(str(exc), code=1) from exc
        return
    _send_remote_turn(
        console=console,
        profile=profile,
        secrets=secrets,
        turn=turn,
        trace=trace,
    )


async def _run_local_turn(
    *,
    console: Console,
    runtime: object,
    turn: ChatTurnInput,
    trace: _ProfileTrace,
) -> None:
    """Render one local runtime turn through the full wrapper contract.

    Wraps the (potentially slow) AI call in a live spinner so the REPL never
    appears frozen. The spinner is transient — Rich removes it from the
    scrollback when the call completes, so the final rendered result is the
    only thing the user sees. Confirmation prompts, action summaries, and
    artifact links are rendered through the shared design system, not just
    `result.text`.
    """
    span_ctx = trace.span(
        "local.runtime.run_chat_turn", conversation_id=turn.conversation_id
    )
    with span_ctx, ui.thinking(console, "thinking…"):
        result = await runtime.run_chat_turn(turn)  # type: ignore[attr-defined]
    if not isinstance(result, ChatTurnResult):
        msg = "local Nimbus runtime returned an invalid turn result"
        raise TypeError(msg)
    with trace.span(
        "cli.render_result",
        outcome=result.outcome,
        actions=len(result.actions),
        artifacts=len(result.artifacts),
        model=result.model,
        steps=result.steps,
    ):
        ui.render_result(
            console,
            text=result.text,
            outcome=result.outcome,
            confirmation=result.confirmation,
            actions=result.actions,
            artifacts=result.artifacts,
        )


def _send_remote_turn(
    *,
    console: Console,
    profile: NimbusProfile,
    secrets: NimbusSecrets,
    turn: ChatTurnInput,
    trace: _ProfileTrace,
) -> None:
    """Send one turn to a remote Nimbus server and render the response.

    Wraps the HTTP call in a transient spinner so the user sees activity
    instead of a frozen terminal during the round-trip.
    """
    if not profile.remote_base_url:
        raise _exit(f"profile {profile.name!r} is missing remote_base_url", code=2)
    with trace.span("remote.body.encode", path=DEFAULT_REMOTE_PATH):
        body = encode_json_body(_turn_body(turn))
    try:
        with trace.span("remote.auth.sign", auth=profile.remote_auth or ""):
            headers = remote_auth_headers(profile=profile, secrets=secrets, body=body)
    except ValueError as exc:
        raise _exit(str(exc), code=2) from exc
    url = f"{profile.remote_base_url}{DEFAULT_REMOTE_PATH}"
    try:
        with ui.thinking(console, "sending to remote…"):
            with trace.span(
                "remote.http.post",
                url=url,
                bytes=len(body),
                timeout_s=_REMOTE_TIMEOUT_SECONDS,
            ):
                response = httpx.post(
                    url,
                    content=body,
                    headers=headers,
                    timeout=_REMOTE_TIMEOUT_SECONDS,
                )
            with trace.span(
                "remote.http.raise_for_status",
                status_code=response.status_code,
            ):
                response.raise_for_status()
    except httpx.HTTPError as exc:
        raise _exit(f"remote Nimbus request failed: {exc}", code=1) from exc
    with trace.span("remote.response.json", response_bytes=len(response.content)):
        parsed = response.json()
        payload = parsed if isinstance(parsed, dict) else {}
    with trace.span(
        "cli.render_result",
        outcome=str(payload.get("outcome") or "reply"),
        actions=len(payload.get("actions") or []),
        artifacts=len(payload.get("artifacts") or []),
    ):
        ui.render_result(
            console,
            text=str(payload.get("text") or ""),
            outcome=str(payload.get("outcome") or "reply"),
            confirmation=_RemoteConfirmation.from_payload(payload.get("confirmation")),
            actions=tuple(
                _RemoteAction.from_payload(a) for a in payload.get("actions") or []
            ),
            artifacts=tuple(
                _RemoteArtifact.from_payload(a) for a in payload.get("artifacts") or []
            ),
        )


def _turn_input(
    *,
    profile: NimbusProfile,
    session: SessionRecord,
    message: str,
) -> ChatTurnInput:
    """Build a transport-neutral turn from one CLI prompt."""
    message_id = f"msg-{time.time_ns()}"
    return ChatTurnInput(
        request_id=f"req-cli-{uuid.uuid4().hex}",
        conversation_id=f"cli:{profile.name}:{session.internal_id}",
        platform="cli",
        workspace_id=profile.name,
        channel_id="terminal",
        thread_id=session.external_id,
        message_id=message_id,
        user_id=os.environ.get("USER", "local-user"),
        text=message,
        idempotency_key=f"cli:{session.internal_id}:{message_id}",
    )


def _turn_body(turn: ChatTurnInput) -> dict[str, object]:
    """Encode a ``ChatTurnInput`` for ``POST /ai/chat/turn``."""
    return {
        "request_id": turn.request_id,
        "platform": turn.platform,
        "workspace_id": turn.workspace_id,
        "channel_id": turn.channel_id,
        "thread_id": turn.thread_id,
        "message_id": turn.message_id,
        "user_id": turn.user_id,
        "text": turn.text,
        "idempotency_key": turn.idempotency_key,
        "attachments": [],
    }


_SLASH_HELP_ROWS: tuple[tuple[str, str], ...] = (
    ("/help", "show this help"),
    ("/clear", "clear the screen"),
    ("/new", "start a fresh session in this profile"),
    ("/model [id]", "switch the current profile's model (interactive without id)"),
    ("/profile", "show the active profile summary"),
    ("/exit, /quit", "leave the REPL"),
)


@dataclass(frozen=True, slots=True)
class _SlashOutcome:
    """The state changes one slash command can produce.

    ``status`` is one of:
      - ``"exit"``        — leave the REPL.
      - ``"handled"``     — command ran; continue, may use updated profile/session.
      - ``"passthrough"`` — not a known slash; fall through to the model.
    """

    status: str
    profile: NimbusProfile | None = None
    session: SessionRecord | None = None


def _handle_slash_command(  # noqa: PLR0911 - Direct dispatch is clearer here.
    *,
    console: Console,
    line: str,
    profile: NimbusProfile,
    session: SessionRecord,
    config: NimbusConfig,
    store: ConfigStore,
) -> _SlashOutcome:
    """Dispatch a REPL slash command and return what changed."""
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in {"/exit", "/quit"}:
        return _SlashOutcome("exit")

    if cmd == "/help":
        console.print(
            ui.card(
                _render_slash_help(),
                title="Nimbus REPL  •  slash commands",
            )
        )
        return _SlashOutcome("handled")

    if cmd == "/clear":
        console.clear()
        return _SlashOutcome("handled")

    if cmd == "/profile":
        _print_session_banner(console=console, profile=profile, session=session)
        return _SlashOutcome("handled")

    if cmd == "/new":
        new_config, new_session = config.resolve_session(
            profile_name=profile.name,
            external_id=None,
            resume_last=False,
        )
        store.save(new_config)
        ui.success(console, f"started new session: {new_session.external_id}")
        return _SlashOutcome("handled", session=new_session)

    if cmd == "/model":
        new_model = arg or _interactive_model_picker(
            console=console, current=profile.model
        )
        if not new_model or new_model == profile.model:
            ui.info(console, "model unchanged")
            return _SlashOutcome("handled")
        updated = NimbusProfile(
            name=profile.name,
            mode=profile.mode,
            model=new_model,
            fallback_model=profile.fallback_model,
            openrouter_base_url=profile.openrouter_base_url,
            storage_container=profile.storage_container,
            aws_region=profile.aws_region,
            session_dir=profile.session_dir,
            remote_base_url=profile.remote_base_url,
            remote_auth=profile.remote_auth,
        )
        store.save(config.with_profile(updated))
        ui.success(console, f"model → {new_model}")
        return _SlashOutcome("handled", profile=updated)

    ui.warn(console, f"unknown slash command {cmd!r} — try /help")
    return _SlashOutcome("handled")


def _render_slash_help() -> Table:
    """Two-column command/description grid for /help output."""
    rows = [ui.KV(cmd, desc) for cmd, desc in _SLASH_HELP_ROWS]
    return ui.kv_table(rows)


def _print_welcome_panel(*, console: Console, requested: str | None) -> None:
    """Render the first-run welcome panel when no profile is configured.

    Shown instead of the bare ``profile 'local' is not configured`` crash so
    new users get a discoverable next step.
    """
    target = requested or DEFAULT_PROFILE
    if requested and requested != DEFAULT_PROFILE:
        intro = Text.assemble(
            ("Profile ", ui.MUTED),
            (f"{requested!r}", "bold"),
            (" is not configured.\n", ui.MUTED),
            (
                "Create it with one of the commands below, or switch profiles "
                "with --profile.",
                "",
            ),
        )
    else:
        intro = Text.assemble(
            ("Welcome to ", ""),
            ("Nimbus", f"bold {ui.PRIMARY}"),
            (" — a proof-carrying storage agent.\n\n", ""),
            ("No profile found. Let's set one up.\n\n", ""),
            ("You'll need:\n", ui.MUTED),
            (f"  {ui.ICON_BULLET} An OpenRouter API key ", ""),
            ("→ ", ui.MUTED),
            ("openrouter.ai/keys\n", ui.PRIMARY),
            (
                f"  {ui.ICON_BULLET} (Optional) AWS S3 credentials for file storage\n",
                "",
            ),
        )

    commands = Text.assemble(
        ("Quick start\n\n", f"bold {ui.MUTED}"),
        ("  ", ""),
        (f"uv run nimbus auth local --profile {target}", f"bold {ui.PRIMARY}"),
        ("\n", ""),
        (
            "    Stores credentials in your OS keyring (interactive prompts)\n\n",
            ui.MUTED,
        ),
        ("  ", ""),
        ("export OPENROUTER_API_KEY=sk-or-... && nimbus", f"bold {ui.PRIMARY}"),
        ("\n", ""),
        (
            "    Bootstrap from environment, no persistence needed\n",
            ui.MUTED,
        ),
    )

    body = Group(intro, Text(""), commands)
    console.print(
        ui.card(
            body,
            title="  Nimbus  ",
            title_style=f"bold {ui.PRIMARY}",
            border_style=ui.PRIMARY,
        )
    )


def _print_session_banner(
    *,
    console: Console,
    profile: NimbusProfile,
    session: SessionRecord,
) -> None:
    """Render the per-session banner shown at REPL start.

    Uses the shared design system so the layout, colors, and spacing match
    the rest of the CLI. Internal IDs (session UUIDs) are intentionally
    omitted — they leak implementation detail and clutter the surface.
    """
    rows: list[ui.KV] = [
        ui.KV("profile", profile.name),
        ui.KV("session", session.external_id),
    ]
    if profile.mode == "local":
        rows.append(ui.KV("model", profile.model))
        if profile.storage_container:
            storage = profile.storage_container
            if profile.aws_region:
                storage = f"{profile.storage_container} ({profile.aws_region})"
            rows.append(ui.KV("storage", storage))
    else:
        rows.append(ui.KV("server", profile.remote_base_url or ""))
        rows.append(ui.KV("auth", profile.remote_auth or ""))

    console.print(ui.card(ui.kv_table(rows), title="Nimbus"))


def _existing_local_profile(
    *,
    config: NimbusConfig,
    profile: str,
) -> NimbusProfile | None:
    existing = config.profiles.get(profile)
    if existing is None:
        return None
    if existing.mode != "local":
        msg = f"profile {profile!r} already exists as a remote profile"
        raise _exit(msg, code=2)
    return existing


def _bootstrap_default_local_profile_from_env(
    *,
    config: NimbusConfig,
    profile_name: str | None,
) -> NimbusConfig:
    target_profile = profile_name or config.active_profile
    if target_profile in config.profiles:
        return config
    if target_profile != DEFAULT_PROFILE:
        return config
    if _env_first(_OPENROUTER_ENV) is None:
        return config
    return config.with_profile(
        NimbusProfile(
            name=DEFAULT_PROFILE,
            mode="local",
            storage_container=_env_first(_NIMBUS_CONTAINER_ENV, _AWS_BUCKET_ENV),
            aws_region=_env_first(_AWS_REGION_ENV, _AWS_DEFAULT_REGION_ENV),
        )
    )


def _resolve_fallback_model(
    *,
    fallback_model: str | None,
    existing: NimbusProfile | None,
) -> str | None:
    if fallback_model is not None:
        return _normalize_optional_model(fallback_model)
    return existing.fallback_model if existing else None


def _store_aws_credentials(
    *,
    profile: str,
    secrets: NimbusSecrets,
    aws_access_key_id: str | None,
    aws_secret_access_key: str | None,
    aws_session_token: str | None,
) -> None:
    access_key_id = _secret_value(
        explicit=aws_access_key_id,
        existing=secrets.get(profile=profile, kind=_AWS_ACCESS_KEY_SECRET_KIND),
        env_names=(_AWS_ACCESS_KEY_ENV,),
    )
    secret_access_key = _secret_value(
        explicit=aws_secret_access_key,
        existing=secrets.get(profile=profile, kind=_AWS_SECRET_KEY_SECRET_KIND),
        env_names=(_AWS_SECRET_KEY_ENV,),
    )
    if access_key_id is None:
        access_key_id = Prompt.ask("AWS access key ID", default="")
    if secret_access_key is None:
        secret_access_key = Prompt.ask(
            "AWS secret access key",
            password=True,
            default="",
        )
    if bool(access_key_id) != bool(secret_access_key):
        msg = "AWS access key ID and secret access key must be provided together"
        raise _exit(msg, code=2)
    if access_key_id and secret_access_key:
        secrets.set(
            profile=profile,
            kind=_AWS_ACCESS_KEY_SECRET_KIND,
            value=access_key_id,
        )
        secrets.set(
            profile=profile,
            kind=_AWS_SECRET_KEY_SECRET_KIND,
            value=secret_access_key,
        )
    session_token = _secret_value(
        explicit=aws_session_token,
        existing=secrets.get(profile=profile, kind=_AWS_SESSION_TOKEN_SECRET_KIND),
        env_names=(_AWS_SESSION_TOKEN_ENV,),
    )
    if session_token:
        secrets.set(
            profile=profile,
            kind=_AWS_SESSION_TOKEN_SECRET_KIND,
            value=session_token,
        )


def _credential_payload(credentials: str | None) -> str:
    """Return a pasted credential payload from an argument or stdin."""
    if credentials is not None:
        return credentials
    Console().print(
        "Paste KEY=value credentials, then press Ctrl-D when finished. "
        "Nimbus will store secrets without printing them back."
    )
    payload = sys.stdin.read()
    if not payload.strip():
        raise _exit("no credentials were pasted", code=2)
    return payload


def _parse_credentials_payload(payload: str) -> dict[str, str]:
    """Parse a small dotenv-style credentials payload."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise _exit(
                f"credential line {line_number} is not KEY=value",
                code=2,
            )
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key:
            raise _exit(f"credential line {line_number} has an empty key", code=2)
        values[key] = _unquote_env_value(raw_value.strip())
    if not values:
        raise _exit("credential paste did not contain any KEY=value lines", code=2)
    return values


def _unquote_env_value(value: str) -> str:
    """Remove one balanced shell-style quote pair from a dotenv value."""
    if (
        len(value) >= _BALANCED_QUOTE_MIN_LENGTH
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        return value[1:-1]
    return value


def _payload_has_aws_keypair(values: dict[str, str]) -> bool:
    """Return whether a paste contains enough AWS key material to store."""
    return bool(values.get(_AWS_ACCESS_KEY_ENV) or values.get(_AWS_SECRET_KEY_ENV))


def _env_has_aws_keypair() -> bool:
    """Return whether the current environment has explicit AWS key material."""
    return bool(_env_first(_AWS_ACCESS_KEY_ENV, _AWS_SECRET_KEY_ENV))


def _recognized_credential_names(values: dict[str, str]) -> tuple[str, ...]:
    """Return recognized credential field names without their values."""
    return tuple(name for name in _RECOGNIZED_CREDENTIAL_ENV_NAMES if name in values)


def _secret_value(
    *,
    explicit: str | None,
    existing: str | None,
    env_names: tuple[str, ...],
) -> str | None:
    return explicit or _env_first(*env_names) or existing


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _normalize_optional_model(raw: str | None) -> str | None:
    if raw is None:
        return None
    return None if raw.lower() in {"none", "off", "clear", ""} else raw


def _profile_target(profile: NimbusProfile) -> str:
    if profile.mode == "local":
        return profile.storage_container or profile.model
    return profile.remote_base_url or ""


def _profile_auth_label(profile: NimbusProfile) -> str:
    if profile.mode == "local":
        return "openrouter"
    return profile.remote_auth or "remote"


def _profile_secret_status(profile: NimbusProfile, secrets: NimbusSecrets) -> str:
    if profile.mode == "local":
        return _secret_status(
            profile=profile.name,
            secrets=secrets,
            kind=_OPENROUTER_SECRET_KIND,
            env_names=(_OPENROUTER_ENV,),
        )
    if profile.remote_auth == "bearer":
        return _secret_status(
            profile=profile.name,
            secrets=secrets,
            kind="remote_bearer_token",
            env_names=(),
        )
    if profile.remote_auth == "hmac":
        return _secret_status(
            profile=profile.name,
            secrets=secrets,
            kind="remote_signing_secret",
            env_names=(),
        )
    return "missing"


def _profile_storage_status(profile: NimbusProfile, secrets: NimbusSecrets) -> str:
    if profile.mode != "local":
        return ""
    if not profile.storage_container:
        return "not configured"
    access_status = _secret_status(
        profile=profile.name,
        secrets=secrets,
        kind=_AWS_ACCESS_KEY_SECRET_KIND,
        env_names=(_AWS_ACCESS_KEY_ENV,),
    )
    credential_status = _secret_status(
        profile=profile.name,
        secrets=secrets,
        kind=_AWS_SECRET_KEY_SECRET_KIND,
        env_names=(_AWS_SECRET_KEY_ENV,),
    )
    if access_status == "missing" and credential_status == "missing":
        return "boto3 chain"
    if access_status == "missing" or credential_status == "missing":
        return "incomplete"
    return f"{profile.storage_container} ({access_status})"


def _secret_status(
    *,
    profile: str,
    secrets: NimbusSecrets,
    kind: str,
    env_names: tuple[str, ...],
) -> str:
    if secrets.has(profile=profile, kind=kind):
        return "stored"
    if _env_first(*env_names):
        return "env"
    return "missing"


# ── nimbus tools subcommands ───────────────────────────────────────────────


@tools_app.command("list")
def tools_list(
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filter by status: current, partial, roadmap."),
    ] = None,
    surface: Annotated[
        str | None,
        typer.Option(
            "--surface",
            help="Filter by surface: runtime, model_tool, cli, slack, worker.",
        ),
    ] = None,
    *,
    include_roadmap: Annotated[
        bool,
        typer.Option("--roadmap/--current-only", help="Include roadmap tools."),
    ] = True,
) -> None:
    """List Nimbus runtime capabilities shared by Slack, CLI, and model tools."""
    console = Console()
    status_filter = _parse_capability_status(status)
    surface_filter = _parse_capability_surface(surface)
    capabilities = all_capabilities(
        status=status_filter,
        surface=surface_filter,
        include_roadmap=include_roadmap,
    )
    if not capabilities:
        console.print(
            ui.empty_state(
                "No Nimbus capabilities matched that filter.",
                hint="Try `nimbus tools list --roadmap`.",
            )
        )
        return
    console.print(
        ui.card(
            _capability_list_table(capabilities),
            title="Nimbus tools",
            subtitle="Runtime-owned capabilities; Slack and CLI are clients.",
        )
    )


@tools_app.command("inspect")
def tools_inspect(
    name: Annotated[str, typer.Argument(help="Capability name to inspect.")],
) -> None:
    """Show one Nimbus capability in detail."""
    console = Console()
    try:
        capability = get_capability(name)
    except KeyError as exc:
        raise _exit(f"unknown Nimbus capability {name!r}", code=1) from exc
    console.print(
        ui.card(
            _capability_detail_table(capability),
            title=f"Tool  •  {capability.name}",
        )
    )


def _parse_capability_status(raw: str | None) -> CapabilityStatus | None:
    if raw is None:
        return None
    try:
        return CapabilityStatus(raw)
    except ValueError as exc:
        valid = ", ".join(status.value for status in CapabilityStatus)
        message = f"unknown capability status {raw!r}. Valid values: {valid}"
        raise _exit(message, code=2) from exc


def _parse_capability_surface(raw: str | None) -> CapabilitySurface | None:
    if raw is None:
        return None
    try:
        return CapabilitySurface(raw)
    except ValueError as exc:
        valid = ", ".join(surface.value for surface in CapabilitySurface)
        message = f"unknown capability surface {raw!r}. Valid values: {valid}"
        raise _exit(message, code=2) from exc


def _capability_list_table(capabilities: Iterable[CapabilitySpec]) -> Table:
    table = Table(show_lines=False, box=None, padding=(0, 1))
    table.add_column("tool", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("risk", no_wrap=True)
    table.add_column("surfaces", style=ui.MUTED)
    table.add_column("description", ratio=1)
    for capability in capabilities:
        table.add_row(
            capability.name,
            capability.status.value,
            capability.risk.value,
            ", ".join(surface.value for surface in capability.surfaces),
            capability.description,
        )
    return table


def _capability_detail_table(capability: CapabilitySpec) -> Table:
    rows: list[ui.KV] = [
        ui.KV("name", capability.name),
        ui.KV("title", capability.title),
        ui.KV("status", capability.status.value),
        ui.KV("risk", capability.risk.value),
        ui.KV("modes", ", ".join(mode.value for mode in capability.modes)),
        ui.KV("surfaces", ", ".join(surface.value for surface in capability.surfaces)),
        ui.KV(
            "approval",
            "required" if capability.requires_approval else "not required",
        ),
        ui.KV("description", capability.description),
    ]
    if capability.claude_analogues:
        rows.append(ui.KV("claude-style", ", ".join(capability.claude_analogues)))
    if capability.ai_tool_name:
        rows.append(ui.KV("model tool", capability.ai_tool_name))
    if capability.roadmap_feature:
        rows.append(ui.KV("roadmap", capability.roadmap_feature))
    return ui.kv_table(rows)


# ── nimbus task subcommands ────────────────────────────────────────────────


@task_app.command("list")
def task_list(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filter by task status."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max tasks to show.")] = 20,
    workspace: Annotated[
        str | None,
        typer.Option("--workspace", help="Workspace/profile name."),
    ] = None,
    watch: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--watch/--no-watch",
            help="Live-refresh the list every few seconds (Ctrl-C to stop).",
        ),
    ] = False,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Poll interval in seconds (--watch only)."),
    ] = 3.0,
) -> None:
    """List recent background tasks for the active profile.

    Pass ``--watch`` for a continuously refreshing live view that auto-updates
    every ``--interval`` seconds until you press Ctrl-C.
    """
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)

    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    task_store = FileTaskStore(session_dir)

    filter_status: TaskStatus | None = None
    if status:
        try:
            filter_status = TaskStatus(status)
        except ValueError as exc:
            raise _exit(
                f"unknown task status {status!r}. "
                f"Valid values: {', '.join(s.value for s in TaskStatus)}",
                code=2,
            ) from exc

    if watch:
        _task_list_watch(
            console=console,
            task_store=task_store,
            tenant=tenant,
            prof=prof,
            filter_status=filter_status,
            limit=limit,
            interval=interval,
        )
        return

    tasks = task_store.list_for_tenant(
        tenant=tenant,
        status=filter_status,
        limit=limit,
    )

    if not tasks:
        console.print(
            ui.empty_state(
                f"No tasks for profile {prof.name!r}.",
                hint="Run `nimbus chat` to start a session, or use @Nimbus in Slack.",
            )
        )
        return

    console.print(ui.card(_task_list_table(tasks), title=f"Tasks  •  {prof.name}"))


def _task_list_table(tasks: Iterable[Task]) -> Table:
    """Build a Rich table from an iterable of tasks."""
    table = Table(show_lines=False, box=None, padding=(0, 1))
    table.add_column("status", no_wrap=True)
    table.add_column("task", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("intent", ratio=1)
    table.add_column("created", style=ui.MUTED, no_wrap=True)

    for task in tasks:
        table.add_row(
            ui.status_badge(task.status.value),
            task.task_id,
            (task.intent[:_TASK_INTENT_PREVIEW_CHARS] + "…")
            if len(task.intent) > _TASK_INTENT_PREVIEW_CHARS
            else task.intent,
            task.created_at.strftime("%b %d %H:%M"),
        )
    return table


def _task_list_watch(
    *,
    console: Console,
    task_store: FileTaskStore,
    tenant: TenantIdentity,
    prof: NimbusProfile,
    filter_status: TaskStatus | None,
    limit: int,
    interval: float,
) -> None:
    """Run the live-refresh loop for ``nimbus task list --watch``."""
    ui.info(console, f"Watching tasks for profile {prof.name!r}  (Ctrl-C to stop)")
    interrupted = False

    try:
        with Live(console=console, refresh_per_second=4, transient=False) as live:
            while True:
                tasks = task_store.list_for_tenant(
                    tenant=tenant,
                    status=filter_status,
                    limit=limit,
                )
                updated_at = time.strftime("%H:%M:%S")
                title = (
                    f"Tasks  •  {prof.name}  "
                    f"[dim](updated {updated_at}, "
                    f"refresh every {interval:.0f}s — Ctrl-C to stop)[/dim]"
                )
                if tasks:
                    renderable = ui.card(_task_list_table(list(tasks)), title=title)
                else:
                    renderable = ui.empty_state(
                        f"No tasks for profile {prof.name!r}.",
                        hint=(
                            "Run `nimbus chat` to start a session, "
                            "or use @Nimbus in Slack."
                        ),
                    )
                live.update(renderable)
                time.sleep(interval)
    except KeyboardInterrupt:
        interrupted = True

    if interrupted:
        console.print()
        ui.info(console, "watch stopped")


@task_app.command("inspect")
def task_inspect(
    task_id: Annotated[str, typer.Argument(help="Task ID to inspect.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
) -> None:
    """Show full details for a single background task."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    task_store = FileTaskStore(session_dir)

    task = task_store.get(tenant=tenant, task_id=task_id)
    if task is None:
        raise _exit(f"task {task_id!r} not found for profile {prof.name!r}", code=1)

    rows: list[ui.KV] = [
        ui.KV("task", task.task_id),
        ui.KV("status", ui.status_badge(task.status.value)),
        ui.KV("intent", task.intent),
        ui.KV("session", task.session_id),
        ui.KV("created", task.created_at.isoformat()),
        ui.KV("updated", task.updated_at.isoformat()),
    ]
    if task.expires_at:
        rows.append(ui.KV("expires", task.expires_at.isoformat()))
    if task.failure_detail:
        rows.append(ui.KV("failure", Text(task.failure_detail, style=ui.DANGER)))
    if task.source_ref:
        rows.append(ui.KV("source_ref", task.source_ref))
    for key, val in task.metadata.items():
        rows.append(ui.KV(key, str(val)))

    # Append cumulative cost / token usage from the session sidecar file.
    usage = load_session_usage(session_dir, task.session_id)
    if usage:
        input_tok = usage.get("input_tokens")
        output_tok = usage.get("output_tokens")
        cost = usage.get("cost_usd_estimate")
        if isinstance(input_tok, int) and isinstance(output_tok, int):
            total = input_tok + output_tok
            rows.append(
                ui.KV(
                    "tokens",
                    Text(
                        f"{total:,} total  ({input_tok:,} in / {output_tok:,} out)",
                        style=ui.MUTED,
                    ),
                )
            )
        if isinstance(cost, float) and cost > 0:
            rows.append(
                ui.KV(
                    "cost",
                    Text(f"~${cost:.4f} USD", style=ui.MUTED),
                )
            )

    console.print(
        ui.card(
            ui.kv_table(rows),
            title=f"Task  •  {task.task_id}",
        )
    )


@task_app.command("events")
def task_events(
    task_id: Annotated[str, typer.Argument(help="Task ID to show events for.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[int, typer.Option("--limit")] = 50,
) -> None:
    """Show the ordered event stream for a background task."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    task_store = FileTaskStore(session_dir)
    task = task_store.get(tenant=tenant, task_id=task_id)
    if task is None:
        raise _exit(f"task {task_id!r} not found", code=1)

    event_store = FileSessionEventStore(session_dir)
    events = event_store.list_events(
        tenant=tenant,
        session_id=task.session_id,
        limit=limit,
    )

    if not events:
        console.print(f"[{_INFO_STYLE}]No events recorded for task {task_id!r}.[/]")
        return

    table = Table(show_lines=False, box=None, padding=(0, 1))
    table.add_column("#", style=ui.MUTED, no_wrap=True, width=4)
    table.add_column("event", style=f"bold {ui.PRIMARY}")
    table.add_column("actor", style=ui.MUTED)
    table.add_column("time", style=ui.MUTED, no_wrap=True)

    for event in events:
        actor_str = event.actor.user_id if event.actor else ""
        icon = ui.event_type_icon(event.event_type)
        table.add_row(
            str(event.sequence),
            f"{icon}  {event.event_type}",
            actor_str,
            event.created_at.strftime("%H:%M:%S"),
        )

    console.print(ui.card(table, title=f"Events  •  {task_id}"))


@task_app.command("artifacts")
def task_artifacts(
    task_id: Annotated[
        str,
        typer.Argument(help="Task ID to show artifacts for, or 'latest'."),
    ],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
) -> None:
    """Show evidence artifacts for a background task."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    task_store = FileTaskStore(session_dir)
    if task_id == "latest":
        tasks = task_store.list_for_tenant(tenant=tenant, limit=1)
        if not tasks:
            raise _exit(f"no tasks found for profile {prof.name!r}", code=1)
        task_id = tasks[0].task_id
    task = task_store.get(tenant=tenant, task_id=task_id)
    if task is None:
        raise _exit(f"task {task_id!r} not found", code=1)

    artifact_store = FileArtifactStore(session_dir)
    artifacts = artifact_store.list_for_session(
        tenant=tenant,
        session_id=task.session_id,
    )

    if not artifacts:
        console.print(f"[{_INFO_STYLE}]No artifacts for task {task_id!r}.[/]")
        return

    table = Table(show_lines=False, box=None, padding=(0, 1))
    table.add_column("artifact", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("kind")
    table.add_column("action", style=ui.MUTED)
    table.add_column("created", style=ui.MUTED, no_wrap=True)

    for artifact in artifacts:
        table.add_row(
            artifact.artifact_id,
            artifact.kind,
            artifact.action_id or "",
            artifact.created_at.strftime("%b %d %H:%M"),
        )

    console.print(ui.card(table, title=f"Artifacts  •  {task_id}"))


@task_app.command("watch")
def task_watch(
    task_ref: Annotated[
        str,
        typer.Argument(help="Task ID, or 'latest' to watch the most recent task."),
    ] = "latest",
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    interval: Annotated[
        float,
        typer.Option("--interval", help="Poll interval in seconds."),
    ] = 2.0,
) -> None:
    """Watch a background task, polling for status changes."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    task_store = FileTaskStore(session_dir)

    if task_ref == "latest":
        tasks = task_store.list_for_tenant(tenant=tenant, limit=1)
        if not tasks:
            raise _exit(f"no tasks found for profile {prof.name!r}", code=1)
        task_id = tasks[0].task_id
        console.print(f"[{_INFO_STYLE}]Watching latest task: {task_id}[/]")
    else:
        task_id = task_ref

    terminal_statuses = {
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.EXPIRED,
        TaskStatus.REJECTED,
    }

    start = time.monotonic()
    last_status: TaskStatus | None = None
    status_history: list[tuple[str, str]] = []
    interrupted = False

    try:
        with Live(console=console, refresh_per_second=4, transient=False) as live:
            while True:
                current = task_store.get(tenant=tenant, task_id=task_id)
                if current is None:
                    raise _exit(f"task {task_id!r} not found", code=1)

                elapsed = time.monotonic() - start
                if current.status != last_status:
                    status_history.append(
                        (
                            time.strftime("%H:%M:%S"),
                            current.status.value,
                        )
                    )
                    last_status = current.status

                live.update(
                    ui.live_task_panel(
                        task_id=task_id,
                        status=current.status.value,
                        intent=current.intent,
                        elapsed=elapsed,
                        status_history=status_history,
                        poll_interval=interval,
                    )
                )

                if current.status in terminal_statuses:
                    break
                time.sleep(interval)
    except KeyboardInterrupt:
        interrupted = True

    if interrupted:
        console.print()
        ui.info(console, "watch interrupted")
        return

    final = task_store.get(tenant=tenant, task_id=task_id)
    if final is not None and final.failure_detail:
        ui.error(console, final.failure_detail)
    final_status_value = last_status.value if last_status else "?"
    succeeded = last_status is TaskStatus.DONE
    if succeeded:
        ui.success(console, f"task reached {final_status_value}")
    else:
        ui.error(console, f"task reached {final_status_value}")
    ui.hint(
        console,
        f"run `nimbus task events {task_id}` "
        f"or `nimbus task artifacts {task_id}` for details",
    )
    if not succeeded:
        # Surface non-success terminals through the process exit code so
        # scripts and shells can tell that the watched task did not finish
        # cleanly. The user has already seen the error/hint above.
        raise typer.Exit(1)


@task_app.command("cancel")
def task_cancel(
    task_id: Annotated[str, typer.Argument(help="Task ID to cancel.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
) -> None:
    """Cancel an in-progress background task."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    task_store = FileTaskStore(session_dir)

    task = task_store.get(tenant=tenant, task_id=task_id)
    if task is None:
        raise _exit(f"task {task_id!r} not found", code=1)

    cancelable_statuses = {
        TaskStatus.CREATED,
        TaskStatus.PLANNING,
        TaskStatus.SCANNING,
        TaskStatus.DIFFING,
        TaskStatus.AWAITING_APPROVAL,
        TaskStatus.APPLYING,
        TaskStatus.VERIFYING,
    }

    if task.status not in cancelable_statuses:
        raise _exit(
            f"task {task_id!r} is in status {task.status.value!r} "
            "and cannot be canceled",
            code=1,
        )

    result = task_store.transition(
        tenant=tenant,
        task_id=task_id,
        transition=TaskTransition(
            expected=task.status,
            next_status=TaskStatus.CANCELED,
            event_type="task_canceled",
            event_payload={"canceled_by": "cli", "reason": "user_requested"},
        ),
    )
    if result is None:
        raise _exit(
            f"task {task_id!r} could not be canceled — its status changed concurrently",
            code=1,
        )
    console.print(f"[{_ACCENT_STYLE}]Canceled task[/] {task_id}")


@task_app.command("approve")
def task_approve(
    task_id: Annotated[str, typer.Argument(help="Task ID whose approval to accept.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    note: Annotated[
        str | None,
        typer.Option("--note", "-n", help="Optional note."),
    ] = None,
) -> None:
    """Approve a task that is awaiting human sign-off.

    Equivalent to clicking the Approve button in the Slack card — records the
    decision in the tenant-local FileApprovalStore so the task worker can
    proceed.
    """
    import datetime

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    task_store = FileTaskStore(session_dir)
    task = task_store.get(tenant=tenant, task_id=task_id)
    if task is None:
        raise _exit(f"task {task_id!r} not found", code=1)

    if task.status is not TaskStatus.AWAITING_APPROVAL:
        raise _exit(
            f"task {task_id!r} is in status {task.status.value!r} — "
            "only tasks in awaiting_approval can be approved",
            code=1,
        )

    approval_store = FileApprovalStore(session_dir)
    approval = approval_store.find_pending_for_task(tenant=tenant, task_id=task_id)
    if approval is None:
        raise _exit(
            f"no pending approval found for task {task_id!r} — "
            "it may have already been decided or expired",
            code=1,
        )

    now = datetime.datetime.now(datetime.UTC)
    actor = VerifiedActor(
        tenant=tenant,
        user_id="cli",
        auth_source="cli_local",
        bridge_id=None,
        verified_at=now,
    )

    result = approval_store.decide(
        tenant=tenant,
        approval_id=approval.approval_id,
        actor=actor,
        choice=ApprovalChoice.APPROVE,
        exact_target=approval.exact_target,
        now=now,
        note=note,
    )

    if not result.accepted:
        raise _exit(f"approval could not be recorded: {result.reason}", code=1)

    ui.success(console, f"Approved task {task_id}")
    ui.hint(
        console,
        f"run `nimbus task watch {task_id}` to follow progress",
    )


@task_app.command("retry")
def task_retry(
    task_id: Annotated[str, typer.Argument(help="Task ID to retry.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
) -> None:
    """Retry a failed or canceled task by creating a new CREATED copy."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    task_store = FileTaskStore(session_dir)

    task = task_store.get(tenant=tenant, task_id=task_id)
    if task is None:
        raise _exit(f"task {task_id!r} not found", code=1)

    retryable = {TaskStatus.FAILED, TaskStatus.CANCELED, TaskStatus.EXPIRED}
    if task.status not in retryable:
        raise _exit(
            f"task {task_id!r} is in status {task.status.value!r} — "
            "only failed, canceled, or expired tasks can be retried",
            code=1,
        )

    now = _dt.datetime.now(_dt.UTC)
    new_task_id = uuid.uuid4().hex
    new_idem_key = uuid.uuid4().hex
    actor = VerifiedActor(
        tenant=tenant,
        user_id="cli",
        auth_source="cli_local",
        bridge_id=None,
        verified_at=now,
    )

    def _build_retry_task() -> Task:
        return Task(
            task_id=new_task_id,
            tenant=tenant,
            session_id=task.session_id,
            created_by=actor,
            status=TaskStatus.CREATED,
            intent=task.intent,
            source_ref=task.source_ref,
            idempotency_key=new_idem_key,
            metadata={"retried_from": task_id},
            failure_detail=None,
            created_at=now,
            updated_at=now,
            expires_at=None,
        )

    new_task = task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key=new_idem_key,
        create=_build_retry_task,
    )

    console.print(
        f"[{_ACCENT_STYLE}]Retrying task[/] "
        f"[italic]{task.intent[:_TASK_INTENT_PREVIEW_CHARS]}[/italic]"
    )
    console.print(f"New task: [bold]{new_task.task_id}[/bold]")
    ui.hint(console, f"run `nimbus task inspect {new_task.task_id}` to follow progress")


# ── Protected root / generation commands ────────────────────────────────────


@root_app.command("protect")
def root_protect(
    container: Annotated[
        str | None,
        typer.Option(
            "--container",
            "-c",
            help="Storage container/bucket. Defaults to the profile container.",
        ),
    ] = None,
    prefix: Annotated[str, typer.Option("--prefix", help="Object prefix.")] = "",
    name: Annotated[
        str | None,
        typer.Option("--name", help="Human-friendly display name."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
) -> None:
    """Protect an S3-backed bucket/prefix so Nimbus can snapshot it."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    resolved_container = container or prof.storage_container
    if not resolved_container:
        raise _exit(
            "no container supplied and the active profile has no storage container",
            code=2,
        )
    now = _dt.datetime.now(_dt.UTC)
    actor = _cli_actor(tenant=tenant, now=now)
    root = FileProtectedRootStore(session_dir).protect(
        tenant=tenant,
        provider="s3",
        container=resolved_container,
        prefix=prefix,
        display_name=name or f"s3://{resolved_container}/{prefix.lstrip('/')}",
        actor=actor,
        metadata={"profile": prof.name},
        now=now,
    )
    rows = [
        ui.KV("root", root.root_id),
        ui.KV("provider", root.provider),
        ui.KV("container", root.container),
        ui.KV("prefix", root.prefix or "/"),
        ui.KV("name", root.display_name),
        ui.KV("created", root.created_at.isoformat()),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Protected root"))
    ui.hint(console, f"run `nimbus generation create {root.root_id}`")


@root_app.command("list")
def root_list(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable roots."),
    ] = False,
) -> None:
    """List protected roots for the active tenant."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    roots = FileProtectedRootStore(_profile_session_dir(prof)).list_for_tenant(
        tenant=tenant,
        limit=limit,
    )
    if json_output:
        console.print_json(
            json.dumps([to_jsonable(root) for root in roots], sort_keys=True)
        )
        return
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("root", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("provider")
    table.add_column("container")
    table.add_column("prefix")
    table.add_column("updated")
    for root in roots:
        table.add_row(
            root.root_id,
            root.provider,
            root.container,
            root.prefix or "/",
            root.updated_at.isoformat(),
        )
    console.print(ui.card(table, title=f"Protected roots  •  {prof.name}"))


@generation_app.command("create")
def generation_create(
    root_id: Annotated[str, typer.Argument(help="Protected root ID.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Session ID to attach artifacts to."),
    ] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable generation."),
    ] = False,
) -> None:
    """Snapshot the current object listing for a protected root."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    root_store = FileProtectedRootStore(session_dir)
    root = root_store.get(tenant=tenant, root_id=root_id)
    if root is None:
        raise _exit(f"protected root {root_id!r} not found", code=1)
    secrets = NimbusSecrets(session_dir)
    storage = _build_storage_for_profile(prof, secrets)
    if storage is None:
        raise _exit(
            "no storage client configured for this profile — "
            "run `nimbus auth local --aws` or set AWS credentials",
            code=2,
        )
    now = _dt.datetime.now(_dt.UTC)
    actor = _cli_actor(tenant=tenant, now=now)
    artifact_store = FileArtifactStore(session_dir)
    generation_store = FileGenerationStore(session_dir)
    base = generation_store.latest_for_root(tenant=tenant, root_id=root.root_id)
    with ui.thinking(console, "Creating generation…"):
        result = create_generation(
            root=root,
            storage=storage,
            artifact_store=artifact_store,
            generation_store=generation_store,
            actor=actor,
            session_id=session_id or f"generation-{root.root_id}",
            base_generation_id=base.generation_id if base is not None else None,
            now=now,
        )
    if json_output:
        console.print_json(
            json.dumps(
                {
                    "generation": to_jsonable(result.generation),
                    "manifest_artifact": _artifact_to_jsonable(
                        result.manifest_artifact
                    ),
                    "proof_artifact": _artifact_to_jsonable(result.proof_artifact),
                },
                sort_keys=True,
            )
        )
        return
    rows = [
        ui.KV("generation", result.generation.generation_id),
        ui.KV("root", result.generation.root_id),
        ui.KV("status", result.generation.status),
        ui.KV("objects", str(result.generation.object_count)),
        ui.KV("bytes", _format_bytes(result.generation.total_bytes)),
        ui.KV("manifest_digest", result.generation.manifest_digest),
        ui.KV("manifest", result.generation.manifest_artifact_id),
        ui.KV("proof", result.proof_artifact.artifact_id),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Generation created"))
    ui.hint(console, f"run `nimbus verify {result.generation.manifest_artifact_id}`")


@generation_app.command("list")
def generation_list(
    root_id: Annotated[
        str | None,
        typer.Argument(help="Protected root ID. Omit to list every snapshot."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 20,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable generations."),
    ] = False,
) -> None:
    """List generations for one protected root, or all roots when omitted."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    generation_store = FileGenerationStore(_profile_session_dir(prof))
    if root_id is None:
        generations = generation_store.list_for_tenant(tenant=tenant, limit=limit)
        title = f"Generations  •  {prof.name}"
    else:
        generations = generation_store.list_for_root(
            tenant=tenant,
            root_id=root_id,
            limit=limit,
        )
        title = f"Generations  •  {root_id}"
    if json_output:
        console.print_json(
            json.dumps([to_jsonable(gen) for gen in generations], sort_keys=True)
        )
        return
    console.print(ui.card(_generation_list_table(generations), title=title))


@manifest_app.command("list")
def manifest_list(
    root_id: Annotated[
        str | None,
        typer.Option("--root", help="Filter to one protected root ID."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable manifests."),
    ] = False,
) -> None:
    """List snapshot manifest artifacts, Git-log style, newest first."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    generation_store = FileGenerationStore(_profile_session_dir(prof))
    generations = (
        generation_store.list_for_root(tenant=tenant, root_id=root_id, limit=limit)
        if root_id is not None
        else generation_store.list_for_tenant(tenant=tenant, limit=limit)
    )
    rows = [
        {
            "manifest": generation.manifest_artifact_id,
            "generation": generation.generation_id,
            "root": generation.root_id,
            "digest": generation.manifest_digest,
            "objects": generation.object_count,
            "bytes": generation.total_bytes,
            "created_at": generation.created_at.isoformat(),
        }
        for generation in generations
    ]
    if json_output:
        console.print_json(json.dumps(rows, sort_keys=True))
        return
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("manifest", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("generation", no_wrap=True)
    table.add_column("root", no_wrap=True)
    table.add_column("objects", justify="right")
    table.add_column("bytes", justify="right")
    table.add_column("created")
    for row in rows:
        table.add_row(
            str(row["manifest"]),
            str(row["generation"]),
            str(row["root"]),
            str(row["objects"]),
            _format_bytes(cast("int", row["bytes"])),
            str(row["created_at"]),
        )
    title_suffix = root_id or prof.name
    console.print(ui.card(table, title=f"Manifests  •  {title_suffix}"))
    ui.hint(console, "run `nimbus verify <manifest-id>` to detect live S3 drift")


def _generation_list_table(generations: Iterable[Generation]) -> Table:
    """Render generations in a compact review table."""
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("generation", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("root", no_wrap=True)
    table.add_column("status")
    table.add_column("objects", justify="right")
    table.add_column("bytes", justify="right")
    table.add_column("manifest")
    table.add_column("created")
    for generation in generations:
        table.add_row(
            generation.generation_id,
            generation.root_id,
            generation.status,
            str(generation.object_count),
            _format_bytes(generation.total_bytes),
            generation.manifest_artifact_id,
            generation.created_at.isoformat(),
        )
    return table


@generation_app.command("diff")
def generation_diff(
    before_generation_id: Annotated[str, typer.Argument(help="Base generation ID.")],
    after_generation_id: Annotated[str, typer.Argument(help="Target generation ID.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable diff."),
    ] = False,
) -> None:
    """Diff two generation manifests."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    generation_store = FileGenerationStore(session_dir)
    before = generation_store.get(tenant=tenant, generation_id=before_generation_id)
    after = generation_store.get(tenant=tenant, generation_id=after_generation_id)
    if before is None:
        raise _exit(f"generation {before_generation_id!r} not found", code=1)
    if after is None:
        raise _exit(f"generation {after_generation_id!r} not found", code=1)
    artifact_store = FileArtifactStore(session_dir)
    before_manifest = _generation_manifest_from_artifact(
        artifact_store=artifact_store,
        tenant=tenant,
        artifact_id=before.manifest_artifact_id,
    )
    after_manifest = _generation_manifest_from_artifact(
        artifact_store=artifact_store,
        tenant=tenant,
        artifact_id=after.manifest_artifact_id,
    )
    diff = diff_generation_manifests(before=before_manifest, after=after_manifest)
    if json_output:
        console.print_json(json.dumps(to_jsonable(diff), sort_keys=True))
        return
    rows = [
        ui.KV("before", before_generation_id),
        ui.KV("after", after_generation_id),
        ui.KV("added", str(diff.added_count)),
        ui.KV("removed", str(diff.removed_count)),
        ui.KV("changed", str(diff.changed_count)),
        ui.KV("unchanged", str(diff.unchanged_count)),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Generation diff"))
    changed_entries = [entry for entry in diff.entries if entry.status != "unchanged"]
    if changed_entries:
        table = Table(show_header=True, header_style="bold", box=None)
        table.add_column("status")
        table.add_column("object")
        table.add_column("before")
        table.add_column("after")
        for entry in changed_entries:
            table.add_row(
                entry.status,
                entry.object_name,
                _pointer_short(entry.before),
                _pointer_short(entry.after),
            )
        console.print(ui.card(table, title="Object changes"))


@app.command("blame")
def blame_object(
    object_name: Annotated[str, typer.Argument(help="Object key to trace.")],
    root_id: Annotated[
        str | None,
        typer.Option("--root", help="Limit provenance to one protected root."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable provenance."),
    ] = False,
) -> None:
    """Show which generations contain an object and the digest they recorded."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    root_store = FileProtectedRootStore(session_dir)
    generation_store = FileGenerationStore(session_dir)
    artifact_store = FileArtifactStore(session_dir)
    roots = (
        [root]
        if root_id
        and (root := root_store.get(tenant=tenant, root_id=root_id)) is not None
        else list(root_store.list_for_tenant(tenant=tenant, limit=500))
    )
    if root_id and not roots:
        raise _exit(f"protected root {root_id!r} not found", code=1)
    occurrences: list[dict[str, object]] = []
    for root in roots:
        generations = generation_store.list_for_root(
            tenant=tenant,
            root_id=root.root_id,
            limit=500,
        )
        for generation in generations:
            manifest = _generation_manifest_from_artifact(
                artifact_store=artifact_store,
                tenant=tenant,
                artifact_id=generation.manifest_artifact_id,
            )
            occurrences.extend(
                {
                    "root_id": root.root_id,
                    "generation_id": generation.generation_id,
                    "manifest_artifact_id": generation.manifest_artifact_id,
                    "manifest_digest": generation.manifest_digest,
                    "content_sha256": pointer.content_sha256,
                    "size_bytes": pointer.size_bytes,
                    "created_at": generation.created_at.isoformat(),
                }
                for pointer in manifest.objects
                if pointer.object_name == object_name
            )
    if json_output:
        console.print_json(
            json.dumps(
                {"object_name": object_name, "occurrences": occurrences},
                sort_keys=True,
            )
        )
        return
    if not occurrences:
        raise _exit(f"object {object_name!r} was not found in generations", code=1)
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("generation", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("root")
    table.add_column("sha256")
    table.add_column("size")
    table.add_column("created")
    for item in occurrences:
        raw_size = item["size_bytes"]
        size_bytes = raw_size if isinstance(raw_size, int) else 0
        table.add_row(
            str(item["generation_id"]),
            str(item["root_id"]),
            str(item["content_sha256"] or "no-hash")[:16],
            _format_bytes(size_bytes),
            str(item["created_at"]),
        )
    console.print(ui.card(table, title=f"Blame  •  {object_name}"))


@migration_app.command("evaluate")
def migration_evaluate(
    root_id: Annotated[str, typer.Argument(help="Protected root ID.")],
    candidate_container: Annotated[
        str,
        typer.Option("--candidate-container", help="Candidate S3 bucket/container."),
    ],
    candidate_prefix: Annotated[
        str,
        typer.Option("--candidate-prefix", help="Candidate destination prefix."),
    ] = "",
    candidate_region: Annotated[
        str | None,
        typer.Option("--candidate-region", help="Candidate S3 region."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable packet."),
    ] = False,
) -> None:
    """Create a durable S3 migration decision packet without switching routes."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    root = FileProtectedRootStore(session_dir).get(tenant=tenant, root_id=root_id)
    if root is None:
        raise _exit(f"protected root {root_id!r} not found", code=1)
    if root.provider != "s3":
        raise _exit("migration packets are S3-only in the MVP", code=2)
    secrets = NimbusSecrets(session_dir)
    storage = _build_storage_for_profile(prof, secrets)
    if storage is None:
        raise _exit(
            "no storage client configured for this profile — "
            "run `nimbus auth local --aws` or set AWS credentials",
            code=2,
        )

    started = time.perf_counter()
    listed = storage.list_files(root.container, root.prefix)
    latency_ms = int((time.perf_counter() - started) * 1000)
    latest = FileGenerationStore(session_dir).latest_for_root(
        tenant=tenant,
        root_id=root.root_id,
    )
    object_count = latest.object_count if latest is not None else len(listed)
    total_bytes = (
        latest.total_bytes
        if latest is not None
        else sum(item.size_bytes or 0 for item in listed)
    )
    now = _dt.datetime.now(_dt.UTC)
    packet_id = _migration_packet_id(
        tenant=tenant,
        root_id=root.root_id,
        candidate_container=candidate_container,
        candidate_prefix=candidate_prefix,
        candidate_region=candidate_region,
    )
    packet = MigrationDecisionPacket(
        packet_id=packet_id,
        tenant=tenant,
        root_id=root.root_id,
        source_provider="s3",
        source_container=root.container,
        source_prefix=root.prefix,
        candidate_provider="s3",
        candidate_container=candidate_container,
        candidate_prefix=candidate_prefix,
        candidate_region=candidate_region,
        object_count=object_count,
        total_bytes=total_bytes,
        source_list_latency_ms=latency_ms,
        estimated_monthly_storage_cost_usd=_estimate_s3_storage_cost(total_bytes),
        assumptions=(
            "S3 Standard storage price estimated at 0.023 USD per GB-month.",
            "Candidate write, read, and checksum verification must run before apply.",
            "Route switching is approval-gated and not executed by evaluate.",
        ),
        safety_checks=(
            "latest generation exists" if latest is not None else "no generation yet",
            "source listing completed",
            "candidate remains S3-only in MVP",
            "rollback keeps source root unchanged",
        ),
        rollback_plan=(
            "Keep source root authoritative; abandon route-switch stack if "
            "candidate verification fails."
        ),
        route_switch_plan=(
            "Create fresh generation, copy objects to candidate, verify hashes, "
            "then propose an approval-gated route-switch stack."
        ),
        recommendation=(
            "ready_for_replica_plan" if object_count > 0 else "snapshot_source_first"
        ),
        created_at=now,
    )
    artifact = FileArtifactStore(session_dir).create(
        artifact=Artifact(
            artifact_id=f"art-{packet_id}",
            tenant=tenant,
            session_id=f"migration-{root.root_id}",
            action_id=None,
            kind="migration_decision_packet",
            uri=None,
            payload=packet,
            created_at=now,
        ),
        actor=_cli_actor(tenant=tenant, now=now),
    )
    if json_output:
        console.print_json(
            json.dumps(
                {
                    "packet": to_jsonable(packet),
                    "artifact": _artifact_to_jsonable(artifact),
                },
                sort_keys=True,
            )
        )
        return
    rows = [
        ui.KV("packet", packet.packet_id),
        ui.KV("artifact", artifact.artifact_id),
        ui.KV("root", packet.root_id),
        ui.KV("candidate", f"s3://{candidate_container}/{candidate_prefix}"),
        ui.KV("objects", str(packet.object_count)),
        ui.KV("bytes", _format_bytes(packet.total_bytes)),
        ui.KV("source_list_latency_ms", str(packet.source_list_latency_ms)),
        ui.KV(
            "estimated_monthly_storage_cost_usd",
            f"{packet.estimated_monthly_storage_cost_usd:.4f}",
        ),
        ui.KV("recommendation", packet.recommendation),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Migration decision packet"))


@heal_app.command("root")
def heal_root(
    root_id: Annotated[str, typer.Argument(help="Protected root ID.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    strict: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--strict/--no-strict", help="Treat unknown hashes as drift."),
    ] = False,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable health."),
    ] = False,
) -> None:
    """Verify the latest generation and report S3-only health/advice."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    root = FileProtectedRootStore(session_dir).get(tenant=tenant, root_id=root_id)
    if root is None:
        raise _exit(f"protected root {root_id!r} not found", code=1)
    generation = FileGenerationStore(session_dir).latest_for_root(
        tenant=tenant,
        root_id=root.root_id,
    )
    if generation is None:
        raise _exit(
            f"root {root_id!r} has no generation; run "
            f"`nimbus generation create {root_id}`",
            code=1,
        )
    artifact_store = FileArtifactStore(session_dir)
    manifest = _generation_manifest_from_artifact(
        artifact_store=artifact_store,
        tenant=tenant,
        artifact_id=generation.manifest_artifact_id,
    )
    storage = _build_storage_for_profile(prof, NimbusSecrets(session_dir))
    if storage is None:
        raise _exit(
            "no storage client configured for this profile — "
            "run `nimbus auth local --aws` or set AWS credentials",
            code=2,
        )
    now = _dt.datetime.now(_dt.UTC)
    report = verify_generation_manifest(
        manifest=manifest,
        manifest_artifact_id=generation.manifest_artifact_id,
        storage=storage,
        artifact_store=artifact_store,
        event_store=FileSessionEventStore(session_dir),
        actor=_cli_actor(tenant=tenant, now=now),
        session_id=f"heal-{root.root_id}",
        strict=strict,
        now=now,
    )
    health_score = _health_score(
        mismatch_count=report.mismatch_count,
        missing_count=report.missing_count,
        unknown_count=report.unknown_count,
        strict=strict,
        total_count=max(report.total_count, 1),
    )
    advice = _healing_advice(report_has_drift=report.has_drift, strict=strict)
    document = {
        "root_id": root.root_id,
        "generation_id": generation.generation_id,
        "manifest_artifact_id": generation.manifest_artifact_id,
        "health_score": health_score,
        "has_drift": report.has_drift,
        "strict": strict,
        "mismatch_count": report.mismatch_count,
        "missing_count": report.missing_count,
        "unknown_count": report.unknown_count,
        "advice": advice,
    }
    if json_output:
        console.print_json(json.dumps(document, sort_keys=True))
        if report.has_drift:
            raise typer.Exit(1)
        return
    rows = [
        ui.KV("root", root.root_id),
        ui.KV("generation", generation.generation_id),
        ui.KV("health_score", str(health_score)),
        ui.KV("has_drift", "yes" if report.has_drift else "no"),
        ui.KV("missing", str(report.missing_count)),
        ui.KV("mismatch", str(report.mismatch_count)),
        ui.KV("unknown", str(report.unknown_count)),
        ui.KV("next_step", advice),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Protected root health"))
    if report.has_drift:
        raise typer.Exit(1)


@heal_app.command("replica")
def heal_replica(
    source_manifest_artifact_id: Annotated[
        str,
        typer.Argument(help="Source generation manifest artifact ID."),
    ],
    replica_manifest_artifact_id: Annotated[
        str,
        typer.Option("--replica-manifest", help="Replica generation manifest ID."),
    ],
    root_id: Annotated[str, typer.Option("--root", help="Protected root ID.")] = (
        "root"
    ),
    source_prefix: Annotated[str, typer.Option("--source-prefix")] = "",
    replica_prefix: Annotated[str, typer.Option("--replica-prefix")] = "",
    allow_missing_repair: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--allow-missing-repair/--no-allow-missing-repair",
            help="Permit policy-authorized missing replica repair.",
        ),
    ] = False,
    apply_repair: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--apply/--dry-run",
            help="Copy missing replicas when the proposal is repairable.",
        ),
    ] = False,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable proposal."),
    ] = False,
) -> None:
    """Evaluate an S3 replica lane and report repairability."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    artifact_store = FileArtifactStore(session_dir)
    source_manifest = _generation_manifest_from_artifact(
        artifact_store=artifact_store,
        tenant=tenant,
        artifact_id=source_manifest_artifact_id,
    )
    replica_manifest = _generation_manifest_from_artifact(
        artifact_store=artifact_store,
        tenant=tenant,
        artifact_id=replica_manifest_artifact_id,
    )
    lane = ReplicaLane(
        lane_id=replica_lane_id(
            tenant=tenant,
            root_id=root_id,
            source_container=source_manifest.container,
            source_prefix=source_prefix or source_manifest.prefix,
            replica_container=replica_manifest.container,
            replica_prefix=replica_prefix or replica_manifest.prefix,
        ),
        tenant=tenant,
        root_id=root_id,
        provider="s3",
        source_container=source_manifest.container,
        source_prefix=source_prefix or source_manifest.prefix,
        replica_container=replica_manifest.container,
        replica_prefix=replica_prefix or replica_manifest.prefix,
        policy_allows_missing_replica_repair=allow_missing_repair,
        created_at=_dt.datetime.now(_dt.UTC),
        metadata={
            "source_manifest_artifact_id": source_manifest_artifact_id,
            "replica_manifest_artifact_id": replica_manifest_artifact_id,
        },
    )
    proposal = evaluate_replica_lane(
        lane=lane,
        source_manifest=source_manifest,
        replica_manifest=replica_manifest,
        now=_dt.datetime.now(_dt.UTC),
    )
    receipt_artifacts: list[Artifact] = []
    if apply_repair:
        if proposal.status != "repairable":
            raise _exit(
                f"replica lane is not repairable: {proposal.status}",
                code=1,
            )
        storage = _build_storage_for_profile(prof, NimbusSecrets(session_dir))
        if storage is None:
            raise _exit(
                "no storage client configured for this profile — "
                "run `nimbus auth local --aws` or set AWS credentials",
                code=2,
            )
        now = _dt.datetime.now(_dt.UTC)
        receipts = apply_missing_replica_repairs(
            proposal=proposal,
            client=_CliReplicaRepairClient(storage),
            authority="policy:repair-missing-replica",
            now=now,
        )
        actor = _cli_actor(tenant=tenant, now=now)
        receipt_artifacts.extend(
            (
                artifact_store.create(
                    artifact=Artifact(
                        artifact_id=f"art-{receipt.receipt_id}",
                        tenant=tenant,
                        session_id=f"heal-{proposal.proposal_id}",
                        action_id=None,
                        kind="repair_receipt",
                        uri=None,
                        payload=receipt,
                        created_at=receipt.repaired_at,
                    ),
                    actor=actor,
                )
            )
            for receipt in receipts
        )
    if json_output:
        console.print_json(
            json.dumps(
                {
                    "proposal": to_jsonable(proposal),
                    "applied": apply_repair,
                    "repair_receipts": [
                        to_jsonable(artifact.payload) for artifact in receipt_artifacts
                    ],
                    "artifacts": [
                        _artifact_to_jsonable(artifact)
                        for artifact in receipt_artifacts
                    ],
                },
                sort_keys=True,
            )
        )
        if proposal.status in {"blocked", "needs_reconciliation"}:
            raise typer.Exit(1)
        return
    rows = [
        ui.KV("proposal", proposal.proposal_id),
        ui.KV("status", ui.status_badge(proposal.status)),
        ui.KV("health_score", str(proposal.health_score)),
        ui.KV("missing", str(proposal.missing_replica_count)),
        ui.KV("mismatch", str(proposal.checksum_mismatch_count)),
        ui.KV("ambiguous", str(proposal.ambiguous_count)),
        ui.KV("repaired", str(len(receipt_artifacts))),
        ui.KV("next_step", proposal.next_step),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Replica lane health"))
    if proposal.status in {"blocked", "needs_reconciliation"}:
        raise typer.Exit(1)


# ── Plan commands ────────────────────────────────────────────────────────────

plan_app = typer.Typer(help="Inspect and act on Nimbus plans.")
app.add_typer(plan_app, name="plan")


@plan_app.command("list")
def plan_list(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable plans."),
    ] = False,
) -> None:
    """List recent Nimbus plans for the active tenant."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    plans = FilePlanStore(_profile_session_dir(prof)).list_for_tenant(
        tenant=tenant,
        limit=limit,
    )
    if json_output:
        console.print_json(json.dumps([to_jsonable(plan) for plan in plans]))
        return
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("plan", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("status")
    table.add_column("risk")
    table.add_column("title")
    table.add_column("task")
    table.add_column("updated")
    for plan in plans:
        table.add_row(
            plan.plan_id,
            ui.status_badge(plan.status.value),
            plan.risk_level.value,
            plan.title,
            plan.task_id or "—",
            plan.updated_at.isoformat(),
        )
    console.print(ui.card(table, title=f"Plans  •  {prof.name}"))


@plan_app.command("cleanup")
def plan_cleanup(
    manifest_artifact_id: Annotated[
        str,
        typer.Argument(help="Manifest artifact ID to inspect for duplicates."),
    ],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print candidate plans as JSON."),
    ] = False,
) -> None:
    """Generate candidate cleanup plans from a manifest artifact."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    artifact_store = FileArtifactStore(session_dir)
    artifact = artifact_store.get(tenant=tenant, artifact_id=manifest_artifact_id)
    if artifact is None:
        raise _exit(f"manifest artifact {manifest_artifact_id!r} not found", code=1)
    now = _dt.datetime.now(_dt.UTC)
    candidates = build_cleanup_plan_candidates(
        manifest_artifact=artifact,
        actor=_cli_actor(tenant=tenant, now=now),
        now=now,
    )
    if not candidates:
        if json_output:
            console.print_json(json.dumps({"candidate_plans": []}, sort_keys=True))
            return
        ui.info(console, "No duplicate cleanup candidates found.")
        return

    plan_store = FilePlanStore(
        session_dir,
        event_store=FileSessionEventStore(session_dir),
    )
    created = tuple(
        plan_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key=candidate.idempotency_key,
            create=_plan_factory(candidate),
        )
        for candidate in candidates
    )
    if json_output:
        console.print_json(
            json.dumps(
                {
                    "candidate_group_id": created[0].metadata.get("candidate_group_id"),
                    "candidate_plans": [to_jsonable(plan) for plan in created],
                },
                sort_keys=True,
            )
        )
        return
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("plan", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("strategy")
    table.add_column("risk")
    table.add_column("targets", justify="right")
    table.add_column("summary")
    for plan in created:
        table.add_row(
            plan.plan_id,
            str(plan.metadata.get("candidate_strategy", "unknown")),
            plan.risk_level.value,
            str(plan.estimated_count or 0),
            plan.summary,
        )
    console.print(ui.card(table, title="Candidate cleanup plans"))


@plan_app.command("show")
def plan_show(
    plan_id: Annotated[str, typer.Argument(help="Plan ID to inspect.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print a stable machine-readable plan."),
    ] = False,
) -> None:
    """Show full details for a pending or historical plan."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    plan_store = FilePlanStore(session_dir)
    plan = plan_store.get(tenant=tenant, plan_id=plan_id)
    if plan is None:
        raise _exit(f"plan {plan_id!r} not found", code=1)
    if json_output:
        console.print_json(json.dumps(to_jsonable(plan), sort_keys=True))
        return

    risk_icon = {
        "read_only": ui.ICON_OK,
        "small_write": "🟡",
        "large_write": "🟠",
        "destructive": ui.ICON_WARN,
        "admin_scope": "🔐",
    }.get(plan.risk_level.value, ui.ICON_INFO)

    rows: list[ui.KV] = [
        ui.KV("plan", plan.plan_id),
        ui.KV("status", ui.status_badge(plan.status.value)),
        ui.KV("title", plan.title),
        ui.KV("risk", Text(f"{risk_icon} {plan.risk_level.value.replace('_', ' ')}")),
        ui.KV("summary", plan.summary),
        ui.KV("session", plan.session_id),
        ui.KV("created", plan.created_at.isoformat()),
    ]
    if plan.task_id:
        rows.append(ui.KV("task", plan.task_id))
    if plan.estimated_count is not None:
        rows.append(ui.KV("files", str(plan.estimated_count)))
    if plan.estimated_bytes is not None:
        rows.append(ui.KV("size", _format_bytes(plan.estimated_bytes)))

    console.print(
        ui.card(
            ui.kv_table(rows),
            title=f"Plan  •  {plan.plan_id}",
        )
    )


@plan_app.command("diff")
def plan_diff(
    plan_id: Annotated[str, typer.Argument(help="Plan ID to inspect.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print a stable machine-readable plan diff."),
    ] = False,
) -> None:
    """Render the target and metadata a plan intends to change."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    plan = FilePlanStore(_profile_session_dir(prof)).get(
        tenant=tenant,
        plan_id=plan_id,
    )
    if plan is None:
        raise _exit(f"plan {plan_id!r} not found", code=1)
    document = _plan_diff_document(plan)
    if json_output:
        console.print_json(json.dumps(document, sort_keys=True))
        return
    rows = [
        ui.KV("plan", plan.plan_id),
        ui.KV("risk", plan.risk_level.value),
        ui.KV("status", plan.status.value),
        ui.KV("operation", str(document["operation"])),
        ui.KV("target", str(document["target"])),
        ui.KV("restore_story", str(document["restore_story"])),
    ]
    console.print(ui.card(ui.kv_table(rows), title=f"Plan diff  •  {plan.plan_id}"))


@plan_app.command("approve")
def plan_approve(
    plan_id: Annotated[str, typer.Argument(help="Plan ID to approve.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print the updated plan as JSON."),
    ] = False,
) -> None:
    """Approve a proposed plan without executing it in-process."""
    _transition_plan_cli(
        plan_id=plan_id,
        profile=profile,
        workspace=workspace,
        expected=PlanStatus.PROPOSED,
        next_status=PlanStatus.APPROVED,
        event_type="plan_approved",
        verb="approved",
        json_output=json_output,
    )


@plan_app.command("reject")
def plan_reject(
    plan_id: Annotated[str, typer.Argument(help="Plan ID to reject.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print the updated plan as JSON."),
    ] = False,
) -> None:
    """Reject a proposed plan and leave the audit trail intact."""
    _transition_plan_cli(
        plan_id=plan_id,
        profile=profile,
        workspace=workspace,
        expected=PlanStatus.PROPOSED,
        next_status=PlanStatus.REJECTED,
        event_type="plan_rejected",
        verb="rejected",
        json_output=json_output,
    )


@plan_app.command("apply")
def plan_apply(
    plan_id: Annotated[str, typer.Argument(help="Plan ID to apply.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    yes: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation."),
    ] = False,
) -> None:
    """Approve and apply a pending plan (moves it to 'approved' status).

    The backing worker will pick it up on its next scan and execute it.
    """
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    plan_store = FilePlanStore(session_dir)
    plan = plan_store.get(tenant=tenant, plan_id=plan_id)
    if plan is None:
        raise _exit(f"plan {plan_id!r} not found", code=1)

    if plan.status.value != "proposed":
        raise _exit(
            f"plan {plan_id!r} is in status {plan.status.value!r} — "
            "only proposed plans can be applied",
            code=1,
        )

    risk_level = plan.risk_level.value.replace("_", " ")
    console.print(
        f"[bold]Plan:[/bold] {plan.title}\n"
        f"[bold]Risk:[/bold] {risk_level}\n"
        f"[bold]Summary:[/bold] {plan.summary}"
    )

    if not yes:
        confirm = Prompt.ask(
            f"[{_ACCENT_STYLE}]Apply this plan?[/] (yes/no)", default="no"
        )
        if confirm.strip().lower() not in {"yes", "y"}:
            console.print("Aborted.")
            return

    candidate_group_id = plan.metadata.get("candidate_group_id")
    if isinstance(candidate_group_id, str):
        updated = plan_store.approve_candidate_group(
            tenant=tenant,
            plan_id=plan_id,
            candidate_group_id=candidate_group_id,
            event_payload={"approved_by": "cli"},
        )
    else:
        updated = plan_store.transition(
            tenant=tenant,
            plan_id=plan_id,
            transition=PlanTransition(
                expected=plan.status,
                next_status=PlanStatus.APPROVED,
                event_type="plan_approved",
                event_payload={"approved_by": "cli", "plan_id": plan_id},
            ),
        )
    if updated is None:
        raise _exit(
            f"plan {plan_id!r} could not be applied — status changed concurrently",
            code=1,
        )

    ui.success(console, f"Plan {plan_id} approved — worker will apply it shortly")
    if plan.task_id:
        ui.hint(console, f"run `nimbus task watch {plan.task_id}` to follow progress")


def _transition_plan_cli(
    *,
    plan_id: str,
    profile: str | None,
    workspace: str | None,
    expected: PlanStatus,
    next_status: PlanStatus,
    event_type: str,
    verb: str,
    json_output: bool,
) -> None:
    """Apply one explicit plan status transition from the CLI."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    plan_store = FilePlanStore(_profile_session_dir(prof))
    plan = plan_store.get(tenant=tenant, plan_id=plan_id)
    if plan is None:
        raise _exit(f"plan {plan_id!r} not found", code=1)
    candidate_group_id = plan.metadata.get("candidate_group_id")
    if next_status is PlanStatus.APPROVED and isinstance(candidate_group_id, str):
        updated = plan_store.approve_candidate_group(
            tenant=tenant,
            plan_id=plan_id,
            candidate_group_id=candidate_group_id,
            event_payload={"decided_by": "cli"},
        )
    else:
        updated = plan_store.transition(
            tenant=tenant,
            plan_id=plan_id,
            transition=PlanTransition(
                expected=expected,
                next_status=next_status,
                event_type=event_type,
                event_payload={"plan_id": plan_id, "decided_by": "cli"},
            ),
        )
    if updated is None:
        raise _exit(
            f"plan {plan_id!r} could not be {verb} — expected "
            f"{expected.value}, found {plan.status.value}",
            code=1,
        )
    if json_output:
        console.print_json(json.dumps(to_jsonable(updated), sort_keys=True))
        return
    ui.success(console, f"Plan {plan_id} {verb}")


def _plan_diff_document(plan: Plan) -> dict[str, object]:
    """Return a stable plan-diff document without pretending execution happened."""
    target = None
    if plan.target is not None:
        target = {
            "provider": plan.target.provider,
            "container": plan.target.container,
            "object_name": plan.target.object_name,
            "version_id": plan.target.version_id,
        }
    metadata = dict(plan.metadata)
    return {
        "plan_id": plan.plan_id,
        "status": plan.status.value,
        "risk_level": plan.risk_level.value,
        "operation": metadata.get("operation", "preview"),
        "target": target or metadata.get("target", "not specified"),
        "estimated_count": plan.estimated_count,
        "estimated_bytes": plan.estimated_bytes,
        "restore_story": metadata.get(
            "restore_story",
            "not recorded; destructive execution must provide restore evidence",
        ),
        "approval_binding": {
            "tenant_id": plan.tenant.tenant_id,
            "plan_id": plan.plan_id,
            "task_id": plan.task_id,
            "action_id": plan.action_id,
            "idempotency_key": plan.idempotency_key,
        },
        "metadata": metadata,
    }


def _plan_factory(plan: Plan) -> Callable[[], Plan]:
    """Return a typed factory for idempotent plan creation."""

    def _create() -> Plan:
        return plan

    return _create


# ── Storage admin commands ───────────────────────────────────────────────────

from nimbus_cli.storage_admin import storage_app  # noqa: E402

app.add_typer(storage_app, name="storage")


# ── Artifact commands ────────────────────────────────────────────────────────

artifact_app = typer.Typer(help="Inspect Nimbus task artifacts.")
app.add_typer(artifact_app, name="artifact")


@artifact_app.command("show")
def artifact_show(
    artifact_id: Annotated[str, typer.Argument(help="Artifact ID to inspect.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
) -> None:
    """Show the full payload of one artifact produced by a Nimbus task."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    artifact_store = FileArtifactStore(session_dir)
    artifact = artifact_store.get(tenant=tenant, artifact_id=artifact_id)
    if artifact is None:
        raise _exit(f"artifact {artifact_id!r} not found", code=1)

    rows: list[ui.KV] = [
        ui.KV("artifact", artifact.artifact_id),
        ui.KV("kind", artifact.kind),
        ui.KV("session", artifact.session_id),
        ui.KV("payload_digest", artifact.payload_digest or "not recorded"),
        ui.KV("created", artifact.created_at.isoformat()),
    ]
    if artifact.action_id:
        rows.append(ui.KV("action", artifact.action_id))
    if artifact.uri:
        rows.append(ui.KV("uri", artifact.uri))

    # Show payload fields inline.
    payload = artifact.payload
    if payload:
        for key, val in vars(payload).items() if hasattr(payload, "__dict__") else []:
            if val is not None:
                rows.append(ui.KV(key, str(val)))

    console.print(
        ui.card(
            ui.kv_table(rows),
            title=f"Artifact  •  {artifact.artifact_id}",
        )
    )


# ── Evidence commands ──────────────────────────────────────────────────────


@evidence_app.command("export")
def evidence_export(
    artifact_id: Annotated[str, typer.Argument(help="Artifact payload to export.")],
    root: Annotated[
        Path | None,
        typer.Option("--root", help="Evidence object root directory."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable evidence."),
    ] = False,
) -> None:
    """Export one artifact payload to the local content-addressed evidence store."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    artifact_store = FileArtifactStore(session_dir)
    artifact = artifact_store.get(tenant=tenant, artifact_id=artifact_id)
    if artifact is None:
        raise _exit(f"artifact {artifact_id!r} not found", code=1)
    evidence_root = _evidence_root(root=root, session_dir=session_dir)
    record = export_artifact_payload(
        artifact=artifact,
        root=evidence_root,
        exported_at=_dt.datetime.now(_dt.UTC),
    )
    payload = {
        "record": evidence_record_to_json(record),
        "verified": True,
        "root": str(evidence_root),
    }
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    rows = [
        ui.KV("artifact", record.artifact_id),
        ui.KV("kind", record.kind),
        ui.KV("payload_digest", record.payload_digest),
        ui.KV("object_uri", record.object_uri),
        ui.KV("stored_bytes", str(record.stored_bytes)),
        ui.KV("status", record.verification_status),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Evidence export"))


@evidence_app.command("preview")
def evidence_preview(
    artifact_id: Annotated[str, typer.Argument(help="Artifact payload to preview.")],
    root: Annotated[
        Path | None,
        typer.Option("--root", help="Evidence object root directory."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable preview."),
    ] = False,
) -> None:
    """Preview one artifact and report whether its evidence object exists."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    artifact_store = FileArtifactStore(session_dir)
    artifact = artifact_store.get(tenant=tenant, artifact_id=artifact_id)
    if artifact is None:
        raise _exit(f"artifact {artifact_id!r} not found", code=1)
    evidence_root = _evidence_root(root=root, session_dir=session_dir)
    preview = preview_artifact(
        artifact=artifact,
        root=evidence_root,
        generated_at=_dt.datetime.now(_dt.UTC),
    )
    payload = {"preview": evidence_preview_to_json(preview), "root": str(evidence_root)}
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    rows = [
        ui.KV("artifact", preview.artifact_id),
        ui.KV("kind", preview.kind),
        ui.KV("summary", preview.summary),
        ui.KV("evidence_available", str(preview.evidence_available).lower()),
        ui.KV("object_uri", preview.evidence_uri),
        ui.KV("next_step", preview.next_step),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Evidence preview"))


@evidence_app.command("compact")
def evidence_compact(
    artifact_ids: Annotated[
        list[str],
        typer.Argument(help="Artifact IDs to export and compact."),
    ],
    root: Annotated[
        Path | None,
        typer.Option("--root", help="Evidence object root directory."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable bundle."),
    ] = False,
) -> None:
    """Export artifacts and write a compacted evidence bundle index."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    artifact_store = FileArtifactStore(session_dir)
    evidence_root = _evidence_root(root=root, session_dir=session_dir)
    now = _dt.datetime.now(_dt.UTC)
    records = []
    for artifact_id in artifact_ids:
        artifact = artifact_store.get(tenant=tenant, artifact_id=artifact_id)
        if artifact is None:
            raise _exit(f"artifact {artifact_id!r} not found", code=1)
        records.append(
            export_artifact_payload(
                artifact=artifact,
                root=evidence_root,
                exported_at=now,
            )
        )
    bundle = compact_evidence_records(
        records=tuple(records),
        root=evidence_root,
        compacted_at=now,
    )
    payload = {
        "bundle": evidence_bundle_to_json(bundle),
        "records": [evidence_record_to_json(record) for record in records],
        "root": str(evidence_root),
    }
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    rows = [
        ui.KV("bundle", bundle.bundle_id),
        ui.KV("artifacts", str(bundle.artifact_count)),
        ui.KV("bundle_uri", bundle.bundle_uri),
        ui.KV("stored_bytes", str(bundle.stored_bytes)),
        ui.KV("status", bundle.verification_status),
        ui.KV("next_step", bundle.next_step),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Evidence bundle"))


# ── Proof commands ──────────────────────────────────────────────────────────

proof_app = typer.Typer(help="Validate and inspect Nimbus proof receipts.")
app.add_typer(proof_app, name="proof")


@proof_app.command("show")
def proof_show(
    receipt_id: Annotated[
        str,
        typer.Argument(help="Proof receipt artifact ID, or 'latest'."),
    ] = "latest",
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print a stable machine-readable document."),
    ] = False,
) -> None:
    """Show and validate a proof receipt and its linked artifacts."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    artifact_store = FileArtifactStore(session_dir)

    if receipt_id == "latest":
        receipts = artifact_store.list_for_tenant(
            tenant=tenant,
            kind="proof_receipt",
            limit=1,
        )
        if not receipts:
            raise _exit(f"no proof receipts found for profile {prof.name!r}", code=1)
        artifact = receipts[0]
    else:
        found = artifact_store.get(tenant=tenant, artifact_id=receipt_id)
        if found is None:
            raise _exit(f"proof receipt {receipt_id!r} not found", code=1)
        artifact = found

    if artifact.kind != "proof_receipt" or not isinstance(
        artifact.payload, ProofReceipt
    ):
        raise _exit(
            f"artifact {artifact.artifact_id!r} is a {artifact.kind!r}, "
            "expected 'proof_receipt'",
            code=2,
        )

    receipt = artifact.payload
    linked_artifacts = {
        linked_id: linked
        for linked_id in receipt.linked_artifact_ids
        if (linked := artifact_store.get(tenant=tenant, artifact_id=linked_id))
        is not None
    }
    failures = validate_proof_receipt_links(
        receipt=receipt,
        artifacts_by_id=linked_artifacts,
    )

    if json_output:
        console.print_json(
            json.dumps(
                {
                    "receipt": to_jsonable(receipt),
                    "receipt_artifact": _artifact_to_jsonable(artifact),
                    "linked_artifacts": {
                        artifact_id: _artifact_to_jsonable(item)
                        for artifact_id, item in linked_artifacts.items()
                    },
                    "valid": not failures,
                    "validation_failures": list(failures),
                    "next_operator_step": _proof_next_operator_step(failures),
                },
                sort_keys=True,
            )
        )
        if failures:
            raise typer.Exit(1)
        return

    status = "valid" if not failures else "invalid"
    rows: list[ui.KV] = [
        ui.KV("receipt", receipt.receipt_id),
        ui.KV("status", ui.status_badge(status)),
        ui.KV("subject", receipt.subject),
        ui.KV("outcome", receipt.outcome),
        ui.KV("summary", receipt.summary),
        ui.KV("session", receipt.session_id),
        ui.KV("task", receipt.task_id or "—"),
        ui.KV("action", receipt.action_id or "—"),
        ui.KV("manifest", receipt.manifest_artifact_id or "—"),
        ui.KV("verifier", receipt.verifier_artifact_id or "—"),
        ui.KV("policy_version", receipt.policy_version),
        ui.KV("created", receipt.created_at.isoformat()),
    ]
    if receipt.event_range_start is not None and receipt.event_range_end is not None:
        rows.append(
            ui.KV(
                "event_range",
                f"{receipt.event_range_start}..{receipt.event_range_end}",
            )
        )
    console.print(ui.card(ui.kv_table(rows), title=f"Proof  •  {receipt.receipt_id}"))

    linked_table = Table(show_header=True, header_style="bold", box=None)
    linked_table.add_column("artifact", style=f"bold {ui.PRIMARY}", no_wrap=True)
    linked_table.add_column("kind")
    linked_table.add_column("digest")
    for linked_id in receipt.linked_artifact_ids:
        linked = linked_artifacts.get(linked_id)
        linked_table.add_row(
            linked_id,
            linked.kind if linked is not None else "missing",
            receipt.artifact_digests.get(linked_id, "missing"),
        )
    console.print(ui.card(linked_table, title="Linked evidence"))

    if failures:
        for failure in failures:
            ui.error(console, failure)
        ui.hint(console, _proof_next_operator_step(failures))
        raise typer.Exit(1)

    for step in receipt.next_steps:
        ui.hint(console, step)


def _artifact_to_jsonable(artifact: Artifact) -> dict[str, object]:
    return {
        "artifact_id": artifact.artifact_id,
        "kind": artifact.kind,
        "session_id": artifact.session_id,
        "action_id": artifact.action_id,
        "payload_digest": artifact.payload_digest,
        "created_at": artifact.created_at.isoformat(),
        "payload": to_jsonable(artifact.payload),
    }


def _proof_next_operator_step(failures: tuple[str, ...]) -> str:
    if not failures:
        return (
            "Proof is valid. Use the linked manifest and verifier artifacts "
            "for audit detail."
        )
    if any("missing" in failure for failure in failures):
        return (
            "Re-run the task or restore the missing artifact store backup before "
            "claiming success."
        )
    if any("digest" in failure for failure in failures):
        return (
            "Treat this proof as tampered or stale; regenerate evidence from "
            "live storage."
        )
    return "Inspect the receipt and linked artifacts, then re-run verification."


# ── Workspace commands ──────────────────────────────────────────────────────


@workspace_app.command("at")
def workspace_at(
    timestamp: Annotated[
        str,
        typer.Argument(
            help="ISO 8601 timestamp (UTC). Example: 2024-01-15T12:00:00Z",
        ),
    ],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max events to replay (default 10 000)."),
    ] = 10_000,
) -> None:
    """Show workspace state reconstructed at a past timestamp.

    Re-plays the event log up to the given moment and prints a summary of
    tasks, pending approvals, pending plans, and artifact count.
    """
    import datetime

    from nimbus_runtime.domain import FutureTimestampError
    from nimbus_runtime.projection import project_workspace_at

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    try:
        at = datetime.datetime.fromisoformat(timestamp)
    except ValueError as exc:
        raise _exit(
            f"invalid timestamp {timestamp!r} — expected ISO 8601 "
            f"(e.g. 2024-01-15T12:00:00Z)",
            code=2,
        ) from exc

    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.UTC)

    event_store = FileSessionEventStore(session_dir)
    events = event_store.list_for_tenant_before(tenant=tenant, before=at, limit=limit)

    try:
        snap = project_workspace_at(tenant=tenant, at=at, events=events)
    except FutureTimestampError as exc:
        raise _exit(str(exc), code=2) from exc

    rows: list[ui.KV] = [
        ui.KV("at", snap.at.isoformat()),
        ui.KV("profile", prof.name),
        ui.KV("events replayed", str(snap.events_replayed)),
        ui.KV("computed in", f"{snap.computation_duration_ms} ms"),
        ui.KV("artifacts", str(snap.artifact_count)),
    ]

    for status, count in sorted(snap.tasks_by_status.items()):
        rows.append(ui.KV(f"tasks/{status}", str(count)))

    total_tasks = sum(snap.tasks_by_status.values())
    if not snap.tasks_by_status:
        rows.append(ui.KV("tasks", "0"))

    console.print(
        ui.card(ui.kv_table(rows), title=f"Workspace snapshot  •  {prof.name}")
    )

    if snap.pending_plans:
        plan_table = Table(show_header=True, header_style="bold", box=None)
        plan_table.add_column("plan_id", style="cyan", no_wrap=True)
        plan_table.add_column("title")
        plan_table.add_column("risk")
        plan_table.add_column("status")
        for p in snap.pending_plans:
            plan_table.add_row(
                p.plan_id,
                p.title or "—",
                p.risk_level or "—",
                ui.status_badge(p.status),
            )
        n_plans = len(snap.pending_plans)
        console.print(ui.card(plan_table, title=f"Pending plans ({n_plans})"))

    if snap.pending_approvals:
        appr_table = Table(show_header=True, header_style="bold", box=None)
        appr_table.add_column("approval_id", style="cyan", no_wrap=True)
        appr_table.add_column("target")
        appr_table.add_column("risk")
        appr_table.add_column("required actor")
        for a in snap.pending_approvals:
            appr_table.add_row(
                a.approval_id,
                a.exact_target or "—",
                a.risk_level or "—",
                a.required_actor_id or "—",
            )
        console.print(
            ui.card(
                appr_table,
                title=f"Pending approvals ({len(snap.pending_approvals)})",
            )
        )

    if not snap.pending_plans and not snap.pending_approvals and total_tasks == 0:
        ui.info(console, "No activity recorded before this timestamp.")


@workspace_app.command("diff")
def workspace_diff(
    timestamp_a: Annotated[
        str,
        typer.Argument(help="Earlier ISO 8601 timestamp (UTC)."),
    ],
    timestamp_b: Annotated[
        str,
        typer.Argument(help="Later ISO 8601 timestamp (UTC)."),
    ],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", help="Max events per snapshot to replay."),
    ] = 10_000,
) -> None:
    """Show the delta between two workspace snapshots.

    Projects the workspace at both timestamps and prints what changed:
    task status counts, new/resolved approvals, new/resolved plans, and
    artifact delta.
    """
    import datetime

    from nimbus_runtime.domain import FutureTimestampError
    from nimbus_runtime.projection import project_workspace_at

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    def _parse_ts(raw: str) -> datetime.datetime:
        try:
            dt = datetime.datetime.fromisoformat(raw)
        except ValueError as exc:
            raise _exit(
                f"invalid timestamp {raw!r} — expected ISO 8601 "
                f"(e.g. 2024-01-15T12:00:00Z)",
                code=2,
            ) from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        return dt

    at_a = _parse_ts(timestamp_a)
    at_b = _parse_ts(timestamp_b)

    if at_a >= at_b:
        raise _exit(
            f"timestamp_a ({timestamp_a}) must be strictly before "
            f"timestamp_b ({timestamp_b})",
            code=2,
        )

    event_store = FileSessionEventStore(session_dir)

    try:
        events_a = event_store.list_for_tenant_before(
            tenant=tenant, before=at_a, limit=limit
        )
        snap_a = project_workspace_at(tenant=tenant, at=at_a, events=events_a)
        events_b = event_store.list_for_tenant_before(
            tenant=tenant, before=at_b, limit=limit
        )
        snap_b = project_workspace_at(tenant=tenant, at=at_b, events=events_b)
    except FutureTimestampError as exc:
        raise _exit(str(exc), code=2) from exc

    rows: list[ui.KV] = [
        ui.KV("from", snap_a.at.isoformat()),
        ui.KV("to", snap_b.at.isoformat()),
        ui.KV("events replayed (A)", str(snap_a.events_replayed)),
        ui.KV("events replayed (B)", str(snap_b.events_replayed)),
    ]

    artifact_delta = snap_b.artifact_count - snap_a.artifact_count
    if artifact_delta != 0:
        sign = "+" if artifact_delta > 0 else ""
        rows.append(ui.KV("artifacts", f"{sign}{artifact_delta}"))

    all_statuses = set(snap_a.tasks_by_status) | set(snap_b.tasks_by_status)
    for status in sorted(all_statuses):
        count_a = snap_a.tasks_by_status.get(status, 0)
        count_b = snap_b.tasks_by_status.get(status, 0)
        delta = count_b - count_a
        if delta != 0:
            sign = "+" if delta > 0 else ""
            rows.append(ui.KV(f"tasks/{status}", f"{sign}{delta}"))

    console.print(ui.card(ui.kv_table(rows), title=f"Workspace diff  •  {prof.name}"))

    ids_a = {a.approval_id for a in snap_a.pending_approvals}
    ids_b = {a.approval_id for a in snap_b.pending_approvals}
    new_approvals = [a for a in snap_b.pending_approvals if a.approval_id not in ids_a]
    resolved_approvals = ids_a - ids_b

    if new_approvals or resolved_approvals:
        appr_table = Table(show_header=True, header_style="bold", box=None)
        appr_table.add_column("change")
        appr_table.add_column("approval_id", style="cyan", no_wrap=True)
        appr_table.add_column("target")
        for a in new_approvals:
            appr_table.add_row(
                "[green]+new[/green]", a.approval_id, a.exact_target or "—"
            )
        for aid in sorted(resolved_approvals):
            appr_table.add_row("[dim]-resolved[/dim]", aid, "")
        console.print(ui.card(appr_table, title="Approval changes"))

    plan_ids_a = {p.plan_id for p in snap_a.pending_plans}
    plan_ids_b = {p.plan_id for p in snap_b.pending_plans}
    new_plans = [p for p in snap_b.pending_plans if p.plan_id not in plan_ids_a]
    resolved_plans = plan_ids_a - plan_ids_b

    if new_plans or resolved_plans:
        plan_table = Table(show_header=True, header_style="bold", box=None)
        plan_table.add_column("change")
        plan_table.add_column("plan_id", style="cyan", no_wrap=True)
        plan_table.add_column("title")
        for p in new_plans:
            plan_table.add_row("[green]+new[/green]", p.plan_id, p.title or "—")
        for pid in sorted(resolved_plans):
            plan_table.add_row("[dim]-resolved[/dim]", pid, "")
        console.print(ui.card(plan_table, title="Plan changes"))

    no_task_delta = not any(
        snap_b.tasks_by_status.get(s, 0) != snap_a.tasks_by_status.get(s, 0)
        for s in all_statuses
    )
    nothing_changed = (
        no_task_delta
        and artifact_delta == 0
        and not new_approvals
        and not resolved_approvals
        and not new_plans
        and not resolved_plans
    )
    if nothing_changed:
        ui.info(console, "No changes between the two timestamps.")


# ── Verify commands ─────────────────────────────────────────────────────────

verify_app = typer.Typer(help="Verify manifest integrity against live storage.")
app.add_typer(verify_app, name="verify")


@verify_app.callback(invoke_without_command=True)
def verify_callback(
    ctx: typer.Context,
    manifest_id: Annotated[
        str | None,
        typer.Argument(help="Manifest artifact ID. Alias for `verify manifest`."),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    strict: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--strict/--no-strict",
            help="Treat unknown (un-hashable) objects as drift.",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Session to attach the drift artifact to."),
    ] = None,
) -> None:
    """Allow `nimbus verify <manifest-id>` as the short manifest verifier."""
    if ctx.invoked_subcommand is not None:
        return
    if manifest_id is None:
        console = Console()
        console.print(ctx.get_help())
        raise typer.Exit(0)
    _run_verify_manifest_command(
        artifact_id=manifest_id,
        profile=profile,
        workspace=workspace,
        strict=strict,
        session_id=session_id,
    )


@verify_app.command("manifest")
def verify_manifest_cmd(
    artifact_id: Annotated[
        str, typer.Argument(help="Artifact ID of the manifest to verify.")
    ],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    strict: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--strict/--no-strict",
            help="Treat unknown (un-hashable) objects as drift.",
        ),
    ] = False,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Session to attach the drift artifact to."),
    ] = None,
) -> None:
    """Compare every object in a manifest against live storage.

    Loads a previously created manifest artifact, HEAD-checks every S3 object
    recorded in it, and writes a drift_report artifact to the local store.
    Exits with code 1 if drift is detected.
    """
    _run_verify_manifest_command(
        artifact_id=artifact_id,
        profile=profile,
        workspace=workspace,
        strict=strict,
        session_id=session_id,
    )


def _run_verify_manifest_command(
    *,
    artifact_id: str,
    profile: str | None,
    workspace: str | None,
    strict: bool,
    session_id: str | None,
) -> None:
    import datetime

    from nimbus_runtime.domain import ManifestReport
    from nimbus_runtime.drift_verifier import verify_manifest

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    artifact_store = FileArtifactStore(session_dir)
    artifact = artifact_store.get(tenant=tenant, artifact_id=artifact_id)
    if artifact is None:
        raise _exit(f"artifact {artifact_id!r} not found", code=1)
    if artifact.kind != "manifest" or not isinstance(
        artifact.payload,
        ManifestReport | GenerationManifest,
    ):
        raise _exit(
            f"artifact {artifact_id!r} is a {artifact.kind!r}, expected 'manifest'",
            code=2,
        )

    manifest = artifact.payload
    sid = session_id or artifact.session_id
    now = datetime.datetime.now(datetime.UTC)
    actor = VerifiedActor(
        tenant=tenant,
        user_id="cli",
        auth_source="cli_local",
        bridge_id=None,
        verified_at=now,
    )
    secrets = NimbusSecrets(session_dir)

    storage = _build_storage_for_profile(prof, secrets)
    if storage is None:
        raise _exit(
            "no storage client configured for this profile — "
            "run `nimbus auth local --aws` or set AWS credentials",
            code=2,
        )

    event_store = FileSessionEventStore(session_dir)

    with ui.thinking(console, "Verifying manifest…"):
        if isinstance(manifest, GenerationManifest):
            report = verify_generation_manifest(
                manifest=manifest,
                manifest_artifact_id=artifact_id,
                storage=storage,
                artifact_store=artifact_store,
                event_store=event_store,
                actor=actor,
                session_id=sid,
                strict=strict,
            )
        else:
            report = verify_manifest(
                manifest,
                artifact_id,
                storage,
                artifact_store,
                event_store,
                actor=actor,
                session_id=sid,
                strict=strict,
            )

    status_icon = ui.ICON_WARN if report.has_drift else ui.ICON_OK
    rows: list[ui.KV] = [
        ui.KV("manifest", artifact_id),
        ui.KV("container", report.container),
        ui.KV("prefix", report.prefix),
        ui.KV("checked_at", report.checked_at.isoformat()),
        ui.KV("total", str(report.total_count)),
        ui.KV("match", str(report.match_count)),
        ui.KV("mismatch", str(report.mismatch_count)),
        ui.KV("missing", str(report.missing_count)),
        ui.KV("unknown", str(report.unknown_count)),
    ]
    if report.bucket_missing:
        rows.append(ui.KV("bucket_missing", "yes"))

    console.print(
        ui.card(
            ui.kv_table(rows),
            title=f"{status_icon} Drift report  •  {prof.name}",
        )
    )

    if report.has_drift:
        drift_entries = [e for e in report.entries if e.status != "match"]
        drift_table = Table(show_header=True, header_style="bold", box=None)
        drift_table.add_column("status", style="bold")
        drift_table.add_column("object_key", style="cyan")
        drift_table.add_column("name")
        drift_table.add_column("expected")
        drift_table.add_column("observed")
        for entry in drift_entries:
            drift_table.add_row(
                entry.status,
                entry.object_key,
                entry.name,
                entry.expected_sha256[:16] + "…",
                (entry.observed_sha256 or "—")[:16]
                + ("…" if entry.observed_sha256 else ""),
            )
        console.print(ui.card(drift_table, title="Drifted objects"))
        raise typer.Exit(1)

    ui.hint(console, "All objects verified — no drift detected.")


# ── Provider health commands ────────────────────────────────────────────────


@provider_app.command("capabilities")
def provider_capabilities_cmd(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable capabilities."),
    ] = False,
) -> None:
    """Describe optional storage-provider features visible to Nimbus."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    session_dir = _profile_session_dir(prof)
    storage = _build_storage_for_profile(prof, NimbusSecrets(session_dir))
    if storage is None:
        raise _exit(
            "no storage client configured for this profile — "
            "run `nimbus auth local --aws` or set AWS credentials",
            code=2,
        )
    capabilities = discover_provider_capabilities(storage)
    supported = sorted(capability.value for capability in capabilities.capabilities)
    supports = {
        capability.value: capabilities.supports(capability)
        for capability in ProviderCapability
    }
    payload = {
        "provider_name": capabilities.provider_name,
        "capabilities": supported,
        "supports": supports,
    }
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return

    rows = [
        ui.KV("provider", capabilities.provider_name),
        ui.KV("capabilities", ", ".join(supported) if supported else "none"),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Provider capabilities"))


@provider_app.command("health")
def provider_health_cmd(
    container: Annotated[
        str | None,
        typer.Option("--container", "-c", help="Storage bucket/container to probe."),
    ] = None,
    prefix: Annotated[
        str,
        typer.Option("--prefix", help="Prefix for the bounded LIST probe."),
    ] = "",
    head_key: Annotated[
        str | None,
        typer.Option("--head-key", help="Optional object key for a HEAD probe."),
    ] = None,
    max_list_keys: Annotated[
        int,
        typer.Option("--max-list-keys", help="Bounded LIST probe page size."),
    ] = 1,
    ttl_seconds: Annotated[
        int,
        typer.Option("--ttl-seconds", help="How long the health artifact is fresh."),
    ] = 300,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    session_id: Annotated[
        str | None,
        typer.Option("--session", help="Session to attach the health artifact to."),
    ] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable health evidence."),
    ] = False,
) -> None:
    """Probe configured storage and write provider-health evidence.

    This command uses live Nimbus probes as the source of truth. Provider
    status pages or news can only be advisory context, never proof that a
    Nimbus action succeeded or failed.
    """
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    resolved_container = container or prof.storage_container
    if not resolved_container:
        raise _exit(
            "no storage container configured — pass --container or run "
            "`nimbus auth local --container ...`",
            code=2,
        )
    storage = _build_storage_for_profile(prof, NimbusSecrets(session_dir))
    if storage is None:
        raise _exit(
            "no storage client configured for this profile — "
            "run `nimbus auth local --aws` or set AWS credentials",
            code=2,
        )

    now = _dt.datetime.now(_dt.UTC)
    with ui.thinking(console, "Probing provider health…"):
        report = run_provider_health_probes(
            storage=storage,
            tenant=tenant,
            provider="s3",
            container=resolved_container,
            prefix=prefix,
            region=prof.aws_region,
            head_key=head_key,
            now=now,
            ttl_seconds=ttl_seconds,
            max_list_keys=max_list_keys,
        )
        artifact = create_provider_health_artifact(
            report=report,
            artifact_store=FileArtifactStore(session_dir),
            actor=_cli_actor(tenant=tenant, now=now),
            session_id=session_id or "provider-health",
        )

    if json_output:
        console.print_json(
            json.dumps(
                {
                    "report": to_jsonable(report),
                    "artifact": _artifact_to_jsonable(artifact),
                },
                sort_keys=True,
            )
        )
    else:
        _render_provider_health(
            console=console,
            report=report,
            artifact_id=artifact.artifact_id,
        )

    if report.status != "healthy":
        raise typer.Exit(1)


def _render_provider_health(
    *,
    console: Console,
    report: ProviderHealthReport,
    artifact_id: str,
) -> None:
    status_icon = ui.ICON_OK if report.status == "healthy" else ui.ICON_WARN
    rows: list[ui.KV] = [
        ui.KV("status", report.status),
        ui.KV("score", str(report.health_score)),
        ui.KV("confidence", report.confidence),
        ui.KV("source", report.evidence_source),
        ui.KV("provider", report.provider),
        ui.KV("container", report.container),
        ui.KV("prefix", report.prefix or "—"),
        ui.KV("artifact", artifact_id),
        ui.KV("generated", report.generated_at.isoformat()),
        ui.KV("expires", report.expires_at.isoformat()),
    ]
    if report.region:
        rows.insert(6, ui.KV("region", report.region))
    console.print(ui.card(ui.kv_table(rows), title=f"{status_icon} Provider health"))

    probe_table = Table(show_header=True, header_style="bold", box=None)
    probe_table.add_column("probe", style="cyan", no_wrap=True)
    probe_table.add_column("outcome")
    probe_table.add_column("ms", justify="right")
    probe_table.add_column("items", justify="right")
    probe_table.add_column("object")
    probe_table.add_column("detail")
    for probe in report.probes:
        probe_table.add_row(
            probe.operation,
            probe.outcome.value,
            str(probe.latency_ms),
            "—" if probe.item_count is None else str(probe.item_count),
            probe.object_name or "—",
            probe.error_message or "—",
        )
    console.print(ui.card(probe_table, title="Live probes"))
    if report.advisory_context:
        advisory_table = Table.grid(padding=(0, 1))
        for item in report.advisory_context:
            advisory_table.add_row(Text(item, style=ui.MUTED))
        console.print(ui.card(advisory_table, title="Advisory context"))
    ui.hint(console, report.next_operator_step)


# ── Search commands ──────────────────────────────────────────────────────────

search_app = typer.Typer(help="Search indexed documents in the local workspace.")
app.add_typer(search_app, name="search")


@search_app.command("run")
def search_run(
    query: Annotated[str, typer.Argument(help="Free-text search query.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[
        int,
        typer.Option("--limit", "-n", help="Maximum number of results (1-100)."),
    ] = 10,
    channel: Annotated[
        str | None,
        typer.Option("--channel", help="Filter results to a specific channel ID."),
    ] = None,
) -> None:
    """Search documents indexed in the local Nimbus search store.

    Queries the SQLite-backed FileSearchIndexStore located in the active
    profile's session directory.  Results are ACL-evaluated as workspace-
    wide for the CLI actor (equivalent to an admin search).
    """
    from datetime import UTC
    from datetime import datetime as _dt

    from nimbus_runtime.domain import VerifiedActor
    from nimbus_runtime.search import (
        FileSearchIndexStore,
        SearchActorScope,
        SearchFilters,
        SearchQuery,
    )

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)

    actor = VerifiedActor(
        tenant=tenant,
        user_id="cli",
        auth_source="cli_local",
        bridge_id=None,
        verified_at=_dt.now(UTC),
    )
    scope = SearchActorScope(actor=actor, workspace_wide=True)
    search_store = FileSearchIndexStore(session_dir)
    try:
        results = search_store.search(
            scope=scope,
            query=SearchQuery(
                text=query,
                filters=SearchFilters(channel_id=channel),
                limit=max(1, min(limit, 100)),
            ),
        )
    except Exception as exc:
        raise _exit(f"search failed: {exc}", code=1) from exc

    if not results:
        console.print(
            ui.empty_state(
                f"No results for {query!r}.",
                hint="Index documents via `nimbus chat` or the Slack adapter first.",
            )
        )
        return

    result_table = Table(
        show_header=True,
        header_style="bold",
        box=None,
        padding=(0, 1),
    )
    result_table.add_column("score", no_wrap=True, style="cyan")
    result_table.add_column("title")
    result_table.add_column("source", style="dim")
    result_table.add_column("snippet")
    for result in results:
        doc = result.document
        snippet = result.chunk_hits[0].snippet if result.chunk_hits else ""
        if len(snippet) > 80:  # noqa: PLR2004
            snippet = snippet[:77] + "…"
        result_table.add_row(
            f"{result.score:.1f}",
            doc.title,
            doc.source_uri,
            snippet,
        )
    console.print(
        ui.card(
            result_table,
            title=f"Search  •  {query!r}  •  {prof.name}",
        )
    )


def _build_storage_for_profile(
    prof: NimbusProfile,
    secrets: NimbusSecrets,
) -> CloudStorageClient | None:
    """Return a storage client for *prof*, or ``None`` when unconfigured."""
    from nimbus_cli.runtime import _storage_client

    return _storage_client(prof, secrets)


class _CliReplicaRepairClient:
    """Adapter from CLI storage clients to the healing repair protocol."""

    def __init__(self, storage: CloudStorageClient) -> None:
        self._storage = storage

    def copy_object(
        self,
        *,
        source_container: str,
        source_object_name: str,
        destination_container: str,
        destination_object_name: str,
    ) -> None:
        copier = getattr(self._storage, "copy_object", None)
        if not callable(copier):
            msg = "configured storage provider does not support server-side copy"
            raise TypeError(msg)
        copier(
            source_container,
            source_object_name,
            destination_container,
            destination_object_name,
        )

    def object_sha256(self, *, container: str, object_name: str) -> str | None:
        info = self._storage.get_file_info(container, object_name)
        return _object_info_sha256(info)


def _object_info_sha256(info: object) -> str | None:
    metadata = getattr(info, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("sha256", "sha256_hex", "nimbus-sha256", "x-amz-meta-sha256"):
            value = metadata.get(key)
            if isinstance(value, str) and value:
                return value.removeprefix("sha256:").lower()
    integrity = getattr(info, "integrity", None)
    if isinstance(integrity, str) and integrity.startswith("sha256:"):
        return integrity.removeprefix("sha256:").lower()
    return None


def _cli_actor(*, tenant: TenantIdentity, now: datetime) -> VerifiedActor:
    """Return the durable actor identity for local CLI operations."""
    return VerifiedActor(
        tenant=tenant,
        user_id="cli",
        auth_source="cli_local",
        bridge_id=None,
        verified_at=now,
    )


def _generation_manifest_from_artifact(
    *,
    artifact_store: FileArtifactStore,
    tenant: TenantIdentity,
    artifact_id: str,
) -> GenerationManifest:
    artifact = artifact_store.get(tenant=tenant, artifact_id=artifact_id)
    if artifact is None:
        raise _exit(f"manifest artifact {artifact_id!r} not found", code=1)
    if artifact.kind != "manifest" or not isinstance(
        artifact.payload,
        GenerationManifest,
    ):
        raise _exit(
            f"artifact {artifact_id!r} is not a generation manifest",
            code=2,
        )
    return artifact.payload


def _pointer_short(pointer: ObjectPointer | None) -> str:
    if pointer is None:
        return "—"
    sha = pointer.content_sha256 or "no-hash"
    size = pointer.size_bytes or 0
    return f"{sha[:16]} ({_format_bytes(size)})"


def _migration_packet_id(
    *,
    tenant: TenantIdentity,
    root_id: str,
    candidate_container: str,
    candidate_prefix: str,
    candidate_region: str | None,
) -> str:
    digest = digest_value(
        {
            "tenant_id": tenant.tenant_id,
            "root_id": root_id,
            "candidate_container": candidate_container,
            "candidate_prefix": candidate_prefix,
            "candidate_region": candidate_region,
        }
    )
    return f"mig-{digest.removeprefix('sha256:')[:32]}"


def _estimate_s3_storage_cost(total_bytes: int) -> float:
    gb_month = total_bytes / (1024**3)
    return round(gb_month * 0.023, 6)


def _health_score(
    *,
    mismatch_count: int,
    missing_count: int,
    unknown_count: int,
    strict: bool,
    total_count: int,
) -> int:
    penalty = (mismatch_count * 60) + (missing_count * 60)
    penalty += unknown_count * (20 if strict else 5)
    normalized = int(100 - (penalty / total_count))
    return max(0, min(100, normalized))


def _healing_advice(*, report_has_drift: bool, strict: bool) -> str:
    if not report_has_drift:
        return "No repair needed; latest generation verifies against live S3."
    if strict:
        return (
            "Drift or unknown hashes detected. Re-run generation creation after "
            "repairing S3 metadata, then verify again."
        )
    return (
        "Drift detected. Configure a replica lane before Nimbus can safely "
        "perform policy-allowed repair."
    )


# ── Storage stack commands ──────────────────────────────────────────────────


@stack_app.command("list")
def stack_list(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable stacks."),
    ] = False,
) -> None:
    """List recent storage change stacks for the active tenant."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    stacks = FileStorageStackStore(_profile_session_dir(prof)).list_for_tenant(
        tenant=tenant,
        limit=limit,
    )
    if json_output:
        console.print_json(json.dumps([to_jsonable(stack) for stack in stacks]))
        return
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("stack", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("status")
    table.add_column("plan")
    table.add_column("title")
    table.add_column("updated")
    for stack in stacks:
        table.add_row(
            stack.stack_id,
            ui.status_badge(stack.status),
            stack.plan_id or "—",
            stack.title,
            stack.updated_at.isoformat(),
        )
    console.print(ui.card(table, title=f"Storage stacks  •  {prof.name}"))


@stack_app.command("propose")
def stack_propose(
    plan_id: Annotated[str, typer.Argument(help="Plan ID to convert to a stack.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print the proposed stack as JSON."),
    ] = False,
) -> None:
    """Create a deterministic storage change stack from a Nimbus plan."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    plan = FilePlanStore(session_dir).get(tenant=tenant, plan_id=plan_id)
    if plan is None:
        raise _exit(f"plan {plan_id!r} not found", code=1)
    state = FileStorageStackStore(session_dir).create_from_plan(
        plan=plan,
        actor=_cli_actor(tenant=tenant, now=_dt.datetime.now(_dt.UTC)),
    )
    _render_stack_state(
        console=console,
        state=state,
        json_output=json_output,
        title=f"Storage stack  •  {state.stack.stack_id}",
    )


@stack_app.command("show")
def stack_show(
    stack_id: Annotated[str, typer.Argument(help="Stack ID to inspect.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print a stable machine-readable stack."),
    ] = False,
) -> None:
    """Show one stack with changes, revisions, and operation log."""
    _load_dotenv_best_effort()
    console = Console()
    state = _load_stack_state(
        profile=profile,
        workspace=workspace,
        stack_id=stack_id,
    )
    _render_stack_state(
        console=console,
        state=state,
        json_output=json_output,
        title=f"Storage stack  •  {stack_id}",
    )


@stack_app.command("diff")
def stack_diff(
    stack_id: Annotated[str, typer.Argument(help="Stack ID to diff.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable diff."),
    ] = False,
) -> None:
    """Show the ordered storage changes a stack would apply."""
    _load_dotenv_best_effort()
    console = Console()
    state = _load_stack_state(
        profile=profile,
        workspace=workspace,
        stack_id=stack_id,
    )
    document = _stack_diff_document(state)
    if json_output:
        console.print_json(json.dumps(document, sort_keys=True))
        return
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("#", justify="right")
    table.add_column("change")
    table.add_column("status")
    table.add_column("operation")
    table.add_column("target")
    table.add_column("digest")
    for item in cast("list[dict[str, object]]", document["changes"]):
        if not isinstance(item, dict):
            continue
        table.add_row(
            str(item["position"]),
            str(item["change_id"]),
            str(item["status"]),
            str(item["operation"]),
            str(item["object_name"]),
            str(item["target_digest"])[:16],
        )
    console.print(ui.card(table, title=f"Stack diff  •  {stack_id}"))


@stack_app.command("approve")
def stack_approve(
    stack_id: Annotated[str, typer.Argument(help="Stack ID to approve.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print the approved stack as JSON."),
    ] = False,
) -> None:
    """Approve a proposed stack without executing storage mutations."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    now = _dt.datetime.now(_dt.UTC)
    updated = FileStorageStackStore(_profile_session_dir(prof)).approve(
        tenant=tenant,
        stack_id=stack_id,
        actor=_cli_actor(tenant=tenant, now=now),
        now=now,
    )
    if updated is None:
        raise _exit(
            f"stack {stack_id!r} could not be approved; expected proposed status",
            code=1,
        )
    _render_stack_state(
        console=console,
        state=updated,
        json_output=json_output,
        title=f"Approved stack  •  {stack_id}",
    )


@stack_app.command("restack")
def stack_restack(
    stack_id: Annotated[str, typer.Argument(help="Stack ID to restack.")],
    manifest_artifact_id: Annotated[
        str,
        typer.Option(
            "--manifest",
            help="Fresh generation manifest artifact ID to compare against.",
        ),
    ],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print the restacked state as JSON."),
    ] = False,
) -> None:
    """Rebase a stack onto a fresh manifest or create conflict artifacts."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    artifact_store = FileArtifactStore(session_dir)
    manifest = _generation_manifest_from_artifact(
        artifact_store=artifact_store,
        tenant=tenant,
        artifact_id=manifest_artifact_id,
    )
    now = _dt.datetime.now(_dt.UTC)
    updated = FileStorageStackStore(session_dir).restack(
        tenant=tenant,
        stack_id=stack_id,
        manifest=manifest,
        artifact_store=artifact_store,
        actor=_cli_actor(tenant=tenant, now=now),
        now=now,
    )
    if updated is None:
        raise _exit(f"stack {stack_id!r} not found", code=1)
    _render_stack_state(
        console=console,
        state=updated,
        json_output=json_output,
        title=f"Restacked  •  {stack_id}",
    )


@stack_app.command("apply")
def stack_apply(
    stack_id: Annotated[str, typer.Argument(help="Stack ID to apply.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    yes: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--yes", "-y", help="Confirm destructive stack execution."),
    ] = False,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable result."),
    ] = False,
) -> None:
    """Apply an approved stack with verifier gates and failure-stop semantics."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    stack_store = FileStorageStackStore(session_dir)
    state = stack_store.get_state(tenant=tenant, stack_id=stack_id)
    if state is None:
        raise _exit(f"stack {stack_id!r} not found", code=1)
    diff = _stack_diff_document(state)
    destructive = any(
        isinstance(item, dict)
        and item.get("operation") in {"delete_duplicate", "archive_then_delete"}
        for item in cast("list[dict[str, object]]", diff["changes"])
    )
    if destructive and not yes:
        confirm = Prompt.ask(
            f"[{_ACCENT_STYLE}]Apply destructive stack {stack_id}?[/] (yes/no)",
            default="no",
        )
        if confirm.strip().lower() not in {"yes", "y"}:
            console.print("Aborted.")
            return
    storage = _build_storage_for_profile(prof, NimbusSecrets(session_dir))
    now = _dt.datetime.now(_dt.UTC)
    result = stack_store.apply(
        tenant=tenant,
        stack_id=stack_id,
        actor=_cli_actor(tenant=tenant, now=now),
        storage=storage,
        artifact_store=FileArtifactStore(session_dir),
        now=now,
    )
    if result is None:
        raise _exit(f"stack {stack_id!r} not found", code=1)
    document = to_jsonable(result)
    if json_output:
        console.print_json(json.dumps(document, sort_keys=True))
        if result.status != "applied":
            raise typer.Exit(1)
        return
    rows = [
        ui.KV("stack", result.stack.stack_id),
        ui.KV("status", ui.status_badge(result.status)),
        ui.KV("applied", str(result.applied_count)),
        ui.KV("blocked", str(result.blocked_count)),
        ui.KV("failed", str(result.failed_count)),
        ui.KV("next_step", result.next_step),
    ]
    console.print(ui.card(ui.kv_table(rows), title=f"Stack apply  •  {stack_id}"))
    if result.status != "applied":
        raise typer.Exit(1)


@stack_app.command("abandon")
def stack_abandon(
    stack_id: Annotated[str, typer.Argument(help="Stack ID to abandon.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print the abandoned stack as JSON."),
    ] = False,
) -> None:
    """Abandon a stack that should no longer be executed."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    now = _dt.datetime.now(_dt.UTC)
    updated = FileStorageStackStore(_profile_session_dir(prof)).abandon(
        tenant=tenant,
        stack_id=stack_id,
        actor=_cli_actor(tenant=tenant, now=now),
        now=now,
    )
    if updated is None:
        raise _exit(
            f"stack {stack_id!r} could not be abandoned; already applied or missing",
            code=1,
        )
    _render_stack_state(
        console=console,
        state=updated,
        json_output=json_output,
        title=f"Abandoned stack  •  {stack_id}",
    )


def _load_stack_state(
    *,
    profile: str | None,
    workspace: str | None,
    stack_id: str,
) -> StorageStackState:
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    state = FileStorageStackStore(_profile_session_dir(prof)).get_state(
        tenant=tenant,
        stack_id=stack_id,
    )
    if state is None:
        raise _exit(f"stack {stack_id!r} not found", code=1)
    return state


def _render_stack_state(
    *,
    console: Console,
    state: StorageStackState,
    json_output: bool,
    title: str,
) -> None:
    if json_output:
        console.print_json(json.dumps(_stack_state_document(state), sort_keys=True))
        return
    rows = [
        ui.KV("stack", state.stack.stack_id),
        ui.KV("status", ui.status_badge(state.stack.status)),
        ui.KV("plan", state.stack.plan_id or "—"),
        ui.KV("changes", str(len(state.changes))),
        ui.KV("operations", str(len(state.operations))),
        ui.KV("updated", state.stack.updated_at.isoformat()),
    ]
    console.print(ui.card(ui.kv_table(rows), title=title))
    diff = _stack_diff_document(state)
    if diff["changes"]:
        table = Table(show_header=True, header_style="bold", box=None)
        table.add_column("#", justify="right")
        table.add_column("status")
        table.add_column("operation")
        table.add_column("target")
        for item in cast("list[dict[str, object]]", diff["changes"]):
            if isinstance(item, dict):
                table.add_row(
                    str(item["position"]),
                    str(item["status"]),
                    str(item["operation"]),
                    str(item["object_name"]),
                )
        console.print(ui.card(table, title="Changes"))


def _stack_state_document(state: StorageStackState) -> dict[str, object]:
    return {
        "stack": to_jsonable(state.stack),
        "entries": to_jsonable(state.entries),
        "changes": to_jsonable(state.changes),
        "revisions": to_jsonable(state.revisions),
        "operations": to_jsonable(state.operations),
        "diff": _stack_diff_document(state),
    }


def _stack_diff_document(state: StorageStackState) -> dict[str, object]:
    revisions = {revision.revision_id: revision for revision in state.revisions}
    entries = sorted(state.entries, key=lambda entry: entry.position)
    changes_by_id = {change.change_id: change for change in state.changes}
    items: list[dict[str, object]] = []
    for entry in entries:
        change = changes_by_id[entry.change_id]
        revision = revisions[change.current_revision_id]
        target = dict(revision.target)
        items.append(
            {
                "position": entry.position,
                "change_id": change.change_id,
                "status": change.status,
                "operation": revision.operation,
                "object_name": target.get("object_name"),
                "container": target.get("container"),
                "target_digest": revision.target_digest,
                "risk_level": revision.risk_level.value,
                "reason": revision.reason,
                "target": target,
            }
        )
    return {
        "stack_id": state.stack.stack_id,
        "status": state.stack.status,
        "plan_id": state.stack.plan_id,
        "changes": items,
        "approval_binding": state.stack.metadata.get("approval_binding", {}),
    }


# ── Learning policy patch commands ─────────────────────────────────────────


@policy_patch_app.command("list")
def policy_patch_list(
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    limit: Annotated[int, typer.Option("--limit", "-n")] = 50,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable proposals."),
    ] = False,
) -> None:
    """List learning-derived policy patch proposals."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    proposals = FilePolicyPatchStore(_profile_session_dir(prof)).list_for_tenant(
        tenant=tenant,
        limit=limit,
    )
    if json_output:
        console.print_json(json.dumps([to_jsonable(item) for item in proposals]))
        return
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("proposal", style=f"bold {ui.PRIMARY}", no_wrap=True)
    table.add_column("status")
    table.add_column("base")
    table.add_column("proposed")
    table.add_column("reviewer")
    for proposal in proposals:
        table.add_row(
            proposal.proposal_id,
            ui.status_badge(proposal.status.value),
            proposal.patch.base_policy.policy_version,
            proposal.patch.proposed_policy_version,
            proposal.patch.reviewer.user_id,
        )
    console.print(ui.card(table, title=f"Policy patches  •  {prof.name}"))


@policy_patch_app.command("propose")
def policy_patch_propose(
    capability: Annotated[
        str,
        typer.Option("--capability", help="Capability the patch changes."),
    ] = "delete_file",
    kind: Annotated[
        CapabilityDeltaKind,
        typer.Option("--kind", help="Capability delta kind."),
    ] = CapabilityDeltaKind.TIGHTEN,
    before: Annotated[str, typer.Option("--before")] = "scope=workspace",
    after: Annotated[str, typer.Option("--after")] = "scope=current_channel",
    base_version: Annotated[str, typer.Option("--base-version")] = (
        "runtime-default-v1"
    ),
    proposed_version: Annotated[str, typer.Option("--proposed-version")] = (
        "runtime-default-v2"
    ),
    signal_subject: Annotated[str, typer.Option("--subject")] = "delete_file",
    evidence: Annotated[
        list[str] | None,
        typer.Option("--evidence", help="Evidence artifact/action/approval ref."),
    ] = None,
    rationale: Annotated[str, typer.Option("--rationale")] = (
        "Learning signal recommends a runtime policy adjustment."
    ),
    authority_expansion_reason: Annotated[
        str | None,
        typer.Option("--authority-expansion-reason"),
    ] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print the proposal as JSON."),
    ] = False,
) -> None:
    """Create a deterministic policy patch proposal from a learning signal."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    now = _dt.datetime.now(_dt.UTC)
    actor = _cli_actor(tenant=tenant, now=now)
    binding = PolicyVersionBinding(policy_version=base_version)
    signal = record_learning_signal(
        tenant=tenant,
        source=LearningSignalSource.HUMAN_FEEDBACK,
        subject=signal_subject,
        outcome=LearningSignalOutcome.NEEDS_REVIEW,
        summary=rationale,
        policy_binding=binding,
        reported_by=actor,
        evidence_refs=tuple(evidence or ("operator:cli",)),
        idempotency_key=f"policy-patch:{capability}:{base_version}",
        created_at=now,
    )
    patch = PolicyPatch(
        tenant=tenant,
        base_policy=binding,
        proposed_policy_version=proposed_version,
        learning_signal_ids=(signal.signal_id,),
        capability_deltas=(
            CapabilityDelta(
                capability=capability,
                kind=kind,
                before=before,
                after=after,
                reason=rationale,
            ),
        ),
        reviewer=actor,
        rationale=rationale,
        authority_expansion_reason=authority_expansion_reason,
        metadata={"signal_summary": signal.summary},
    )
    proposal = propose_policy_patch(
        patch=patch,
        signals=(signal,),
        proposed_by=actor,
        now=now,
    )
    stored = FilePolicyPatchStore(_profile_session_dir(prof)).create_or_get(proposal)
    _render_policy_patch(console=console, proposal=stored, json_output=json_output)


@policy_patch_app.command("show")
def policy_patch_show(
    proposal_id: Annotated[str, typer.Argument(help="Policy patch proposal ID.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable proposal."),
    ] = False,
) -> None:
    """Show one policy patch proposal."""
    _load_dotenv_best_effort()
    console = Console()
    proposal = _load_policy_patch(
        proposal_id=proposal_id,
        profile=profile,
        workspace=workspace,
    )
    _render_policy_patch(console=console, proposal=proposal, json_output=json_output)


@policy_patch_app.command("accept")
def policy_patch_accept(
    proposal_id: Annotated[str, typer.Argument(help="Policy patch proposal ID.")],
    current_version: Annotated[str, typer.Option("--current-version")] = (
        "runtime-default-v1"
    ),
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable proposal."),
    ] = False,
) -> None:
    """Accept a proposal if it is still bound to the current policy version."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    now = _dt.datetime.now(_dt.UTC)
    updated = FilePolicyPatchStore(_profile_session_dir(prof)).accept(
        tenant=tenant,
        proposal_id=proposal_id,
        reviewer=_cli_actor(tenant=tenant, now=now),
        current_policy=PolicyVersionBinding(policy_version=current_version),
        now=now,
        decision_note="accepted from CLI",
    )
    if updated is None:
        raise _exit(f"policy patch {proposal_id!r} not found", code=1)
    _render_policy_patch(console=console, proposal=updated, json_output=json_output)


@policy_patch_app.command("reject")
def policy_patch_reject(
    proposal_id: Annotated[str, typer.Argument(help="Policy patch proposal ID.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable proposal."),
    ] = False,
) -> None:
    """Reject a policy patch proposal."""
    import datetime as _dt

    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    now = _dt.datetime.now(_dt.UTC)
    updated = FilePolicyPatchStore(_profile_session_dir(prof)).reject(
        tenant=tenant,
        proposal_id=proposal_id,
        reviewer=_cli_actor(tenant=tenant, now=now),
        now=now,
        decision_note="rejected from CLI",
    )
    if updated is None:
        raise _exit(f"policy patch {proposal_id!r} not found", code=1)
    _render_policy_patch(console=console, proposal=updated, json_output=json_output)


def _load_policy_patch(
    *,
    proposal_id: str,
    profile: str | None,
    workspace: str | None,
) -> PolicyPatchProposal:
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    proposal = FilePolicyPatchStore(_profile_session_dir(prof)).get(
        tenant=tenant,
        proposal_id=proposal_id,
    )
    if proposal is None:
        raise _exit(f"policy patch {proposal_id!r} not found", code=1)
    return proposal


def _render_policy_patch(
    *,
    console: Console,
    proposal: PolicyPatchProposal,
    json_output: bool,
) -> None:
    if json_output:
        console.print_json(json.dumps(to_jsonable(proposal), sort_keys=True))
        return
    patch = proposal.patch
    rows = [
        ui.KV("proposal", proposal.proposal_id),
        ui.KV("status", ui.status_badge(proposal.status.value)),
        ui.KV("base_policy", patch.base_policy.policy_version),
        ui.KV("proposed_policy", patch.proposed_policy_version),
        ui.KV("reviewer", patch.reviewer.user_id),
        ui.KV("signals", ", ".join(patch.learning_signal_ids)),
        ui.KV("rationale", patch.rationale),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Policy patch"))


# ── Runtime spec commands ──────────────────────────────────────────────────


@spec_app.command("check")
def spec_check(
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable spec output."),
    ] = False,
) -> None:
    """Check the executable runtime status specification used by replay."""
    console = Console()
    spec = runtime_status_spec()
    statuses = cast("dict[str, list[str]]", spec["statuses"])
    counts = {name: len(values) for name, values in statuses.items()}
    formal_specs = _formal_spec_digests()
    payload = {
        "spec": spec,
        "content_digest": digest_value(spec),
        "formal_specs": formal_specs,
        "status_domain_counts": counts,
        "checked": True,
    }
    if json_output:
        console.print_json(json.dumps(payload, sort_keys=True))
        return
    rows = [
        ui.KV("schema_version", str(spec["schema_version"])),
        ui.KV("content_digest", str(payload["content_digest"])),
        ui.KV("formal_specs", str(len(formal_specs))),
        ui.KV("domains", ", ".join(sorted(counts))),
        ui.KV("checked", "true"),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Runtime status spec"))


def _formal_spec_digests() -> list[dict[str, str]]:
    """Return formal spec file digests when the repo checkout is available."""
    repo_root = Path(__file__).resolve().parents[3]
    specs = (
        repo_root / "formal" / "tla" / "NimbusActionLedger.tla",
        repo_root / "formal" / "tla" / "NimbusActionLedger.cfg",
        repo_root / "formal" / "lean" / "Nimbus" / "ActionLedger.lean",
    )
    rows: list[dict[str, str]] = []
    for path in specs:
        if not path.is_file():
            continue
        relative = path.relative_to(repo_root).as_posix()
        rows.append(
            {
                "path": relative,
                "content_digest": digest_value(
                    {
                        "path": relative,
                        "text": path.read_text(encoding="utf-8"),
                    }
                ),
            }
        )
    return rows


# ── Replay trace commands ──────────────────────────────────────────────────


@trace_app.command("export")
def trace_export(
    session_id: Annotated[str, typer.Argument(help="Nimbus session ID to export.")],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable trace."),
    ] = True,
) -> None:
    """Export session events and artifacts as a deterministic replay trace."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    events = FileSessionEventStore(session_dir).list_events(
        tenant=tenant,
        session_id=session_id,
        limit=10_000,
    )
    artifacts = FileArtifactStore(session_dir).list_for_session(
        tenant=tenant,
        session_id=session_id,
    )
    trace = export_trace(events=events, artifacts=artifacts)
    if json_output:
        console.print_json(json.dumps(trace, sort_keys=True))
        return
    rows = [
        ui.KV("trace", str(trace["trace_id"])),
        ui.KV("events", str(len(events))),
        ui.KV("artifacts", str(len(artifacts))),
        ui.KV("content_digest", str(trace["content_digest"])),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Replay trace"))


@trace_app.command("replay")
def trace_replay(
    session_id: Annotated[str, typer.Argument(help="Nimbus session ID to replay.")],
    expected: Annotated[
        Path,
        typer.Option("--expected", exists=True, help="Expected trace JSON file."),
    ],
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    workspace: Annotated[str | None, typer.Option("--workspace")] = None,
    json_output: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--json", help="Print stable machine-readable comparison."),
    ] = False,
) -> None:
    """Replay current evidence and strictly diff it against a trace file."""
    _load_dotenv_best_effort()
    console = Console()
    prof = _resolve_profile(profile or workspace)
    tenant = _cli_tenant(prof)
    session_dir = _profile_session_dir(prof)
    events = FileSessionEventStore(session_dir).list_events(
        tenant=tenant,
        session_id=session_id,
        limit=10_000,
    )
    artifacts = FileArtifactStore(session_dir).list_for_session(
        tenant=tenant,
        session_id=session_id,
    )
    expected_trace = json.loads(expected.read_text(encoding="utf-8"))
    if not isinstance(expected_trace, dict):
        raise _exit("expected trace file must contain a JSON object", code=2)
    comparison = replay_trace(
        expected_trace,
        events=events,
        artifacts=artifacts,
    )
    document = {
        "matches": comparison.matches,
        "diffs": to_jsonable(comparison.diffs),
        "actual_trace": comparison.actual_trace,
    }
    if json_output:
        console.print_json(json.dumps(document, sort_keys=True))
        if not comparison.matches:
            raise typer.Exit(1)
        return
    rows = [
        ui.KV("matches", "yes" if comparison.matches else "no"),
        ui.KV("diffs", str(len(comparison.diffs))),
    ]
    console.print(ui.card(ui.kv_table(rows), title="Replay comparison"))
    if not comparison.matches:
        raise typer.Exit(1)


# ── Formatting helpers ──────────────────────────────────────────────────────


def _format_bytes(n: int) -> str:
    """Return a human-readable byte size (e.g. ``1.4 MB``)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":  # noqa: PLR2004
            return f"{n:.1f} {unit}"
        n //= 1024
    return f"{n} B"  # unreachable but satisfies type-checkers


# ── Task CLI helpers ────────────────────────────────────────────────────────


def _resolve_profile(profile_name: str | None) -> NimbusProfile:
    """Load the active CLI profile."""
    store = ConfigStore()
    config = store.load()
    config = _bootstrap_default_local_profile_from_env(
        config=config,
        profile_name=profile_name,
    )
    try:
        return config.profile(profile_name)
    except (KeyError, ValueError) as exc:
        raise _exit(str(exc), code=2) from exc


def _cli_tenant(profile: NimbusProfile) -> TenantIdentity:
    """Return the tenant identity for a CLI profile."""
    return TenantIdentity(platform="cli", workspace_id=profile.name)


def _profile_session_dir(profile: NimbusProfile) -> Path:
    """Return the session directory for a CLI profile."""
    if profile.session_dir:
        return Path(profile.session_dir).expanduser()
    return default_session_dir()


def _evidence_root(*, root: Path | None, session_dir: Path) -> Path:
    """Return the local content-addressed evidence root for a CLI profile."""
    if root is not None:
        return root.expanduser()
    return session_dir / "evidence"


def _task_status_icon(status: TaskStatus) -> str:
    icons = {
        TaskStatus.CREATED: "🔵",
        TaskStatus.PLANNING: "🔵",
        TaskStatus.SCANNING: "🔍",
        TaskStatus.DIFFING: "🔍",
        TaskStatus.AWAITING_APPROVAL: "⏳",
        TaskStatus.APPLYING: "⚙️",
        TaskStatus.VERIFYING: "🔎",
        TaskStatus.DONE: "✅",
        TaskStatus.FAILED: "❌",
        TaskStatus.CANCELED: "🚫",
        TaskStatus.EXPIRED: "⏱️",
        TaskStatus.REJECTED: "🚫",
    }
    return icons.get(status, "⚪")


_READLINE_HISTORY_LENGTH = 1000


def _setup_readline_history(*, history_path: Path) -> None:
    """Load readline history and register a save-on-exit hook.

    Silently skips if ``readline`` is not available (Windows / PyPy).
    History is saved atomically via ``atexit`` so Ctrl-C still persists it.
    """
    try:
        import atexit
        import readline
    except ImportError:
        return

    import contextlib

    history_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        readline.read_history_file(str(history_path))
    readline.set_history_length(_READLINE_HISTORY_LENGTH)
    atexit.register(readline.write_history_file, str(history_path))


def _load_dotenv_and_announce() -> None:
    """Load a nearby dotenv file and print the path when one is found."""
    path = _load_dotenv_best_effort()
    if path is not None:
        ui.info(Console(), f"Loaded {path.name} from {path}")


def _load_dotenv_best_effort() -> Path | None:
    """Load the nearest dotenv file without overriding env vars.

    Discovery is intentionally local and deterministic:

    1. ``NIMBUS_ENV_FILE=/path/to/file`` wins when set.
    2. Walk up from the current directory looking for a dotenv file.
    3. In the nearest directory with candidates, prefer ``credentials.env``,
       then ``.env``, then any other ``*.env`` file alphabetically.

    The loader never overrides already-exported variables, so shell/CI secrets
    remain the highest-precedence source.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return None
    explicit = os.environ.get(_NIMBUS_ENV_FILE_ENV, "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            load_dotenv(path, override=False)
            return path.resolve()
        return None
    cwd = Path.cwd().resolve()
    for candidate_dir in (cwd, *cwd.parents):
        for path in _dotenv_candidates(candidate_dir):
            load_dotenv(path, override=False)
            return path
    return None


def _dotenv_candidates(directory: Path) -> tuple[Path, ...]:
    """Return deterministic dotenv candidates inside ``directory``."""
    explicit = [directory / name for name in _DOTENV_PRIORITY_NAMES]
    discovered = sorted(directory.glob("*.env"))
    seen: set[Path] = set()
    paths: list[Path] = []
    for path in (*explicit, *discovered):
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        paths.append(path)
    return tuple(paths)


def _exit(message: str, *, code: int) -> NoReturn:
    typer.echo(typer.style(f"fatal: {message}", fg="red"), err=True)
    raise typer.Exit(code=code)


# ── Remote payload adapters (duck-typed for ui.render_result) ─────────────


@dataclass(frozen=True, slots=True)
class _RemoteConfirmation:
    """Attribute-only view of a remote ``confirmation`` JSON object."""

    kind: str
    prompt: str
    expected_reply: str
    expires_at: str | None

    @classmethod
    def from_payload(cls, raw: object) -> _RemoteConfirmation | None:
        if not isinstance(raw, dict):
            return None
        return cls(
            kind=str(raw.get("kind") or "action"),
            prompt=str(raw.get("prompt") or ""),
            expected_reply=str(raw.get("expected_reply") or ""),
            expires_at=str(raw["expires_at"]) if raw.get("expires_at") else None,
        )


@dataclass(frozen=True, slots=True)
class _RemoteAction:
    """Attribute-only view of a remote ``action`` JSON object."""

    kind: str
    status: str
    target: object

    @classmethod
    def from_payload(cls, raw: object) -> _RemoteAction:
        if isinstance(raw, dict):
            return cls(
                kind=str(raw.get("kind") or "?"),
                status=str(raw.get("status") or "?"),
                target=raw.get("target"),
            )
        return cls(kind="?", status="?", target=None)


@dataclass(frozen=True, slots=True)
class _RemoteArtifact:
    """Attribute-only view of a remote ``artifact`` JSON object."""

    kind: str
    artifact_id: str

    @classmethod
    def from_payload(cls, raw: object) -> _RemoteArtifact:
        if isinstance(raw, dict):
            return cls(
                kind=str(raw.get("kind") or "?"),
                artifact_id=str(raw.get("artifact_id") or "?"),
            )
        return cls(kind="?", artifact_id="?")
