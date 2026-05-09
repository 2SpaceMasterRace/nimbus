"""Worker loop primitives for durable Nimbus background tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from nimbus_runtime.domain import Task, TaskStatus, TenantIdentity, WorkerLease

if TYPE_CHECKING:
    from nimbus_runtime.stores import TaskStore, WorkerLeaseStore

_MAX_BATCH_SIZE = 500
EXECUTABLE_TASK_STATUSES: tuple[TaskStatus, ...] = (
    TaskStatus.CREATED,
    TaskStatus.PLANNING,
    TaskStatus.SCANNING,
    TaskStatus.DIFFING,
    TaskStatus.APPLYING,
    TaskStatus.VERIFYING,
)
TERMINAL_TASK_STATUSES: frozenset[TaskStatus] = frozenset(
    {
        TaskStatus.DONE,
        TaskStatus.FAILED,
        TaskStatus.CANCELED,
        TaskStatus.EXPIRED,
        TaskStatus.REJECTED,
    }
)
TaskExecutionOutcome = Literal["completed", "failed", "lost_lease"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class TaskWorkerConfig:
    """Configuration for one tenant-scoped Nimbus worker loop."""

    tenant: TenantIdentity
    worker_id: str
    lease_duration: timedelta = timedelta(seconds=30)
    heartbeat_interval: timedelta = timedelta(seconds=10)
    poll_interval: timedelta = timedelta(seconds=1)
    batch_size: int = 25
    claim_statuses: Sequence[TaskStatus] = EXECUTABLE_TASK_STATUSES

    def __post_init__(self) -> None:
        """Validate worker-loop bounds before the loop starts."""
        if not self.worker_id:
            msg = "worker_id is required"
            raise ValueError(msg)
        if self.lease_duration.total_seconds() <= 0:
            msg = "lease_duration must be positive"
            raise ValueError(msg)
        if self.heartbeat_interval.total_seconds() <= 0:
            msg = "heartbeat_interval must be positive"
            raise ValueError(msg)
        if self.heartbeat_interval >= self.lease_duration:
            msg = "heartbeat_interval must be shorter than lease_duration"
            raise ValueError(msg)
        if self.poll_interval.total_seconds() < 0:
            msg = "poll_interval cannot be negative"
            raise ValueError(msg)
        if not 1 <= self.batch_size <= _MAX_BATCH_SIZE:
            msg = "batch_size must be between 1 and 500"
            raise ValueError(msg)
        if any(status in TERMINAL_TASK_STATUSES for status in self.claim_statuses):
            msg = "claim_statuses cannot include terminal statuses"
            raise ValueError(msg)


@dataclass(slots=True)
class TaskLeaseContext:
    """Context passed to a task handler while a worker owns the task lease."""

    task_store: TaskStore
    lease_store: WorkerLeaseStore
    task: Task
    worker_id: str
    lease_duration: timedelta
    _lease: WorkerLease
    _clock: Callable[[], datetime]

    @property
    def lease(self) -> WorkerLease:
        """Return the latest lease observed by this worker."""
        return self._lease

    def current_task(self) -> Task | None:
        """Read the latest durable task state."""
        return self.task_store.get(tenant=self.task.tenant, task_id=self.task.task_id)

    def heartbeat(self) -> WorkerLease | None:
        """Extend this worker's lease if it still owns an active lease."""
        now = self._clock()
        renewed = self.lease_store.heartbeat(
            tenant=self.task.tenant,
            task_id=self.task.task_id,
            worker_id=self.worker_id,
            lease_until=now + self.lease_duration,
            now=now,
        )
        if renewed is not None:
            self._lease = renewed
        return renewed

    def release(self) -> bool:
        """Release this worker's current lease."""
        return self.lease_store.release(
            tenant=self.task.tenant,
            task_id=self.task.task_id,
            worker_id=self.worker_id,
        )


type TaskHandler = Callable[[TaskLeaseContext], Coroutine[Any, Any, None]]
type SleepCallable = Callable[[float], Coroutine[Any, Any, None]]


@dataclass(frozen=True, slots=True)
class TaskWorkerRuntime:
    """Runtime hooks used by a worker loop."""

    clock: Callable[[], datetime] = _utc_now
    sleep: SleepCallable = asyncio.sleep


@dataclass(frozen=True, slots=True)
class TaskExecutionResult:
    """Result of running one claimed task under a worker lease."""

    task_id: str
    lease_attempt: int
    outcome: TaskExecutionOutcome
    error: str | None = None


@dataclass(frozen=True, slots=True)
class TaskWorkerRunResult:
    """Summary of one bounded worker-loop scan."""

    scanned: int
    claimed: int
    completed: int
    failed: int
    lost_leases: int
    results: tuple[TaskExecutionResult, ...]


