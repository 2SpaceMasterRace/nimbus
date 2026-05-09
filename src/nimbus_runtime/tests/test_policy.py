"""Unit tests for the Nimbus data-driven policy engine."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from nimbus_runtime import (
    Action,
    ActionKind,
    ActionStatus,
    CopyFileInput,
    DeleteFileInput,
    ObjectRef,
    PolicyActorRole,
    PolicyConfig,
    PolicyContext,
    PolicyDecision,
    PolicyGrant,
    TenantIdentity,
    UploadAttachmentInput,
    VerifiedActor,
    WriteFileInput,
    approval_actor_ids_for_action,
    authorize_action,
    authorize_action_with_record,
)

pytestmark = pytest.mark.unit


def _tenant(workspace_id: str = "T123TEAM") -> TenantIdentity:
    return TenantIdentity(platform="slack", workspace_id=workspace_id)


def _actor(
    tenant: TenantIdentity,
    *,
    user_id: str = "U123USER",
) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id=user_id,
        auth_source="slack_signed_event",
        bridge_id="slack",
        verified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _upload_action(
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    size_bytes: int = 128,
) -> Action:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Action(
        action_id="act-upload",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.UPLOAD_ATTACHMENT,
        target=ObjectRef(
            provider="s3",
            container="bucket",
            object_name="uploads/report.txt",
        ),
        status=ActionStatus.AUTHORIZED,
        idempotency_key="idem-upload",
        input=UploadAttachmentInput(
            platform_file_id="F123",
            filename="report.txt",
            content_type="text/plain",
            size_bytes=size_bytes,
            sha256_hex=None,
            remote_path="uploads/report.txt",
        ),
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )


def _delete_action(*, tenant: TenantIdentity, actor: VerifiedActor) -> Action:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Action(
        action_id="act-delete",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.DELETE_FILE,
        target=ObjectRef(
            provider="s3",
            container="bucket",
            object_name="reports/old.csv",
        ),
        status=ActionStatus.AWAITING_CONFIRMATION,
        idempotency_key="idem-delete",
        input=DeleteFileInput(remote_path="reports/old.csv"),
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=10),
    )


def test_policy_allows_default_channel_scope_uploads() -> None:
    """Default current-channel work should be allowed when bounds hold."""
    tenant = _tenant()
    actor = _actor(tenant)
    action = _upload_action(tenant=tenant, actor=actor)

    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert record.decision is PolicyDecision.ALLOW
    assert record.reason == "upload_within_size_limit"
    assert record.tenant_id == tenant.tenant_id
    assert record.operation == ActionKind.UPLOAD_ATTACHMENT.value
    assert record.target == "uploads/report.txt"


def test_policy_requires_admin_grant_for_workspace_scope() -> None:
    """Workspace-scope operations should fail closed without an admin grant."""
    tenant = _tenant()
    actor = _actor(tenant)
    action = _upload_action(tenant=tenant, actor=actor)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    denied = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(
            pinned_container="bucket",
            max_upload_bytes=1024,
            requested_scope="workspace",
        ),
        now=now,
    )
    allowed = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(
            pinned_container="bucket",
            max_upload_bytes=1024,
            requested_scope="workspace",
            grants=(
                PolicyGrant(
                    actor_id=actor.user_id,
                    role=PolicyActorRole.WORKSPACE_ADMIN,
                ),
            ),
        ),
        now=now,
    )

    assert denied.decision is PolicyDecision.REQUIRES_ADMIN_GRANT
    assert denied.reason == "workspace_scope_requires_admin_grant"
    assert allowed.decision is PolicyDecision.ALLOW


def test_policy_denies_wrong_tenant_and_wrong_container() -> None:
    """Tenant and pinned-container mismatches should deny before execution."""
    tenant = _tenant()
    actor = _actor(tenant)
    action = _upload_action(tenant=tenant, actor=actor)
    other_actor = _actor(_tenant("T999TEAM"))

    wrong_tenant = authorize_action_with_record(
        actor=other_actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    wrong_container = authorize_action_with_record(
        actor=actor,
        action=replace(
            action,
            target=ObjectRef(
                provider="s3",
                container="other-bucket",
                object_name="uploads/report.txt",
            ),
        ),
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert wrong_tenant.decision is PolicyDecision.DENY
    assert wrong_tenant.reason == "actor_tenant_mismatch"
    assert wrong_container.decision is PolicyDecision.DENY
    assert wrong_container.reason == "target_container_not_pinned"


def test_delete_policy_records_approval_and_delegated_approvers() -> None:
    """Delete decisions should require approval and name allowed delegates."""
    tenant = _tenant()
    actor = _actor(tenant)
    action = _delete_action(tenant=tenant, actor=actor)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    context = PolicyContext(
        pinned_container="bucket",
        max_upload_bytes=1024,
        current_channel_id="C123CHAN",
        grants=(
            PolicyGrant(
                actor_id="UADMIN",
                role=PolicyActorRole.DELEGATED_ADMIN,
            ),
            PolicyGrant(
                actor_id="UOWNER",
                role=PolicyActorRole.CHANNEL_OWNER,
                channel_id="C123CHAN",
            ),
        ),
    )

    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=context,
        now=now,
    )
    approvers = approval_actor_ids_for_action(
        actor=actor,
        action=action,
        context=context,
        now=now,
    )

    assert record.decision is PolicyDecision.REQUIRES_APPROVAL
    assert record.reason == "delete_file_requires_actor_or_delegate_approval"
    assert approvers == ("U123USER", "UADMIN", "UOWNER")


def test_expired_grants_do_not_allow_admin_or_approval_paths() -> None:
    """Expired grants should be ignored by policy decisions and approvals."""
    tenant = _tenant()
    actor = _actor(tenant)
    action = _upload_action(tenant=tenant, actor=actor)
    delete_action = _delete_action(tenant=tenant, actor=actor)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    expired = PolicyGrant(
        actor_id=actor.user_id,
        role=PolicyActorRole.WORKSPACE_ADMIN,
        expires_at=now - timedelta(seconds=1),
    )
    expired_owner = PolicyGrant(
        actor_id="UOWNER",
        role=PolicyActorRole.CHANNEL_OWNER,
        channel_id="C123CHAN",
        expires_at=now - timedelta(seconds=1),
    )
    context = PolicyContext(
        pinned_container="bucket",
        max_upload_bytes=1024,
        current_channel_id="C123CHAN",
        requested_scope="workspace",
        grants=(expired, expired_owner),
    )

    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=context,
        now=now,
    )
    approvers = approval_actor_ids_for_action(
        actor=actor,
        action=delete_action,
        context=context,
        now=now,
    )

    assert record.decision is PolicyDecision.REQUIRES_ADMIN_GRANT
    assert approvers == ("U123USER",)


def test_policy_config_rejects_empty_version() -> None:
    """PolicyConfig should raise ValueError when policy_version is empty."""
    with pytest.raises(ValueError, match="policy_version is required"):
        PolicyConfig(policy_version="")


def test_policy_config_rejects_non_positive_expiry() -> None:
    """PolicyConfig should raise ValueError for non-positive approval_expiry_minutes."""
    with pytest.raises(ValueError, match="approval_expiry_minutes must be positive"):
        PolicyConfig(approval_expiry_minutes=0)


def test_policy_config_rejects_non_positive_max_files() -> None:
    """PolicyConfig should raise ValueError for non-positive max_files_without_preview."""
    with pytest.raises(ValueError, match="max_files_without_preview must be positive"):
        PolicyConfig(max_files_without_preview=0)


def test_policy_config_rejects_non_positive_max_bytes() -> None:
    """PolicyConfig should raise ValueError for non-positive max_bytes_without_preview."""
    with pytest.raises(ValueError, match="max_bytes_without_preview must be positive"):
        PolicyConfig(max_bytes_without_preview=0)


def test_policy_context_rejects_non_positive_max_upload() -> None:
    """PolicyContext should raise ValueError for non-positive max_upload_bytes."""
    with pytest.raises(ValueError, match="max_upload_bytes must be positive"):
        PolicyContext(pinned_container="bucket", max_upload_bytes=0)


def test_policy_denies_upload_exceeding_max_size() -> None:
    """Uploads larger than max_upload_bytes should be denied."""
    tenant = _tenant()
    actor = _actor(tenant)
    action = _upload_action(tenant=tenant, actor=actor, size_bytes=2048)

    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert record.decision is PolicyDecision.DENY
    assert record.reason == "upload_size_out_of_bounds"


def test_policy_denies_unsupported_action_kind() -> None:
    """Unknown or unsupported action kinds should be denied."""
    tenant = _tenant()
    actor = _actor(tenant)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    action = Action(
        action_id="act-unknown",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.SUMMARIZE_PREFIX,
        target=ObjectRef(provider="s3", container="bucket", object_name="reports/"),
        status=ActionStatus.PROPOSED,
        idempotency_key="idem-summarize",
        input=None,
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )

    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=now,
    )

    assert record.decision is PolicyDecision.DENY
    assert record.reason == "unsupported_action_kind"


def test_policy_allows_list_files() -> None:
    """LIST_FILES should be allowed by default scope."""
    tenant = _tenant()
    actor = _actor(tenant)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    action = Action(
        action_id="act-list",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.LIST_FILES,
        target=ObjectRef(provider="s3", container="bucket", object_name=""),
        status=ActionStatus.PROPOSED,
        idempotency_key="idem-list",
        input=None,
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )

    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=now,
    )

    assert record.decision is PolicyDecision.ALLOW
    assert record.reason == "read_within_default_scope"


def test_policy_allows_list_files_workspace_scope() -> None:
    """LIST_FILES should be allowed with workspace scope reason."""
    tenant = _tenant()
    actor = _actor(tenant)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    action = Action(
        action_id="act-list",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.LIST_FILES,
        target=ObjectRef(provider="s3", container="bucket", object_name=""),
        status=ActionStatus.PROPOSED,
        idempotency_key="idem-list",
        input=None,
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )

    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(
            pinned_container="bucket",
            max_upload_bytes=1024,
            requested_scope="workspace",
            grants=(
                PolicyGrant(
                    actor_id=actor.user_id,
                    role=PolicyActorRole.WORKSPACE_ADMIN,
                ),
            ),
        ),
        now=now,
    )

    assert record.decision is PolicyDecision.ALLOW
    assert record.reason == "read_within_workspace_admin_scope"


def test_authorize_action_compat_wrapper() -> None:
    """authorize_action compat wrapper should return a PolicyDecision enum."""
    tenant = _tenant()
    actor = _actor(tenant)
    action = _upload_action(tenant=tenant, actor=actor)

    decision = authorize_action(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
    )

    assert decision is PolicyDecision.ALLOW


def test_approval_actor_ids_returns_self_for_non_destructive() -> None:
    """approval_actor_ids_for_action should return just the actor for non-risky kinds."""
    tenant = _tenant()
    actor = _actor(tenant)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    action = Action(
        action_id="act-list",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.LIST_FILES,
        target=ObjectRef(provider="s3", container="bucket", object_name=""),
        status=ActionStatus.PROPOSED,
        idempotency_key="idem-list",
        input=None,
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )

    approvers = approval_actor_ids_for_action(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=now,
    )

    assert approvers == (actor.user_id,)


def test_policy_target_for_delete_input() -> None:
    """_policy_target should use remote_path from DeleteFileInput."""
    tenant = _tenant()
    actor = _actor(tenant)
    action = _delete_action(tenant=tenant, actor=actor)
    # When target is not None, target.object_name is used.
    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert record.target == "reports/old.csv"


def test_policy_target_copy_move() -> None:
    """_policy_target should format source -> dest for copy/move."""
    tenant = _tenant()
    actor = _actor(tenant)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    action = Action(
        action_id="act-copy",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.COPY_FILE,
        target=None,
        status=ActionStatus.AWAITING_CONFIRMATION,
        idempotency_key="idem-copy",
        input=CopyFileInput(
            source_path="src.txt", dest_path="dst.txt", overwrite=False
        ),
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=now,
    )
    assert record.target == "src.txt -> dst.txt"


def test_policy_target_write_file() -> None:
    """_policy_target should include sha256 for write actions."""
    tenant = _tenant()
    actor = _actor(tenant)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    action = Action(
        action_id="act-write",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.WRITE_FILE,
        target=None,
        status=ActionStatus.AWAITING_CONFIRMATION,
        idempotency_key="idem-write",
        input=WriteFileInput(
            remote_path="notes.txt",
            content_base64="SGVsbG8=",
            content_sha256_hex="abc123",
            size_bytes=5,
            encoding="utf-8",
            overwrite=False,
        ),
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=now,
    )
    assert record.target == "notes.txt sha256:abc123"


def test_policy_target_falls_back_to_kind_value() -> None:
    """_policy_target should fall back to kind value when no matching input."""
    tenant = _tenant()
    actor = _actor(tenant)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    action = Action(
        action_id="act-list",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.LIST_FILES,
        target=None,
        status=ActionStatus.PROPOSED,
        idempotency_key="idem-list",
        input=None,
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    record = authorize_action_with_record(
        actor=actor,
        action=action,
        context=PolicyContext(pinned_container="bucket", max_upload_bytes=1024),
        now=now,
    )
    assert record.target == "list_files"
