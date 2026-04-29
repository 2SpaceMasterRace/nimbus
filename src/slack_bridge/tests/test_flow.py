"""Tests for slack_bridge.flow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from nimbus_runtime.models import ChatTurnResult, ConfirmationDetails
from slack_bridge.models import NimbusTurnRequest

if TYPE_CHECKING:
    from chat_client_api import ChatClient

pytestmark = pytest.mark.unit


@dataclass
class _RecordingChatClient:
    """Tiny fake ChatClient that records sent payloads."""

    calls: list[tuple[str, str]]

    def send_message(self, channel_id: str, text: str) -> object:
        """Record a send call and return a minimal message-like object."""
        self.calls.append((channel_id, text))
        return SimpleNamespace(channel=channel_id, text=text)


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
    """Injected client is used directly without resolving the dependency."""
    from slack_bridge import flow

    turn = _sample_turn_request()
    result = _sample_result()
    injected_client = _RecordingChatClient(calls=[])

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)

    def _forbidden_dependency() -> ChatClient:
        msg = "get_chat_client should not be called when a client is injected"
        raise AssertionError(msg)

    monkeypatch.setattr(flow, "get_chat_client", _forbidden_dependency)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={"channel": "ignored", "ts": "ignored", "user": "ignored"},
        chat_client=injected_client,
    )

    assert returned is result
    assert injected_client.calls == [("C999", "Hi from Nimbus")]


def test_handle_slack_event_resolves_dependency_when_not_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback dependency is used when no explicit client is passed."""
    from slack_bridge import flow

    turn = _sample_turn_request()
    result = _sample_result()
    resolved_client = _RecordingChatClient(calls=[])

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)
    monkeypatch.setattr(flow, "get_chat_client", lambda: resolved_client)

    returned = flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={"channel": "ignored", "ts": "ignored", "user": "ignored"},
    )

    assert returned is result
    assert resolved_client.calls == [("C999", "Hi from Nimbus")]


def test_handle_slack_event_posts_user_visible_error_when_nimbus_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nimbus failures are surfaced to the user and re-raised for ops."""
    from slack_bridge import flow

    turn = _sample_turn_request()
    injected_client = _RecordingChatClient(calls=[])

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)

    def _fail(_: object) -> ChatTurnResult:
        msg = "AI server unreachable"
        raise RuntimeError(msg)

    monkeypatch.setattr(flow, "call_nimbus", _fail)

    with pytest.raises(RuntimeError, match="AI server unreachable"):
        flow.handle_slack_event(
            team_id="T123",
            event_id="Ev1",
            event={"channel": "ignored", "ts": "ignored", "user": "ignored"},
            chat_client=injected_client,
        )

    assert len(injected_client.calls) == 1
    sent_channel, sent_text = injected_client.calls[0]
    assert sent_channel == "C999"
    assert "AI service" in sent_text


def test_handle_slack_event_absorbs_failure_send_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure-message send_message error must not mask the original Nimbus error."""
    from slack_bridge import flow

    turn = _sample_turn_request()

    class _FailingChatClient:
        """Chat client whose send_message always raises."""

        def send_message(self, channel_id: str, text: str) -> object:
            """Raise to simulate a downed chat-client transport."""
            del channel_id, text
            msg = "chat client down"
            raise RuntimeError(msg)

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)

    def _fail(_: object) -> ChatTurnResult:
        msg = "AI server unreachable"
        raise RuntimeError(msg)

    monkeypatch.setattr(flow, "call_nimbus", _fail)

    with pytest.raises(RuntimeError, match="AI server unreachable"):
        flow.handle_slack_event(
            team_id="T123",
            event_id="Ev1",
            event={"channel": "ignored", "ts": "ignored", "user": "ignored"},
            chat_client=_FailingChatClient(),
        )


def test_handle_slack_event_renders_confirmation_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The renderer is applied to outbound text for confirmation outcomes."""
    from slack_bridge import flow

    turn = _sample_turn_request()
    confirmation = ConfirmationDetails(
        action_id="act-1",
        kind="delete_file",
        prompt="Delete file foo.txt?",
        expected_reply="YES",
        expires_at="2030-01-01T00:00:00Z",
    )
    result = ChatTurnResult(
        request_id="slack-Ev1",
        conversation_id="conv-1",
        text="Delete file foo.txt?",
        outcome="confirmation_required",
        confirmation_required=True,
        confirmation=confirmation,
    )
    injected_client = _RecordingChatClient(calls=[])

    monkeypatch.setattr(flow, "build_event_body", lambda **_: turn)
    monkeypatch.setattr(flow, "call_nimbus", lambda _: result)

    flow.handle_slack_event(
        team_id="T123",
        event_id="Ev1",
        event={"channel": "ignored", "ts": "ignored", "user": "ignored"},
        chat_client=injected_client,
    )

    assert len(injected_client.calls) == 1
    sent_channel, sent_text = injected_client.calls[0]
    assert sent_channel == "C999"
    assert sent_text.startswith("Delete file foo.txt?")
    assert "Reply `YES` to confirm." in sent_text


def _sample_slash_form() -> dict[str, str]:
    """Build a deterministic slash-command form payload for wiring tests."""
    return {
        "team_id": "T123",
        "trigger_id": "trig-1",
        "channel_id": "C9",
        "user_id": "U7",
        "text": "list reports/",
        "command": "/nimbus",
    }


def test_handle_slack_command_dispatches_via_chat_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slash commands sign and post the rendered Nimbus reply to the channel."""
    from slack_bridge import flow

    captured: list[object] = []

    def _stub_call_nimbus(turn: object) -> ChatTurnResult:
        captured.append(turn)
        return _sample_result()

    monkeypatch.setattr(flow, "call_nimbus", _stub_call_nimbus)

    injected_client = _RecordingChatClient(calls=[])
    returned = flow.handle_slack_command(
        _sample_slash_form(),
        chat_client=injected_client,
    )

    assert returned.text == "Hi from Nimbus"
    assert len(captured) == 1
    turn = captured[0]
    assert isinstance(turn, NimbusTurnRequest)
    assert turn.thread_id is None
    assert turn.message_id == f"cmd:{hashlib.sha256(b'trig-1').hexdigest()[:48]}"
    assert turn.idempotency_key == "slack:T123:command:trig-1"
    assert injected_client.calls == [("C9", "Hi from Nimbus")]


def test_handle_slack_command_posts_user_visible_error_when_nimbus_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slash-command failures surface a fallback message and re-raise for ops."""
    from slack_bridge import flow

    def _fail(_: object) -> ChatTurnResult:
        msg = "Nimbus down"
        raise RuntimeError(msg)

    monkeypatch.setattr(flow, "call_nimbus", _fail)
    injected_client = _RecordingChatClient(calls=[])

    with pytest.raises(RuntimeError, match="Nimbus down"):
        flow.handle_slack_command(
            _sample_slash_form(),
            chat_client=injected_client,
        )

    assert len(injected_client.calls) == 1
    sent_channel, sent_text = injected_client.calls[0]
    assert sent_channel == "C9"
    assert "AI service" in sent_text
