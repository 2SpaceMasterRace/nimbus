"""Small fail-closed policy layer for Nimbus runtime actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nimbus_runtime.domain import (
    Action,
    ActionKind,
    UploadAttachmentInput,
    VerifiedActor,
)


class PolicyDecision(StrEnum):
    """Possible authorization outcomes for a Nimbus action."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_ADMIN_APPROVAL = "require_admin_approval"


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Inputs that policy needs beyond the actor and action."""

    pinned_container: str | None
    max_upload_bytes: int


def authorize_action(  # noqa: PLR0911 - fail-closed rules are clearer as guards
    *,
    actor: VerifiedActor,
    action: Action,
    context: PolicyContext,
) -> PolicyDecision:
    """Return the least-surprising safe policy decision for an action."""
    if actor.tenant != action.tenant:
        return PolicyDecision.DENY
    if (
        action.target is not None
        and action.target.container != context.pinned_container
    ):
        return PolicyDecision.DENY
    if action.kind is ActionKind.DELETE_FILE:
        return PolicyDecision.REQUIRE_CONFIRMATION
    if action.kind is ActionKind.UPLOAD_ATTACHMENT:
        if (
            isinstance(action.input, UploadAttachmentInput)
            and 0 < action.input.size_bytes <= context.max_upload_bytes
        ):
            return PolicyDecision.ALLOW
        return PolicyDecision.DENY
    if action.kind in {ActionKind.LIST_FILES, ActionKind.GET_FILE_INFO}:
        return PolicyDecision.ALLOW
    return PolicyDecision.DENY
