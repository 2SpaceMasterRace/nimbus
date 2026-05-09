"""Unit tests for Nimbus action and event store primitives."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import nimbus_runtime.stores as stores_mod
import pytest
from nimbus_runtime.domain import (
    Action,
    ActionKind,
    ActionStatus,
    ActionTransition,
    Approval,
    ApprovalChoice,
    ApprovalStatus,
    Artifact,
    DeleteFileInput,
    DeleteFileResult,
    DeleteReport,
    ManifestObjectEntry,
    ManifestReport,
    ObjectRef,
    ObjectVerificationEntry,
    ObjectVerificationReport,
    Plan,
    PlanRiskLevel,
    PlanStatus,
    PlanTransition,
    PolicyDecision,
    PolicyDecisionRecord,
    RestorePlan,
    RestoreStrategy,
    TenantIdentity,
    UploadReport,
    VerifiedActor,
    validate_action_transition,
)
from nimbus_runtime.proof import artifact_payload_digest
from nimbus_runtime.stores import (
    FileActionStore,
    FileApprovalStore,
    FileArtifactStore,
    FilePlanStore,
    FileSessionEventStore,
)

pytestmark = pytest.mark.unit


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="slack", workspace_id="T123TEAM")


def _actor(tenant: TenantIdentity) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id="U123USER",
        auth_source="slack_signed_event",
        bridge_id="slack",
        verified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _delete_action(
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    action_id: str = "act-test",
    idempotency_key: str = "idem-test",
) -> Action:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Action(
        action_id=action_id,
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
        idempotency_key=idempotency_key,
        input=DeleteFileInput(remote_path="reports/old.csv"),
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def _delete_plan(
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    action_id: str = "act-test",
) -> Plan:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Plan(
        plan_id="plan-test",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        task_id=None,
        action_id=action_id,
        created_by=actor,
        status=PlanStatus.PROPOSED,
        risk_level=PlanRiskLevel.DESTRUCTIVE,
        title="Delete reports/old.csv",
        summary="Delete one S3 object after approval.",
        target=ObjectRef(
            provider="s3",
            container="bucket",
            object_name="reports/old.csv",
        ),
        estimated_count=1,
        estimated_bytes=None,
        idempotency_key="idem-plan",
        metadata={"operation": "delete_file"},
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def _delete_approval(
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    plan_id: str = "plan-test",
    action_id: str = "act-test",
) -> Approval:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Approval(
        approval_id="appr-test",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        task_id=None,
        plan_id=plan_id,
        action_id=action_id,
        requested_by=actor,
        required_actor_id=actor.user_id,
        allowed_actor_ids=(actor.user_id,),
        status=ApprovalStatus.PENDING,
        risk_level=PlanRiskLevel.DESTRUCTIVE,
        exact_target="reports/old.csv",
        reason="delete_file_requires_exact_actor_bound_approval",
        idempotency_key="idem-approval",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def _restore_plan() -> RestorePlan:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return RestorePlan(
        original_key="reports/old.csv",
        strategy=RestoreStrategy.S3_VERSION,
        restorable=True,
        trash_key=None,
        version_id="v-source",
        sha256_hex="0" * 64,
        size_bytes=42,
        deleted_by="U123USER",
        deleted_at=now,
        restore_command="Restore reports/old.csv from version v-source.",
        limitations=("Requires provider version restore permissions.",),
    )


def test_invalid_action_transition_is_rejected() -> None:
    """Terminal actions should not transition back to executing."""
    with pytest.raises(ValueError, match="invalid action transition"):
        validate_action_transition(
            expected=ActionStatus.SUCCEEDED,
            next_status=ActionStatus.EXECUTING,
        )


def test_file_action_store_creates_action_once_by_idempotency(
    tmp_path: Path,
) -> None:
    """Duplicate logical requests should return the original action."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    calls = 0

    def create() -> Action:
        nonlocal calls
        calls += 1
        return _delete_action(tenant=tenant, actor=actor)

    first = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=create,
    )
    second = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=create,
    )

    assert first == second
    assert calls == 1
    events = event_store.list_events(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
    )
    assert [event.event_type for event in events] == ["action_created"]


