"""Tests for slack_bridge.flow."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from nimbus_runtime.models import ChatTurnResult
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
