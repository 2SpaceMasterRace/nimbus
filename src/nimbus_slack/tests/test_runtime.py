"""Tests for tenant-local Slack runtime selection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from nimbus_slack.crypto import SecretCodec
from nimbus_slack.models import NimbusTurnRequest
from nimbus_slack.runtime import (
    NIMBUS_SLACK_MODEL_MODE,
    NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL,
    SlackTenantRuntimeError,
    run_tenant_runtime_turn,
    tenant_local_runtime_enabled,
)
from nimbus_slack.store import SlackInstallation, SlackStore

pytestmark = pytest.mark.unit


def test_tenant_local_runtime_mode_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant-local runtime mode should require an explicit env value."""
    monkeypatch.delenv(NIMBUS_SLACK_MODEL_MODE, raising=False)
    assert not tenant_local_runtime_enabled()

    monkeypatch.setenv(NIMBUS_SLACK_MODEL_MODE, NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL)
    assert tenant_local_runtime_enabled()


def test_run_tenant_runtime_requires_byok_config(tmp_path: Path) -> None:
    """Tenant-local runtime should fail closed when BYOK setup is missing."""
    store = SlackStore(
        db_path=tmp_path / "slack.sqlite3",
        codec=SecretCodec.from_key(Fernet.generate_key().decode("utf-8")),
    )
    store.upsert_installation(
        SlackInstallation(
            team_id="T123",
            enterprise_id=None,
            team_name="Nimbus Lab",
            bot_user_id="Ubot",
            bot_token="xoxb-runtime-token",  # noqa: S106
            scopes=("chat:write",),
            installed_by="Uadmin",
            installed_at=datetime(2026, 5, 9, tzinfo=UTC),
        )
    )

    with pytest.raises(SlackTenantRuntimeError):
        run_tenant_runtime_turn(
            team_id="T123",
            turn=NimbusTurnRequest(
                platform="slack",
                workspace_id="T123",
                channel_id="C123",
                message_id="1710000000.123",
                user_id="U123",
                text="hello",
                idempotency_key="slack:T123:event:Ev1",
            ),
            store=store,
        )
