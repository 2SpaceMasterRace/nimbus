"""Slack event to NimbusTurnRequest translation for the Nimbus Slack bridge.

Converts raw Slack event payloads into the wire format expected by
POST /ai/chat/turn on the Nimbus AI service.
"""

from __future__ import annotations

import structlog

from nimbus_slack.models import NimbusTurnRequest

log = structlog.get_logger()


def _strip_mention(text: str) -> str:
    """Strip a leading Slack app-mention token from message text.

    Args:
        text: Raw message text from the Slack event.

    Returns:
        Text with the leading <@USERID> mention removed, or the original
        text unchanged if no mention is present.

    """
    parts = text.split(maxsplit=1)
    if parts and parts[0].startswith("<@") and parts[0].endswith(">"):
        return parts[1] if len(parts) > 1 else ""
    return text


def _require_str(event: dict[str, object], key: str) -> str:
    """Return a required Slack event string field."""
    value = event.get(key)
    if not isinstance(value, str) or not value:
        msg = f"Slack event field {key!r} must be a non-empty string"
        raise ValueError(msg)
    return value


def _optional_str(event: dict[str, object], key: str) -> str | None:
    """Return an optional Slack event string field."""
    value = event.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"Slack event field {key!r} must be a string or null"
    raise TypeError(msg)


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
    message_ts = _require_str(event, "ts")
    thread_id = _optional_str(event, "thread_ts") or message_ts
    channel_id = _require_str(event, "channel")
    user_id = _require_str(event, "user")
    raw_text = event.get("text", "")
    if not isinstance(raw_text, str):
        msg = "Slack event field 'text' must be a string"
        raise TypeError(msg)
    text = _strip_mention(raw_text).strip()
    if not text:
        msg = "Slack event text must not be empty after mention stripping"
        raise ValueError(msg)

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
