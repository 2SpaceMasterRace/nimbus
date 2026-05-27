"""Workspace time-travel projection: reconstruct state from the event log.

This module exposes a single pure function ``project_workspace_at`` that folds
an ordered stream of ``SessionEvent`` records into a ``WorkspaceSnapshot``.
No side effects. No store writes. The caller is responsible for supplying
correctly scoped and ordered events.

Typical usage::

    events = event_store.list_for_tenant_before(
        tenant=tenant,
        before=target_ts,
    )
    snapshot = project_workspace_at(tenant=tenant, at=target_ts, events=events)

Event payload conventions relied upon (all guaranteed by the store impls):

- Every task event carries ``task_id`` and ``status`` in its payload.
- Every plan event carries ``plan_id``, ``status``, and ``risk_level``.
- Every approval event carries ``approval_id``, ``status``, ``risk_level``,
  ``exact_target``, and ``required_actor_id``.
- ``artifact_created`` events carry ``artifact_id`` in their payload.
- Unknown event types and missing optional fields are silently skipped so that
  schema evolution does not crash older projections.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nimbus_runtime.domain import (
    ApprovalSummary,
    FutureTimestampError,
    PlanSummary,
    WorkspaceSnapshot,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nimbus_runtime.domain import SessionEvent, TenantIdentity

logger = logging.getLogger(__name__)

_STATUS_KEY = "status"
_TASK_ID_KEY = "task_id"
_PLAN_ID_KEY = "plan_id"
_APPROVAL_ID_KEY = "approval_id"
_ARTIFACT_CREATED = "artifact_created"
_APPROVAL_PENDING = "pending"
_PLAN_PROPOSED = "proposed"


@dataclass
class _ProjectionState:
    """Mutable accumulator while folding events."""

    task_status_by_id: dict[str, str] = field(default_factory=dict)
    plan_by_id: dict[str, _PlanState] = field(default_factory=dict)
    approval_by_id: dict[str, _ApprovalState] = field(default_factory=dict)
    artifact_count: int = 0
    events_replayed: int = 0


@dataclass
class _PlanState:
    plan_id: str
    task_id: str | None
    title: str
    risk_level: str
    status: str


@dataclass
class _ApprovalState:
    approval_id: str
    task_id: str | None
    plan_id: str | None
    exact_target: str
    risk_level: str
    required_actor_id: str
    status: str


def _str_or_none(value: object) -> str | None:
    """Return value as str if it is a non-empty string, else None."""
    return str(value) if isinstance(value, str) else None


def _str_field(payload: object, key: str) -> str | None:
    """Safely extract a string field from a payload mapping."""
    if not isinstance(payload, dict):
        return None
    return _str_or_none(payload.get(key))


def _fold_event(state: _ProjectionState, event: SessionEvent) -> None:
    """Apply one event to the mutable projection state.

    Dispatch uses entity-ownership priority so that cross-reference fields (e.g.
    a plan event that carries task_id as a reference) do not accidentally update
    the wrong entity type.  Priority: approval > plan > task.
    """
    payload = event.payload
    state.events_replayed += 1

    # Approval events: carry approval_id as the primary entity key.
    # plan_id and task_id may appear as reference fields and must be ignored
    # for plan/task state updates.
    approval_id = _str_field(payload, _APPROVAL_ID_KEY)
    if approval_id is not None:
        new_status = _str_field(payload, _STATUS_KEY)
        if new_status is not None:
            existing_approval = state.approval_by_id.get(approval_id)
            exact_target = _str_field(payload, "exact_target") or (
                existing_approval.exact_target if existing_approval is not None else ""
            )
            risk_level = _str_field(payload, "risk_level") or (
                existing_approval.risk_level if existing_approval is not None else ""
            )
            required_actor_id = _str_field(payload, "required_actor_id") or (
                existing_approval.required_actor_id
                if existing_approval is not None
                else ""
            )
            plan_id_ref = _str_field(payload, _PLAN_ID_KEY) or (
                existing_approval.plan_id if existing_approval is not None else None
            )
            task_id_ref = _str_field(payload, _TASK_ID_KEY) or (
                existing_approval.task_id if existing_approval is not None else None
            )
            state.approval_by_id[approval_id] = _ApprovalState(
                approval_id=approval_id,
                task_id=task_id_ref,
                plan_id=plan_id_ref,
                exact_target=exact_target,
                risk_level=risk_level,
                required_actor_id=required_actor_id,
                status=new_status,
            )
        return  # approval is the primary entity; do not also update plan/task

    # Plan events: carry plan_id as the primary entity key.
    # task_id may appear as a reference and must not trigger a task state update.
    plan_id = _str_field(payload, _PLAN_ID_KEY)
    if plan_id is not None:
        new_status = _str_field(payload, _STATUS_KEY)
        if new_status is not None:
            existing_plan = state.plan_by_id.get(plan_id)
            title = _str_field(payload, "title") or (
                existing_plan.title if existing_plan is not None else ""
            )
            risk_level = _str_field(payload, "risk_level") or (
                existing_plan.risk_level if existing_plan is not None else ""
            )
            task_id_ref = _str_field(payload, _TASK_ID_KEY) or (
                existing_plan.task_id if existing_plan is not None else None
            )
            state.plan_by_id[plan_id] = _PlanState(
                plan_id=plan_id,
                task_id=task_id_ref,
                title=title,
                risk_level=risk_level,
                status=new_status,
            )
        return  # plan is the primary entity; do not also update task

    # Task events: carry task_id as the primary entity key.
    task_id = _str_field(payload, _TASK_ID_KEY)
    if task_id is not None:
        new_status = _str_field(payload, _STATUS_KEY)
        if new_status is not None:
            state.task_status_by_id[task_id] = new_status
        return

    # Artifact events have no entity ownership key, only a kind tag.
    if event.event_type == _ARTIFACT_CREATED:
        state.artifact_count += 1


def project_workspace_at(
    tenant: TenantIdentity,
    at: datetime,
    events: Iterable[SessionEvent],
    *,
    now: datetime | None = None,
) -> WorkspaceSnapshot:
    """Reconstruct workspace state at ``at`` by folding ``events``.

    ``events`` must be ordered by ``(created_at ASC, event_id ASC)`` and must
    contain only events with ``created_at <= at``.  The caller is responsible
    for both constraints; this function does not re-filter or re-sort.

    Raises:
        FutureTimestampError: if ``at`` is strictly after ``now`` (wall clock
            by default, injectable for testing).

    """
    wall_now = now if now is not None else datetime.now(UTC)
    if at > wall_now:
        msg = (
            f"projection timestamp {at.isoformat()} is in the future "
            f"(now is {wall_now.isoformat()})"
        )
        raise FutureTimestampError(msg)

    t0 = time.monotonic()
    state = _ProjectionState()

    for event in events:
        _fold_event(state, event)

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    computed_at = datetime.now(UTC)

    tasks_by_status: dict[str, int] = {}
    for status in state.task_status_by_id.values():
        tasks_by_status[status] = tasks_by_status.get(status, 0) + 1

    pending_approvals = tuple(
        ApprovalSummary(
            approval_id=a.approval_id,
            task_id=a.task_id,
            plan_id=a.plan_id,
            exact_target=a.exact_target,
            risk_level=a.risk_level,
            required_actor_id=a.required_actor_id,
        )
        for a in state.approval_by_id.values()
        if a.status == _APPROVAL_PENDING
    )

    pending_plans = tuple(
        PlanSummary(
            plan_id=p.plan_id,
            task_id=p.task_id,
            title=p.title,
            risk_level=p.risk_level,
            status=p.status,
        )
        for p in state.plan_by_id.values()
        if p.status == _PLAN_PROPOSED
    )

    return WorkspaceSnapshot(
        tenant=tenant,
        at=at,
        tasks_by_status=tasks_by_status,
        pending_approvals=pending_approvals,
        pending_plans=pending_plans,
        artifact_count=state.artifact_count,
        events_replayed=state.events_replayed,
        computed_at=computed_at,
        computation_duration_ms=elapsed_ms,
    )