def test_file_action_store_round_trips_policy_decision_record(
    tmp_path: Path,
) -> None:
    """Actions should keep the policy decision that authorized their state."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    policy_decision = PolicyDecisionRecord(
        tenant_id=tenant.tenant_id,
        actor_id=actor.user_id,
        operation=ActionKind.DELETE_FILE.value,
        target="reports/old.csv",
        decision=PolicyDecision.REQUIRES_APPROVAL,
        reason="delete_requires_actor_or_delegate_approval",
        policy_version="runtime-policy-v1",
        created_at=now,
    )

    action = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=lambda: replace(
            _delete_action(tenant=tenant, actor=actor),
            policy_decision=policy_decision,
        ),
    )

    assert action.policy_decision == policy_decision
    stored = action_store.get(tenant=tenant, action_id=action.action_id)
    assert stored is not None
    assert stored.policy_decision == policy_decision
    events = event_store.list_events(tenant=tenant, session_id=action.session_id)
    assert events[0].payload["policy_decision"] == "requires_approval"


def test_file_action_store_transition_is_compare_and_set(tmp_path: Path) -> None:
    """Only callers that see the expected state should move an action."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    action = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=lambda: _delete_action(tenant=tenant, actor=actor),
    )

    authorized = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.AWAITING_CONFIRMATION,
            next_status=ActionStatus.AUTHORIZED,
            event_type="action_authorized",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )
    stale = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.AWAITING_CONFIRMATION,
            next_status=ActionStatus.AUTHORIZED,
            event_type="action_authorized",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )

    assert authorized is not None
    assert authorized.status is ActionStatus.AUTHORIZED
    assert stale is None
    events = event_store.list_events(
        tenant=tenant,
        session_id=action.session_id,
    )
    assert [event.event_type for event in events] == [
        "action_created",
        "action_authorized",
    ]


