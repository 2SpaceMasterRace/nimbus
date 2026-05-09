"""Tests for the Nimbus Slack control-plane store."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

import pytest
from cryptography.fernet import Fernet
from nimbus_slack.crypto import SecretCodec
from nimbus_slack.store import (
    SLACK_SCHEMA_VERSION,
    PostgresSlackStore,
    SlackInstallation,
    SlackStore,
    SlackStoreError,
    TenantConfig,
)

from nimbus_slack import store as store_module

pytestmark = pytest.mark.unit


def _store(path: Path) -> SlackStore:
    """Create a test store with an isolated encryption key."""
    return SlackStore(
        db_path=path,
        codec=SecretCodec.from_key(Fernet.generate_key().decode("utf-8")),
    )


def _install(store: SlackStore) -> None:
    """Insert a deterministic Slack installation fixture."""
    store.upsert_installation(
        SlackInstallation(
            team_id="T123",
            enterprise_id=None,
            team_name="Nimbus Lab",
            bot_user_id="Ubot",
            bot_token="xoxb-real-token",  # noqa: S106
            scopes=("chat:write", "files:read"),
            installed_by="Uadmin",
            installed_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
    )


def _postgres_store(
    monkeypatch: pytest.MonkeyPatch,
    connection: _PgConnection,
    *,
    codec: SecretCodec | None = None,
) -> PostgresSlackStore:
    """Create a Postgres store backed by a fake connection."""

    def skip_schema(_self: PostgresSlackStore) -> None:
        return None

    def fake_transaction(
        database_url: str | None = None,
    ) -> AbstractContextManager[_PgConnection]:
        assert database_url == "postgresql://example/db"
        return _fake_pg_transaction(connection)

    monkeypatch.setattr(PostgresSlackStore, "_ensure_schema", skip_schema)
    monkeypatch.setattr(store_module, "postgres_transaction", fake_transaction)
    return PostgresSlackStore(
        database_url="postgresql://example/db",
        codec=codec or SecretCodec.from_key(Fernet.generate_key().decode("utf-8")),
    )


@dataclass
class _PgCursor:
    """Minimal Postgres cursor result used by store unit tests."""

    row: dict[str, object] | None = None
    rows: list[dict[str, object]] = field(default_factory=list)
    rowcount: int = 1

    def fetchone(self) -> dict[str, object] | None:
        """Return the configured single row."""
        return self.row

    def fetchall(self) -> list[dict[str, object]]:
        """Return configured rows."""
        return list(self.rows)


@dataclass
class _PgConnection:
    """Tiny stand-in for a psycopg connection."""

    cursors_by_fragment: dict[str, _PgCursor] = field(default_factory=dict)
    statements: list[tuple[str, tuple[object, ...] | None]] = field(
        default_factory=list
    )

    def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _PgCursor:
        """Record a query and return a matching fake cursor."""
        self.statements.append((query, params))
        for fragment, cursor in self.cursors_by_fragment.items():
            if fragment in query:
                return cursor
        return _PgCursor()


@contextmanager
def _fake_pg_transaction(connection: _PgConnection) -> Iterator[_PgConnection]:
    """Yield a fake Postgres transaction."""
    yield connection


def test_installation_tokens_are_encrypted_at_rest(tmp_path: Path) -> None:
    """Slack bot tokens should round-trip without plaintext in SQLite."""
    db_path = tmp_path / "slack.sqlite3"
    store = _store(db_path)

    _install(store)

    installation = store.get_installation("T123")
    assert installation is not None
    assert installation.bot_token == "xoxb-real-token"
    assert b"xoxb-real-token" not in db_path.read_bytes()


def test_complete_setup_persists_config_and_consumes_token_atomically(
    tmp_path: Path,
) -> None:
    """Setup completion should be one-time and leave decrypted config readable."""
    store = _store(tmp_path / "slack.sqlite3")
    _install(store)
    token = store.create_setup_session(
        team_id="T123",
        user_id="Uadmin",
        now=datetime(2026, 5, 9, tzinfo=UTC),
    )
    config = TenantConfig(
        team_id="T123",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="slack/archive",
        status="configured",
        updated_at=datetime(2026, 5, 9, tzinfo=UTC),
    )

    completed = store.complete_setup_session(token, config, now=config.updated_at)
    duplicate = store.complete_setup_session(token, config, now=config.updated_at)
    persisted = store.get_tenant_config("T123")

    assert completed is not None
    assert duplicate is None
    assert persisted == config


def test_complete_setup_round_trips_empty_s3_prefix(tmp_path: Path) -> None:
    """An optional empty S3 prefix should survive write-then-read."""
    store = _store(tmp_path / "slack.sqlite3")
    _install(store)
    token = store.create_setup_session(
        team_id="T123",
        user_id="Uadmin",
        now=datetime(2026, 5, 9, tzinfo=UTC),
    )
    config = TenantConfig(
        team_id="T123",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="",
        status="configured",
        updated_at=datetime(2026, 5, 9, tzinfo=UTC),
    )

    store.complete_setup_session(token, config, now=config.updated_at)

    assert store.get_tenant_config("T123") == config


def test_thread_follow_round_trips_and_expires(tmp_path: Path) -> None:
    """Slack thread-follow state should be durable and expiry-bound."""
    store = _store(tmp_path / "slack.sqlite3")
    now = datetime(2026, 5, 9, tzinfo=UTC)

    follow = store.activate_thread_follow(
        team_id="T123",
        channel_id="C123",
        thread_ts="1715000000.000100",
        user_id="U123",
        now=now,
        ttl_seconds=60,
    )

    assert follow.expires_at == now + timedelta(seconds=60)
    assert (
        store.is_thread_follow_active(
            team_id="T123",
            channel_id="C123",
            thread_ts="1715000000.000100",
            now=now + timedelta(seconds=59),
        )
        is True
    )
    assert (
        store.is_thread_follow_active(
            team_id="T123",
            channel_id="C123",
            thread_ts="1715000000.000100",
            now=now + timedelta(seconds=61),
        )
        is False
    )


def test_thread_follow_refresh_extends_expiry(tmp_path: Path) -> None:
    """Active thread replies should extend the follow window."""
    store = _store(tmp_path / "slack.sqlite3")
    now = datetime(2026, 5, 9, tzinfo=UTC)
    store.activate_thread_follow(
        team_id="T123",
        channel_id="C123",
        thread_ts="1715000000.000100",
        user_id="U123",
        now=now,
        ttl_seconds=60,
    )

    assert store.is_thread_follow_active(
        team_id="T123",
        channel_id="C123",
        thread_ts="1715000000.000100",
        now=now + timedelta(seconds=50),
        refresh_ttl_seconds=60,
    )
    assert store.is_thread_follow_active(
        team_id="T123",
        channel_id="C123",
        thread_ts="1715000000.000100",
        now=now + timedelta(seconds=105),
    )


def test_expired_setup_session_cannot_write_config(tmp_path: Path) -> None:
    """Expired setup tokens should fail closed."""
    store = _store(tmp_path / "slack.sqlite3")
    _install(store)
    issued_at = datetime(2026, 5, 9, tzinfo=UTC)
    token = store.create_setup_session(
        team_id="T123",
        user_id="Uadmin",
        now=issued_at,
        ttl_seconds=1,
    )
    config = TenantConfig(
        team_id="T123",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="",
        status="configured",
        updated_at=issued_at + timedelta(seconds=5),
    )

    completed = store.complete_setup_session(
        token,
        config,
        now=issued_at + timedelta(seconds=5),
    )

    assert completed is None
    assert store.get_tenant_config("T123") is None


def test_sqlite_store_absent_and_invalid_paths(tmp_path: Path) -> None:
    """SQLite store should fail closed for absent rows and invalid setup."""
    store = _store(tmp_path / "slack.sqlite3")
    config = TenantConfig(
        team_id="T123",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="",
        status="configured",
        updated_at=datetime(2026, 5, 9, tzinfo=UTC),
    )

    assert store.get_installation("missing") is None
    assert store.mark_uninstalled("missing") is False
    assert store.complete_setup_session("missing", config) is None
    assert store.get_setup_session("missing") is None
    assert store.consume_setup_session("missing") is None
    with pytest.raises(SlackStoreError, match="TTL"):
        store.create_setup_session(team_id="T123", user_id="Uadmin", ttl_seconds=0)


def test_sqlite_complete_setup_rejects_workspace_mismatch(tmp_path: Path) -> None:
    """Setup tokens should not configure a different Slack workspace."""
    store = _store(tmp_path / "slack.sqlite3")
    _install(store)
    issued_at = datetime(2026, 5, 9, tzinfo=UTC)
    token = store.create_setup_session(
        team_id="T123",
        user_id="Uadmin",
        now=issued_at,
    )
    config = TenantConfig(
        team_id="T999",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="",
        status="configured",
        updated_at=issued_at,
    )

    with pytest.raises(SlackStoreError, match="workspace"):
        store.complete_setup_session(token, config, now=issued_at)


def test_slack_store_backend_from_env_defaults_to_sqlite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack store backend should remain SQLite unless explicitly changed."""
    monkeypatch.delenv("NIMBUS_SLACK_STORE_BACKEND", raising=False)

    assert store_module.slack_store_backend_from_env() == "sqlite"


