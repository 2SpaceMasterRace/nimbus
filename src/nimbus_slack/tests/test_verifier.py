"""Tests for scheduled Slack saved-manifest verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import cast

import pytest
from cloud_storage_api.exceptions import ObjectNotFoundError
from nimbus_slack.deps import SlackPoster
from nimbus_slack.store import SavedSlackFileRecord, TenantConfig
from nimbus_slack.verifier import (
    post_new_drift_alerts,
    run_scheduled_verifier_once,
    verify_saved_manifest_records,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 21, 18, 0, tzinfo=UTC)


def test_saved_manifest_verifier_detects_deleted_s3_object() -> None:
    """A manual S3 delete becomes a missing-object drift finding."""
    store = _FakeStore(records=(_saved_record(),))
    report = verify_saved_manifest_records(
        team_id="T123",
        store=store,
        now=_NOW,
        storage_factory=lambda _config: _FakeInspector(missing={"slack/a.txt"}),
    )

    assert report.checked_count == 1
    assert report.has_drift is True
    assert report.drifted[0].status == "missing"
    assert report.drifted[0].s3_key == "slack/a.txt"


def test_scheduled_verifier_posts_new_alert_once() -> None:
    """Repeated sweeps should not spam Slack for the same drift issue."""
    store = _FakeStore(records=(_saved_record(),))
    poster = _FakePoster()

    first = run_scheduled_verifier_once(
        team_id="T123",
        store=store,
        now=_NOW,
        storage_factory=lambda _config: _FakeInspector(missing={"slack/a.txt"}),
        poster_factory=lambda _channel: cast("SlackPoster", poster),
    )
    second = post_new_drift_alerts(
        report=first,
        store=store,
        now=_NOW,
        poster_factory=lambda _channel: cast("SlackPoster", poster),
    )

    assert first.has_drift is True
    assert len(poster.calls) == 1
    assert second == 0
    assert "Storage drift detected" in str(poster.calls[0]["fallback"])


def test_saved_manifest_verifier_detects_size_mismatch() -> None:
    """Unexpected object replacement is visible even when the key still exists."""
    store = _FakeStore(records=(_saved_record(size_bytes=12),))
    report = verify_saved_manifest_records(
        team_id="T123",
        store=store,
        now=_NOW,
        storage_factory=lambda _config: _FakeInspector(sizes={"slack/a.txt": 99}),
    )

    assert report.has_drift is True
    assert report.drifted[0].status == "size_mismatch"
    assert report.drifted[0].observed_size_bytes == 99


@dataclass
class _FakeStore:
    records: tuple[SavedSlackFileRecord, ...]
    claimed: set[str] = field(default_factory=set)

    def get_tenant_config(self, team_id: str) -> TenantConfig | None:
        assert team_id == "T123"
        return TenantConfig(
            team_id="T123",
            openrouter_api_key="sk-test",
            aws_access_key_id="AKIA_TEST",
            aws_secret_access_key="secret",  # noqa: S106 - test fixture only.
            aws_region="us-east-1",
            s3_bucket="bucket",
            s3_prefix="demo",
            status="active",
            updated_at=_NOW,
        )

    def list_saved_files_for_team(
        self,
        *,
        team_id: str,
    ) -> tuple[SavedSlackFileRecord, ...]:
        assert team_id == "T123"
        return self.records

    def claim_drift_alert(
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
        assert team_id == "T123"
        assert channel_id == "C123"
        assert status
        assert s3_bucket == "bucket"
        assert s3_key == "slack/a.txt"
        assert now == _NOW
        before = len(self.claimed)
        self.claimed.add(issue_key)
        return len(self.claimed) > before


@dataclass
class _FakeInspector:
    missing: set[str] = field(default_factory=set)
    sizes: dict[str, int] = field(default_factory=dict)

    def get_file_info(self, container: str, object_name: str) -> object:
        assert container == "bucket"
        if object_name in self.missing:
            raise ObjectNotFoundError(container, object_name)
        return _Info(size_bytes=self.sizes.get(object_name, 12), metadata={})


@dataclass(frozen=True)
class _Info:
    size_bytes: int
    metadata: dict[str, str]


@dataclass
class _FakePoster:
    calls: list[dict[str, object]] = field(default_factory=list)

    def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        self.calls.append(
            {"channel_id": channel_id, "text": text, "thread_ts": thread_ts}
        )
        return {"ok": True}

    def update_message(self, channel_id: str, ts: str, text: str) -> object:
        self.calls.append({"channel_id": channel_id, "ts": ts, "text": text})
        return {"ok": True}

    def send_blocks(
        self,
        channel_id: str,
        blocks: list[dict[str, object]],
        fallback_text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        self.calls.append(
            {
                "channel_id": channel_id,
                "blocks": blocks,
                "fallback": fallback_text,
                "thread_ts": thread_ts,
            }
        )
        return {"ok": True}

    def update_blocks(
        self,
        channel_id: str,
        ts: str,
        blocks: list[dict[str, object]],
        fallback_text: str,
    ) -> object:
        self.calls.append(
            {
                "channel_id": channel_id,
                "ts": ts,
                "blocks": blocks,
                "fallback": fallback_text,
            }
        )
        return {"ok": True}


def _saved_record(*, size_bytes: int = 12) -> SavedSlackFileRecord:
    return SavedSlackFileRecord(
        team_id="T123",
        channel_id="C123",
        file_id="F123",
        content_sha256="a" * 64,
        s3_bucket="bucket",
        s3_key="slack/a.txt",
        size_bytes=size_bytes,
        saved_at=_NOW,
    )
