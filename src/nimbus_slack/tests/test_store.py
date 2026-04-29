"""Tests for the Nimbus Slack control-plane store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from nimbus_slack.crypto import SecretCodec
from nimbus_slack.store import SlackInstallation, SlackStore, TenantConfig

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
