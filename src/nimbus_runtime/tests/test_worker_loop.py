"""Unit tests for the Nimbus task worker loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nimbus_runtime.domain import (
    Task,
    TaskStatus,
    TaskTransition,
    TenantIdentity,
    VerifiedActor,
    WorkerLease,
)
from nimbus_runtime.stores import FileTaskStore, FileWorkerLeaseStore
from nimbus_runtime.worker import (
    TaskLeaseContext,
    TaskWorkerConfig,
    TaskWorkerLoop,
    TaskWorkerRuntime,
)

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


def _task(
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    task_id: str = "task-test",
    idempotency_key: str = "idem-task",
) -> Task:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Task(
        task_id=task_id,
        tenant=tenant,
        session_id=f"{tenant.tenant_id}:{task_id}",
        created_by=actor,
        status=TaskStatus.CREATED,
        intent="backup_channel",
        source_ref="slack:T123TEAM:C123CHAN:thread",
        idempotency_key=idempotency_key,
        metadata={"channel_id": "C123CHAN"},
        failure_detail=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )


def _create_task(
    task_store: FileTaskStore,
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    task_id: str = "task-test",
    idempotency_key: str = "idem-task",
) -> Task:
    return task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key=idempotency_key,
        create=lambda: _task(
            tenant=tenant,
            actor=actor,
            task_id=task_id,
            idempotency_key=idempotency_key,
        ),
    )


def _planning_transition(reason: str = "worker advanced task") -> TaskTransition:
    return TaskTransition(
        expected=TaskStatus.CREATED,
        next_status=TaskStatus.PLANNING,
        event_type="task_planning_started",
        event_payload={"reason": reason},
    )


class _NotifyingLeaseStore:
    """Lease-store wrapper that exposes successful heartbeat events to tests."""

    def __init__(
        self,
        wrapped: FileWorkerLeaseStore,
        heartbeat_seen: asyncio.Event,
    ) -> None:
        self._wrapped = wrapped
        self._heartbeat_seen = heartbeat_seen

    def acquire(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> WorkerLease | None:
        """Delegate lease acquisition to the wrapped store."""
        return self._wrapped.acquire(
            tenant=tenant,
            task_id=task_id,
            worker_id=worker_id,
            lease_until=lease_until,
            now=now,
        )

    def heartbeat(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> WorkerLease | None:
        """Notify the test after a successful heartbeat."""
        renewed = self._wrapped.heartbeat(
            tenant=tenant,
            task_id=task_id,
            worker_id=worker_id,
            lease_until=lease_until,
            now=now,
        )
        if renewed is not None:
            self._heartbeat_seen.set()
        return renewed

    def release(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
    ) -> bool:
        """Delegate lease release to the wrapped store."""
        return self._wrapped.release(
            tenant=tenant,
            task_id=task_id,
            worker_id=worker_id,
        )

    def get(self, *, tenant: TenantIdentity, task_id: str) -> WorkerLease | None:
        """Delegate lease reads to the wrapped store."""
        return self._wrapped.get(tenant=tenant, task_id=task_id)


def test_worker_config_rejects_unbounded_or_unsafe_values() -> None:
    """Worker configuration should fail before an unsafe loop starts."""
    tenant = _tenant()

    with pytest.raises(ValueError, match="worker_id"):
        TaskWorkerConfig(tenant=tenant, worker_id="")

    with pytest.raises(ValueError, match="heartbeat_interval"):
        TaskWorkerConfig(
            tenant=tenant,
            worker_id="worker-a",
            lease_duration=timedelta(seconds=10),
            heartbeat_interval=timedelta(seconds=10),
        )

    with pytest.raises(ValueError, match="terminal"):
        TaskWorkerConfig(
            tenant=tenant,
            worker_id="worker-a",
            claim_statuses=(TaskStatus.DONE,),
        )


def test_worker_loop_claims_runs_once_and_releases(tmp_path: Path) -> None:
    """A claimed task should run once and release its lease on success."""
    tenant = _tenant()
    actor = _actor(tenant)
    task_store = FileTaskStore(tmp_path)
    lease_store = FileWorkerLeaseStore(tmp_path)
    task = _create_task(task_store, tenant=tenant, actor=actor)
    calls: list[str] = []

    async def handler(context: TaskLeaseContext) -> None:
        calls.append(context.task.task_id)
        assert context.lease.worker_id == "worker-a"
        assert (
            task_store.transition(
                tenant=tenant,
                task_id=context.task.task_id,
                transition=_planning_transition(),
            )
            is not None
        )

    loop = TaskWorkerLoop(
        task_store=task_store,
        lease_store=lease_store,
        handler=handler,
        config=TaskWorkerConfig(tenant=tenant, worker_id="worker-a"),
    )

    result = asyncio.run(loop.run_once())

    assert calls == [task.task_id]
    assert result.scanned == 1
    assert result.claimed == 1
    assert result.completed == 1
    assert result.failed == 0
    assert result.lost_leases == 0
    assert result.results[0].outcome == "completed"
    assert lease_store.get(tenant=tenant, task_id=task.task_id) is None
    current = task_store.get(tenant=tenant, task_id=task.task_id)
    assert current is not None
    assert current.status is TaskStatus.PLANNING


def test_worker_loop_does_not_claim_approval_or_terminal_tasks(
    tmp_path: Path,
) -> None:
    """Workers should skip tasks that are waiting on humans or already done."""
    tenant = _tenant()
    actor = _actor(tenant)
    task_store = FileTaskStore(tmp_path)
    lease_store = FileWorkerLeaseStore(tmp_path)
    approval_task = _create_task(
        task_store,
        tenant=tenant,
        actor=actor,
        task_id="task-approval",
        idempotency_key="idem-approval",
    )
    done_task = _create_task(
        task_store,
        tenant=tenant,
        actor=actor,
        task_id="task-done",
        idempotency_key="idem-done",
    )
    assert (
        task_store.transition(
            tenant=tenant,
            task_id=approval_task.task_id,
            transition=TaskTransition(
                expected=TaskStatus.CREATED,
                next_status=TaskStatus.AWAITING_APPROVAL,
                event_type="task_approval_required",
                event_payload={"reason": "destructive action"},
            ),
        )
        is not None
    )
    assert (
        task_store.transition(
            tenant=tenant,
            task_id=done_task.task_id,
            transition=_planning_transition(),
        )
        is not None
    )
    assert (
        task_store.transition(
            tenant=tenant,
            task_id=done_task.task_id,
            transition=TaskTransition(
                expected=TaskStatus.PLANNING,
                next_status=TaskStatus.DONE,
                event_type="task_done",
                event_payload={"reason": "nothing to do"},
            ),
        )
        is not None
    )
    calls = 0

    async def handler(_context: TaskLeaseContext) -> None:
        nonlocal calls
        calls += 1

    loop = TaskWorkerLoop(
        task_store=task_store,
        lease_store=lease_store,
        handler=handler,
        config=TaskWorkerConfig(tenant=tenant, worker_id="worker-a"),
    )

    result = asyncio.run(loop.run_once())

    assert calls == 0
    assert result.scanned == 0
    assert result.claimed == 0


def test_worker_loop_heartbeats_while_handler_is_running(tmp_path: Path) -> None:
    """A running handler should keep extending the worker lease."""
    tenant = _tenant()
    actor = _actor(tenant)
    task_store = FileTaskStore(tmp_path)
    lease_store = FileWorkerLeaseStore(tmp_path)
    task = _create_task(task_store, tenant=tenant, actor=actor)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    sleep_calls = 0

    def clock() -> datetime:
        return now

    async def fake_sleep(seconds: float) -> None:
        nonlocal now, sleep_calls
        sleep_calls += 1
        now += timedelta(seconds=seconds)
        await asyncio.sleep(0)

    async def run() -> tuple[int, datetime, datetime]:
        heartbeat_seen = asyncio.Event()
        notifying_lease_store = _NotifyingLeaseStore(lease_store, heartbeat_seen)
        observed_times: tuple[datetime, datetime] | None = None

        async def handler(context: TaskLeaseContext) -> None:
            nonlocal observed_times
            await heartbeat_seen.wait()
            observed_times = (context.lease.acquired_at, context.lease.heartbeat_at)
            assert (
                task_store.transition(
                    tenant=tenant,
                    task_id=context.task.task_id,
                    transition=_planning_transition("heartbeat observed"),
                )
                is not None
            )

        loop = TaskWorkerLoop(
            task_store=task_store,
            lease_store=notifying_lease_store,
            handler=handler,
            config=TaskWorkerConfig(
                tenant=tenant,
                worker_id="worker-a",
                lease_duration=timedelta(seconds=5),
                heartbeat_interval=timedelta(seconds=1),
            ),
            runtime=TaskWorkerRuntime(clock=clock, sleep=fake_sleep),
        )
        result = await loop.run_once()
        assert result.completed == 1
        assert observed_times is not None
        return sleep_calls, observed_times[0], observed_times[1]

    count, acquired_at, heartbeat_at = asyncio.run(run())

    assert count >= 1
    assert heartbeat_at > acquired_at
    assert lease_store.get(tenant=tenant, task_id=task.task_id) is None


def test_worker_loop_cancels_handler_when_lease_is_lost(tmp_path: Path) -> None:
    """A worker that cannot renew its lease should stop the in-flight handler."""
    tenant = _tenant()
    actor = _actor(tenant)
    task_store = FileTaskStore(tmp_path)
    lease_store = FileWorkerLeaseStore(tmp_path)
    task = _create_task(task_store, tenant=tenant, actor=actor)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cancelled = False

    def clock() -> datetime:
        return now

    async def jump_past_lease(_seconds: float) -> None:
        nonlocal now
        now += timedelta(seconds=3)
        await asyncio.sleep(0)

    async def handler(_context: TaskLeaseContext) -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    loop = TaskWorkerLoop(
        task_store=task_store,
        lease_store=lease_store,
        handler=handler,
        config=TaskWorkerConfig(
            tenant=tenant,
            worker_id="worker-a",
            lease_duration=timedelta(seconds=2),
            heartbeat_interval=timedelta(seconds=1),
        ),
        runtime=TaskWorkerRuntime(clock=clock, sleep=jump_past_lease),
    )

    result = asyncio.run(loop.run_once())

    assert cancelled is True
    assert result.claimed == 1
    assert result.completed == 0
    assert result.failed == 0
    assert result.lost_leases == 1
    assert result.results[0].outcome == "lost_lease"
    lease = lease_store.get(tenant=tenant, task_id=task.task_id)
    assert lease is not None
    assert lease.worker_id == "worker-a"
