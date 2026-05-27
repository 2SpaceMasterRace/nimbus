"""Unit tests for Nimbus task store primitives."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import nimbus_runtime.stores as stores_mod
import pytest
from nimbus_runtime.domain import (
    Task,
    TaskStatus,
    TaskTransition,
    TenantIdentity,
    VerifiedActor,
    validate_task_transition,
)
from nimbus_runtime.stores import FileSessionEventStore, FileTaskStore

pytestmark = pytest.mark.unit


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
        session_id=f"{tenant.tenant_id}:task-test",
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


def test_invalid_task_transition_is_rejected() -> None:
    """Terminal tasks should not transition back to active states."""
    with pytest.raises(ValueError, match="invalid task transition"):
        validate_task_transition(
            expected=TaskStatus.DONE,
            next_status=TaskStatus.APPLYING,
        )


def test_file_task_store_creates_task_once_by_idempotency(tmp_path: Path) -> None:
    """Duplicate logical requests should return the original task."""
    event_store = FileSessionEventStore(tmp_path)
    task_store = FileTaskStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    calls = 0

    def create() -> Task:
        nonlocal calls
        calls += 1
        return _task(tenant=tenant, actor=actor)

    first = task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=create,
    )
    second = task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=create,
    )

    assert first == second
    assert calls == 1
    events = event_store.list_events(tenant=tenant, session_id=first.session_id)
    assert [event.event_type for event in events] == ["task_created"]
    assert events[0].payload["task_id"] == "task-test"
    assert events[0].payload["status"] == TaskStatus.CREATED.value


def test_file_task_store_transition_is_compare_and_set(tmp_path: Path) -> None:
    """Only callers that see the expected state should move a task."""
    event_store = FileSessionEventStore(tmp_path)
    task_store = FileTaskStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    task = task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=lambda: _task(tenant=tenant, actor=actor),
    )

    planning = task_store.transition(
        tenant=tenant,
        task_id=task.task_id,
        transition=TaskTransition(
            expected=TaskStatus.CREATED,
            next_status=TaskStatus.PLANNING,
            event_type="task_planning_started",
            event_payload={"reason": "classified backup workflow"},
        ),
    )
    stale = task_store.transition(
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
    assert task_store.get(tenant=tenant, task_id=task.task_id) == planning
    events = event_store.list_events(tenant=tenant, session_id=task.session_id)
    assert [event.event_type for event in events] == [
        "task_created",
        "task_planning_started",
    ]


def test_file_task_store_list_for_tenant_is_tenant_scoped(tmp_path: Path) -> None:
    """Task listings should not cross tenant boundaries."""
    task_store = FileTaskStore(tmp_path)
    first_tenant = _tenant("T123TEAM")
    second_tenant = _tenant("T999TEAM")
    first_actor = _actor(first_tenant)
    second_actor = _actor(second_tenant)
    first = task_store.create_or_get_by_idempotency(
        tenant=first_tenant,
        idempotency_key="idem-first",
        create=lambda: _task(
            tenant=first_tenant,
            actor=first_actor,
            task_id="task-first",
            idempotency_key="idem-first",
        ),
    )
    task_store.create_or_get_by_idempotency(
        tenant=second_tenant,
        idempotency_key="idem-second",
        create=lambda: _task(
            tenant=second_tenant,
            actor=second_actor,
            task_id="task-second",
            idempotency_key="idem-second",
        ),
    )

    assert task_store.list_for_tenant(tenant=first_tenant) == (first,)
    assert (
        task_store.list_for_tenant(
            tenant=first_tenant,
            status=TaskStatus.PLANNING,
        )
        == ()
    )
    assert task_store.get(tenant=second_tenant, task_id=first.task_id) is None


def test_task_create_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task creation and event append should share one transaction."""
    event_store = FileSessionEventStore(tmp_path)
    task_store = FileTaskStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)

    def fail_append(*_args: object, **_kwargs: object) -> None:
        msg = "simulated event write failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(stores_mod, "_append_event", fail_append)

    with pytest.raises(RuntimeError, match="simulated event write failure"):
        task_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key="idem-task",
            create=lambda: _task(tenant=tenant, actor=actor),
        )

    assert task_store.list_for_tenant(tenant=tenant) == ()


def test_task_transition_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed audit writes must not leave the task in a new state."""
    event_store = FileSessionEventStore(tmp_path)
    task_store = FileTaskStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    task = task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-task",
        create=lambda: _task(tenant=tenant, actor=actor),
    )

    def fail_append(*_args: object, **_kwargs: object) -> None:
        msg = "simulated event write failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(stores_mod, "_append_event", fail_append)

    with pytest.raises(RuntimeError, match="simulated event write failure"):
        task_store.transition(
            tenant=tenant,
            task_id=task.task_id,
            transition=TaskTransition(
                expected=TaskStatus.CREATED,
                next_status=TaskStatus.PLANNING,
                event_type="task_planning_started",
                event_payload={"reason": "classified backup workflow"},
            ),
        )

    current = task_store.get(tenant=tenant, task_id=task.task_id)
    assert current is not None
    assert current.status is TaskStatus.CREATED