def test_file_artifact_store_persists_artifacts_and_appends_events(
    tmp_path: Path,
) -> None:
    """Artifacts should be durable evidence linked back into the session log."""
    event_store = FileSessionEventStore(tmp_path)
    artifact_store = FileArtifactStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    artifact = Artifact(
        artifact_id="art-test",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        action_id="act-test",
        kind="upload_report",
        uri=None,
        payload=UploadReport(
            remote_path="reports/new.csv",
            filename="new.csv",
            size_bytes=12,
            sha256_hex="0" * 64,
        ),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    created = artifact_store.create(artifact=artifact, actor=actor)
    repeated = artifact_store.create(artifact=artifact, actor=actor)

    assert created == replace(
        artifact,
        payload_digest=artifact_payload_digest(artifact.payload),
    )
    assert repeated == created
    assert artifact_store.list_for_session(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
    ) == (created,)
    events = event_store.list_events(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
    )
    assert [event.event_type for event in events] == ["artifact_created"]


def test_file_artifact_store_round_trips_delete_report_restore_plan(
    tmp_path: Path,
) -> None:
    """Delete reports should keep their restore story as typed evidence."""
    event_store = FileSessionEventStore(tmp_path)
    artifact_store = FileArtifactStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    restore_plan = _restore_plan()
    artifact = Artifact(
        artifact_id="art-delete",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        action_id="act-test",
        kind="delete_report",
        uri=None,
        payload=DeleteReport(
            remote_path="reports/old.csv",
            deleted=True,
            version_id="v-delete-marker",
            restore_plan=restore_plan,
        ),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    artifact_store.create(artifact=artifact, actor=actor)

    [created] = artifact_store.list_for_session(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
    )
    assert created.payload == DeleteReport(
        remote_path="reports/old.csv",
        deleted=True,
        version_id="v-delete-marker",
        restore_plan=restore_plan,
    )


def test_file_artifact_store_round_trips_manifest_and_verifier_artifacts(
    tmp_path: Path,
) -> None:
    """Workflow evidence artifacts should round-trip as typed payloads."""
    event_store = FileSessionEventStore(tmp_path)
    artifact_store = FileArtifactStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    session_id = "slack:T123TEAM:C123CHAN:thread"
    verifier = Artifact(
        artifact_id="art-verifier",
        tenant=tenant,
        session_id=session_id,
        action_id=None,
        kind="verification_report",
        uri=None,
        payload=ObjectVerificationReport(
            verifier="sha256_size",
            subject="channel_backup",
            verified=True,
            entries=(
                ObjectVerificationEntry(
                    file_id="F1",
                    object_key="archives/file.pdf",
                    size_bytes=12,
                    sha256_hex="0" * 64,
                    verified=True,
                ),
            ),
        ),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    manifest = Artifact(
        artifact_id="art-manifest",
        tenant=tenant,
        session_id=session_id,
        action_id=None,
        kind="manifest",
        uri=None,
        payload=ManifestReport(
            source_platform="slack",
            workspace_id="T123TEAM",
            channel_id="C123CHAN",
            destination_container="bucket",
            destination_prefix="archives",
            scanned_count=1,
            matched_count=1,
            total_count=1,
            truncated=False,
            object_entries=(
                ManifestObjectEntry(
                    file_id="F1",
                    name="file.pdf",
                    object_key="archives/file.pdf",
                    size_bytes=12,
                    sha256_hex="0" * 64,
                    disposition="uploaded",
                ),
            ),
            failed_files=(),
            verifier_artifact_id="art-verifier",
        ),
        created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
    )

    artifact_store.create(artifact=verifier, actor=actor)
    artifact_store.create(artifact=manifest, actor=actor)

    assert artifact_store.list_for_session(
        tenant=tenant,
        session_id=session_id,
    ) == (
        replace(verifier, payload_digest=artifact_payload_digest(verifier.payload)),
        replace(manifest, payload_digest=artifact_payload_digest(manifest.payload)),
    )
    events = event_store.list_events(tenant=tenant, session_id=session_id)
    assert [event.event_type for event in events] == [
        "artifact_created",
        "artifact_created",
    ]


def test_artifact_create_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifacts and their creation events should commit together."""
    event_store = FileSessionEventStore(tmp_path)
    artifact_store = FileArtifactStore(tmp_path, event_store=event_store)
    tenant = _tenant()

    def fail_append(*_args: object, **_kwargs: object) -> None:
        msg = "simulated event write failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(stores_mod, "_append_event", fail_append)

    artifact = Artifact(
        artifact_id="art-test",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        action_id="act-test",
        kind="upload_report",
        uri=None,
        payload=UploadReport(
            remote_path="reports/new.csv",
            filename="new.csv",
            size_bytes=12,
            sha256_hex="0" * 64,
        ),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="simulated event write failure"):
        artifact_store.create(artifact=artifact, actor=_actor(tenant))

    assert (
        artifact_store.list_for_session(
            tenant=tenant,
            session_id="slack:T123TEAM:C123CHAN:thread",
        )
        == ()
    )


def test_action_transition_persists_typed_result(tmp_path: Path) -> None:
    """Action results should round-trip as typed payloads, not stringly dicts."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    action = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=lambda: _delete_action(tenant=tenant, actor=actor),
    )

    authorized = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.AWAITING_CONFIRMATION,
            next_status=ActionStatus.AUTHORIZED,
            event_type="action_authorized",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )
    assert authorized is not None
    executing = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.AUTHORIZED,
            next_status=ActionStatus.EXECUTING,
            event_type="action_started",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )
    assert executing is not None
    verifying = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.EXECUTING,
            next_status=ActionStatus.VERIFYING,
            event_type="verification_started",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )
    assert verifying is not None
    completed = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.VERIFYING,
            next_status=ActionStatus.SUCCEEDED,
            event_type="action_completed",
            event_payload={"remote_path": "reports/old.csv"},
            result=DeleteFileResult(
                remote_path="reports/old.csv",
                deleted=True,
                version_id=None,
                artifact_id="art-test",
            ),
        ),
    )

    assert completed is not None
    assert completed.result == DeleteFileResult(
        remote_path="reports/old.csv",
        deleted=True,
        version_id=None,
        artifact_id="art-test",
    )
    assert action_store.get(tenant=tenant, action_id=action.action_id) == completed


