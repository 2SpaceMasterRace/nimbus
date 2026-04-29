"""Unit tests for Nimbus action and event store primitives."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import nimbus_runtime.stores as stores_mod
import pytest
from nimbus_runtime.domain import (
    Action,
    ActionKind,
    ActionStatus,
    ActionTransition,
    Artifact,
    DeleteFileInput,
    DeleteFileResult,
    ObjectRef,
    TenantIdentity,
    UploadReport,
    VerifiedActor,
    validate_action_transition,
)
from nimbus_runtime.stores import (
    FileActionStore,
    FileArtifactStore,
    FileSessionEventStore,
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


def _delete_action(
    *,
    tenant: TenantIdentity,
    actor: VerifiedActor,
    action_id: str = "act-test",
    idempotency_key: str = "idem-test",
) -> Action:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return Action(
        action_id=action_id,
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        actor=actor,
        kind=ActionKind.DELETE_FILE,
        target=ObjectRef(
            provider="s3",
            container="bucket",
            object_name="reports/old.csv",
        ),
        status=ActionStatus.AWAITING_CONFIRMATION,
        idempotency_key=idempotency_key,
        input=DeleteFileInput(remote_path="reports/old.csv"),
        result=None,
        failure=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(minutes=15),
    )


def test_invalid_action_transition_is_rejected() -> None:
    """Terminal actions should not transition back to executing."""
    with pytest.raises(ValueError, match="invalid action transition"):
        validate_action_transition(
            expected=ActionStatus.SUCCEEDED,
            next_status=ActionStatus.EXECUTING,
        )


def test_file_action_store_creates_action_once_by_idempotency(
    tmp_path: Path,
) -> None:
    """Duplicate logical requests should return the original action."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    calls = 0

    def create() -> Action:
        nonlocal calls
        calls += 1
        return _delete_action(tenant=tenant, actor=actor)

    first = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=create,
    )
    second = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=create,
    )

    assert first == second
    assert calls == 1
    events = event_store.list_events(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
    )
    assert [event.event_type for event in events] == ["action_created"]


def test_file_action_store_transition_is_compare_and_set(tmp_path: Path) -> None:
    """Only callers that see the expected state should move an action."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    action = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=lambda: _delete_action(tenant=tenant, actor=actor),
    )

    authorized = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.AWAITING_CONFIRMATION,
            next_status=ActionStatus.AUTHORIZED,
            event_type="action_authorized",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )
    stale = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.AWAITING_CONFIRMATION,
            next_status=ActionStatus.AUTHORIZED,
            event_type="action_authorized",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )

    assert authorized is not None
    assert authorized.status is ActionStatus.AUTHORIZED
    assert stale is None
    events = event_store.list_events(
        tenant=tenant,
        session_id=action.session_id,
    )
    assert [event.event_type for event in events] == [
        "action_created",
        "action_authorized",
    ]


def test_file_artifact_store_persists_artifacts_and_appends_events(
    tmp_path: Path,
) -> None:
    """Artifacts should be durable evidence linked back into the session log."""
    event_store = FileSessionEventStore(tmp_path)
    artifact_store = FileArtifactStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    artifact = Artifact(
        artifact_id="art-test",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        action_id="act-test",
        kind="upload_report",
        uri=None,
        payload=UploadReport(
            remote_path="reports/new.csv",
            filename="new.csv",
            size_bytes=12,
            sha256_hex="0" * 64,
        ),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    created = artifact_store.create(artifact=artifact, actor=actor)
    repeated = artifact_store.create(artifact=artifact, actor=actor)

    assert created == artifact
    assert repeated == artifact
    assert artifact_store.list_for_session(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
    ) == (artifact,)
    events = event_store.list_events(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
    )
    assert [event.event_type for event in events] == ["artifact_created"]


def test_artifact_create_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifacts and their creation events should commit together."""
    event_store = FileSessionEventStore(tmp_path)
    artifact_store = FileArtifactStore(tmp_path, event_store=event_store)
    tenant = _tenant()

    def fail_append(*_args: object, **_kwargs: object) -> None:
        msg = "simulated event write failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(stores_mod, "_append_event", fail_append)

    artifact = Artifact(
        artifact_id="art-test",
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:thread",
        action_id="act-test",
        kind="upload_report",
        uri=None,
        payload=UploadReport(
            remote_path="reports/new.csv",
            filename="new.csv",
            size_bytes=12,
            sha256_hex="0" * 64,
        ),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="simulated event write failure"):
        artifact_store.create(artifact=artifact, actor=_actor(tenant))

    assert (
        artifact_store.list_for_session(
            tenant=tenant,
            session_id="slack:T123TEAM:C123CHAN:thread",
        )
        == ()
    )


