"""Tests for nimbus_slack.flow."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest
from nimbus_runtime.models import ChatTurnResult
from nimbus_slack.file_sync import (
    DedupeReport,
    FileSyncReport,
    SlackFileRef,
    SlackFileSyncError,
)
from nimbus_slack.models import NimbusTurnRequest
from nimbus_slack.oauth import NIMBUS_SLACK_PUBLIC_BASE_URL
from nimbus_slack.runtime import (
    NIMBUS_SLACK_MODEL_MODE_AUTO,
    NIMBUS_SLACK_MODEL_MODE_REMOTE,
    NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL,
    SlackTenantConfigMissingError,
    SlackTenantRuntimeError,
)
from slack_sdk.errors import SlackApiError

from nimbus_slack import flow

pytestmark = pytest.mark.unit


@dataclass
class _RecordingPoster:
    """Tiny fake Slack poster that records sent payloads."""

    calls: list[tuple[str, str, str | None]]
    updates: list[tuple[str, str, str]] = field(default_factory=list)
    next_ts: str = "1710000000.123456"

    def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        """Record a send call and return a minimal message-like object."""
        self.calls.append((channel_id, text, thread_ts))
        return {"channel": channel_id, "ts": self.next_ts, "text": text}

    def update_message(self, channel_id: str, ts: str, text: str) -> object:
        """Record a chat.update call."""
        self.updates.append((channel_id, ts, text))
        return {"channel": channel_id, "ts": ts, "text": text}


@dataclass
class _RecordingTelemetry:
    """Tiny telemetry double for reply-result label assertions."""

    turns: list[tuple[str, str]] = field(default_factory=list)
    replies: list[tuple[str, str]] = field(default_factory=list)

    def record_slack_turn(self, *, kind: str, outcome: str) -> None:
        """Record Slack turn telemetry."""
        self.turns.append((kind, outcome))

    def record_slack_reply(self, *, result: str, reason: str) -> None:
        """Record Slack reply telemetry."""
        self.replies.append((result, reason))


@dataclass
class _FailingPoster:
    """Slack poster double that raises a configured SDK error."""

    error: SlackApiError

    def send_message(
        self,
        channel_id: str,
        text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        """Raise the configured Slack SDK error."""
        raise self.error

    def update_message(self, channel_id: str, ts: str, text: str) -> object:
        """Raise the configured Slack SDK error."""
        raise self.error


@dataclass
class _FakeFileService:
    """Tiny file service double for Slack command routing tests."""

    diff_report: FileSyncReport | None = None
    save_report: FileSyncReport | None = None
    dedupe_report_value: object | None = None
    diff_calls: list[tuple[str, str]] = field(default_factory=list)
    save_calls: list[tuple[str, str]] = field(default_factory=list)
    dedupe_calls: list[tuple[str, str]] = field(default_factory=list)
    dedupe_saved_calls: list[tuple[str, tuple[str, ...] | None]] = field(
        default_factory=list
    )

    def diff_channel(self, *, team_id: str, channel_id: str) -> FileSyncReport:
        """Record a diff call and return the configured report."""
        self.diff_calls.append((team_id, channel_id))
        if self.diff_report is None:
            msg = "diff_report was not configured"
            raise AssertionError(msg)
        return self.diff_report

    def dedupe_report(self, *, team_id: str, channel_id: str) -> object:
        """Record a dedupe call and return the configured report."""
        self.dedupe_calls.append((team_id, channel_id))
        if self.dedupe_report_value is None:
            msg = "dedupe_report_value was not configured"
            raise AssertionError(msg)
        return self.dedupe_report_value

    def dedupe_saved_files(
        self,
        *,
        team_id: str,
        channel_ids: tuple[str, ...] | None = None,
    ) -> object:
        """Record a workspace/channel dedupe call and return the report."""
        self.dedupe_saved_calls.append((team_id, channel_ids))
        if self.dedupe_report_value is None:
            msg = "dedupe_report_value was not configured"
            raise AssertionError(msg)
        return self.dedupe_report_value

    def save_channel(
        self,
        *,
        team_id: str,
        channel_id: str,
        on_progress: object = None,
    ) -> FileSyncReport:
        """Record a save call and fire the optional progress callback once."""
        self.save_calls.append((team_id, channel_id))
        if self.save_report is None:
            msg = "save_report was not configured"
            raise AssertionError(msg)
        if callable(on_progress):
            from nimbus_slack.file_sync import SaveProgress

            on_progress(
                SaveProgress(
                    total=1,
                    saved=1,
                    skipped=0,
                    failed=0,
                    current_file=None,
                )
            )
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
    turn = _sample_turn_request()
    result = _sample_result()
    injected_poster = _RecordingPoster(calls=[])

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_REMOTE,
    )
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
            "channel_type": "im",
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
    turn = _sample_turn_request()
    result = _sample_result()
    resolved_poster = _RecordingPoster(calls=[])

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_REMOTE,
    )
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)
    monkeypatch.setattr(flow, "get_slack_poster", lambda **_: resolved_poster)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={
            "type": "message",
            "channel_type": "im",
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
    result = _sample_result()
    poster = _RecordingPoster(calls=[])

    def _forbidden_remote(_: object) -> object:
        msg = "tenant-local mode should not call remote Nimbus"
        raise AssertionError(msg)

    def _store() -> object:
        return object()

    def _run_tenant_runtime_turn(**_: object) -> ChatTurnResult:
        return result

    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL,
    )
    monkeypatch.setattr(flow, "get_slack_store", _store)
    monkeypatch.setattr(flow, "call_nimbus", _forbidden_remote)
    monkeypatch.setattr(flow, "run_tenant_runtime_turn", _run_tenant_runtime_turn)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-local",
        event={
            "type": "message",
            "channel_type": "im",
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


def test_handle_slack_event_tools_command_without_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack tool discovery should come from the adapter/runtime catalog."""
    poster = _RecordingPoster(calls=[])

    def _forbidden_model(_: object) -> object:
        msg = "tools command should not call Nimbus model path"
        raise AssertionError(msg)

    monkeypatch.setattr(flow, "call_nimbus", _forbidden_model)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-tools",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1710000000.123456",
            "user": "U999",
            "text": "<@BOT> tools",
        },
        poster=poster,
    )

    assert returned.model == "nimbus-slack"
    assert "Nimbus tools" in poster.calls[0][1]
    assert "candidate_plans" in poster.calls[0][1]


