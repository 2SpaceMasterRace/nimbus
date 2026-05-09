"""Scheduled Slack saved-manifest verification and drift alerts."""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

import structlog
from aws_client_impl.s3_client import S3Client
from cloud_storage_api.exceptions import ObjectNotFoundError, StorageBackendError

from nimbus_slack import design
from nimbus_slack.blocks import blocks_to_fallback_text

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from nimbus_slack.deps import SlackPoster
    from nimbus_slack.store import SavedSlackFileRecord, TenantConfig

log = structlog.get_logger()

NIMBUS_SLACK_VERIFIER_ENABLED = "NIMBUS_SLACK_VERIFIER_ENABLED"
NIMBUS_SLACK_VERIFIER_INTERVAL_SECONDS = "NIMBUS_SLACK_VERIFIER_INTERVAL_SECONDS"
NIMBUS_SLACK_VERIFIER_INITIAL_DELAY_SECONDS = (
    "NIMBUS_SLACK_VERIFIER_INITIAL_DELAY_SECONDS"
)
NIMBUS_SLACK_VERIFIER_MAX_RECORDS = "NIMBUS_SLACK_VERIFIER_MAX_RECORDS"

_DEFAULT_VERIFIER_INTERVAL_SECONDS = 300.0
_DEFAULT_VERIFIER_INITIAL_DELAY_SECONDS = 30.0
_DEFAULT_VERIFIER_MAX_RECORDS = 500
_MAX_ALERT_ROWS = 8


class SavedObjectInspector(Protocol):
    """Storage capability required by the scheduled Slack verifier."""

    def get_file_info(self, container: str, object_name: str) -> object:
        """Return object metadata or raise ObjectNotFoundError."""


class SavedManifestVerifierStore(Protocol):
    """Durable store operations required by the scheduled verifier."""

    def get_tenant_config(self, team_id: str) -> TenantConfig | None:
        """Return tenant configuration for one workspace."""

    def list_saved_files_for_team(
        self,
        *,
        team_id: str,
    ) -> tuple[SavedSlackFileRecord, ...]:
        """Return all saved Slack file manifest rows for one workspace."""

    def claim_drift_alert(  # noqa: PLR0913 - idempotency key contract.
        self,
        *,
        team_id: str,
        channel_id: str,
        issue_key: str,
        status: str,
        s3_bucket: str,
        s3_key: str,
        now: datetime,
    ) -> bool:
        """Return whether a drift alert should be posted for this issue."""


