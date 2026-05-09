"""Durable Slack control-plane store for installations and tenant setup."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from nimbus_slack.crypto import SecretCodec

NIMBUS_SLACK_STATE_DIR = "NIMBUS_SLACK_STATE_DIR"
NIMBUS_SLACK_STORE_BACKEND = "NIMBUS_SLACK_STORE_BACKEND"
NIMBUS_SLACK_DATABASE_URL = "NIMBUS_SLACK_DATABASE_URL"
DEFAULT_DATABASE_NAME = "nimbus_slack.sqlite3"
DEFAULT_SETUP_TTL_SECONDS = 15 * 60
POSTGRES_BACKEND = "postgres"
SQLITE_BACKEND = "sqlite"
SLACK_SCHEMA_VERSION = 3


class SlackStoreError(RuntimeError):
    """Raised when the Slack control-plane store cannot satisfy a contract."""


@dataclass(frozen=True, slots=True)
class SlackInstallation:
    """One Slack workspace installation."""

    team_id: str
    enterprise_id: str | None
    team_name: str | None
    bot_user_id: str | None
    bot_token: str
    scopes: tuple[str, ...]
    installed_by: str | None
    installed_at: datetime
    uninstalled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class TenantConfig:
    """BYOK configuration for one Slack workspace."""

    team_id: str
    openrouter_api_key: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket: str
    s3_prefix: str
    status: str
    updated_at: datetime
    validated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SetupSession:
    """One short-lived setup link minted from a trusted Slack context."""

    token_hash: str
    team_id: str
    user_id: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SlackFileRecord:
    """Slack file metadata observed in a workspace channel."""

    team_id: str
    channel_id: str
    file_id: str
    name: str
    title: str | None
    mimetype: str | None
    size_bytes: int
    url_private_download: str | None
    user_id: str | None
    created_ts: int | None
    indexed_at: datetime


@dataclass(frozen=True, slots=True)
class SavedSlackFileRecord:
    """Manifest record proving a Slack file was saved to S3."""

    team_id: str
    channel_id: str
    file_id: str
    content_sha256: str
    s3_bucket: str
    s3_key: str
    size_bytes: int
    saved_at: datetime


@dataclass(frozen=True, slots=True)
class SlackThreadFollow:
    """One Slack thread where Nimbus may accept unmentioned replies."""

    team_id: str
    channel_id: str
    thread_ts: str
    started_by_user_id: str
    started_at: datetime
    updated_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class SlackDriftAlert:
    """One posted Slack alert for a saved-file drift issue."""

    team_id: str
    channel_id: str
    issue_key: str
    status: str
    s3_bucket: str
    s3_key: str
    first_seen_at: datetime
    last_seen_at: datetime


def default_store_path() -> Path:
    """Return the default local SQLite path for Slack control-plane state."""
    state_dir = os.environ.get(NIMBUS_SLACK_STATE_DIR, "").strip()
    base = (
        Path(state_dir).expanduser() if state_dir else Path.home() / ".nimbus" / "slack"
    )
    return base / DEFAULT_DATABASE_NAME


def slack_store_backend_from_env() -> str:
    """Return the configured Slack store backend."""
    raw_value = os.environ.get(NIMBUS_SLACK_STORE_BACKEND, SQLITE_BACKEND)
    backend = raw_value.strip().lower() or SQLITE_BACKEND
    if backend in {SQLITE_BACKEND, POSTGRES_BACKEND}:
        return backend
    msg = (
        f"{NIMBUS_SLACK_STORE_BACKEND} must be either "
        f"{SQLITE_BACKEND!r} or {POSTGRES_BACKEND!r}."
    )
    raise SlackStoreError(msg)


def slack_database_url_from_env() -> str:
    """Return the Postgres URL for the Slack store.

    ``NIMBUS_SLACK_DATABASE_URL`` lets the Slack control plane use a dedicated
    database. Falling back to ``DATABASE_URL`` keeps Render free deployments
    simple when Nimbus runtime and Slack state share the same Postgres database.
    """
    raw_value = os.environ.get(NIMBUS_SLACK_DATABASE_URL, "").strip()
    database_url = raw_value or os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        return database_url
    msg = (
        f"{NIMBUS_SLACK_DATABASE_URL} or DATABASE_URL is required when "
        f"{NIMBUS_SLACK_STORE_BACKEND}=postgres."
    )
    raise SlackStoreError(msg)


def connect_postgres(
    database_url: str | None = None,
) -> Connection[dict[str, object]]:
    """Open one Postgres connection with dictionary rows."""
    try:
        return psycopg.connect(
            database_url or slack_database_url_from_env(),
            row_factory=dict_row,
        )
    except psycopg.Error as exc:
        msg = "Postgres Slack store is not reachable."
        raise SlackStoreError(msg) from exc


@contextmanager
def postgres_transaction(
    database_url: str | None = None,
) -> Iterator[Connection[dict[str, object]]]:
    """Yield one Postgres connection inside a transaction."""
    with connect_postgres(database_url) as conn, conn.transaction():
        yield conn


class SlackStore:
    """SQLite-backed store for the first production-credible Slack topology.

    The naked deployment is one process with one durable SQLite database. The
    contract intentionally keeps all writes idempotent and schema-driven so this
    can graduate to Postgres when multiple writable app processes become a real
    requirement.
    """

    def __init__(self, db_path: Path, codec: SecretCodec) -> None:
        """Create a store and initialize its schema if needed."""
        self._db_path = db_path
        self._codec = codec
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def upsert_installation(self, installation: SlackInstallation) -> None:
        """Insert or replace a Slack workspace installation."""
        token_ciphertext = self._codec.encrypt(
            installation.bot_token,
            tenant_id=installation.team_id,
            field_name="bot_token",
            record_id=installation.team_id,
            purpose="slack_installation",
        )
        scopes_json = json.dumps(list(installation.scopes), separators=(",", ":"))
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO slack_installations (
                    team_id,
                    enterprise_id,
                    team_name,
                    bot_user_id,
                    bot_token_ciphertext,
                    scopes_json,
                    installed_by,
                    installed_at,
                    uninstalled_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team_id) DO UPDATE SET
                    enterprise_id = excluded.enterprise_id,
                    team_name = excluded.team_name,
                    bot_user_id = excluded.bot_user_id,
                    bot_token_ciphertext = excluded.bot_token_ciphertext,
                    scopes_json = excluded.scopes_json,
                    installed_by = excluded.installed_by,
                    installed_at = excluded.installed_at,
                    uninstalled_at = excluded.uninstalled_at
                """,
                (
                    installation.team_id,
                    installation.enterprise_id,
                    installation.team_name,
                    installation.bot_user_id,
                    token_ciphertext,
                    scopes_json,
                    installation.installed_by,
                    _to_iso(installation.installed_at),
                    _to_optional_iso(installation.uninstalled_at),
                ),
            )

    def get_installation(self, team_id: str) -> SlackInstallation | None:
        """Return the active installation for a Slack workspace."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT
                    team_id,
                    enterprise_id,
                    team_name,
                    bot_user_id,
                    bot_token_ciphertext,
                    scopes_json,
                    installed_by,
                    installed_at,
                    uninstalled_at
                FROM slack_installations
                WHERE team_id = ? AND uninstalled_at IS NULL
                """,
                (team_id,),
            ).fetchone()
        if row is None:
            return None
        return self._installation_from_row(row)

    def list_active_team_ids(self) -> list[str]:
        """Return team IDs of workspaces with active installations and BYOK config."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT i.team_id
                FROM slack_installations i
                INNER JOIN tenant_configs tc ON tc.team_id = i.team_id
                WHERE i.uninstalled_at IS NULL
                ORDER BY i.team_id
                """,
            ).fetchall()
        return [row[0] for row in rows]

    def mark_uninstalled(self, team_id: str, *, now: datetime | None = None) -> bool:
        """Mark an installation as uninstalled without deleting audit state."""
        removed_at = now or _utc_now()
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE slack_installations
                SET uninstalled_at = ?
                WHERE team_id = ? AND uninstalled_at IS NULL
                """,
                (_to_iso(removed_at), team_id),
            )
        return cursor.rowcount > 0

    def upsert_tenant_config(self, config: TenantConfig) -> None:
        """Insert or replace encrypted BYOK configuration for a workspace."""
        with self._connection() as conn:
            self._write_tenant_config(conn, config)

    def complete_setup_session(
        self,
        token: str,
        config: TenantConfig,
        *,
        now: datetime | None = None,
    ) -> SetupSession | None:
        """Persist BYOK config and consume the setup token atomically."""
        consumed_at = now or _utc_now()
        token_hash = _hash_token(token)
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT
                    token_hash,
                    team_id,
                    user_id,
                    created_at,
                    expires_at,
                    consumed_at
                FROM setup_sessions
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            session = self._setup_session_from_row(row)
            if session.consumed_at is not None or session.expires_at <= consumed_at:
                return None
            if session.team_id != config.team_id:
                msg = "Setup token workspace does not match tenant configuration."
                raise SlackStoreError(msg)
            self._write_tenant_config(conn, config)
            conn.execute(
                """
                UPDATE setup_sessions
                SET consumed_at = ?
                WHERE token_hash = ? AND consumed_at IS NULL
                """,
                (_to_iso(consumed_at), token_hash),
            )
        return SetupSession(
            token_hash=session.token_hash,
            team_id=session.team_id,
            user_id=session.user_id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            consumed_at=consumed_at,
        )

    def get_tenant_config(self, team_id: str) -> TenantConfig | None:
        """Return decrypted BYOK configuration for a workspace."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT
                    team_id,
                    openrouter_api_key_ciphertext,
                    aws_access_key_id_ciphertext,
                    aws_secret_access_key_ciphertext,
                    aws_region,
                    s3_bucket,
                    s3_prefix,
                    status,
                    updated_at,
                    validated_at
                FROM tenant_configs
                WHERE team_id = ?
                """,
                (team_id,),
            ).fetchone()
        if row is None:
            return None
        return self._tenant_config_from_row(row)

    def create_setup_session(
        self,
        *,
        team_id: str,
        user_id: str,
        now: datetime | None = None,
        ttl_seconds: int = DEFAULT_SETUP_TTL_SECONDS,
    ) -> str:
        """Create a one-time setup token and return its raw value."""
        if ttl_seconds <= 0:
            msg = "Setup-session TTL must be positive."
            raise SlackStoreError(msg)
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        created_at = now or _utc_now()
        expires_at = created_at + timedelta(seconds=ttl_seconds)
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO setup_sessions (
                    token_hash,
                    team_id,
                    user_id,
                    created_at,
                    expires_at,
                    consumed_at
                )
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (
                    token_hash,
                    team_id,
                    user_id,
                    _to_iso(created_at),
                    _to_iso(expires_at),
                ),
            )
        return token

    def get_setup_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> SetupSession | None:
        """Return a setup session only while it is unexpired and unused."""
        checked_at = now or _utc_now()
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT
                    token_hash,
                    team_id,
                    user_id,
                    created_at,
                    expires_at,
                    consumed_at
                FROM setup_sessions
                WHERE token_hash = ?
                """,
                (_hash_token(token),),
            ).fetchone()
        if row is None:
            return None
        session = self._setup_session_from_row(row)
        if session.consumed_at is not None or session.expires_at <= checked_at:
            return None
        return session

    def consume_setup_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> SetupSession | None:
        """Atomically consume a setup session if it is valid."""
        consumed_at = now or _utc_now()
        token_hash = _hash_token(token)
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT
                    token_hash,
                    team_id,
                    user_id,
                    created_at,
                    expires_at,
                    consumed_at
                FROM setup_sessions
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            session = self._setup_session_from_row(row)
            if session.consumed_at is not None or session.expires_at <= consumed_at:
                return None
            conn.execute(
                """
                UPDATE setup_sessions
                SET consumed_at = ?
                WHERE token_hash = ? AND consumed_at IS NULL
                """,
                (_to_iso(consumed_at), token_hash),
            )
        return SetupSession(
            token_hash=session.token_hash,
            team_id=session.team_id,
            user_id=session.user_id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            consumed_at=consumed_at,
        )

    def record_slack_files(self, files: Iterable[SlackFileRecord]) -> None:
        """Upsert Slack file inventory rows observed during a scan."""
        with self._connection() as conn:
            for file in files:
                conn.execute(
                    """
                    INSERT INTO slack_files (
                        team_id,
                        channel_id,
                        file_id,
                        name,
                        title,
                        mimetype,
                        size_bytes,
                        url_private_download,
                        user_id,
                        created_ts,
                        indexed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(team_id, channel_id, file_id) DO UPDATE SET
                        name = excluded.name,
                        title = excluded.title,
                        mimetype = excluded.mimetype,
                        size_bytes = excluded.size_bytes,
                        url_private_download = excluded.url_private_download,
                        user_id = excluded.user_id,
                        created_ts = excluded.created_ts,
                        indexed_at = excluded.indexed_at
                    """,
                    (
                        file.team_id,
                        file.channel_id,
                        file.file_id,
                        file.name,
                        file.title,
                        file.mimetype,
                        file.size_bytes,
                        file.url_private_download,
                        file.user_id,
                        file.created_ts,
                        _to_iso(file.indexed_at),
                    ),
                )

    def saved_file_ids(self, *, team_id: str, channel_id: str) -> set[str]:
        """Return file IDs already saved to S3 for a channel."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT file_id
                FROM s3_file_manifest
                WHERE team_id = ? AND channel_id = ?
                """,
                (team_id, channel_id),
            ).fetchall()
        return {_row_str(row, "file_id") for row in rows}

    def list_saved_files(
        self,
        *,
        team_id: str,
        channel_id: str,
    ) -> tuple[SavedSlackFileRecord, ...]:
        """Return manifest records for files already saved to S3."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    team_id, channel_id, file_id, content_sha256,
                    s3_bucket, s3_key, size_bytes, saved_at
                FROM s3_file_manifest
                WHERE team_id = ? AND channel_id = ?
                ORDER BY saved_at ASC
                """,
                (team_id, channel_id),
            ).fetchall()
        return tuple(_saved_file_from_row(row) for row in rows)

    def list_saved_files_for_team(
        self,
        *,
        team_id: str,
    ) -> tuple[SavedSlackFileRecord, ...]:
        """Return all saved Slack file manifest rows for one workspace."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    team_id, channel_id, file_id, content_sha256,
                    s3_bucket, s3_key, size_bytes, saved_at
                FROM s3_file_manifest
                WHERE team_id = ?
                ORDER BY saved_at ASC
                """,
                (team_id,),
            ).fetchall()
        return tuple(_saved_file_from_row(row) for row in rows)

    def record_saved_file(self, record: SavedSlackFileRecord) -> None:
        """Upsert manifest evidence for one saved Slack file."""
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO s3_file_manifest (
                    team_id,
                    channel_id,
                    file_id,
                    content_sha256,
                    s3_bucket,
                    s3_key,
                    size_bytes,
                    saved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team_id, channel_id, file_id) DO UPDATE SET
                    content_sha256 = excluded.content_sha256,
                    s3_bucket = excluded.s3_bucket,
                    s3_key = excluded.s3_key,
                    size_bytes = excluded.size_bytes,
                    saved_at = excluded.saved_at
                """,
                (
                    record.team_id,
                    record.channel_id,
                    record.file_id,
                    record.content_sha256,
                    record.s3_bucket,
                    record.s3_key,
                    record.size_bytes,
                    _to_iso(record.saved_at),
                ),
            )

    def activate_thread_follow(  # noqa: PLR0913 - thread key, actor, and TTL are the contract.
        self,
        *,
        team_id: str,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        now: datetime,
        ttl_seconds: int,
    ) -> SlackThreadFollow:
        """Allow unmentioned user replies in one Slack thread until expiry."""
        if ttl_seconds <= 0:
            msg = "thread follow ttl_seconds must be positive"
            raise ValueError(msg)
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM slack_thread_follows WHERE expires_at <= ?",
                (_to_iso(now),),
            )
            conn.execute(
                """
                INSERT INTO slack_thread_follows (
                    team_id,
                    channel_id,
                    thread_ts,
                    started_by_user_id,
                    started_at,
                    updated_at,
                    expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(team_id, channel_id, thread_ts) DO UPDATE SET
                    started_by_user_id = excluded.started_by_user_id,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    team_id,
                    channel_id,
                    thread_ts,
                    user_id,
                    _to_iso(now),
                    _to_iso(now),
                    _to_iso(expires_at),
                ),
            )
        return SlackThreadFollow(
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            started_by_user_id=user_id,
            started_at=now,
            updated_at=now,
            expires_at=expires_at,
        )

    def is_thread_follow_active(
        self,
        *,
        team_id: str,
        channel_id: str,
        thread_ts: str,
        now: datetime,
        refresh_ttl_seconds: int | None = None,
    ) -> bool:
        """Return whether unmentioned replies should route to Nimbus."""
        with self._connection() as conn:
            conn.execute(
                "DELETE FROM slack_thread_follows WHERE expires_at <= ?",
                (_to_iso(now),),
            )
            row = conn.execute(
                """
                SELECT expires_at
                FROM slack_thread_follows
                WHERE team_id = ?
                  AND channel_id = ?
                  AND thread_ts = ?
                  AND expires_at > ?
                """,
                (team_id, channel_id, thread_ts, _to_iso(now)),
            ).fetchone()
            if row is None:
                return False
            if refresh_ttl_seconds is not None and refresh_ttl_seconds > 0:
                conn.execute(
                    """
                    UPDATE slack_thread_follows
                    SET updated_at = ?, expires_at = ?
                    WHERE team_id = ? AND channel_id = ? AND thread_ts = ?
                    """,
                    (
                        _to_iso(now),
                        _to_iso(now + timedelta(seconds=refresh_ttl_seconds)),
                        team_id,
                        channel_id,
                        thread_ts,
                    ),
                )
            return True

    def claim_drift_alert(  # noqa: PLR0913 - durable idempotency key contract.
        self,
        *,
        team_id: str,
        channel_id: str,
        issue_key: str,
        status: str,
        s3_bucket: str,
        s3_key: str,
        now: datetime,
    ) -> bool:
        """Record one drift alert and return whether it should be posted.

        The primary key is the logical issue, not the scheduler attempt. This
        keeps a missing object from paging the same channel every interval.
        """
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO slack_drift_alerts (
                    team_id,
                    channel_id,
                    issue_key,
                    status,
                    s3_bucket,
                    s3_key,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team_id,
                    channel_id,
                    issue_key,
                    status,
                    s3_bucket,
                    s3_key,
                    _to_iso(now),
                    _to_iso(now),
                ),
            )
            inserted = cursor.rowcount > 0
            if not inserted:
                conn.execute(
                    """
                    UPDATE slack_drift_alerts
                    SET last_seen_at = ?
                    WHERE team_id = ? AND channel_id = ? AND issue_key = ?
                    """,
                    (_to_iso(now), team_id, channel_id, issue_key),
                )
            return inserted

    def _ensure_schema(self) -> None:
        """Create control-plane tables."""
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS slack_installations (
                    team_id TEXT PRIMARY KEY,
                    enterprise_id TEXT,
                    team_name TEXT,
                    bot_user_id TEXT,
                    bot_token_ciphertext TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    installed_by TEXT,
                    installed_at TEXT NOT NULL,
                    uninstalled_at TEXT
                );

                CREATE TABLE IF NOT EXISTS tenant_configs (
                    team_id TEXT PRIMARY KEY,
                    openrouter_api_key_ciphertext TEXT NOT NULL,
                    aws_access_key_id_ciphertext TEXT NOT NULL,
                    aws_secret_access_key_ciphertext TEXT NOT NULL,
                    aws_region TEXT NOT NULL,
                    s3_bucket TEXT NOT NULL,
                    s3_prefix TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    validated_at TEXT,
                    FOREIGN KEY(team_id)
                        REFERENCES slack_installations(team_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS setup_sessions (
                    token_hash TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(team_id)
                        REFERENCES slack_installations(team_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS setup_sessions_team_idx
                    ON setup_sessions(team_id, expires_at);

                CREATE TABLE IF NOT EXISTS slack_files (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    title TEXT,
                    mimetype TEXT,
                    size_bytes INTEGER NOT NULL,
                    url_private_download TEXT,
                    user_id TEXT,
                    created_ts INTEGER,
                    indexed_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, channel_id, file_id),
                    FOREIGN KEY(team_id)
                        REFERENCES slack_installations(team_id)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS s3_file_manifest (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    s3_bucket TEXT NOT NULL,
                    s3_key TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, channel_id, file_id),
                    FOREIGN KEY(team_id, channel_id, file_id)
                        REFERENCES slack_files(team_id, channel_id, file_id)
                        ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS s3_file_manifest_channel_idx
                    ON s3_file_manifest(team_id, channel_id, saved_at);

                CREATE TABLE IF NOT EXISTS slack_thread_follows (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    started_by_user_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, channel_id, thread_ts)
                );

                CREATE INDEX IF NOT EXISTS slack_thread_follows_expiry_idx
                    ON slack_thread_follows(expires_at);

                CREATE TABLE IF NOT EXISTS slack_drift_alerts (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    issue_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    s3_bucket TEXT NOT NULL,
                    s3_key TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, channel_id, issue_key)
                );

                CREATE INDEX IF NOT EXISTS slack_drift_alerts_seen_idx
                    ON slack_drift_alerts(team_id, last_seen_at);
                """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Open and close one SQLite connection."""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        """Open one SQLite connection with conservative settings."""
        conn = sqlite3.connect(self._db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _write_tenant_config(
        self,
        conn: sqlite3.Connection,
        config: TenantConfig,
    ) -> None:
        """Insert or replace encrypted tenant configuration on a connection."""
        conn.execute(
            """
            INSERT INTO tenant_configs (
                team_id,
                openrouter_api_key_ciphertext,
                aws_access_key_id_ciphertext,
                aws_secret_access_key_ciphertext,
                aws_region,
                s3_bucket,
                s3_prefix,
                status,
                updated_at,
                validated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id) DO UPDATE SET
                openrouter_api_key_ciphertext =
                    excluded.openrouter_api_key_ciphertext,
                aws_access_key_id_ciphertext =
                    excluded.aws_access_key_id_ciphertext,
                aws_secret_access_key_ciphertext =
                    excluded.aws_secret_access_key_ciphertext,
                aws_region = excluded.aws_region,
                s3_bucket = excluded.s3_bucket,
                s3_prefix = excluded.s3_prefix,
                status = excluded.status,
                updated_at = excluded.updated_at,
                validated_at = excluded.validated_at
            """,
            (
                config.team_id,
                self._codec.encrypt(
                    config.openrouter_api_key,
                    tenant_id=config.team_id,
                    field_name="openrouter_api_key",
                    record_id=config.team_id,
                    purpose="tenant_config",
                ),
                self._codec.encrypt(
                    config.aws_access_key_id,
                    tenant_id=config.team_id,
                    field_name="aws_access_key_id",
                    record_id=config.team_id,
                    purpose="tenant_config",
                ),
                self._codec.encrypt(
                    config.aws_secret_access_key,
                    tenant_id=config.team_id,
                    field_name="aws_secret_access_key",
                    record_id=config.team_id,
                    purpose="tenant_config",
                ),
                config.aws_region,
                config.s3_bucket,
                config.s3_prefix,
                config.status,
                _to_iso(config.updated_at),
                _to_optional_iso(config.validated_at),
            ),
        )

    def _installation_from_row(self, row: sqlite3.Row) -> SlackInstallation:
        """Map a SQLite row to a decrypted installation object."""
        team_id = _row_str(row, "team_id")
        return SlackInstallation(
            team_id=team_id,
            enterprise_id=_row_optional_str(row, "enterprise_id"),
            team_name=_row_optional_str(row, "team_name"),
            bot_user_id=_row_optional_str(row, "bot_user_id"),
            bot_token=self._codec.decrypt(
                _row_str(row, "bot_token_ciphertext"),
                tenant_id=team_id,
                field_name="bot_token",
                record_id=team_id,
                purpose="slack_installation",
            ),
            scopes=_json_tuple(_row_str(row, "scopes_json")),
            installed_by=_row_optional_str(row, "installed_by"),
            installed_at=_from_iso(_row_str(row, "installed_at")),
            uninstalled_at=_from_optional_iso(_row_optional_str(row, "uninstalled_at")),
        )

    def _tenant_config_from_row(self, row: sqlite3.Row) -> TenantConfig:
        """Map a SQLite row to a decrypted tenant configuration."""
        team_id = _row_str(row, "team_id")
        return TenantConfig(
            team_id=team_id,
            openrouter_api_key=self._codec.decrypt(
                _row_str(row, "openrouter_api_key_ciphertext"),
                tenant_id=team_id,
                field_name="openrouter_api_key",
                record_id=team_id,
                purpose="tenant_config",
            ),
            aws_access_key_id=self._codec.decrypt(
                _row_str(row, "aws_access_key_id_ciphertext"),
                tenant_id=team_id,
                field_name="aws_access_key_id",
                record_id=team_id,
                purpose="tenant_config",
            ),
            aws_secret_access_key=self._codec.decrypt(
                _row_str(row, "aws_secret_access_key_ciphertext"),
                tenant_id=team_id,
                field_name="aws_secret_access_key",
                record_id=team_id,
                purpose="tenant_config",
            ),
            aws_region=_row_str(row, "aws_region"),
            s3_bucket=_row_str(row, "s3_bucket"),
            s3_prefix=_row_optional_str(row, "s3_prefix") or "",
            status=_row_str(row, "status"),
            updated_at=_from_iso(_row_str(row, "updated_at")),
            validated_at=_from_optional_iso(_row_optional_str(row, "validated_at")),
        )

    @staticmethod
    def _setup_session_from_row(row: sqlite3.Row) -> SetupSession:
        """Map a SQLite row to a setup session object."""
        return SetupSession(
            token_hash=_row_str(row, "token_hash"),
            team_id=_row_str(row, "team_id"),
            user_id=_row_str(row, "user_id"),
            created_at=_from_iso(_row_str(row, "created_at")),
            expires_at=_from_iso(_row_str(row, "expires_at")),
            consumed_at=_from_optional_iso(_row_optional_str(row, "consumed_at")),
        )


class PostgresSlackStore:
    """Postgres-backed Slack control-plane store for Render deployments.

    This store preserves the SQLite public contract while moving the authority
    for Slack installations, BYOK credentials, setup sessions, and file
    manifests into a shared durable database. It is the smallest primitive that
    makes Render free web services viable because correctness no longer depends
    on a persistent local disk.
    """

    def __init__(self, database_url: str, codec: SecretCodec) -> None:
        """Create a store and initialize its schema if needed."""
        self._database_url = database_url
        self._codec = codec
        self._ensure_schema()

    def upsert_installation(self, installation: SlackInstallation) -> None:
        """Insert or replace a Slack workspace installation."""
        token_ciphertext = self._codec.encrypt(
            installation.bot_token,
            tenant_id=installation.team_id,
            field_name="bot_token",
            record_id=installation.team_id,
            purpose="slack_installation",
        )
        scopes_json = json.dumps(list(installation.scopes), separators=(",", ":"))
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO slack_installations (
                    team_id,
                    enterprise_id,
                    team_name,
                    bot_user_id,
                    bot_token_ciphertext,
                    scopes_json,
                    installed_by,
                    installed_at,
                    uninstalled_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(team_id) DO UPDATE SET
                    enterprise_id = excluded.enterprise_id,
                    team_name = excluded.team_name,
                    bot_user_id = excluded.bot_user_id,
                    bot_token_ciphertext = excluded.bot_token_ciphertext,
                    scopes_json = excluded.scopes_json,
                    installed_by = excluded.installed_by,
                    installed_at = excluded.installed_at,
                    uninstalled_at = excluded.uninstalled_at
                """,
                (
                    installation.team_id,
                    installation.enterprise_id,
                    installation.team_name,
                    installation.bot_user_id,
                    token_ciphertext,
                    scopes_json,
                    installation.installed_by,
                    _to_iso(installation.installed_at),
                    _to_optional_iso(installation.uninstalled_at),
                ),
            )

    def get_installation(self, team_id: str) -> SlackInstallation | None:
        """Return the active installation for a Slack workspace."""
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT
                    team_id,
                    enterprise_id,
                    team_name,
                    bot_user_id,
                    bot_token_ciphertext,
                    scopes_json,
                    installed_by,
                    installed_at,
                    uninstalled_at
                FROM slack_installations
                WHERE team_id = %s AND uninstalled_at IS NULL
                """,
                (team_id,),
            ).fetchone()
        if row is None:
            return None
        return self._installation_from_row(row)

    def list_active_team_ids(self) -> list[str]:
        """Return team IDs of workspaces with active installations and BYOK config."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT i.team_id
                FROM slack_installations i
                INNER JOIN tenant_configs tc ON tc.team_id = i.team_id
                WHERE i.uninstalled_at IS NULL
                ORDER BY i.team_id
                """,
            ).fetchall()
        return [str(row["team_id"]) for row in rows]

    def mark_uninstalled(self, team_id: str, *, now: datetime | None = None) -> bool:
        """Mark an installation as uninstalled without deleting audit state."""
        removed_at = now or _utc_now()
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE slack_installations
                SET uninstalled_at = %s
                WHERE team_id = %s AND uninstalled_at IS NULL
                """,
                (_to_iso(removed_at), team_id),
            )
        return cursor.rowcount > 0

    def upsert_tenant_config(self, config: TenantConfig) -> None:
        """Insert or replace encrypted BYOK configuration for a workspace."""
        with self._transaction() as conn:
            self._write_tenant_config(conn, config)

    def complete_setup_session(
        self,
        token: str,
        config: TenantConfig,
        *,
        now: datetime | None = None,
    ) -> SetupSession | None:
        """Persist BYOK config and consume the setup token atomically."""
        consumed_at = now or _utc_now()
        token_hash = _hash_token(token)
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT
                    token_hash,
                    team_id,
                    user_id,
                    created_at,
                    expires_at,
                    consumed_at
                FROM setup_sessions
                WHERE token_hash = %s
                FOR UPDATE
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            session = self._setup_session_from_row(row)
            if session.consumed_at is not None or session.expires_at <= consumed_at:
                return None
            if session.team_id != config.team_id:
                msg = "Setup token workspace does not match tenant configuration."
                raise SlackStoreError(msg)
            self._write_tenant_config(conn, config)
            cursor = conn.execute(
                """
                UPDATE setup_sessions
                SET consumed_at = %s
                WHERE token_hash = %s AND consumed_at IS NULL
                """,
                (_to_iso(consumed_at), token_hash),
            )
            if cursor.rowcount != 1:
                return None
        return SetupSession(
            token_hash=session.token_hash,
            team_id=session.team_id,
            user_id=session.user_id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            consumed_at=consumed_at,
        )

    def get_tenant_config(self, team_id: str) -> TenantConfig | None:
        """Return decrypted BYOK configuration for a workspace."""
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT
                    team_id,
                    openrouter_api_key_ciphertext,
                    aws_access_key_id_ciphertext,
                    aws_secret_access_key_ciphertext,
                    aws_region,
                    s3_bucket,
                    s3_prefix,
                    status,
                    updated_at,
                    validated_at
                FROM tenant_configs
                WHERE team_id = %s
                """,
                (team_id,),
            ).fetchone()
        if row is None:
            return None
        return self._tenant_config_from_row(row)

    def create_setup_session(
        self,
        *,
        team_id: str,
        user_id: str,
        now: datetime | None = None,
        ttl_seconds: int = DEFAULT_SETUP_TTL_SECONDS,
    ) -> str:
        """Create a one-time setup token and return its raw value."""
        if ttl_seconds <= 0:
            msg = "Setup-session TTL must be positive."
            raise SlackStoreError(msg)
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        created_at = now or _utc_now()
        expires_at = created_at + timedelta(seconds=ttl_seconds)
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO setup_sessions (
                    token_hash,
                    team_id,
                    user_id,
                    created_at,
                    expires_at,
                    consumed_at
                )
                VALUES (%s, %s, %s, %s, %s, NULL)
                """,
                (
                    token_hash,
                    team_id,
                    user_id,
                    _to_iso(created_at),
                    _to_iso(expires_at),
                ),
            )
        return token

    def get_setup_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> SetupSession | None:
        """Return a setup session only while it is unexpired and unused."""
        checked_at = now or _utc_now()
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT
                    token_hash,
                    team_id,
                    user_id,
                    created_at,
                    expires_at,
                    consumed_at
                FROM setup_sessions
                WHERE token_hash = %s
                """,
                (_hash_token(token),),
            ).fetchone()
        if row is None:
            return None
        session = self._setup_session_from_row(row)
        if session.consumed_at is not None or session.expires_at <= checked_at:
            return None
        return session

    def consume_setup_session(
        self,
        token: str,
        *,
        now: datetime | None = None,
    ) -> SetupSession | None:
        """Atomically consume a setup session if it is valid."""
        consumed_at = now or _utc_now()
        token_hash = _hash_token(token)
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT
                    token_hash,
                    team_id,
                    user_id,
                    created_at,
                    expires_at,
                    consumed_at
                FROM setup_sessions
                WHERE token_hash = %s
                FOR UPDATE
                """,
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            session = self._setup_session_from_row(row)
            if session.consumed_at is not None or session.expires_at <= consumed_at:
                return None
            cursor = conn.execute(
                """
                UPDATE setup_sessions
                SET consumed_at = %s
                WHERE token_hash = %s AND consumed_at IS NULL
                """,
                (_to_iso(consumed_at), token_hash),
            )
            if cursor.rowcount != 1:
                return None
        return SetupSession(
            token_hash=session.token_hash,
            team_id=session.team_id,
            user_id=session.user_id,
            created_at=session.created_at,
            expires_at=session.expires_at,
            consumed_at=consumed_at,
        )

    def record_slack_files(self, files: Iterable[SlackFileRecord]) -> None:
        """Upsert Slack file inventory rows observed during a scan."""
        with self._transaction() as conn:
            for file in files:
                conn.execute(
                    """
                    INSERT INTO slack_files (
                        team_id,
                        channel_id,
                        file_id,
                        name,
                        title,
                        mimetype,
                        size_bytes,
                        url_private_download,
                        user_id,
                        created_ts,
                        indexed_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(team_id, channel_id, file_id) DO UPDATE SET
                        name = excluded.name,
                        title = excluded.title,
                        mimetype = excluded.mimetype,
                        size_bytes = excluded.size_bytes,
                        url_private_download = excluded.url_private_download,
                        user_id = excluded.user_id,
                        created_ts = excluded.created_ts,
                        indexed_at = excluded.indexed_at
                    """,
                    (
                        file.team_id,
                        file.channel_id,
                        file.file_id,
                        file.name,
                        file.title,
                        file.mimetype,
                        file.size_bytes,
                        file.url_private_download,
                        file.user_id,
                        file.created_ts,
                        _to_iso(file.indexed_at),
                    ),
                )

    def saved_file_ids(self, *, team_id: str, channel_id: str) -> set[str]:
        """Return file IDs already saved to S3 for a channel."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT file_id
                FROM s3_file_manifest
                WHERE team_id = %s AND channel_id = %s
                """,
                (team_id, channel_id),
            ).fetchall()
        return {_row_str(row, "file_id") for row in rows}

    def list_saved_files(
        self,
        *,
        team_id: str,
        channel_id: str,
    ) -> tuple[SavedSlackFileRecord, ...]:
        """Return manifest records for files already saved to S3."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT
                    team_id, channel_id, file_id, content_sha256,
                    s3_bucket, s3_key, size_bytes, saved_at
                FROM s3_file_manifest
                WHERE team_id = %s AND channel_id = %s
                ORDER BY saved_at ASC
                """,
                (team_id, channel_id),
            ).fetchall()
        return tuple(_saved_file_from_row(row) for row in rows)

    def list_saved_files_for_team(
        self,
        *,
        team_id: str,
    ) -> tuple[SavedSlackFileRecord, ...]:
        """Return all saved Slack file manifest rows for one workspace."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT
                    team_id, channel_id, file_id, content_sha256,
                    s3_bucket, s3_key, size_bytes, saved_at
                FROM s3_file_manifest
                WHERE team_id = %s
                ORDER BY saved_at ASC
                """,
                (team_id,),
            ).fetchall()
        return tuple(_saved_file_from_row(row) for row in rows)

    def record_saved_file(self, record: SavedSlackFileRecord) -> None:
        """Upsert manifest evidence for one saved Slack file."""
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO s3_file_manifest (
                    team_id,
                    channel_id,
                    file_id,
                    content_sha256,
                    s3_bucket,
                    s3_key,
                    size_bytes,
                    saved_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(team_id, channel_id, file_id) DO UPDATE SET
                    content_sha256 = excluded.content_sha256,
                    s3_bucket = excluded.s3_bucket,
                    s3_key = excluded.s3_key,
                    size_bytes = excluded.size_bytes,
                    saved_at = excluded.saved_at
                """,
                (
                    record.team_id,
                    record.channel_id,
                    record.file_id,
                    record.content_sha256,
                    record.s3_bucket,
                    record.s3_key,
                    record.size_bytes,
                    _to_iso(record.saved_at),
                ),
            )

    def activate_thread_follow(  # noqa: PLR0913 - thread key, actor, and TTL are the contract.
        self,
        *,
        team_id: str,
        channel_id: str,
        thread_ts: str,
        user_id: str,
        now: datetime,
        ttl_seconds: int,
    ) -> SlackThreadFollow:
        """Allow unmentioned user replies in one Slack thread until expiry."""
        if ttl_seconds <= 0:
            msg = "thread follow ttl_seconds must be positive"
            raise ValueError(msg)
        expires_at = now + timedelta(seconds=ttl_seconds)
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM slack_thread_follows WHERE expires_at <= %s",
                (_to_iso(now),),
            )
            conn.execute(
                """
                INSERT INTO slack_thread_follows (
                    team_id,
                    channel_id,
                    thread_ts,
                    started_by_user_id,
                    started_at,
                    updated_at,
                    expires_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(team_id, channel_id, thread_ts) DO UPDATE SET
                    started_by_user_id = excluded.started_by_user_id,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    team_id,
                    channel_id,
                    thread_ts,
                    user_id,
                    _to_iso(now),
                    _to_iso(now),
                    _to_iso(expires_at),
                ),
            )
        return SlackThreadFollow(
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            started_by_user_id=user_id,
            started_at=now,
            updated_at=now,
            expires_at=expires_at,
        )

    def is_thread_follow_active(
        self,
        *,
        team_id: str,
        channel_id: str,
        thread_ts: str,
        now: datetime,
        refresh_ttl_seconds: int | None = None,
    ) -> bool:
        """Return whether unmentioned replies should route to Nimbus."""
        with self._transaction() as conn:
            conn.execute(
                "DELETE FROM slack_thread_follows WHERE expires_at <= %s",
                (_to_iso(now),),
            )
            row = conn.execute(
                """
                SELECT expires_at
                FROM slack_thread_follows
                WHERE team_id = %s
                  AND channel_id = %s
                  AND thread_ts = %s
                  AND expires_at > %s
                """,
                (team_id, channel_id, thread_ts, _to_iso(now)),
            ).fetchone()
            if row is None:
                return False
            if refresh_ttl_seconds is not None and refresh_ttl_seconds > 0:
                conn.execute(
                    """
                    UPDATE slack_thread_follows
                    SET updated_at = %s, expires_at = %s
                    WHERE team_id = %s AND channel_id = %s AND thread_ts = %s
                    """,
                    (
                        _to_iso(now),
                        _to_iso(now + timedelta(seconds=refresh_ttl_seconds)),
                        team_id,
                        channel_id,
                        thread_ts,
                    ),
                )
            return True

    def claim_drift_alert(  # noqa: PLR0913 - durable idempotency key contract.
        self,
        *,
        team_id: str,
        channel_id: str,
        issue_key: str,
        status: str,
        s3_bucket: str,
        s3_key: str,
        now: datetime,
    ) -> bool:
        """Record one drift alert and return whether it should be posted."""
        with self._transaction() as conn:
            cursor = conn.execute(
                """
                INSERT INTO slack_drift_alerts (
                    team_id,
                    channel_id,
                    issue_key,
                    status,
                    s3_bucket,
                    s3_key,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(team_id, channel_id, issue_key) DO NOTHING
                """,
                (
                    team_id,
                    channel_id,
                    issue_key,
                    status,
                    s3_bucket,
                    s3_key,
                    _to_iso(now),
                    _to_iso(now),
                ),
            )
            inserted = cursor.rowcount > 0
            if not inserted:
                conn.execute(
                    """
                    UPDATE slack_drift_alerts
                    SET last_seen_at = %s
                    WHERE team_id = %s AND channel_id = %s AND issue_key = %s
                    """,
                    (_to_iso(now), team_id, channel_id, issue_key),
                )
            return inserted

    def check_ready(self) -> None:
        """Verify the Postgres store is reachable and migrated."""
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT version
                FROM nimbus_slack_schema_metadata
                WHERE name = 'slack'
                """
            ).fetchone()
        if row is None or row.get("version") != SLACK_SCHEMA_VERSION:
            msg = "Postgres Slack store schema is missing or out of date."
            raise SlackStoreError(msg)

    def _ensure_schema(self) -> None:
        """Create Postgres control-plane tables."""
        with self._transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS nimbus_slack_schema_metadata (
                    name TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_installations (
                    team_id TEXT PRIMARY KEY,
                    enterprise_id TEXT,
                    team_name TEXT,
                    bot_user_id TEXT,
                    bot_token_ciphertext TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    installed_by TEXT,
                    installed_at TEXT NOT NULL,
                    uninstalled_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tenant_configs (
                    team_id TEXT PRIMARY KEY,
                    openrouter_api_key_ciphertext TEXT NOT NULL,
                    aws_access_key_id_ciphertext TEXT NOT NULL,
                    aws_secret_access_key_ciphertext TEXT NOT NULL,
                    aws_region TEXT NOT NULL,
                    s3_bucket TEXT NOT NULL,
                    s3_prefix TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    validated_at TEXT,
                    FOREIGN KEY(team_id)
                        REFERENCES slack_installations(team_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS setup_sessions (
                    token_hash TEXT PRIMARY KEY,
                    team_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    FOREIGN KEY(team_id)
                        REFERENCES slack_installations(team_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS setup_sessions_team_idx
                    ON setup_sessions(team_id, expires_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_files (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    title TEXT,
                    mimetype TEXT,
                    size_bytes INTEGER NOT NULL,
                    url_private_download TEXT,
                    user_id TEXT,
                    created_ts INTEGER,
                    indexed_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, channel_id, file_id),
                    FOREIGN KEY(team_id)
                        REFERENCES slack_installations(team_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS s3_file_manifest (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    s3_bucket TEXT NOT NULL,
                    s3_key TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    saved_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, channel_id, file_id),
                    FOREIGN KEY(team_id, channel_id, file_id)
                        REFERENCES slack_files(team_id, channel_id, file_id)
                        ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS s3_file_manifest_channel_idx
                    ON s3_file_manifest(team_id, channel_id, saved_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_thread_follows (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    thread_ts TEXT NOT NULL,
                    started_by_user_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, channel_id, thread_ts)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS slack_thread_follows_expiry_idx
                    ON slack_thread_follows(expires_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS slack_drift_alerts (
                    team_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    issue_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    s3_bucket TEXT NOT NULL,
                    s3_key TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(team_id, channel_id, issue_key)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS slack_drift_alerts_seen_idx
                    ON slack_drift_alerts(team_id, last_seen_at)
                """
            )
            conn.execute(
                """
                INSERT INTO nimbus_slack_schema_metadata (name, version)
                VALUES ('slack', %s)
                ON CONFLICT (name) DO UPDATE
                SET version = EXCLUDED.version,
                    updated_at = NOW()
                """,
                (SLACK_SCHEMA_VERSION,),
            )

    @contextmanager
    def _transaction(self) -> Iterator[Connection[dict[str, object]]]:
        """Open a Postgres transaction and translate SDK failures."""
        try:
            with postgres_transaction(self._database_url) as conn:
                yield conn
        except SlackStoreError:
            raise
        except psycopg.Error as exc:
            msg = "Postgres Slack store operation failed."
            raise SlackStoreError(msg) from exc

    def _write_tenant_config(
        self,
        conn: Connection[dict[str, object]],
        config: TenantConfig,
    ) -> None:
        """Insert or replace encrypted tenant configuration on a connection."""
        conn.execute(
            """
            INSERT INTO tenant_configs (
                team_id,
                openrouter_api_key_ciphertext,
                aws_access_key_id_ciphertext,
                aws_secret_access_key_ciphertext,
                aws_region,
                s3_bucket,
                s3_prefix,
                status,
                updated_at,
                validated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(team_id) DO UPDATE SET
                openrouter_api_key_ciphertext =
                    excluded.openrouter_api_key_ciphertext,
                aws_access_key_id_ciphertext =
                    excluded.aws_access_key_id_ciphertext,
                aws_secret_access_key_ciphertext =
                    excluded.aws_secret_access_key_ciphertext,
                aws_region = excluded.aws_region,
                s3_bucket = excluded.s3_bucket,
                s3_prefix = excluded.s3_prefix,
                status = excluded.status,
                updated_at = excluded.updated_at,
                validated_at = excluded.validated_at
            """,
            (
                config.team_id,
                self._codec.encrypt(
                    config.openrouter_api_key,
                    tenant_id=config.team_id,
                    field_name="openrouter_api_key",
                    record_id=config.team_id,
                    purpose="tenant_config",
                ),
                self._codec.encrypt(
                    config.aws_access_key_id,
                    tenant_id=config.team_id,
                    field_name="aws_access_key_id",
                    record_id=config.team_id,
                    purpose="tenant_config",
                ),
                self._codec.encrypt(
                    config.aws_secret_access_key,
                    tenant_id=config.team_id,
                    field_name="aws_secret_access_key",
                    record_id=config.team_id,
                    purpose="tenant_config",
                ),
                config.aws_region,
                config.s3_bucket,
                config.s3_prefix,
                config.status,
                _to_iso(config.updated_at),
                _to_optional_iso(config.validated_at),
            ),
        )

    def _installation_from_row(
        self,
        row: dict[str, object],
    ) -> SlackInstallation:
        """Map a Postgres row to a decrypted installation object."""
        team_id = _row_str(row, "team_id")
        return SlackInstallation(
            team_id=team_id,
            enterprise_id=_row_optional_str(row, "enterprise_id"),
            team_name=_row_optional_str(row, "team_name"),
            bot_user_id=_row_optional_str(row, "bot_user_id"),
            bot_token=self._codec.decrypt(
                _row_str(row, "bot_token_ciphertext"),
                tenant_id=team_id,
                field_name="bot_token",
                record_id=team_id,
                purpose="slack_installation",
            ),
            scopes=_json_tuple(_row_str(row, "scopes_json")),
            installed_by=_row_optional_str(row, "installed_by"),
            installed_at=_from_iso(_row_str(row, "installed_at")),
            uninstalled_at=_from_optional_iso(_row_optional_str(row, "uninstalled_at")),
        )

    def _tenant_config_from_row(self, row: dict[str, object]) -> TenantConfig:
        """Map a Postgres row to a decrypted tenant configuration."""
        team_id = _row_str(row, "team_id")
        return TenantConfig(
            team_id=team_id,
            openrouter_api_key=self._codec.decrypt(
                _row_str(row, "openrouter_api_key_ciphertext"),
                tenant_id=team_id,
                field_name="openrouter_api_key",
                record_id=team_id,
                purpose="tenant_config",
            ),
            aws_access_key_id=self._codec.decrypt(
                _row_str(row, "aws_access_key_id_ciphertext"),
                tenant_id=team_id,
                field_name="aws_access_key_id",
                record_id=team_id,
                purpose="tenant_config",
            ),
            aws_secret_access_key=self._codec.decrypt(
                _row_str(row, "aws_secret_access_key_ciphertext"),
                tenant_id=team_id,
                field_name="aws_secret_access_key",
                record_id=team_id,
                purpose="tenant_config",
            ),
            aws_region=_row_str(row, "aws_region"),
            s3_bucket=_row_str(row, "s3_bucket"),
            s3_prefix=_row_optional_str(row, "s3_prefix") or "",
            status=_row_str(row, "status"),
            updated_at=_from_iso(_row_str(row, "updated_at")),
            validated_at=_from_optional_iso(_row_optional_str(row, "validated_at")),
        )

    @staticmethod
    def _setup_session_from_row(row: dict[str, object]) -> SetupSession:
        """Map a Postgres row to a setup session object."""
        return SetupSession(
            token_hash=_row_str(row, "token_hash"),
            team_id=_row_str(row, "team_id"),
            user_id=_row_str(row, "user_id"),
            created_at=_from_iso(_row_str(row, "created_at")),
            expires_at=_from_iso(_row_str(row, "expires_at")),
            consumed_at=_from_optional_iso(_row_optional_str(row, "consumed_at")),
        )


SlackStoreBackend = SlackStore | PostgresSlackStore


def _utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)


def _to_iso(value: datetime) -> str:
    """Serialize a timestamp as UTC ISO 8601."""
    return value.astimezone(UTC).isoformat()


def _to_optional_iso(value: datetime | None) -> str | None:
    """Serialize an optional timestamp."""
    return _to_iso(value) if value is not None else None


def _from_iso(value: str) -> datetime:
    """Parse an ISO 8601 timestamp and normalize it to UTC."""
    return datetime.fromisoformat(value).astimezone(UTC)


def _from_optional_iso(value: str | None) -> datetime | None:
    """Parse an optional ISO 8601 timestamp."""
    return _from_iso(value) if value is not None else None


def _hash_token(token: str) -> str:
    """Hash a bearer setup token before it enters durable storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _saved_file_from_row(
    row: sqlite3.Row | dict[str, object],
) -> SavedSlackFileRecord:
    """Map a manifest row to a SavedSlackFileRecord."""
    return SavedSlackFileRecord(
        team_id=_row_str(row, "team_id"),
        channel_id=_row_str(row, "channel_id"),
        file_id=_row_str(row, "file_id"),
        content_sha256=_row_str(row, "content_sha256"),
        s3_bucket=_row_str(row, "s3_bucket"),
        s3_key=_row_str(row, "s3_key"),
        size_bytes=_row_int(row, "size_bytes"),
        saved_at=_from_iso(_row_str(row, "saved_at")),
    )


def _row_int(row: sqlite3.Row | dict[str, object], key: str) -> int:
    """Return an integer field from a row, validating the type."""
    value = row[key]
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    msg = f"Stored Slack row field {key!r} must be an integer."
    raise SlackStoreError(msg)


def _row_str(row: sqlite3.Row | dict[str, object], key: str) -> str:
    """Return a required text field from a row."""
    value = row[key]
    if isinstance(value, str) and value:
        return value
    msg = f"Stored Slack row field {key!r} must be a non-empty string."
    raise SlackStoreError(msg)


def _row_optional_str(row: sqlite3.Row | dict[str, object], key: str) -> str | None:
    """Return an optional text field from a row."""
    value = row[key]
    if value is None or isinstance(value, str):
        return value
    msg = f"Stored Slack row field {key!r} must be a string or null."
    raise SlackStoreError(msg)


def _json_tuple(value: str) -> tuple[str, ...]:
    """Parse a JSON list of strings into an immutable tuple."""
    parsed = json.loads(value)
    if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
        return tuple(parsed)
    msg = "Stored Slack scopes must be a JSON array of strings."
    raise SlackStoreError(msg)
