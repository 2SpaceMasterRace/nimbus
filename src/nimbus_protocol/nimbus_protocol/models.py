"""Shared Nimbus protocol models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping


TurnOutcome = Literal["reply", "confirmation_required", "partial_success", "error"]


class StreamEventType(StrEnum):
    """Event names that can be streamed and replayed to clients."""

    TURN_STARTED = "turn.started"
    PROVIDER_REQUEST_STARTED = "provider.request.started"
    TEXT_DELTA = "text.delta"
    TEXT_COMPLETED = "text.completed"
    REASONING_DELTA = "reasoning.delta"
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_COMPLETED = "tool.call.completed"
    MODEL_FALLBACK = "model.fallback"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"
    ACTION_CREATED = "action.created"
    ACTION_UPDATED = "action.updated"
    ARTIFACT_CREATED = "artifact.created"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    ERROR = "error"


class PermissionEffect(StrEnum):
    """Effect one permission rule can have."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class ApprovalChoice(StrEnum):
    """Choices a client may send for an approval request."""

    ALLOW_ONCE = "allow_once"
    ALLOW_ALWAYS = "allow_always"
    DENY = "deny"


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
    kind: Literal["delete_file", "copy_file", "move_file", "write_file"]
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


@dataclass(frozen=True, slots=True)
class SessionRef:
    """Stable external and internal identity for one Nimbus session."""

    internal_id: str
    external_id: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """Actor-scoped durable permission rule."""

    permission: str
    target_pattern: str
    effect: PermissionEffect
    actor_id: str | None = None
    tenant_id: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Typed approval request emitted by the runtime."""

    approval_id: str
    session_id: str
    turn_id: str
    action_id: str | None
    actor_id: str
    permission: str
    target: str
    reason: str
    choices: tuple[ApprovalChoice, ...]
    proposed_rule: PermissionRule | None = None
    expires_at: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Typed approval decision sent from a client to the runtime."""

    approval_id: str
    session_id: str
    actor_id: str
    choice: ApprovalChoice
    message: str | None = None


@dataclass(frozen=True, slots=True)
class NimbusEvent:
    """Ordered event that can be rendered live or replayed later."""

    session_id: str
    sequence: int
    event_id: str
    event_type: str
    payload: Mapping[str, object] = field(default_factory=dict)
    turn_id: str | None = None
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class TurnStartCommand:
    """Command to start a new turn."""

    session: SessionRef
    text: str
    user_id: str
    model: str | None = None
    local: bool = True


@dataclass(frozen=True, slots=True)
class TurnResumeCommand:
    """Command to resume event streaming from a known sequence."""

    session: SessionRef
    after_sequence: int = 0


def event_from_mapping(payload: Mapping[str, object]) -> NimbusEvent:
    """Decode a JSON-like mapping into a ``NimbusEvent``."""
    return NimbusEvent(
        session_id=_require_str(payload, "session_id"),
        sequence=_require_int(payload, "sequence"),
        event_id=_require_str(payload, "event_id"),
        event_type=_require_str(payload, "event_type"),
        payload=_optional_mapping(payload, "payload") or {},
        turn_id=_optional_str(payload, "turn_id"),
        created_at=_optional_str(payload, "created_at"),
    )


def _require_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        msg = f"{key!r} must be a string"
        raise TypeError(msg)
    return value


def _optional_str(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"{key!r} must be a string or null"
    raise TypeError(msg)


def _require_int(payload: Mapping[str, object], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{key!r} must be an integer"
        raise TypeError(msg)
    return value


def _optional_mapping(
    payload: Mapping[str, object],
    key: str,
) -> dict[str, object] | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, dict):
        return dict(value)
    msg = f"{key!r} must be an object or null"
    raise TypeError(msg)