def test_should_handle_event_ignores_bot_messages() -> None:
    """Bot-originated messages should not re-enter Nimbus."""
    assert (
        flow.should_handle_event(
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
    assert (
        flow.should_handle_event({"type": "message", "subtype": "message_deleted"})
        is False
    )


def test_should_handle_event_ignores_unknown_event_type() -> None:
    """Non-message event types should be ignored."""
    assert (
        flow.should_handle_event({"type": "channel_join", "user": "U1", "text": "hi"})
        is False
    )


def test_should_handle_event_ignores_unmentioned_channel_message() -> None:
    """Broad channel-message subscriptions must not make Nimbus answer everything."""
    assert (
        flow.should_handle_event(
            {
                "type": "message",
                "channel_type": "channel",
                "channel": "C123",
                "ts": "1715000000.000200",
                "user": "U1",
                "text": "ordinary channel chatter",
            },
            team_id="T123",
        )
        is False
    )


def test_should_handle_event_accepts_followed_thread_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unmentioned replies are accepted only inside an active Nimbus thread."""

    class _Store:
        def is_thread_follow_active(self, **kwargs: object) -> bool:
            assert kwargs["team_id"] == "T123"
            assert kwargs["channel_id"] == "C123"
            assert kwargs["thread_ts"] == "1715000000.000100"
            assert kwargs["refresh_ttl_seconds"] == 1800
            return True

    monkeypatch.setattr(flow, "get_slack_store", _Store)

    assert flow.should_handle_event(
        {
            "type": "message",
            "channel_type": "channel",
            "channel": "C123",
            "thread_ts": "1715000000.000100",
            "ts": "1715000000.000200",
            "user": "U1",
            "text": "what changed?",
        },
        team_id="T123",
    )


def test_handle_slack_event_activates_thread_follow_for_mentions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A channel mention should open the thread for unmentioned follow-ups."""
    result = _sample_result()
    poster = _RecordingPoster(calls=[])
    activations: list[dict[str, object]] = []

    class _Store:
        def activate_thread_follow(self, **kwargs: object) -> object:
            activations.append(kwargs)
            return object()

    monkeypatch.setattr(flow, "get_slack_store", _Store)
    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_REMOTE,
    )
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)

    flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-thread-start",
        event={
            "type": "app_mention",
            "channel_type": "channel",
            "channel": "C123",
            "ts": "1715000000.000100",
            "user": "U1",
            "text": "<@BOT> summarize this thread",
        },
        poster=poster,
    )

    assert activations
    assert activations[0]["team_id"] == "T123"
    assert activations[0]["channel_id"] == "C123"
    assert activations[0]["thread_ts"] == "1715000000.000100"
    assert activations[0]["user_id"] == "U1"


def test_handle_slack_event_raises_for_unhandlable_event() -> None:
    """Unhandled Slack events should fail before runtime work starts."""
    with pytest.raises(ValueError, match="not a user-authored message"):
        flow.handle_slack_event(
            team_id="T1",
            event_id="E1",
            event={"type": "message", "subtype": "bot_message", "bot_id": "B1"},
        )


def test_handle_slack_event_skips_posting_when_result_text_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty result text should not trigger a poster call."""
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
    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_REMOTE,
    )
    monkeypatch.setattr(flow, "call_nimbus", lambda _: empty_result)

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={
            "type": "message",
            "channel_type": "im",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "hi",
        },
        poster=poster,
    )

    assert result is empty_result
    assert poster.calls == []


def test_handle_slack_event_tenant_error_returns_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant runtime errors should produce a user-visible error message."""
    poster = _RecordingPoster(calls=[])

    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL,
    )

    store = object()
    monkeypatch.setattr(flow, "get_slack_store", lambda: store)

    def _raise_tenant_runtime_error(**_: object) -> ChatTurnResult:
        msg = "bad"
        raise SlackTenantRuntimeError(msg)

    monkeypatch.setattr(
        flow,
        "run_tenant_runtime_turn",
        _raise_tenant_runtime_error,
    )

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={
            "type": "message",
            "channel_type": "im",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "hi",
        },
        poster=poster,
    )

    assert "tenant-local" in result.text


def test_handle_slack_event_remote_error_returns_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote Nimbus transport failures should still produce a Slack reply."""
    poster = _RecordingPoster(calls=[])

    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_REMOTE,
    )

    def _raise_remote_error(_: object) -> ChatTurnResult:
        msg = "connection refused"
        raise httpx.ConnectError(msg)

    monkeypatch.setattr(flow, "call_nimbus", _raise_remote_error)

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={
            "type": "message",
            "channel_type": "im",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "hi",
        },
        poster=poster,
    )

    assert "could not reach" in result.text
    assert poster.calls == [("C999", result.text, "1.0")]


def test_handle_slack_event_auto_mode_prefers_tenant_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto mode should use workspace BYOK runtime when tenant setup exists."""
    result = _sample_result()
    poster = _RecordingPoster(calls=[])

    def _forbidden_remote(_: object) -> object:
        msg = "auto mode should not call remote Nimbus when tenant runtime works"
        raise AssertionError(msg)

    def _run_tenant_runtime_turn(**_: object) -> ChatTurnResult:
        return result

    monkeypatch.setattr(flow, "slack_model_mode", lambda: NIMBUS_SLACK_MODEL_MODE_AUTO)
    monkeypatch.setattr(flow, "get_slack_store", object)
    monkeypatch.setattr(flow, "call_nimbus", _forbidden_remote)
    monkeypatch.setattr(flow, "run_tenant_runtime_turn", _run_tenant_runtime_turn)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-auto",
        event={
            "type": "message",
            "channel_type": "im",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "hi",
        },
        poster=poster,
    )

    assert returned is result
    assert poster.calls == [("C999", "Hi from Nimbus", "1.0")]


def test_handle_slack_event_auto_mode_falls_back_to_remote_without_byok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Auto mode should fall back to remote runtime before setup is complete."""
    result = _sample_result()
    poster = _RecordingPoster(calls=[])

    def _missing_tenant_config(**_: object) -> ChatTurnResult:
        msg = "missing setup"
        raise SlackTenantConfigMissingError(msg)

    monkeypatch.setattr(flow, "slack_model_mode", lambda: NIMBUS_SLACK_MODEL_MODE_AUTO)
    monkeypatch.setattr(flow, "get_slack_store", object)
    monkeypatch.setattr(flow, "run_tenant_runtime_turn", _missing_tenant_config)
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-auto-remote",
        event={
            "type": "message",
            "channel_type": "im",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "hi",
        },
        poster=poster,
    )

    assert returned is result
    assert poster.calls == [("C999", "Hi from Nimbus", "1.0")]


def test_handle_slack_event_slack_post_error_is_operator_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack reply-post failures should include the Slack error in logs/errors."""
    turn = _sample_turn_request()
    result = _sample_result()
    response = {"error": "not_in_channel"}
    poster = _FailingPoster(error=SlackApiError("failed", response=response))

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)

    with pytest.raises(RuntimeError, match="not_in_channel"):
        flow.handle_slack_event(
            team_id="T123",
            event_id="Ev1",
            event={
                "type": "message",
                "channel_type": "im",
                "channel": "C999",
                "ts": "1.0",
                "user": "U1",
                "text": "hi",
            },
            poster=poster,
        )


