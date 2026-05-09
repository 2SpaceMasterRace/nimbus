"""Typed learning signals and policy patch proposal transitions.

The learning kernel is intentionally transport-neutral. It records typed
signals, binds proposed policy patches to a specific policy version, and keeps
authority changes explicit before a reviewer can accept them.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from nimbus_runtime.domain import TenantIdentity, VerifiedActor

type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)

_LEARNING_SIGNAL_PREFIX = "lsig-"
_POLICY_PATCH_PROPOSAL_PREFIX = "pprop-"
_DIGEST_PREFIX = "sha256:"


class LearningSignalSource(StrEnum):
    """Trusted sources that can produce learning signals."""

    HUMAN_FEEDBACK = "human_feedback"
    POLICY_DECISION = "policy_decision"
    APPROVAL_DECISION = "approval_decision"
    EVAL = "eval"
    INCIDENT = "incident"
    VERIFIER = "verifier"


class LearningSignalOutcome(StrEnum):
    """Normalized learning outcomes that may inform policy changes."""

    SAFE_ACCEPT = "safe_accept"
    SAFE_REJECT = "safe_reject"
    FALSE_ALLOW = "false_allow"
    FALSE_DENY = "false_deny"
    NEEDS_REVIEW = "needs_review"


class CapabilityDeltaKind(StrEnum):
    """How a policy patch changes one capability's authority."""

    GRANT = "grant"
    REVOKE = "revoke"
    TIGHTEN = "tighten"
    LOOSEN = "loosen"
    LIMIT = "limit"


class PolicyPatchStatus(StrEnum):
    """Durable lifecycle states for a policy patch proposal."""

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class PolicyPatchTransitionError(ValueError):
    """Raised when a policy patch proposal transition is invalid."""


@dataclass(frozen=True, slots=True)
class PolicyVersionBinding:
    """Specific policy version and optional digest a patch is based on."""

    policy_version: str
    policy_digest: str | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        """Validate policy binding fields."""
        _require_nonempty(self.policy_version, field_name="policy_version")
        if self.policy_digest is not None and not self.policy_digest.startswith(
            _DIGEST_PREFIX,
        ):
            msg = "policy_digest must start with 'sha256:'"
            raise ValueError(msg)

    def accepts(self, observed: PolicyVersionBinding) -> bool:
        """Return whether ``observed`` satisfies this binding."""
        if observed.policy_version != self.policy_version:
            return False
        if self.policy_digest is None:
            return True
        return observed.policy_digest == self.policy_digest


