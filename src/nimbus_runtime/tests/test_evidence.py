"""Tests for content-addressed evidence payload exports."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from nimbus_runtime.domain import (
    Artifact,
    ArtifactKind,
    ArtifactPayload,
    ProofReceipt,
    ProviderHealthReport,
    ProviderOutcome,
    ProviderProbeResult,
    RepairReceipt,
    TenantIdentity,
    UploadReport,
)
from nimbus_runtime.evidence import (
    compact_evidence_records,
    export_artifact_payload,
    preview_artifact,
    verify_evidence_object,
)

pytestmark = pytest.mark.unit


def test_export_artifact_payload_is_content_addressed_and_deduped(
    tmp_path: Path,
) -> None:
    """Repeated exports of the same payload reuse one verified object."""
    artifact = _artifact("art-evidence")

    first = export_artifact_payload(
        artifact=artifact,
        root=tmp_path,
        exported_at=_now(),
    )
    second = export_artifact_payload(
        artifact=artifact,
        root=tmp_path,
        exported_at=_now(),
    )

    assert first.object_uri == second.object_uri
    assert first.payload_digest == second.payload_digest
    assert verify_evidence_object(record=first, root=tmp_path)
    assert len(list(tmp_path.rglob("*.payload.json.gz"))) == 1


def test_preview_reports_missing_then_available_evidence(
    tmp_path: Path,
) -> None:
    """Previews are useful before and after an evidence export."""
    artifact = _artifact("art-preview")

    before = preview_artifact(artifact=artifact, root=tmp_path, generated_at=_now())
    export_artifact_payload(artifact=artifact, root=tmp_path, exported_at=_now())
    after = preview_artifact(artifact=artifact, root=tmp_path, generated_at=_now())

    assert before.evidence_available is False
    assert after.evidence_available is True
    assert after.summary == "Upload evidence for docs/test.txt."


def test_compaction_verifies_sources_and_keeps_payload_objects(
    tmp_path: Path,
) -> None:
    """Compaction writes a bundle index without deleting source payloads."""
    first = export_artifact_payload(
        artifact=_artifact("art-one"),
        root=tmp_path,
        exported_at=_now(),
    )
    second = export_artifact_payload(
        artifact=_artifact("art-two", remote_path="docs/other.txt"),
        root=tmp_path,
        exported_at=_now(),
    )

    bundle = compact_evidence_records(
        records=(first, second),
        root=tmp_path,
        compacted_at=_now(),
    )

    assert bundle.artifact_count == 2
    assert bundle.verification_status == "verified"
    assert len(list(tmp_path.rglob("*.payload.json.gz"))) == 2
    assert len(list(tmp_path.rglob("*.bundle.json.gz"))) == 1


def test_export_rejects_payload_digest_mismatch(tmp_path: Path) -> None:
    """Artifact rows with stale payload digests cannot enter evidence storage."""
    artifact = replace(_artifact("art-stale"), payload_digest="sha256:" + "0" * 64)

    with pytest.raises(ValueError, match="payload digest mismatch"):
        export_artifact_payload(artifact=artifact, root=tmp_path, exported_at=_now())


def test_verify_evidence_object_detects_missing_corrupt_and_invalid_gzip(
    tmp_path: Path,
) -> None:
    """Object verification fails closed for missing or corrupted payload bytes."""
    record = export_artifact_payload(
        artifact=_artifact("art-verify"),
        root=tmp_path,
        exported_at=_now(),
    )
    object_path = next(tmp_path.rglob("*.payload.json.gz"))

    assert not verify_evidence_object(
        record=replace(record, payload_digest="sha256:" + "b" * 64),
        root=tmp_path,
    )
    assert not verify_evidence_object(
        record=replace(record, stored_digest="sha256:" + "c" * 64),
        root=tmp_path,
    )

    bad_gzip = b"not a gzip payload"
    object_path.write_bytes(bad_gzip)
    assert not verify_evidence_object(
        record=replace(record, stored_digest=_sha256(bad_gzip)),
        root=tmp_path,
    )


def test_export_detects_content_address_collision(tmp_path: Path) -> None:
    """A path collision with different bytes is treated as corruption."""
    artifact = _artifact("art-collision")
    export_artifact_payload(artifact=artifact, root=tmp_path, exported_at=_now())
    next(tmp_path.rglob("*.payload.json.gz")).write_bytes(b"wrong bytes")

    with pytest.raises(ValueError, match="content-addressed object collision"):
        export_artifact_payload(artifact=artifact, root=tmp_path, exported_at=_now())


def test_compaction_fails_closed_on_empty_mixed_or_missing_sources(
    tmp_path: Path,
) -> None:
    """Bundle creation refuses underdefined, cross-tenant, or missing evidence."""
    first = export_artifact_payload(
        artifact=_artifact("art-first"),
        root=tmp_path,
        exported_at=_now(),
    )
    second = export_artifact_payload(
        artifact=_artifact("art-second", workspace_id="other"),
        root=tmp_path,
        exported_at=_now(),
    )

    with pytest.raises(ValueError, match="at least one"):
        compact_evidence_records(records=(), root=tmp_path, compacted_at=_now())
    with pytest.raises(ValueError, match="multiple tenants"):
        compact_evidence_records(
            records=(first, second),
            root=tmp_path,
            compacted_at=_now(),
        )
    with pytest.raises(ValueError, match="missing or corrupt"):
        compact_evidence_records(
            records=(replace(first, payload_digest="sha256:" + "d" * 64),),
            root=tmp_path,
            compacted_at=_now(),
        )


def test_preview_summaries_cover_health_repair_and_proof_payloads(
    tmp_path: Path,
) -> None:
    """Preview summaries stay compact across core evidence payload families."""
    health = preview_artifact(
        artifact=_artifact_with_payload(
            artifact_id="art-health",
            kind="provider_health",
            payload=_provider_health_report(),
        ),
        root=tmp_path,
        generated_at=_now(),
    )
    repair = preview_artifact(
        artifact=_artifact_with_payload(
            artifact_id="art-repair",
            kind="repair_receipt",
            payload=_repair_receipt(),
        ),
        root=tmp_path,
        generated_at=_now(),
    )
    proof = preview_artifact(
        artifact=_artifact_with_payload(
            artifact_id="art-proof",
            kind="proof_receipt",
            payload=_proof_receipt(),
        ),
        root=tmp_path,
        generated_at=_now(),
    )

    assert health.summary == "Provider health is healthy."
    assert health.fields["advisory_context"] == "1 items"
    assert repair.summary == "Replica repair repaired."
    assert proof.summary == "Proof receipt outcome is succeeded."
    assert proof.fields["artifact_digests"] == "1 fields"


def _artifact(
    artifact_id: str,
    *,
    remote_path: str = "docs/test.txt",
    workspace_id: str = "local",
) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        tenant=TenantIdentity(platform="cli", workspace_id=workspace_id),
        session_id="sess-evidence",
        action_id=None,
        kind="upload_report",
        uri=None,
        payload=UploadReport(
            remote_path=remote_path,
            filename=Path(remote_path).name,
            size_bytes=512,
            sha256_hex="a" * 64,
        ),
        created_at=_now(),
    )


def _artifact_with_payload(
    *,
    artifact_id: str,
    kind: str,
    payload: object,
) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        tenant=TenantIdentity(platform="cli", workspace_id="local"),
        session_id="sess-evidence",
        action_id=None,
        kind=cast("ArtifactKind", kind),
        uri=None,
        payload=cast("ArtifactPayload", payload),
        created_at=_now(),
    )


def _provider_health_report() -> ProviderHealthReport:
    tenant = TenantIdentity(platform="cli", workspace_id="local")
    return ProviderHealthReport(
        report_id="health-1",
        tenant=tenant,
        provider="s3",
        container="bucket",
        prefix="docs/",
        region="us-east-1",
        status="healthy",
        health_score=100,
        confidence="high",
        evidence_source="nimbus_probe",
        generated_at=_now(),
        expires_at=_now(),
        probes=(
            ProviderProbeResult(
                probe_name="list",
                operation="list_files_page",
                provider="s3",
                container="bucket",
                prefix="docs/",
                object_name=None,
                region="us-east-1",
                outcome=ProviderOutcome.SUCCESS,
                latency_ms=1,
                item_count=1,
                request_id=None,
                error_message=None,
                observed_at=_now(),
            ),
        ),
        advisory_context=("Live bounded probe succeeded.",),
        next_operator_step="No action required.",
    )


def _repair_receipt() -> RepairReceipt:
    tenant = TenantIdentity(platform="cli", workspace_id="local")
    return RepairReceipt(
        receipt_id="repair-1",
        lane_id="lane-1",
        tenant=tenant,
        source_object_name="docs/a.txt",
        replica_object_name="replica/a.txt",
        source_sha256="a" * 64,
        destination_sha256="a" * 64,
        authority="policy:repair-missing-replica",
        outcome="repaired",
        repaired_at=_now(),
        next_step="Validate the repaired lane.",
    )


def _proof_receipt() -> ProofReceipt:
    tenant = TenantIdentity(platform="cli", workspace_id="local")
    return ProofReceipt(
        receipt_id="rec-1",
        tenant=tenant,
        subject="demo",
        outcome="succeeded",
        summary="Demo proof.",
        task_id=None,
        action_id=None,
        manifest_artifact_id=None,
        verifier_artifact_id=None,
        linked_artifact_ids=("art-upload",),
        artifact_digests={"art-upload": "sha256:" + "a" * 64},
        session_id="sess-evidence",
        event_range_start=None,
        event_range_end=None,
        policy_version="runtime-default-v1",
        idempotency_key=None,
        next_steps=("Inspect linked evidence.",),
        created_at=_now(),
    )


def _now() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()