def test_handle_slack_event_setup_command_returns_setup_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SETUP command should return browser-based setup instructions."""
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


def test_handle_slack_event_setup_command_posts_block_kit_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When NIMBUS_SLACK_PUBLIC_BASE_URL is set, SETUP should post a
    Block Kit card with the install URL — not a plain-text reply.
    """
    monkeypatch.setenv(NIMBUS_SLACK_PUBLIC_BASE_URL, "https://nimbus.test")

    blocks_sent: list[tuple[str, list[dict[str, object]], str]] = []

    @dataclass
    class _BlockPoster:
        calls: list[tuple[str, str, str | None]] = field(default_factory=list)

        def send_message(
            self,
            channel_id: str,
            text: str,
            *,
            thread_ts: str | None = None,
        ) -> object:
            self.calls.append((channel_id, text, thread_ts))
            return {"channel": channel_id, "ts": "1.0", "text": text}

        def send_blocks(
            self,
            channel_id: str,
            blocks: list[dict[str, object]],
            fallback_text: str,
            *,
            thread_ts: str | None = None,
        ) -> object:
            del thread_ts
            blocks_sent.append((channel_id, blocks, fallback_text))
            return {"channel": channel_id, "ts": "1.0"}

    poster = _BlockPoster()
    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-setup-blocks",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "setup",
        },
        poster=poster,
    )

    # Result.text is empty because the blocks path posted directly.
    assert result.text == ""
    # Exactly one Block Kit payload was sent.
    assert len(blocks_sent) == 1
    channel_id, blocks, fallback = blocks_sent[0]
    assert channel_id == "C999"
    # The fallback text mentions the install URL (proves card was built).
    assert "browser" in fallback.lower() or "Open Setup" in fallback
    # An action block with an `Open Setup` button linking to install URL exists.
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert action_blocks
    urls = [
        el.get("url")
        for el in action_blocks[0].get("elements", [])
        if isinstance(el, dict)
    ]
    assert "https://nimbus.test/slack/install" in urls


def test_save_command_streams_progress_via_chat_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The streaming save flow should post a placeholder and edit it twice."""
    monkeypatch.setattr(flow, "_PROGRESS_MIN_INTERVAL_SECONDS", 0.0)
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
    poster = _RecordingPoster(calls=[], next_ts="1710000000.000001")

    flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-stream",
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

    assert len(poster.calls) == 1
    assert "Saving Slack files" in poster.calls[0][1]
    # one progress edit + one final-report edit at minimum
    assert len(poster.updates) >= 2
    # Final update carries the save report (Block Kit fallback text)
    assert (
        "complete" in poster.updates[-1][2].lower()
        or "scanned" in poster.updates[-1][2].lower()
    )


def test_save_command_falls_back_when_placeholder_post_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed initial post should still drive the save and produce a final reply."""
    monkeypatch.setattr(flow, "_PROGRESS_MIN_INTERVAL_SECONDS", 0.0)
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

    @dataclass
    class _PlaceholderFailingPoster:
        sends: list[tuple[str, str, str | None]] = field(default_factory=list)
        attempts: int = 0

        def send_message(
            self,
            channel_id: str,
            text: str,
            *,
            thread_ts: str | None = None,
        ) -> object:
            self.attempts += 1
            if self.attempts == 1:
                err = SlackApiError("network down", response={"error": "network_error"})
                raise err
            self.sends.append((channel_id, text, thread_ts))
            return {"channel": channel_id, "ts": "later", "text": text}

        def update_message(self, channel_id: str, ts: str, text: str) -> object:
            return {"channel": channel_id, "ts": ts, "text": text}

    poster = _PlaceholderFailingPoster()
    flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-fallback",
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

    assert service.save_calls == [("T123", "C999")]
    # Final reply still posted via the standard non-streaming path (Block Kit fallback text).
    assert any(
        "complete" in send[1].lower() or "scanned" in send[1].lower()
        for send in poster.sends
    )


def test_extract_ts_handles_non_dict_response() -> None:
    """_extract_ts should fall back to None on unexpected response shapes."""
    assert flow._extract_ts(None) is None
    assert flow._extract_ts({"ts": 42}) is None
    assert flow._extract_ts({"ts": "1.0"}) == "1.0"


def test_format_progress_includes_current_filename() -> None:
    """_format_progress should mention the most recent file when present."""
    from nimbus_slack.file_sync import SaveProgress

    file = SlackFileRef(
        file_id="F1",
        name="design.pdf",
        title=None,
        mimetype=None,
        size_bytes=10,
        url_private_download=None,
        user_id=None,
        created_ts=1,
    )
    text = flow._format_progress(
        SaveProgress(total=2, saved=1, skipped=0, failed=0, current_file=file)
    )
    assert "design.pdf" in text


