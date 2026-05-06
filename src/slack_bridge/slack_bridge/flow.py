"""Orchestration helpers for Slack-event to Nimbus-turn processing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from slack_bridge.body import build_event_body, build_slash_command_body
from slack_bridge.client import call_nimbus
from slack_bridge.deps import get_chat_client
from slack_bridge.render import render_for_chat

if TYPE_CHECKING:
    from collections.abc import Mapping

    from chat_client_api import ChatClient
    from nimbus_runtime.models import ChatTurnResult

    from slack_bridge.models import NimbusTurnRequest

log = structlog.get_logger()

_AI_FAILURE_MESSAGE = (
    "Sorry, I couldn't reach the AI service right now. Please try again in a moment."
)


def handle_slack_event(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, object],
    chat_client: ChatClient | None = None,
) -> ChatTurnResult:
    """Send one Slack event through Nimbus and post the result back to chat.

    Failure model:

    * If :func:`call_nimbus` raises (after its own internal retries), the
      bridge posts a short, user-visible failure message back to the
      channel and re-raises so the background dispatcher records the
      structured failure for ops. A best-effort send_message failure
      during that fallback is logged and absorbed so the original
      exception is not lost.

    Args:
        team_id: Slack team ID from the outer event-callback payload.
        event_id: Slack event ID used for idempotency tracking.
        event: Inner Slack event payload.
        chat_client: Optional injected client used for testing/call-site overrides.

    Returns:
        The parsed Nimbus chat-turn result.

    """
    turn = build_event_body(team_id=team_id, event_id=event_id, event=event)
    return _dispatch_turn(
        turn,
        chat_client=chat_client,
        log_context={
            "source": "event",
            "team_id": team_id,
            "event_id": event_id,
        },
    )


def handle_slack_command(
    form: Mapping[str, str],
    *,
    chat_client: ChatClient | None = None,
) -> ChatTurnResult:
    """Send one Slack slash-command turn through Nimbus and post the result.

    Slash commands share the same downstream contract and failure model
    as message events: the bridge builds a signed turn, retries via
    :func:`call_nimbus`, and posts a user-visible message via the chat
    client (rendered for ``confirmation_required`` outcomes, raw text
    otherwise). On hard failure a short fallback message is posted and
    the exception is re-raised so the background dispatcher records the
    failure for ops.

    Args:
        form: Decoded Slack slash-command form payload. The form must
            include ``team_id``, ``trigger_id``, ``channel_id``,
            ``user_id``; ``text`` is optional.
        chat_client: Optional injected client used for testing/call-site overrides.

    Returns:
        The parsed Nimbus chat-turn result.

    """
    turn = build_slash_command_body(form)
    return _dispatch_turn(
        turn,
        chat_client=chat_client,
        log_context={
            "source": "slash_command",
            "team_id": form.get("team_id"),
            "trigger_id": form.get("trigger_id"),
            "command": form.get("command"),
        },
    )


def _dispatch_turn(
    turn: NimbusTurnRequest,
    *,
    chat_client: ChatClient | None,
    log_context: Mapping[str, object],
) -> ChatTurnResult:
    """Sign, send, and respond for one already-built turn request.

    This is the shared core for :func:`handle_slack_event` and
    :func:`handle_slack_command`. It centralizes the failure model so the
    two entry points cannot drift in how they handle Nimbus errors or
    chat-client failures during the fallback notification.
    """
    resolved_chat_client = chat_client or get_chat_client()
    try:
        result = call_nimbus(turn)
    except Exception:
        log.exception(
            "slack_bridge_nimbus_call_failed",
            request_id=turn.request_id,
            **log_context,
        )
        try:
            resolved_chat_client.send_message(turn.channel_id, _AI_FAILURE_MESSAGE)
        except Exception:
            log.exception(
                "slack_bridge_failure_notification_failed",
                channel_id=turn.channel_id,
                **log_context,
            )
        raise
    resolved_chat_client.send_message(turn.channel_id, render_for_chat(result))
    return result
