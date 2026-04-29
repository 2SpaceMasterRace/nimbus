"""Postgres state primitives for Render-backed Nimbus deployments."""

from __future__ import annotations

import hashlib
import os
import re
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ai_client_api import Conversation

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

_SCHEMA_VERSION = 1
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:]+$")
_POSTGRES_BACKEND = "postgres"
_DEFAULT_SYSTEM_PROMPT = "You are Nimbus, a careful cloud-storage assistant."


class PostgresStateError(RuntimeError):
    """Raised when Postgres state is requested but unavailable or unhealthy."""


def postgres_enabled() -> bool:
    """Return whether Nimbus should use Postgres for runtime state."""
    return (
        os.environ.get("NIMBUS_STATE_BACKEND", "").strip().lower() == _POSTGRES_BACKEND
    )


def database_url_from_env() -> str:
    """Return the configured Postgres connection string.

    Raises:
        PostgresStateError: ``DATABASE_URL`` is missing.

    """
    raw = os.environ.get("DATABASE_URL", "").strip()
    if not raw:
        msg = "DATABASE_URL is required when NIMBUS_STATE_BACKEND=postgres"
        raise PostgresStateError(msg)
    return raw


def _key_hash(key: str) -> str:
    return f"sha256-{hashlib.sha256(key.encode('utf-8')).hexdigest()}"


def _validate_session_id(session_id: str) -> None:
    if not session_id or not _SAFE_SESSION_ID_RE.match(session_id):
        msg = (
            f"session_id {session_id!r} contains unsafe characters. Only "
            "alphanumerics, hyphens, underscores, dots, and colons are allowed."
        )
        raise ValueError(msg)


def connect(database_url: str | None = None) -> Connection[dict[str, object]]:
    """Open a Postgres connection with dictionary rows."""
    return psycopg.connect(
        database_url or database_url_from_env(),
        row_factory=dict_row,
    )


@contextmanager
def transaction(
    database_url: str | None = None,
) -> Iterator[Connection[dict[str, object]]]:
    """Yield a Postgres connection inside one transaction."""
    with connect(database_url) as con, con.transaction():
        yield con


