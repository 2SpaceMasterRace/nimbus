"""Property-based tests for Nimbus durable store invariants."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from nimbus_runtime.domain import (
    Artifact,
    Task,
    TaskStatus,
    TenantIdentity,
    UploadReport,
    VerifiedActor,
)
from nimbus_runtime.proof import artifact_payload_digest
from nimbus_runtime.stores import (
    FileArtifactStore,
    FileSessionEventStore,
    FileTaskStore,
    FileWorkerLeaseStore,
)

pytestmark = pytest.mark.property

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_SAFE_ID = st.from_regex(r"^[A-Za-z0-9_.:-]{1,24}$", fullmatch=True)
_SAFE_PATH = st.from_regex(
    r"^[A-Za-z0-9_.:-]{1,12}/[A-Za-z0-9_.:-]{1,24}$",
    fullmatch=True,
)
_SHA256 = st.from_regex(r"^[0-9a-f]{64}$", fullmatch=True)


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
        verified_at=_NOW,
    )


def _task(*, tenant: TenantIdentity, actor: VerifiedActor, task_id: str) -> Task:
    return Task(
        task_id=task_id,
        tenant=tenant,
        session_id=f"{tenant.tenant_id}:{task_id}",
        created_by=actor,
        status=TaskStatus.CREATED,
        intent="backup_channel",
        source_ref="slack:T123TEAM:C123CHAN:thread",
        idempotency_key=f"idem-{task_id}",
        metadata={"channel_id": "C123CHAN"},
        failure_detail=None,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=None,
    )


def _create_task(root: Path, *, tenant: TenantIdentity, task_id: str) -> Task:
    actor = _actor(tenant)
    return FileTaskStore(root).create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key=f"idem-{task_id}",
        create=lambda: _task(tenant=tenant, actor=actor, task_id=task_id),
    )


def _upload_artifact(
    *,
    tenant: TenantIdentity,
    artifact_id: str,
    remote_path: str,
    filename: str,
    size_bytes: int,
    sha256_hex: str,
    created_at: datetime = _NOW,
) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        tenant=tenant,
        session_id="sess-proof",
        action_id="act-upload",
        kind="upload_report",
        uri=None,
        payload=UploadReport(
            remote_path=remote_path,
            filename=filename,
            size_bytes=size_bytes,
            sha256_hex=sha256_hex,
        ),
        created_at=created_at,
    )


@given(
    task_id=_SAFE_ID,
    first_worker=_SAFE_ID,
    second_worker=_SAFE_ID,
    lease_seconds=st.integers(min_value=1, max_value=120),
)
@settings(max_examples=25, deadline=None)
def test_worker_lease_excludes_other_workers_until_expiry(
    task_id: str,
    first_worker: str,
    second_worker: str,
    lease_seconds: int,
) -> None:
    """At most one unexpired worker lease may exist for a tenant-scoped task."""
    tenant = _tenant()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        task = _create_task(root, tenant=tenant, task_id=task_id)
        lease_store = FileWorkerLeaseStore(root)
        first = lease_store.acquire(
            tenant=tenant,
            task_id=task.task_id,
            worker_id=first_worker,
            lease_until=_NOW + timedelta(seconds=lease_seconds),
            now=_NOW,
        )
        assert first is not None

        before_expiry = lease_store.acquire(
            tenant=tenant,
            task_id=task.task_id,
            worker_id=second_worker,
            lease_until=_NOW + timedelta(seconds=lease_seconds * 2),
            now=_NOW + timedelta(seconds=lease_seconds - 1),
        )
        after_expiry = lease_store.acquire(
            tenant=tenant,
            task_id=task.task_id,
            worker_id=second_worker,
            lease_until=_NOW + timedelta(seconds=lease_seconds * 3),
            now=_NOW + timedelta(seconds=lease_seconds),
        )

        assert before_expiry is None
        assert after_expiry is not None
        assert after_expiry.worker_id == second_worker
        assert after_expiry.attempt == 2
        assert lease_store.get(tenant=tenant, task_id=task.task_id) == after_expiry


@given(
    task_id=_SAFE_ID,
    owner=_SAFE_ID,
    caller=_SAFE_ID,
    expired=st.booleans(),
)
@settings(max_examples=25, deadline=None)
def test_worker_lease_heartbeat_requires_current_active_owner(
    task_id: str,
    owner: str,
    caller: str,
    expired: bool,
) -> None:
    """Only the current owner can heartbeat before the lease expires."""
    tenant = _tenant()
    lease_until = _NOW + timedelta(seconds=30)
    heartbeat_at = _NOW + timedelta(seconds=31 if expired else 5)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        task = _create_task(root, tenant=tenant, task_id=task_id)
        lease_store = FileWorkerLeaseStore(root)
        lease = lease_store.acquire(
            tenant=tenant,
            task_id=task.task_id,
            worker_id=owner,
            lease_until=lease_until,
            now=_NOW,
        )
        assert lease is not None

        renewed = lease_store.heartbeat(
            tenant=tenant,
            task_id=task.task_id,
            worker_id=caller,
            lease_until=_NOW + timedelta(seconds=60),
            now=heartbeat_at,
        )

        should_renew = caller == owner and not expired
        assert (renewed is not None) is should_renew
        stored = lease_store.get(tenant=tenant, task_id=task.task_id)
        assert stored is not None
        if should_renew:
            assert stored.worker_id == owner
            assert stored.heartbeat_at == heartbeat_at
            assert stored.lease_until == _NOW + timedelta(seconds=60)
        else:
            assert stored == lease


@given(
    artifact_id=_SAFE_ID,
    remote_path=_SAFE_PATH,
    filename=_SAFE_ID,
    size_bytes=st.integers(min_value=0, max_value=10_000_000),
    sha256_hex=_SHA256,
)
@settings(max_examples=30, deadline=None)
def test_artifact_store_populates_digest_and_returns_same_artifact_by_id(
    artifact_id: str,
    remote_path: str,
    filename: str,
    size_bytes: int,
    sha256_hex: str,
) -> None:
    """Artifact IDs are immutable and converge on the first persisted payload."""
    tenant = _tenant()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        event_store = FileSessionEventStore(root)
        store = FileArtifactStore(root, event_store=event_store)
        first = _upload_artifact(
            tenant=tenant,
            artifact_id=artifact_id,
            remote_path=remote_path,
            filename=filename,
            size_bytes=size_bytes,
            sha256_hex=sha256_hex,
        )
        second = replace(
            first,
            payload=UploadReport(
                remote_path=f"{remote_path}.changed",
                filename=f"{filename}.changed",
                size_bytes=size_bytes + 1,
                sha256_hex="f" * 64,
            ),
        )

        created = store.create(artifact=first, actor=_actor(tenant))
        repeated = store.create(artifact=second, actor=_actor(tenant))
        found = store.get(tenant=tenant, artifact_id=artifact_id)

        assert created == repeated
        assert found == created
        assert found is not None
        assert found.payload_digest == artifact_payload_digest(first.payload)
        assert found.payload_digest != artifact_payload_digest(second.payload)
        events = event_store.list_events(tenant=tenant, session_id=first.session_id)
        assert [event.event_type for event in events] == ["artifact_created"]


@given(
    artifact_id=_SAFE_ID,
    remote_path=_SAFE_PATH,
    filename=_SAFE_ID,
    size_bytes=st.integers(min_value=0, max_value=10_000_000),
    sha256_hex=_SHA256,
)
@settings(max_examples=20, deadline=None)
def test_artifact_store_rejects_mismatched_payload_digest(
    artifact_id: str,
    remote_path: str,
    filename: str,
    size_bytes: int,
    sha256_hex: str,
) -> None:
    """A supplied payload digest must match the typed payload before persistence."""
    tenant = _tenant()
    artifact = replace(
        _upload_artifact(
            tenant=tenant,
            artifact_id=artifact_id,
            remote_path=remote_path,
            filename=filename,
            size_bytes=size_bytes,
            sha256_hex=sha256_hex,
        ),
        payload_digest="sha256:" + ("0" * 64),
    )
    if artifact.payload_digest == artifact_payload_digest(artifact.payload):
        artifact = replace(artifact, payload_digest="sha256:" + ("1" * 64))

    with tempfile.TemporaryDirectory() as tmp:
        store = FileArtifactStore(Path(tmp))
        with pytest.raises(ValueError, match="payload digest mismatch"):
            store.create(artifact=artifact, actor=None)
        assert store.get(tenant=tenant, artifact_id=artifact_id) is None


@given(
    first_id=_SAFE_ID,
    second_id=_SAFE_ID,
    first_size=st.integers(min_value=0, max_value=1_000),
    second_size=st.integers(min_value=0, max_value=1_000),
)
@settings(max_examples=20, deadline=None)
def test_artifact_session_listing_is_chronological_and_tenant_scoped(
    first_id: str,
    second_id: str,
    first_size: int,
    second_size: int,
) -> None:
    """Session artifact listings are ordered by creation time within one tenant."""
    tenant = _tenant()
    other_tenant = _tenant("T999TEAM")
    with tempfile.TemporaryDirectory() as tmp:
        store = FileArtifactStore(Path(tmp))
        first = _upload_artifact(
            tenant=tenant,
            artifact_id=f"art-a-{first_id}",
            remote_path="reports/a.txt",
            filename="a.txt",
            size_bytes=first_size,
            sha256_hex="a" * 64,
            created_at=_NOW,
        )
        second = _upload_artifact(
            tenant=tenant,
            artifact_id=f"art-b-{second_id}",
            remote_path="reports/b.txt",
            filename="b.txt",
            size_bytes=second_size,
            sha256_hex="b" * 64,
            created_at=_NOW + timedelta(seconds=1),
        )
        cross_tenant = replace(
            first,
            artifact_id="art-cross-tenant",
            tenant=other_tenant,
        )
        store.create(artifact=second, actor=None)
        store.create(artifact=cross_tenant, actor=None)
        store.create(artifact=first, actor=None)

        listed = store.list_for_session(tenant=tenant, session_id=first.session_id)

        assert [artifact.artifact_id for artifact in listed] == [
            first.artifact_id,
            second.artifact_id,
        ]
        assert all(artifact.tenant == tenant for artifact in listed)
