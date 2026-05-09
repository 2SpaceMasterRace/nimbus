"""Unit tests for the Nimbus Slack background task worker helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from nimbus_runtime.backup import (
    BackupChannelRef,
    BackupDestination,
    BackupManifestEntry,
)
from nimbus_runtime.domain import TenantIdentity
from nimbus_slack.worker import (
    _InMemoryManifestStore,
    _S3ObjectSink,
    _slack_task_handler,
    _SlackFileSource,
)

pytestmark = pytest.mark.unit

# ── Helpers ───────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TENANT = TenantIdentity(platform="slack", workspace_id="T001")
_CHANNEL = BackupChannelRef(
    platform="slack",
    workspace_id="T001",
    channel_id="C001",
    channel_name="general",
)
_DEST = BackupDestination(container="my-bucket", prefix="slack/")


def _manifest_entry(file_id: str = "F001") -> BackupManifestEntry:
    return BackupManifestEntry(
        tenant=_TENANT,
        channel=_CHANNEL,
        file_id=file_id,
        name="file.txt",
        content_type="text/plain",
        size_bytes=100,
        content_sha256="a" * 64,
        destination=_DEST,
        object_key="slack/file.txt",
        saved_at=_NOW,
    )


# ── _InMemoryManifestStore ────────────────────────────────────────────────────


class TestInMemoryManifestStore:
    def test_empty_store_returns_no_entries(self) -> None:
        store = _InMemoryManifestStore()
        result = store.list_entries(tenant=_TENANT, channel=_CHANNEL)
        assert result == ()

    def test_record_and_retrieve_single_entry(self) -> None:
        store = _InMemoryManifestStore()
        entry = _manifest_entry("F001")
        store.record_entry(entry)
        result = store.list_entries(tenant=_TENANT, channel=_CHANNEL)
        assert len(result) == 1
        assert result[0].file_id == "F001"

    def test_record_multiple_entries(self) -> None:
        store = _InMemoryManifestStore()
        store.record_entry(_manifest_entry("F001"))
        store.record_entry(_manifest_entry("F002"))
        result = store.list_entries(tenant=_TENANT, channel=_CHANNEL)
        assert len(result) == 2

    def test_record_same_file_id_overwrites(self) -> None:
        store = _InMemoryManifestStore()
        store.record_entry(_manifest_entry("F001"))
        store.record_entry(_manifest_entry("F001"))
        result = store.list_entries(tenant=_TENANT, channel=_CHANNEL)
        assert len(result) == 1

    def test_list_entries_filters_by_tenant(self) -> None:
        store = _InMemoryManifestStore()
        entry = _manifest_entry("F001")
        store.record_entry(entry)
        other_tenant = TenantIdentity(platform="slack", workspace_id="OTHER")
        result = store.list_entries(tenant=other_tenant, channel=_CHANNEL)
        assert result == ()

    def test_list_entries_filters_by_channel(self) -> None:
        store = _InMemoryManifestStore()
        entry = _manifest_entry("F001")
        store.record_entry(entry)
        other_channel = BackupChannelRef(
            platform="slack",
            workspace_id="T001",
            channel_id="C_OTHER",
        )
        result = store.list_entries(tenant=_TENANT, channel=other_channel)
        assert result == ()


# ── _SlackFileSource ──────────────────────────────────────────────────────────


class TestSlackFileSource:
    def test_list_files_returns_empty_scan(self) -> None:
        source = _SlackFileSource(channel_id="C001", team_id="T001")
        scan = source.list_files(_CHANNEL)
        assert scan.truncated is False
        assert scan.total_count == 0
        assert scan.files == ()

    def test_list_files_with_custom_page_params(self) -> None:
        source = _SlackFileSource(channel_id="C001", team_id="T001")
        scan = source.list_files(_CHANNEL, page_size=50, max_pages=5)
        assert scan.files == ()

    def test_download_file_raises_not_implemented(self) -> None:
        source = _SlackFileSource(channel_id="C001", team_id="T001")
        with pytest.raises(NotImplementedError, match="download_file"):
            source.download_file(object(), max_bytes=1024)


# ── _S3ObjectSink ─────────────────────────────────────────────────────────────


class TestS3ObjectSink:
    def test_verify_object_returns_true(self) -> None:
        sink = _S3ObjectSink(runtime=object())
        result = sink.verify_object(
            destination=_DEST,
            key="slack/file.txt",
            content_sha256="a" * 64,
            size_bytes=100,
        )
        assert result is True

    def test_upload_bytes_without_storage_attr_does_not_raise(self) -> None:
        """Upload should silently skip when runtime has no .storage attribute."""
        sink = _S3ObjectSink(runtime=object())
        # Should not raise
        sink.upload_bytes(destination=_DEST, key="slack/file.txt", content=b"data")

    def test_upload_bytes_with_none_storage_does_not_raise(self) -> None:
        """Upload should silently skip when runtime.storage is None."""

        class FakeRuntime:
            storage = None

        sink = _S3ObjectSink(runtime=FakeRuntime())
        sink.upload_bytes(destination=_DEST, key="slack/file.txt", content=b"data")


# ── _slack_task_handler ───────────────────────────────────────────────────────


@dataclass
class _FakeTask:
    task_id: str = "T-fake-001"
    intent: str = "unknown_intent"
    tenant: TenantIdentity = field(
        default_factory=lambda: TenantIdentity(platform="slack", workspace_id="T001")
    )
    status: object = type("S", (), {"value": "pending"})()
    metadata: dict[str, object] | None = None


@dataclass
class _FakeLeaseContext:
    task: _FakeTask = field(default_factory=_FakeTask)


class TestSlackTaskHandler:
    def test_unknown_intent_does_not_raise(self) -> None:
        """An unknown task intent should log a warning and return without error."""
        ctx = _FakeLeaseContext(task=_FakeTask(intent="totally_unknown_intent"))
        # Should complete without raising
        asyncio.run(_slack_task_handler(ctx))  # type: ignore[arg-type]

    def test_handler_completes_for_unrecognized_intent(self) -> None:
        """Handler must return (not raise) for any unrecognized intent."""
        ctx = _FakeLeaseContext(task=_FakeTask(intent="sync_files"))
        asyncio.run(_slack_task_handler(ctx))  # type: ignore[arg-type]