@dataclass(frozen=True, slots=True)
class SavedFileDrift:
    """One saved Slack manifest row that no longer matches live storage."""

    team_id: str
    channel_id: str
    file_id: str
    s3_bucket: str
    s3_key: str
    status: str
    expected_size_bytes: int
    observed_size_bytes: int | None
    expected_sha256: str
    observed_sha256: str | None
    detail: str

    @property
    def issue_key(self) -> str:
        """Return a durable idempotency key for this logical drift issue."""
        seed = "\0".join(
            (
                self.team_id,
                self.channel_id,
                self.file_id,
                self.s3_bucket,
                self.s3_key,
                self.status,
                str(self.expected_size_bytes),
                self.expected_sha256,
            )
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SlackDriftScanReport:
    """Result of one scheduled verification sweep for a Slack workspace."""

    team_id: str
    checked_count: int
    drifted: tuple[SavedFileDrift, ...]
    error_count: int
    duration_ms: int
    truncated: bool
    checked_at: datetime

    @property
    def has_drift(self) -> bool:
        """Return whether the sweep found user-visible drift."""
        return bool(self.drifted)


def verifier_enabled_from_env() -> bool:
    """Return whether the scheduled verifier should run in this process."""
    raw = os.environ.get(NIMBUS_SLACK_VERIFIER_ENABLED, "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def verifier_interval_seconds_from_env() -> float:
    """Return the configured verifier interval."""
    return _positive_float_env(
        NIMBUS_SLACK_VERIFIER_INTERVAL_SECONDS,
        default=_DEFAULT_VERIFIER_INTERVAL_SECONDS,
    )


def verifier_initial_delay_seconds_from_env() -> float:
    """Return the configured startup delay before the first verifier sweep."""
    return _non_negative_float_env(
        NIMBUS_SLACK_VERIFIER_INITIAL_DELAY_SECONDS,
        default=_DEFAULT_VERIFIER_INITIAL_DELAY_SECONDS,
    )


def verifier_max_records_from_env() -> int:
    """Return the maximum saved manifest rows checked per workspace per sweep."""
    return _positive_int_env(
        NIMBUS_SLACK_VERIFIER_MAX_RECORDS,
        default=_DEFAULT_VERIFIER_MAX_RECORDS,
    )


def build_scheduled_verifier_tasks(
    *,
    team_ids: Iterable[str],
    store: SavedManifestVerifierStore,
) -> list[asyncio.Task[None]]:
    """Start the scheduled saved-manifest verifier when enabled."""
    teams = tuple(dict.fromkeys(team_ids))
    if not teams or not verifier_enabled_from_env():
        return []
    task = asyncio.create_task(
        scheduled_saved_manifest_verifier_loop(
            team_ids=teams,
            store=store,
            interval_seconds=verifier_interval_seconds_from_env(),
            initial_delay_seconds=verifier_initial_delay_seconds_from_env(),
            max_records=verifier_max_records_from_env(),
        ),
        name="nimbus-slack-scheduled-verifier",
    )
    return [task]


async def scheduled_saved_manifest_verifier_loop(
    *,
    team_ids: Sequence[str],
    store: SavedManifestVerifierStore,
    interval_seconds: float,
    initial_delay_seconds: float,
    max_records: int,
) -> None:
    """Run saved-manifest verification forever until the task is cancelled."""
    if initial_delay_seconds > 0:
        await asyncio.sleep(initial_delay_seconds)
    while True:
        for team_id in team_ids:
            await asyncio.to_thread(
                run_scheduled_verifier_once,
                team_id=team_id,
                store=store,
                max_records=max_records,
            )
        await asyncio.sleep(interval_seconds)


def run_scheduled_verifier_once(  # noqa: PLR0913 - injection points keep tests deterministic.
    *,
    team_id: str,
    store: SavedManifestVerifierStore,
    max_records: int = _DEFAULT_VERIFIER_MAX_RECORDS,
    now: datetime | None = None,
    storage_factory: Callable[[TenantConfig], SavedObjectInspector] | None = None,
    poster_factory: Callable[[str], SlackPoster] | None = None,
) -> SlackDriftScanReport:
    """Verify one workspace's saved Slack manifests and post new drift alerts."""
    checked_at = now or datetime.now(UTC)
    report = verify_saved_manifest_records(
        team_id=team_id,
        store=store,
        max_records=max_records,
        now=checked_at,
        storage_factory=storage_factory,
    )
    posted = post_new_drift_alerts(
        report=report,
        store=store,
        now=checked_at,
        poster_factory=poster_factory,
    )
    log.info(
        "slack_scheduled_verifier_sweep",
        team_id=team_id,
        checked=report.checked_count,
        drifted=len(report.drifted),
        posted=posted,
        errors=report.error_count,
        duration_ms=report.duration_ms,
        truncated=report.truncated,
    )
    return report


def verify_saved_manifest_records(
    *,
    team_id: str,
    store: SavedManifestVerifierStore,
    max_records: int = _DEFAULT_VERIFIER_MAX_RECORDS,
    now: datetime | None = None,
    storage_factory: Callable[[TenantConfig], SavedObjectInspector] | None = None,
) -> SlackDriftScanReport:
    """Compare saved Slack manifest rows against live S3 object metadata."""
    started = time.perf_counter()
    checked_at = now or datetime.now(UTC)
    config = store.get_tenant_config(team_id)
    if config is None:
        return SlackDriftScanReport(
            team_id=team_id,
            checked_count=0,
            drifted=(),
            error_count=1,
            duration_ms=_elapsed_ms(started),
            truncated=False,
            checked_at=checked_at,
        )
    records = store.list_saved_files_for_team(team_id=team_id)
    bounded_records = records[: max(0, max_records)]
    storage = (storage_factory or _s3_inspector_from_config)(config)
    drifts: list[SavedFileDrift] = []
    error_count = 0
    for record in bounded_records:
        try:
            drift = _verify_one_record(storage=storage, record=record)
        except StorageBackendError as exc:
            error_count += 1
            log.warning(
                "slack_scheduled_verifier_storage_error",
                team_id=team_id,
                bucket=record.s3_bucket,
                key=record.s3_key,
                error=str(exc),
            )
            continue
        if drift is not None:
            drifts.append(drift)
    return SlackDriftScanReport(
        team_id=team_id,
        checked_count=len(bounded_records),
        drifted=tuple(drifts),
        error_count=error_count,
        duration_ms=_elapsed_ms(started),
        truncated=len(records) > len(bounded_records),
        checked_at=checked_at,
    )


def post_new_drift_alerts(
    *,
    report: SlackDriftScanReport,
    store: SavedManifestVerifierStore,
    now: datetime | None = None,
    poster_factory: Callable[[str], SlackPoster] | None = None,
) -> int:
    """Post exactly-once Slack alerts for newly observed drift issues."""
    if not report.drifted:
        return 0
    checked_at = now or report.checked_at
    grouped: dict[str, list[SavedFileDrift]] = {}
    for drift in report.drifted:
        should_post = store.claim_drift_alert(
            team_id=drift.team_id,
            channel_id=drift.channel_id,
            issue_key=drift.issue_key,
            status=drift.status,
            s3_bucket=drift.s3_bucket,
            s3_key=drift.s3_key,
            now=checked_at,
        )
        if should_post:
            grouped.setdefault(drift.channel_id, []).append(drift)
    if not grouped:
        return 0
    resolved_poster_factory = poster_factory or _poster_for_team(report.team_id)
    posted = 0
    for channel_id, drifts in grouped.items():
        blocks = drift_alert_card(report=report, drifts=tuple(drifts))
        fallback = blocks_to_fallback_text(blocks)
        resolved_poster_factory(channel_id).send_blocks(
            channel_id,
            blocks,
            fallback,
        )
        posted += 1
    return posted


def drift_alert_card(
    *,
    report: SlackDriftScanReport,
    drifts: tuple[SavedFileDrift, ...],
) -> list[dict[str, object]]:
    """Render a Slack card for saved-manifest drift."""
    shown = drifts[:_MAX_ALERT_ROWS]
    lines = [
        f"*{drift.status.replace('_', ' ')}* `{drift.s3_key}` - {drift.detail}"
        for drift in shown
    ]
    remaining = len(drifts) - len(shown)
    if remaining > 0:
        lines.append(f"_...and {remaining} more drifted objects in this channel._")
    return [
        design.branded_header("Storage drift detected", status="warning"),
        design.section(
            "Nimbus verified saved Slack file receipts against live S3 and "
            "found objects that no longer match the manifest."
        ),
        design.section("\n".join(lines)),
        design.context(
            f"checked={report.checked_count}  drifted={len(report.drifted)}  "
            f"errors={report.error_count}  at={report.checked_at.isoformat()}",
            "AWS status: https://health.aws.amazon.com/health/status "
            "(advisory; Nimbus trusts the live bucket probe)",
        ),
    ]


def _verify_one_record(
    *,
    storage: SavedObjectInspector,
    record: SavedSlackFileRecord,
) -> SavedFileDrift | None:
    try:
        info = storage.get_file_info(record.s3_bucket, record.s3_key)
    except ObjectNotFoundError:
        return SavedFileDrift(
            team_id=record.team_id,
            channel_id=record.channel_id,
            file_id=record.file_id,
            s3_bucket=record.s3_bucket,
            s3_key=record.s3_key,
            status="missing",
            expected_size_bytes=record.size_bytes,
            observed_size_bytes=None,
            expected_sha256=record.content_sha256,
            observed_sha256=None,
            detail=f"expected {record.size_bytes} bytes, object is missing",
        )
    observed_size = getattr(info, "size_bytes", None)
    if isinstance(observed_size, int) and observed_size != record.size_bytes:
        return SavedFileDrift(
            team_id=record.team_id,
            channel_id=record.channel_id,
            file_id=record.file_id,
            s3_bucket=record.s3_bucket,
            s3_key=record.s3_key,
            status="size_mismatch",
            expected_size_bytes=record.size_bytes,
            observed_size_bytes=observed_size,
            expected_sha256=record.content_sha256,
            observed_sha256=None,
            detail=f"expected {record.size_bytes} bytes, observed {observed_size}",
        )
    observed_sha256 = _metadata_sha256(info)
    if observed_sha256 is not None and observed_sha256 != record.content_sha256:
        return SavedFileDrift(
            team_id=record.team_id,
            channel_id=record.channel_id,
            file_id=record.file_id,
            s3_bucket=record.s3_bucket,
            s3_key=record.s3_key,
            status="hash_mismatch",
            expected_size_bytes=record.size_bytes,
            observed_size_bytes=(
                observed_size if isinstance(observed_size, int) else None
            ),
            expected_sha256=record.content_sha256,
            observed_sha256=observed_sha256,
            detail=(
                f"expected sha256 {record.content_sha256[:12]}, "
                f"observed {observed_sha256[:12]}"
            ),
        )
    return None


def _metadata_sha256(info: object) -> str | None:
    metadata = getattr(info, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    typed = cast("Mapping[object, object]", metadata)
    for key in ("sha256", "content_sha256", "x-amz-meta-sha256"):
        value = typed.get(key)
        if isinstance(value, str) and value:
            return value.lower().removeprefix("sha256:")
    return None


def _s3_inspector_from_config(config: TenantConfig) -> SavedObjectInspector:
    return S3Client(
        region_name=config.aws_region,
        aws_access_key_id=config.aws_access_key_id,
        aws_secret_access_key=config.aws_secret_access_key,
    )


def _poster_for_team(team_id: str) -> Callable[[str], SlackPoster]:
    from nimbus_slack.deps import get_slack_poster  # noqa: PLC0415

    def factory(_channel_id: str) -> SlackPoster:
        return get_slack_poster(team_id=team_id)

    return factory


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _positive_float_env(name: str, *, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value <= 0:
        msg = f"{name} must be positive."
        raise ValueError(msg)
    return value


def _non_negative_float_env(name: str, *, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = float(raw)
    if value < 0:
        msg = f"{name} must be non-negative."
        raise ValueError(msg)
    return value


def _positive_int_env(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if value <= 0:
        msg = f"{name} must be positive."
        raise ValueError(msg)
    return value
