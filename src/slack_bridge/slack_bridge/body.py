"""Slack event to NimbusTurnRequest translation for the Nimbus Slack bridge.

Converts raw Slack event payloads (and slash-command form posts) into the
wire format expected by POST /ai/chat/turn on the Nimbus AI service.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import structlog
from nimbus_runtime.models import TurnAttachment

from slack_bridge.models import NimbusTurnRequest

if TYPE_CHECKING:
    from collections.abc import Mapping

log = structlog.get_logger()

# Limits applied to inbound Slack file metadata before forwarding to Nimbus.
# The byte cap matches the Nimbus AI service's per-attachment ceiling; the
# count cap keeps a single noisy turn from inflating the signed body.
_MAX_ATTACHMENT_BYTES: Final[int] = 20 * 1024 * 1024
_MAX_ATTACHMENTS_PER_TURN: Final[int] = 10
_DEFAULT_CONTENT_TYPE: Final[str] = "application/octet-stream"


def _strip_mention(text: str) -> str:
    """Strip a leading Slack app-mention token from message text.

    Slack mentions are emitted as ``<@USERID>`` or ``<@USERID|fallback>``
    and may be followed by a space, a newline, or nothing at all when the
    user pings the bot with no further text. Any leading whitespace after
    the mention is also removed so downstream parsers see the user's
    intended text without padding.

    Args:
        text: Raw message text from the Slack event.

    Returns:
        Text with the leading mention removed, or the original text
        unchanged when no leading mention is present or the mention is
        malformed (no closing ``>``).

    """
    if not text.startswith("<@"):
        return text
    end = text.find(">")
    if end == -1:
        return text
    return text[end + 1 :].lstrip()


def build_event_body(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, object],
) -> NimbusTurnRequest:
    """Build a NimbusTurnRequest from a Slack message event payload.

    Slack file uploads arrive on the inner event under ``files`` as a list
    of metadata dicts. Each entry is normalized into a
    :class:`TurnAttachment` so the AI server can reason about the upload
    without the bridge fetching bytes. Entries with non-positive size,
    missing required fields, or sizes above
    :data:`_MAX_ATTACHMENT_BYTES` are dropped, and at most
    :data:`_MAX_ATTACHMENTS_PER_TURN` attachments are forwarded to keep
    the signed body bounded.

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
    attachments = _extract_event_attachments(event.get("files"))

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
        attachments=attachments,
    )


def build_slash_command_body(
    form: Mapping[str, str],
    *,
    command_text: str | None = None,
) -> NimbusTurnRequest:
    """Build a NimbusTurnRequest from a Slack slash-command form payload.

    Slash commands arrive as ``application/x-www-form-urlencoded`` POSTs,
    not Events API JSON. They are one-shot invocations: there is no
    ``thread_ts`` to anchor a conversation, and Slack does not send the
    user's command back as a normal channel message either. The Nimbus
    contract therefore treats each invocation as its own conversation
    (``thread_id=None``) keyed on the slash command's ``trigger_id``.

    Args:
        form: Decoded form fields from the slash-command POST. Must
            include ``team_id``, ``trigger_id``, ``channel_id``,
            ``user_id``; ``text`` is optional and may be empty.
        command_text: Optional pre-resolved text. When omitted, the
            ``text`` form field is used as-is (with whitespace trimmed).

    Returns:
        A NimbusTurnRequest ready to be signed and sent to the Nimbus AI service.

    Raises:
        KeyError: One of the required form fields is missing.

    """
    team_id = form["team_id"]
    trigger_id = form["trigger_id"]
    channel_id = form["channel_id"]
    user_id = form["user_id"]
    text = (command_text if command_text is not None else form.get("text", "")).strip()
    return NimbusTurnRequest(
        platform="slack",
        workspace_id=team_id,
        channel_id=channel_id,
        thread_id=None,
        message_id=f"cmd:{trigger_id}",
        user_id=user_id,
        text=text,
        idempotency_key=f"slack:{team_id}:command:{trigger_id}",
        request_id=f"slack-cmd-{trigger_id}",
    )


def _extract_event_attachments(
    raw_files: object,
) -> tuple[TurnAttachment, ...]:
    """Normalize a Slack ``event.files`` list into Nimbus attachments.

    Returns an empty tuple when ``raw_files`` is missing or not a list so
    the bridge silently passes turns without attachments through unchanged.
    """
    if not isinstance(raw_files, list):
        return ()
    normalized: list[TurnAttachment] = []
    for raw_file in raw_files:
        if len(normalized) >= _MAX_ATTACHMENTS_PER_TURN:
            break
        attachment = _normalize_attachment(raw_file)
        if attachment is not None:
            normalized.append(attachment)
    return tuple(normalized)


def _normalize_attachment(raw_file: object) -> TurnAttachment | None:
    """Return a TurnAttachment for ``raw_file`` or None to drop it.

    Drops malformed entries, entries missing ``id``/``name``, and entries
    whose declared size is non-positive or larger than
    :data:`_MAX_ATTACHMENT_BYTES`.
    """
    if not isinstance(raw_file, dict):
        return None
    file_id = raw_file.get("id")
    filename = raw_file.get("name")
    if not isinstance(file_id, str) or not file_id:
        return None
    if not isinstance(filename, str) or not filename:
        return None
    size = _coerce_size(raw_file.get("size"))
    if size is None or size <= 0 or size > _MAX_ATTACHMENT_BYTES:
        return None
    mimetype = raw_file.get("mimetype")
    content_type = (
        mimetype if isinstance(mimetype, str) and mimetype else _DEFAULT_CONTENT_TYPE
    )
    return TurnAttachment(
        platform_file_id=file_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size,
    )


def _coerce_size(raw_size: object) -> int | None:
    """Return ``raw_size`` as a non-negative int when possible, else None.

    Slack documents ``files[].size`` as an integer byte count, but
    payloads observed in the wild occasionally arrive as numeric strings.
    Accept both shapes; reject anything else (booleans, floats, missing
    fields) rather than guessing.
    """
    if isinstance(raw_size, bool):
        return None
    if isinstance(raw_size, int):
        return raw_size
    if isinstance(raw_size, str):
        try:
            return int(raw_size)
        except ValueError:
            return None
    return None
