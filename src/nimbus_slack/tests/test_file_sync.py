"""Tests for Slack file diff/save operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cloud_storage_api import StorageBackendError
from cryptography.fernet import Fernet
from nimbus_slack.crypto import SecretCodec
from nimbus_slack.file_sync import (
    FileInventory,
    SlackFileRef,
    SlackFileSyncService,
    format_diff_report,
    format_save_report,
)
from nimbus_slack.store import (
    SavedSlackFileRecord,
    SlackFileRecord,
    SlackInstallation,
    SlackStore,
    TenantConfig,
)

pytestmark = pytest.mark.unit


@dataclass(slots=True)
class _FakeSource:
    inventory: FileInventory
    content_by_file_id: dict[str, bytes]
    downloads: list[str] = field(default_factory=list)

    def list_channel_files(
        self,
        channel_id: str,
        *,
        page_size: int,
        max_pages: int,
    ) -> FileInventory:
        assert channel_id == "C123"
        assert page_size > 0
        assert max_pages > 0
        return self.inventory

    def download_file(self, file: SlackFileRef, *, max_bytes: int) -> bytes:
        self.downloads.append(file.file_id)
        content = self.content_by_file_id[file.file_id]
        assert len(content) <= max_bytes
        return content


@dataclass(slots=True)
class _FakeSink:
    uploads: list[tuple[str, bytes]] = field(default_factory=list)
    error: Exception | None = None

    def upload_bytes(
        self,
        *,
        config: TenantConfig,
        key: str,
        content: bytes,
    ) -> None:
        assert config.s3_bucket == "nimbus-test-bucket"
        if self.error is not None:
            raise self.error
        self.uploads.append((key, content))


def _store(path: Path) -> SlackStore:
    store = SlackStore(
        db_path=path,
        codec=SecretCodec.from_key(Fernet.generate_key().decode("utf-8")),
    )
    store.upsert_installation(
        SlackInstallation(
            team_id="T123",
            enterprise_id=None,
            team_name="Nimbus Lab",
            bot_user_id="Ubot",
            bot_token="xoxb-file-token",  # noqa: S106
            scopes=("files:read", "chat:write"),
            installed_by="Uadmin",
            installed_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
    )
    store.upsert_tenant_config(
        TenantConfig(
            team_id="T123",
            openrouter_api_key="sk-or-secret",
            aws_access_key_id="AKIA_TEST_SECRET",
            aws_secret_access_key="aws-secret",  # noqa: S106
            aws_region="us-east-1",
            s3_bucket="nimbus-test-bucket",
            s3_prefix="archive",
            status="configured",
            updated_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
    )
    return store


def _files() -> tuple[SlackFileRef, SlackFileRef]:
    return (
        SlackFileRef(
            file_id="F1",
            name="already.txt",
            title=None,
            mimetype="text/plain",
            size_bytes=3,
            url_private_download="https://files.slack.test/F1",
            user_id="U1",
            created_ts=1,
        ),
        SlackFileRef(
            file_id="F2",
            name="missing.txt",
            title=None,
            mimetype="text/plain",
            size_bytes=4,
            url_private_download="https://files.slack.test/F2",
            user_id="U2",
            created_ts=2,
        ),
    )


def _service(
    store: SlackStore,
    source: _FakeSource,
    sink: _FakeSink,
) -> SlackFileSyncService:
    return SlackFileSyncService(
        store=store,
        source=source,
        sink=sink,
        page_size=100,
        max_pages=1,
        max_file_bytes=16,
        _now=datetime(2026, 5, 9, tzinfo=UTC),
    )


def test_diff_channel_reports_files_missing_from_manifest(tmp_path: Path) -> None:
    """Diff should compare current Slack inventory to durable S3 manifest."""
    store = _store(tmp_path / "slack.sqlite3")
    file1, file2 = _files()
    _record_saved(store, file1)
    source = _FakeSource(
        inventory=FileInventory(files=(file1, file2), total_count=2, truncated=False),
        content_by_file_id={},
    )
    sink = _FakeSink()

    report = _service(store, source, sink).diff_channel(
        team_id="T123",
        channel_id="C123",
    )

    assert report.missing_files == (file2,)
    assert report.skipped_files == (file1,)
    assert "missing.txt" in format_diff_report(report)


def test_save_channel_uploads_only_missing_files_and_records_manifest(
    tmp_path: Path,
) -> None:
    """Save should be idempotent through the manifest."""
    store = _store(tmp_path / "slack.sqlite3")
    file1, file2 = _files()
    _record_saved(store, file1)
    source = _FakeSource(
        inventory=FileInventory(files=(file1, file2), total_count=2, truncated=False),
        content_by_file_id={"F2": b"data"},
    )
    sink = _FakeSink()
    service = _service(store, source, sink)

    first = service.save_channel(team_id="T123", channel_id="C123")
    second = service.save_channel(team_id="T123", channel_id="C123")

    assert first.saved_keys == ("archive/slack/T123/C123/F2/missing.txt",)
    assert first.skipped_files == (file1,)
    assert second.saved_keys == ()
    assert source.downloads == ["F2"]
    assert sink.uploads == [("archive/slack/T123/C123/F2/missing.txt", b"data")]
    assert store.saved_file_ids(team_id="T123", channel_id="C123") == {"F1", "F2"}
    assert "Saved 1 Slack file" in format_save_report(first)


def test_save_channel_skips_oversized_files(tmp_path: Path) -> None:
    """Files above the configured byte limit should fail before download."""
    store = _store(tmp_path / "slack.sqlite3")
    oversized = SlackFileRef(
        file_id="F3",
        name="large.bin",
        title=None,
        mimetype="application/octet-stream",
        size_bytes=20,
        url_private_download="https://files.slack.test/F3",
        user_id="U3",
        created_ts=3,
    )
    source = _FakeSource(
        inventory=FileInventory(files=(oversized,), total_count=1, truncated=False),
        content_by_file_id={"F3": b"not-used"},
    )
    sink = _FakeSink()

    report = _service(store, source, sink).save_channel(
        team_id="T123",
        channel_id="C123",
    )

    assert report.saved_keys == ()
    assert len(report.failures) == 1
    assert source.downloads == []
    assert sink.uploads == []


def test_save_channel_records_storage_failures(tmp_path: Path) -> None:
    """Storage domain errors should fail one file without crashing the batch."""
    store = _store(tmp_path / "slack.sqlite3")
    file = _files()[1]
    source = _FakeSource(
        inventory=FileInventory(files=(file,), total_count=1, truncated=False),
        content_by_file_id={"F2": b"data"},
    )
    msg = "s3 unavailable"
    sink = _FakeSink(error=StorageBackendError(msg))

    report = _service(store, source, sink).save_channel(
        team_id="T123",
        channel_id="C123",
    )

    assert report.saved_keys == ()
    assert len(report.failures) == 1
    assert store.saved_file_ids(team_id="T123", channel_id="C123") == set()


def _record_saved(store: SlackStore, file: SlackFileRef) -> None:
    store.record_slack_files(
        (
            SlackFileRecord(
                team_id="T123",
                channel_id="C123",
                file_id=file.file_id,
                name=file.name,
                title=file.title,
                mimetype=file.mimetype,
                size_bytes=file.size_bytes,
                url_private_download=file.url_private_download,
                user_id=file.user_id,
                created_ts=file.created_ts,
                indexed_at=datetime(2026, 5, 9, tzinfo=UTC),
            ),
        )
    )
    store.record_saved_file(
        SavedSlackFileRecord(
            team_id="T123",
            channel_id="C123",
            file_id=file.file_id,
            content_sha256="sha",
            s3_bucket="nimbus-test-bucket",
            s3_key=f"archive/slack/T123/C123/{file.file_id}/{file.name}",
            size_bytes=file.size_bytes,
            saved_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
    )