def test_save_command_streaming_handles_sync_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SlackFileSyncError during save should produce a final error edit."""
    monkeypatch.setattr(flow, "_PROGRESS_MIN_INTERVAL_SECONDS", 0.0)
    telemetry = _RecordingTelemetry()
    monkeypatch.setattr(flow, "runtime_telemetry", telemetry)

    @dataclass
    class _ErroringFileService:
        def save_channel(
            self,
            *,
            team_id: str,
            channel_id: str,
            on_progress: object = None,
        ) -> FileSyncReport:
            msg = "manifest unavailable"
            raise SlackFileSyncError(msg)

    poster = _RecordingPoster(calls=[])
    flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-err",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "<@BOT> save my files in this channel to s3",
        },
        poster=poster,
        file_service=_ErroringFileService(),  # type: ignore[arg-type]
    )

    assert len(poster.calls) == 1
    # Error update uses "Save failed:" fallback text from the failure card
    assert any("Save failed" in update[2] for update in poster.updates)
    assert ("failure", "save_failed") in telemetry.replies
    assert ("success", "streaming") not in telemetry.replies


def test_handle_slack_event_save_command() -> None:
    """SAVE command should call save_channel and return a report."""
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


def test_save_command_with_channel_mentions_saves_each_channel() -> None:
    """Mentioned channels should produce one multi-channel save summary."""
    service = _FakeFileService(
        save_report=FileSyncReport(
            channel_id="ignored",
            s3_bucket="b",
            s3_prefix="p",
            scanned_count=1,
            total_count=1,
            truncated=False,
            missing_files=(),
            saved_keys=("p/file.txt",),
            skipped_files=(),
            failures=(),
        )
    )
    poster = _RecordingPoster(calls=[])

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-multi-save",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "<@BOT> save files from <#C1|legal> and <#C2|design>",
        },
        poster=poster,
        file_service=service,
    )

    assert result.model == "nimbus-slack"
    assert service.save_calls == [("T123", "C1"), ("T123", "C2")]
    assert "Across 2 channels" in poster.calls[0][1]


def test_handle_slack_event_file_sync_error_returns_error_text() -> None:
    """File sync errors should be presented as a friendly error message."""

    class _FailingService:
        def diff_channel(self, *, team_id: str, channel_id: str) -> object:
            msg = "s3 unreachable"
            raise SlackFileSyncError(msg)

        def save_channel(self, *, team_id: str, channel_id: str) -> object:
            msg = "s3 unreachable"
            raise SlackFileSyncError(msg)

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

    # Error is now posted as Block Kit (fallback text via send_message for test doubles)
    assert result.text == "" or "file operation" in result.text.lower()
    assert any("file operation" in call[1].lower() for call in poster.calls)


# ── Feature 7: Interactive (button click) dispatch ────────────────────────


def _interactive_payload(*, action_id: str) -> dict[str, object]:
    """Build a minimal Slack block_actions payload for tests."""
    return {
        "type": "block_actions",
        "team": {"id": "T123"},
        "user": {"id": "U999"},
        "channel": {"id": "C42"},
        "container": {"type": "message", "message_ts": "1715000000.000100"},
        "actions": [
            {
                "action_id": action_id,
                "block_id": "b1",
                "action_ts": "1715000001.000200",
                "value": "x",
                "type": "button",
            }
        ],
    }


def test_handle_slack_interaction_routes_dedupe_button_through_command_path() -> None:
    """Clicking the `[Find duplicates]` button should call dedupe_report
    on the file service the same way a text mention would.
    """
    fake_service = _FakeFileService(
        dedupe_report_value=DedupeReport(
            channel_id="C42",
            s3_bucket="b",
            saved_count=1,
            duplicate_groups=(),
            stale_files=(),
            truncated=False,
        ),
    )
    poster = _RecordingPoster(calls=[])

    result = flow.handle_slack_interaction(
        team_id="T123",
        payload=_interactive_payload(action_id="cmd:dedupe_report"),
        poster=poster,
        file_service=fake_service,
    )

    assert result is not None
    assert fake_service.dedupe_calls == [("T123", "C42")]


def test_dedupe_bucket_prompt_uses_workspace_saved_manifest() -> None:
    """Bucket wording should use workspace-wide saved manifest dedupe, not current channel."""
    fake_service = _FakeFileService(
        dedupe_report_value=DedupeReport(
            channel_id="workspace",
            s3_bucket="b",
            saved_count=2,
            duplicate_groups=(),
            stale_files=(),
            truncated=False,
            scope_label="all Nimbus-saved Slack manifests in this workspace",
            stale_checked=False,
        ),
    )
    poster = _RecordingPoster(calls=[])

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-dedupe-workspace",
        event={
            "type": "app_mention",
            "channel": "C42",
            "ts": "1715000000.000100",
            "user": "U1",
            "text": "<@BOT> find duplicate files in my bucket",
        },
        poster=poster,
        file_service=fake_service,
    )

    assert result.model == "nimbus-slack"
    assert fake_service.dedupe_calls == []
    assert fake_service.dedupe_saved_calls == [("T123", None)]
    assert "all Nimbus-saved Slack manifests" in poster.calls[0][1]


def test_dedupe_mentions_use_selected_channel_manifests() -> None:
    """Mentioned channels should scope manifest dedupe to those channels."""
    fake_service = _FakeFileService(
        dedupe_report_value=DedupeReport(
            channel_id="C1,C2",
            s3_bucket="b",
            saved_count=2,
            duplicate_groups=(),
            stale_files=(),
            truncated=False,
            scope_label="2 selected Slack channels' saved manifests",
            stale_checked=False,
        ),
    )
    poster = _RecordingPoster(calls=[])

    flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-dedupe-selected",
        event={
            "type": "app_mention",
            "channel": "C42",
            "ts": "1715000000.000100",
            "user": "U1",
            "text": "<@BOT> find duplicate files in <#C1|legal> and <#C2|design>",
        },
        poster=poster,
        file_service=fake_service,
    )

    assert fake_service.dedupe_saved_calls == [("T123", ("C1", "C2"))]


def test_handle_slack_interaction_save_button_triggers_save_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `[Save all to S3]` button (cmd:save_channel_files) maps to the
    save channel command. Uses the same _FakeFileService double.
    """
    monkeypatch.setattr(flow, "_PROGRESS_MIN_INTERVAL_SECONDS", 0.0)
    save_report = FileSyncReport(
        channel_id="C42",
        s3_bucket="b",
        s3_prefix="p",
        scanned_count=0,
        total_count=0,
        truncated=False,
        missing_files=(),
        saved_keys=(),
        skipped_files=(),
        failures=(),
    )
    fake_service = _FakeFileService(save_report=save_report)
    poster = _RecordingPoster(calls=[])

    result = flow.handle_slack_interaction(
        team_id="T123",
        payload=_interactive_payload(action_id="cmd:save_channel_files"),
        poster=poster,
        file_service=fake_service,
    )

    assert result is not None
    assert fake_service.save_calls == [("T123", "C42")]


def test_handle_slack_interaction_open_setup_is_noop() -> None:
    """Link-style buttons should return None without invoking handlers."""
    fake_service = _FakeFileService()
    poster = _RecordingPoster(calls=[])

    result = flow.handle_slack_interaction(
        team_id="T123",
        payload=_interactive_payload(action_id="open_setup"),
        poster=poster,
        file_service=fake_service,
    )

    assert result is None
    assert fake_service.dedupe_calls == []
    assert fake_service.save_calls == []
    assert poster.calls == []


def test_handle_slack_interaction_unknown_action_is_noop() -> None:
    """Unknown action_ids should log and return None without crashing."""
    result = flow.handle_slack_interaction(
        team_id="T123",
        payload=_interactive_payload(action_id="something:weird"),
    )
    assert result is None


def test_handle_slack_interaction_payload_without_channel_returns_none() -> None:
    """A malformed payload without channel.id should not crash."""
    payload: dict[str, object] = {
        "type": "block_actions",
        "team": {"id": "T123"},
        "user": {"id": "U999"},
        # no channel
        "actions": [
            {
                "action_id": "cmd:dedupe_report",
                "action_ts": "1715000001.000200",
                "type": "button",
            }
        ],
    }
    result = flow.handle_slack_interaction(team_id="T123", payload=payload)
    assert result is None


# ── Approval handler tests ──────────────────────────────────────────────────


@dataclass
class _RecordingPosterWithBlocks(_RecordingPoster):
    """Extends _RecordingPoster with update_blocks support."""

    block_updates: list[tuple[str, str, list[dict[str, object]]]] = field(
        default_factory=list
    )

    def update_blocks(
        self,
        channel_id: str,
        ts: str,
        blocks: list[dict[str, object]],
        fallback_text: str,
    ) -> object:
        """Record a block update call."""
        self.block_updates.append((channel_id, ts, blocks))
        return {"channel": channel_id, "ts": ts}


@dataclass
class _FakeApproval:
    """Minimal Approval-like object for testing."""

    approval_id: str = "appr-1"
    action_id: str | None = "action-xyz"
    exact_target: str = "channel/general"
    risk_level: str = "small_write"
    # session_id required by _handle_approve_button duck-typing path
    session_id: str = "1715000000.000000"


@dataclass
class _FakeDecisionResult:
    """Minimal ApprovalDecisionResult-like object for testing."""

    accepted: bool = True
    reason: str = "approved"
    approval: object = None


@dataclass
class _FakeApprovalStore:
    """Fake ApprovalStore for approval handler tests."""

    pending: object | None = None
    decision: object = field(default_factory=_FakeDecisionResult)
    find_calls: list[str] = field(default_factory=list)
    decide_calls: list[dict[str, object]] = field(default_factory=list)

    def find_pending_for_action(
        self, *, tenant: object, action_id: str
    ) -> object | None:
        """Return configured pending approval."""
        self.find_calls.append(action_id)
        return self.pending

    def decide(self, **kwargs: object) -> object:
        """Record decide call and return configured result."""
        self.decide_calls.append(kwargs)
        return self.decision


