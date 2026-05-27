"""Durable local store for Nimbus learning policy patch proposals."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import TYPE_CHECKING, cast

from nimbus_runtime.domain import ActorAuthSource, TenantIdentity, VerifiedActor
from nimbus_runtime.learning import (
    CapabilityDelta,
    CapabilityDeltaKind,
    PolicyPatch,
    PolicyPatchProposal,
    PolicyPatchStatus,
    PolicyVersionBinding,
    accept_policy_patch,
    reject_policy_patch,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

_STORE_FILENAME = "policy_patches.json"
_SCHEMA_VERSION = 1


class FilePolicyPatchStore:
    """JSON-backed policy patch store for local demos and single-process use."""

    def __init__(self, root: Path) -> None:
        """Create a store under ``root``."""
        self._path = root / _STORE_FILENAME
        self._lock = threading.RLock()

    def create_or_get(self, proposal: PolicyPatchProposal) -> PolicyPatchProposal:
        """Persist a proposal idempotently by its deterministic ID."""
        with self._lock:
            proposals = self._load()
            existing = proposals.get(proposal.proposal_id)
            if existing is not None:
                return existing
            proposals[proposal.proposal_id] = proposal
            self._write(proposals)
            return proposal

    def get(
        self,
        *,
        tenant: TenantIdentity,
        proposal_id: str,
    ) -> PolicyPatchProposal | None:
        """Return one tenant-scoped proposal."""
        with self._lock:
            proposal = self._load().get(proposal_id)
        if proposal is None or proposal.tenant != tenant:
            return None
        return proposal

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        limit: int = 100,
    ) -> tuple[PolicyPatchProposal, ...]:
        """Return recent proposals for one tenant."""
        with self._lock:
            proposals = [
                proposal
                for proposal in self._load().values()
                if proposal.tenant == tenant
            ]
        return tuple(
            sorted(proposals, key=lambda item: item.updated_at, reverse=True)[:limit]
        )

    def accept(  # noqa: PLR0913
        self,
        *,
        tenant: TenantIdentity,
        proposal_id: str,
        reviewer: VerifiedActor,
        current_policy: PolicyVersionBinding,
        now: datetime,
        decision_note: str | None = None,
    ) -> PolicyPatchProposal | None:
        """Accept a proposal if it exists and its policy binding is current."""
        with self._lock:
            proposals = self._load()
            proposal = proposals.get(proposal_id)
            if proposal is None or proposal.tenant != tenant:
                return None
            updated = accept_policy_patch(
                proposal,
                reviewer=reviewer,
                current_policy=current_policy,
                now=now,
                decision_note=decision_note,
            )
            proposals[proposal_id] = updated
            self._write(proposals)
            return updated

    def reject(
        self,
        *,
        tenant: TenantIdentity,
        proposal_id: str,
        reviewer: VerifiedActor,
        now: datetime,
        decision_note: str | None = None,
    ) -> PolicyPatchProposal | None:
        """Reject a proposal by its declared reviewer."""
        with self._lock:
            proposals = self._load()
            proposal = proposals.get(proposal_id)
            if proposal is None or proposal.tenant != tenant:
                return None
            updated = reject_policy_patch(
                proposal,
                reviewer=reviewer,
                now=now,
                decision_note=decision_note,
            )
            proposals[proposal_id] = updated
            self._write(proposals)
            return updated

    def _load(self) -> dict[str, PolicyPatchProposal]:
        if not self._path.exists():
            return {}
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != _SCHEMA_VERSION:
            msg = "unsupported policy patch store schema"
            raise ValueError(msg)
        proposals = data.get("proposals")
        if not isinstance(proposals, list):
            msg = "policy patch store proposals must be a list"
            raise TypeError(msg)
        loaded: dict[str, PolicyPatchProposal] = {}
        for item in proposals:
            proposal = _proposal_from_json(_mapping(item, field="proposal"))
            loaded[proposal.proposal_id] = proposal
        return loaded

    def _write(self, proposals: Mapping[str, PolicyPatchProposal]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "proposals": [
                _proposal_to_json(proposal)
                for proposal in sorted(
                    proposals.values(),
                    key=lambda item: item.proposal_id,
                )
            ],
        }
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(payload, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._path)


def _proposal_to_json(proposal: PolicyPatchProposal) -> dict[str, object]:
    return {
        "proposal_id": proposal.proposal_id,
        "tenant": _tenant_to_json(proposal.tenant),
        "patch": _patch_to_json(proposal.patch),
        "proposed_by": _actor_to_json(proposal.proposed_by),
        "status": proposal.status.value,
        "created_at": proposal.created_at.isoformat(),
        "updated_at": proposal.updated_at.isoformat(),
        "decided_by": (
            None if proposal.decided_by is None else _actor_to_json(proposal.decided_by)
        ),
        "decided_at": (
            None if proposal.decided_at is None else proposal.decided_at.isoformat()
        ),
        "decision_note": proposal.decision_note,
    }


def _proposal_from_json(data: Mapping[str, object]) -> PolicyPatchProposal:
    return PolicyPatchProposal(
        proposal_id=_required_str(data, "proposal_id"),
        tenant=_tenant_from_json(_mapping(data.get("tenant"), field="tenant")),
        patch=_patch_from_json(_mapping(data.get("patch"), field="patch")),
        proposed_by=_actor_from_json(_mapping(data.get("proposed_by"), field="actor")),
        status=PolicyPatchStatus(_required_str(data, "status")),
        created_at=_datetime_from_json(_required_str(data, "created_at")),
        updated_at=_datetime_from_json(_required_str(data, "updated_at")),
        decided_by=_optional_actor(data.get("decided_by")),
        decided_at=_optional_datetime(data.get("decided_at")),
        decision_note=_optional_str(data, "decision_note"),
    )


def _patch_to_json(patch: PolicyPatch) -> dict[str, object]:
    return {
        "tenant": _tenant_to_json(patch.tenant),
        "base_policy": _binding_to_json(patch.base_policy),
        "proposed_policy_version": patch.proposed_policy_version,
        "learning_signal_ids": list(patch.learning_signal_ids),
        "capability_deltas": [
            _delta_to_json(delta) for delta in patch.capability_deltas
        ],
        "reviewer": _actor_to_json(patch.reviewer),
        "rationale": patch.rationale,
        "authority_expansion_reason": patch.authority_expansion_reason,
        "metadata": dict(patch.metadata),
    }


def _patch_from_json(data: Mapping[str, object]) -> PolicyPatch:
    return PolicyPatch(
        tenant=_tenant_from_json(_mapping(data.get("tenant"), field="tenant")),
        base_policy=_binding_from_json(
            _mapping(data.get("base_policy"), field="base_policy")
        ),
        proposed_policy_version=_required_str(data, "proposed_policy_version"),
        learning_signal_ids=tuple(
            _string_sequence(
                data.get("learning_signal_ids"),
                field="learning_signal_ids",
            )
        ),
        capability_deltas=tuple(
            _delta_from_json(_mapping(item, field="capability_delta"))
            for item in _sequence(
                data.get("capability_deltas"),
                field="capability_deltas",
            )
        ),
        reviewer=_actor_from_json(_mapping(data.get("reviewer"), field="reviewer")),
        rationale=_required_str(data, "rationale"),
        authority_expansion_reason=_optional_str(data, "authority_expansion_reason"),
        metadata=_mapping(data.get("metadata"), field="metadata"),
    )


def _binding_to_json(binding: PolicyVersionBinding) -> dict[str, object]:
    return {
        "policy_version": binding.policy_version,
        "policy_digest": binding.policy_digest,
        "source": binding.source,
    }


def _binding_from_json(data: Mapping[str, object]) -> PolicyVersionBinding:
    return PolicyVersionBinding(
        policy_version=_required_str(data, "policy_version"),
        policy_digest=_optional_str(data, "policy_digest"),
        source=_optional_str(data, "source"),
    )


def _delta_to_json(delta: CapabilityDelta) -> dict[str, object]:
    return {
        "capability": delta.capability,
        "kind": delta.kind.value,
        "before": delta.before,
        "after": delta.after,
        "reason": delta.reason,
    }


def _delta_from_json(data: Mapping[str, object]) -> CapabilityDelta:
    return CapabilityDelta(
        capability=_required_str(data, "capability"),
        kind=CapabilityDeltaKind(_required_str(data, "kind")),
        before=_required_str(data, "before"),
        after=_required_str(data, "after"),
        reason=_required_str(data, "reason"),
    )


def _tenant_to_json(tenant: TenantIdentity) -> dict[str, object]:
    return {"platform": tenant.platform, "workspace_id": tenant.workspace_id}


def _tenant_from_json(data: Mapping[str, object]) -> TenantIdentity:
    return TenantIdentity(
        platform=_required_str(data, "platform"),
        workspace_id=_required_str(data, "workspace_id"),
    )


def _actor_to_json(actor: VerifiedActor) -> dict[str, object]:
    return {
        "tenant": _tenant_to_json(actor.tenant),
        "user_id": actor.user_id,
        "auth_source": actor.auth_source,
        "bridge_id": actor.bridge_id,
        "verified_at": actor.verified_at.isoformat(),
    }


def _actor_from_json(data: Mapping[str, object]) -> VerifiedActor:
    return VerifiedActor(
        tenant=_tenant_from_json(_mapping(data.get("tenant"), field="tenant")),
        user_id=_required_str(data, "user_id"),
        auth_source=cast("ActorAuthSource", _required_str(data, "auth_source")),
        bridge_id=_optional_str(data, "bridge_id"),
        verified_at=_datetime_from_json(_required_str(data, "verified_at")),
    )


def _optional_actor(value: object) -> VerifiedActor | None:
    if value is None:
        return None
    return _actor_from_json(_mapping(value, field="actor"))


def _datetime_from_json(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        msg = "expected optional datetime string"
        raise TypeError(msg)
    return _datetime_from_json(value)


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        msg = f"expected {field} to be an object"
        raise TypeError(msg)
    return cast("Mapping[str, object]", value)


def _sequence(value: object, *, field: str) -> Sequence[object]:
    if not isinstance(value, list):
        msg = f"expected {field} to be a list"
        raise TypeError(msg)
    return value


def _string_sequence(value: object, *, field: str) -> tuple[str, ...]:
    return tuple(
        _required_string_item(field, item) for item in _sequence(value, field=field)
    )


def _required_string_item(field: str, value: object) -> str:
    if not isinstance(value, str):
        msg = f"expected {field} item to be a string"
        raise TypeError(msg)
    return value


def _required_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        msg = f"expected string field {key!r}"
        raise TypeError(msg)
    return value


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"expected optional string field {key!r}"
    raise TypeError(msg)
