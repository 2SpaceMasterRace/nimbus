"""Transport-neutral runtime request and response shapes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

TurnOutcome = Literal["reply", "confirmation_required", "partial_success", "error"]


@dataclass(frozen=True, slots=True)
class TurnAttachment:
    """Attachment metadata and optional inline bytes for one wrapper turn."""

    platform_file_id: str
    filename: str
    content_type: str
    size_bytes: int
    content_base64: str | None = None
    sha256_hex: str | None = None


@dataclass(frozen=True, slots=True)
class ConfirmationDetails:
    """Explicit confirmation state returned to the wrapper."""

    action_id: str
    kind: Literal["delete_file"]
    prompt: str
    expected_reply: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class ActionSummary:
    """Small transport-neutral summary of a durable Nimbus action."""

    action_id: str
    kind: str
    status: str
    target: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ArtifactSummary:
    """Small transport-neutral summary of a Nimbus artifact."""

    artifact_id: str
    kind: str
    action_id: str | None = None
    payload: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ChatTurnInput:
    """Normalized runtime request for one chat turn."""

    request_id: str
    conversation_id: str
    platform: str
    workspace_id: str
    channel_id: str
    thread_id: str | None
    message_id: str
    user_id: str
    text: str
    idempotency_key: str | None = None
    attachments: tuple[TurnAttachment, ...] = ()


@dataclass(frozen=True, slots=True)
class ChatTurnResult:
    """Transport-neutral result returned by the shared runtime."""

    request_id: str
    conversation_id: str
    text: str
    outcome: TurnOutcome
    confirmation_required: bool
    suggested_next_actions: tuple[str, ...] = ()
    model: str = "nimbus-runtime"
    steps: int = 0
    fallback_used: bool = False
    confirmation: ConfirmationDetails | None = None
    actions: tuple[ActionSummary, ...] = ()
    artifacts: tuple[ArtifactSummary, ...] = ()
