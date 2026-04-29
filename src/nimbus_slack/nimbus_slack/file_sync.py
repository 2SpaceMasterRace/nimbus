"""Slack file inventory and S3 save operations for Nimbus Slack."""

from __future__ import annotations

import hashlib
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
from slack_sdk.errors import SlackApiError

from nimbus_slack.store import (
    SavedSlackFileRecord,
    SlackFileRecord,
    SlackStore,
    TenantConfig,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from slack_sdk import WebClient

DEFAULT_FILE_SCAN_PAGE_SIZE = 100
DEFAULT_FILE_SCAN_MAX_PAGES = 3
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 30.0
MAX_PREVIEW_NAMES = 5
MAX_PREVIEW_FAILURES = 3


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


class SlackFileSource(Protocol):
    """Slack file listing and byte download capability."""

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
        payload = cast(
            "object",
            response.data if hasattr(response, "data") else response,
        )
        if not isinstance(payload, dict):
            msg = "Slack files.list response must be a mapping."
            raise SlackFileSyncError(msg)
        if payload.get("ok") is False:
            error = payload.get("error")
            detail = error if isinstance(error, str) else "unknown_error"
            msg = f"Slack files.list failed: {detail}"
            raise SlackFileSyncError(msg)
        return cast("Mapping[str, object]", payload)


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

    store: SlackStore
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
        missing = tuple(
            file for file in inventory.files if file.file_id not in saved_ids
        )
        return FileSyncReport(
            channel_id=channel_id,
            s3_bucket=config.s3_bucket,
            s3_prefix=config.s3_prefix,
            scanned_count=len(inventory.files),
            total_count=inventory.total_count,
            truncated=inventory.truncated,
            missing_files=missing,
            skipped_files=tuple(
                file for file in inventory.files if file.file_id in saved_ids
            ),
        )

    def save_channel(self, *, team_id: str, channel_id: str) -> FileSyncReport:
        """Save Slack channel files that are missing from the S3 manifest."""
        config = self._tenant_config(team_id)
        inventory = self._scan(team_id=team_id, channel_id=channel_id)
        saved_ids = self.store.saved_file_ids(team_id=team_id, channel_id=channel_id)
        saved_keys: list[str] = []
        skipped: list[SlackFileRef] = []
        failures: list[FileFailure] = []
        missing: list[SlackFileRef] = []
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
                            f"File is {file.size_bytes} bytes, above the "
                            f"{self.max_file_bytes} byte limit."
                        ),
                    )
                )
                continue
            try:
                content = self.source.download_file(file, max_bytes=self.max_file_bytes)
                key = self._s3_key(config=config, channel_id=channel_id, file=file)
                self.sink.upload_bytes(config=config, key=key, content=content)
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
                saved_keys.append(key)
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
                failures.append(FileFailure(file=file, reason=str(exc)))
        return FileSyncReport(
            channel_id=channel_id,
            s3_bucket=config.s3_bucket,
            s3_prefix=config.s3_prefix,
            scanned_count=len(inventory.files),
            total_count=inventory.total_count,
            truncated=inventory.truncated,
            missing_files=tuple(missing),
            saved_keys=tuple(saved_keys),
            skipped_files=tuple(skipped),
            failures=tuple(failures),
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
        inventory = self.source.list_channel_files(
            channel_id,
            page_size=self.page_size,
            max_pages=self.max_pages,
        )
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

    def _s3_key(
        self,
        *,
        config: TenantConfig,
        channel_id: str,
        file: SlackFileRef,
    ) -> str:
        """Return a stable S3 key for a Slack file."""
        parts = [part for part in (config.s3_prefix.strip("/"),) if part]
        parts.extend(["slack", config.team_id, channel_id, file.file_id])
        parts.append(_safe_filename(file.name, fallback=file.file_id))
        return "/".join(parts)

    def _timestamp(self) -> datetime:
        """Return the service timestamp used in tests and manifests."""
        return self._now or datetime.now(UTC)


def format_diff_report(report: FileSyncReport) -> str:
    """Render a Slack-friendly diff report."""
    target = _target_text(report)
    if not report.missing_files:
        return (
            f"All {report.scanned_count} scanned Slack file(s) in this channel "
            f"are recorded in `{target}`.{_truncation_suffix(report)}"
        )
    names = _names(report.missing_files)
    return (
        f"Found {len(report.missing_files)} unsaved Slack file(s) among "
        f"{report.scanned_count} scanned for `{target}`: {names}."
        f"{_truncation_suffix(report)}"
    )


def format_save_report(report: FileSyncReport) -> str:
    """Render a Slack-friendly save report."""
    target = _target_text(report)
    parts = [
        f"Saved {len(report.saved_keys)} Slack file(s) to `{target}`.",
        f"Skipped {len(report.skipped_files)} already-saved file(s).",
    ]
    if report.failures:
        parts.append(
            f"{len(report.failures)} file(s) failed: {_failures(report.failures)}."
        )
    suffix = _truncation_suffix(report)
    if suffix:
        parts.append(suffix)
    return " ".join(parts)


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
        f" Scanned {report.scanned_count} of {report.total_count} Slack file(s); "
        "more pages remain beyond this bounded scan."
    )