class TaskWorkerLoop:
    """Tenant-scoped async worker that claims tasks through short leases."""

    def __init__(
        self,
        *,
        task_store: TaskStore,
        lease_store: WorkerLeaseStore,
        handler: TaskHandler,
        config: TaskWorkerConfig,
        runtime: TaskWorkerRuntime | None = None,
    ) -> None:
        """Create a worker loop around durable task and lease stores."""
        self._task_store = task_store
        self._lease_store = lease_store
        self._handler = handler
        self._config = config
        self._runtime = TaskWorkerRuntime() if runtime is None else runtime

    async def run_once(self) -> TaskWorkerRunResult:
        """Scan a bounded batch of tasks and run each task the worker claims."""
        scanned = 0
        claimed = 0
        completed = 0
        failed = 0
        lost_leases = 0
        results: list[TaskExecutionResult] = []
        seen_task_ids: set[str] = set()

        for status in self._config.claim_statuses:
            if claimed >= self._config.batch_size:
                break
            remaining = self._config.batch_size - claimed
            tasks = self._task_store.list_for_tenant(
                tenant=self._config.tenant,
                status=status,
                limit=remaining,
            )
            for task in tasks:
                if claimed >= self._config.batch_size:
                    break
                if task.task_id in seen_task_ids:
                    continue
                seen_task_ids.add(task.task_id)
                scanned += 1
                lease = self._claim(task)
                if lease is None:
                    continue
                claimed += 1
                result = await self._run_claimed_task(task=task, lease=lease)
                results.append(result)
                if result.outcome == "completed":
                    completed += 1
                elif result.outcome == "failed":
                    failed += 1
                elif result.outcome == "lost_lease":
                    lost_leases += 1

        return TaskWorkerRunResult(
            scanned=scanned,
            claimed=claimed,
            completed=completed,
            failed=failed,
            lost_leases=lost_leases,
            results=tuple(results),
        )

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        """Run worker scans until cancelled or until ``stop`` is set."""
        while stop is None or not stop.is_set():
            await self.run_once()
            if stop is not None and stop.is_set():
                return
            await self._runtime.sleep(self._config.poll_interval.total_seconds())

    def _claim(self, task: Task) -> WorkerLease | None:
        now = self._runtime.clock()
        return self._lease_store.acquire(
            tenant=task.tenant,
            task_id=task.task_id,
            worker_id=self._config.worker_id,
            lease_until=now + self._config.lease_duration,
            now=now,
        )

    async def _run_claimed_task(
        self,
        *,
        task: Task,
        lease: WorkerLease,
    ) -> TaskExecutionResult:
        context = TaskLeaseContext(
            task_store=self._task_store,
            lease_store=self._lease_store,
            task=task,
            worker_id=self._config.worker_id,
            lease_duration=self._config.lease_duration,
            _lease=lease,
            _clock=self._runtime.clock,
        )
        lost_lease = asyncio.Event()
        handler_task: asyncio.Task[None] = asyncio.create_task(self._handler(context))
        heartbeat_task = asyncio.create_task(
            self._heartbeat_until_done(
                context=context,
                handler_task=handler_task,
                lost_lease=lost_lease,
            ),
        )
        outcome: TaskExecutionOutcome = "completed"
        error: str | None = None
        try:
            await handler_task
        except asyncio.CancelledError:
            if not lost_lease.is_set():
                raise
            outcome = "lost_lease"
        except Exception as exc:  # noqa: BLE001
            # Handler failures are isolated so one bad task cannot stop the loop.
            outcome = "failed"
            error = str(exc) or type(exc).__name__
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task

        if outcome == "completed" or self._task_is_terminal(task):
            context.release()
        return TaskExecutionResult(
            task_id=task.task_id,
            lease_attempt=lease.attempt,
            outcome=outcome,
            error=error,
        )

    async def _heartbeat_until_done(
        self,
        *,
        context: TaskLeaseContext,
        handler_task: asyncio.Task[None],
        lost_lease: asyncio.Event,
    ) -> None:
        while not handler_task.done():
            await self._runtime.sleep(
                self._config.heartbeat_interval.total_seconds(),
            )
            if handler_task.done():
                return
            try:
                renewed = context.heartbeat()
            except Exception:  # noqa: BLE001
                # A heartbeat storage error means the worker no longer has proof
                # of ownership, so the in-flight handler must stop.
                lost_lease.set()
                handler_task.cancel()
                return
            if renewed is None:
                lost_lease.set()
                handler_task.cancel()
                return

    def _task_is_terminal(self, task: Task) -> bool:
        current = self._task_store.get(tenant=task.tenant, task_id=task.task_id)
        return current is not None and current.status in TERMINAL_TASK_STATUSES
