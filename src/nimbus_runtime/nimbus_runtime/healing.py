"""S3 replica-lane healing proposals and repair receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from nimbus_runtime.domain import RepairReceipt
from nimbus_runtime.proof import digest_value

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from nimbus_runtime.domain import GenerationManifest, ObjectPointer, TenantIdentity

_PERFECT_HEALTH = 100


class ReplicaRepairClient(Protocol):
    """Capability protocol for policy-authorized replica repair."""

    def copy_object(
        self,
        *,
        source_container: str,
        source_object_name: str,
        destination_container: str,
        destination_object_name: str,
    ) -> None:
        """Copy one source object to a replica destination."""

    def object_sha256(self, *, container: str, object_name: str) -> str | None:
        """Return the best available SHA-256 for one object."""


@dataclass(frozen=True, slots=True)
class ReplicaLane:
    """Configured S3 replica lane for one protected root."""

    lane_id: str
    tenant: TenantIdentity
    root_id: str
    provider: str
    source_container: str
    source_prefix: str
    replica_container: str
    replica_prefix: str
    policy_allows_missing_replica_repair: bool
    created_at: datetime
    metadata: Mapping[str, object]

    def __post_init__(self) -> None:
        """Validate the supported MVP provider and repair policy."""
        if self.provider != "s3":
            msg = "replica lane repair is S3-only in this Nimbus MVP"
            raise ValueError(msg)
        if not self.lane_id:
            msg = "lane_id is required"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class HealingObject:
    """Per-object replica health decision."""

    source_object_name: str
    replica_object_name: str
    source_sha256: str | None
    replica_sha256: str | None
    status: str
    next_step: str


@dataclass(frozen=True, slots=True)
class HealingProposal:
    """Reviewable healing proposal for a replica lane."""

    proposal_id: str
    lane: ReplicaLane
    status: str
    health_score: int
    missing_replica_count: int
    checksum_mismatch_count: int
    ambiguous_count: int
    objects: tuple[HealingObject, ...]
    created_at: datetime
    next_step: str


def replica_lane_id(  # noqa: PLR0913
    *,
    tenant: TenantIdentity,
    root_id: str,
    source_container: str,
    source_prefix: str,
    replica_container: str,
    replica_prefix: str,
) -> str:
    """Return the deterministic ID for a replica lane."""
    digest = digest_value(
        {
            "tenant_id": tenant.tenant_id,
            "root_id": root_id,
            "source_container": source_container,
            "source_prefix": source_prefix,
            "replica_container": replica_container,
            "replica_prefix": replica_prefix,
        }
    )
    return f"lane-{digest.removeprefix('sha256:')[:24]}"


def evaluate_replica_lane(
    *,
    lane: ReplicaLane,
    source_manifest: GenerationManifest,
    replica_manifest: GenerationManifest,
    now: datetime,
) -> HealingProposal:
    """Compare source and replica manifests and propose safe repair work."""
    source_by_name = {
        pointer.object_name: pointer for pointer in source_manifest.objects
    }
    replica_by_name = {
        _source_name_for_replica(
            lane=lane,
            replica_object_name=pointer.object_name,
        ): pointer
        for pointer in replica_manifest.objects
    }
    objects: list[HealingObject] = []
    for source_name, source in sorted(source_by_name.items()):
        replica_name = _replica_name_for_source(
            lane=lane,
            source_object_name=source_name,
        )
        replica = replica_by_name.get(source_name)
        objects.append(
            _healing_object(
                source=source,
                replica=replica,
                replica_name=replica_name,
            )
        )
    missing = sum(1 for item in objects if item.status == "missing_replica")
    mismatches = sum(1 for item in objects if item.status == "checksum_mismatch")
    ambiguous = sum(1 for item in objects if item.status == "ambiguous")
    health = health_score(
        total_count=max(len(objects), 1),
        missing_count=missing,
        mismatch_count=mismatches,
        ambiguous_count=ambiguous,
    )
    status = "healthy" if health == _PERFECT_HEALTH else "repairable"
    if mismatches:
        status = "blocked"
    elif ambiguous:
        status = "needs_reconciliation"
    elif missing and not lane.policy_allows_missing_replica_repair:
        status = "blocked"
    return HealingProposal(
        proposal_id=healing_proposal_id(lane=lane, objects=objects),
        lane=lane,
        status=status,
        health_score=health,
        missing_replica_count=missing,
        checksum_mismatch_count=mismatches,
        ambiguous_count=ambiguous,
        objects=tuple(objects),
        created_at=now,
        next_step=_proposal_next_step(status=status),
    )


def apply_missing_replica_repairs(
    *,
    proposal: HealingProposal,
    client: ReplicaRepairClient,
    authority: str,
    now: datetime,
) -> tuple[RepairReceipt, ...]:
    """Repair missing replicas when policy allows it and hashes verify."""
    if proposal.status != "repairable":
        msg = f"healing proposal is not repairable: {proposal.status}"
        raise ValueError(msg)
    if not proposal.lane.policy_allows_missing_replica_repair:
        msg = "lane policy does not allow missing replica repair"
        raise PermissionError(msg)
    receipts: list[RepairReceipt] = []
    for item in proposal.objects:
        if item.status != "missing_replica":
            continue
        if item.source_sha256 is None:
            msg = f"source hash unavailable for {item.source_object_name}"
            raise ValueError(msg)
        client.copy_object(
            source_container=proposal.lane.source_container,
            source_object_name=item.source_object_name,
            destination_container=proposal.lane.replica_container,
            destination_object_name=item.replica_object_name,
        )
        observed = client.object_sha256(
            container=proposal.lane.replica_container,
            object_name=item.replica_object_name,
        )
        if observed != item.source_sha256:
            msg = (
                f"repair checksum mismatch for {item.replica_object_name}: "
                f"expected {item.source_sha256}, observed {observed}"
            )
            raise ValueError(msg)
        receipts.append(
            repair_receipt(
                lane=proposal.lane,
                source_object_name=item.source_object_name,
                replica_object_name=item.replica_object_name,
                source_sha256=item.source_sha256,
                destination_sha256=observed,
                authority=authority,
                now=now,
            )
        )
    return tuple(receipts)


def repair_receipt(  # noqa: PLR0913
    *,
    lane: ReplicaLane,
    source_object_name: str,
    replica_object_name: str,
    source_sha256: str,
    destination_sha256: str,
    authority: str,
    now: datetime,
) -> RepairReceipt:
    """Build deterministic repair evidence for one object."""
    outcome = "repaired" if source_sha256 == destination_sha256 else "blocked"
    digest = digest_value(
        {
            "lane_id": lane.lane_id,
            "source_object_name": source_object_name,
            "replica_object_name": replica_object_name,
            "source_sha256": source_sha256,
            "destination_sha256": destination_sha256,
            "authority": authority,
        }
    )
    return RepairReceipt(
        receipt_id=f"repair-{digest.removeprefix('sha256:')[:24]}",
        lane_id=lane.lane_id,
        tenant=lane.tenant,
        source_object_name=source_object_name,
        replica_object_name=replica_object_name,
        source_sha256=source_sha256,
        destination_sha256=destination_sha256,
        authority=authority,
        outcome=outcome,
        repaired_at=now,
        next_step="Validate the repaired lane with `nimbus heal root --strict`.",
    )


def health_score(
    *,
    total_count: int,
    missing_count: int,
    mismatch_count: int,
    ambiguous_count: int,
) -> int:
    """Return a bounded 0-100 health score for replica state."""
    penalty = (missing_count * 40) + (mismatch_count * 70) + (ambiguous_count * 20)
    return max(0, min(100, int(100 - (penalty / max(total_count, 1)))))


def healing_proposal_id(
    *,
    lane: ReplicaLane,
    objects: Sequence[HealingObject],
) -> str:
    """Return the deterministic ID for a healing proposal."""
    digest = digest_value(
        {
            "lane_id": lane.lane_id,
            "objects": [
                {
                    "source_object_name": item.source_object_name,
                    "replica_object_name": item.replica_object_name,
                    "source_sha256": item.source_sha256,
                    "replica_sha256": item.replica_sha256,
                    "status": item.status,
                }
                for item in objects
            ],
        }
    )
    return f"heal-{digest.removeprefix('sha256:')[:24]}"


def _healing_object(
    *,
    source: ObjectPointer,
    replica: ObjectPointer | None,
    replica_name: str,
) -> HealingObject:
    if source.content_sha256 is None:
        return HealingObject(
            source_object_name=source.object_name,
            replica_object_name=replica_name,
            source_sha256=None,
            replica_sha256=replica.content_sha256 if replica is not None else None,
            status="ambiguous",
            next_step="recreate generation with SHA-256 metadata before repair",
        )
    if replica is None:
        return HealingObject(
            source_object_name=source.object_name,
            replica_object_name=replica_name,
            source_sha256=source.content_sha256,
            replica_sha256=None,
            status="missing_replica",
            next_step="copy from source to replica lane if policy allows",
        )
    if replica.content_sha256 is None:
        return HealingObject(
            source_object_name=source.object_name,
            replica_object_name=replica_name,
            source_sha256=source.content_sha256,
            replica_sha256=None,
            status="ambiguous",
            next_step="repair requires replica SHA-256 before overwrite decision",
        )
    if replica.content_sha256 != source.content_sha256:
        return HealingObject(
            source_object_name=source.object_name,
            replica_object_name=replica_name,
            source_sha256=source.content_sha256,
            replica_sha256=replica.content_sha256,
            status="checksum_mismatch",
            next_step="manual reconciliation required before Nimbus overwrites data",
        )
    return HealingObject(
        source_object_name=source.object_name,
        replica_object_name=replica_name,
        source_sha256=source.content_sha256,
        replica_sha256=replica.content_sha256,
        status="healthy",
        next_step="none",
    )


def _replica_name_for_source(*, lane: ReplicaLane, source_object_name: str) -> str:
    suffix = source_object_name.removeprefix(lane.source_prefix)
    return f"{lane.replica_prefix}{suffix}"


def _source_name_for_replica(*, lane: ReplicaLane, replica_object_name: str) -> str:
    suffix = replica_object_name.removeprefix(lane.replica_prefix)
    return f"{lane.source_prefix}{suffix}"


def _proposal_next_step(*, status: str) -> str:
    if status == "healthy":
        return "No repair needed."
    if status == "repairable":
        return "Apply policy-authorized missing replica repairs."
    if status == "needs_reconciliation":
        return "Create reconciliation tasks for objects without trusted hashes."
    return "Manual review required before Nimbus can repair this lane."
