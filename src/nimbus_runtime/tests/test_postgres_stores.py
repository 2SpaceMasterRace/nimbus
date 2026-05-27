"""Unit tests for Postgres-backed Nimbus runtime stores."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Self

import nimbus_runtime.stores as stores_mod
import pytest
from nimbus_runtime.domain import (
    Task,
    TaskStatus,
    TaskTransition,
    TenantIdentity,
    VerifiedActor,
)
from nimbus_runtime.stores import PostgresTaskStore, PostgresWorkerLeaseStore

pytestmark = pytest.mark.unit

type _Row = dict[str, object]
type _Params = tuple[object, ...]
type _Handler = Callable[[_Params], _Cursor]

_TASK_COLUMNS = (
    "task_id",
    "tenant_id",
    "tenant_json",
    "session_id",
    "actor_json",
    "status",
    "intent",
    "source_ref",
    "idempotency_key",
    "metadata_json",
    "failure_detail",
    "created_at",
    "updated_at",
    "expires_at",
    "schema_version",
)


@dataclass
class _Cursor:
    """Minimal cursor result for fake Postgres connections."""

    row: _Row | None = None
    rows: list[_Row] = field(default_factory=list)
    rowcount: int = 0

    def fetchone(self) -> _Row | None:
        """Return the configured single row."""
        return self.row

    def fetchall(self) -> list[_Row]:
        """Return the configured row list."""
        return list(self.rows)


@dataclass
class _FakePostgres:
    """Small in-memory subset of the Postgres runtime store contract."""

    tasks_by_id: dict[tuple[str, str], _Row] = field(default_factory=dict)
    tasks_by_idempotency: dict[tuple[str, str], _Row] = field(default_factory=dict)
    leases_by_task: dict[tuple[str, str], _Row] = field(default_factory=dict)
    statements: list[tuple[str, _Params | None]] = field(default_factory=list)

    def __enter__(self) -> Self:
        """Enter the fake connection context."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Exit the fake connection context."""

    @contextmanager
    def transaction(self) -> Iterator[Self]:
        """Yield this fake connection as a transaction."""
        yield self

    def execute(self, query: str, params: _Params | None = None) -> _Cursor:
        """Execute the small SQL subset used by the Postgres stores."""
        sql = _compact_sql(query)
        self.statements.append((sql, params))
        resolved = self._resolve_handler(sql)
        if resolved is not None:
            count, handler = resolved
            return handler(_require_params(params, count))
        msg = f"unhandled fake postgres SQL: {sql}"
        raise AssertionError(msg)

    def _resolve_handler(self, sql: str) -> tuple[int, _Handler] | None:
        def list_tasks(params: _Params) -> _Cursor:
            return self._list_tasks(sql, params)

        list_task_count = 3 if "status = %s" in sql else 2
        handlers: tuple[tuple[bool, int, _Handler], ...] = (
            (
                sql.startswith("select * from tasks") and "idempotency_key = %s" in sql,
                2,
                self._select_task_by_idempotency,
            ),
            (
                sql.startswith("insert into tasks"),
                len(_TASK_COLUMNS),
                self._insert_task,
            ),
            (
                sql.startswith("select * from tasks") and "task_id = %s" in sql,
                2,
                self._select_task_by_id,
            ),
            (sql.startswith("update tasks"), 7, self._update_task),
            (
                sql.startswith("select * from tasks")
                and "order by updated_at desc" in sql,
                list_task_count,
                list_tasks,
            ),
            (sql.startswith("insert into worker_leases"), 11, self._acquire_lease),
            (sql.startswith("update worker_leases"), 7, self._heartbeat_lease),
            (sql.startswith("delete from worker_leases"), 3, self._release_lease),
            (sql.startswith("select * from worker_leases"), 2, self._select_lease),
        )
        for matches, count, handler in handlers:
            if matches:
                return count, handler
        return None

    def _select_task_by_idempotency(self, params: _Params) -> _Cursor:
        tenant_id = _str_param(params[0])
        idempotency_key = _str_param(params[1])
        return _Cursor(row=self.tasks_by_idempotency.get((tenant_id, idempotency_key)))

    def _insert_task(self, params: _Params) -> _Cursor:
        row = dict(zip(_TASK_COLUMNS, params, strict=True))
        tenant_id = _row_str(row, "tenant_id")
        task_id = _row_str(row, "task_id")
        idempotency_key = _row_str(row, "idempotency_key")
        self.tasks_by_id[(tenant_id, task_id)] = row
        self.tasks_by_idempotency[(tenant_id, idempotency_key)] = row
        return _Cursor(rowcount=1)

    def _select_task_by_id(self, params: _Params) -> _Cursor:
        tenant_id = _str_param(params[0])
        task_id = _str_param(params[1])
        return _Cursor(row=self.tasks_by_id.get((tenant_id, task_id)))

    def _update_task(self, params: _Params) -> _Cursor:
        tenant_id = _str_param(params[5])
        task_id = _str_param(params[6])
        row = self.tasks_by_id.get((tenant_id, task_id))
        if row is None:
            return _Cursor(rowcount=0)
        row.update(
            {
                "status": params[0],
                "failure_detail": params[1],
                "updated_at": params[2],
                "expires_at": params[3],
                "schema_version": params[4],
            }
        )
        return _Cursor(rowcount=1)

    def _list_tasks(self, sql: str, params: _Params) -> _Cursor:
        tenant_id = _str_param(params[0])
        status = _str_param(params[1]) if "status = %s" in sql else None
        limit = _int_param(params[-1])
        rows = [
            row
            for (row_tenant_id, _task_id), row in self.tasks_by_id.items()
            if row_tenant_id == tenant_id
            and (status is None or row["status"] == status)
        ]
        rows.sort(key=lambda row: _row_str(row, "updated_at"), reverse=True)
        return _Cursor(rows=rows[:limit])

    def _acquire_lease(self, params: _Params) -> _Cursor:
        tenant_id = _str_param(params[0])
        task_id = _str_param(params[1])
        key = (tenant_id, task_id)
        if key not in self.tasks_by_id:
            return _Cursor()

        worker_id = _str_param(params[2])
        lease_until = _str_param(params[3])
        now = _str_param(params[4])
        current = self.leases_by_task.get(key)
        if current is not None and _parse_datetime(
            current["lease_until"],
        ) > _parse_datetime(params[10]):
            return _Cursor()
        attempt = 1 if current is None else _row_int(current, "attempt") + 1
        row: _Row = {
            "tenant_id": tenant_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "lease_until": lease_until,
            "acquired_at": now,
            "heartbeat_at": now,
            "attempt": attempt,
            "schema_version": params[7],
        }
        self.leases_by_task[key] = row
        return _Cursor(row=row, rowcount=1)

    def _heartbeat_lease(self, params: _Params) -> _Cursor:
        tenant_id = _str_param(params[3])
        task_id = _str_param(params[4])
        worker_id = _str_param(params[5])
        key = (tenant_id, task_id)
        row = self.leases_by_task.get(key)
        if row is None or row["worker_id"] != worker_id:
            return _Cursor()
        if _parse_datetime(row["lease_until"]) <= _parse_datetime(params[6]):
            return _Cursor()
        row.update(
            {
                "lease_until": params[0],
                "heartbeat_at": params[1],
                "schema_version": params[2],
            }
        )
        return _Cursor(row=row, rowcount=1)

    def _release_lease(self, params: _Params) -> _Cursor:
        tenant_id = _str_param(params[0])
        task_id = _str_param(params[1])
        worker_id = _str_param(params[2])
        row = self.leases_by_task.get((tenant_id, task_id))
        if row is None or row["worker_id"] != worker_id:
            return _Cursor(rowcount=0)
        del self.leases_by_task[(tenant_id, task_id)]
        return _Cursor(rowcount=1)

    def _select_lease(self, params: _Params) -> _Cursor:
        tenant_id = _str_param(params[0])
        task_id = _str_param(params[1])
        return _Cursor(row=self.leases_by_task.get((tenant_id, task_id)))