@dataclass(frozen=True, slots=True)
class LearningSignal:
    """Typed, idempotent signal that can justify a future policy patch."""

    signal_id: str
    tenant: TenantIdentity
    source: LearningSignalSource
    subject: str
    outcome: LearningSignalOutcome
    summary: str
    policy_binding: PolicyVersionBinding
    reported_by: VerifiedActor
    evidence_refs: tuple[str, ...]
    idempotency_key: str
    created_at: datetime
    confidence: float = 1.0
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate signal identity and tenant binding."""
        _require_nonempty(self.subject, field_name="subject")
        _require_nonempty(self.summary, field_name="summary")
        _require_nonempty(self.idempotency_key, field_name="idempotency_key")
        if self.reported_by.tenant != self.tenant:
            msg = "reported_by tenant must match learning signal tenant"
            raise ValueError(msg)
        if not 0.0 <= self.confidence <= 1.0:
            msg = "confidence must be between 0.0 and 1.0"
            raise ValueError(msg)
        evidence_refs = _canonical_nonempty_strings(
            self.evidence_refs,
            field_name="evidence_refs",
        )
        object.__setattr__(self, "evidence_refs", evidence_refs)
        expected = learning_signal_id_for(
            tenant=self.tenant,
            source=self.source,
            subject=self.subject,
            outcome=self.outcome,
            policy_binding=self.policy_binding,
            reported_by=self.reported_by,
            evidence_refs=evidence_refs,
            idempotency_key=self.idempotency_key,
        )
        if self.signal_id != expected:
            msg = f"signal_id must be deterministic: expected {expected}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class CapabilityDelta:
    """Declared authority change for one policy-controlled capability."""

    capability: str
    kind: CapabilityDeltaKind
    before: str
    after: str
    reason: str

    def __post_init__(self) -> None:
        """Validate a declared capability delta."""
        _require_nonempty(self.capability, field_name="capability")
        _require_nonempty(self.before, field_name="before")
        _require_nonempty(self.after, field_name="after")
        _require_nonempty(self.reason, field_name="reason")

    @property
    def expands_authority(self) -> bool:
        """Return whether this delta broadens policy authority."""
        return self.kind in {CapabilityDeltaKind.GRANT, CapabilityDeltaKind.LOOSEN}

    @property
    def stable_key(self) -> tuple[str, str, str, str]:
        """Return the deterministic identity key for this delta."""
        return (self.capability, self.kind.value, self.before, self.after)


@dataclass(frozen=True, slots=True)
class PolicyPatch:
    """Typed policy patch bound to learning evidence and a reviewer."""

    tenant: TenantIdentity
    base_policy: PolicyVersionBinding
    proposed_policy_version: str
    learning_signal_ids: tuple[str, ...]
    capability_deltas: tuple[CapabilityDelta, ...]
    reviewer: VerifiedActor
    rationale: str
    authority_expansion_reason: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate policy patch safety invariants."""
        _require_nonempty(
            self.proposed_policy_version,
            field_name="proposed_policy_version",
        )
        if self.proposed_policy_version == self.base_policy.policy_version:
            msg = "proposed_policy_version must differ from base policy version"
            raise ValueError(msg)
        _require_nonempty(self.rationale, field_name="rationale")
        if self.reviewer.tenant != self.tenant:
            msg = "reviewer tenant must match policy patch tenant"
            raise ValueError(msg)
        learning_signal_ids = _canonical_nonempty_strings(
            self.learning_signal_ids,
            field_name="learning_signal_ids",
        )
        object.__setattr__(self, "learning_signal_ids", learning_signal_ids)
        capability_deltas = _canonical_capability_deltas(self.capability_deltas)
        object.__setattr__(self, "capability_deltas", capability_deltas)
        if self.expands_authority and not self.authority_expansion_reason:
            msg = "authority_expansion_reason is required for authority expansion"
            raise ValueError(msg)

    @property
    def expands_authority(self) -> bool:
        """Return whether any declared capability delta expands authority."""
        return any(delta.expands_authority for delta in self.capability_deltas)


@dataclass(frozen=True, slots=True)
class PolicyPatchProposal:
    """Reviewable policy patch proposal with deterministic identity."""

    proposal_id: str
    tenant: TenantIdentity
    patch: PolicyPatch
    proposed_by: VerifiedActor
    status: PolicyPatchStatus
    created_at: datetime
    updated_at: datetime
    decided_by: VerifiedActor | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None

    def __post_init__(self) -> None:
        """Validate proposal state and deterministic identity."""
        if self.patch.tenant != self.tenant:
            msg = "patch tenant must match proposal tenant"
            raise ValueError(msg)
        if self.proposed_by.tenant != self.tenant:
            msg = "proposed_by tenant must match proposal tenant"
            raise ValueError(msg)
        expected = deterministic_policy_patch_proposal_id(
            patch=self.patch,
            proposed_by=self.proposed_by,
        )
        if self.proposal_id != expected:
            msg = f"proposal_id must be deterministic: expected {expected}"
            raise ValueError(msg)
        if self.updated_at < self.created_at:
            msg = "updated_at cannot be earlier than created_at"
            raise ValueError(msg)
        if self.status is PolicyPatchStatus.PROPOSED:
            if self.decided_by is not None or self.decided_at is not None:
                msg = "proposed policy patches cannot have decision data"
                raise ValueError(msg)
            return
        if self.decided_by is None or self.decided_at is None:
            msg = "terminal policy patches require decision data"
            raise ValueError(msg)
        if self.decided_by.principal_key != self.patch.reviewer.principal_key:
            msg = "decision actor must be the declared reviewer"
            raise ValueError(msg)


def learning_signal_id_for(  # noqa: PLR0913 - signal identity binds each audit axis explicitly.
    *,
    tenant: TenantIdentity,
    source: LearningSignalSource,
    subject: str,
    outcome: LearningSignalOutcome,
    policy_binding: PolicyVersionBinding,
    reported_by: VerifiedActor,
    evidence_refs: Sequence[str],
    idempotency_key: str,
) -> str:
    """Return the retry-stable ID for one learning signal."""
    seed = {
        "tenant_id": tenant.tenant_id,
        "source": source.value,
        "subject": subject,
        "outcome": outcome.value,
        "policy_binding": _policy_binding_seed(policy_binding),
        "reported_by": reported_by.principal_key,
        "evidence_refs": _canonical_nonempty_strings(
            tuple(evidence_refs),
            field_name="evidence_refs",
        ),
        "idempotency_key": idempotency_key,
    }
    return _prefixed_digest(prefix=_LEARNING_SIGNAL_PREFIX, seed=seed)


