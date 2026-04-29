"""Slack event to NimbusTurnRequest translation for the Nimbus Slack bridge.

Converts raw Slack event payloads into the wire format expected by
POST /ai/chat/turn on the Nimbus AI service.
"""

from __future__ import annotations

import structlog

from slack_bridge.models import NimbusTurnRequest

log = structlog.get_logger()


def _strip_mention(text: str) -> str:
    """Strip a leading Slack app-mention token from message text.

    Args:
        text: Raw message text from the Slack event.

    Returns:
        Text with the leading <@USERID> mention removed, or the original
        text unchanged if no mention is present.

    """
    if text.startswith("<@"):
        return text.split("> ", 1)[1]
    return text


def build_event_body(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, object],
) -> NimbusTurnRequest:
    """Build a NimbusTurnRequest from a Slack message event payload.

    Args:
        team_id: Slack team ID from the top-level event callback payload.
        event_id: Slack event ID used for idempotency tracking.
        event: The inner event dict from the Slack payload.

    Returns:
        A NimbusTurnRequest ready to be signed and sent to the Nimbus AI service.

    """
    message_ts = str(event["ts"])
    thread_id = str(event.get("thread_ts") or message_ts)
    channel_id = str(event["channel"])
    user_id = str(event["user"])
    text = _strip_mention(str(event.get("text", "")))

    return NimbusTurnRequest(
        platform="slack",
        workspace_id=team_id,
        channel_id=channel_id,
        thread_id=thread_id,
        message_id=message_ts,
        user_id=user_id,
        text=text,
        idempotency_key=f"slack:{team_id}:event:{event_id}",
        request_id=f"slack-{event_id}",
    )
