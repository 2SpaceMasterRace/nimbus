"""Reference helpers for Python chat wrappers that call Nimbus.

These helpers intentionally stay small and transport-focused:

- normalize Slack message-ish events into the canonical Nimbus request body
- normalize Slack slash commands into the same request body
- sign requests for ``POST /ai/chat/turn``

The wrapper still owns Slack verification, dedupe, retry policy, and posting the
final response back into Slack.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

_WRAPPER_PLATFORM: Final[str] = "slack"
_TURN_PATH: Final[str] = "/ai/chat/turn"


def _thread_anchor_for_message_event(event: Mapping[str, object]) -> str:
    """Return the Slack thread root or the message timestamp itself."""
    thread_ts = event.get("thread_ts")
    if isinstance(thread_ts, str) and thread_ts:
        return thread_ts
    message_ts = event.get("ts")
    if isinstance(message_ts, str) and message_ts:
        return message_ts
    msg = "Slack message event must include a non-empty ts"
    raise ValueError(msg)


def _strip_leading_app_mention(text: str) -> str:
    """Strip one leading Slack app mention token from a message body."""
    parts = text.split(maxsplit=1)
    if parts and parts[0].startswith("<@") and parts[0].endswith(">"):
        return parts[1] if len(parts) > 1 else ""
    return text


def build_message_event_turn(
    *,
    workspace_id: str,
    event_id: str,
    event: Mapping[str, object],
) -> dict[str, object]:
    """Normalize a Slack message, app mention, thread reply, or DM event."""
    message_ts = event.get("ts")
    channel_id = event.get("channel")
    user_id = event.get("user")
    text = event.get("text", "")
    if not isinstance(message_ts, str) or not message_ts:
        msg = "Slack message event must include a non-empty ts"
        raise ValueError(msg)
    if not isinstance(channel_id, str) or not channel_id:
        msg = "Slack message event must include a non-empty channel"
        raise ValueError(msg)
    if not isinstance(user_id, str) or not user_id:
        msg = "Slack message event must include a non-empty user"
        raise ValueError(msg)
    normalized_text = _strip_leading_app_mention(str(text)).strip()
    return {
        "platform": _WRAPPER_PLATFORM,
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "thread_id": _thread_anchor_for_message_event(event),
        "message_id": message_ts,
        "user_id": user_id,
        "text": normalized_text,
        "idempotency_key": f"slack:{workspace_id}:event:{event_id}",
        "request_id": f"req-slack-{event_id}",
    }


def build_slash_command_turn(  # noqa: PLR0913 - explicit Slack fields keep the wrapper contract obvious
    *,
    workspace_id: str,
    channel_id: str,
    trigger_id: str,
    user_id: str,
    text: str,
    thread_id: str | None = None,
) -> dict[str, object]:
    """Normalize a Slack slash command into the canonical Nimbus request body."""
    return {
        "platform": _WRAPPER_PLATFORM,
        "workspace_id": workspace_id,
        "channel_id": channel_id,
        "thread_id": thread_id,
        "message_id": f"cmd:{trigger_id}",
        "user_id": user_id,
        "text": text.strip(),
        "idempotency_key": f"slack:{workspace_id}:command:{trigger_id}",
        "request_id": f"req-slack-cmd-{trigger_id}",
    }


def encode_turn_body(body: Mapping[str, object]) -> bytes:
    """Encode one canonical request body to JSON bytes for signing/sending."""
    return json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def sign_nimbus_request(  # noqa: PLR0913 - explicit signing inputs keep the HMAC contract legible
    *,
    body: bytes,
    secret: str,
    method: str = "POST",
    path: str = _TURN_PATH,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Return the signed headers Nimbus expects on ``POST /ai/chat/turn``."""
    ts = int(time.time()) if timestamp is None else timestamp
    used_nonce = nonce or f"nonce-{uuid.uuid4().hex}"
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = f"{method.upper()}\n{path}\n{ts}\n{used_nonce}\n{body_digest}"
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Nimbus-Timestamp": str(ts),
        "X-Nimbus-Nonce": used_nonce,
        "X-Nimbus-Signature": signature,
    }
