"""Tests for nimbus_slack.flow."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from nimbus_runtime.models import ChatTurnResult
from nimbus_slack.file_sync import FileSyncReport, SlackFileRef
from nimbus_slack.models import NimbusTurnRequest

pytestmark = pytest.mark.unit


@dataclass
class _RecordingPoster:
    """Tiny fake Slack poster that records sent payloads."""

    calls: list[tuple[str, str, str | None]]

    def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        """Record a send call and return a minimal message-like object."""
        self.calls.append((channel_id, text, thread_ts))
        return SimpleNamespace(channel=channel_id, text=text)


@dataclass
class _FakeFileService:
    """Tiny file service double for Slack command routing tests."""

    diff_report: FileSyncReport | None = None
    save_report: FileSyncReport | None = None
    diff_calls: list[tuple[str, str]] = field(default_factory=list)
    save_calls: list[tuple[str, str]] = field(default_factory=list)

    def diff_channel(self, *, team_id: str, channel_id: str) -> FileSyncReport:
        """Record a diff call and return the configured report."""
        self.diff_calls.append((team_id, channel_id))
        if self.diff_report is None:
            msg = "diff_report was not configured"
            raise AssertionError(msg)
        return self.diff_report

    def save_channel(self, *, team_id: str, channel_id: str) -> FileSyncReport:
        """Record a save call and return the configured report."""
        self.save_calls.append((team_id, channel_id))
        if self.save_report is None:
            msg = "save_report was not configured"
            raise AssertionError(msg)
        return self.save_report


def _sample_turn_request() -> NimbusTurnRequest:
    """Build a deterministic turn request for wiring tests."""
    return NimbusTurnRequest(
        platform="slack",
        workspace_id="T123",
        channel_id="C999",
        message_id="1710000000.123456",
        user_id="U999",
        text="hello",
        idempotency_key="slack:T123:event:Ev1",
        thread_id="1710000000.123456",
        request_id="slack-Ev1",
    )


def _sample_result() -> ChatTurnResult:
    """Build a deterministic Nimbus result for wiring tests."""
    return ChatTurnResult(
        request_id="slack-Ev1",
        conversation_id="conv-1",
        text="Hi from Nimbus",
        outcome="reply",
        confirmation_required=False,
        model="nimbus-runtime",
        steps=1,
        fallback_used=False,
    )


def test_handle_slack_event_uses_injected_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected poster is used directly without resolving the dependency."""
    from nimbus_slack import flow

    turn = _sample_turn_request()
    result = _sample_result()
    injected_poster = _RecordingPoster(calls=[])

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)

    def _forbidden_dependency() -> object:
        msg = "get_slack_poster should not be called when a poster is injected"
        raise AssertionError(msg)

    monkeypatch.setattr(flow, "get_slack_poster", _forbidden_dependency)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={
            "type": "message",
            "channel": "ignored",
            "ts": "ignored",
            "user": "ignored",
            "text": "hello",
        },
        poster=injected_poster,
    )

    assert returned is result
    assert injected_poster.calls == [("C999", "Hi from Nimbus", "1710000000.123456")]


def test_handle_slack_event_resolves_dependency_when_not_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback dependency is used when no explicit poster is passed."""
    from nimbus_slack import flow

    turn = _sample_turn_request()
    result = _sample_result()
    resolved_poster = _RecordingPoster(calls=[])

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)
    monkeypatch.setattr(flow, "get_slack_poster", lambda **_: resolved_poster)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={
            "type": "message",
            "channel": "ignored",
            "ts": "ignored",
            "user": "ignored",
            "text": "hello",
        },
    )

    assert returned is result
    assert resolved_poster.calls == [("C999", "Hi from Nimbus", "1710000000.123456")]


def test_handle_slack_event_can_use_tenant_local_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant-local mode should bypass the remote AI server call."""
    from nimbus_slack import flow

    result = _sample_result()
    poster = _RecordingPoster(calls=[])

    def _forbidden_remote(_: object) -> object:
        msg = "tenant-local mode should not call remote Nimbus"
        raise AssertionError(msg)

    def _store() -> object:
        return object()

    def _run_tenant_runtime_turn(**_: object) -> ChatTurnResult:
        return result

    monkeypatch.setattr(flow, "tenant_local_runtime_enabled", lambda: True)
    monkeypatch.setattr(flow, "get_slack_store", _store)
    monkeypatch.setattr(flow, "call_nimbus", _forbidden_remote)
    monkeypatch.setattr(flow, "run_tenant_runtime_turn", _run_tenant_runtime_turn)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-local",
        event={
            "type": "message",
            "channel": "C999",
            "ts": "1710000000.123456",
            "user": "U999",
            "text": "hello",
        },
        poster=poster,
    )

    assert returned is result
    assert poster.calls == [("C999", "Hi from Nimbus", "1710000000.123456")]


