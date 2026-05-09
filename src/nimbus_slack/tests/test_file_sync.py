"""Tests for Slack file diff/save operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Self, cast

import httpx
import pytest
from cloud_storage_api import StorageBackendError
from cryptography.fernet import Fernet
from nimbus_slack.crypto import SecretCodec
from nimbus_slack.file_sync import (
    ChannelFileListing,
    DedupeReport,
    FileFailure,
    FileInventory,
    FileSyncReport,
    SlackFileRef,
    SlackFileSyncError,
    SlackFileSyncService,
    SlackWebFileSource,
    format_changed_since_sync,
    format_channel_listing,
    format_dedupe_report,
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
from slack_sdk import WebClient

pytestmark = pytest.mark.unit


@dataclass(slots=True)
class _FakeSource:
    inventory: FileInventory
    content_by_file_id: dict[str, bytes]
    conversation_name: str | None = "project-alpha"
    downloads: list[str] = field(default_factory=list)

    def conversation_label(self, channel_id: str) -> str | None:
        assert channel_id == "C123"
        return self.conversation_name

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


@dataclass(frozen=True, slots=True)
class _FakeSlackResponse:
    data: object


@dataclass(slots=True)
class _FakeSlackMetadataClient:
    conversation_payload: dict[str, object]
    user_payload: dict[str, object] | None = None

    def conversations_info(self, *, channel: str) -> _FakeSlackResponse:
        assert channel == "C123"
        return _FakeSlackResponse(self.conversation_payload)

    def users_info(self, *, user: str) -> _FakeSlackResponse:
        assert user == "U123"
        assert self.user_payload is not None
        return _FakeSlackResponse(self.user_payload)


@dataclass(slots=True)
class _FakeSlackFileClient:
    pages: list[object]
    calls: list[tuple[str, int, int]] = field(default_factory=list)

    def files_list(self, *, channel: str, count: int, page: int) -> _FakeSlackResponse:
        self.calls.append((channel, count, page))
        return _FakeSlackResponse(self.pages[page - 1])


@dataclass(slots=True)
class _FakeStreamResponse:
    chunks: tuple[bytes, ...]

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self) -> tuple[bytes, ...]:
        return self.chunks


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

    assert first.saved_keys == ("archive/slack/Nimbus Lab/project-alpha/missing.txt",)
    assert first.skipped_files == (file1,)
    assert second.saved_keys == ()
    assert source.downloads == ["F2"]
    assert sink.uploads == [
        ("archive/slack/Nimbus Lab/project-alpha/missing.txt", b"data")
    ]
    assert store.saved_file_ids(team_id="T123", channel_id="C123") == {"F1", "F2"}
    assert "Saved 1 Slack file" in format_save_report(first)


def test_web_file_source_returns_channel_name_for_storage_prefixes() -> None:
    """Slack channel metadata should provide the readable storage segment."""
    client = _FakeSlackMetadataClient(
        conversation_payload={"ok": True, "channel": {"name": "project-alpha"}}
    )
    source = SlackWebFileSource(
        client=cast("WebClient", client),
        bot_token="xoxb-test",  # noqa: S106
    )

    assert source.conversation_label("C123") == "project-alpha"


def test_web_file_source_returns_dm_user_name_for_storage_prefixes() -> None:
    """Slack DM metadata should provide a readable chat storage segment."""
    client = _FakeSlackMetadataClient(
        conversation_payload={"ok": True, "channel": {"user": "U123"}},
        user_payload={
            "ok": True,
            "user": {"profile": {"display_name": "Aarav Agrawal"}},
        },
    )
    source = SlackWebFileSource(
        client=cast("WebClient", client),
        bot_token="xoxb-test",  # noqa: S106
    )

    assert source.conversation_label("C123") == "chat-Aarav Agrawal"


def test_web_file_source_returns_none_for_unusable_channel_payloads() -> None:
    """Slack metadata lookup failures should degrade to caller fallback labels."""
    for payload in (
        {"ok": False, "error": "missing_scope"},
        {"ok": True},
        {"ok": True, "channel": {}},
    ):
        client = _FakeSlackMetadataClient(conversation_payload=payload)
        source = SlackWebFileSource(
            client=cast("WebClient", client),
            bot_token="xoxb-test",  # noqa: S106
        )

        assert source.conversation_label("C123") is None


def test_web_file_source_dm_label_falls_back_to_user_real_name() -> None:
    """DM labels should still be readable when display_name is absent."""
    client = _FakeSlackMetadataClient(
        conversation_payload={"ok": True, "channel": {"user": "U123"}},
        user_payload={"ok": True, "user": {"real_name": "Ada Lovelace"}},
    )
    source = SlackWebFileSource(
        client=cast("WebClient", client),
        bot_token="xoxb-test",  # noqa: S106
    )

    assert source.conversation_label("C123") == "chat-Ada Lovelace"


def test_web_file_source_dm_label_falls_back_to_user_id() -> None:
    """A missing user payload should not block building a stable S3 prefix."""
    client = _FakeSlackMetadataClient(
        conversation_payload={"ok": True, "channel": {"user": "U123"}},
        user_payload={"ok": True, "user": "not-a-mapping"},
    )
    source = SlackWebFileSource(
        client=cast("WebClient", client),
        bot_token="xoxb-test",  # noqa: S106
    )

    assert source.conversation_label("C123") == "chat-U123"


def test_web_file_source_lists_paginated_files() -> None:
    """Slack files.list pages should parse into precise file references."""
    client = _FakeSlackFileClient(
        pages=[
            {
                "ok": True,
                "files": [
                    {
                        "id": "F1",
                        "title": "report.csv",
                        "mimetype": "text/csv",
                        "size": 5,
                        "url_private": "https://files.slack.test/F1",
                        "user": "U1",
                        "timestamp": 10,
                    }
                ],
                "paging": {"total": 2, "pages": 2},
            },
            {
                "ok": True,
                "files": [{"id": "F2", "name": "notes.txt", "created": 11}],
                "paging": {"total": 2, "pages": 2},
            },
        ]
    )
    source = SlackWebFileSource(
        client=cast("WebClient", client),
        bot_token="xoxb-test",  # noqa: S106
    )

    inventory = source.list_channel_files("C123", page_size=1, max_pages=2)

    assert client.calls == [("C123", 1, 1), ("C123", 1, 2)]
    assert inventory.total_count == 2
    assert inventory.truncated is False
    assert [file.file_id for file in inventory.files] == ["F1", "F2"]
    assert inventory.files[0].name == "report.csv"
    assert inventory.files[0].url_private_download == "https://files.slack.test/F1"
    assert inventory.files[0].created_ts == 10


def test_web_file_source_rejects_invalid_scan_bounds() -> None:
    """Boundary scan controls must be explicit positive integers."""
    source = SlackWebFileSource(
        client=cast("WebClient", _FakeSlackFileClient(pages=[])),
        bot_token="xoxb-test",  # noqa: S106
    )

    with pytest.raises(SlackFileSyncError, match="bounds must be positive"):
        source.list_channel_files("C123", page_size=0, max_pages=1)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (["not-a-mapping"], "response must be a mapping"),
        ({"ok": False, "error": "missing_scope"}, "missing_scope"),
        ({"ok": True}, "field 'files' must be a list"),
        ({"ok": True, "files": ["bad"]}, "entries must be mappings"),
        ({"ok": True, "files": [{"id": ""}]}, "non-empty string"),
        ({"ok": True, "files": [{"id": "F1", "name": 1}]}, "string or null"),
        ({"ok": True, "files": [{"id": "F1", "size": "large"}]}, "integer"),
    ],
)
def test_web_file_source_rejects_malformed_file_payloads(
    payload: object,
    match: str,
) -> None:
    """Malformed Slack transport payloads should be rejected before storage."""
    source = SlackWebFileSource(
        client=cast("WebClient", _FakeSlackFileClient(pages=[payload])),
        bot_token="xoxb-test",  # noqa: S106
    )

    with pytest.raises(SlackFileSyncError, match=match):
        source.list_channel_files("C123", page_size=1, max_pages=1)


def test_web_file_source_downloads_private_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack downloads should stream bytes with bot-token auth."""
    calls: list[tuple[str, str, dict[str, str], float]] = []

    def fake_stream(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeStreamResponse:
        calls.append((method, url, headers, timeout))
        return _FakeStreamResponse((b"ab", b"cd"))

    monkeypatch.setattr("nimbus_slack.file_sync.httpx.stream", fake_stream)
    source = SlackWebFileSource(
        client=cast("WebClient", _FakeSlackFileClient(pages=[])),
        bot_token="xoxb-test",  # noqa: S106
    )

    content = source.download_file(_files()[1], max_bytes=10)

    assert content == b"abcd"
    assert calls == [
        (
            "GET",
            "https://files.slack.test/F2",
            {"Authorization": "Bearer xoxb-test"},
            30.0,
        )
    ]


def test_web_file_source_rejects_download_without_private_url() -> None:
    """Files without private URLs cannot be safely downloaded."""
    file = SlackFileRef(
        file_id="F-no-url",
        name="no-url.txt",
        title=None,
        mimetype=None,
        size_bytes=1,
        url_private_download=None,
        user_id=None,
        created_ts=None,
    )
    source = SlackWebFileSource(
        client=cast("WebClient", _FakeSlackFileClient(pages=[])),
        bot_token="xoxb-test",  # noqa: S106
    )

    with pytest.raises(SlackFileSyncError, match="does not expose"):
        source.download_file(file, max_bytes=10)


def test_web_file_source_rejects_nonpositive_download_limit() -> None:
    """Download bounds should fail closed before the network call."""
    source = SlackWebFileSource(
        client=cast("WebClient", _FakeSlackFileClient(pages=[])),
        bot_token="xoxb-test",  # noqa: S106
    )

    with pytest.raises(SlackFileSyncError, match="byte limit must be positive"):
        source.download_file(_files()[1], max_bytes=0)


def test_web_file_source_rejects_oversized_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file larger than the byte bound should stop during streaming."""

    def fake_stream(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeStreamResponse:
        assert method == "GET"
        assert url == "https://files.slack.test/F2"
        assert headers == {"Authorization": "Bearer xoxb-test"}
        assert timeout == 30.0
        return _FakeStreamResponse((b"abcd", b"ef"))

    monkeypatch.setattr("nimbus_slack.file_sync.httpx.stream", fake_stream)
    source = SlackWebFileSource(
        client=cast("WebClient", _FakeSlackFileClient(pages=[])),
        bot_token="xoxb-test",  # noqa: S106
    )

    with pytest.raises(SlackFileSyncError, match="exceeds"):
        source.download_file(_files()[1], max_bytes=4)


def test_web_file_source_translates_http_download_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP transport failures should become Slack file-sync domain errors."""

    def fake_stream(
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
    ) -> _FakeStreamResponse:
        assert method == "GET"
        assert url == "https://files.slack.test/F2"
        assert headers == {"Authorization": "Bearer xoxb-test"}
        assert timeout == 30.0
        msg = "connection reset"
        raise httpx.ConnectError(msg)

    monkeypatch.setattr("nimbus_slack.file_sync.httpx.stream", fake_stream)
    source = SlackWebFileSource(
        client=cast("WebClient", _FakeSlackFileClient(pages=[])),
        bot_token="xoxb-test",  # noqa: S106
    )

    with pytest.raises(SlackFileSyncError, match="Failed to download"):
        source.download_file(_files()[1], max_bytes=10)


def test_save_channel_falls_back_to_channel_id_when_name_is_unavailable(
    tmp_path: Path,
) -> None:
    """S3 keys should stay writable when Slack metadata lookup is unavailable."""
    store = _store(tmp_path / "slack.sqlite3")
    file = _files()[1]
    source = _FakeSource(
        inventory=FileInventory(files=(file,), total_count=1, truncated=False),
        content_by_file_id={"F2": b"data"},
        conversation_name=None,
    )
    sink = _FakeSink()

    report = _service(store, source, sink).save_channel(
        team_id="T123",
        channel_id="C123",
    )

    assert report.saved_keys == ("archive/slack/Nimbus Lab/C123/missing.txt",)


def test_save_channel_falls_back_when_channel_label_lookup_fails(
    tmp_path: Path,
) -> None:
    """A metadata lookup failure should not block the durable save operation."""

    @dataclass(slots=True)
    class RaisingLabelSource(_FakeSource):
        def conversation_label(self, channel_id: str) -> str | None:
            assert channel_id == "C123"
            msg = "metadata unavailable"
            raise SlackFileSyncError(msg)

    store = _store(tmp_path / "slack.sqlite3")
    file = _files()[1]
    source = RaisingLabelSource(
        inventory=FileInventory(files=(file,), total_count=1, truncated=False),
        content_by_file_id={"F2": b"data"},
    )
    sink = _FakeSink()

    report = _service(store, source, sink).save_channel(
        team_id="T123",
        channel_id="C123",
    )

    assert report.saved_keys == ("archive/slack/Nimbus Lab/C123/missing.txt",)


def test_save_channel_handles_filename_collisions(tmp_path: Path) -> None:
    """Two files with the same name in one channel should get unique keys."""
    store = _store(tmp_path / "slack.sqlite3")
    file_a = SlackFileRef(
        file_id="FA",
        name="photo.png",
        title=None,
        mimetype="image/png",
        size_bytes=3,
        url_private_download="https://files.slack.test/FA",
        user_id="U1",
        created_ts=1,
    )
    file_b = SlackFileRef(
        file_id="FB",
        name="photo.png",
        title=None,
        mimetype="image/png",
        size_bytes=3,
        url_private_download="https://files.slack.test/FB",
        user_id="U2",
        created_ts=2,
    )
    source = _FakeSource(
        inventory=FileInventory(
            files=(file_a, file_b),
            total_count=2,
            truncated=False,
        ),
        content_by_file_id={"FA": b"aaa", "FB": b"bbb"},
    )
    sink = _FakeSink()

    report = _service(store, source, sink).save_channel(
        team_id="T123",
        channel_id="C123",
    )

    assert len(report.saved_keys) == 2
    assert "archive/slack/Nimbus Lab/project-alpha/photo.png" in report.saved_keys
    collision_key = next(k for k in report.saved_keys if "photo-" in k)
    assert collision_key.endswith(".png")
    assert sink.uploads[0][0] != sink.uploads[1][0]


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


def test_list_channel_returns_slack_inventory(tmp_path: Path) -> None:
    """list_channel should surface Slack files without touching the manifest."""
    store = _store(tmp_path / "slack.sqlite3")
    file1, file2 = _files()
    source = _FakeSource(
        inventory=FileInventory(files=(file1, file2), total_count=2, truncated=False),
        content_by_file_id={},
    )
    sink = _FakeSink()

    listing = _service(store, source, sink).list_channel(
        team_id="T123",
        channel_id="C123",
    )

    assert listing.files == (file1, file2)
    text = format_channel_listing(listing)
    assert "already.txt" in text
    assert "missing.txt" in text


def test_changed_since_sync_flags_new_and_resized_files(tmp_path: Path) -> None:
    """changed_since_sync should compare current Slack inventory to manifest."""
    store = _store(tmp_path / "slack.sqlite3")
    file1, file2 = _files()
    _record_saved(store, file1)
    resized = SlackFileRef(
        file_id=file1.file_id,
        name=file1.name,
        title=file1.title,
        mimetype=file1.mimetype,
        size_bytes=file1.size_bytes + 1,
        url_private_download=file1.url_private_download,
        user_id=file1.user_id,
        created_ts=file1.created_ts,
    )
    source = _FakeSource(
        inventory=FileInventory(files=(resized, file2), total_count=2, truncated=False),
        content_by_file_id={},
    )
    sink = _FakeSink()

    report = _service(store, source, sink).changed_since_sync(
        team_id="T123",
        channel_id="C123",
    )

    assert report.new_files == (file2,)
    assert report.resized_files == (resized,)
    assert report.last_sync_at == datetime(2026, 5, 9, tzinfo=UTC)
    assert "1 new file" in format_changed_since_sync(report)


def test_format_channel_listing_handles_empty_inventory() -> None:
    """An empty channel listing should produce an explicit no-files message."""
    rendered = format_channel_listing(
        ChannelFileListing(channel_id="C0", files=(), total_count=0, truncated=False)
    )
    assert "No files" in rendered


def test_format_channel_listing_uses_human_readable_sizes() -> None:
    """A channel listing should not force users to read raw byte counts."""
    rendered = format_channel_listing(
        ChannelFileListing(
            channel_id="C0",
            files=(
                SlackFileRef(
                    file_id="Flarge",
                    name="large.bin",
                    title=None,
                    mimetype=None,
                    size_bytes=2_500_000,
                    url_private_download=None,
                    user_id=None,
                    created_ts=None,
                ),
            ),
            total_count=1,
            truncated=False,
        )
    )

    assert "2.4 MB" in rendered


def test_format_channel_listing_uses_tb_units() -> None:
    """Very large files should still render compactly."""
    rendered = format_channel_listing(
        ChannelFileListing(
            channel_id="C0",
            files=(
                SlackFileRef(
                    file_id="Fhuge",
                    name="huge.bin",
                    title=None,
                    mimetype=None,
                    size_bytes=5 * 1024 * 1024 * 1024 * 1024,
                    url_private_download=None,
                    user_id=None,
                    created_ts=None,
                ),
            ),
            total_count=1,
            truncated=False,
        )
    )

    assert "5 TB" in rendered


def test_format_diff_report_clean_manifest_mentions_target() -> None:
    """A clean diff should be explicit about the checked S3 destination."""
    rendered = format_diff_report(
        FileSyncReport(
            channel_id="C0",
            s3_bucket="bucket",
            s3_prefix="",
            scanned_count=3,
            total_count=3,
            truncated=True,
        )
    )

    assert "All 3 scanned" in rendered
    assert "s3://bucket/" in rendered


def test_format_save_report_lists_failures_and_unbounded_truncation() -> None:
    """Save summaries should preserve failure reasons and scan-bound hints."""
    rendered = format_save_report(
        FileSyncReport(
            channel_id="C0",
            s3_bucket="bucket",
            s3_prefix="archive",
            scanned_count=2,
            total_count=None,
            truncated=True,
            failures=(
                FileFailure(file=_files()[0], reason="too large"),
                FileFailure(file=_files()[1], reason="download failed"),
                FileFailure(file=_files()[0], reason="storage unavailable"),
                FileFailure(file=_files()[1], reason="retry later"),
            ),
        )
    )

    assert "too large" in rendered
    assert "and 1 more" in rendered
    assert "More Slack file pages remain" in rendered


def test_format_channel_listing_truncation_suffix(tmp_path: Path) -> None:
    """A truncated listing should include a hint that more files exist."""
    store = _store(tmp_path / "slack.sqlite3")
    file1, file2 = _files()
    source = _FakeSource(
        inventory=FileInventory(files=(file1, file2), total_count=10, truncated=True),
        content_by_file_id={},
    )
    sink = _FakeSink()

    listing = _service(store, source, sink).list_channel(
        team_id="T123",
        channel_id="C123",
    )

    assert "Showing the first" in format_channel_listing(listing)


def test_format_changed_since_sync_no_changes_with_last_sync(tmp_path: Path) -> None:
    """When nothing changed but a prior sync exists the timestamp is shown."""
    store = _store(tmp_path / "slack.sqlite3")
    file1, _ = _files()
    _record_saved(store, file1)
    source = _FakeSource(
        inventory=FileInventory(files=(file1,), total_count=1, truncated=False),
        content_by_file_id={},
    )
    sink = _FakeSink()

    report = _service(store, source, sink).changed_since_sync(
        team_id="T123",
        channel_id="C123",
    )

    rendered = format_changed_since_sync(report)
    assert "have changed" in rendered
    assert "2026-05-09" in rendered


def test_format_dedupe_report_no_saved_files() -> None:
    """An empty manifest should produce a friendly message."""
    rendered = format_dedupe_report(
        DedupeReport(
            channel_id="C0",
            s3_bucket="b",
            saved_count=0,
            duplicate_groups=(),
            stale_files=(),
            truncated=False,
        )
    )
    assert "No saved files" in rendered


def test_format_dedupe_report_clean_manifest() -> None:
    """A manifest with no duplicates and no stale entries should say so."""
    rendered = format_dedupe_report(
        DedupeReport(
            channel_id="C0",
            s3_bucket="b",
            saved_count=2,
            duplicate_groups=(),
            stale_files=(),
            truncated=False,
        )
    )
    assert "unique" in rendered


def test_dedupe_report_groups_duplicates_and_flags_stale_entries(
    tmp_path: Path,
) -> None:
    """dedupe_report should group by content hash and detect stale rows."""
    store = _store(tmp_path / "slack.sqlite3")
    file1, file2 = _files()
    _record_saved(store, file1)
    _record_saved(store, file2)
    # Force file1 and file2 to share a content hash.
    store.record_saved_file(
        SavedSlackFileRecord(
            team_id="T123",
            channel_id="C123",
            file_id=file2.file_id,
            content_sha256="sha",
            s3_bucket="nimbus-test-bucket",
            s3_key=f"archive/slack/T123/C123/{file2.name}",
            size_bytes=file2.size_bytes,
            saved_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
    )
    # Slack only sees file1 now — file2 is stale relative to the manifest.
    source = _FakeSource(
        inventory=FileInventory(files=(file1,), total_count=1, truncated=False),
        content_by_file_id={},
    )
    sink = _FakeSink()

    report = _service(store, source, sink).dedupe_report(
        team_id="T123",
        channel_id="C123",
    )

    assert report.saved_count == 2
    assert len(report.duplicate_groups) == 1
    assert len(report.stale_files) == 1
    assert report.stale_files[0].file_id == file2.file_id
    rendered = format_dedupe_report(report)
    assert "duplicate group" in rendered
    assert "stale" in rendered


def test_dedupe_saved_files_checks_workspace_manifest_scope(tmp_path: Path) -> None:
    """Workspace dedupe should group duplicate hashes across saved channel manifests."""
    store = _store(tmp_path / "slack.sqlite3")
    file1, file2 = _files()
    _record_saved(store, file1)
    store.record_slack_files(
        (
            SlackFileRecord(
                team_id="T123",
                channel_id="C124",
                file_id=file2.file_id,
                name=file2.name,
                title=file2.title,
                mimetype=file2.mimetype,
                size_bytes=file2.size_bytes,
                url_private_download=file2.url_private_download,
                user_id=file2.user_id,
                created_ts=file2.created_ts,
                indexed_at=datetime(2026, 5, 9, tzinfo=UTC),
            ),
        )
    )
    store.record_saved_file(
        SavedSlackFileRecord(
            team_id="T123",
            channel_id="C124",
            file_id=file2.file_id,
            content_sha256="sha",
            s3_bucket="nimbus-test-bucket",
            s3_key=f"archive/slack/T123/C124/{file2.name}",
            size_bytes=file2.size_bytes,
            saved_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
    )
    source = _FakeSource(
        inventory=FileInventory(files=(), total_count=0, truncated=False),
        content_by_file_id={},
    )
    sink = _FakeSink()

    report = _service(store, source, sink).dedupe_saved_files(team_id="T123")

    assert report.saved_count == 2
    assert len(report.duplicate_groups) == 1
    assert report.stale_checked is False
    assert "not arbitrary bucket uploads" in report.scope_label
    assert "unique and still visible" not in format_dedupe_report(report)


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
            s3_key=f"archive/slack/T123/C123/{file.name}",
            size_bytes=file.size_bytes,
            saved_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
    )
