"""Tests for S3 replica-lane healing."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nimbus_runtime.domain import GenerationManifest, ObjectPointer, TenantIdentity
from nimbus_runtime.healing import (
    ReplicaLane,
    apply_missing_replica_repairs,
    evaluate_replica_lane,
    replica_lane_id,
)

pytestmark = pytest.mark.unit


def test_replica_lane_evaluate_marks_missing_replica_repairable() -> None:
    """Missing replicas are repairable only when lane policy allows it."""
    lane = _lane(allow=True)

    proposal = evaluate_replica_lane(
        lane=lane,
        source_manifest=_manifest("source-bucket", "docs/", ("a.txt", "a" * 64)),
        replica_manifest=_manifest("replica-bucket", "replica/"),
        now=_now(),
    )

    assert proposal.status == "repairable"
    assert proposal.missing_replica_count == 1
    assert proposal.health_score == 60
    assert proposal.objects[0].replica_object_name == "replica/a.txt"


def test_replica_lane_blocks_checksum_mismatch() -> None:
    """Checksum mismatch blocks automated repair instead of overwriting data."""
    lane = _lane(allow=True)

    proposal = evaluate_replica_lane(
        lane=lane,
        source_manifest=_manifest("source-bucket", "docs/", ("a.txt", "a" * 64)),
        replica_manifest=_manifest("replica-bucket", "replica/", ("a.txt", "b" * 64)),
        now=_now(),
    )

    assert proposal.status == "blocked"
    assert proposal.checksum_mismatch_count == 1
    assert "Manual review" in proposal.next_step


def test_apply_missing_replica_repair_writes_hash_receipt() -> None:
    """Repair execution proves source and destination hashes match."""
    lane = _lane(allow=True)
    proposal = evaluate_replica_lane(
        lane=lane,
        source_manifest=_manifest("source-bucket", "docs/", ("a.txt", "a" * 64)),
        replica_manifest=_manifest("replica-bucket", "replica/"),
        now=_now(),
    )
    client = _FakeRepairClient(hash_after_copy="a" * 64)

    receipts = apply_missing_replica_repairs(
        proposal=proposal,
        client=client,
        authority="policy:repair-missing-replica",
        now=_now(),
    )

    assert client.copied == [
        ("source-bucket", "docs/a.txt", "replica-bucket", "replica/a.txt")
    ]
    assert len(receipts) == 1
    assert receipts[0].outcome == "repaired"
    assert receipts[0].source_sha256 == receipts[0].destination_sha256


def test_apply_missing_replica_repair_fails_closed_on_bad_hash() -> None:
    """A copied replica with the wrong checksum does not get a repair receipt."""
    lane = _lane(allow=True)
    proposal = evaluate_replica_lane(
        lane=lane,
        source_manifest=_manifest("source-bucket", "docs/", ("a.txt", "a" * 64)),
        replica_manifest=_manifest("replica-bucket", "replica/"),
        now=_now(),
    )

    with pytest.raises(ValueError, match="repair checksum mismatch"):
        apply_missing_replica_repairs(
            proposal=proposal,
            client=_FakeRepairClient(hash_after_copy="b" * 64),
            authority="policy:repair-missing-replica",
            now=_now(),
        )


def test_missing_replica_without_policy_is_blocked() -> None:
    """Lane policy must explicitly allow automated missing-replica repair."""
    proposal = evaluate_replica_lane(
        lane=_lane(allow=False),
        source_manifest=_manifest("source-bucket", "docs/", ("a.txt", "a" * 64)),
        replica_manifest=_manifest("replica-bucket", "replica/"),
        now=_now(),
    )

    assert proposal.status == "blocked"
    assert "Manual review" in proposal.next_step


def test_unknown_source_hash_requires_reconciliation() -> None:
    """Unknown hashes are not treated as safe repair inputs."""
    lane = _lane(allow=True)
    source = GenerationManifest(
        root_id="root",
        generation_id="gen-source",
        manifest_digest="sha256:source",
        provider="s3",
        container="source-bucket",
        prefix="docs/",
        objects=(
            ObjectPointer(
                provider="s3",
                container="source-bucket",
                object_name="docs/a.txt",
                content_sha256=None,
                size_bytes=10,
            ),
        ),
        object_count=1,
        total_bytes=10,
        partial=False,
        created_at=_now(),
    )

    proposal = evaluate_replica_lane(
        lane=lane,
        source_manifest=source,
        replica_manifest=_manifest("replica-bucket", "replica/"),
        now=_now(),
    )

    assert proposal.status == "needs_reconciliation"
    assert proposal.ambiguous_count == 1


def test_non_s3_replica_lane_is_rejected() -> None:
    """The MVP should not pretend a non-S3 repair lane is implemented."""
    with pytest.raises(ValueError, match="S3-only"):
        ReplicaLane(
            lane_id="lane-gcs",
            tenant=_tenant(),
            root_id="root",
            provider="gcs",
            source_container="source",
            source_prefix="docs/",
            replica_container="replica",
            replica_prefix="docs/",
            policy_allows_missing_replica_repair=True,
            created_at=_now(),
            metadata={},
        )


class _FakeRepairClient:
    def __init__(self, *, hash_after_copy: str) -> None:
        self._hash_after_copy = hash_after_copy
        self.copied: list[tuple[str, str, str, str]] = []

    def copy_object(
        self,
        *,
        source_container: str,
        source_object_name: str,
        destination_container: str,
        destination_object_name: str,
    ) -> None:
        self.copied.append(
            (
                source_container,
                source_object_name,
                destination_container,
                destination_object_name,
            )
        )

    def object_sha256(self, *, container: str, object_name: str) -> str | None:
        return self._hash_after_copy


def _lane(*, allow: bool) -> ReplicaLane:
    tenant = _tenant()
    return ReplicaLane(
        lane_id=replica_lane_id(
            tenant=tenant,
            root_id="root",
            source_container="source-bucket",
            source_prefix="docs/",
            replica_container="replica-bucket",
            replica_prefix="replica/",
        ),
        tenant=tenant,
        root_id="root",
        provider="s3",
        source_container="source-bucket",
        source_prefix="docs/",
        replica_container="replica-bucket",
        replica_prefix="replica/",
        policy_allows_missing_replica_repair=allow,
        created_at=_now(),
        metadata={},
    )


def _manifest(
    container: str,
    prefix: str,
    *items: tuple[str, str],
) -> GenerationManifest:
    objects = tuple(
        ObjectPointer(
            provider="s3",
            container=container,
            object_name=f"{prefix}{name}",
            content_sha256=digest,
            size_bytes=10,
        )
        for name, digest in items
    )
    return GenerationManifest(
        root_id="root",
        generation_id=f"gen-{container}",
        manifest_digest=f"sha256:{container}",
        provider="s3",
        container=container,
        prefix=prefix,
        objects=objects,
        object_count=len(objects),
        total_bytes=10 * len(objects),
        partial=False,
        created_at=_now(),
    )


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="cli", workspace_id="local")


def _now() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