def test_action_create_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action creation and event append should share one transaction."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)

    def fail_append(*_args: object, **_kwargs: object) -> None:
        msg = "simulated event write failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(stores_mod, "_append_event", fail_append)

    with pytest.raises(RuntimeError, match="simulated event write failure"):
        action_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key="idem-test",
            create=lambda: _delete_action(tenant=tenant, actor=actor),
        )

    assert (
        action_store.list_for_session(
            tenant=tenant,
            session_id="slack:T123TEAM:C123CHAN:thread",
        )
        == ()
    )


def test_action_transition_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed audit writes must not leave the action in a new state."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    action = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=lambda: _delete_action(tenant=tenant, actor=actor),
    )

    def fail_append(*_args: object, **_kwargs: object) -> None:
        msg = "simulated event write failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(stores_mod, "_append_event", fail_append)

    with pytest.raises(RuntimeError, match="simulated event write failure"):
        action_store.transition(
            tenant=tenant,
            action_id=action.action_id,
            transition=ActionTransition(
                expected=ActionStatus.AWAITING_CONFIRMATION,
                next_status=ActionStatus.AUTHORIZED,
                event_type="action_authorized",
                event_payload={"remote_path": "reports/old.csv"},
            ),
        )

    current = action_store.get(tenant=tenant, action_id=action.action_id)
    assert current is not None
    assert current.status is ActionStatus.AWAITING_CONFIRMATION


def test_file_plan_store_creates_and_transitions_plan_once(
    tmp_path: Path,
) -> None:
    """Plans should be idempotent previews with compare-and-set transitions."""
    event_store = FileSessionEventStore(tmp_path)
    plan_store = FilePlanStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    calls = 0

    def create() -> Plan:
        nonlocal calls
        calls += 1
        return _delete_plan(tenant=tenant, actor=actor)

    first = plan_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-plan",
        create=create,
    )
    second = plan_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-plan",
        create=create,
    )
    approved = plan_store.transition(
        tenant=tenant,
        plan_id=first.plan_id,
        transition=PlanTransition(
            expected=PlanStatus.PROPOSED,
            next_status=PlanStatus.APPROVED,
            event_type="plan_approved",
            event_payload={"action_id": first.action_id},
        ),
    )
    stale = plan_store.transition(
        tenant=tenant,
        plan_id=first.plan_id,
        transition=PlanTransition(
            expected=PlanStatus.PROPOSED,
            next_status=PlanStatus.REJECTED,
            event_type="plan_rejected",
            event_payload={"action_id": first.action_id},
        ),
    )

    assert first == second
    assert calls == 1
    assert approved is not None
    assert approved.status is PlanStatus.APPROVED
    assert stale is None
    assert plan_store.get(tenant=tenant, plan_id=first.plan_id) == approved
    assert plan_store.list_for_session(
        tenant=tenant,
        session_id=first.session_id,
    ) == (approved,)
    events = event_store.list_events(tenant=tenant, session_id=first.session_id)
    assert [event.event_type for event in events] == [
        "plan_created",
        "plan_approved",
    ]