def record_learning_signal(  # noqa: PLR0913 - signal creation names each boundary field.
    *,
    tenant: TenantIdentity,
    source: LearningSignalSource,
    subject: str,
    outcome: LearningSignalOutcome,
    summary: str,
    policy_binding: PolicyVersionBinding,
    reported_by: VerifiedActor,
    evidence_refs: Sequence[str],
    idempotency_key: str,
    created_at: datetime,
    confidence: float = 1.0,
    metadata: Mapping[str, object] | None = None,
) -> LearningSignal:
    """Create a validated learning signal with a deterministic ID."""
    canonical_evidence_refs = _canonical_nonempty_strings(
        tuple(evidence_refs),
        field_name="evidence_refs",
    )
    signal_id = learning_signal_id_for(
        tenant=tenant,
        source=source,
        subject=subject,
        outcome=outcome,
        policy_binding=policy_binding,
        reported_by=reported_by,
        evidence_refs=canonical_evidence_refs,
        idempotency_key=idempotency_key,
    )
    return LearningSignal(
        signal_id=signal_id,
        tenant=tenant,
        source=source,
        subject=subject,
        outcome=outcome,
        summary=summary,
        policy_binding=policy_binding,
        reported_by=reported_by,
        evidence_refs=canonical_evidence_refs,
        idempotency_key=idempotency_key,
        created_at=created_at,
        confidence=confidence,
        metadata={} if metadata is None else metadata,
    )


def policy_patch_digest(patch: PolicyPatch) -> str:
    """Return a deterministic digest for the material patch contract."""
    return (
        _DIGEST_PREFIX
        + hashlib.sha256(
            _canonical_json_bytes(_policy_patch_seed(patch)),
        ).hexdigest()
    )


def deterministic_policy_patch_proposal_id(
    *,
    patch: PolicyPatch,
    proposed_by: VerifiedActor,
) -> str:
    """Return the retry-stable proposal ID for a policy patch."""
    if proposed_by.tenant != patch.tenant:
        msg = "proposed_by tenant must match policy patch tenant"
        raise ValueError(msg)
    seed = {
        "tenant_id": patch.tenant.tenant_id,
        "patch_digest": policy_patch_digest(patch),
        "proposed_by": proposed_by.principal_key,
    }
    return _prefixed_digest(prefix=_POLICY_PATCH_PROPOSAL_PREFIX, seed=seed)


def propose_policy_patch(
    *,
    patch: PolicyPatch,
    signals: Sequence[LearningSignal],
    proposed_by: VerifiedActor,
    now: datetime,
) -> PolicyPatchProposal:
    """Create a reviewable policy patch proposal from learning signals."""
    _validate_patch_signals(patch=patch, signals=signals)
    proposal_id = deterministic_policy_patch_proposal_id(
        patch=patch,
        proposed_by=proposed_by,
    )
    return PolicyPatchProposal(
        proposal_id=proposal_id,
        tenant=patch.tenant,
        patch=patch,
        proposed_by=proposed_by,
        status=PolicyPatchStatus.PROPOSED,
        created_at=now,
        updated_at=now,
    )


def accept_policy_patch(
    proposal: PolicyPatchProposal,
    *,
    reviewer: VerifiedActor,
    current_policy: PolicyVersionBinding,
    now: datetime,
    decision_note: str | None = None,
) -> PolicyPatchProposal:
    """Accept a proposed policy patch if the policy binding is still current."""
    _require_proposed(proposal)
    _require_declared_reviewer(proposal=proposal, reviewer=reviewer)
    if not proposal.patch.base_policy.accepts(current_policy):
        msg = (
            "policy patch is stale: expected "
            f"{proposal.patch.base_policy.policy_version}, observed "
            f"{current_policy.policy_version}"
        )
        raise PolicyPatchTransitionError(msg)
    return replace(
        proposal,
        status=PolicyPatchStatus.ACCEPTED,
        updated_at=now,
        decided_by=reviewer,
        decided_at=now,
        decision_note=decision_note,
    )


