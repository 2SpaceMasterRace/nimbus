"""Data-driven fail-closed policy layer for Nimbus runtime actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from nimbus_runtime.domain import (
    Action,
    ActionKind,
    CopyFileInput,
    DeleteFileInput,
    MoveFileInput,
    PolicyDecision,
    PolicyDecisionRecord,
    UploadAttachmentInput,
    VerifiedActor,
    WriteFileInput,
)

__all__ = [
    "PolicyActorRole",
    "PolicyConfig",
    "PolicyContext",
    "PolicyDecision",
    "PolicyGrant",
    "approval_actor_ids_for_action",
    "authorize_action",
    "authorize_action_with_record",
]

PolicyScope = Literal["current_channel", "workspace"]


class PolicyActorRole(StrEnum):
    """Role names accepted by the runtime policy engine."""

    WORKSPACE_ADMIN = "workspace_admin"
    DELEGATED_ADMIN = "delegated_admin"
    CHANNEL_OWNER = "channel_owner"


@dataclass(frozen=True, slots=True)
class PolicyGrant:
    """Actor grant used by data-driven policy decisions."""

    actor_id: str
    role: PolicyActorRole
    channel_id: str | None = None
    expires_at: datetime | None = None

    def is_active(self, *, now: datetime) -> bool:
        """Return whether this grant is active at ``now``."""
        return self.expires_at is None or self.expires_at > now


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    """Versioned policy settings for one Nimbus runtime."""

    policy_version: str = "runtime-policy-v1"
    default_scope: PolicyScope = "current_channel"
    approval_expiry_minutes: int = 10
    max_files_without_preview: int = 100
    max_bytes_without_preview: int = 2 * 1024 * 1024 * 1024

    def __post_init__(self) -> None:
        """Validate policy bounds before authorizing actions."""
        if not self.policy_version:
            msg = "policy_version is required"
            raise ValueError(msg)
        if self.approval_expiry_minutes < 1:
            msg = "approval_expiry_minutes must be positive"
            raise ValueError(msg)
        if self.max_files_without_preview < 1:
            msg = "max_files_without_preview must be positive"
            raise ValueError(msg)
        if self.max_bytes_without_preview < 1:
            msg = "max_bytes_without_preview must be positive"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Inputs that policy needs beyond the actor and action."""

    pinned_container: str | None
    max_upload_bytes: int
    current_channel_id: str | None = None
    requested_scope: PolicyScope = "current_channel"
    grants: tuple[PolicyGrant, ...] = ()
    config: PolicyConfig = field(default_factory=PolicyConfig)

    def __post_init__(self) -> None:
        """Validate runtime policy context."""
        if self.max_upload_bytes < 1:
            msg = "max_upload_bytes must be positive"
            raise ValueError(msg)


