"""Unit tests for Postgres state helpers."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Self

import pytest

from ai_client_api import Conversation
from nimbus_runtime import postgres

pytestmark = pytest.mark.unit


@dataclass
class _Cursor:
    """Minimal DB cursor result used by Postgres helper tests."""

    row: dict[str, object] | None = None
    rows: list[dict[str, object]] = field(default_factory=list)
    rowcount: int | None = None

    def fetchone(self) -> dict[str, object] | None:
        """Return the configured single row."""
        return self.row

    def fetchall(self) -> list[dict[str, object]]:
        """Return the configured row list."""
        return list(self.rows)


@dataclass
class _Connection:
    """Tiny context-manager stand-in for a psycopg connection."""

    cursors: list[_Cursor]
    statements: list[tuple[str, tuple[object, ...] | None]] = field(
        default_factory=list
    )

    def __enter__(self) -> Self:
        """Enter the fake connection context."""
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Exit the fake connection context."""

    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _Cursor:
        """Record the query and return the next queued cursor."""
        self.statements.append((query, params))
        if not self.cursors:
            return _Cursor()
        return self.cursors.pop(0)


@contextmanager
def _fake_transaction(connection: _Connection) -> Iterator[_Connection]:
    """Yield a fake connection using the same shape as postgres.transaction."""
    yield connection


def test_postgres_enabled_reads_state_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the explicit postgres state backend should enable Postgres state."""
    monkeypatch.setenv("NIMBUS_STATE_BACKEND", "postgres")
    assert postgres.postgres_enabled() is True

    monkeypatch.setenv("NIMBUS_STATE_BACKEND", "file")
    assert postgres.postgres_enabled() is False


def test_database_url_from_env_requires_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres mode should fail clearly without a database URL."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(postgres.PostgresStateError, match="DATABASE_URL"):
        postgres.database_url_from_env()

    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    assert postgres.database_url_from_env() == "postgresql://example/db"


def test_check_ready_accepts_current_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness succeeds when the runtime schema row is current."""
    connection = _Connection(cursors=[_Cursor(row={"version": 1})])
    monkeypatch.setattr(postgres, "connect", lambda _url=None: connection)

    postgres.check_ready()

    assert "nimbus_schema_metadata" in connection.statements[0][0]


def test_check_ready_wraps_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness should translate database failures into domain state errors."""

    def _raise_connect(_url: str | None = None) -> _Connection:
        msg = "network down"
        raise RuntimeError(msg)

    monkeypatch.setattr(postgres, "connect", _raise_connect)

    with pytest.raises(postgres.PostgresStateError, match="not reachable"):
        postgres.check_ready()


def test_check_ready_rejects_missing_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing migration marker should fail readiness."""
    connection = _Connection(cursors=[_Cursor(row=None)])
    monkeypatch.setattr(postgres, "connect", lambda _url=None: connection)

    with pytest.raises(postgres.PostgresStateError, match="schema"):
        postgres.check_ready()


def test_load_session_returns_fresh_conversation_for_missing_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing persisted sessions should become fresh conversations."""
    connection = _Connection(cursors=[_Cursor(row=None)])
    monkeypatch.setattr(postgres, "connect", lambda _url=None: connection)

    conv = postgres.load_session("session-123", "system prompt")

    assert conv.session_id == "session-123"
    assert conv.system == "system prompt"


def test_load_session_decodes_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid persisted conversation JSON should reconstruct the conversation."""
    persisted = Conversation(system="old", session_id="session-123")
    persisted.add_user("hello")
    connection = _Connection(
        cursors=[
            _Cursor(
                row={
                    "schema_version": 1,
                    "payload_json": persisted.to_json(),
                }
            )
        ]
    )
    monkeypatch.setattr(postgres, "connect", lambda _url=None: connection)

    conv = postgres.load_session("session-123", "fallback system")

    assert conv.session_id == "session-123"
    assert conv.system == "old"
    assert [message.content for message in conv.messages()] == ["old", "hello"]


def test_load_session_rejects_unsafe_session_id() -> None:
    """Unsafe session IDs should fail before any database call."""
    with pytest.raises(ValueError, match="unsafe characters"):
        postgres.load_session("../escape")


def test_save_session_writes_json_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saving a session should upsert the serialized conversation."""
    connection = _Connection(cursors=[])
    monkeypatch.setattr(
        postgres,
        "transaction",
        lambda _url=None: _fake_transaction(connection),
    )
    conv = Conversation(system="sys", session_id="session-123")

    postgres.save_session("session-123", conv)

    query, params = connection.statements[0]
    assert "INSERT INTO nimbus_sessions" in query
    assert params is not None
    assert params[0] == "session-123"
    assert params[2] == 1


def test_session_exists_delete_and_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Basic session helpers should preserve their public return shapes."""
    exists_connection = _Connection(cursors=[_Cursor(row={"?column?": 1})])
    monkeypatch.setattr(postgres, "connect", lambda _url=None: exists_connection)
    assert postgres.session_exists("session-123") is True

    delete_connection = _Connection(cursors=[_Cursor(rowcount=1)])
    monkeypatch.setattr(
        postgres,
        "transaction",
        lambda _url=None: _fake_transaction(delete_connection),
    )
    assert postgres.delete_session("session-123") is True

    list_connection = _Connection(
        cursors=[_Cursor(rows=[{"session_id": "a"}, {"session_id": "b"}])]
    )
    monkeypatch.setattr(postgres, "connect", lambda _url=None: list_connection)
    assert postgres.list_sessions() == ("a", "b")


def test_request_state_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Request-state helpers should map rows and row counts predictably."""
    monkeypatch.setattr(postgres.time, "time", lambda: 100.0)

    get_connection = _Connection(
        cursors=[
            _Cursor(rowcount=2),
            _Cursor(row={"value_json": {"status": "ok"}}),
        ]
    )
    monkeypatch.setattr(
        postgres,
        "transaction",
        lambda _url=None: _fake_transaction(get_connection),
    )
    assert postgres.get_request_state("nonce", "key") == ({"status": "ok"}, 2)

    put_connection = _Connection(cursors=[_Cursor(rowcount=3), _Cursor()])
    monkeypatch.setattr(
        postgres,
        "transaction",
        lambda _url=None: _fake_transaction(put_connection),
    )
    assert (
        postgres.put_request_state(
            "nonce",
            "key",
            value={"status": "ok"},
            expires_at=200.0,
        )
        == 3
    )

    claim_connection = _Connection(cursors=[_Cursor(rowcount=0), _Cursor(rowcount=1)])
    monkeypatch.setattr(
        postgres,
        "transaction",
        lambda _url=None: _fake_transaction(claim_connection),
    )
    assert postgres.put_request_state_if_absent(
        "nonce",
        "key",
        value={"status": "ok"},
        expires_at=200.0,
    ) == (True, 0)

    delete_connection = _Connection(cursors=[_Cursor()])
    monkeypatch.setattr(
        postgres,
        "transaction",
        lambda _url=None: _fake_transaction(delete_connection),
    )
    postgres.delete_request_state("nonce", "key")
    assert "DELETE FROM nimbus_request_state" in delete_connection.statements[0][0]