def reject_policy_patch(
    proposal: PolicyPatchProposal,
    *,
    reviewer: VerifiedActor,
    now: datetime,
    decision_note: str | None = None,
) -> PolicyPatchProposal:
    """Reject a proposed policy patch by its declared reviewer."""
    _require_proposed(proposal)
    _require_declared_reviewer(proposal=proposal, reviewer=reviewer)
    return replace(
        proposal,
        status=PolicyPatchStatus.REJECTED,
        updated_at=now,
        decided_by=reviewer,
        decided_at=now,
        decision_note=decision_note,
    )


def _validate_patch_signals(
    *,
    patch: PolicyPatch,
    signals: Sequence[LearningSignal],
) -> None:
    signal_ids = _canonical_nonempty_strings(
        tuple(signal.signal_id for signal in signals),
        field_name="signals",
    )
    if signal_ids != patch.learning_signal_ids:
        msg = "signals must exactly match patch.learning_signal_ids"
        raise ValueError(msg)
    for signal in signals:
        if signal.tenant != patch.tenant:
            msg = "learning signal tenant must match policy patch tenant"
            raise ValueError(msg)
        if not signal.policy_binding.accepts(patch.base_policy):
            msg = "learning signal policy binding must match patch base policy"
            raise ValueError(msg)


def _require_proposed(proposal: PolicyPatchProposal) -> None:
    if proposal.status is not PolicyPatchStatus.PROPOSED:
        msg = f"cannot transition policy patch from {proposal.status.value}"
        raise PolicyPatchTransitionError(msg)


def _require_declared_reviewer(
    *,
    proposal: PolicyPatchProposal,
    reviewer: VerifiedActor,
) -> None:
    if reviewer.principal_key != proposal.patch.reviewer.principal_key:
        msg = "only the declared reviewer can decide a policy patch"
        raise PolicyPatchTransitionError(msg)


def _policy_patch_seed(patch: PolicyPatch) -> dict[str, JsonValue]:
    return {
        "tenant_id": patch.tenant.tenant_id,
        "base_policy": _policy_binding_seed(patch.base_policy),
        "proposed_policy_version": patch.proposed_policy_version,
        "learning_signal_ids": list(patch.learning_signal_ids),
        "capability_deltas": [
            _capability_delta_seed(delta) for delta in patch.capability_deltas
        ],
        "reviewer": patch.reviewer.principal_key,
        "rationale": patch.rationale,
        "authority_expansion_reason": patch.authority_expansion_reason,
        "metadata": _to_jsonable(patch.metadata),
    }


def _policy_binding_seed(binding: PolicyVersionBinding) -> dict[str, JsonValue]:
    return {
        "policy_version": binding.policy_version,
        "policy_digest": binding.policy_digest,
        "source": binding.source,
    }


def _capability_delta_seed(delta: CapabilityDelta) -> dict[str, JsonValue]:
    return {
        "capability": delta.capability,
        "kind": delta.kind.value,
        "before": delta.before,
        "after": delta.after,
        "reason": delta.reason,
    }


def _canonical_capability_deltas(
    deltas: tuple[CapabilityDelta, ...],
) -> tuple[CapabilityDelta, ...]:
    if not deltas:
        msg = "capability_deltas must not be empty"
        raise ValueError(msg)
    keys = [delta.stable_key for delta in deltas]
    if len(set(keys)) != len(keys):
        msg = "capability_deltas must not contain duplicate capability changes"
        raise ValueError(msg)
    return tuple(sorted(deltas, key=lambda delta: delta.stable_key))


def _canonical_nonempty_strings(
    values: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if not values:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    stripped = tuple(
        _require_nonempty(value, field_name=field_name) for value in values
    )
    if len(set(stripped)) != len(stripped):
        msg = f"{field_name} must not contain duplicates"
        raise ValueError(msg)
    return tuple(sorted(stripped))


def _require_nonempty(value: str, *, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        msg = f"{field_name} is required"
        raise ValueError(msg)
    return stripped


def _prefixed_digest(*, prefix: str, seed: object) -> str:
    digest = hashlib.sha256(_canonical_json_bytes(seed)).hexdigest()
    return f"{prefix}{digest[:32]}"


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _to_jsonable(value: object) -> JsonValue:  # noqa: PLR0911
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _to_jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, frozenset | set):
        return sorted((_to_jsonable(item) for item in value), key=str)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
