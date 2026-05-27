"""Slack file inventory and S3 save operations for Nimbus Slack."""

from __future__ import annotations

import concurrent.futures
import hashlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from typing import TYPE_CHECKING, Protocol, cast

import httpx
from aws_client_impl.s3_client import S3Client
from cloud_storage_api import (
    AuthenticationError,
    ContainerNotFoundError,
    InvalidContainerError,
    InvalidFileObjectError,
    InvalidObjectNameError,
    LocalFileAccessError,
    ObjectNotFoundError,
    StorageBackendError,
)
from nimbus_runtime.telemetry import runtime_telemetry
from opentelemetry import trace
from slack_sdk.errors import SlackApiError

from nimbus_slack.store import (
    SavedSlackFileRecord,
    SlackFileRecord,
    SlackStoreBackend,
    TenantConfig,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from slack_sdk import WebClient

DEFAULT_FILE_SCAN_PAGE_SIZE = 100
DEFAULT_FILE_SCAN_MAX_PAGES = 3
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30.0
MAX_PREVIEW_NAMES = 5
MAX_PREVIEW_FAILURES = 3
BYTES_PER_DISPLAY_UNIT = 1024
DISPLAY_SIZE_UNITS = ("KB", "MB", "GB")
_SAVE_CHANNEL_MAX_WORKERS = 8

_tracer = trace.get_tracer("nimbus-slack.file_sync")


@dataclass(frozen=True, slots=True)
class SaveProgress:
    """Progress snapshot emitted while ``save_channel`` is running."""

    total: int
    saved: int
    skipped: int
    failed: int
    current_file: SlackFileRef | None = None


class SlackFileSyncError(RuntimeError):
    """Raised when Slack file synchronization cannot complete."""


@dataclass(frozen=True, slots=True)
class SlackFileRef:
    """Downloadable Slack file metadata."""

    file_id: str
    name: str
    title: str | None
    mimetype: str | None
    size_bytes: int
    url_private_download: str | None
    user_id: str | None
    created_ts: int | None


@dataclass(frozen=True, slots=True)
class FileInventory:
    """A bounded Slack file listing result."""

    files: tuple[SlackFileRef, ...]
    total_count: int | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class FileFailure:
    """A failed file action visible to the user and operator."""

    file: SlackFileRef
    reason: str


@dataclass(frozen=True, slots=True)
class FileSyncReport:
    """Result of a Slack file diff or save operation."""

    channel_id: str
    s3_bucket: str
    s3_prefix: str
    scanned_count: int
    total_count: int | None
    truncated: bool
    missing_files: tuple[SlackFileRef, ...] = ()
    saved_keys: tuple[str, ...] = ()
    skipped_files: tuple[SlackFileRef, ...] = ()
    failures: tuple[FileFailure, ...] = ()


@dataclass(frozen=True, slots=True)
class ChannelFileListing:
    """Bounded Slack file inventory for a channel, with no S3 comparison."""

    channel_id: str
    files: tuple[SlackFileRef, ...]
    total_count: int | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class ChangedSinceSyncReport:
    """Files in a Slack channel whose state diverges from the saved manifest."""

    channel_id: str
    s3_bucket: str
    new_files: tuple[SlackFileRef, ...]
    resized_files: tuple[SlackFileRef, ...]
    last_sync_at: datetime | None
    truncated: bool


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """One content-hash collision among saved manifest entries."""

    content_sha256: str
    keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StaleSavedFile:
    """A manifest entry whose Slack file is no longer visible to the bot."""

    file_id: str
    s3_key: str


@dataclass(frozen=True, slots=True)
class DedupeReport:
    """Result of a manifest-side duplicate and stale-file scan."""

    channel_id: str
    s3_bucket: str
    saved_count: int
    duplicate_groups: tuple[DuplicateGroup, ...]
    stale_files: tuple[StaleSavedFile, ...]
    truncated: bool
    scope_label: str = "this Slack channel's saved manifest"
    stale_checked: bool = True


class SlackFileSource(Protocol):
    """Slack file listing and byte download capability."""

    def conversation_label(self, channel_id: str) -> str | None:
        """Return a human-readable conversation label, if Slack exposes one."""

    def list_channel_files(
        self,
        channel_id: str,
        *,
        page_size: int,
        max_pages: int,
    ) -> FileInventory:
        """Return a bounded list of files shared in a Slack channel."""

    def download_file(self, file: SlackFileRef, *, max_bytes: int) -> bytes:
        """Download one Slack file with a byte bound."""


class TenantObjectSink(Protocol):
    """Tenant-scoped object upload capability."""

    def upload_bytes(
        self,
        *,
        config: TenantConfig,
        key: str,
        content: bytes,
    ) -> None:
        """Upload bytes to the tenant's object store."""


@dataclass(frozen=True, slots=True)
class SlackWebFileSource:
    """Slack SDK-backed file source."""

    client: WebClient
    bot_token: str

    def conversation_label(self, channel_id: str) -> str | None:
        """Return a display label for a Slack channel, DM, or group chat."""
        try:
            response = self.client.conversations_info(channel=channel_id)
        except SlackApiError as exc:
            _record_metadata_lookup_failure(
                channel_id=channel_id,
                reason=_slack_error(exc),
            )
            return None
        payload = _response_payload(response)
        if payload is None or payload.get("ok") is False:
            _record_metadata_lookup_failure(
                channel_id=channel_id,
                reason=_payload_error(payload),
            )
            return None
        conversation = payload.get("channel")
        if not isinstance(conversation, dict):
            _record_metadata_lookup_failure(
                channel_id=channel_id,
                reason="missing_channel_payload",
            )
            return None
        label = _optional_conversation_name(conversation)
        if label is not None:
            return label
        user_id = _optional_str(conversation, "user")
        if user_id is None:
            return None
        user_label = self._user_label(user_id)
        return f"chat-{user_label}" if user_label is not None else f"chat-{user_id}"

    def list_channel_files(
        self,
        channel_id: str,
        *,
        page_size: int,
        max_pages: int,
    ) -> FileInventory:
        """List files in a channel using Slack ``files.list``."""
        if page_size <= 0 or max_pages <= 0:
            msg = "Slack file scan bounds must be positive."
            raise SlackFileSyncError(msg)
        files: list[SlackFileRef] = []
        total_count: int | None = None
        truncated = False
        for page in range(1, max_pages + 1):
            payload = self._files_list_page(
                channel_id=channel_id,
                count=page_size,
                page=page,
            )
            files.extend(_file_from_payload(file) for file in _payload_files(payload))
            paging = payload.get("paging")
            if not isinstance(paging, dict):
                break
            total_count = _optional_int(paging, "total")
            pages = _optional_int(paging, "pages") or page
            truncated = page < pages
            if not truncated:
                break
        return FileInventory(
            files=tuple(files),
            total_count=total_count,
            truncated=truncated,
        )

    def download_file(self, file: SlackFileRef, *, max_bytes: int) -> bytes:
        """Download a Slack file through its private URL."""
        if max_bytes <= 0:
            msg = "Slack file download byte limit must be positive."
            raise SlackFileSyncError(msg)
        if file.url_private_download is None:
            msg = f"Slack file {file.file_id} does not expose a private URL."
            raise SlackFileSyncError(msg)
        try:
            with httpx.stream(
                "GET",
                file.url_private_download,
                headers={"Authorization": f"Bearer {self.bot_token}"},
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
            ) as response:
                response.raise_for_status()
                chunks: list[bytes] = []
                total_bytes = 0
                for chunk in response.iter_bytes():
                    total_bytes += len(chunk)
                    if total_bytes > max_bytes:
                        msg = (
                            f"Slack file {file.file_id} exceeds the "
                            f"{max_bytes} byte limit."
                        )
                        raise SlackFileSyncError(msg)
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            msg = f"Failed to download Slack file {file.file_id}."
            raise SlackFileSyncError(msg) from exc
        return b"".join(chunks)

    def _files_list_page(
        self,
        *,
        channel_id: str,
        count: int,
        page: int,
    ) -> Mapping[str, object]:
        """Fetch one ``files.list`` page."""
        try:
            response = self.client.files_list(
                channel=channel_id,
                count=count,
                page=page,
            )
        except SlackApiError as exc:
            msg = f"Slack files.list failed for channel {channel_id}."
            raise SlackFileSyncError(msg) from exc
        payload = _response_payload(response)
        if payload is None:
            msg = "Slack files.list response must be a mapping."
            raise SlackFileSyncError(msg)
        if payload.get("ok") is False:
            msg = f"Slack files.list failed: {_payload_error(payload)}"
            raise SlackFileSyncError(msg)
        return payload

    def _user_label(self, user_id: str) -> str | None:
        """Return a Slack user's display label for direct-message paths."""
        try:
            response = self.client.users_info(user=user_id)
        except SlackApiError as exc:
            _record_metadata_lookup_failure(
                channel_id=user_id,
                reason=_slack_error(exc),
            )
            return None
        payload = _response_payload(response)
        if payload is None or payload.get("ok") is False:
            _record_metadata_lookup_failure(
                channel_id=user_id,
                reason=_payload_error(payload),
            )
            return None
        user = payload.get("user")
        if not isinstance(user, dict):
            return None
        profile = user.get("profile")
        if isinstance(profile, dict):
            display_name = _optional_str(profile, "display_name")
            real_name = _optional_str(profile, "real_name")
            if display_name:
                return display_name
            if real_name:
                return real_name
        return _optional_str(user, "real_name") or _optional_str(user, "name")


@dataclass(frozen=True, slots=True)
class S3TenantObjectSink:
    """AWS S3 sink using tenant BYOK credentials."""

    def upload_bytes(
        self,
        *,
        config: TenantConfig,
        key: str,
        content: bytes,
    ) -> None:
        """Upload bytes to S3 with tenant-scoped credentials."""
        client = S3Client(
            region_name=config.aws_region,
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        )
        client.upload_obj(config.s3_bucket, BytesIO(content), key)


@dataclass(slots=True)
class SlackFileSyncService:
    """Coordinate Slack file inventory, S3 uploads, and manifest updates."""

    store: SlackStoreBackend
    source: SlackFileSource
    sink: TenantObjectSink
    page_size: int = DEFAULT_FILE_SCAN_PAGE_SIZE
    max_pages: int = DEFAULT_FILE_SCAN_MAX_PAGES
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    _now: datetime | None = field(default=None, repr=False)

    def diff_channel(self, *, team_id: str, channel_id: str) -> FileSyncReport:
        """Return files that are visible in Slack but absent from the manifest."""
        config = self._tenant_config(team_id)
        inventory = self._scan(team_id=team_id, channel_id=channel_id)
        saved_ids = self.store.saved_file_ids(team_id=team_id, channel_id=channel_id)
        target_prefix = self._channel_storage_prefix(
            config=config,
            channel_id=channel_id,
        )
        missing = tuple(
            file for file in inventory.files if file.file_id not in saved_ids
        )
        return FileSyncReport(
            channel_id=channel_id,
            s3_bucket=config.s3_bucket,
            s3_prefix=target_prefix,
            scanned_count=len(inventory.files),
            total_count=inventory.total_count,
            truncated=inventory.truncated,
            missing_files=missing,
            skipped_files=tuple(
                file for file in inventory.files if file.file_id in saved_ids
            ),
        )

    def save_channel(  # noqa: C901
        self,
        *,
        team_id: str,
        channel_id: str,
        on_progress: Callable[[SaveProgress], None] | None = None,
    ) -> FileSyncReport:
        """Save Slack channel files that are missing from the S3 manifest.

        Args:
            team_id: Slack team ID owning the channel.
            channel_id: Slack channel ID.
            on_progress: Optional callback invoked once per processed file
                with a :class:`SaveProgress` snapshot. The callback runs in
                the worker that drives the save loop, so it must be fast and
                non-blocking; the wiring layer is responsible for rate-
                limiting any external side effects (e.g. Slack message edits).

        """
        _save_started = time.monotonic()
        config = self._tenant_config(team_id)
        inventory = self._scan(team_id=team_id, channel_id=channel_id)
        saved_ids = self.store.saved_file_ids(team_id=team_id, channel_id=channel_id)
        target_prefix = self._channel_storage_prefix(
            config=config,
            channel_id=channel_id,
        )
        existing_keys: set[str] = {
            record.s3_key
            for record in self.store.list_saved_files(
                team_id=team_id,
                channel_id=channel_id,
            )
        }
        saved_keys: list[str] = []
        skipped: list[SlackFileRef] = []
        failures: list[FileFailure] = []
        missing: list[SlackFileRef] = []
        total = len(inventory.files)

        def _emit_progress(current: SlackFileRef | None) -> None:
            if on_progress is None:
                return
            on_progress(
                SaveProgress(
                    total=total,
                    saved=len(saved_keys),
                    skipped=len(skipped),
                    failed=len(failures),
                    current_file=current,
                )
            )

        def _save_one(
            file: SlackFileRef,
        ) -> tuple[SlackFileRef, str | None, str | None]:
            """Download, upload, and record one file.

            Returns (file, s3_key, error_reason) where exactly one of
            s3_key / error_reason is non-None.
            """
            try:
                content = self.source.download_file(file, max_bytes=self.max_file_bytes)
                key = self._s3_key(
                    prefix=target_prefix,
                    file=file,
                    existing_keys=existing_keys,
                )
                self.sink.upload_bytes(config=config, key=key, content=content)
                existing_keys.add(key)
                self.store.record_saved_file(
                    SavedSlackFileRecord(
                        team_id=team_id,
                        channel_id=channel_id,
                        file_id=file.file_id,
                        content_sha256=hashlib.sha256(content).hexdigest(),
                        s3_bucket=config.s3_bucket,
                        s3_key=key,
                        size_bytes=len(content),
                        saved_at=self._timestamp(),
                    )
                )
            except (
                SlackFileSyncError,
                AuthenticationError,
                ContainerNotFoundError,
                InvalidContainerError,
                InvalidFileObjectError,
                InvalidObjectNameError,
                LocalFileAccessError,
                ObjectNotFoundError,
                StorageBackendError,
            ) as exc:
                return file, None, str(exc)
            else:
                return file, key, None

        files_to_save: list[SlackFileRef] = []
        for file in inventory.files:
            if file.file_id in saved_ids:
                skipped.append(file)
                continue
            missing.append(file)
            if file.size_bytes > self.max_file_bytes:
                failures.append(
                    FileFailure(
                        file=file,
                        reason=(
                            f"File is {_format_size(file.size_bytes)}, above the "
                            f"{_format_size(self.max_file_bytes)} limit."
                        ),
                    )
                )
                continue
            files_to_save.append(file)

        n_workers = (
            min(_SAVE_CHANNEL_MAX_WORKERS, len(files_to_save)) if files_to_save else 1
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as pool:
            fut_map = {pool.submit(_save_one, f): f for f in files_to_save}
            for fut in concurrent.futures.as_completed(fut_map):
                file, key, reason = fut.result()
                if key is not None:
                    saved_keys.append(key)
                else:
                    failures.append(FileFailure(file=file, reason=reason or "unknown"))
                _emit_progress(file)

        _emit_progress(None)
        runtime_telemetry.record_file_sync_save(
            team_id=team_id,
            duration_ms=int((time.monotonic() - _save_started) * 1000),
            saved=len(saved_keys),
            skipped=len(skipped),
            failed=len(failures),
        )
        return FileSyncReport(
            channel_id=channel_id,
            s3_bucket=config.s3_bucket,
            s3_prefix=target_prefix,
            scanned_count=len(inventory.files),
            total_count=inventory.total_count,
            truncated=inventory.truncated,
            missing_files=tuple(missing),
            saved_keys=tuple(saved_keys),
            skipped_files=tuple(skipped),
            failures=tuple(failures),
        )

    def list_channel(
        self,
        *,
        team_id: str,
        channel_id: str,
    ) -> ChannelFileListing:
        """Return the Slack-side file inventory for a channel."""
        # Confirm tenant config exists so the failure mode matches the other
        # adapter commands; we do not access S3 here.
        self._tenant_config(team_id)
        inventory = self._scan(team_id=team_id, channel_id=channel_id)
        return ChannelFileListing(
            channel_id=channel_id,
            files=inventory.files,
            total_count=inventory.total_count,
            truncated=inventory.truncated,
        )

    def changed_since_sync(
        self,
        *,
        team_id: str,
        channel_id: str,
    ) -> ChangedSinceSyncReport:
        """Return Slack files that are new or resized since the last save."""
        config = self._tenant_config(team_id)
        inventory = self._scan(team_id=team_id, channel_id=channel_id)
        saved_records = self.store.list_saved_files(
            team_id=team_id, channel_id=channel_id
        )
        saved_by_id = {record.file_id: record for record in saved_records}
        new_files = tuple(
            file for file in inventory.files if file.file_id not in saved_by_id
        )
        resized_files = tuple(
            file
            for file in inventory.files
            if file.file_id in saved_by_id
            and file.size_bytes != saved_by_id[file.file_id].size_bytes
        )
        last_sync_at = (
            max((record.saved_at for record in saved_records), default=None)
            if saved_records
            else None
        )
        return ChangedSinceSyncReport(
            channel_id=channel_id,
            s3_bucket=config.s3_bucket,
            new_files=new_files,
            resized_files=resized_files,
            last_sync_at=last_sync_at,
            truncated=inventory.truncated,
        )

    def dedupe_report(
        self,
        *,
        team_id: str,
        channel_id: str,
    ) -> DedupeReport:
        """Return content-hash duplicates and stale manifest entries."""
        config = self._tenant_config(team_id)
        inventory = self._scan(team_id=team_id, channel_id=channel_id)
        live_ids = {file.file_id for file in inventory.files}
        saved_records = self.store.list_saved_files(
            team_id=team_id, channel_id=channel_id
        )
        keys_by_hash: dict[str, list[str]] = {}
        for record in saved_records:
            keys_by_hash.setdefault(record.content_sha256, []).append(record.s3_key)
        duplicate_groups = tuple(
            DuplicateGroup(content_sha256=sha, keys=tuple(sorted(keys)))
            for sha, keys in keys_by_hash.items()
            if len(keys) > 1
        )
        stale_files = tuple(
            StaleSavedFile(file_id=record.file_id, s3_key=record.s3_key)
            for record in saved_records
            if record.file_id not in live_ids
        )
        return DedupeReport(
            channel_id=channel_id,
            s3_bucket=config.s3_bucket,
            saved_count=len(saved_records),
            duplicate_groups=duplicate_groups,
            stale_files=stale_files,
            truncated=inventory.truncated,
        )

    def dedupe_saved_files(
        self,
        *,
        team_id: str,
        channel_ids: tuple[str, ...] | None = None,
    ) -> DedupeReport:
        """Return duplicate groups across saved Slack manifests.

        This is intentionally manifest-scoped. It does not claim to scan
        arbitrary S3 objects that were uploaded outside Nimbus Slack.
        """
        config = self._tenant_config(team_id)
        if channel_ids:
            unique_channel_ids = tuple(dict.fromkeys(channel_ids))
            saved_records = tuple(
                record
                for channel_id in unique_channel_ids
                for record in self.store.list_saved_files(
                    team_id=team_id,
                    channel_id=channel_id,
                )
            )
            scope = (
                "the selected Slack channel's saved manifest"
                if len(unique_channel_ids) == 1
                else (
                    f"{len(unique_channel_ids)} selected Slack channels' "
                    "saved manifests"
                )
            )
            channel_label = ",".join(unique_channel_ids)
        else:
            saved_records = self.store.list_saved_files_for_team(team_id=team_id)
            scope = (
                "all Nimbus-saved Slack manifests in this workspace "
                "(not arbitrary bucket uploads)"
            )
            channel_label = "workspace"

        keys_by_hash: dict[str, list[str]] = {}
        for record in saved_records:
            keys_by_hash.setdefault(record.content_sha256, []).append(record.s3_key)
        duplicate_groups = tuple(
            DuplicateGroup(content_sha256=sha, keys=tuple(sorted(keys)))
            for sha, keys in keys_by_hash.items()
            if len(keys) > 1
        )
        return DedupeReport(
            channel_id=channel_label,
            s3_bucket=config.s3_bucket,
            saved_count=len(saved_records),
            duplicate_groups=duplicate_groups,
            stale_files=(),
            truncated=False,
            scope_label=scope,
            stale_checked=False,
        )

    def _tenant_config(self, team_id: str) -> TenantConfig:
        """Load tenant configuration or fail closed."""
        config = self.store.get_tenant_config(team_id)
        if config is None:
            msg = "Nimbus Slack is not configured for this workspace yet."
            raise SlackFileSyncError(msg)
        return config

    def _scan(self, *, team_id: str, channel_id: str) -> FileInventory:
        """Scan Slack files and persist observed metadata."""
        with _tracer.start_as_current_span(
            "slack.file_sync.scan",
            attributes={
                "slack.team_id": team_id,
                "slack.channel_id": channel_id,
                "slack.page_size": self.page_size,
                "slack.max_pages": self.max_pages,
            },
        ) as span:
            inventory = self.source.list_channel_files(
                channel_id,
                page_size=self.page_size,
                max_pages=self.max_pages,
            )
            span.set_attribute("nimbus.scanned_count", len(inventory.files))
            span.set_attribute("nimbus.truncated", inventory.truncated)
            indexed_at = self._timestamp()
            self.store.record_slack_files(
                SlackFileRecord(
                    team_id=team_id,
                    channel_id=channel_id,
                    file_id=file.file_id,
                    name=file.name,
                    title=file.title,
                    mimetype=file.mimetype,
                    size_bytes=file.size_bytes,
                    url_private_download=file.url_private_download,
                    user_id=file.user_id,
                    created_ts=file.created_ts,
                    indexed_at=indexed_at,
                )
                for file in inventory.files
            )
            return inventory

    def _channel_storage_prefix(self, *, config: TenantConfig, channel_id: str) -> str:
        """Return the human-readable S3 prefix for a Slack conversation."""
        installation = self.store.get_installation(config.team_id)
        workspace_label = (
            installation.team_name
            if installation is not None and installation.team_name
            else config.team_id
        )
        try:
            conversation_label = self.source.conversation_label(channel_id)
        except SlackFileSyncError as exc:
            _record_metadata_lookup_failure(channel_id=channel_id, reason=str(exc))
            conversation_label = None
        conversation_label = conversation_label or channel_id
        parts = [part for part in (config.s3_prefix.strip("/"),) if part]
        parts.extend(
            [
                "slack",
                _safe_path_segment(workspace_label, fallback=config.team_id),
                _safe_path_segment(conversation_label, fallback=channel_id),
            ]
        )
        return "/".join(parts)

    def _s3_key(
        self,
        *,
        prefix: str,
        file: SlackFileRef,
        existing_keys: set[str] | None = None,
    ) -> str:
        """Return a stable S3 key for a Slack file.

        If *existing_keys* is provided and the base key already exists, a
        short deterministic suffix derived from the file ID is appended
        before the extension to avoid collisions.
        """
        parts = [part for part in (prefix.strip("/"),) if part]
        safe_name = _safe_filename(file.name, fallback=file.file_id)
        base_key = "/".join([*parts, safe_name])
        if existing_keys is None or base_key not in existing_keys:
            return base_key
        stem, ext = _split_filename(safe_name)
        suffix = _collision_suffix(file.file_id)
        return "/".join([*parts, f"{stem}{suffix}{ext}"])

    def _timestamp(self) -> datetime:
        """Return the service timestamp used in tests and manifests."""
        return self._now or datetime.now(UTC)


def format_diff_report(report: FileSyncReport) -> str:
    """Render a Slack-friendly diff report."""
    target = _target_text(report)
    if not report.missing_files:
        scanned = _plural(report.scanned_count, "scanned Slack file")
        return (
            f"All {scanned} in this channel are recorded in `{target}`."
            f"{_truncation_suffix(report)}"
        )
    names = _names(report.missing_files)
    return (
        f"Found {_plural(len(report.missing_files), 'unsaved Slack file')} among "
        f"{report.scanned_count} scanned for `{target}`: {names}."
        f"{_truncation_suffix(report)}"
    )


def format_save_report(report: FileSyncReport) -> str:
    """Render a Slack-friendly save report."""
    target = _target_text(report)
    parts = [
        f"Saved {_plural(len(report.saved_keys), 'Slack file')} to `{target}`.",
        f"Skipped {_plural(len(report.skipped_files), 'already-saved file')}.",
    ]
    if report.failures:
        failed = _plural(len(report.failures), "file")
        parts.append(f"{failed} failed: {_failures(report.failures)}.")
    suffix = _truncation_suffix(report)
    if suffix:
        parts.append(suffix)
    return " ".join(parts)


def format_channel_listing(listing: ChannelFileListing) -> str:
    """Render a Slack-friendly listing of channel files."""
    if not listing.files:
        return "No files are visible to Nimbus in this channel."
    names = ", ".join(_format_filename(file) for file in listing.files)
    suffix = ""
    if listing.truncated:
        suffix = (
            f" Showing the first {len(listing.files)}; raise the scan bounds to "
            "see more."
        )
    found = _plural(len(listing.files), "file")
    return f"Found {found} in this channel: {names}.{suffix}"


def format_changed_since_sync(report: ChangedSinceSyncReport) -> str:
    """Render the changes-since-last-sync report."""
    if not report.new_files and not report.resized_files:
        anchor = (
            f" Last sync was at {report.last_sync_at.isoformat()}."
            if report.last_sync_at is not None
            else " No prior syncs are recorded."
        )
        return f"No Slack files have changed since the last save.{anchor}"
    parts: list[str] = []
    if report.new_files:
        parts.append(
            f"{_plural(len(report.new_files), 'new file')}: "
            f"{', '.join(_format_filename(f) for f in report.new_files)}"
        )
    if report.resized_files:
        parts.append(
            f"{_plural(len(report.resized_files), 'resized file')}: "
            f"{', '.join(_format_filename(f) for f in report.resized_files)}"
        )
    anchor = (
        f"Last sync was at {report.last_sync_at.isoformat()}."
        if report.last_sync_at is not None
        else "No prior syncs are recorded."
    )
    return f"{anchor} {' '.join(parts)}."


def format_dedupe_report(report: DedupeReport) -> str:
    """Render a duplicate-and-stale summary of saved manifest entries."""
    if report.saved_count == 0:
        return "No saved files to deduplicate yet."
    parts: list[str] = []
    if report.duplicate_groups:
        groups_text = "; ".join(
            f"{group.content_sha256[:10]}… ({len(group.keys)} copies)"
            for group in report.duplicate_groups
        )
        parts.append(
            f"{_plural(len(report.duplicate_groups), 'duplicate group')}: {groups_text}"
        )
    if report.stale_files:
        stale_entries = _plural(
            len(report.stale_files),
            "stale manifest entry",
            "stale manifest entries",
        )
        parts.append(f"{stale_entries} no longer visible in Slack")
    if not parts:
        if report.stale_checked:
            return (
                f"All {_plural(report.saved_count, 'saved file')} in "
                f"{report.scope_label} are unique and still visible in Slack."
            )
        return (
            f"All {_plural(report.saved_count, 'saved file')} in "
            f"{report.scope_label} have unique recorded content hashes."
        )
    return " ".join(parts) + "."


def _format_filename(file: SlackFileRef) -> str:
    """Render a Slack file's display name with size."""
    return f"`{file.name}` ({_format_size(file.size_bytes)})"


def _format_size(size_bytes: int) -> str:
    """Return a compact binary-size label with familiar storage units."""
    if size_bytes < BYTES_PER_DISPLAY_UNIT:
        return f"{size_bytes} B"
    value = float(size_bytes)
    for unit in DISPLAY_SIZE_UNITS:
        value /= BYTES_PER_DISPLAY_UNIT
        if value < BYTES_PER_DISPLAY_UNIT:
            return f"{value:.1f}".rstrip("0").rstrip(".") + f" {unit}"
    value /= BYTES_PER_DISPLAY_UNIT
    return f"{value:.1f}".rstrip("0").rstrip(".") + " TB"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _payload_files(payload: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    """Return validated file payloads from Slack."""
    files = payload.get("files")
    if not isinstance(files, list):
        msg = "Slack files.list response field 'files' must be a list."
        raise SlackFileSyncError(msg)
    valid_files: list[Mapping[str, object]] = []
    for item in files:
        if not isinstance(item, dict):
            msg = "Slack file entries must be mappings."
            raise SlackFileSyncError(msg)
        valid_files.append(cast("Mapping[str, object]", item))
    return valid_files


def _file_from_payload(payload: Mapping[str, object]) -> SlackFileRef:
    """Validate a Slack file object."""
    file_id = _required_str(payload, "id")
    name = _optional_str(payload, "name") or _optional_str(payload, "title") or file_id
    return SlackFileRef(
        file_id=file_id,
        name=name,
        title=_optional_str(payload, "title"),
        mimetype=_optional_str(payload, "mimetype"),
        size_bytes=_optional_int(payload, "size") or 0,
        url_private_download=_optional_str(payload, "url_private_download")
        or _optional_str(payload, "url_private"),
        user_id=_optional_str(payload, "user"),
        created_ts=_optional_int(payload, "created")
        or _optional_int(payload, "timestamp"),
    )


def _required_str(payload: Mapping[str, object], key: str) -> str:
    """Return a required Slack string field."""
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    msg = f"Slack file field {key!r} must be a non-empty string."
    raise SlackFileSyncError(msg)


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    """Return an optional Slack string field."""
    value = payload.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"Slack file field {key!r} must be a string or null."
    raise SlackFileSyncError(msg)


def _optional_int(payload: Mapping[str, object], key: str) -> int | None:
    """Return an optional Slack integer field."""
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, int):
        return value
    msg = f"Slack file field {key!r} must be an integer or null."
    raise SlackFileSyncError(msg)


def _safe_filename(name: str, *, fallback: str) -> str:
    """Return a path-safe filename segment."""
    cleaned = "".join("_" if char in "/\\\r\n\t" else char for char in name).strip()
    return cleaned or fallback


def _split_filename(name: str) -> tuple[str, str]:
    """Split a filename into (stem, extension) preserving the dot."""
    dot = name.rfind(".")
    if dot <= 0:
        return name, ""
    return name[:dot], name[dot:]


def _collision_suffix(file_id: str) -> str:
    """Return a short deterministic suffix to disambiguate filename collisions."""
    digest = hashlib.sha256(file_id.encode()).hexdigest()
    return f"-{digest[:8]}"


def _safe_path_segment(name: str, *, fallback: str) -> str:
    """Return a path-safe Slack workspace or conversation segment."""
    cleaned = "".join("_" if char in "/\\\r\n\t" else char for char in name).strip()
    return cleaned or fallback


def _optional_conversation_name(payload: Mapping[str, object]) -> str | None:
    """Return a channel or group-chat name from a Slack conversation payload."""
    return _optional_str(payload, "name") or _optional_str(payload, "name_normalized")


def _response_payload(response: object) -> Mapping[str, object] | None:
    """Return a mapping payload from a Slack SDK response object."""
    payload = cast("object", response.data if hasattr(response, "data") else response)
    if not isinstance(payload, dict):
        return None
    return cast("Mapping[str, object]", payload)


def _payload_error(payload: Mapping[str, object] | None) -> str:
    """Return the Slack error string from a response payload."""
    if payload is None:
        return "invalid_response"
    error = payload.get("error")
    return error if isinstance(error, str) else "unknown_error"


def _slack_error(exc: SlackApiError) -> str:
    """Return the Slack error string from a Slack SDK exception."""
    payload = _response_payload(exc.response)
    error = payload.get("error") if payload is not None else None
    if isinstance(error, str):
        return error
    return str(exc)


def _record_metadata_lookup_failure(*, channel_id: str, reason: str) -> None:
    """Record a non-blocking Slack metadata lookup failure on the active span."""
    trace.get_current_span().add_event(
        "slack.metadata_lookup_failed",
        {"slack.channel_id": channel_id, "reason": reason},
    )


def _target_text(report: FileSyncReport) -> str:
    """Return a readable S3 target."""
    prefix = report.s3_prefix.strip("/")
    if prefix:
        return f"s3://{report.s3_bucket}/{prefix}/"
    return f"s3://{report.s3_bucket}/"


def _names(files: Sequence[SlackFileRef]) -> str:
    """Return a compact file-name list for Slack."""
    names = [file.name for file in files[:MAX_PREVIEW_NAMES]]
    suffix = (
        ""
        if len(files) <= MAX_PREVIEW_NAMES
        else f", and {len(files) - MAX_PREVIEW_NAMES} more"
    )
    return ", ".join(f"`{name}`" for name in names) + suffix


def _failures(failures: Sequence[FileFailure]) -> str:
    """Return compact failure details for Slack."""
    details = [
        f"`{failure.file.name}` ({failure.reason})"
        for failure in failures[:MAX_PREVIEW_FAILURES]
    ]
    suffix = (
        ""
        if len(failures) <= MAX_PREVIEW_FAILURES
        else f", and {len(failures) - MAX_PREVIEW_FAILURES} more"
    )
    return ", ".join(details) + suffix


def _truncation_suffix(report: FileSyncReport) -> str:
    """Return a note when the scan reached its configured page bound."""
    if not report.truncated:
        return ""
    if report.total_count is None:
        return " More Slack file pages remain beyond this bounded scan."
    return (
        f" Scanned {report.scanned_count} of {report.total_count} Slack files; "
        "more pages remain beyond this bounded scan."
    )
