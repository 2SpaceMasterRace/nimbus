"""Tests for runtime learning signals and policy patch proposals."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nimbus_runtime.domain import TenantIdentity, VerifiedActor
from nimbus_runtime.learning import (
    CapabilityDelta,
    CapabilityDeltaKind,
    LearningSignal,
    LearningSignalOutcome,
    LearningSignalSource,
    PolicyPatch,
    PolicyPatchProposal,
    PolicyPatchStatus,
    PolicyPatchTransitionError,
    PolicyVersionBinding,
    accept_policy_patch,
    deterministic_policy_patch_proposal_id,
    propose_policy_patch,
    record_learning_signal,
    reject_policy_patch,
)
from nimbus_runtime.learning_store import FilePolicyPatchStore

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
        verified_at=datetime(2026, 5, 21, tzinfo=UTC),
    )


def _policy_binding(
    version: str = "runtime-policy-v1",
    *,
    digest: str | None = "sha256:" + ("a" * 64),
) -> PolicyVersionBinding:
    return PolicyVersionBinding(
        policy_version=version,
        policy_digest=digest,
        source="unit-test",
    )


def _signal(
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    binding: PolicyVersionBinding,
    idempotency_key: str = "evt-1",
    evidence_refs: tuple[str, ...] = ("approval:1", "artifact:1"),
) -> LearningSignal:
    return record_learning_signal(
        tenant=tenant,
        source=LearningSignalSource.APPROVAL_DECISION,
        subject="delete_file",
        outcome=LearningSignalOutcome.FALSE_DENY,
        summary="Reviewer approved a previously blocked delete.",
        policy_binding=binding,
        reported_by=actor,
        evidence_refs=evidence_refs,
        idempotency_key=idempotency_key,
        created_at=datetime(2026, 5, 21, tzinfo=UTC),
    )


def _delta(
    capability: str = "delete_file",
    *,
    kind: CapabilityDeltaKind = CapabilityDeltaKind.TIGHTEN,
) -> CapabilityDelta:
    return CapabilityDelta(
        capability=capability,
        kind=kind,
        before="scope=workspace",
        after="scope=current_channel",
        reason=f"{capability} authority changed by learning signal",
    )


def _patch(
    *,
    tenant: TenantIdentity,
    signals: tuple[LearningSignal, ...],
    reviewer: VerifiedActor,
    binding: PolicyVersionBinding,
    deltas: tuple[CapabilityDelta, ...] = (_delta(),),
    authority_expansion_reason: str | None = None,
) -> PolicyPatch:
    return PolicyPatch(
        tenant=tenant,
        base_policy=binding,
        proposed_policy_version="runtime-policy-v2",
        learning_signal_ids=tuple(signal.signal_id for signal in signals),
        capability_deltas=deltas,
        reviewer=reviewer,
        rationale="Tighten policy around a reviewed storage action.",
        authority_expansion_reason=authority_expansion_reason,
    )


def _proposal() -> tuple[PolicyVersionBinding, VerifiedActor, PolicyPatchProposal]:
    tenant = _tenant()
    proposer = _actor(tenant, user_id="UPROPOSER")
    reviewer = _actor(tenant, user_id="UREVIEWER")
    binding = _policy_binding()
    signal = _signal(tenant=tenant, actor=proposer, binding=binding)
    patch = _patch(
        tenant=tenant,
        signals=(signal,),
        reviewer=reviewer,
        binding=binding,
    )
    proposal = propose_policy_patch(
        patch=patch,
        signals=(signal,),
        proposed_by=proposer,
        now=datetime(2026, 5, 21, tzinfo=UTC),
    )
    return binding, reviewer, proposal


def _decide_for_test(
    transition: str,
    proposal: PolicyPatchProposal,
    *,
    reviewer: VerifiedActor,
    binding: PolicyVersionBinding,
    now: datetime,
) -> PolicyPatchProposal:
    if transition == "accept":
        return accept_policy_patch(
            proposal,
            reviewer=reviewer,
            current_policy=binding,
            now=now,
        )
    return reject_policy_patch(
        proposal,
        reviewer=reviewer,
        now=now,
    )


def test_learning_signal_id_is_retry_stable_and_order_insensitive() -> None:
    """The same trusted signal should converge to one deterministic ID."""
    tenant = _tenant()
    actor = _actor(tenant)
    binding = _policy_binding()

    first = _signal(
        tenant=tenant,
        actor=actor,
        binding=binding,
        evidence_refs=("artifact:1", "approval:1"),
    )
    second = _signal(
        tenant=tenant,
        actor=actor,
        binding=binding,
        evidence_refs=("approval:1", "artifact:1"),
    )

    assert first.signal_id == second.signal_id
    assert first.evidence_refs == ("approval:1", "artifact:1")


def test_file_policy_patch_store_round_trips_acceptance(tmp_path: Path) -> None:
    """Local proposal review should survive process boundaries."""
    binding, reviewer, proposal = _proposal()
    store = FilePolicyPatchStore(tmp_path)

    created = store.create_or_get(proposal)
    reloaded = FilePolicyPatchStore(tmp_path).get(
        tenant=proposal.tenant,
        proposal_id=proposal.proposal_id,
    )
    accepted = FilePolicyPatchStore(tmp_path).accept(
        tenant=proposal.tenant,
        proposal_id=proposal.proposal_id,
        reviewer=reviewer,
        current_policy=binding,
        now=datetime(2026, 5, 21, 1, tzinfo=UTC),
    )

    assert created.proposal_id == proposal.proposal_id
    assert reloaded == proposal
    assert accepted is not None
    assert accepted.status is PolicyPatchStatus.ACCEPTED
    assert accepted.decided_by == reviewer


def test_file_policy_patch_store_rejects_and_filters_by_tenant(tmp_path: Path) -> None:
    """The local store preserves tenant boundaries and reject decisions."""
    _binding, reviewer, proposal = _proposal()
    other_tenant = _tenant("OTHER")
    store = FilePolicyPatchStore(tmp_path)
    store.create_or_get(proposal)

    assert store.get(tenant=other_tenant, proposal_id=proposal.proposal_id) is None
    assert store.list_for_tenant(tenant=other_tenant) == ()

    rejected = store.reject(
        tenant=proposal.tenant,
        proposal_id=proposal.proposal_id,
        reviewer=reviewer,
        now=datetime(2026, 5, 21, 2, tzinfo=UTC),
        decision_note="not this week",
    )

    assert rejected is not None
    assert rejected.status is PolicyPatchStatus.REJECTED
    assert rejected.decision_note == "not this week"


def test_file_policy_patch_store_rejects_corrupt_schema(tmp_path: Path) -> None:
    """Corrupt local proposal state fails explicitly instead of being ignored."""
    (tmp_path / "policy_patches.json").write_text(
        '{"schema_version": 999, "proposals": []}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported"):
        FilePolicyPatchStore(tmp_path).list_for_tenant(tenant=_tenant())


@given(st.permutations(("delete_file", "write_file", "move_file")))
@pytest.mark.property
def test_policy_patch_proposal_id_is_deterministic(
    capability_order: tuple[str, ...],
) -> None:
    """Proposal IDs should not depend on signal or capability delta ordering."""
    tenant = _tenant()
    proposer = _actor(tenant, user_id="UPROPOSER")
    reviewer = _actor(tenant, user_id="UREVIEWER")
    binding = _policy_binding()
    signals = (
        _signal(
            tenant=tenant,
            actor=proposer,
            binding=binding,
            idempotency_key="evt-1",
        ),
        _signal(
            tenant=tenant,
            actor=proposer,
            binding=binding,
            idempotency_key="evt-2",
            evidence_refs=("artifact:2",),
        ),
    )
    deltas = tuple(_delta(capability) for capability in capability_order)

    first_patch = _patch(
        tenant=tenant,
        signals=signals,
        reviewer=reviewer,
        binding=binding,
        deltas=deltas,
    )
    second_patch = _patch(
        tenant=tenant,
        signals=tuple(reversed(signals)),
        reviewer=reviewer,
        binding=binding,
        deltas=tuple(reversed(deltas)),
    )

    assert deterministic_policy_patch_proposal_id(
        patch=first_patch,
        proposed_by=proposer,
    ) == deterministic_policy_patch_proposal_id(
        patch=second_patch,
        proposed_by=proposer,
    )


def test_policy_patch_requires_declared_capability_delta() -> None:
    """Policy patches cannot hide authority changes behind metadata."""
    tenant = _tenant()
    proposer = _actor(tenant)
    reviewer = _actor(tenant, user_id="UREVIEWER")
    binding = _policy_binding()
    signal = _signal(tenant=tenant, actor=proposer, binding=binding)

    with pytest.raises(ValueError, match="capability_deltas must not be empty"):
        _patch(
            tenant=tenant,
            signals=(signal,),
            reviewer=reviewer,
            binding=binding,
            deltas=(),
        )


def test_authority_expansion_requires_explicit_reason() -> None:
    """Expanding authority must be declared and justified before review."""
    tenant = _tenant()
    proposer = _actor(tenant)
    reviewer = _actor(tenant, user_id="UREVIEWER")
    binding = _policy_binding()
    signal = _signal(tenant=tenant, actor=proposer, binding=binding)

    with pytest.raises(ValueError, match="authority_expansion_reason"):
        _patch(
            tenant=tenant,
            signals=(signal,),
            reviewer=reviewer,
            binding=binding,
            deltas=(_delta("write_file", kind=CapabilityDeltaKind.LOOSEN),),
        )


def test_patch_reviewer_must_belong_to_patch_tenant() -> None:
    """The declared reviewer is tenant-scoped policy authority."""
    tenant = _tenant()
    proposer = _actor(tenant)
    other_reviewer = _actor(_tenant("T999TEAM"), user_id="UREVIEWER")
    binding = _policy_binding()
    signal = _signal(tenant=tenant, actor=proposer, binding=binding)

    with pytest.raises(ValueError, match="reviewer tenant"):
        _patch(
            tenant=tenant,
            signals=(signal,),
            reviewer=other_reviewer,
            binding=binding,
        )


def test_accept_policy_patch_binds_current_policy_version() -> None:
    """Acceptance should only succeed against the bound base policy."""
    binding, reviewer, proposal = _proposal()

    accepted = accept_policy_patch(
        proposal,
        reviewer=reviewer,
        current_policy=binding,
        now=datetime(2026, 5, 21, 0, 1, tzinfo=UTC),
        decision_note="Accepted after review.",
    )

    assert accepted.status is PolicyPatchStatus.ACCEPTED
    assert accepted.proposal_id == proposal.proposal_id
    assert accepted.decided_by == reviewer
    assert accepted.decision_note == "Accepted after review."


def test_reject_policy_patch_records_declared_reviewer() -> None:
    """Rejection is terminal evidence from the declared reviewer."""
    _, reviewer, proposal = _proposal()

    rejected = reject_policy_patch(
        proposal,
        reviewer=reviewer,
        now=datetime(2026, 5, 21, 0, 1, tzinfo=UTC),
        decision_note="Too broad for this channel.",
    )

    assert rejected.status is PolicyPatchStatus.REJECTED
    assert rejected.decided_by == reviewer
    assert rejected.decision_note == "Too broad for this channel."


@pytest.mark.parametrize("transition", ["accept", "reject"])
@pytest.mark.parametrize("terminal_status", ["accepted", "rejected"])
def test_terminal_policy_patch_proposals_reject_invalid_transitions(
    transition: str,
    terminal_status: str,
) -> None:
    """Accepted and rejected proposals cannot be decided twice."""
    binding, reviewer, proposal = _proposal()
    now = datetime(2026, 5, 21, 0, 1, tzinfo=UTC)
    terminal = (
        accept_policy_patch(
            proposal,
            reviewer=reviewer,
            current_policy=binding,
            now=now,
        )
        if terminal_status == "accepted"
        else reject_policy_patch(
            proposal,
            reviewer=reviewer,
            now=now,
        )
    )

    with pytest.raises(PolicyPatchTransitionError, match="cannot transition"):
        _decide_for_test(
            transition,
            terminal,
            reviewer=reviewer,
            binding=binding,
            now=now + timedelta(minutes=1),
        )


def test_accept_policy_patch_rejects_wrong_reviewer() -> None:
    """Only the declared reviewer can decide a proposal."""
    binding, _, proposal = _proposal()
    wrong_reviewer = _actor(_tenant(), user_id="UOTHER")

    with pytest.raises(PolicyPatchTransitionError, match="declared reviewer"):
        accept_policy_patch(
            proposal,
            reviewer=wrong_reviewer,
            current_policy=binding,
            now=datetime(2026, 5, 21, 0, 1, tzinfo=UTC),
        )


def test_accept_policy_patch_rejects_stale_policy_binding() -> None:
    """Stale proposals should fail closed instead of applying to new policy."""
    _, reviewer, proposal = _proposal()

    with pytest.raises(PolicyPatchTransitionError, match="stale"):
        accept_policy_patch(
            proposal,
            reviewer=reviewer,
            current_policy=_policy_binding("runtime-policy-v9"),
            now=datetime(2026, 5, 21, 0, 1, tzinfo=UTC),
        )
