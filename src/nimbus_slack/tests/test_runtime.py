"""Tests for tenant-local Slack runtime selection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import nimbus_slack.runtime as runtime_module
import pytest
from cryptography.fernet import Fernet
from nimbus_slack.crypto import SecretCodec
from nimbus_slack.models import NimbusTurnRequest
from nimbus_slack.runtime import (
    NIMBUS_SLACK_MODEL_MODE,
    NIMBUS_SLACK_MODEL_MODE_AUTO,
    NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL,
    SlackTenantRuntimeError,
    run_tenant_runtime_turn,
    slack_model_mode,
    tenant_local_runtime_enabled,
)
from nimbus_slack.store import SlackInstallation, SlackStore, TenantConfig

pytestmark = pytest.mark.unit


def test_tenant_local_runtime_mode_is_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tenant-local runtime mode should require an explicit env value."""
    monkeypatch.delenv(NIMBUS_SLACK_MODEL_MODE, raising=False)
    assert not tenant_local_runtime_enabled()
    assert slack_model_mode() == NIMBUS_SLACK_MODEL_MODE_AUTO

    monkeypatch.setenv(NIMBUS_SLACK_MODEL_MODE, NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL)
    assert tenant_local_runtime_enabled()


def test_slack_model_mode_rejects_unknown_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid model routing mode should fail closed at startup/use time."""
    monkeypatch.setenv(NIMBUS_SLACK_MODEL_MODE, "surprise")

    with pytest.raises(SlackTenantRuntimeError, match=NIMBUS_SLACK_MODEL_MODE):
        slack_model_mode()


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


def test_tenant_local_runtime_can_complete_guarded_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack tenant-local turns should use the same delete action flow as CLI/API."""
    deletes: list[dict[str, str]] = []

    class _FakeOpenRouterClient:
        """OpenRouter stand-in; direct delete turns should not call it."""

        def __init__(self, config: object) -> None:
            self.config = config

        def send_message(self, *_args: object, **_kwargs: object) -> object:
            msg = "delete confirmation should bypass the model"
            raise AssertionError(msg)

    class _FakeS3Client:
        """S3 stand-in that records delete calls across runtime instances."""

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return

        def delete_file(self, *, container: str, object_name: str) -> dict[str, object]:
            deletes.append({"container": container, "object_name": object_name})
            return {"deleted": True, "version_id": "v-slack-delete"}

    class _FakeStore:
        """Tenant config provider for run_tenant_runtime_turn."""

        def get_tenant_config(self, team_id: str) -> TenantConfig | None:
            assert team_id == "T123"
            return TenantConfig(
                team_id="T123",
                openrouter_api_key="sk-or-secret",
                aws_access_key_id="AKIA_TEST",
                aws_secret_access_key="aws-secret",  # noqa: S106
                aws_region="us-east-1",
                s3_bucket="nimbus-test-bucket",
                s3_prefix="slack/archive",
                status="configured",
                updated_at=datetime(2026, 5, 9, tzinfo=UTC),
            )

    monkeypatch.setenv("NIMBUS_SLACK_SESSION_DIR", str(tmp_path))
    monkeypatch.setattr(runtime_module, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(runtime_module, "S3Client", _FakeS3Client)

    first = run_tenant_runtime_turn(
        team_id="T123",
        turn=NimbusTurnRequest(
            platform="slack",
            workspace_id="T123",
            channel_id="C123",
            thread_id="1710000000.000001",
            message_id="1710000000.000001",
            user_id="U123",
            text="delete reports/old.csv",
            idempotency_key="slack:T123:event:Ev-delete-1",
        ),
        store=_FakeStore(),  # type: ignore[arg-type]
    )
    second = run_tenant_runtime_turn(
        team_id="T123",
        turn=NimbusTurnRequest(
            platform="slack",
            workspace_id="T123",
            channel_id="C123",
            thread_id="1710000000.000001",
            message_id="1710000000.000002",
            user_id="U123",
            text="yes, delete reports/old.csv",
            idempotency_key="slack:T123:event:Ev-delete-2",
        ),
        store=_FakeStore(),  # type: ignore[arg-type]
    )

    assert first.outcome == "confirmation_required"
    assert first.confirmation is not None
    assert second.text == "Deleted `reports/old.csv`."
    assert second.artifacts[0].payload is not None
    assert second.artifacts[0].payload["remote_path"] == "reports/old.csv"
    assert second.artifacts[0].payload["deleted"] is True
    assert second.artifacts[0].payload["version_id"] == "v-slack-delete"
    restore_plan = second.artifacts[0].payload["restore_plan"]
    assert isinstance(restore_plan, dict)
    assert restore_plan["strategy"] == "s3_version"
    assert deletes == [
        {"container": "nimbus-test-bucket", "object_name": "reports/old.csv"}
    ]
