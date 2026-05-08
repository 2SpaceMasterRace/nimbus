"""Nimbus action, artifact, and session event stores.

Render deployments use Postgres-backed stores. Local development and tests keep
the ``File*Store`` classes, which use SQLite under ``AI_SESSION_DIR`` to provide
one transaction boundary for idempotency, action state, and audit events.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol, cast

from nimbus_runtime.domain import (
    Action,
    ActionFailure,
    ActionInput,
    ActionKind,
    ActionResult,
    ActionStatus,
    ActionTransition,
    ActorAuthSource,
    Artifact,
    ArtifactPayload,
    DeleteFileInput,
    DeleteFileResult,
    DeleteReport,
    ObjectRef,
    ProviderName,
    SessionEvent,
    TenantIdentity,
    UploadAttachmentInput,
    UploadAttachmentResult,
    UploadReport,
    VerifiedActor,
    validate_action_transition,
)
from nimbus_runtime.postgres import connect as pg_connect
from nimbus_runtime.postgres import transaction as pg_transaction

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from pathlib import Path

    from psycopg import Connection as PostgresConnection
_DB_FILENAME = "nimbus_runtime.sqlite3"
_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5_000
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class SessionEventStore(Protocol):
    """Durable ordered event store for Nimbus sessions."""

    def append(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        event_type: str,
        actor: VerifiedActor | None,
        payload: Mapping[str, object],
    ) -> SessionEvent:
        """Append one event and return it with a sequence number."""

    def list_events(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> Sequence[SessionEvent]:
        """Return ordered events for one tenant-scoped session."""


class ActionStore(Protocol):
    """Durable store for action creation and state transitions."""

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Action],
    ) -> Action:
        """Create an action once for a logical tenant-scoped request."""

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
        transition: ActionTransition,
    ) -> Action | None:
        """Move an action only if it is still in the expected state."""

    def get(self, *, tenant: TenantIdentity, action_id: str) -> Action | None:
        """Return one tenant-scoped action if it exists."""

    def find_latest_awaiting_confirmation(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        kind: ActionKind,
    ) -> Action | None:
        """Return the newest action waiting for confirmation in one session."""


class ArtifactStore(Protocol):
    """Durable store for Nimbus evidence and work products."""

    def create(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None = None,
    ) -> Artifact:
        """Persist one immutable artifact."""

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Artifact]:
        """Return artifacts for one tenant-scoped session."""


@dataclass(frozen=True, slots=True)
class _EventAppend:
    """Append-only event payload inside one SQLite transaction."""

    tenant: TenantIdentity
    session_id: str
    event_type: str
    actor: VerifiedActor | None
    payload: Mapping[str, object]


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[resolved] = lock
        return lock


def _json_dumps(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _json_loads_object(raw: str | None, *, field: str) -> dict[str, object] | None:
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        msg = f"expected JSON object for {field}"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _required_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        msg = f"expected string field {key!r}"
        raise TypeError(msg)
    return value


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"expected optional string field {key!r}"
    raise TypeError(msg)


def _required_int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"expected integer field {key!r}"
        raise TypeError(msg)
    return value


def _required_bool(data: Mapping[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        msg = f"expected boolean field {key!r}"
        raise TypeError(msg)
    return value


def _required_mapping(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        msg = f"expected object field {key!r}"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _optional_mapping(
    data: Mapping[str, object],
    key: str,
) -> dict[str, object] | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    msg = f"expected optional object field {key!r}"
    raise TypeError(msg)


def _datetime_to_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime_from_json(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime_from_json(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _tenant_to_json(tenant: TenantIdentity) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "platform": tenant.platform,
        "workspace_id": tenant.workspace_id,
    }


def _tenant_from_json(data: Mapping[str, object]) -> TenantIdentity:
    return TenantIdentity(
        platform=_required_str(data, "platform"),
        workspace_id=_required_str(data, "workspace_id"),
    )


def _actor_to_json(actor: VerifiedActor | None) -> dict[str, object] | None:
    if actor is None:
        return None
    return {
        "schema_version": _SCHEMA_VERSION,
        "tenant": _tenant_to_json(actor.tenant),
        "user_id": actor.user_id,
        "auth_source": actor.auth_source,
        "bridge_id": actor.bridge_id,
        "verified_at": _datetime_to_json(actor.verified_at),
        "principal_key": actor.principal_key,
    }


def _actor_from_json(data: Mapping[str, object] | None) -> VerifiedActor | None:
    if data is None:
        return None
    return VerifiedActor(
        tenant=_tenant_from_json(_required_mapping(data, "tenant")),
        user_id=_required_str(data, "user_id"),
        auth_source=cast("ActorAuthSource", _required_str(data, "auth_source")),
        bridge_id=_optional_str(data, "bridge_id"),
        verified_at=_datetime_from_json(_required_str(data, "verified_at")),
    )


def _object_ref_to_json(target: ObjectRef | None) -> dict[str, object] | None:
    if target is None:
        return None
    return {
        "schema_version": _SCHEMA_VERSION,
        "provider": target.provider,
        "container": target.container,
        "object_name": target.object_name,
        "version_id": target.version_id,
    }


def _object_ref_from_json(data: Mapping[str, object] | None) -> ObjectRef | None:
    if data is None:
        return None
    return ObjectRef(
        provider=cast("ProviderName", _required_str(data, "provider")),
        container=_required_str(data, "container"),
        object_name=_required_str(data, "object_name"),
        version_id=_optional_str(data, "version_id"),
    )


def _action_input_to_json(payload: ActionInput) -> dict[str, object]:
    if isinstance(payload, DeleteFileInput):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.DELETE_FILE.value,
            "remote_path": payload.remote_path,
        }
    if isinstance(payload, UploadAttachmentInput):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.UPLOAD_ATTACHMENT.value,
            "platform_file_id": payload.platform_file_id,
            "filename": payload.filename,
            "content_type": payload.content_type,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
            "remote_path": payload.remote_path,
        }
    msg = f"unsupported action input: {type(payload).__name__}"
    raise TypeError(msg)


def _action_input_from_json(
    *, kind: ActionKind, data: Mapping[str, object]
) -> ActionInput:
    if kind is ActionKind.DELETE_FILE:
        return DeleteFileInput(remote_path=_required_str(data, "remote_path"))
    if kind is ActionKind.UPLOAD_ATTACHMENT:
        return UploadAttachmentInput(
            platform_file_id=_required_str(data, "platform_file_id"),
            filename=_required_str(data, "filename"),
            content_type=_required_str(data, "content_type"),
            size_bytes=_required_int(data, "size_bytes"),
            sha256_hex=_optional_str(data, "sha256_hex"),
            remote_path=_required_str(data, "remote_path"),
        )
    msg = f"unsupported action input kind: {kind.value}"
    raise ValueError(msg)


def _action_result_to_json(payload: ActionResult | None) -> dict[str, object] | None:
    if payload is None:
        return None
    if isinstance(payload, DeleteFileResult):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.DELETE_FILE.value,
            "remote_path": payload.remote_path,
            "deleted": payload.deleted,
            "version_id": payload.version_id,
            "artifact_id": payload.artifact_id,
        }
    if isinstance(payload, UploadAttachmentResult):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.UPLOAD_ATTACHMENT.value,
            "remote_path": payload.remote_path,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
            "artifact_id": payload.artifact_id,
        }
    msg = f"unsupported action result: {type(payload).__name__}"
    raise TypeError(msg)


def _action_result_from_json(
    *, kind: ActionKind, data: Mapping[str, object] | None
) -> ActionResult | None:
    if data is None:
        return None
    if kind is ActionKind.DELETE_FILE:
        return DeleteFileResult(
            remote_path=_required_str(data, "remote_path"),
            deleted=_required_bool(data, "deleted"),
            version_id=_optional_str(data, "version_id"),
            artifact_id=_optional_str(data, "artifact_id"),
        )
    if kind is ActionKind.UPLOAD_ATTACHMENT:
        return UploadAttachmentResult(
            remote_path=_required_str(data, "remote_path"),
            size_bytes=_required_int(data, "size_bytes"),
            sha256_hex=_required_str(data, "sha256_hex"),
            artifact_id=_optional_str(data, "artifact_id"),
        )
    msg = f"unsupported action result kind: {kind.value}"
    raise ValueError(msg)


def _action_failure_to_json(payload: ActionFailure | None) -> dict[str, object] | None:
    if payload is None:
        return None
    return {
        "schema_version": _SCHEMA_VERSION,
        "detail": payload.detail,
        "remote_path": payload.remote_path,
    }


def _action_failure_from_json(
    data: Mapping[str, object] | None,
) -> ActionFailure | None:
    if data is None:
        return None
    return ActionFailure(
        detail=_required_str(data, "detail"),
        remote_path=_optional_str(data, "remote_path"),
    )


def _artifact_payload_to_json(payload: ArtifactPayload) -> dict[str, object]:
    if isinstance(payload, DeleteReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "delete_report",
            "remote_path": payload.remote_path,
            "deleted": payload.deleted,
            "version_id": payload.version_id,
        }
    if isinstance(payload, UploadReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "upload_report",
            "remote_path": payload.remote_path,
            "filename": payload.filename,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
        }
    msg = f"unsupported artifact payload: {type(payload).__name__}"
    raise TypeError(msg)


def _artifact_payload_from_json(
    *, kind: str, data: Mapping[str, object]
) -> ArtifactPayload:
    if kind == "delete_report":
        return DeleteReport(
            remote_path=_required_str(data, "remote_path"),
            deleted=_required_bool(data, "deleted"),
            version_id=_optional_str(data, "version_id"),
        )
    if kind == "upload_report":
        return UploadReport(
            remote_path=_required_str(data, "remote_path"),
            filename=_required_str(data, "filename"),
            size_bytes=_required_int(data, "size_bytes"),
            sha256_hex=_required_str(data, "sha256_hex"),
        )
    msg = f"unsupported artifact kind: {kind}"
    raise ValueError(msg)


def _row_str(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        msg = f"expected SQLite text column {key!r}"
        raise TypeError(msg)
    return value


def _row_optional_str(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is None or isinstance(value, str):
        return value
    msg = f"expected optional SQLite text column {key!r}"
    raise TypeError(msg)


def _row_int(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"expected SQLite integer column {key!r}"
        raise TypeError(msg)
    return cast("int", value)


def _safe_row(
    parser: Callable[[sqlite3.Row], object],
    row: sqlite3.Row,
) -> object | None:
    try:
        return parser(row)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


class _SQLiteStore:
    """Shared SQLite connection and schema helper."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._db_path = root / _DB_FILENAME
        self._lock = _path_lock(self._db_path)

    @property
    def db_path(self) -> Path:
        """Return the backing SQLite file path."""
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        self._root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._db_path, timeout=5.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = FULL")
        self._ensure_schema(con)
        return con

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
            except Exception:
                con.execute("ROLLBACK")
                raise
            else:
                con.execute("COMMIT")
            finally:
                con.close()

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS session_events (
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_id TEXT NOT NULL PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor_json TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                UNIQUE (tenant_id, session_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS session_events_by_session
                ON session_events (tenant_id, session_id, sequence);
            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                session_id TEXT NOT NULL,
                actor_json TEXT NOT NULL,
                kind TEXT NOT NULL,
                target_json TEXT,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                input_json TEXT NOT NULL,
                result_json TEXT,
                failure_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                schema_version INTEGER NOT NULL,
                UNIQUE (tenant_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS actions_by_session
                ON actions (tenant_id, session_id, created_at);
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                session_id TEXT NOT NULL,
                action_id TEXT,
                kind TEXT NOT NULL,
                uri TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS artifacts_by_session
                ON artifacts (tenant_id, session_id, created_at);
            """
        )
        con.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _append_event(con: sqlite3.Connection, event: _EventAppend) -> SessionEvent:
    row = con.execute(
        """
        SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
        FROM session_events
        WHERE tenant_id = ? AND session_id = ?
        """,
        (event.tenant.tenant_id, event.session_id),
    ).fetchone()
    sequence = _row_int(row, "next_sequence")
    session_event = SessionEvent(
        tenant=event.tenant,
        session_id=event.session_id,
        sequence=sequence,
        event_id=f"evt-{uuid.uuid4().hex}",
        event_type=event.event_type,
        actor=event.actor,
        payload=dict(event.payload),
        created_at=datetime.now(UTC),
    )
    con.execute(
        """
        INSERT INTO session_events (
            tenant_id, session_id, sequence, event_id, event_type,
            actor_json, payload_json, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.tenant.tenant_id,
            event.session_id,
            sequence,
            session_event.event_id,
            event.event_type,
            _json_dumps(_actor_to_json(event.actor)),
            _json_dumps(dict(event.payload)),
            _datetime_to_json(session_event.created_at),
            _SCHEMA_VERSION,
        ),
    )
    return session_event


class FileSessionEventStore(_SQLiteStore):
    """SQLite-backed ordered event store for local Nimbus deployments."""

    def __init__(self, root: Path) -> None:
        """Create an event store under ``root``."""
        super().__init__(root)

    def append(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        event_type: str,
        actor: VerifiedActor | None,
        payload: Mapping[str, object],
    ) -> SessionEvent:
        """Append one event and return it with a sequence number."""
        with self._transaction() as con:
            return _append_event(
                con,
                _EventAppend(
                    tenant=tenant,
                    session_id=session_id,
                    event_type=event_type,
                    actor=actor,
                    payload=payload,
                ),
            )

    def list_events(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> Sequence[SessionEvent]:
        """Return ordered events for one tenant-scoped session."""
        query = (
            "SELECT * FROM session_events "
            "WHERE tenant_id = ? AND session_id = ? AND sequence > ? "
            "ORDER BY sequence ASC LIMIT ?"
        )
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    query,
                    (
                        tenant.tenant_id,
                        session_id,
                        0 if after_sequence is None else after_sequence,
                        limit,
                    ),
                ).fetchall()
            finally:
                con.close()
        events = [
            event
            for row in rows
            if (event := _safe_row(self._event_from_row, row)) is not None
        ]
        return tuple(cast("SessionEvent", event) for event in events)

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> SessionEvent:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported event schema version"
            raise ValueError(msg)
        actor_data = _json_loads_object(
            _row_optional_str(row, "actor_json"),
            field="actor",
        )
        payload_data = _json_loads_object(
            _row_str(row, "payload_json"),
            field="payload",
        )
        return SessionEvent(
            tenant=TenantIdentity(
                platform=_row_str(row, "tenant_id").split(":", maxsplit=1)[0],
                workspace_id=_row_str(row, "tenant_id").split(":", maxsplit=1)[1],
            ),
            session_id=_row_str(row, "session_id"),
            sequence=_row_int(row, "sequence"),
            event_id=_row_str(row, "event_id"),
            event_type=_row_str(row, "event_type"),
            actor=_actor_from_json(actor_data),
            payload={} if payload_data is None else payload_data,
            created_at=_datetime_from_json(_row_str(row, "created_at")),
        )


class FileActionStore(_SQLiteStore):
    """SQLite-backed action store with transactional idempotency and events."""

    def __init__(
        self,
        root: Path,
        *,
        event_store: SessionEventStore | None = None,
    ) -> None:
        """Create an action store under ``root``."""
        super().__init__(root)
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Action],
    ) -> Action:
        """Create an action once for a logical tenant-scoped request."""
        external_event: tuple[Action, str, Mapping[str, object]] | None = None
        with self._transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            action = create()
            self._validate_new_action(
                tenant=tenant,
                idempotency_key=idempotency_key,
                action=action,
            )
            self._insert_action(con, action)
            event_payload: Mapping[str, object] = {
                "status": action.status.value,
                "kind": action.kind.value,
            }
            if self._event_store_in_same_db():
                self._append_action_event_with_connection(
                    con,
                    action=action,
                    event_type="action_created",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (action, "action_created", event_payload)
        if external_event is not None:
            event_action, event_type, event_payload = external_event
            self._append_action_event(
                action=event_action,
                event_type=event_type,
                event_payload=event_payload,
            )
        return action

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
        transition: ActionTransition,
    ) -> Action | None:
        """Move an action only if it is still in the expected state."""
        validate_action_transition(
            expected=transition.expected,
            next_status=transition.next_status,
        )
        external_event: tuple[Action, str, Mapping[str, object]] | None = None
        with self._transaction() as con:
            action = self._read_action_with_connection(
                con,
                tenant=tenant,
                action_id=action_id,
            )
            if action is None or action.status is not transition.expected:
                return None
            updated = replace(
                action,
                status=transition.next_status,
                result=(
                    transition.result
                    if transition.result is not None
                    else action.result
                ),
                failure=(
                    transition.failure
                    if transition.failure is not None
                    else action.failure
                ),
                updated_at=datetime.now(UTC),
            )
            self._update_action(con, updated)
            if self._event_store_in_same_db():
                self._append_action_event_with_connection(
                    con,
                    action=updated,
                    event_type=transition.event_type,
                    event_payload=transition.event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    updated,
                    transition.event_type,
                    transition.event_payload,
                )
        if external_event is not None:
            event_action, event_type, event_payload = external_event
            self._append_action_event(
                action=event_action,
                event_type=event_type,
                event_payload=event_payload,
            )
        return updated

    def get(self, *, tenant: TenantIdentity, action_id: str) -> Action | None:
        """Return one tenant-scoped action if it exists."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_action_with_connection(
                    con,
                    tenant=tenant,
                    action_id=action_id,
                )
            finally:
                con.close()

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Action]:
        """Return all known actions for one tenant-scoped session."""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT * FROM actions
                    WHERE tenant_id = ? AND session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (tenant.tenant_id, session_id),
                ).fetchall()
            finally:
                con.close()
        actions = [
            action
            for row in rows
            if (action := _safe_row(self._action_from_row, row)) is not None
        ]
        return tuple(cast("Action", action) for action in actions)

    def find_latest_awaiting_confirmation(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        kind: ActionKind,
    ) -> Action | None:
        """Return the newest action waiting for confirmation in one session."""
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT * FROM actions
                    WHERE tenant_id = ?
                      AND session_id = ?
                      AND kind = ?
                      AND status = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (
                        tenant.tenant_id,
                        session_id,
                        kind.value,
                        ActionStatus.AWAITING_CONFIRMATION.value,
                    ),
                ).fetchone()
            finally:
                con.close()
        if row is None:
            return None
        parsed = _safe_row(self._action_from_row, row)
        return cast("Action | None", parsed)

    def _event_store_in_same_db(self) -> bool:
        return (
            isinstance(self._event_store, FileSessionEventStore)
            and self._event_store.db_path == self._db_path
        )

    def _append_action_event_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        action: Action,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        payload = {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            **dict(event_payload),
        }
        _append_event(
            con,
            _EventAppend(
                tenant=action.tenant,
                session_id=action.session_id,
                event_type=event_type,
                actor=action.actor,
                payload=payload,
            ),
        )

    def _append_action_event(
        self,
        *,
        action: Action,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            **dict(event_payload),
        }
        self._event_store.append(
            tenant=action.tenant,
            session_id=action.session_id,
            event_type=event_type,
            actor=action.actor,
            payload=payload,
        )

    @staticmethod
    def _insert_action(con: sqlite3.Connection, action: Action) -> None:
        con.execute(
            """
            INSERT INTO actions (
                action_id, tenant_id, tenant_json, session_id, actor_json,
                kind, target_json, status, idempotency_key, input_json,
                result_json, failure_json, created_at, updated_at, expires_at,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _action_sql_values(action),
        )

    @staticmethod
    def _update_action(con: sqlite3.Connection, action: Action) -> None:
        con.execute(
            """
            UPDATE actions
            SET status = ?,
                result_json = ?,
                failure_json = ?,
                updated_at = ?,
                expires_at = ?,
                schema_version = ?
            WHERE tenant_id = ? AND action_id = ?
            """,
            (
                action.status.value,
                _json_dumps(_action_result_to_json(action.result)),
                _json_dumps(_action_failure_to_json(action.failure)),
                _datetime_to_json(action.updated_at),
                (
                    None
                    if action.expires_at is None
                    else _datetime_to_json(action.expires_at)
                ),
                _SCHEMA_VERSION,
                action.tenant.tenant_id,
                action.action_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Action | None:
        row = con.execute(
            """
            SELECT * FROM actions
            WHERE tenant_id = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._action_from_row, row)
        return cast("Action | None", parsed)

    def _read_action_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        action_id: str,
    ) -> Action | None:
        row = con.execute(
            """
            SELECT * FROM actions
            WHERE tenant_id = ? AND action_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, action_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._action_from_row, row)
        return cast("Action | None", parsed)

    @staticmethod
    def _action_from_row(row: sqlite3.Row) -> Action:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported action schema version"
            raise ValueError(msg)
        kind = ActionKind(_row_str(row, "kind"))
        tenant_data = _json_loads_object(_row_str(row, "tenant_json"), field="tenant")
        actor_data = _json_loads_object(_row_str(row, "actor_json"), field="actor")
        target_data = _json_loads_object(
            _row_optional_str(row, "target_json"),
            field="target",
        )
        input_data = _json_loads_object(_row_str(row, "input_json"), field="input")
        result_data = _json_loads_object(
            _row_optional_str(row, "result_json"),
            field="result",
        )
        failure_data = _json_loads_object(
            _row_optional_str(row, "failure_json"), field="failure"
        )
        actor = _actor_from_json(actor_data)
        if actor is None:
            msg = "action actor cannot be null"
            raise TypeError(msg)
        if tenant_data is None or input_data is None:
            msg = "action row is missing required JSON fields"
            raise TypeError(msg)
        return Action(
            action_id=_row_str(row, "action_id"),
            tenant=_tenant_from_json(tenant_data),
            session_id=_row_str(row, "session_id"),
            actor=actor,
            kind=kind,
            target=_object_ref_from_json(target_data),
            status=ActionStatus(_row_str(row, "status")),
            idempotency_key=_row_str(row, "idempotency_key"),
            input=_action_input_from_json(kind=kind, data=input_data),
            result=_action_result_from_json(kind=kind, data=result_data),
            failure=_action_failure_from_json(failure_data),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
            updated_at=_datetime_from_json(_row_str(row, "updated_at")),
            expires_at=_optional_datetime_from_json(
                _row_optional_str(row, "expires_at")
            ),
        )

    @staticmethod
    def _validate_new_action(
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        action: Action,
    ) -> None:
        if action.tenant != tenant:
            msg = "created action tenant does not match idempotency tenant"
            raise ValueError(msg)
        if action.idempotency_key != idempotency_key:
            msg = "created action idempotency key does not match request key"
            raise ValueError(msg)


def _action_sql_values(action: Action) -> tuple[object, ...]:
    return (
        action.action_id,
        action.tenant.tenant_id,
        _json_dumps(_tenant_to_json(action.tenant)),
        action.session_id,
        _json_dumps(_actor_to_json(action.actor)),
        action.kind.value,
        _json_dumps(_object_ref_to_json(action.target)),
        action.status.value,
        action.idempotency_key,
        _json_dumps(_action_input_to_json(action.input)),
        _json_dumps(_action_result_to_json(action.result)),
        _json_dumps(_action_failure_to_json(action.failure)),
        _datetime_to_json(action.created_at),
        _datetime_to_json(action.updated_at),
        None if action.expires_at is None else _datetime_to_json(action.expires_at),
        _SCHEMA_VERSION,
    )


class FileArtifactStore(_SQLiteStore):
    """SQLite-backed immutable artifact store for local Nimbus deployments."""

    def __init__(
        self,
        root: Path,
        *,
        event_store: SessionEventStore | None = None,
    ) -> None:
        """Create an artifact store under ``root``."""
        super().__init__(root)
        self._event_store = event_store

    def create(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None = None,
    ) -> Artifact:
        """Persist one immutable artifact."""
        external_event: tuple[Artifact, VerifiedActor | None] | None = None
        with self._transaction() as con:
            existing = self._read_artifact_with_connection(
                con,
                tenant=artifact.tenant,
                artifact_id=artifact.artifact_id,
            )
            if existing is not None:
                return existing
            self._insert_artifact(con, artifact)
            if self._event_store_in_same_db():
                self._append_artifact_event_with_connection(
                    con,
                    artifact=artifact,
                    actor=actor,
                )
            elif self._event_store is not None:
                external_event = (artifact, actor)
        if external_event is not None:
            artifact, actor = external_event
            self._append_artifact_event(artifact=artifact, actor=actor)
        return artifact

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Artifact]:
        """Return artifacts for one tenant-scoped session."""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE tenant_id = ? AND session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (tenant.tenant_id, session_id),
                ).fetchall()
            finally:
                con.close()
        artifacts = [
            artifact
            for row in rows
            if (artifact := _safe_row(self._artifact_from_row, row)) is not None
        ]
        return tuple(cast("Artifact", artifact) for artifact in artifacts)

    def _event_store_in_same_db(self) -> bool:
        return (
            isinstance(self._event_store, FileSessionEventStore)
            and self._event_store.db_path == self._db_path
        )

    @staticmethod
    def _insert_artifact(con: sqlite3.Connection, artifact: Artifact) -> None:
        con.execute(
            """
            INSERT INTO artifacts (
                artifact_id, tenant_id, tenant_json, session_id, action_id,
                kind, uri, payload_json, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.tenant.tenant_id,
                _json_dumps(_tenant_to_json(artifact.tenant)),
                artifact.session_id,
                artifact.action_id,
                artifact.kind,
                artifact.uri,
                _json_dumps(_artifact_payload_to_json(artifact.payload)),
                _datetime_to_json(artifact.created_at),
                _SCHEMA_VERSION,
            ),
        )

    def _read_artifact_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        artifact_id: str,
    ) -> Artifact | None:
        row = con.execute(
            """
            SELECT * FROM artifacts
            WHERE tenant_id = ? AND artifact_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, artifact_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._artifact_from_row, row)
        return cast("Artifact | None", parsed)

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> Artifact:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported artifact schema version"
            raise ValueError(msg)
        kind = _row_str(row, "kind")
        if kind not in {"delete_report", "upload_report"}:
            msg = f"unsupported artifact kind: {kind}"
            raise ValueError(msg)
        tenant_data = _json_loads_object(_row_str(row, "tenant_json"), field="tenant")
        payload_data = _json_loads_object(
            _row_str(row, "payload_json"),
            field="payload",
        )
        if tenant_data is None or payload_data is None:
            msg = "artifact row is missing required JSON fields"
            raise TypeError(msg)
        return Artifact(
            artifact_id=_row_str(row, "artifact_id"),
            tenant=_tenant_from_json(tenant_data),
            session_id=_row_str(row, "session_id"),
            action_id=_row_optional_str(row, "action_id"),
            kind=cast("Literal['delete_report', 'upload_report']", kind),
            uri=_row_optional_str(row, "uri"),
            payload=_artifact_payload_from_json(kind=kind, data=payload_data),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
        )

    def _append_artifact_event_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        _append_event(
            con,
            _EventAppend(
                tenant=artifact.tenant,
                session_id=artifact.session_id,
                event_type="artifact_created",
                actor=actor,
                payload={
                    "artifact_id": artifact.artifact_id,
                    "action_id": artifact.action_id,
                    "kind": artifact.kind,
                },
            ),
        )

    def _append_artifact_event(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        if self._event_store is None:
            return
        self._event_store.append(
            tenant=artifact.tenant,
            session_id=artifact.session_id,
            event_type="artifact_created",
            actor=actor,
            payload={
                "artifact_id": artifact.artifact_id,
                "action_id": artifact.action_id,
                "kind": artifact.kind,
            },
        )


def _append_event_postgres(
    con: PostgresConnection[dict[str, object]],
    event: _EventAppend,
) -> SessionEvent:
    """Append an ordered session event using Postgres advisory locking."""
    lock_key = f"{event.tenant.tenant_id}:{event.session_id}"
    con.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
    row = con.execute(
        """
        SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
        FROM session_events
        WHERE tenant_id = %s AND session_id = %s
        """,
        (event.tenant.tenant_id, event.session_id),
    ).fetchone()
    if row is None:
        msg = "Postgres did not return the next event sequence"
        raise RuntimeError(msg)
    raw_sequence = row["next_sequence"]
    if not isinstance(raw_sequence, int | str):
        msg = "Postgres returned a non-integer event sequence"
        raise TypeError(msg)
    sequence = int(raw_sequence)
    session_event = SessionEvent(
        tenant=event.tenant,
        session_id=event.session_id,
        sequence=sequence,
        event_id=f"evt-{uuid.uuid4().hex}",
        event_type=event.event_type,
        actor=event.actor,
        payload=dict(event.payload),
        created_at=datetime.now(UTC),
    )
    con.execute(
        """
        INSERT INTO session_events (
            tenant_id, session_id, sequence, event_id, event_type,
            actor_json, payload_json, created_at, schema_version
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event.tenant.tenant_id,
            event.session_id,
            sequence,
            session_event.event_id,
            event.event_type,
            _json_dumps(_actor_to_json(event.actor)),
            _json_dumps(dict(event.payload)),
            _datetime_to_json(session_event.created_at),
            _SCHEMA_VERSION,
        ),
    )
    return session_event


class PostgresSessionEventStore:
    """Postgres-backed ordered event store for Render deployments."""

    def append(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        event_type: str,
        actor: VerifiedActor | None,
        payload: Mapping[str, object],
    ) -> SessionEvent:
        """Append one event and return it with a sequence number."""
        with pg_transaction() as con:
            return _append_event_postgres(
                con,
                _EventAppend(
                    tenant=tenant,
                    session_id=session_id,
                    event_type=event_type,
                    actor=actor,
                    payload=payload,
                ),
            )

    def list_events(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> Sequence[SessionEvent]:
        """Return ordered events for one tenant-scoped session."""
        with pg_connect() as con:
            rows = con.execute(
                """
                SELECT * FROM session_events
                WHERE tenant_id = %s AND session_id = %s AND sequence > %s
                ORDER BY sequence ASC LIMIT %s
                """,
                (
                    tenant.tenant_id,
                    session_id,
                    0 if after_sequence is None else after_sequence,
                    limit,
                ),
            ).fetchall()
        events = [
            event
            for row in rows
            if (
                event := _safe_row(
                    FileSessionEventStore._event_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("SessionEvent", event) for event in events)


class PostgresActionStore:
    """Postgres-backed action store with transactional idempotency and events."""

    def __init__(self, *, event_store: SessionEventStore | None = None) -> None:
        """Create an action store using the configured ``DATABASE_URL``."""
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Action],
    ) -> Action:
        """Create an action once for a logical tenant-scoped request."""
        external_event: tuple[Action, str, Mapping[str, object]] | None = None
        with pg_transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            action = create()
            FileActionStore._validate_new_action(  # noqa: SLF001
                tenant=tenant,
                idempotency_key=idempotency_key,
                action=action,
            )
            self._insert_action(con, action)
            event_payload: Mapping[str, object] = {
                "status": action.status.value,
                "kind": action.kind.value,
            }
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_action_event_with_connection(
                    con,
                    action=action,
                    event_type="action_created",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (action, "action_created", event_payload)
        if external_event is not None:
            event_action, event_type, event_payload = external_event
            self._append_action_event(
                action=event_action,
                event_type=event_type,
                event_payload=event_payload,
            )
        return action

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
        transition: ActionTransition,
    ) -> Action | None:
        """Move an action only if it is still in the expected state."""
        validate_action_transition(
            expected=transition.expected,
            next_status=transition.next_status,
        )
        external_event: tuple[Action, str, Mapping[str, object]] | None = None
        with pg_transaction() as con:
            action = self._read_action_with_connection(
                con,
                tenant=tenant,
                action_id=action_id,
            )
            if action is None or action.status is not transition.expected:
                return None
            updated = replace(
                action,
                status=transition.next_status,
                result=(
                    transition.result
                    if transition.result is not None
                    else action.result
                ),
                failure=(
                    transition.failure
                    if transition.failure is not None
                    else action.failure
                ),
                updated_at=datetime.now(UTC),
            )
            self._update_action(con, updated)
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_action_event_with_connection(
                    con,
                    action=updated,
                    event_type=transition.event_type,
                    event_payload=transition.event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    updated,
                    transition.event_type,
                    transition.event_payload,
                )
        if external_event is not None:
            event_action, event_type, event_payload = external_event
            self._append_action_event(
                action=event_action,
                event_type=event_type,
                event_payload=event_payload,
            )
        return updated

    def get(self, *, tenant: TenantIdentity, action_id: str) -> Action | None:
        """Return one tenant-scoped action if it exists."""
        with pg_connect() as con:
            return self._read_action_with_connection(
                con,
                tenant=tenant,
                action_id=action_id,
            )

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Action]:
        """Return all known actions for one tenant-scoped session."""
        with pg_connect() as con:
            rows = con.execute(
                """
                SELECT * FROM actions
                WHERE tenant_id = %s AND session_id = %s
                ORDER BY created_at ASC
                """,
                (tenant.tenant_id, session_id),
            ).fetchall()
        actions = [
            action
            for row in rows
            if (
                action := _safe_row(
                    FileActionStore._action_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("Action", action) for action in actions)

    def find_latest_awaiting_confirmation(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        kind: ActionKind,
    ) -> Action | None:
        """Return the newest action waiting for confirmation in one session."""
        with pg_connect() as con:
            row = con.execute(
                """
                SELECT * FROM actions
                WHERE tenant_id = %s
                  AND session_id = %s
                  AND kind = %s
                  AND status = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    tenant.tenant_id,
                    session_id,
                    kind.value,
                    ActionStatus.AWAITING_CONFIRMATION.value,
                ),
            ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileActionStore._action_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Action | None", parsed)

    @staticmethod
    def _insert_action(
        con: PostgresConnection[dict[str, object]],
        action: Action,
    ) -> None:
        con.execute(
            """
            INSERT INTO actions (
                action_id, tenant_id, tenant_json, session_id, actor_json,
                kind, target_json, status, idempotency_key, input_json,
                result_json, failure_json, created_at, updated_at, expires_at,
                schema_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            _action_sql_values(action),
        )

    @staticmethod
    def _update_action(
        con: PostgresConnection[dict[str, object]],
        action: Action,
    ) -> None:
        con.execute(
            """
            UPDATE actions
            SET status = %s,
                result_json = %s,
                failure_json = %s,
                updated_at = %s,
                expires_at = %s,
                schema_version = %s
            WHERE tenant_id = %s AND action_id = %s
            """,
            (
                action.status.value,
                _json_dumps(_action_result_to_json(action.result)),
                _json_dumps(_action_failure_to_json(action.failure)),
                _datetime_to_json(action.updated_at),
                (
                    None
                    if action.expires_at is None
                    else _datetime_to_json(action.expires_at)
                ),
                _SCHEMA_VERSION,
                action.tenant.tenant_id,
                action.action_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Action | None:
        row = con.execute(
            """
            SELECT * FROM actions
            WHERE tenant_id = %s AND idempotency_key = %s
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileActionStore._action_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Action | None", parsed)

    def _read_action_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        action_id: str,
    ) -> Action | None:
        row = con.execute(
            """
            SELECT * FROM actions
            WHERE tenant_id = %s AND action_id = %s
            LIMIT 1
            """,
            (tenant.tenant_id, action_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileActionStore._action_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Action | None", parsed)

    def _append_action_event_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        action: Action,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        payload = {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            **dict(event_payload),
        }
        _append_event_postgres(
            con,
            _EventAppend(
                tenant=action.tenant,
                session_id=action.session_id,
                event_type=event_type,
                actor=action.actor,
                payload=payload,
            ),
        )

    def _append_action_event(
        self,
        *,
        action: Action,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            **dict(event_payload),
        }
        self._event_store.append(
            tenant=action.tenant,
            session_id=action.session_id,
            event_type=event_type,
            actor=action.actor,
            payload=payload,
        )


class PostgresArtifactStore:
    """Postgres-backed immutable artifact store for Render deployments."""

    def __init__(self, *, event_store: SessionEventStore | None = None) -> None:
        """Create an artifact store using the configured ``DATABASE_URL``."""
        self._event_store = event_store

    def create(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None = None,
    ) -> Artifact:
        """Persist one immutable artifact."""
        external_event: tuple[Artifact, VerifiedActor | None] | None = None
        with pg_transaction() as con:
            existing = self._read_artifact_with_connection(
                con,
                tenant=artifact.tenant,
                artifact_id=artifact.artifact_id,
            )
            if existing is not None:
                return existing
            self._insert_artifact(con, artifact)
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_artifact_event_with_connection(
                    con,
                    artifact=artifact,
                    actor=actor,
                )
            elif self._event_store is not None:
                external_event = (artifact, actor)
        if external_event is not None:
            artifact, actor = external_event
            self._append_artifact_event(artifact=artifact, actor=actor)
        return artifact

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Artifact]:
        """Return artifacts for one tenant-scoped session."""
        with pg_connect() as con:
            rows = con.execute(
                """
                SELECT * FROM artifacts
                WHERE tenant_id = %s AND session_id = %s
                ORDER BY created_at ASC
                """,
                (tenant.tenant_id, session_id),
            ).fetchall()
        artifacts = [
            artifact
            for row in rows
            if (
                artifact := _safe_row(
                    FileArtifactStore._artifact_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("Artifact", artifact) for artifact in artifacts)

    @staticmethod
    def _insert_artifact(
        con: PostgresConnection[dict[str, object]],
        artifact: Artifact,
    ) -> None:
        con.execute(
            """
            INSERT INTO artifacts (
                artifact_id, tenant_id, tenant_json, session_id, action_id,
                kind, uri, payload_json, created_at, schema_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                artifact.artifact_id,
                artifact.tenant.tenant_id,
                _json_dumps(_tenant_to_json(artifact.tenant)),
                artifact.session_id,
                artifact.action_id,
                artifact.kind,
                artifact.uri,
                _json_dumps(_artifact_payload_to_json(artifact.payload)),
                _datetime_to_json(artifact.created_at),
                _SCHEMA_VERSION,
            ),
        )

    def _read_artifact_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        artifact_id: str,
    ) -> Artifact | None:
        row = con.execute(
            """
            SELECT * FROM artifacts
            WHERE tenant_id = %s AND artifact_id = %s
            LIMIT 1
            """,
            (tenant.tenant_id, artifact_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileArtifactStore._artifact_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Artifact | None", parsed)

    def _append_artifact_event_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        _append_event_postgres(
            con,
            _EventAppend(
                tenant=artifact.tenant,
                session_id=artifact.session_id,
                event_type="artifact_created",
                actor=actor,
                payload={
                    "artifact_id": artifact.artifact_id,
                    "action_id": artifact.action_id,
                    "kind": artifact.kind,
                },
            ),
        )

    def _append_artifact_event(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        if self._event_store is None:
            return
        self._event_store.append(
            tenant=artifact.tenant,
            session_id=artifact.session_id,
            event_type="artifact_created",
            actor=actor,
            payload={
                "artifact_id": artifact.artifact_id,
                "action_id": artifact.action_id,
                "kind": artifact.kind,
            },
        )