def _approval_payload(*, action_id: str) -> dict[str, object]:
    """Build a minimal Slack block_actions payload for approval tests."""
    return {
        "type": "block_actions",
        "team": {"id": "T123"},
        "user": {"id": "U-approver"},
        "channel": {"id": "C-approval"},
        "container": {"type": "message", "message_ts": "1715000000.000100"},
        "actions": [
            {
                "action_id": action_id,
                "block_id": "b-approval",
                "action_ts": "1715000001.000200",
                "value": "action-xyz",
                "type": "button",
            }
        ],
    }


def test_approval_button_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clicking an approve button should return None (handled in-place)."""
    monkeypatch.setattr(
        flow,
        "_handle_approval_interaction",
        lambda **_: None,
    )

    result = flow.handle_slack_interaction(
        team_id="T123",
        payload=_approval_payload(action_id="approve:action-xyz"),
    )
    assert result is None


def test_approval_handler_approve_routes_through_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Approve click routes through run_tenant_runtime_turn, not approve_store.decide."""
    from nimbus_runtime.models import ChatTurnResult

    approval = _FakeApproval()
    store = _FakeApprovalStore(pending=approval)

    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )

    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    runtime_calls: list[object] = []

    def _fake_runtime_turn(**kwargs: object) -> ChatTurnResult:
        runtime_calls.append(kwargs)
        return ChatTurnResult(
            request_id="r1",
            conversation_id="c1",
            text="Deleted `channel/general`.",
            outcome="reply",
            confirmation_required=False,
            model="nimbus-runtime",
            steps=1,
            fallback_used=False,
        )

    monkeypatch.setattr(flow, "run_tenant_runtime_turn", _fake_runtime_turn)

    poster = _RecordingPosterWithBlocks(calls=[])
    monkeypatch.setattr(flow, "get_slack_poster", lambda **_: poster)
    monkeypatch.setattr(flow, "get_slack_store", object)

    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    # The approval store is queried but decide() is NOT called directly —
    # that happens inside run_tenant_runtime_turn atomically.
    assert store.find_calls == ["action-xyz"]
    assert store.decide_calls == []
    # The runtime turn was invoked.
    assert len(runtime_calls) == 1


def test_approval_handler_records_reject_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Reject click should call decide(REJECT)."""
    from nimbus_runtime.domain import ApprovalChoice

    approval = _FakeApproval()
    store = _FakeApprovalStore(pending=approval)

    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )

    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    poster = _RecordingPosterWithBlocks(calls=[])

    flow._handle_approval_interaction(
        team_id="T123",
        action_id="reject:action-xyz",
        payload=_approval_payload(action_id="reject:action-xyz"),
        poster=poster,
    )

    assert store.decide_calls[0]["choice"] is ApprovalChoice.REJECT


def test_approval_handler_warns_when_reject_cannot_expire_action(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Reject must not render a clean rejection when action state diverges."""
    approval = _FakeApproval()
    store = _FakeApprovalStore(pending=approval)

    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )

    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    class _RacingActionStore:
        def transition(self, **_: object) -> None:
            msg = "action already executing"
            raise RuntimeError(msg)

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileActionStore",
        lambda _root: _RacingActionStore(),
    )

    poster = _RecordingPosterWithBlocks(calls=[])

    flow._handle_approval_interaction(
        team_id="T123",
        action_id="reject:action-xyz",
        payload=_approval_payload(action_id="reject:action-xyz"),
        poster=poster,
    )

    assert len(store.decide_calls) == 1
    assert len(poster.block_updates) == 1
    _channel, _ts, blocks = poster.block_updates[0]
    rendered = str(blocks)
    assert "Rejected, but action may still be running" in rendered
    assert "check artifacts" in rendered
    assert not any(header == "Rejected" for header in _header_texts(blocks))


def test_approval_handler_updates_message_after_decision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """After a successful approve, the original Slack message should be updated
    with an 'Approved' card (not an error card).
    """
    from nimbus_runtime.models import ChatTurnResult

    approval = _FakeApproval()
    store = _FakeApprovalStore(pending=approval)

    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )

    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    def _fake_runtime_turn(**_: object) -> ChatTurnResult:
        from nimbus_protocol.models import ActionSummary

        return ChatTurnResult(
            request_id="r1",
            conversation_id="c1",
            text="Done.",
            outcome="reply",
            confirmation_required=False,
            model="nimbus-runtime",
            steps=1,
            fallback_used=False,
            actions=(
                ActionSummary(
                    action_id="action-xyz",
                    kind="delete_file",
                    status="succeeded",
                ),
            ),
        )

    monkeypatch.setattr(flow, "run_tenant_runtime_turn", _fake_runtime_turn)
    monkeypatch.setattr(flow, "get_slack_store", object)

    poster = _RecordingPosterWithBlocks(calls=[])

    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    assert len(poster.block_updates) == 1
    channel, ts, blocks = poster.block_updates[0]
    assert channel == "C-approval"
    assert ts == "1715000000.000100"
    assert any("Approved" in str(b) for b in blocks)


def test_approval_handler_local_renders_error_card_when_no_action_executed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """In tenant-local mode, an Approve click whose runtime turn returns
    no ``succeeded`` action must NOT flip the card to Approved.

    This guards the same honesty gap as the remote path: the tenant runtime
    might respond 200 OK with ``outcome=reply`` but no executed action
    (e.g. the pending action expired before the click was processed). The
    card must surface the failure, not declare a fake success.
    """
    from nimbus_runtime.models import ChatTurnResult

    approval = _FakeApproval()
    store = _FakeApprovalStore(pending=approval)

    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )

    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    def _fake_runtime_turn(**_: object) -> ChatTurnResult:
        # outcome=reply with no succeeded action == the synthetic ``yes``
        # reached the runtime but no pending action matched it.
        return ChatTurnResult(
            request_id="r1",
            conversation_id="c1",
            text="There is no pending destructive action to confirm.",
            outcome="reply",
            confirmation_required=False,
            model="nimbus-runtime",
            steps=1,
            fallback_used=False,
        )

    monkeypatch.setattr(flow, "run_tenant_runtime_turn", _fake_runtime_turn)
    monkeypatch.setattr(flow, "get_slack_store", object)

    poster = _RecordingPosterWithBlocks(calls=[])
    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    assert len(poster.block_updates) == 1
    _channel, _ts, blocks_sent = poster.block_updates[0]
    headers = _header_texts(blocks_sent)
    assert any("failed" in h.lower() for h in headers), headers
    assert not any("Approved" in h for h in headers), headers


def test_approval_handler_does_not_update_message_when_decision_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """A reject decision with accepted=False (e.g. wrong actor) must leave the card unchanged.

    The REJECT path calls approval_store.decide() directly.  When the store
    returns accepted=False the handler returns early without touching the
    original Slack message.
    """
    approval = _FakeApproval()
    store = _FakeApprovalStore(
        pending=approval,
        decision=_FakeDecisionResult(accepted=False, reason="wrong_actor"),
    )

    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )

    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    poster = _RecordingPosterWithBlocks(calls=[])

    # Use reject: so decide() is called on the store (not routed through runtime)
    flow._handle_approval_interaction(
        team_id="T123",
        action_id="reject:action-xyz",
        payload=_approval_payload(action_id="reject:action-xyz"),
        poster=poster,
    )

    # The store's decide() was called and returned accepted=False
    assert len(store.decide_calls) == 1
    assert store.decide_calls[0]["choice"].__class__.__name__ == "ApprovalChoice"
    # Card must NOT be updated when the decision was rejected (wrong actor etc.)
    assert poster.block_updates == []


