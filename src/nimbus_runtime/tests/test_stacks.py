"""Tests for Nimbus storage change stacks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cloud_storage_api.models import DeleteResult
from nimbus_runtime.cleanup import build_cleanup_plan_candidates
from nimbus_runtime.domain import (
    Artifact,
    ConflictArtifact,
    GenerationManifest,
    ObjectPointer,
    Plan,
    TenantIdentity,
    VerifiedActor,
)
from nimbus_runtime.stacks import FileStorageStackStore
from nimbus_runtime.stores import FileArtifactStore

pytestmark = pytest.mark.unit


def test_stack_propose_from_cleanup_plan_creates_ordered_changes(
    tmp_path: Path,
) -> None:
    """A cleanup candidate becomes an ordered storage stack."""
    tenant = _tenant()
    actor = _actor(tenant)
    plan = _cleanup_plan(tmp_path=tmp_path, tenant=tenant, actor=actor)

    state = FileStorageStackStore(tmp_path).create_from_plan(
        plan=plan,
        actor=actor,
        now=_now(),
    )

    assert state.stack.plan_id == plan.plan_id
    assert state.stack.status == "proposed"
    assert len(state.changes) == 2
    assert [entry.position for entry in state.entries] == [1, 2]
    assert {revision.operation for revision in state.revisions} == {
        "archive_then_delete"
    }
    assert {revision.target["container"] for revision in state.revisions} == {"bucket"}
    assert state.operations[0].kind == "stack_proposed"


def test_restack_conflict_writes_artifact_and_marks_stack_conflicted(
    tmp_path: Path,
) -> None:
    """Restack fails closed when a target digest changed after planning."""
    tenant = _tenant()
    actor = _actor(tenant)
    plan = _cleanup_plan(tmp_path=tmp_path, tenant=tenant, actor=actor)
    stack_store = FileStorageStackStore(tmp_path)
    state = stack_store.create_from_plan(plan=plan, actor=actor, now=_now())
    manifest = _manifest(
        tenant=tenant,
        objects=(
            _pointer("docs/a.txt", "a" * 64),
            _pointer("docs/copy/a.txt", "b" * 64),
            _pointer("docs/extra/a.txt", "a" * 64),
        ),
    ).payload
    assert isinstance(manifest, GenerationManifest)

    updated = stack_store.restack(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        manifest=manifest,
        artifact_store=FileArtifactStore(tmp_path),
        actor=actor,
        now=_now(),
    )

    assert updated is not None
    assert updated.stack.status == "conflicted"
    assert any(change.status == "conflicted" for change in updated.changes)
    conflicts = FileArtifactStore(tmp_path).list_for_tenant(
        tenant=tenant,
        kind="conflict_artifact",
    )
    assert len(conflicts) == 1
    assert isinstance(conflicts[0].payload, ConflictArtifact)
    assert conflicts[0].payload.observed_digest == "b" * 64


def test_approved_conflicted_stack_apply_is_blocked(tmp_path: Path) -> None:
    """A conflicted stack cannot be applied until restacked or abandoned."""
    tenant = _tenant()
    actor = _actor(tenant)
    plan = _cleanup_plan(tmp_path=tmp_path, tenant=tenant, actor=actor)
    stack_store = FileStorageStackStore(tmp_path)
    state = stack_store.create_from_plan(plan=plan, actor=actor, now=_now())
    approved = stack_store.approve(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        actor=actor,
        now=_now(),
    )
    assert approved is not None
    manifest = _manifest(
        tenant=tenant,
        objects=(
            _pointer("docs/a.txt", "a" * 64),
            _pointer("docs/copy/a.txt", "b" * 64),
            _pointer("docs/extra/a.txt", "a" * 64),
        ),
    ).payload
    assert isinstance(manifest, GenerationManifest)
    conflicted = stack_store.restack(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        manifest=manifest,
        artifact_store=FileArtifactStore(tmp_path),
        actor=actor,
        now=_now(),
    )
    assert conflicted is not None

    result = stack_store.apply(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        actor=actor,
        storage=_FakeStorage(),
        artifact_store=FileArtifactStore(tmp_path),
        now=_now(),
    )

    assert result is not None
    assert result.status == "blocked"
    assert "resolve conflicts" in result.next_step


def test_delete_stack_apply_verifier_conflict_writes_conflict_artifact(
    tmp_path: Path,
) -> None:
    """Delete execution checks the approved digest immediately before mutation."""
    tenant = _tenant()
    actor = _actor(tenant)
    plan = next(
        plan
        for plan in _cleanup_candidates(tmp_path=tmp_path, tenant=tenant, actor=actor)
        if plan.metadata["candidate_strategy"] == "delete_extra_copies"
    )
    stack_store = FileStorageStackStore(tmp_path)
    state = stack_store.create_from_plan(plan=plan, actor=actor, now=_now())
    approved = stack_store.approve(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        actor=actor,
        now=_now(),
    )
    assert approved is not None

    result = stack_store.apply(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        actor=actor,
        storage=_FakeStorage(integrity="sha256:" + "b" * 64),
        artifact_store=FileArtifactStore(tmp_path),
        now=_now(),
    )

    assert result is not None
    assert result.status == "blocked"
    conflicts = FileArtifactStore(tmp_path).list_for_tenant(
        tenant=tenant,
        kind="conflict_artifact",
    )
    assert len(conflicts) == 1


def test_delete_stack_apply_success_writes_mutation_and_proof_receipt(
    tmp_path: Path,
) -> None:
    """Successful delete stack apply leaves durable mutation and proof evidence."""
    tenant = _tenant()
    actor = _actor(tenant)
    plan = next(
        plan
        for plan in _cleanup_candidates(tmp_path=tmp_path, tenant=tenant, actor=actor)
        if plan.metadata["candidate_strategy"] == "delete_extra_copies"
    )
    stack_store = FileStorageStackStore(tmp_path)
    state = stack_store.create_from_plan(plan=plan, actor=actor, now=_now())
    approved = stack_store.approve(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        actor=actor,
        now=_now(),
    )
    assert approved is not None
    artifact_store = FileArtifactStore(tmp_path)
    storage = _FakeStorage(integrity="sha256:" + "a" * 64)

    result = stack_store.apply(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        actor=actor,
        storage=storage,
        artifact_store=artifact_store,
        now=_now(),
    )

    assert result is not None
    assert result.status == "applied"
    assert storage.deleted == [
        ("bucket", "docs/copy/a.txt"),
        ("bucket", "docs/extra/a.txt"),
    ]
    mutations = artifact_store.list_for_tenant(
        tenant=tenant,
        kind="storage_mutation_report",
    )
    receipts = artifact_store.list_for_tenant(tenant=tenant, kind="proof_receipt")
    assert len(mutations) == 2
    assert len(receipts) == 2


def test_stack_abandon_marks_unapplied_changes_abandoned(tmp_path: Path) -> None:
    """Abandoning a proposed stack is durable and terminal for local review."""
    tenant = _tenant()
    actor = _actor(tenant)
    plan = _cleanup_plan(tmp_path=tmp_path, tenant=tenant, actor=actor)
    stack_store = FileStorageStackStore(tmp_path)
    state = stack_store.create_from_plan(plan=plan, actor=actor, now=_now())

    abandoned = stack_store.abandon(
        tenant=tenant,
        stack_id=state.stack.stack_id,
        actor=actor,
        now=_now(),
    )

    assert abandoned is not None
    assert abandoned.stack.status == "abandoned"
    assert {change.status for change in abandoned.changes} == {"abandoned"}


@dataclass(frozen=True, slots=True)
class _FakeInfo:
    integrity: str
    metadata: dict[str, str] | None = None


class _FakeStorage:
    def __init__(self, integrity: str = "sha256:" + ("a" * 64)) -> None:
        self._integrity = integrity
        self.deleted: list[tuple[str, str]] = []

    def get_file_info(self, container: str, object_name: str) -> _FakeInfo:
        return _FakeInfo(integrity=self._integrity)

    def delete_file(self, container: str, object_name: str) -> DeleteResult:
        self.deleted.append((container, object_name))
        return DeleteResult(deleted=True, version_id="v1", request_charged=False)


def _cleanup_plan(
    *,
    tmp_path: Path,
    tenant: TenantIdentity,
    actor: VerifiedActor,
) -> Plan:
    return _cleanup_candidates(tmp_path=tmp_path, tenant=tenant, actor=actor)[0]


def _cleanup_candidates(
    *,
    tmp_path: Path,
    tenant: TenantIdentity,
    actor: VerifiedActor,
) -> tuple[Plan, ...]:
    manifest = _manifest(
        tenant=tenant,
        objects=(
            _pointer("docs/a.txt", "a" * 64),
            _pointer("docs/copy/a.txt", "a" * 64),
            _pointer("docs/extra/a.txt", "a" * 64),
        ),
    )
    FileArtifactStore(tmp_path).create(artifact=manifest, actor=actor)
    candidates = build_cleanup_plan_candidates(
        manifest_artifact=manifest,
        actor=actor,
        now=_now(),
    )
    assert len(candidates) == 3
    return candidates


def _manifest(
    *,
    tenant: TenantIdentity,
    objects: tuple[ObjectPointer, ...],
) -> Artifact:
    return Artifact(
        artifact_id="art-manifest-stack",
        tenant=tenant,
        session_id="sess-stack",
        action_id=None,
        kind="manifest",
        uri=None,
        payload=GenerationManifest(
            root_id="root-stack",
            generation_id="gen-stack",
            manifest_digest="sha256:stack",
            provider="s3",
            container="bucket",
            prefix="docs/",
            objects=objects,
            object_count=len(objects),
            total_bytes=sum(pointer.size_bytes or 0 for pointer in objects),
            partial=False,
            created_at=_now(),
        ),
        created_at=_now(),
    )


def _pointer(object_name: str, digest: str) -> ObjectPointer:
    return ObjectPointer(
        provider="s3",
        container="bucket",
        object_name=object_name,
        content_sha256=digest,
        size_bytes=10,
    )


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="cli", workspace_id="local")


def _actor(tenant: TenantIdentity) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id="tester",
        auth_source="cli_local",
        bridge_id=None,
        verified_at=_now(),
    )


def _now() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
