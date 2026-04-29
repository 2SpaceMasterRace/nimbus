"""Orchestration helpers for Slack-event to Nimbus-turn processing."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from nimbus_runtime.models import ChatTurnResult

from ai_client_api import AIClientError
from nimbus_slack.body import build_event_body
from nimbus_slack.client import call_nimbus
from nimbus_slack.commands import SlackCommand, SlackCommandKind, parse_slack_command
from nimbus_slack.crypto import SecretCodecError
from nimbus_slack.deps import get_file_sync_service, get_slack_poster, get_slack_store
from nimbus_slack.file_sync import (
    SlackFileSyncError,
    format_diff_report,
    format_save_report,
)
from nimbus_slack.oauth import NIMBUS_SLACK_PUBLIC_BASE_URL
from nimbus_slack.runtime import (
    SlackTenantRuntimeError,
    run_tenant_runtime_turn,
    tenant_local_runtime_enabled,
)
from nimbus_slack.store import SlackStoreError

if TYPE_CHECKING:
    from nimbus_slack.deps import SlackPoster
    from nimbus_slack.file_sync import SlackFileSyncService
    from nimbus_slack.models import NimbusTurnRequest


def should_handle_event(event: dict[str, object]) -> bool:
    """Return whether a Slack event should become a Nimbus turn."""
    if event.get("bot_id") is not None:
        return False
    subtype = event.get("subtype")
    if subtype in {"bot_message", "message_deleted", "message_changed"}:
        return False
    event_type = event.get("type")
    if event_type not in {"message", "app_mention"}:
        return False
    return bool(event.get("user")) and bool(event.get("text"))


def handle_slack_event(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, object],
    poster: SlackPoster | None = None,
    file_service: SlackFileSyncService | None = None,
) -> ChatTurnResult:
    """Send one Slack event through Nimbus and post the result back to chat.

    Args:
        team_id: Slack team ID from the outer event-callback payload.
        event_id: Slack event ID used for idempotency tracking.
        event: Inner Slack event payload.
        poster: Optional injected poster used for testing/call-site overrides.
        file_service: Optional injected file service for adapter-owned file
            commands.

    Returns:
        The parsed Nimbus chat-turn result.

    """
    if not should_handle_event(event):
        msg = "Slack event is not a user-authored message Nimbus should handle"
        raise ValueError(msg)
    turn = build_event_body(team_id=team_id, event_id=event_id, event=event)
    command = parse_slack_command(turn.text)
    if command.kind is not SlackCommandKind.MODEL_TURN:
        result = _handle_adapter_command(
            command=command,
            team_id=team_id,
            turn=turn,
            file_service=file_service,
        )
        if result.text.strip():
            resolved_poster = poster or get_slack_poster(team_id=team_id)
            resolved_poster.send_message(
                turn.channel_id,
                result.text,
                thread_ts=turn.thread_id,
            )
        return result

    result = _handle_model_turn(team_id=team_id, turn=turn)
    if result.text.strip():
        resolved_poster = poster or get_slack_poster(team_id=team_id)
        resolved_poster.send_message(
            turn.channel_id,
            result.text,
            thread_ts=turn.thread_id,
        )
    return result


def _handle_model_turn(*, team_id: str, turn: NimbusTurnRequest) -> ChatTurnResult:
    """Handle model-backed Slack turns through remote or tenant-local runtime."""
    if not tenant_local_runtime_enabled():
        return call_nimbus(turn)
    try:
        return run_tenant_runtime_turn(
            team_id=team_id,
            turn=turn,
            store=get_slack_store(),
        )
    except (
        AIClientError,
        SecretCodecError,
        SlackStoreError,
        SlackTenantRuntimeError,
    ) as exc:
        return _command_result(
            turn=turn,
            text=f"I could not run the tenant-local Nimbus runtime: {exc}",
        )


def _handle_adapter_command(
    *,
    command: SlackCommand,
    team_id: str,
    turn: NimbusTurnRequest,
    file_service: SlackFileSyncService | None,
) -> ChatTurnResult:
    """Handle commands owned by the Slack adapter itself."""
    if command.kind is SlackCommandKind.SETUP:
        return _command_result(turn=turn, text=_setup_text())
    try:
        service = file_service or _file_service(team_id)
        if command.kind is SlackCommandKind.DIFF_CHANNEL_FILES:
            report = service.diff_channel(team_id=team_id, channel_id=turn.channel_id)
            return _command_result(turn=turn, text=format_diff_report(report))
        if command.kind is SlackCommandKind.SAVE_CHANNEL_FILES:
            report = service.save_channel(team_id=team_id, channel_id=turn.channel_id)
            return _command_result(turn=turn, text=format_save_report(report))
    except (SlackFileSyncError, ValueError) as exc:
        return _command_result(
            turn=turn,
            text=f"I could not complete that Slack file operation: {exc}",
        )
    msg = f"Unhandled Slack command kind: {command.kind}"
    raise ValueError(msg)


def _file_service(team_id: str) -> SlackFileSyncService:
    """Resolve the production file sync service lazily."""
    return get_file_sync_service(team_id=team_id)


def _setup_text() -> str:
    """Return setup guidance without accepting secrets in Slack."""
    base_url = os.environ.get(NIMBUS_SLACK_PUBLIC_BASE_URL, "").rstrip("/")
    if not base_url:
        return (
            "Nimbus setup is browser-based so secrets never enter Slack. "
            f"Set `{NIMBUS_SLACK_PUBLIC_BASE_URL}` and then open "
            "`/slack/install` on the deployed Nimbus Slack service."
        )
    return (
        "Nimbus setup is browser-based so secrets never enter Slack. "
        f"Open {base_url}/slack/install to install or reconfigure this workspace."
    )


def _command_result(*, turn: NimbusTurnRequest, text: str) -> ChatTurnResult:
    """Build a synthetic Nimbus result for adapter-owned commands."""
    return ChatTurnResult(
        request_id=turn.request_id or turn.idempotency_key,
        conversation_id=turn.thread_id or turn.message_id,
        text=text,
        outcome="reply",
        confirmation_required=False,
        model="nimbus-slack",
        steps=1,
        fallback_used=False,
    )