def test_approval_handler_noop_when_approval_not_found(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """In tenant-local mode, a missing approval is a silent no-op.

    The remote fallback path only fires when the model mode allows remote
    routing; here we pin tenant-local so the handler exits cleanly when
    ``find_pending_for_action`` returns ``None``.
    """
    monkeypatch.setenv("NIMBUS_SLACK_MODEL_MODE", "tenant-local")
    store = _FakeApprovalStore(pending=None)  # not found

    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )

    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    poster = _RecordingPosterWithBlocks(calls=[])

    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    assert store.decide_calls == []
    assert poster.block_updates == []


def test_approval_handler_noop_when_store_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _session_dir raises, handler should return without crashing."""
    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: (_ for _ in ()).throw(RuntimeError("no store")),
    )

    poster = _RecordingPosterWithBlocks(calls=[])

    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    assert poster.block_updates == []


def _header_texts(blocks: list[dict[str, object]]) -> list[str]:
    """Return every Slack ``header`` block's plain-text content."""
    out: list[str] = []
    for block in blocks:
        if block.get("type") != "header":
            continue
        text_field = block.get("text")
        if isinstance(text_field, dict):
            value = text_field.get("text")
            if isinstance(value, str):
                out.append(value)
    return out


def _remote_approve_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Shared monkeypatch setup for the remote-mode approval tests."""
    monkeypatch.setenv("NIMBUS_SLACK_MODEL_MODE", "remote")
    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )
    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: _FakeApprovalStore(pending=None),
    )


def _remote_result(
    *,
    outcome: str = "reply",
    text: str = "Deleted the target.",
    action_status: str | None = "succeeded",
) -> ChatTurnResult:
    """Build a ChatTurnResult shaped like what the remote runtime returns."""
    from nimbus_protocol.models import ActionSummary

    actions: tuple[ActionSummary, ...] = ()
    if action_status is not None:
        actions = (
            ActionSummary(
                action_id="action-xyz",
                kind="delete_file",
                status=action_status,
                target={
                    "provider": "s3",
                    "container": "bucket",
                    "object_name": "key",
                    "version_id": None,
                },
            ),
        )
    return ChatTurnResult(
        request_id="r",
        conversation_id="c",
        text=text,
        outcome=outcome,  # type: ignore[arg-type]
        confirmation_required=False,
        suggested_next_actions=(),
        model="m",
        steps=1,
        fallback_used=False,
        confirmation=None,
        actions=actions,
        artifacts=(),
    )


def test_approval_handler_approve_in_remote_mode_calls_remote_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Approve in remote mode POSTs a synthetic ``yes`` to the remote runtime
    and flips the card to ``Approved`` when the runtime confirms the action
    actually executed.
    """
    _remote_approve_setup(monkeypatch, tmp_path)

    captured_turns: list[object] = []

    def _fake_call_nimbus(turn: object) -> ChatTurnResult:
        captured_turns.append(turn)
        return _remote_result()

    monkeypatch.setattr("nimbus_slack.flow.call_nimbus", _fake_call_nimbus)

    poster = _RecordingPosterWithBlocks(calls=[])
    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    # The remote runtime was called with a "yes" turn keyed on the original
    # session id (recovered from container.thread_ts).
    assert len(captured_turns) == 1
    sent_turn = captured_turns[0]
    assert sent_turn.text == "yes"
    assert sent_turn.workspace_id == "T123"
    assert sent_turn.thread_id  # session_id is set
    # The card was flipped to the Approved state, not the error card.
    assert len(poster.block_updates) == 1
    _channel, _ts, blocks_sent = poster.block_updates[0]
    headers = _header_texts(blocks_sent)
    assert any("Approved" in h for h in headers), headers
    assert not any("failed" in h.lower() for h in headers), headers
    # Runtime reply text was also posted as a thread follow-up.
    assert any(
        text == "Deleted the target." and thread_ts is not None
        for _channel_id, text, thread_ts in poster.calls
    )


def test_approval_handler_approve_renders_error_card_when_runtime_returns_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """If the runtime returns ``outcome=error`` the card must NOT flip to
    Approved — render an error card carrying the runtime's reply text so the
    user knows the action did not execute.
    """
    _remote_approve_setup(monkeypatch, tmp_path)

    def _fake_call_nimbus(_turn: object) -> ChatTurnResult:
        return _remote_result(
            outcome="error",
            text="There is no pending destructive action to confirm.",
            action_status=None,
        )

    monkeypatch.setattr("nimbus_slack.flow.call_nimbus", _fake_call_nimbus)

    poster = _RecordingPosterWithBlocks(calls=[])
    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    assert len(poster.block_updates) == 1
    _channel, _ts, blocks_sent = poster.block_updates[0]
    headers = _header_texts(blocks_sent)
    assert any("failed" in h.lower() for h in headers), headers
    rendered = str(blocks_sent)
    assert "no pending destructive action" in rendered.lower()
    # The runtime's reply text is surfaced as the error detail; no
    # "Approved" follow-up should reach the channel.
    assert poster.calls == []


def test_approval_handler_approve_renders_error_card_when_no_action_executed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """If the runtime returns ``outcome=reply`` but no action transitioned to
    ``succeeded``, the synthetic ``yes`` did not actually confirm anything.
    The card must NOT flip to Approved.
    """
    _remote_approve_setup(monkeypatch, tmp_path)

    def _fake_call_nimbus(_turn: object) -> ChatTurnResult:
        # outcome="reply" with an action stuck in awaiting_confirmation
        # is the shape returned when the runtime didn't recognise the
        # ``yes`` as confirming a pending action.
        return _remote_result(
            outcome="reply",
            text="I'm not sure what 'yes' refers to here.",
            action_status="awaiting_confirmation",
        )

    monkeypatch.setattr("nimbus_slack.flow.call_nimbus", _fake_call_nimbus)

    poster = _RecordingPosterWithBlocks(calls=[])
    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    assert len(poster.block_updates) == 1
    _channel, _ts, blocks_sent = poster.block_updates[0]
    headers = _header_texts(blocks_sent)
    assert any("failed" in h.lower() for h in headers), headers
    assert poster.calls == []


