"""Core Nimbus session/action domain primitives."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

ProviderName = Literal["s3", "gcs", "dropbox", "drive"]
ActorAuthSource = Literal[
    "slack_signed_event",
    "cli_local",
    "github_oauth",
    "oidc",
    "service_account",
]


@dataclass(frozen=True, slots=True)
class TenantIdentity:
    """Tenant isolation boundary for all durable Nimbus state."""

    platform: str
    workspace_id: str

    @property
    def tenant_id(self) -> str:
        """Return the canonical tenant key."""
        return f"{self.platform}:{self.workspace_id}"


@dataclass(frozen=True, slots=True)
class VerifiedActor:
    """Verified human or service principal that initiated a Nimbus operation."""

    tenant: TenantIdentity
    user_id: str
    auth_source: ActorAuthSource
    bridge_id: str | None
    verified_at: datetime

    @property
    def principal_key(self) -> str:
        """Return the tenant-scoped principal key."""
        return f"{self.tenant.tenant_id}:{self.user_id}"


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """Tenant-scoped pointer to one provider object."""

    provider: ProviderName
    container: str
    object_name: str
    version_id: str | None = None


class ActionKind(StrEnum):
    """Supported durable Nimbus action kinds."""

    LIST_FILES = "list_files"
    GET_FILE_INFO = "get_file_info"
    UPLOAD_ATTACHMENT = "upload_attachment"
    DELETE_FILE = "delete_file"
    SUMMARIZE_PREFIX = "summarize_prefix"
    SPAWN_CHILD_SESSION = "spawn_child_session"


class ActionStatus(StrEnum):
    """Durable action state-machine statuses."""

    PROPOSED = "proposed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AUTHORIZED = "authorized"
    QUEUED = "queued"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class DeleteFileInput:
    """Typed input for a delete-file action."""

    remote_path: str


@dataclass(frozen=True, slots=True)
class UploadAttachmentInput:
    """Typed input for an attachment upload action."""

    platform_file_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256_hex: str | None
    remote_path: str


@dataclass(frozen=True, slots=True)
class DeleteFileResult:
    """Typed result for a delete-file action."""

    remote_path: str
    deleted: bool
    version_id: str | None
    artifact_id: str | None = None

    def with_artifact(self, artifact_id: str) -> DeleteFileResult:
        """Return this result linked to its evidence artifact."""
        return replace(self, artifact_id=artifact_id)


@dataclass(frozen=True, slots=True)
class UploadAttachmentResult:
    """Typed result for an attachment upload action."""

    remote_path: str
    size_bytes: int
    sha256_hex: str
    artifact_id: str | None = None

    def with_artifact(self, artifact_id: str) -> UploadAttachmentResult:
        """Return this result linked to its evidence artifact."""
        return replace(self, artifact_id=artifact_id)


@dataclass(frozen=True, slots=True)
class ActionFailure:
    """Typed failure recorded for an action transition."""

    detail: str
    remote_path: str | None = None


@dataclass(frozen=True, slots=True)
class DeleteReport:
    """Evidence payload for a completed delete action."""

    remote_path: str
    deleted: bool
    version_id: str | None


@dataclass(frozen=True, slots=True)
class UploadReport:
    """Evidence payload for a completed upload action."""

    remote_path: str
    filename: str
    size_bytes: int
    sha256_hex: str


ActionInput = DeleteFileInput | UploadAttachmentInput
ActionResult = DeleteFileResult | UploadAttachmentResult
ArtifactPayload = DeleteReport | UploadReport


@dataclass(frozen=True, slots=True)
class ActionTransition:
    """Compare-and-set state transition with its durable event payload."""

    expected: ActionStatus
    next_status: ActionStatus
    event_type: str
    event_payload: Mapping[str, object]
    result: ActionResult | None = None
    failure: ActionFailure | None = None


@dataclass(frozen=True, slots=True)
class Action:
    """Durable side-effecting unit of Nimbus work."""

    action_id: str
    tenant: TenantIdentity
    session_id: str
    actor: VerifiedActor
    kind: ActionKind
    target: ObjectRef | None
    status: ActionStatus
    idempotency_key: str
    input: ActionInput
    result: ActionResult | None
    failure: ActionFailure | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """Ordered durable event in a Nimbus session."""

    tenant: TenantIdentity
    session_id: str
    sequence: int
    event_id: str
    event_type: str
    actor: VerifiedActor | None
    payload: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Artifact:
    """Evidence or work product created during a Nimbus session."""

    artifact_id: str
    tenant: TenantIdentity
    session_id: str
    action_id: str | None
    kind: Literal["delete_report", "upload_report"]
    uri: str | None
    payload: ArtifactPayload
    created_at: datetime


_ALLOWED_TRANSITIONS: Mapping[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.PROPOSED: frozenset(
        {
            ActionStatus.AUTHORIZED,
            ActionStatus.AWAITING_CONFIRMATION,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.AWAITING_CONFIRMATION: frozenset(
        {
            ActionStatus.AUTHORIZED,
            ActionStatus.EXPIRED,
            ActionStatus.CANCELLED,
        }
    ),
    ActionStatus.AUTHORIZED: frozenset(
        {
            ActionStatus.QUEUED,
            ActionStatus.EXECUTING,
            ActionStatus.CANCELLED,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.QUEUED: frozenset(
        {
            ActionStatus.EXECUTING,
            ActionStatus.CANCELLED,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.EXECUTING: frozenset(
        {
            ActionStatus.VERIFYING,
            ActionStatus.FAILED_RETRYABLE,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.VERIFYING: frozenset(
        {
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED_RETRYABLE,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.FAILED_RETRYABLE: frozenset(
        {
            ActionStatus.QUEUED,
            ActionStatus.FAILED_TERMINAL,
            ActionStatus.CANCELLED,
        }
    ),
    ActionStatus.SUCCEEDED: frozenset(),
    ActionStatus.FAILED_TERMINAL: frozenset(),
    ActionStatus.EXPIRED: frozenset(),
    ActionStatus.CANCELLED: frozenset(),
}


def is_valid_action_transition(
    *, expected: ActionStatus, next_status: ActionStatus
) -> bool:
    """Return whether an action may move from ``expected`` to ``next_status``."""
    return next_status in _ALLOWED_TRANSITIONS[expected]


def validate_action_transition(
    *, expected: ActionStatus, next_status: ActionStatus
) -> None:
    """Raise ``ValueError`` when an action transition is not allowed."""
    if is_valid_action_transition(expected=expected, next_status=next_status):
        return
    msg = f"invalid action transition: {expected.value} -> {next_status.value}"
    raise ValueError(msg)