def migrate(database_url: str | None = None) -> None:
    """Create or update the Render/Postgres runtime schema."""
    with transaction(database_url) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS nimbus_schema_metadata (
                name TEXT PRIMARY KEY,
                version INTEGER NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS nimbus_sessions (
                session_id TEXT PRIMARY KEY,
                payload_json JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                schema_version INTEGER NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS nimbus_request_state (
                namespace TEXT NOT NULL,
                key_hash TEXT NOT NULL,
                state_key TEXT NOT NULL,
                value_json JSONB NOT NULL,
                expires_at_epoch DOUBLE PRECISION NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                schema_version INTEGER NOT NULL,
                PRIMARY KEY (namespace, key_hash)
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS nimbus_request_state_expiry
                ON nimbus_request_state (namespace, expires_at_epoch)
            """
        )
        con.execute(
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
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS session_events_by_session
                ON session_events (tenant_id, session_id, sequence)
            """
        )
        con.execute(
            """
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
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS actions_by_session
                ON actions (tenant_id, session_id, created_at)
            """
        )
        con.execute(
            """
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
            )
            """
        )
        con.execute(
            """
            CREATE INDEX IF NOT EXISTS artifacts_by_session
                ON artifacts (tenant_id, session_id, created_at)
            """
        )
        con.execute(
            """
            INSERT INTO nimbus_schema_metadata (name, version)
            VALUES ('runtime', %s)
            ON CONFLICT (name) DO UPDATE
            SET version = EXCLUDED.version,
                updated_at = NOW()
            """,
            (_SCHEMA_VERSION,),
        )


def check_ready(database_url: str | None = None) -> None:
    """Verify the Postgres state store is reachable and migrated."""
    try:
        with connect(database_url) as con:
            row = con.execute(
                """
                SELECT version
                FROM nimbus_schema_metadata
                WHERE name = 'runtime'
                """
            ).fetchone()
    except Exception as exc:
        msg = "Postgres runtime state is not reachable"
        raise PostgresStateError(msg) from exc
    if row is None or row.get("version") != _SCHEMA_VERSION:
        msg = "Postgres runtime state schema is missing or out of date"
        raise PostgresStateError(msg)


def load_session(
    session_id: str,
    system_prompt: str | None = None,
    *,
    database_url: str | None = None,
) -> Conversation:
    """Load a persisted conversation from Postgres or create a fresh one."""
    _validate_session_id(session_id)
    with connect(database_url) as con:
        row = con.execute(
            """
            SELECT payload_json, schema_version
            FROM nimbus_sessions
            WHERE session_id = %s
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        return Conversation(
            system=system_prompt or _DEFAULT_SYSTEM_PROMPT,
            session_id=session_id,
        )
    if row.get("schema_version") != _SCHEMA_VERSION:
        return Conversation(
            system=system_prompt or _DEFAULT_SYSTEM_PROMPT,
            session_id=session_id,
        )
    payload = row.get("payload_json")
    if not isinstance(payload, dict):
        return Conversation(
            system=system_prompt or _DEFAULT_SYSTEM_PROMPT,
            session_id=session_id,
        )
    try:
        return Conversation.from_json(payload)
    except (ValueError, TypeError, KeyError):
        return Conversation(
            system=system_prompt or _DEFAULT_SYSTEM_PROMPT,
            session_id=session_id,
        )


def save_session(
    session_id: str,
    conv: Conversation,
    *,
    database_url: str | None = None,
) -> None:
    """Persist a conversation snapshot in Postgres."""
    _validate_session_id(session_id)
    with transaction(database_url) as con:
        con.execute(
            """
            INSERT INTO nimbus_sessions (session_id, payload_json, schema_version)
            VALUES (%s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE
            SET payload_json = EXCLUDED.payload_json,
                updated_at = NOW(),
                schema_version = EXCLUDED.schema_version
            """,
            (session_id, Jsonb(conv.to_json()), _SCHEMA_VERSION),
        )


def session_exists(session_id: str, *, database_url: str | None = None) -> bool:
    """Return whether a conversation exists in Postgres."""
    _validate_session_id(session_id)
    with connect(database_url) as con:
        row = con.execute(
            "SELECT 1 FROM nimbus_sessions WHERE session_id = %s",
            (session_id,),
        ).fetchone()
    return row is not None


def delete_session(session_id: str, *, database_url: str | None = None) -> bool:
    """Delete a persisted conversation snapshot from Postgres."""
    _validate_session_id(session_id)
    with transaction(database_url) as con:
        cursor = con.execute(
            "DELETE FROM nimbus_sessions WHERE session_id = %s",
            (session_id,),
        )
    return int(cursor.rowcount or 0) > 0


def list_sessions(*, database_url: str | None = None) -> Sequence[str]:
    """Return all known Postgres session IDs."""
    with connect(database_url) as con:
        rows = con.execute(
            "SELECT session_id FROM nimbus_sessions ORDER BY session_id ASC"
        ).fetchall()
    return tuple(str(row["session_id"]) for row in rows)


def _cleanup_request_state(
    con: Connection[dict[str, object]],
    *,
    namespace: str,
    now: float,
) -> int:
    cursor = con.execute(
        """
        DELETE FROM nimbus_request_state
        WHERE namespace = %s AND expires_at_epoch <= %s
        """,
        (namespace, now),
    )
    return int(cursor.rowcount or 0)


def get_request_state(
    namespace: str,
    key: str,
    *,
    database_url: str | None = None,
) -> tuple[dict[str, object] | None, int]:
    """Return an unexpired request-state value and cleanup count."""
    now = time.time()
    with transaction(database_url) as con:
        cleaned = _cleanup_request_state(con, namespace=namespace, now=now)
        row = con.execute(
            """
            SELECT value_json
            FROM nimbus_request_state
            WHERE namespace = %s
              AND key_hash = %s
              AND expires_at_epoch > %s
            """,
            (namespace, _key_hash(key), now),
        ).fetchone()
    if row is None:
        return None, cleaned
    value = row.get("value_json")
    if not isinstance(value, dict):
        return None, cleaned
    return cast("dict[str, object]", value), cleaned


def put_request_state(
    namespace: str,
    key: str,
    *,
    value: Mapping[str, object],
    expires_at: float,
    database_url: str | None = None,
) -> int:
    """Store request state in Postgres and return cleanup count."""
    now = time.time()
    with transaction(database_url) as con:
        cleaned = _cleanup_request_state(con, namespace=namespace, now=now)
        con.execute(
            """
            INSERT INTO nimbus_request_state (
                namespace, key_hash, state_key, value_json,
                expires_at_epoch, schema_version
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (namespace, key_hash) DO UPDATE
            SET state_key = EXCLUDED.state_key,
                value_json = EXCLUDED.value_json,
                expires_at_epoch = EXCLUDED.expires_at_epoch,
                schema_version = EXCLUDED.schema_version
            """,
            (
                namespace,
                _key_hash(key),
                key,
                Jsonb(dict(value)),
                expires_at,
                _SCHEMA_VERSION,
            ),
        )
    return cleaned


def put_request_state_if_absent(
    namespace: str,
    key: str,
    *,
    value: Mapping[str, object],
    expires_at: float,
    database_url: str | None = None,
) -> tuple[bool, int]:
    """Store request state only when no live value exists."""
    now = time.time()
    with transaction(database_url) as con:
        cleaned = _cleanup_request_state(con, namespace=namespace, now=now)
        cursor = con.execute(
            """
            INSERT INTO nimbus_request_state (
                namespace, key_hash, state_key, value_json,
                expires_at_epoch, schema_version
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (namespace, key_hash) DO NOTHING
            """,
            (
                namespace,
                _key_hash(key),
                key,
                Jsonb(dict(value)),
                expires_at,
                _SCHEMA_VERSION,
            ),
        )
    return int(cursor.rowcount or 0) > 0, cleaned


def delete_request_state(
    namespace: str,
    key: str,
    *,
    database_url: str | None = None,
) -> None:
    """Delete one request-state entry from Postgres."""
    with transaction(database_url) as con:
        con.execute(
            """
            DELETE FROM nimbus_request_state
            WHERE namespace = %s AND key_hash = %s
            """,
            (namespace, _key_hash(key)),
        )