def authorize_action_with_record(  # noqa: PLR0911 - fail-closed rules are clearer as guards
    *,
    actor: VerifiedActor,
    action: Action,
    context: PolicyContext,
    now: datetime | None = None,
) -> PolicyDecisionRecord:
    """Return a durable policy decision record for an action."""
    decision_at = datetime.now(UTC) if now is None else now
    if actor.tenant != action.tenant:
        return _record(
            actor=actor,
            action=action,
            context=context,
            decision=PolicyDecision.DENY,
            reason="actor_tenant_mismatch",
            now=decision_at,
        )
    if (
        action.target is not None
        and action.target.container != context.pinned_container
    ):
        return _record(
            actor=actor,
            action=action,
            context=context,
            decision=PolicyDecision.DENY,
            reason="target_container_not_pinned",
            now=decision_at,
        )
    if action.kind in {
        ActionKind.DELETE_FILE,
        ActionKind.COPY_FILE,
        ActionKind.MOVE_FILE,
        ActionKind.WRITE_FILE,
    }:
        return _record(
            actor=actor,
            action=action,
            context=context,
            decision=PolicyDecision.REQUIRES_APPROVAL,
            reason=f"{action.kind.value}_requires_actor_or_delegate_approval",
            now=decision_at,
        )
    if context.requested_scope == "workspace" and not _has_role(
        actor_id=actor.user_id,
        role=PolicyActorRole.WORKSPACE_ADMIN,
        grants=context.grants,
        now=decision_at,
    ):
        return _record(
            actor=actor,
            action=action,
            context=context,
            decision=PolicyDecision.REQUIRES_ADMIN_GRANT,
            reason="workspace_scope_requires_admin_grant",
            now=decision_at,
        )
    if action.kind is ActionKind.UPLOAD_ATTACHMENT:
        if (
            isinstance(action.input, UploadAttachmentInput)
            and 0 < action.input.size_bytes <= context.max_upload_bytes
        ):
            return _record(
                actor=actor,
                action=action,
                context=context,
                decision=PolicyDecision.ALLOW,
                reason="upload_within_size_limit",
                now=decision_at,
            )
        return _record(
            actor=actor,
            action=action,
            context=context,
            decision=PolicyDecision.DENY,
            reason="upload_size_out_of_bounds",
            now=decision_at,
        )
    if action.kind in {ActionKind.LIST_FILES, ActionKind.GET_FILE_INFO}:
        return _record(
            actor=actor,
            action=action,
            context=context,
            decision=PolicyDecision.ALLOW,
            reason=(
                "read_within_workspace_admin_scope"
                if context.requested_scope == "workspace"
                else "read_within_default_scope"
            ),
            now=decision_at,
        )
    return _record(
        actor=actor,
        action=action,
        context=context,
        decision=PolicyDecision.DENY,
        reason="unsupported_action_kind",
        now=decision_at,
    )


def authorize_action(
    *,
    actor: VerifiedActor,
    action: Action,
    context: PolicyContext,
) -> PolicyDecision:
    """Return the policy decision for compatibility with older call sites."""
    return authorize_action_with_record(
        actor=actor,
        action=action,
        context=context,
    ).decision


def approval_actor_ids_for_action(
    *,
    actor: VerifiedActor,
    action: Action,
    context: PolicyContext,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Return actor IDs allowed to approve a risky action."""
    if action.kind not in {
        ActionKind.DELETE_FILE,
        ActionKind.COPY_FILE,
        ActionKind.MOVE_FILE,
        ActionKind.WRITE_FILE,
    }:
        return (actor.user_id,)
    decision_at = datetime.now(UTC) if now is None else now
    allowed = {actor.user_id}
    for grant in context.grants:
        if not grant.is_active(now=decision_at):
            continue
        if grant.role is PolicyActorRole.DELEGATED_ADMIN or (
            grant.role is PolicyActorRole.CHANNEL_OWNER
            and (
                grant.channel_id is None
                or grant.channel_id == context.current_channel_id
            )
        ):
            allowed.add(grant.actor_id)
    return tuple(sorted(allowed))


def _record(  # noqa: PLR0913 - decision records intentionally name each axis.
    *,
    actor: VerifiedActor,
    action: Action,
    context: PolicyContext,
    decision: PolicyDecision,
    reason: str,
    now: datetime,
) -> PolicyDecisionRecord:
    return PolicyDecisionRecord(
        tenant_id=action.tenant.tenant_id,
        actor_id=actor.user_id,
        operation=action.kind.value,
        target=_policy_target(action),
        decision=decision,
        reason=reason,
        policy_version=context.config.policy_version,
        created_at=now,
    )


def _policy_target(action: Action) -> str:
    if action.target is not None:
        return action.target.object_name
    if isinstance(action.input, DeleteFileInput):
        return action.input.remote_path
    if isinstance(action.input, UploadAttachmentInput):
        return action.input.remote_path
    if isinstance(action.input, CopyFileInput | MoveFileInput):
        return f"{action.input.source_path} -> {action.input.dest_path}"
    if isinstance(action.input, WriteFileInput):
        return f"{action.input.remote_path} sha256:{action.input.content_sha256_hex}"
    return action.kind.value


def _has_role(
    *,
    actor_id: str,
    role: PolicyActorRole,
    grants: tuple[PolicyGrant, ...],
    now: datetime,
) -> bool:
    return any(
        grant.actor_id == actor_id and grant.role is role and grant.is_active(now=now)
        for grant in grants
    )
