"""Tenant-local Nimbus runtime construction for Slack workspaces."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from aws_client_impl.s3_client import S3Client
from nimbus_runtime.models import ChatTurnInput, ChatTurnResult
from openrouter_ai_client_impl.config import (
    DEFAULT_BASE_URL,
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    DEFAULT_SYSTEM_PROMPT,
    OpenRouterConfig,
)
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient

from nimbus_runtime import NimbusRuntime, runtime_telemetry
from nimbus_slack.store import SlackStore, TenantConfig, default_store_path

if TYPE_CHECKING:
    from nimbus_slack.models import NimbusTurnRequest

NIMBUS_SLACK_MODEL_MODE = "NIMBUS_SLACK_MODEL_MODE"
NIMBUS_SLACK_MODEL_MODE_REMOTE = "remote"
NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL = "tenant-local"
NIMBUS_SLACK_SESSION_DIR = "NIMBUS_SLACK_SESSION_DIR"


class SlackTenantRuntimeError(RuntimeError):
    """Raised when a tenant-local Slack runtime cannot be constructed."""


def tenant_local_runtime_enabled() -> bool:
    """Return whether Slack model turns should use tenant BYOK locally."""
    mode = os.environ.get(NIMBUS_SLACK_MODEL_MODE, NIMBUS_SLACK_MODEL_MODE_REMOTE)
    return mode.strip().lower() == NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL


def run_tenant_runtime_turn(
    *,
    team_id: str,
    turn: NimbusTurnRequest,
    store: SlackStore,
) -> ChatTurnResult:
    """Run one Slack turn through an in-process tenant-local Nimbus runtime."""
    config = store.get_tenant_config(team_id)
    if config is None:
        msg = "Nimbus Slack tenant-local mode requires BYOK setup for this workspace."
        raise SlackTenantRuntimeError(msg)
    runtime = build_tenant_runtime(team_id=team_id, config=config)
    return asyncio.run(runtime.run_chat_turn(_chat_turn_input(turn)))


def build_tenant_runtime(*, team_id: str, config: TenantConfig) -> NimbusRuntime:
    """Build an in-process runtime backed by tenant OpenRouter and S3 keys."""
    return NimbusRuntime(
        ai_client=OpenRouterClient(_openrouter_config(config)),
        storage=S3Client(
            region_name=config.aws_region,
            aws_access_key_id=config.aws_access_key_id,
            aws_secret_access_key=config.aws_secret_access_key,
        ),
        session_dir=_session_dir(team_id),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        tool_container=config.s3_bucket,
        telemetry=runtime_telemetry,
        storage_tools_enabled=True,
    )


def _openrouter_config(config: TenantConfig) -> OpenRouterConfig:
    """Build OpenRouter configuration from tenant BYOK plus env defaults."""
    fallback = os.environ.get("OPENROUTER_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL)
    return OpenRouterConfig(
        api_key=config.openrouter_api_key,
        model=os.environ.get("OPENROUTER_MODEL", DEFAULT_MODEL).strip()
        or DEFAULT_MODEL,
        fallback_model=fallback.strip() or None,
        base_url=os.environ.get("OPENROUTER_BASE_URL", DEFAULT_BASE_URL).strip()
        or DEFAULT_BASE_URL,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        app_referer=os.environ.get("OPENROUTER_APP_REFERER") or None,
        app_title=os.environ.get("OPENROUTER_APP_TITLE") or None,
    )


def _session_dir(team_id: str) -> Path:
    """Return tenant-local session state directory."""
    configured = os.environ.get(NIMBUS_SLACK_SESSION_DIR, "").strip()
    base = Path(configured).expanduser() if configured else default_store_path().parent
    session_dir = base / "sessions" / team_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _chat_turn_input(turn: NimbusTurnRequest) -> ChatTurnInput:
    """Map the Slack wire request into the runtime turn contract."""
    return ChatTurnInput(
        request_id=turn.request_id or turn.idempotency_key,
        conversation_id=turn.thread_id or turn.message_id,
        platform=turn.platform,
        workspace_id=turn.workspace_id,
        channel_id=turn.channel_id,
        thread_id=turn.thread_id,
        message_id=turn.message_id,
        user_id=turn.user_id,
        text=turn.text,
        idempotency_key=turn.idempotency_key,
        attachments=turn.attachments,
    )
