"""Tests for Nimbus proof receipt helpers."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from nimbus_runtime.domain import (
    Artifact,
    ProofReceipt,
    TenantIdentity,
    UploadReport,
)
from nimbus_runtime.proof import (
    artifact_payload_digest,
    deterministic_receipt_id,
    validate_proof_receipt_links,
)
from nimbus_runtime.stores import FileArtifactStore

pytestmark = pytest.mark.unit


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="cli", workspace_id="local")


def _upload_artifact(artifact_id: str = "art-upload") -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        tenant=_tenant(),
        session_id="sess-proof",
        action_id="act-upload",
        kind="upload_report",
        uri=None,
        payload=UploadReport(
            remote_path="reports/demo.txt",
            filename="demo.txt",
            size_bytes=12,
            sha256_hex="ab" * 32,
        ),
        created_at=datetime(2026, 5, 21, tzinfo=UTC),
    )


def _receipt(linked: Artifact, *, digest: str | None = None) -> ProofReceipt:
    return ProofReceipt(
        receipt_id="rec-proof",
        tenant=linked.tenant,
        subject=linked.kind,
        outcome="succeeded",
        summary="Proof under test",
        task_id=None,
        action_id=linked.action_id,
        manifest_artifact_id=None,
        verifier_artifact_id=None,
        linked_artifact_ids=(linked.artifact_id,),
        artifact_digests={
            linked.artifact_id: digest
            if digest is not None
            else artifact_payload_digest(linked.payload)
        },
        session_id=linked.session_id,
        event_range_start=None,
        event_range_end=None,
        policy_version="runtime-default-v1",
        idempotency_key=None,
        next_steps=("Inspect linked evidence.",),
        created_at=datetime(2026, 5, 21, tzinfo=UTC),
    )


@given(
    remote_path=st.text(min_size=1, max_size=80),
    filename=st.text(min_size=1, max_size=80),
    size_bytes=st.integers(min_value=0, max_value=10_000_000),
)
@pytest.mark.property
def test_artifact_payload_digest_is_stable(
    remote_path: str,
    filename: str,
    size_bytes: int,
) -> None:
    """Digesting the same logical payload twice yields the same value."""
    payload = UploadReport(
        remote_path=remote_path,
        filename=filename,
        size_bytes=size_bytes,
        sha256_hex="cd" * 32,
    )

    assert artifact_payload_digest(payload) == artifact_payload_digest(payload)
    assert artifact_payload_digest(payload).startswith("sha256:")


def test_file_artifact_store_populates_payload_digest(tmp_path: Path) -> None:
    """Artifact stores should persist payload digests for evidence rows."""
    store = FileArtifactStore(tmp_path)
    artifact = store.create(artifact=_upload_artifact(), actor=None)

    found = store.get(tenant=_tenant(), artifact_id=artifact.artifact_id)

    assert found is not None
    assert found.payload_digest == artifact_payload_digest(found.payload)


def test_validate_proof_receipt_links_accepts_matching_digest() -> None:
    """A receipt is valid only when linked evidence exists and matches digest."""
    artifact = replace(
        _upload_artifact(),
        payload_digest=artifact_payload_digest(_upload_artifact().payload),
    )
    receipt = _receipt(artifact, digest=artifact.payload_digest)

    failures = validate_proof_receipt_links(
        receipt=receipt,
        artifacts_by_id={artifact.artifact_id: artifact},
    )

    assert failures == ()


def test_validate_proof_receipt_links_reports_missing_artifact() -> None:
    """Missing linked evidence makes a receipt invalid."""
    artifact = _upload_artifact()
    receipt = _receipt(artifact)

    failures = validate_proof_receipt_links(receipt=receipt, artifacts_by_id={})

    assert any("missing" in failure for failure in failures)


def test_deterministic_receipt_id_converges_for_retry() -> None:
    """The same evidence bundle should produce one retry-stable receipt ID."""
    first = deterministic_receipt_id(
        tenant=_tenant(),
        subject="channel_backup",
        task_id="task-1",
        action_id=None,
        manifest_artifact_id="art-manifest",
        verifier_artifact_id="art-verifier",
        linked_artifact_ids=("art-verifier", "art-manifest"),
    )
    second = deterministic_receipt_id(
        tenant=_tenant(),
        subject="channel_backup",
        task_id="task-1",
        action_id=None,
        manifest_artifact_id="art-manifest",
        verifier_artifact_id="art-verifier",
        linked_artifact_ids=("art-manifest", "art-verifier"),
    )

    assert first == second