def test_file_approval_store_binds_actor_target_and_duplicate_clicks(
    tmp_path: Path,
) -> None:
    """Approval decisions should fail closed unless actor and target match."""
    event_store = FileSessionEventStore(tmp_path)
    approval_store = FileApprovalStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    other_actor = VerifiedActor(
        tenant=tenant,
        user_id="U999OTHER",
        auth_source="slack_signed_event",
        bridge_id="slack",
        verified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    approval = approval_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-approval",
        create=lambda: _delete_approval(tenant=tenant, actor=actor),
    )

    wrong_actor = approval_store.decide(
        tenant=tenant,
        approval_id=approval.approval_id,
        actor=other_actor,
        choice=ApprovalChoice.APPROVE,
        exact_target="reports/old.csv",
        now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )
    wrong_target = approval_store.decide(
        tenant=tenant,
        approval_id=approval.approval_id,
        actor=actor,
        choice=ApprovalChoice.APPROVE,
        exact_target="reports/new.csv",
        now=datetime(2026, 1, 1, 0, 2, tzinfo=UTC),
    )
    approved = approval_store.decide(
        tenant=tenant,
        approval_id=approval.approval_id,
        actor=actor,
        choice=ApprovalChoice.APPROVE,
        exact_target="reports/old.csv",
        now=datetime(2026, 1, 1, 0, 3, tzinfo=UTC),
        note="looks right",
    )
    duplicate_click = approval_store.decide(
        tenant=tenant,
        approval_id=approval.approval_id,
        actor=actor,
        choice=ApprovalChoice.APPROVE,
        exact_target="reports/old.csv",
        now=datetime(2026, 1, 1, 0, 4, tzinfo=UTC),
    )

    assert wrong_actor.accepted is False
    assert wrong_actor.reason == "wrong_actor"
    assert wrong_actor.approval is not None
    assert wrong_actor.approval.status is ApprovalStatus.PENDING
    assert wrong_target.accepted is False
    assert wrong_target.reason == "target_mismatch"
    assert approved.accepted is True
    assert approved.reason == "approved"
    assert approved.approval is not None
    assert approved.approval.status is ApprovalStatus.APPROVED
    assert approved.approval.decided_by == actor
    assert approved.approval.decision_note == "looks right"
    assert duplicate_click.accepted is False
    assert duplicate_click.reason == "already_decided"
    events = event_store.list_events(tenant=tenant, session_id=approval.session_id)
    assert [event.event_type for event in events] == [
        "approval_requested",
        "approval_decision_failed",
        "approval_decision_failed",
        "approval_decided",
        "approval_decision_failed",
    ]


def test_file_approval_store_reject_and_expire_paths(
    tmp_path: Path,
) -> None:
    """Reject and expiry are terminal approval outcomes with durable events."""
    event_store = FileSessionEventStore(tmp_path)
    approval_store = FileApprovalStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    rejected = approval_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-reject",
        create=lambda: replace(
            _delete_approval(tenant=tenant, actor=actor),
            approval_id="appr-reject",
            idempotency_key="idem-reject",
        ),
    )
    expired = approval_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-expired",
        create=lambda: replace(
            _delete_approval(tenant=tenant, actor=actor),
            approval_id="appr-expired",
            idempotency_key="idem-expired",
            expires_at=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        ),
    )

    reject_decision = approval_store.decide(
        tenant=tenant,
        approval_id=rejected.approval_id,
        actor=actor,
        choice=ApprovalChoice.REJECT,
        exact_target="reports/old.csv",
        now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )
    expired_decision = approval_store.decide(
        tenant=tenant,
        approval_id=expired.approval_id,
        actor=actor,
        choice=ApprovalChoice.APPROVE,
        exact_target="reports/old.csv",
        now=datetime(2026, 1, 1, 0, 6, tzinfo=UTC),
    )

    assert reject_decision.accepted is True
    assert reject_decision.reason == "rejected"
    assert reject_decision.approval is not None
    assert reject_decision.approval.status is ApprovalStatus.REJECTED
    assert expired_decision.accepted is False
    assert expired_decision.reason == "expired"
    assert expired_decision.approval is not None
    assert expired_decision.approval.status is ApprovalStatus.EXPIRED
    events = event_store.list_events(tenant=tenant, session_id=rejected.session_id)
    assert [event.event_type for event in events] == [
        "approval_requested",
        "approval_requested",
        "approval_decided",
        "approval_expired",
    ]