def _install_fake_postgres(
    monkeypatch: pytest.MonkeyPatch,
    database: _FakePostgres,
) -> None:
    monkeypatch.setattr(stores_mod, "pg_connect", lambda: database)
    monkeypatch.setattr(stores_mod, "pg_transaction", database.transaction)


def _compact_sql(query: str) -> str:
    return " ".join(query.lower().split())


def _require_params(params: _Params | None, count: int) -> _Params:
    if params is None or len(params) != count:
        msg = f"expected {count} SQL params, got {params!r}"
        raise AssertionError(msg)
    return params


def _str_param(value: object) -> str:
    if not isinstance(value, str):
        msg = f"expected string SQL param, got {value!r}"
        raise TypeError(msg)
    return value


def _int_param(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"expected integer SQL param, got {value!r}"
        raise TypeError(msg)
    return value


def _row_str(row: _Row, key: str) -> str:
    return _str_param(row[key])


def _row_int(row: _Row, key: str) -> int:
    return _int_param(row[key])


def _parse_datetime(value: object) -> datetime:
    return datetime.fromisoformat(_str_param(value))


def _tenant(workspace_id: str = "T123TEAM") -> TenantIdentity:
    return TenantIdentity(platform="slack", workspace_id=workspace_id)


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
        source_ref=f"slack:{tenant.workspace_id}:C123CHAN:thread",
        idempotency_key=idempotency_key,
        metadata={"channel_id": "C123CHAN"},
        failure_detail=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )


