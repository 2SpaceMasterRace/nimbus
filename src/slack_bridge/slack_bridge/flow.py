"""Orchestration helpers for Slack-event to Nimbus-turn processing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from slack_bridge.body import build_event_body
from slack_bridge.client import call_nimbus
from slack_bridge.deps import get_chat_client

if TYPE_CHECKING:
    from chat_client_api import ChatClient
    from nimbus_runtime.models import ChatTurnResult


def handle_slack_event(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, object],
    chat_client: ChatClient | None = None,
) -> ChatTurnResult:
    """Send one Slack event through Nimbus and post the result back to chat.

    Args:
        team_id: Slack team ID from the outer event-callback payload.
        event_id: Slack event ID used for idempotency tracking.
        event: Inner Slack event payload.
        chat_client: Optional injected client used for testing/call-site overrides.

    Returns:
        The parsed Nimbus chat-turn result.

    """
    turn = build_event_body(team_id=team_id, event_id=event_id, event=event)
    result = call_nimbus(turn)
    resolved_chat_client = chat_client or get_chat_client()
    resolved_chat_client.send_message(turn.channel_id, result.text)
    return result