def test_slack_store_backend_from_env_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown store backends should fail before touching credentials."""
    monkeypatch.setenv("NIMBUS_SLACK_STORE_BACKEND", "redis")

    with pytest.raises(SlackStoreError, match="must be either"):
        store_module.slack_store_backend_from_env()


def test_slack_database_url_from_env_prefers_slack_specific_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Slack-specific database URL should override the runtime URL."""
    monkeypatch.setenv("NIMBUS_SLACK_DATABASE_URL", "postgresql://slack/db")
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime/db")

    assert store_module.slack_database_url_from_env() == "postgresql://slack/db"


def test_slack_database_url_from_env_falls_back_to_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render deployments can share DATABASE_URL with Nimbus runtime state."""
    monkeypatch.delenv("NIMBUS_SLACK_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://runtime/db")

    assert store_module.slack_database_url_from_env() == "postgresql://runtime/db"


def test_slack_database_url_from_env_requires_a_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres mode should fail clearly without a configured database URL."""
    monkeypatch.delenv("NIMBUS_SLACK_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(SlackStoreError, match="DATABASE_URL"):
        store_module.slack_database_url_from_env()


def test_connect_postgres_wraps_psycopg_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres transport failures should become Slack store errors."""

    def fail_connect(_database_url: str, **_kwargs: object) -> object:
        msg = "network down"
        raise store_module.psycopg.OperationalError(msg)

    monkeypatch.setattr(store_module.psycopg, "connect", fail_connect)

    with pytest.raises(SlackStoreError, match="not reachable"):
        store_module.connect_postgres("postgresql://example/db")


def test_postgres_transaction_uses_connection_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres transaction helper should enter the connection transaction."""

    @dataclass
    class _ConnectionContext:
        transaction_entered: bool = False

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

        @contextmanager
        def transaction(self) -> Iterator[None]:
            self.transaction_entered = True
            yield None

    connection = _ConnectionContext()

    def fake_connect(_database_url: str | None = None) -> _ConnectionContext:
        return connection

    monkeypatch.setattr(store_module, "connect_postgres", fake_connect)

    with store_module.postgres_transaction("postgresql://example/db") as active:
        assert active is connection

    assert connection.transaction_entered is True


def test_postgres_schema_migration_records_schema_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres store initialization should create and mark the Slack schema."""
    connection = _PgConnection()

    def fake_transaction(
        database_url: str | None = None,
    ) -> AbstractContextManager[_PgConnection]:
        assert database_url == "postgresql://example/db"
        return _fake_pg_transaction(connection)

    monkeypatch.setattr(store_module, "postgres_transaction", fake_transaction)

    PostgresSlackStore(
        database_url="postgresql://example/db",
        codec=SecretCodec.from_key(Fernet.generate_key().decode("utf-8")),
    )

    statements = [statement for statement, _params in connection.statements]
    assert any("nimbus_slack_schema_metadata" in statement for statement in statements)
    assert any(
        "CREATE TABLE IF NOT EXISTS slack_installations" in statement
        for statement in statements
    )
    assert connection.statements[-1][1] == (SLACK_SCHEMA_VERSION,)


def test_postgres_complete_setup_session_locks_row_before_consuming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup completion should serialize concurrent token consumers."""
    issued_at = datetime(2026, 5, 9, tzinfo=UTC)
    connection = _PgConnection(
        cursors_by_fragment={
            "FROM setup_sessions": _PgCursor(
                row={
                    "token_hash": hashlib.sha256(
                        b"setup-token",
                    ).hexdigest(),
                    "team_id": "T123",
                    "user_id": "Uadmin",
                    "created_at": issued_at.isoformat(),
                    "expires_at": (issued_at + timedelta(minutes=5)).isoformat(),
                    "consumed_at": None,
                }
            ),
            "UPDATE setup_sessions": _PgCursor(rowcount=1),
        }
    )

    store = _postgres_store(monkeypatch, connection)
    config = TenantConfig(
        team_id="T123",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="",
        status="configured",
        updated_at=issued_at,
    )

    completed = store.complete_setup_session("setup-token", config, now=issued_at)

    assert completed is not None
    assert completed.consumed_at == issued_at
    assert any(
        "FOR UPDATE" in statement for statement, _params in connection.statements
    )


def test_postgres_store_absent_and_invalid_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres store should return none for absent rows and reject bad TTLs."""
    connection = _PgConnection(
        cursors_by_fragment={
            "FROM slack_installations": _PgCursor(row=None),
            "FROM tenant_configs": _PgCursor(row=None),
            "FROM setup_sessions": _PgCursor(row=None),
        }
    )
    store = _postgres_store(monkeypatch, connection)
    config = TenantConfig(
        team_id="T123",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="",
        status="configured",
        updated_at=datetime(2026, 5, 9, tzinfo=UTC),
    )

    assert store.get_installation("missing") is None
    assert store.get_tenant_config("missing") is None
    assert store.complete_setup_session("missing", config) is None
    assert store.get_setup_session("missing") is None
    assert store.consume_setup_session("missing") is None
    with pytest.raises(SlackStoreError, match="TTL"):
        store.create_setup_session(team_id="T123", user_id="Uadmin", ttl_seconds=0)


def test_postgres_setup_rejects_workspace_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres setup completion should enforce token workspace ownership."""
    issued_at = datetime(2026, 5, 9, tzinfo=UTC)
    connection = _PgConnection(
        cursors_by_fragment={
            "FROM setup_sessions": _PgCursor(
                row={
                    "token_hash": hashlib.sha256(b"setup-token").hexdigest(),
                    "team_id": "T123",
                    "user_id": "Uadmin",
                    "created_at": issued_at.isoformat(),
                    "expires_at": (issued_at + timedelta(minutes=5)).isoformat(),
                    "consumed_at": None,
                }
            )
        }
    )
    store = _postgres_store(monkeypatch, connection)
    config = TenantConfig(
        team_id="T999",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="",
        status="configured",
        updated_at=issued_at,
    )

    with pytest.raises(SlackStoreError, match="workspace"):
        store.complete_setup_session("setup-token", config, now=issued_at)


def test_postgres_setup_update_race_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres setup helpers should fail closed if the update loses a race."""
    issued_at = datetime(2026, 5, 9, tzinfo=UTC)
    connection = _PgConnection(
        cursors_by_fragment={
            "FROM setup_sessions": _PgCursor(
                row={
                    "token_hash": hashlib.sha256(b"setup-token").hexdigest(),
                    "team_id": "T123",
                    "user_id": "Uadmin",
                    "created_at": issued_at.isoformat(),
                    "expires_at": (issued_at + timedelta(minutes=5)).isoformat(),
                    "consumed_at": None,
                }
            ),
            "UPDATE setup_sessions": _PgCursor(rowcount=0),
        }
    )
    store = _postgres_store(monkeypatch, connection)
    config = TenantConfig(
        team_id="T123",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="",
        status="configured",
        updated_at=issued_at,
    )

    assert store.complete_setup_session("setup-token", config, now=issued_at) is None
    assert store.consume_setup_session("setup-token", now=issued_at) is None


def test_postgres_transaction_translates_query_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres query failures should become Slack store errors."""
    connection = _PgConnection()
    store = _postgres_store(monkeypatch, connection)

    @contextmanager
    def failing_transaction(
        _database_url: str | None = None,
    ) -> Iterator[_PgConnection]:
        yield connection
        msg = "write failed"
        raise store_module.psycopg.OperationalError(msg)

    monkeypatch.setattr(store_module, "postgres_transaction", failing_transaction)

    with pytest.raises(SlackStoreError, match="operation failed"):
        store.check_ready()


def test_postgres_installation_and_tenant_rows_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres rows should map through the same encrypted store contract."""
    codec = SecretCodec.from_key(Fernet.generate_key().decode("utf-8"))
    installed_at = datetime(2026, 5, 9, tzinfo=UTC)
    updated_at = datetime(2026, 5, 10, tzinfo=UTC)
    connection = _PgConnection(
        cursors_by_fragment={
            "FROM slack_installations": _PgCursor(
                row={
                    "team_id": "T123",
                    "enterprise_id": "E123",
                    "team_name": "Nimbus Lab",
                    "bot_user_id": "Ubot",
                    "bot_token_ciphertext": codec.encrypt(
                        "xoxb-real-token",
                        tenant_id="T123",
                        field_name="bot_token",
                        record_id="T123",
                        purpose="slack_installation",
                    ),
                    "scopes_json": '["chat:write","files:read"]',
                    "installed_by": "Uadmin",
                    "installed_at": installed_at.isoformat(),
                    "uninstalled_at": None,
                }
            ),
            "FROM tenant_configs": _PgCursor(
                row={
                    "team_id": "T123",
                    "openrouter_api_key_ciphertext": codec.encrypt(
                        "sk-or-secret",
                        tenant_id="T123",
                        field_name="openrouter_api_key",
                        record_id="T123",
                        purpose="tenant_config",
                    ),
                    "aws_access_key_id_ciphertext": codec.encrypt(
                        "AKIA_TEST_SECRET",
                        tenant_id="T123",
                        field_name="aws_access_key_id",
                        record_id="T123",
                        purpose="tenant_config",
                    ),
                    "aws_secret_access_key_ciphertext": codec.encrypt(
                        "aws-secret",
                        tenant_id="T123",
                        field_name="aws_secret_access_key",
                        record_id="T123",
                        purpose="tenant_config",
                    ),
                    "aws_region": "us-east-1",
                    "s3_bucket": "nimbus-test-bucket",
                    "s3_prefix": "slack/archive",
                    "status": "configured",
                    "updated_at": updated_at.isoformat(),
                    "validated_at": None,
                }
            ),
            "UPDATE slack_installations": _PgCursor(rowcount=1),
        }
    )
    store = _postgres_store(monkeypatch, connection, codec=codec)
    installation = SlackInstallation(
        team_id="T123",
        enterprise_id="E123",
        team_name="Nimbus Lab",
        bot_user_id="Ubot",
        bot_token="xoxb-real-token",  # noqa: S106
        scopes=("chat:write", "files:read"),
        installed_by="Uadmin",
        installed_at=installed_at,
    )
    config = TenantConfig(
        team_id="T123",
        openrouter_api_key="sk-or-secret",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="aws-secret",  # noqa: S106
        aws_region="us-east-1",
        s3_bucket="nimbus-test-bucket",
        s3_prefix="slack/archive",
        status="configured",
        updated_at=updated_at,
    )

    store.upsert_installation(installation)
    store.upsert_tenant_config(config)

    assert store.get_installation("T123") == installation
    assert store.mark_uninstalled("T123", now=updated_at) is True
    assert store.get_tenant_config("T123") == config
    statements = [statement for statement, _params in connection.statements]
    assert any(
        "INSERT INTO slack_installations" in statement for statement in statements
    )
    assert any("INSERT INTO tenant_configs" in statement for statement in statements)


def test_postgres_setup_session_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres setup session reads and consumption should preserve TTL rules."""
    issued_at = datetime(2026, 5, 9, tzinfo=UTC)
    connection = _PgConnection(
        cursors_by_fragment={
            "FROM setup_sessions": _PgCursor(
                row={
                    "token_hash": hashlib.sha256(b"setup-token").hexdigest(),
                    "team_id": "T123",
                    "user_id": "Uadmin",
                    "created_at": issued_at.isoformat(),
                    "expires_at": (issued_at + timedelta(minutes=5)).isoformat(),
                    "consumed_at": None,
                }
            ),
            "UPDATE setup_sessions": _PgCursor(rowcount=1),
        }
    )
    store = _postgres_store(monkeypatch, connection)

    token = store.create_setup_session(
        team_id="T123",
        user_id="Uadmin",
        now=issued_at,
    )
    session = store.get_setup_session("setup-token", now=issued_at)
    consumed = store.consume_setup_session("setup-token", now=issued_at)

    assert token
    assert session is not None
    assert session.team_id == "T123"
    assert consumed is not None
    assert consumed.consumed_at == issued_at
    statements = [statement for statement, _params in connection.statements]
    assert any("INSERT INTO setup_sessions" in statement for statement in statements)
    assert any("FOR UPDATE" in statement for statement in statements)


def test_postgres_file_manifest_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres file inventory and manifest helpers should share row contracts."""
    indexed_at = datetime(2026, 5, 9, tzinfo=UTC)
    connection = _PgConnection(
        cursors_by_fragment={
            "FROM s3_file_manifest": _PgCursor(
                rows=[{"file_id": "F123"}, {"file_id": "F456"}]
            )
        }
    )
    store = _postgres_store(monkeypatch, connection)

    store.record_slack_files(
        [
            store_module.SlackFileRecord(
                team_id="T123",
                channel_id="C123",
                file_id="F123",
                name="report.pdf",
                title="Report",
                mimetype="application/pdf",
                size_bytes=12,
                url_private_download="https://slack.example/file",
                user_id="U123",
                created_ts=1_714_000_000,
                indexed_at=indexed_at,
            )
        ]
    )
    saved_ids = store.saved_file_ids(team_id="T123", channel_id="C123")
    store.record_saved_file(
        store_module.SavedSlackFileRecord(
            team_id="T123",
            channel_id="C123",
            file_id="F123",
            content_sha256="abc123",
            s3_bucket="nimbus-test-bucket",
            s3_key="slack/archive/report.pdf",
            size_bytes=12,
            saved_at=indexed_at,
        )
    )

    assert saved_ids == {"F123", "F456"}
    statements = [statement for statement, _params in connection.statements]
    assert any("INSERT INTO slack_files" in statement for statement in statements)
    assert any("INSERT INTO s3_file_manifest" in statement for statement in statements)


def test_sqlite_drift_alert_claim_is_idempotent(tmp_path: Path) -> None:
    """The scheduled verifier should post only the first alert for one issue."""
    store = _store(tmp_path / "slack.sqlite3")
    now = datetime(2026, 5, 21, tzinfo=UTC)

    first = store.claim_drift_alert(
        team_id="T123",
        channel_id="C123",
        issue_key="issue-1",
        status="missing",
        s3_bucket="bucket",
        s3_key="slack/a.txt",
        now=now,
    )
    second = store.claim_drift_alert(
        team_id="T123",
        channel_id="C123",
        issue_key="issue-1",
        status="missing",
        s3_bucket="bucket",
        s3_key="slack/a.txt",
        now=now + timedelta(minutes=5),
    )

    assert first is True
    assert second is False


def test_postgres_check_ready_accepts_current_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness should pass when the Slack schema marker is current."""
    connection = _PgConnection(
        cursors_by_fragment={
            "FROM nimbus_slack_schema_metadata": _PgCursor(
                row={"version": SLACK_SCHEMA_VERSION}
            )
        }
    )
    store = _postgres_store(monkeypatch, connection)

    store.check_ready()

    assert connection.statements[-1][1] is None


def test_postgres_check_ready_rejects_missing_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness should fail closed without the Slack schema marker."""
    connection = _PgConnection(
        cursors_by_fragment={"FROM nimbus_slack_schema_metadata": _PgCursor(row=None)}
    )
    store = _postgres_store(monkeypatch, connection)

    with pytest.raises(SlackStoreError, match="schema"):
        store.check_ready()
