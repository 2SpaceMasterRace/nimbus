"""Wire-format models for the Nimbus Slack bridge."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nimbus_protocol import TurnAttachment


@dataclasses.dataclass(frozen=True, slots=True)
class NimbusTurnRequest:
    """Wrapper-facing request body for POST /ai/chat/turn.

    Important:
        idempotency_key must be stable across retries for the same source event.
        Do not generate a fresh UUID for each retry.

    """

    platform: str
    workspace_id: str
    channel_id: str
    message_id: str
    user_id: str
    text: str
    idempotency_key: str
    thread_id: str | None = None
    request_id: str | None = None
    attachments: tuple[TurnAttachment, ...] = ()