def test_approval_handler_reject_in_remote_mode_updates_card_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Reject in remote mode does not call the remote runtime (no API yet);
    it updates the card with cancel-via-text guidance.
    """
    monkeypatch.setenv("NIMBUS_SLACK_MODEL_MODE", "remote")
    store = _FakeApprovalStore(pending=None)
    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )
    import nimbus_runtime.stores

    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    def _explode(_turn: object) -> object:
        msg = "call_nimbus should not be invoked on reject"
        raise AssertionError(msg)

    monkeypatch.setattr("nimbus_slack.flow.call_nimbus", _explode)

    poster = _RecordingPosterWithBlocks(calls=[])
    flow._handle_approval_interaction(
        team_id="T123",
        action_id="reject:action-xyz",
        payload=_approval_payload(action_id="reject:action-xyz"),
        poster=poster,
    )

    assert len(poster.block_updates) == 1
    # The card includes the cancel-via-text guidance.
    rendered = poster.block_updates[0][2]
    assert any(
        "expire automatically" in str(block) or "Type `no`" in str(block)
        for block in rendered
    )


def test_approval_decided_blocks_approve_has_ok_header() -> None:
    """Approved decision blocks should have a success header."""
    from nimbus_runtime.domain import ApprovalChoice

    approval = _FakeApproval()
    blocks = flow._approval_decided_blocks(
        approval=approval,
        choice=ApprovalChoice.APPROVE,
        user_id="U-approver",
        accepted=True,
        reason="approved",
    )
    assert blocks[0]["type"] == "header"
    header_text = blocks[0]["text"]["text"]
    assert "Approved" in header_text


def test_approval_decided_blocks_reject_has_error_header() -> None:
    """Rejected decision blocks should have an error header."""
    from nimbus_runtime.domain import ApprovalChoice

    approval = _FakeApproval()
    blocks = flow._approval_decided_blocks(
        approval=approval,
        choice=ApprovalChoice.REJECT,
        user_id="U-approver",
        accepted=True,
        reason="rejected",
    )
    header_text = blocks[0]["text"]["text"]
    assert "Rejected" in header_text


# ── P9: approval card decided_at timestamp and consequence message ────────────


def test_approval_decided_blocks_approve_shows_timestamp() -> None:
    """Approved card should embed a HH:MM UTC timestamp when decided_at is given."""
    from datetime import UTC, datetime

    from nimbus_runtime.domain import ApprovalChoice

    decided_at = datetime(2024, 5, 18, 14, 33, 0, tzinfo=UTC)
    blocks = flow._approval_decided_blocks(
        approval=_FakeApproval(),
        choice=ApprovalChoice.APPROVE,
        user_id="U-hari",
        accepted=True,
        reason="approved",
        decided_at=decided_at,
    )
    block_text = " ".join(str(b) for b in blocks)
    assert "14:33" in block_text
    assert "UTC" in block_text


def test_approval_decided_blocks_reject_shows_timestamp() -> None:
    """Rejected card should also embed the decision timestamp."""
    from datetime import UTC, datetime

    from nimbus_runtime.domain import ApprovalChoice

    decided_at = datetime(2024, 5, 18, 9, 7, 0, tzinfo=UTC)
    blocks = flow._approval_decided_blocks(
        approval=_FakeApproval(),
        choice=ApprovalChoice.REJECT,
        user_id="U-hari",
        accepted=True,
        reason="rejected",
        decided_at=decided_at,
    )
    block_text = " ".join(str(b) for b in blocks)
    assert "09:07" in block_text
    assert "UTC" in block_text


def test_approval_decided_blocks_no_timestamp_when_decided_at_omitted() -> None:
    """When decided_at is not passed the timestamp suffix is simply absent."""
    from nimbus_runtime.domain import ApprovalChoice

    blocks = flow._approval_decided_blocks(
        approval=_FakeApproval(),
        choice=ApprovalChoice.APPROVE,
        user_id="U-hari",
        accepted=True,
        reason="approved",
        # decided_at omitted
    )
    block_text = " ".join(str(b) for b in blocks)
    # No ":XX UTC" timestamp should appear
    assert "UTC" not in block_text


def test_approval_decided_blocks_approve_includes_consequence_message() -> None:
    """Approve card must tell the user the task will apply next cycle."""
    from nimbus_runtime.domain import ApprovalChoice

    blocks = flow._approval_decided_blocks(
        approval=_FakeApproval(),
        choice=ApprovalChoice.APPROVE,
        user_id="U-hari",
        accepted=True,
        reason="approved",
    )
    block_text = " ".join(str(b) for b in blocks)
    assert "next worker cycle" in block_text


def test_approval_decided_blocks_reject_includes_consequence_message() -> None:
    """Reject card must tell the user the operation was stopped."""
    from nimbus_runtime.domain import ApprovalChoice

    blocks = flow._approval_decided_blocks(
        approval=_FakeApproval(),
        choice=ApprovalChoice.REJECT,
        user_id="U-hari",
        accepted=True,
        reason="rejected",
    )
    block_text = " ".join(str(b) for b in blocks)
    assert "operation was stopped" in block_text


def test_approval_decided_blocks_shows_user_mention() -> None:
    """The decided-by context block must include the Slack user mention."""
    from nimbus_runtime.domain import ApprovalChoice

    blocks = flow._approval_decided_blocks(
        approval=_FakeApproval(),
        choice=ApprovalChoice.APPROVE,
        user_id="U-HARI",
        accepted=True,
        reason="approved",
    )
    block_text = " ".join(str(b) for b in blocks)
    assert "<@U-HARI>" in block_text


def test_approval_handler_passes_decided_at_to_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> None:
    """_handle_approval_interaction must forward now as decided_at."""
    from datetime import datetime

    import nimbus_runtime.stores
    from nimbus_runtime.models import ChatTurnResult

    approval = _FakeApproval()
    store = _FakeApprovalStore(pending=approval)

    monkeypatch.setattr(
        "nimbus_slack.runtime._session_dir",
        lambda _team_id: tmp_path,
    )
    monkeypatch.setattr(
        nimbus_runtime.stores,
        "FileApprovalStore",
        lambda _root: store,
    )

    def _fake_runtime_turn(**_: object) -> ChatTurnResult:
        from nimbus_protocol.models import ActionSummary

        return ChatTurnResult(
            request_id="r1",
            conversation_id="c1",
            text="",
            outcome="reply",
            confirmation_required=False,
            model="nimbus-runtime",
            steps=1,
            fallback_used=False,
            actions=(
                ActionSummary(
                    action_id="action-xyz",
                    kind="delete_file",
                    status="succeeded",
                ),
            ),
        )

    monkeypatch.setattr(flow, "run_tenant_runtime_turn", _fake_runtime_turn)
    monkeypatch.setattr(flow, "get_slack_store", object)

    captured_kwargs: list[dict] = []
    original = flow._approval_decided_blocks

    def _recording(*args: object, **kwargs: object) -> object:
        captured_kwargs.append(dict(kwargs))
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(flow, "_approval_decided_blocks", _recording)

    poster = _RecordingPosterWithBlocks(calls=[])
    flow._handle_approval_interaction(
        team_id="T123",
        action_id="approve:action-xyz",
        payload=_approval_payload(action_id="approve:action-xyz"),
        poster=poster,
    )

    assert len(captured_kwargs) == 1
    assert isinstance(captured_kwargs[0].get("decided_at"), datetime)
    dt = captured_kwargs[0]["decided_at"]
    assert dt.tzinfo is not None  # must be timezone-aware


# ── P2: handle_app_home_opened ───────────────────────────────────────────────


@dataclass
class _RecordingHomePublisher:
    """Minimal fake home publisher that records calls."""

    calls: list[tuple[str, list[dict]]] = field(default_factory=list)

    def publish_home_tab(
        self,
        user_id: str,
        blocks: list[dict],
    ) -> object:
        """Record the call and return a stub response."""
        self.calls.append((user_id, blocks))
        return {"ok": True}


def test_handle_app_home_opened_calls_publisher() -> None:
    """handle_app_home_opened must call publisher.publish_home_tab with the user ID."""
    publisher = _RecordingHomePublisher()
    flow.handle_app_home_opened(
        team_id="T_HOME",
        user_id="U_USER1",
        publisher=publisher,
    )
    assert len(publisher.calls) == 1
    user_id_received, blocks = publisher.calls[0]
    assert user_id_received == "U_USER1"
    assert isinstance(blocks, list)
    assert len(blocks) >= 1


def test_handle_app_home_opened_blocks_are_dicts() -> None:
    """Every block returned to the publisher must be a dict."""
    publisher = _RecordingHomePublisher()
    flow.handle_app_home_opened(
        team_id="T_HOME",
        user_id="U_ANY",
        publisher=publisher,
    )
    _, blocks = publisher.calls[0]
    assert all(isinstance(b, dict) for b in blocks)


def test_handle_app_home_opened_blocks_contain_header() -> None:
    """At least one block in the home view must be a header block."""
    publisher = _RecordingHomePublisher()
    flow.handle_app_home_opened(
        team_id="T_TEST",
        user_id="U_TEST",
        publisher=publisher,
    )
    _, blocks = publisher.calls[0]
    assert any(b.get("type") == "header" for b in blocks)


def test_handle_app_home_opened_publishes_exactly_once_per_call() -> None:
    """Each call to handle_app_home_opened publishes exactly one view."""
    publisher = _RecordingHomePublisher()
    flow.handle_app_home_opened(team_id="T1", user_id="U1", publisher=publisher)
    flow.handle_app_home_opened(team_id="T1", user_id="U2", publisher=publisher)
    assert len(publisher.calls) == 2
    assert publisher.calls[0][0] == "U1"
    assert publisher.calls[1][0] == "U2"


# ── SEARCH command ────────────────────────────────────────────────────────────


def test_search_command_posts_results_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SEARCH command should call _search_result_blocks and post the blocks."""
    search_blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🔍 Search: budget"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*Budget Report*"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "1 result(s)"}]},
    ]
    monkeypatch.setattr(
        flow,
        "_search_result_blocks",
        lambda **_kw: search_blocks,
    )
    poster = _RecordingPoster(calls=[])

    result = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-search",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1.0",
            "user": "U1",
            "text": "search for the budget document",
        },
        poster=poster,
    )

    assert result.model == "nimbus-slack"
    assert poster.calls, "Expected at least one message posted"