def test_postgres_task_store_creates_task_once_by_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate logical requests should return the original Postgres task."""
    database = _FakePostgres()
    _install_fake_postgres(monkeypatch, database)
    store = PostgresTaskStore()
    tenant = _tenant()
    actor = _actor(tenant)
    calls = 0

    def create() -> Task:
        nonlocal calls
        calls += 1
        return _task(tenant=tenant, actor=actor)

    first = store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=create,
    )
    second = store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=create,
    )

    assert first == second
    assert calls == 1
    assert len(database.tasks_by_id) == 1


def test_postgres_task_store_transition_and_listing_are_tenant_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres task updates should be compare-and-set and tenant scoped."""
    database = _FakePostgres()
    _install_fake_postgres(monkeypatch, database)
    store = PostgresTaskStore()
    tenant = _tenant()
    actor = _actor(tenant)
    other_tenant = _tenant("T999TEAM")
    other_actor = _actor(other_tenant)
    task = store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=lambda: _task(tenant=tenant, actor=actor),
    )
    store.create_or_get_by_idempotency(
        tenant=other_tenant,
        idempotency_key="idem-other",
        create=lambda: _task(
            tenant=other_tenant,
            actor=other_actor,
            task_id="task-other",
            idempotency_key="idem-other",
        ),
    )

    planning = store.transition(
        tenant=tenant,
        task_id=task.task_id,
        transition=TaskTransition(
            expected=TaskStatus.CREATED,
            next_status=TaskStatus.PLANNING,
            event_type="task_planning_started",
            event_payload={"reason": "classified backup workflow"},
        ),
    )
    stale = store.transition(
        tenant=tenant,
        task_id=task.task_id,
        transition=TaskTransition(
            expected=TaskStatus.CREATED,
            next_status=TaskStatus.PLANNING,
            event_type="task_planning_started",
            event_payload={"reason": "stale retry"},
        ),
    )

    assert planning is not None
    assert planning.status is TaskStatus.PLANNING
    assert stale is None
    assert store.get(tenant=tenant, task_id=task.task_id) == planning
    assert store.get(tenant=other_tenant, task_id=task.task_id) is None
    assert store.list_for_tenant(tenant=tenant) == (planning,)
    assert store.list_for_tenant(tenant=tenant, status=TaskStatus.PLANNING) == (
        planning,
    )
    assert store.list_for_tenant(tenant=tenant, status=TaskStatus.CREATED) == ()
    assert [stored.tenant for stored in store.list_for_tenant(tenant=other_tenant)] == [
        other_tenant
    ]


def test_postgres_worker_lease_requires_existing_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres workers should not lease non-durable task IDs."""
    database = _FakePostgres()
    _install_fake_postgres(monkeypatch, database)
    store = PostgresWorkerLeaseStore()
    tenant = _tenant()
    now = datetime(2026, 1, 1, tzinfo=UTC)

    assert (
        store.acquire(
            tenant=tenant,
            task_id="missing-task",
            worker_id="worker-a",
            lease_until=now + timedelta(seconds=30),
            now=now,
        )
        is None
    )


def test_postgres_worker_lease_acquire_heartbeat_and_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres leases should preserve owner, expiry, and takeover semantics."""
    database = _FakePostgres()
    _install_fake_postgres(monkeypatch, database)
    task_store = PostgresTaskStore()
    lease_store = PostgresWorkerLeaseStore()
    tenant = _tenant()
    actor = _actor(tenant)
    task = task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=lambda: _task(tenant=tenant, actor=actor),
    )
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
    assert (
        lease_store.heartbeat(
            tenant=tenant,
            task_id=task.task_id,
            worker_id="worker-a",
            lease_until=now + timedelta(seconds=80),
            now=now + timedelta(seconds=32),
        )
        is None
    )

    renewed = lease_store.heartbeat(
        tenant=tenant,
        task_id=task.task_id,
        worker_id="worker-b",
        lease_until=now + timedelta(seconds=90),
        now=now + timedelta(seconds=32),
    )

    assert renewed is not None
    assert renewed.worker_id == "worker-b"
    assert renewed.heartbeat_at == now + timedelta(seconds=32)
    assert renewed.lease_until == now + timedelta(seconds=90)
    assert lease_store.get(tenant=tenant, task_id=task.task_id) == renewed
    assert (
        lease_store.release(
            tenant=tenant,
            task_id=task.task_id,
            worker_id="worker-a",
        )
        is False
    )
    assert (
        lease_store.release(
            tenant=tenant,
            task_id=task.task_id,
            worker_id="worker-b",
        )
        is True
    )
    assert lease_store.get(tenant=tenant, task_id=task.task_id) is None