def test_handle_slack_event_handles_file_diff_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack-owned file commands should not call the LLM path."""
    from nimbus_slack import flow

    missing = SlackFileRef(
        file_id="F2",
        name="missing.txt",
        title=None,
        mimetype="text/plain",
        size_bytes=4,
        url_private_download="https://files.slack.test/F2",
        user_id="U2",
        created_ts=2,
    )
    service = _FakeFileService(
        diff_report=FileSyncReport(
            channel_id="C999",
            s3_bucket="nimbus-test-bucket",
            s3_prefix="archive",
            scanned_count=1,
            total_count=1,
            truncated=False,
            missing_files=(missing,),
        )
    )
    poster = _RecordingPoster(calls=[])

    def _forbidden_model(_: object) -> object:
        msg = "file diff command should not call Nimbus model path"
        raise AssertionError(msg)

    monkeypatch.setattr(flow, "call_nimbus", _forbidden_model)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-file-diff",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1710000000.123456",
            "user": "U999",
            "text": "<@BOT> what files in this channel are not saved in my s3 bucket?",
        },
        poster=poster,
        file_service=service,
    )

    assert returned.model == "nimbus-slack"
    assert service.diff_calls == [("T123", "C999")]
    assert "missing.txt" in poster.calls[0][1]


def test_should_handle_event_ignores_bot_messages() -> None:
    """Bot-originated messages should not re-enter Nimbus."""
    from nimbus_slack.flow import should_handle_event

    assert (
        should_handle_event(
            {
                "type": "message",
                "subtype": "bot_message",
                "bot_id": "B123",
                "text": "hello",
            }
        )
        is False
    )


def test_should_handle_event_ignores_message_subtype_without_bot_id() -> None:
    """Subtype filter is applied even when bot_id is absent."""
    from nimbus_slack.flow import should_handle_event

    assert should_handle_event({"type": "message", "subtype": "message_deleted"}) is False


def test_should_handle_event_ignores_unknown_event_type() -> None:
    from nimbus_slack.flow import should_handle_event

    assert should_handle_event({"type": "channel_join", "user": "U1", "text": "hi"}) is False


def test_handle_slack_event_raises_for_unhandlable_event() -> None:
    from nimbus_slack.flow import handle_slack_event

    with pytest.raises(ValueError, match="not a user-authored message"):
        handle_slack_event(
            team_id="T1",
            event_id="E1",
            event={"type": "message", "subtype": "bot_message", "bot_id": "B1"},
        )


def test_handle_slack_event_skips_posting_when_result_text_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty result text should not trigger a poster call."""
    from nimbus_slack import flow

    turn = _sample_turn_request()
    empty_result = ChatTurnResult(
        request_id="r1",
        conversation_id="c1",
        text="",
        outcome="reply",
        confirmation_required=False,
        model="m",
        steps=1,
        fallback_used=False,
    )
    poster = _RecordingPoster(calls=[])
    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(flow, "call_nimbus", lambda _: empty_result)

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={"type": "message", "channel": "C999", "ts": "1.0", "user": "U1", "text": "hi"},
        poster=poster,
    )

    assert result is empty_result
    assert poster.calls == []


def test_handle_slack_event_tenant_error_returns_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant runtime errors should produce a user-visible error message."""
    from nimbus_slack import flow
    from nimbus_slack.runtime import SlackTenantRuntimeError

    turn = _sample_turn_request()
    poster = _RecordingPoster(calls=[])

    monkeypatch.setattr(flow, "tenant_local_runtime_enabled", lambda: True)
    monkeypatch.setattr(flow, "get_slack_store", lambda: object())
    monkeypatch.setattr(
        flow,
        "run_tenant_runtime_turn",
        lambda **_: (_ for _ in ()).throw(SlackTenantRuntimeError("bad")),
    )

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={"type": "message", "channel": "C999", "ts": "1.0", "user": "U1", "text": "hi"},
        poster=poster,
    )

    assert "tenant-local" in result.text


def test_handle_slack_event_setup_command_returns_setup_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SETUP command should return browser-based setup instructions."""
    from nimbus_slack import flow
    from nimbus_slack.oauth import NIMBUS_SLACK_PUBLIC_BASE_URL

    monkeypatch.delenv(NIMBUS_SLACK_PUBLIC_BASE_URL, raising=False)
    poster = _RecordingPoster(calls=[])

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-setup",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "setup",
        },
        poster=poster,
    )

    assert "browser-based" in result.text
    assert poster.calls


def test_handle_slack_event_save_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SAVE command should call save_channel and return a report."""
    from nimbus_slack import flow
    from nimbus_slack.file_sync import FileSyncReport

    report = FileSyncReport(
        channel_id="C999",
        s3_bucket="b",
        s3_prefix="p",
        scanned_count=1,
        total_count=1,
        truncated=False,
        missing_files=(),
    )
    service = _FakeFileService(save_report=report)
    poster = _RecordingPoster(calls=[])

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-save",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "<@BOT> save my files in this channel to s3",
        },
        poster=poster,
        file_service=service,
    )

    assert result.model == "nimbus-slack"
    assert service.save_calls == [("T123", "C999")]


def test_handle_slack_event_file_sync_error_returns_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """File sync errors should be presented as a friendly error message."""
    from nimbus_slack import flow
    from nimbus_slack.file_sync import SlackFileSyncError

    class _FailingService:
        def diff_channel(self, *, team_id: str, channel_id: str) -> object:
            raise SlackFileSyncError("s3 unreachable")

        def save_channel(self, *, team_id: str, channel_id: str) -> object:
            raise SlackFileSyncError("s3 unreachable")

    poster = _RecordingPoster(calls=[])
    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-err",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "<@BOT> what files in this channel are not saved in my s3 bucket?",
        },
        poster=poster,
        file_service=_FailingService(),
    )

    assert "file operation" in result.text