def test_search_command_empty_results_still_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even an empty search result set should produce a posted message."""
    empty_blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🔍 Search: noop"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "No indexed documents matched your query.",
            },
        },
    ]
    monkeypatch.setattr(
        flow,
        "_search_result_blocks",
        lambda **_kw: empty_blocks,
    )
    poster = _RecordingPoster(calls=[])

    flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-search-empty",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "2.0",
            "user": "U1",
            "text": "find the indexed file noop",
        },
        poster=poster,
    )

    assert poster.calls


@dataclass
class _BlockRecordingPoster(_RecordingPoster):
    """RecordingPoster extension that also captures send_blocks payloads."""

    block_sends: list[tuple[str, list[dict[str, object]], str, str | None]] = field(
        default_factory=list
    )

    def send_blocks(
        self,
        channel_id: str,
        blocks: list[dict[str, object]],
        fallback_text: str,
        *,
        thread_ts: str | None = None,
    ) -> object:
        """Record a block send call."""
        self.block_sends.append((channel_id, blocks, fallback_text, thread_ts))
        return {"channel": channel_id, "ts": self.next_ts}


def test_handle_slack_event_profile_timing_strips_flag_and_posts_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--profile-timing` is stripped from the text and produces a trace card.

    The poster receives the main reply first and the timing-trace block card
    second in the same thread. The text seen by ``parse_slack_command`` must
    NOT contain the flag, otherwise the command parser could misclassify the
    intent.
    """
    result = _sample_result()
    poster = _BlockRecordingPoster(calls=[])
    seen_text: list[str] = []

    original_parse = flow.parse_slack_command

    def _recording_parse(text: str) -> object:
        seen_text.append(text)
        return original_parse(text)

    monkeypatch.setattr(flow, "parse_slack_command", _recording_parse)
    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_REMOTE,
    )
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-profile",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1710000000.123456",
            "user": "U999",
            "text": "<@BOT> hello world --profile-timing",
        },
        poster=poster,
    )

    assert returned is result
    # parse_slack_command saw the cleaned text — no `--profile-timing` token.
    assert seen_text == ["hello world"]
    # Main reply posted as plain text.
    assert poster.calls == [("C999", "Hi from Nimbus", "1710000000.123456")]
    # Trace card posted as a Block Kit message in the same thread.
    assert len(poster.block_sends) == 1
    trace_channel, trace_blocks, _trace_fallback, trace_thread = poster.block_sends[0]
    assert trace_channel == "C999"
    assert trace_thread == "1710000000.123456"
    header_text = trace_blocks[0]["text"]["text"]
    body_text = trace_blocks[1]["text"]["text"]
    assert "Profile timing" in header_text
    assert "slack.parse_command" in body_text
    assert "slack.post_result" in body_text


def test_handle_slack_event_without_profile_timing_posts_no_trace_card(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default behaviour is unchanged: no extra Slack message when the flag is absent."""
    result = _sample_result()
    poster = _RecordingPoster(calls=[])

    monkeypatch.setattr(
        flow,
        "slack_model_mode",
        lambda: NIMBUS_SLACK_MODEL_MODE_REMOTE,
    )
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)

    flow.handle_slack_event(
        team_id="T123",
        event_id="Ev-no-profile",
        event={
            "type": "app_mention",
            "channel": "C999",
            "ts": "1710000000.123456",
            "user": "U999",
            "text": "<@BOT> hello world",
        },
        poster=poster,
    )

    assert len(poster.calls) == 1
    assert poster.calls[0][1] == "Hi from Nimbus"


def test_handle_slack_event_profile_timing_only_text_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A message containing only the flag is rejected so we never call the model.

    Without this guard the cleaned text would be empty and the model would
    be invoked with no user content.
    """

    def _forbidden_parse(_text: str) -> object:
        msg = "parse_slack_command should not run when text is flag-only"
        raise AssertionError(msg)

    monkeypatch.setattr(flow, "parse_slack_command", _forbidden_parse)

    with pytest.raises(ValueError, match="must not be empty"):
        flow.handle_slack_event(
            team_id="T123",
            event_id="Ev-flag-only",
            event={
                "type": "app_mention",
                "channel": "C999",
                "ts": "1710000000.123456",
                "user": "U999",
                "text": "<@BOT> --profile-timing",
            },
            poster=_RecordingPoster(calls=[]),
        )
