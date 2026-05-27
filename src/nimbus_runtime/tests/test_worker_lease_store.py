"""Unit tests for Nimbus worker lease primitives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nimbus_runtime.domain import Task, TaskStatus, TenantIdentity, VerifiedActor
from nimbus_runtime.stores import FileTaskStore, FileWorkerLeaseStore

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


def _task(*, tenant: TenantIdentity, actor: VerifiedActor) -> Task:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Task(
        task_id="task-test",
        tenant=tenant,
        session_id=f"{tenant.tenant_id}:task-test",
        created_by=actor,
        status=TaskStatus.CREATED,
        intent="backup_channel",
        source_ref="slack:T123TEAM:C123CHAN:thread",
        idempotency_key="idem-task",
        metadata={"channel_id": "C123CHAN"},
        failure_detail=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )


def _create_task(tmp_path: Path) -> tuple[TenantIdentity, Task]:
    tenant = _tenant()
    actor = _actor(tenant)
    task_store = FileTaskStore(tmp_path)
    task = task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=lambda: _task(tenant=tenant, actor=actor),
    )
    return tenant, task


def test_worker_lease_requires_existing_task(tmp_path: Path) -> None:
    """Workers should not lease task IDs that are not durable tasks."""
    tenant = _tenant()
    lease_store = FileWorkerLeaseStore(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    assert (
        lease_store.acquire(
            tenant=tenant,
            task_id="missing-task",
            worker_id="worker-a",
            lease_until=now + timedelta(seconds=30),
            now=now,
        )
        is None
    )


def test_worker_lease_acquire_blocks_until_expiry(tmp_path: Path) -> None:
    """Only one worker should hold a task lease until it expires."""
    tenant, task = _create_task(tmp_path)
    lease_store = FileWorkerLeaseStore(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    first = lease_store.acquire(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-a",
        lease_until=now + timedelta(seconds=30),
        now=now,
    )
    blocked = lease_store.acquire(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-b",
        lease_until=now + timedelta(seconds=40),
        now=now + timedelta(seconds=10),
    )
    takeover = lease_store.acquire(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-b",
        lease_until=now + timedelta(seconds=70),
        now=now + timedelta(seconds=31),
    )

    assert first is not None
    assert first.worker_id == "worker-a"
    assert first.attempt == 1
    assert blocked is None
    assert takeover is not None
    assert takeover.worker_id == "worker-b"
    assert takeover.attempt == 2
    assert lease_store.get(tenant=tenant, task_id=task.task_id) == takeover


def test_worker_lease_heartbeat_requires_owner_and_active(tmp_path: Path) -> None:
    """Only the owning worker should heartbeat an unexpired lease."""
    tenant, task = _create_task(tmp_path)
    lease_store = FileWorkerLeaseStore(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease = lease_store.acquire(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-a",
        lease_until=now + timedelta(seconds=30),
        now=now,
    )
    assert lease is not None

    wrong_owner = lease_store.heartbeat(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-b",
        lease_until=now + timedelta(seconds=60),
        now=now + timedelta(seconds=5),
    )
    renewed = lease_store.heartbeat(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-a",
        lease_until=now + timedelta(seconds=60),
        now=now + timedelta(seconds=5),
    )
    expired = lease_store.heartbeat(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-a",
        lease_until=now + timedelta(seconds=90),
        now=now + timedelta(seconds=61),
    )

    assert wrong_owner is None
    assert renewed is not None
    assert renewed.worker_id == "worker-a"
    assert renewed.attempt == 1
    assert renewed.heartbeat_at == now + timedelta(seconds=5)
    assert renewed.lease_until == now + timedelta(seconds=60)
    assert expired is None


def test_worker_lease_release_requires_owner(tmp_path: Path) -> None:
    """A worker should not release another worker's task lease."""
    tenant, task = _create_task(tmp_path)
    lease_store = FileWorkerLeaseStore(tmp_path)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    lease = lease_store.acquire(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-a",
        lease_until=now + timedelta(seconds=30),
        now=now,
    )
    assert lease is not None

    assert (
        lease_store.release(
            tenant=tenant,
            task_id=task.task_id,
            worker_id="worker-b",
        )
        is False
    )
    assert lease_store.get(tenant=tenant, task_id=task.task_id) == lease
    assert (
        lease_store.release(
            tenant=tenant,
            task_id=task.task_id,
            worker_id="worker-a",
        )
        is True
    )
    assert lease_store.get(tenant=tenant, task_id=task.task_id) is None