def test_action_transition_persists_typed_result(tmp_path: Path) -> None:
    """Action results should round-trip as typed payloads, not stringly dicts."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    action = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=lambda: _delete_action(tenant=tenant, actor=actor),
    )

    authorized = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.AWAITING_CONFIRMATION,
            next_status=ActionStatus.AUTHORIZED,
            event_type="action_authorized",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )
    assert authorized is not None
    executing = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.AUTHORIZED,
            next_status=ActionStatus.EXECUTING,
            event_type="action_started",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )
    assert executing is not None
    verifying = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.EXECUTING,
            next_status=ActionStatus.VERIFYING,
            event_type="verification_started",
            event_payload={"remote_path": "reports/old.csv"},
        ),
    )
    assert verifying is not None
    completed = action_store.transition(
        tenant=tenant,
        action_id=action.action_id,
        transition=ActionTransition(
            expected=ActionStatus.VERIFYING,
            next_status=ActionStatus.SUCCEEDED,
            event_type="action_completed",
            event_payload={"remote_path": "reports/old.csv"},
            result=DeleteFileResult(
                remote_path="reports/old.csv",
                deleted=True,
                version_id=None,
                artifact_id="art-test",
            ),
        ),
    )

    assert completed is not None
    assert completed.result == DeleteFileResult(
        remote_path="reports/old.csv",
        deleted=True,
        version_id=None,
        artifact_id="art-test",
    )
    assert action_store.get(tenant=tenant, action_id=action.action_id) == completed


def test_action_create_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Action creation and event append should share one transaction."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)

    def fail_append(*_args: object, **_kwargs: object) -> None:
        msg = "simulated event write failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(stores_mod, "_append_event", fail_append)

    with pytest.raises(RuntimeError, match="simulated event write failure"):
        action_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key="idem-test",
            create=lambda: _delete_action(tenant=tenant, actor=actor),
        )

    assert (
        action_store.list_for_session(
            tenant=tenant,
            session_id="slack:T123TEAM:C123CHAN:thread",
        )
        == ()
    )


def test_action_transition_rolls_back_when_event_append_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failed audit writes must not leave the action in a new state."""
    event_store = FileSessionEventStore(tmp_path)
    action_store = FileActionStore(tmp_path, event_store=event_store)
    tenant = _tenant()
    actor = _actor(tenant)
    action = action_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-test",
        create=lambda: _delete_action(tenant=tenant, actor=actor),
    )

    def fail_append(*_args: object, **_kwargs: object) -> None:
        msg = "simulated event write failure"
        raise RuntimeError(msg)

    monkeypatch.setattr(stores_mod, "_append_event", fail_append)

    with pytest.raises(RuntimeError, match="simulated event write failure"):
        action_store.transition(
            tenant=tenant,
            action_id=action.action_id,
            transition=ActionTransition(
                expected=ActionStatus.AWAITING_CONFIRMATION,
                next_status=ActionStatus.AUTHORIZED,
                event_type="action_authorized",
                event_payload={"remote_path": "reports/old.csv"},
            ),
        )

    current = action_store.get(tenant=tenant, action_id=action.action_id)
    assert current is not None
    assert current.status is ActionStatus.AWAITING_CONFIRMATION
