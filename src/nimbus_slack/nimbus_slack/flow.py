"""Orchestration helpers for Slack-event to Nimbus-turn processing."""

from __future__ import annotations

import contextlib
import dataclasses
import os
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import structlog
from nimbus_runtime.capabilities import all_capabilities
from nimbus_runtime.models import ChatTurnResult
from nimbus_runtime.telemetry import runtime_telemetry
from opentelemetry import trace
from slack_sdk.errors import SlackApiError

from ai_client_api import AIClientError
from nimbus_slack import design
from nimbus_slack.blocks import (
    app_home_card,
    approval_request_card,
    blocks_to_fallback_text,
    capability_list_card,
    changed_since_sync_card,
    dedupe_report_card,
    diff_report_card,
    failure_card,
    file_list_card,
    save_progress_card,
    save_report_card,
    search_results_card,
    workspace_status_card,
)
from nimbus_slack.body import build_event_body
from nimbus_slack.client import call_nimbus
from nimbus_slack.commands import SlackCommand, SlackCommandKind, parse_slack_command
from nimbus_slack.crypto import SecretCodecError
from nimbus_slack.deps import (
    get_file_sync_service,
    get_slack_home_publisher,
    get_slack_poster,
    get_slack_store,
)
from nimbus_slack.file_sync import (
    SaveProgress,
    SlackFileSyncError,
)
from nimbus_slack.models import NimbusTurnRequest
from nimbus_slack.oauth import NIMBUS_SLACK_PUBLIC_BASE_URL
from nimbus_slack.profile import (
    PROFILE_TIMING_FLAG,
    ProfileTrace,
    extract_profile_timing_mode,
    profile_trace_card,
)
from nimbus_slack.runtime import (
    NIMBUS_SLACK_MODEL_MODE_AUTO,
    NIMBUS_SLACK_MODEL_MODE_REMOTE,
    NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL,
    SlackTenantConfigMissingError,
    SlackTenantRuntimeError,
    run_tenant_runtime_turn,
    slack_model_mode,
)
from nimbus_slack.store import SlackStoreError

if TYPE_CHECKING:
    from nimbus_runtime.models import ConfirmationDetails

    from nimbus_slack.deps import SlackHomePublisher, SlackPoster
    from nimbus_slack.file_sync import SlackFileSyncService

log = structlog.get_logger()
_tracer = trace.get_tracer("nimbus-slack")

NIMBUS_SLACK_THREAD_FOLLOW_TTL_SECONDS = "NIMBUS_SLACK_THREAD_FOLLOW_TTL_SECONDS"
DEFAULT_THREAD_FOLLOW_TTL_SECONDS = 30 * 60
MAX_THREAD_FOLLOW_TTL_SECONDS = 24 * 60 * 60
_CHANNEL_MENTION_RE = re.compile(r"<#([A-Z0-9]+)(?:\|[^>]+)?>")
_MULTI_CHANNEL_SAVE_PREVIEW_LIMIT = 5

_CONFIRMATION_CONSEQUENCES: dict[str, str] = {
    "delete_file": (
        "This permanently removes the file from storage."
        " Restore requires S3 bucket versioning."
    ),
    "move_file": "This moves the file — the original path will no longer exist.",
    "copy_file": "This creates a copy at the destination path.",
    "write_file": "This overwrites the existing file content.",
}


def handle_app_home_opened(
    *,
    team_id: str,
    user_id: str,
    publisher: SlackHomePublisher | None = None,
) -> None:
    """Publish the Nimbus App Home tab view for a user who opened the tab.

    Args:
        team_id: Slack workspace ID — used to query tenant stores and look
            up the bot token when ``publisher`` is not provided.
        user_id: Slack user ID — the user whose Home tab is being refreshed.
        publisher: Optional injected home publisher; falls back to the live
            ``SlackSdkHomePublisher`` when omitted.

    Raises:
        ValueError: When no bot token can be resolved for the workspace.

    """
    with _tracer.start_as_current_span(
        "slack.app_home_opened",
        attributes={"slack.team_id": team_id, "slack.user_id": user_id},
    ):
        blocks = _app_home_blocks(team_id=team_id)
        pub = publisher or get_slack_home_publisher(team_id=team_id)
        pub.publish_home_tab(user_id, blocks)
        log.info("slack_home_published", team_id=team_id, user_id=user_id)


def _app_home_blocks(*, team_id: str) -> list[dict[str, object]]:
    """Build home-tab blocks from live tenant store data."""
    # Reuse the same query logic as _workspace_status_blocks — import locally
    # so tests can avoid the full store stack.
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from nimbus_runtime.domain import (  # noqa: PLC0415
        ApprovalStatus,
        PlanStatus,
        TaskStatus,
        TenantIdentity,
    )
    from nimbus_runtime.stores import (  # noqa: PLC0415
        FileApprovalStore,
        FilePlanStore,
        FileTaskStore,
    )

    from nimbus_slack.runtime import _session_dir  # noqa: PLC0415

    session_dir = _session_dir(team_id)
    tenant = TenantIdentity(platform="slack", workspace_id=team_id)

    _running = {
        TaskStatus.CREATED,
        TaskStatus.PLANNING,
        TaskStatus.SCANNING,
        TaskStatus.DIFFING,
        TaskStatus.APPLYING,
        TaskStatus.VERIFYING,
    }
    task_store = FileTaskStore(session_dir)
    all_tasks = task_store.list_for_tenant(tenant=tenant, limit=500)
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_cutoff = today_start - timedelta(hours=1)

    tasks_running = sum(1 for t in all_tasks if t.status in _running)
    tasks_awaiting = sum(
        1 for t in all_tasks if t.status is TaskStatus.AWAITING_APPROVAL
    )
    tasks_done_today = sum(
        1
        for t in all_tasks
        if t.status is TaskStatus.DONE and t.updated_at >= today_cutoff
    )
    tasks_failed = sum(1 for t in all_tasks if t.status is TaskStatus.FAILED)

    approval_store = FileApprovalStore(session_dir)
    all_approvals = approval_store.list_for_tenant(tenant=tenant, limit=200)
    pending_approvals = sum(
        1 for a in all_approvals if a.status is ApprovalStatus.PENDING
    )

    plan_store = FilePlanStore(session_dir)
    all_plans = plan_store.list_for_tenant(tenant=tenant, limit=200)
    proposed_plans = sum(1 for p in all_plans if p.status is PlanStatus.PROPOSED)

    return app_home_card(
        team_id=team_id,
        tasks_running=tasks_running,
        tasks_awaiting=tasks_awaiting,
        tasks_done_today=tasks_done_today,
        tasks_failed=tasks_failed,
        pending_approvals=pending_approvals,
        proposed_plans=proposed_plans,
    )


def should_handle_event(  # noqa: PLR0911 - guard clauses keep event gating explicit.
    event: dict[str, object],
    *,
    team_id: str | None = None,
) -> bool:
    """Return whether a Slack event should become a Nimbus turn."""
    if event.get("bot_id") is not None:
        return False
    subtype = event.get("subtype")
    if subtype in {"bot_message", "message_deleted", "message_changed"}:
        return False
    event_type = event.get("type")
    if event_type not in {"message", "app_mention"}:
        return False
    if not bool(event.get("user")) or not bool(event.get("text")):
        return False
    if event_type == "app_mention":
        return True
    if _has_leading_app_mention(event):
        return True
    if event.get("channel_type") == "im":
        return True
    return _is_followed_thread_reply(event=event, team_id=team_id)


def _has_leading_app_mention(event: dict[str, object]) -> bool:
    """Return whether a message event is explicitly addressed to Nimbus."""
    text = event.get("text")
    if not isinstance(text, str):
        return False
    parts = text.split(maxsplit=1)
    first = parts[0] if parts else ""
    return first.startswith("<@") and first.endswith(">")


def _thread_follow_ttl_seconds() -> int:
    """Return the configured Slack thread-follow TTL, bounded for safety."""
    raw_value = os.environ.get(
        NIMBUS_SLACK_THREAD_FOLLOW_TTL_SECONDS,
        str(DEFAULT_THREAD_FOLLOW_TTL_SECONDS),
    )
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_THREAD_FOLLOW_TTL_SECONDS
    return max(0, min(parsed, MAX_THREAD_FOLLOW_TTL_SECONDS))


def _thread_key_from_event(
    event: dict[str, object],
    *,
    allow_root: bool,
) -> tuple[str, str] | None:
    """Return ``(channel_id, thread_ts)`` for a Slack event, if available."""
    channel_id = event.get("channel")
    if not isinstance(channel_id, str) or not channel_id:
        return None
    thread_ts = event.get("thread_ts")
    if isinstance(thread_ts, str) and thread_ts:
        return channel_id, thread_ts
    if not allow_root:
        return None
    message_ts = event.get("ts")
    if isinstance(message_ts, str) and message_ts:
        return channel_id, message_ts
    return None


def _is_followed_thread_reply(
    *,
    event: dict[str, object],
    team_id: str | None,
) -> bool:
    """Return whether an unmentioned channel reply belongs to an active thread."""
    if team_id is None:
        return False
    ttl_seconds = _thread_follow_ttl_seconds()
    if ttl_seconds <= 0:
        return False
    key = _thread_key_from_event(event, allow_root=False)
    if key is None:
        return False
    channel_id, thread_ts = key
    try:
        return get_slack_store().is_thread_follow_active(
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            now=datetime.now(UTC),
            refresh_ttl_seconds=ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "slack_thread_follow_lookup_failed",
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            error=str(exc),
        )
        return False


def _maybe_activate_thread_follow(
    *,
    team_id: str,
    event: dict[str, object],
    turn: NimbusTurnRequest,
) -> None:
    """Persist thread-follow state after an explicit channel mention."""
    if event.get("channel_type") == "im":
        return
    if event.get("type") != "app_mention" and not _has_leading_app_mention(event):
        return
    ttl_seconds = _thread_follow_ttl_seconds()
    if ttl_seconds <= 0:
        return
    key = _thread_key_from_event(event, allow_root=True)
    if key is None:
        return
    channel_id, thread_ts = key
    try:
        get_slack_store().activate_thread_follow(
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_id=turn.user_id,
            now=datetime.now(UTC),
            ttl_seconds=ttl_seconds,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "slack_thread_follow_activation_failed",
            team_id=team_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            error=str(exc),
        )


def handle_slack_event(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, object],
    poster: SlackPoster | None = None,
    file_service: SlackFileSyncService | None = None,
) -> ChatTurnResult:
    """Send one Slack event through Nimbus and post the result back to chat.

    When the user includes ``--profile-timing`` anywhere in the message, the
    flag is stripped from the text before command parsing and a follow-up
    Block Kit card with a per-step timing breakdown is posted after the main
    reply. The flag is the Slack-side counterpart to the CLI's
    ``--profile-timing`` global option.

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
    if not should_handle_event(event, team_id=team_id):
        msg = "Slack event is not a user-authored message Nimbus should handle"
        raise ValueError(msg)
    turn = build_event_body(team_id=team_id, event_id=event_id, event=event)
    cleaned_text, profile_mode = extract_profile_timing_mode(turn.text)
    if not cleaned_text:
        msg = (
            "Slack event text must not be empty after stripping the "
            f"{PROFILE_TIMING_FLAG!r} flag"
        )
        raise ValueError(msg)
    if cleaned_text != turn.text:
        turn = dataclasses.replace(turn, text=cleaned_text)
    _maybe_activate_thread_follow(team_id=team_id, event=event, turn=turn)
    profile = ProfileTrace(
        enabled=profile_mode is not None,
        mode=profile_mode or "half",
    )

    with profile.span("slack.parse_command"):
        command = parse_slack_command(turn.text)
    with _tracer.start_as_current_span(
        "slack.handle_event",
        attributes={
            "slack.team_id": team_id,
            "slack.event_id": event_id,
            "slack.channel_id": turn.channel_id,
            "nimbus.command_kind": command.kind.value,
        },
    ) as span:
        if command.kind is not SlackCommandKind.MODEL_TURN:
            with profile.span("slack.adapter_command", kind=command.kind.value):
                result = _handle_adapter_command(
                    command=command,
                    team_id=team_id,
                    turn=turn,
                    file_service=file_service,
                    poster=poster,
                    profile=profile,
                )
        else:
            with profile.span("slack.model_turn"):
                result = _handle_model_turn(team_id=team_id, turn=turn, profile=profile)
            span.set_attribute("nimbus.model", result.model)
        runtime_telemetry.record_slack_turn(
            kind=command.kind.value,
            outcome=result.outcome,
        )
        span.set_attribute("nimbus.outcome", result.outcome)
        with profile.span("slack.post_result", outcome=result.outcome):
            _post_result(team_id=team_id, turn=turn, result=result, poster=poster)
        _maybe_post_profile_card(
            team_id=team_id, turn=turn, profile=profile, poster=poster
        )
        return result


def _maybe_post_profile_card(
    *,
    team_id: str,
    turn: NimbusTurnRequest,
    profile: ProfileTrace,
    poster: SlackPoster | None,
) -> None:
    """Post the timing-trace card to the same thread when profiling was on.

    Failures are logged and swallowed: the main reply has already been posted
    and the trace is a debugging aid, not a correctness signal.
    """
    if not profile.enabled:
        return
    blocks = profile_trace_card(profile)
    fallback = blocks_to_fallback_text(blocks)
    resolved_poster = poster
    if resolved_poster is None:
        try:
            resolved_poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError) as exc:
            log.warning(
                "slack_profile_card_poster_unavailable",
                team_id=team_id,
                error=str(exc),
            )
            return
    try:
        try:
            resolved_poster.send_blocks(
                turn.channel_id, blocks, fallback, thread_ts=turn.thread_id
            )
        except AttributeError:
            resolved_poster.send_message(
                turn.channel_id, fallback, thread_ts=turn.thread_id
            )
    except SlackApiError as exc:
        log.warning(
            "slack_profile_card_post_failed",
            team_id=team_id,
            channel_id=turn.channel_id,
            slack_error=_slack_error(exc),
        )


def _handle_model_turn(
    *,
    team_id: str,
    turn: NimbusTurnRequest,
    profile: ProfileTrace | None = None,
) -> ChatTurnResult:
    """Handle model-backed Slack turns through remote or tenant-local runtime."""
    trace_ = profile or ProfileTrace(enabled=False)
    mode = slack_model_mode()
    if mode == NIMBUS_SLACK_MODEL_MODE_REMOTE:
        return _handle_remote_model_turn(turn=turn, profile=trace_)
    try:
        with trace_.span("slack.runtime.tenant_local", team_id=team_id):
            return run_tenant_runtime_turn(
                team_id=team_id,
                turn=turn,
                store=get_slack_store(),
            )
    except SlackTenantConfigMissingError:
        if mode == NIMBUS_SLACK_MODEL_MODE_AUTO:
            return _handle_remote_model_turn(turn=turn, profile=trace_)
        return _command_result(
            turn=turn,
            text=(
                "Nimbus Slack needs workspace setup before it can use the "
                "tenant-local model runtime. Ask an admin to run `@Nimbus setup`."
            ),
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


def _handle_remote_model_turn(
    *,
    turn: NimbusTurnRequest,
    profile: ProfileTrace | None = None,
) -> ChatTurnResult:
    """Handle one model turn through the remote Nimbus HTTP service."""
    trace_ = profile or ProfileTrace(enabled=False)
    try:
        with trace_.span("slack.runtime.remote"):
            return call_nimbus(turn)
    except (httpx.HTTPError, RuntimeError, TypeError, ValueError) as exc:
        return _command_result(
            turn=turn,
            text=f"I could not reach the Nimbus AI runtime: {exc}",
        )


def _handle_adapter_command(  # noqa: C901, PLR0911, PLR0912, PLR0913 — flat dispatch reads cleaner than a lookup map for these few kinds
    *,
    command: SlackCommand,
    team_id: str,
    turn: NimbusTurnRequest,
    file_service: SlackFileSyncService | None,
    poster: SlackPoster | None = None,
    profile: ProfileTrace | None = None,
) -> ChatTurnResult:
    """Handle commands owned by the Slack adapter itself."""
    trace_ = profile or ProfileTrace(enabled=False)
    if command.kind is SlackCommandKind.SETUP:
        install_url = _setup_install_url()
        if install_url is None:
            # No public base URL configured — fall back to plain text guidance.
            return _command_result(turn=turn, text=_setup_text())
        return _post_and_return(
            team_id=team_id,
            turn=turn,
            poster=poster,
            blocks=design.setup_card(install_url=install_url),
        )
    if command.kind is SlackCommandKind.STATUS:
        with trace_.span("slack.workspace_status"):
            blocks = _workspace_status_blocks(team_id=team_id)
        return _post_and_return(
            team_id=team_id,
            turn=turn,
            poster=poster,
            blocks=blocks,
        )
    if command.kind is SlackCommandKind.TOOLS:
        blocks = capability_list_card(all_capabilities())
        return _post_and_return(
            team_id=team_id,
            turn=turn,
            poster=poster,
            blocks=blocks,
        )
    if command.kind is SlackCommandKind.SEARCH:
        with trace_.span("slack.search"):
            blocks = _search_result_blocks(team_id=team_id, query=turn.text)
        return _post_and_return(
            team_id=team_id,
            turn=turn,
            poster=poster,
            blocks=blocks,
        )
    try:
        service = file_service or _file_service(team_id)
        if command.kind is SlackCommandKind.DIFF_CHANNEL_FILES:
            with trace_.span("slack.file_service.diff_channel"):
                report = service.diff_channel(
                    team_id=team_id, channel_id=turn.channel_id
                )
            return _post_and_return(
                team_id=team_id,
                turn=turn,
                poster=poster,
                blocks=diff_report_card(report),
            )
        if command.kind is SlackCommandKind.SAVE_CHANNEL_FILES:
            with trace_.span("slack.file_service.save_channel"):
                mentioned_channel_ids = _mentioned_channel_ids(turn.text)
                if mentioned_channel_ids:
                    return _run_multi_channel_save(
                        team_id=team_id,
                        turn=turn,
                        service=service,
                        channel_ids=mentioned_channel_ids,
                    )
                return _run_streaming_save(
                    team_id=team_id,
                    turn=turn,
                    service=service,
                    poster=poster,
                )
        if command.kind is SlackCommandKind.LIST_CHANNEL_FILES:
            with trace_.span("slack.file_service.list_channel"):
                listing = service.list_channel(
                    team_id=team_id, channel_id=turn.channel_id
                )
            return _post_and_return(
                team_id=team_id,
                turn=turn,
                poster=poster,
                blocks=file_list_card(listing),
            )
        if command.kind is SlackCommandKind.CHANGED_SINCE_SYNC:
            with trace_.span("slack.file_service.changed_since_sync"):
                changed = service.changed_since_sync(
                    team_id=team_id, channel_id=turn.channel_id
                )
            return _post_and_return(
                team_id=team_id,
                turn=turn,
                poster=poster,
                blocks=changed_since_sync_card(changed),
            )
        if command.kind is SlackCommandKind.DEDUPE_REPORT:
            with trace_.span("slack.file_service.dedupe_report"):
                mentioned_channel_ids = _mentioned_channel_ids(turn.text)
                if mentioned_channel_ids or _requests_workspace_dedupe(turn.text):
                    dedupe = service.dedupe_saved_files(
                        team_id=team_id,
                        channel_ids=mentioned_channel_ids or None,
                    )
                else:
                    dedupe = service.dedupe_report(
                        team_id=team_id, channel_id=turn.channel_id
                    )
            return _post_and_return(
                team_id=team_id,
                turn=turn,
                poster=poster,
                blocks=dedupe_report_card(dedupe),
            )
    except (SlackFileSyncError, ValueError) as exc:
        return _post_and_return(
            team_id=team_id,
            turn=turn,
            poster=poster,
            blocks=failure_card(
                title="Slack file operation failed",
                detail=str(exc),
                recoverable=True,
                retry_hint="Check workspace configuration and try again.",
            ),
        )
    msg = f"Unhandled Slack command kind: {command.kind}"
    raise ValueError(msg)


def _mentioned_channel_ids(text: str) -> tuple[str, ...]:
    """Return unique Slack channel IDs mentioned in user text, in order."""
    return tuple(dict.fromkeys(_CHANNEL_MENTION_RE.findall(text)))


def _requests_workspace_dedupe(text: str) -> bool:
    """Return whether a dedupe prompt asks beyond the current channel."""
    normalized = " ".join(text.lower().split())
    return any(
        phrase in normalized
        for phrase in (
            "bucket",
            "workspace",
            "all channels",
            "all saved",
            "every channel",
        )
    )


def _run_multi_channel_save(
    *,
    team_id: str,
    turn: NimbusTurnRequest,
    service: SlackFileSyncService,
    channel_ids: tuple[str, ...],
) -> ChatTurnResult:
    """Save files from explicitly mentioned channels and post one summary."""
    reports = tuple(
        service.save_channel(team_id=team_id, channel_id=channel_id)
        for channel_id in channel_ids
    )
    return _blocks_result(turn=turn, blocks=_multi_channel_save_report_card(reports))


def _multi_channel_save_report_card(
    reports: tuple[object, ...],
) -> list[dict[str, object]]:
    """Render a compact multi-channel save summary."""
    if not reports:
        return failure_card(
            title="No channels selected",
            detail="Mention one or more channels for a multi-channel save.",
            recoverable=True,
        )
    scanned = sum(int(getattr(report, "scanned_count", 0)) for report in reports)
    saved = sum(len(getattr(report, "saved_keys", ())) for report in reports)
    skipped = sum(len(getattr(report, "skipped_files", ())) for report in reports)
    failed = sum(len(getattr(report, "failures", ())) for report in reports)
    lines = []
    for report in reports[:_MULTI_CHANNEL_SAVE_PREVIEW_LIMIT]:
        channel_id = str(getattr(report, "channel_id", "unknown"))
        lines.append(
            f"• `{channel_id}`: scanned {getattr(report, 'scanned_count', 0)}, "
            f"saved {len(getattr(report, 'saved_keys', ()))}, "
            f"skipped {len(getattr(report, 'skipped_files', ()))}, "
            f"failed {len(getattr(report, 'failures', ()))}"
        )
    if len(reports) > _MULTI_CHANNEL_SAVE_PREVIEW_LIMIT:
        hidden_count = len(reports) - _MULTI_CHANNEL_SAVE_PREVIEW_LIMIT
        lines.append(f"• and {hidden_count} more channels")
    status = "Saved files from selected channels"
    if failed:
        status = "Saved selected channels with some failures"
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": status, "emoji": False},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Across {len(reports)} channels I scanned {scanned}, saved "
                    f"{saved}, skipped {skipped}, and failed {failed} files."
                ),
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "\n".join(lines)},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "Already-recorded Slack files are skipped. Run "
                        "`find duplicate files in my bucket` to review duplicate "
                        "content across Nimbus-saved manifests."
                    ),
                }
            ],
        },
    ]


def _search_result_blocks(
    *,
    team_id: str,
    query: str,
) -> list[dict[str, object]]:
    """Run a search against the tenant's FileSearchIndexStore and return blocks.

    The search uses a workspace-wide actor scope so channel-level ACLs are
    bypassed — the Slack command surfaces results to anyone who can already
    message the bot.  A production deployment should pass the caller's
    ``user_id`` and ``visible_channel_ids`` for stricter ACL enforcement.
    """
    from datetime import UTC  # noqa: PLC0415
    from datetime import datetime as _dt  # noqa: PLC0415

    from nimbus_runtime.domain import TenantIdentity, VerifiedActor  # noqa: PLC0415
    from nimbus_runtime.search import (  # noqa: PLC0415
        FileSearchIndexStore,
        SearchActorScope,
        SearchQuery,
    )

    from nimbus_slack.runtime import _session_dir  # noqa: PLC0415

    session_dir = _session_dir(team_id)
    tenant = TenantIdentity(platform="slack", workspace_id=team_id)
    actor = VerifiedActor(
        tenant=tenant,
        user_id="slack-bot",
        auth_source="slack_signed_event",
        bridge_id=None,
        verified_at=_dt.now(UTC),
    )
    scope = SearchActorScope(actor=actor, workspace_wide=True)
    search_store = FileSearchIndexStore(session_dir)
    try:
        results = search_store.search(
            scope=scope,
            query=SearchQuery(text=query),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("slack_search_failed", team_id=team_id, error=str(exc))
        return failure_card(
            title="Search failed",
            detail=str(exc),
            recoverable=True,
            retry_hint="Ensure documents have been indexed and try again.",
        )
    return search_results_card(query=query, results=results)


def _workspace_status_blocks(*, team_id: str) -> list[dict[str, object]]:
    """Query tenant stores and return Block Kit blocks for the status card."""
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    from nimbus_runtime.domain import (  # noqa: PLC0415
        ApprovalStatus,
        PlanStatus,
        TaskStatus,
        TenantIdentity,
    )
    from nimbus_runtime.stores import (  # noqa: PLC0415
        FileApprovalStore,
        FilePlanStore,
        FileTaskStore,
    )

    from nimbus_slack.runtime import _session_dir  # noqa: PLC0415

    session_dir = _session_dir(team_id)
    tenant = TenantIdentity(platform="slack", workspace_id=team_id)

    _running = {
        TaskStatus.CREATED,
        TaskStatus.PLANNING,
        TaskStatus.SCANNING,
        TaskStatus.DIFFING,
        TaskStatus.APPLYING,
        TaskStatus.VERIFYING,
    }
    task_store = FileTaskStore(session_dir)
    all_tasks = task_store.list_for_tenant(tenant=tenant, limit=500)
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_cutoff = today_start - timedelta(hours=1)  # small buffer for timezone drift

    tasks_running = sum(1 for t in all_tasks if t.status in _running)
    tasks_awaiting = sum(
        1 for t in all_tasks if t.status is TaskStatus.AWAITING_APPROVAL
    )
    tasks_done_today = sum(
        1
        for t in all_tasks
        if t.status is TaskStatus.DONE and t.updated_at >= today_cutoff
    )
    tasks_failed = sum(1 for t in all_tasks if t.status is TaskStatus.FAILED)

    approval_store = FileApprovalStore(session_dir)
    all_approvals = approval_store.list_for_tenant(tenant=tenant, limit=200)
    pending_approvals = sum(
        1 for a in all_approvals if a.status is ApprovalStatus.PENDING
    )

    plan_store = FilePlanStore(session_dir)
    all_plans = plan_store.list_for_tenant(tenant=tenant, limit=200)
    proposed_plans = sum(1 for p in all_plans if p.status is PlanStatus.PROPOSED)

    return workspace_status_card(
        team_id=team_id,
        tasks_running=tasks_running,
        tasks_awaiting=tasks_awaiting,
        tasks_done_today=tasks_done_today,
        tasks_failed=tasks_failed,
        pending_approvals=pending_approvals,
        proposed_plans=proposed_plans,
    )


_PROGRESS_MIN_INTERVAL_SECONDS = 1.0


def _run_streaming_save(
    *,
    team_id: str,
    turn: NimbusTurnRequest,
    service: SlackFileSyncService,
    poster: SlackPoster | None = None,
) -> ChatTurnResult:
    """Run save_channel and stream progress as Slack message edits.

    Posts a placeholder reply, runs the save loop with a rate-limited
    progress callback that edits the placeholder via ``chat.update``, and
    overwrites it with the final report. Returns an empty-text result so
    the caller's ``_post_result`` step does not double-post.

    Falls back to a single-shot reply if the initial Slack post fails or
    the workspace bot token is unavailable; the file save still runs.
    """
    if poster is None:
        try:
            poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError) as exc:
            log.warning(
                "slack_streaming_poster_unavailable",
                team_id=team_id,
                channel_id=turn.channel_id,
                error=str(exc),
            )
            report = service.save_channel(team_id=team_id, channel_id=turn.channel_id)
            return _blocks_result(turn=turn, blocks=save_report_card(report))

    initial_blocks = design.thinking_card(
        "Saving Slack files to S3…",
        steps=["Scan", "Upload", "Verify"],
    )
    initial_text = "Saving Slack files to S3…"
    ts: str | None
    try:
        ts = _post_progress_placeholder_blocks(
            poster=poster,
            channel_id=turn.channel_id,
            thread_ts=turn.thread_id,
            blocks=initial_blocks,
            fallback_text=initial_text,
        )
    except SlackApiError as exc:
        log.warning(
            "slack_streaming_initial_post_failed",
            team_id=team_id,
            channel_id=turn.channel_id,
            slack_error=_slack_error(exc),
        )
        ts = None
    if ts is None:
        report = service.save_channel(team_id=team_id, channel_id=turn.channel_id)
        return _blocks_result(turn=turn, blocks=save_report_card(report))
    placeholder_ts: str = ts

    last_update = [time.monotonic()]

    def on_progress(progress: SaveProgress) -> None:
        now = time.monotonic()
        if now - last_update[0] < _PROGRESS_MIN_INTERVAL_SECONDS:
            return
        last_update[0] = now
        blocks = save_progress_card(progress)
        text = _format_progress(progress)
        try:
            try:
                poster.update_blocks(turn.channel_id, placeholder_ts, blocks, text)
            except AttributeError:
                poster.update_message(turn.channel_id, placeholder_ts, text)
        except SlackApiError as exc:
            log.debug(
                "slack_streaming_progress_update_failed",
                team_id=team_id,
                channel_id=turn.channel_id,
                slack_error=_slack_error(exc),
            )

    try:
        report = service.save_channel(
            team_id=team_id,
            channel_id=turn.channel_id,
            on_progress=on_progress,
        )
    except SlackFileSyncError as exc:
        fail_blocks = failure_card(
            title="Save failed",
            detail=str(exc),
            recoverable=True,
        )
        _safe_update_blocks(
            poster=poster,
            channel_id=turn.channel_id,
            ts=placeholder_ts,
            blocks=fail_blocks,
            fallback_text=f"Save failed: {exc}",
        )
        runtime_telemetry.record_slack_reply(
            result="failure",
            reason="save_failed",
        )
        return _command_result(turn=turn, text="")

    final_blocks = save_report_card(report)
    final_text = blocks_to_fallback_text(final_blocks)
    _safe_update_blocks(
        poster=poster,
        channel_id=turn.channel_id,
        ts=ts,
        blocks=final_blocks,
        fallback_text=final_text,
    )
    runtime_telemetry.record_slack_reply(result="success", reason="streaming")
    return _command_result(turn=turn, text="")


def _format_progress(progress: SaveProgress) -> str:
    """Format a SaveProgress snapshot for a Slack progress message."""
    processed = progress.saved + progress.skipped + progress.failed
    summary = (
        f"Saving Slack files… {processed}/{progress.total} processed "
        f"(saved {progress.saved}, skipped {progress.skipped}, "
        f"failed {progress.failed})."
    )
    if progress.current_file is not None:
        summary += f" Last: `{progress.current_file.name}`."
    return summary


def _post_progress_placeholder(
    *,
    poster: SlackPoster,
    channel_id: str,
    thread_ts: str | None,
    text: str,
) -> str | None:
    """Post the initial placeholder message and return its ts (or None)."""
    response = poster.send_message(channel_id, text, thread_ts=thread_ts)
    return _extract_ts(response)


def _post_progress_placeholder_blocks(
    *,
    poster: SlackPoster,
    channel_id: str,
    thread_ts: str | None,
    blocks: list[dict[str, object]],
    fallback_text: str,
) -> str | None:
    """Post the initial Block Kit placeholder and return its ts (or None)."""
    try:
        response = poster.send_blocks(
            channel_id, blocks, fallback_text, thread_ts=thread_ts
        )
    except AttributeError:
        # Poster does not support blocks — fall back to plain text
        response = poster.send_message(channel_id, fallback_text, thread_ts=thread_ts)
    return _extract_ts(response)


def _safe_update(
    *,
    poster: SlackPoster,
    channel_id: str,
    ts: str,
    text: str,
) -> None:
    """Update a Slack message and log on failure without raising."""
    try:
        poster.update_message(channel_id, ts, text)
    except SlackApiError as exc:
        log.warning(
            "slack_streaming_final_update_failed",
            channel_id=channel_id,
            slack_error=_slack_error(exc),
        )


def _safe_update_blocks(
    *,
    poster: SlackPoster,
    channel_id: str,
    ts: str,
    blocks: list[dict[str, object]],
    fallback_text: str,
) -> None:
    """Update a Slack message with Block Kit blocks, logging on failure."""
    try:
        try:
            poster.update_blocks(channel_id, ts, blocks, fallback_text)
        except AttributeError:
            poster.update_message(channel_id, ts, fallback_text)
    except SlackApiError as exc:
        log.warning(
            "slack_streaming_final_update_failed",
            channel_id=channel_id,
            slack_error=_slack_error(exc),
        )


def _extract_ts(response: object) -> str | None:
    """Return the ts field from a Slack SDK response payload."""
    if isinstance(response, dict):
        ts = response.get("ts")
        return ts if isinstance(ts, str) else None
    getter = getattr(response, "get", None)
    if callable(getter):
        ts = getter("ts")
        return ts if isinstance(ts, str) else None
    return None


def _slack_error(exc: SlackApiError) -> str:
    """Extract the Slack-side error code from a SlackApiError."""
    if exc.response is None:
        return "unknown_error"
    error = exc.response.get("error")
    return error if isinstance(error, str) else "unknown_error"


# ── Interactive (button click) handling ────────────────────────────────────


# Map command-trigger button action_ids onto the canonical text intent the
# command parser already recognises. Keeping a tiny lookup table here means
# the routing stays auditable without ever invoking the LLM.
_BUTTON_TO_TEXT: dict[str, str] = {
    "cmd:save_channel_files": "save all the files in this channel",
    "cmd:diff_channel_files": "what files in this channel are not saved in my s3 bucket?",  # noqa: E501
    "cmd:list_channel_files": "what files are in this channel?",
    "cmd:changed_since_sync": "which files changed since the last sync?",
    "cmd:dedupe_report": "find duplicate files",
    "cmd:retry_save": "save all the files in this channel",
}


def handle_slack_interaction(  # noqa: PLR0911
    *,
    team_id: str,
    payload: dict[str, object],
    poster: SlackPoster | None = None,
    file_service: SlackFileSyncService | None = None,
) -> ChatTurnResult | None:
    """Handle one Slack ``block_actions`` interactive payload.

    Translates a button click into the corresponding text intent and reuses
    the same command dispatch path as text mentions. Returns the produced
    ``ChatTurnResult`` for testing, or ``None`` when the action is a
    no-op acknowledgement (link buttons, approval stubs, unknown ids).
    """
    actions = payload.get("actions") or []
    if not isinstance(actions, list) or not actions:
        return None
    action = actions[0]
    if not isinstance(action, dict):
        return None

    action_id_raw = action.get("action_id")
    action_id = action_id_raw if isinstance(action_id_raw, str) else ""

    # 1. Approval responses on destructive actions.
    if action_id.startswith(("approve:", "reject:")):
        log.info(
            "slack_interactive_approval_action",
            team_id=team_id,
            action_id=action_id,
        )
        _handle_approval_interaction(
            team_id=team_id,
            action_id=action_id,
            payload=payload,
            poster=poster,
        )
        return None

    # 2. Link-style buttons — Slack opens the URL client-side; nothing to do.
    if action_id in {"open_setup", "open_docs", "open_link"}:
        return None

    # 3. Command-trigger buttons re-issue the matching adapter command.
    text = _BUTTON_TO_TEXT.get(action_id)
    if text is None:
        log.warning(
            "slack_interactive_unknown_action",
            team_id=team_id,
            action_id=action_id,
        )
        return None

    turn = _turn_from_interactive(payload=payload, team_id=team_id, text=text)
    if turn is None:
        return None

    command = parse_slack_command(turn.text)
    result = _handle_adapter_command(
        command=command,
        team_id=team_id,
        turn=turn,
        file_service=file_service,
        poster=poster,
    )
    runtime_telemetry.record_slack_turn(
        kind=command.kind.value,
        outcome=result.outcome,
    )
    _post_result(team_id=team_id, turn=turn, result=result, poster=poster)
    return result


def _turn_from_interactive(  # noqa: C901
    *,
    payload: dict[str, object],
    team_id: str,
    text: str,
) -> NimbusTurnRequest | None:
    """Synthesise a NimbusTurnRequest from a block_actions payload.

    Returns None if the payload lacks the channel / user fields the rest of
    the adapter needs to act on the button click.
    """
    channel = payload.get("channel") or {}
    channel_id = channel.get("id") if isinstance(channel, dict) else None
    user = payload.get("user") or {}
    user_id = user.get("id") if isinstance(user, dict) else None
    if not isinstance(channel_id, str) or not channel_id:
        return None
    if not isinstance(user_id, str) or not user_id:
        return None

    container = payload.get("container") or {}
    message_ts = ""
    thread_ts = None
    if isinstance(container, dict):
        raw_message_ts = container.get("message_ts")
        if isinstance(raw_message_ts, str):
            message_ts = raw_message_ts
        raw_thread_ts = container.get("thread_ts")
        if isinstance(raw_thread_ts, str):
            thread_ts = raw_thread_ts

    if not message_ts:
        message = payload.get("message")
        if isinstance(message, dict):
            ts = message.get("ts")
            if isinstance(ts, str):
                message_ts = ts

    if not message_ts:
        # Use action_ts as a stable fallback id when Slack omits the message
        # context (rare but possible for some block payloads).
        actions = payload.get("actions") or []
        if isinstance(actions, list) and actions and isinstance(actions[0], dict):
            ats = actions[0].get("action_ts")
            if isinstance(ats, str):
                message_ts = ats

    if not message_ts:
        return None

    return NimbusTurnRequest(
        platform="slack",
        workspace_id=team_id,
        channel_id=channel_id,
        thread_id=thread_ts or message_ts,
        message_id=message_ts,
        user_id=user_id,
        text=text,
        idempotency_key=f"slack:{team_id}:interactive:{message_ts}:{_first_action_id(payload)}",
        request_id=f"slack-interactive-{message_ts}",
    )


def _first_action_id(payload: dict[str, object]) -> str:
    """Return the first action_id from a block_actions payload, or '?'."""
    actions = payload.get("actions") or []
    if isinstance(actions, list) and actions and isinstance(actions[0], dict):
        action_id = actions[0].get("action_id")
        if isinstance(action_id, str):
            return action_id
    return "?"


def _handle_approval_interaction(  # noqa: C901, PLR0911, PLR0912, PLR0915
    *,
    team_id: str,
    action_id: str,
    payload: dict[str, object],
    poster: SlackPoster | None,
) -> None:
    """Record an approval decision, execute the operation, and update the card.

    Parses ``approve:{id}`` or ``reject:{id}`` button clicks.

    **Approve path**: routes through ``run_tenant_runtime_turn`` with a
    synthetic ``"yes"`` turn so the runtime atomically decides the approval
    *and* executes the storage operation (delete / copy / move / write).  The
    original card is updated to show who approved and when; the execution
    result is posted as a follow-up thread message.

    **Reject path**: records the rejection, transitions the pending action to
    expired (so it cannot be confirmed by a subsequent text message), and
    updates the card.

    Gracefully no-ops when the approval store is not accessible (remote mode)
    or when the approval is already decided / not found.
    """
    import datetime  # noqa: PLC0415

    from nimbus_runtime.domain import (  # noqa: PLC0415
        ActionStatus,
        ActionTransition,
        ApprovalChoice,
        TenantIdentity,
        VerifiedActor,
    )
    from nimbus_runtime.stores import (  # noqa: PLC0415
        FileActionStore,
        FileApprovalStore,
    )

    from nimbus_slack.runtime import _session_dir  # noqa: PLC0415

    # Parse choice and the raw action_id stored on the approval record.
    if action_id.startswith("approve:"):
        choice = ApprovalChoice.APPROVE
        approval_action_id = action_id[len("approve:") :]
    else:
        choice = ApprovalChoice.REJECT
        approval_action_id = action_id[len("reject:") :]

    # Extract acting user and channel/message context from the payload.
    user = payload.get("user") or {}
    user_id = user.get("id") if isinstance(user, dict) else None
    channel = payload.get("channel") or {}
    channel_id = channel.get("id") if isinstance(channel, dict) else None
    container = payload.get("container") or {}
    message_ts = ""
    if isinstance(container, dict):
        raw_ts = container.get("message_ts")
        if isinstance(raw_ts, str):
            message_ts = raw_ts

    if not isinstance(user_id, str) or not user_id:
        log.warning(
            "slack_approval_missing_user",
            team_id=team_id,
            action_id=action_id,
        )
        return

    # Build domain identity objects.
    tenant = TenantIdentity(platform="slack", workspace_id=team_id)
    now = datetime.datetime.now(datetime.UTC)
    actor = VerifiedActor(
        tenant=tenant,
        user_id=user_id,
        auth_source="slack_signed_event",
        bridge_id=None,
        verified_at=now,
    )

    # Access the tenant-local approval store (unavailable in remote mode).
    try:
        session_dir = _session_dir(team_id)
        approval_store = FileApprovalStore(session_dir)
    except Exception:  # noqa: BLE001
        log.warning(
            "slack_approval_store_unavailable",
            team_id=team_id,
            action_id=action_id,
        )
        return

    # Find the pending approval for this action.
    approval = approval_store.find_pending_for_action(
        tenant=tenant,
        action_id=approval_action_id,
    )
    if approval is None:
        # The Approval record lives in the runtime that processed the original
        # destructive turn. In ``remote`` (and ``auto`` without BYOK) mode that
        # is the remote ai_server, not the local Slack session dir. Forward the
        # click to the remote runtime instead of silently no-oping so the user
        # actually gets a response.
        if slack_model_mode() != NIMBUS_SLACK_MODEL_MODE_TENANT_LOCAL:
            log.info(
                "slack_approval_remote_fallback",
                team_id=team_id,
                action_id=approval_action_id,
                choice=choice.value,
            )
            _handle_remote_approval_interaction(
                team_id=team_id,
                choice=choice,
                approval_action_id=approval_action_id,
                payload=payload,
                user_id=user_id,
                channel_id=channel_id,
                message_ts=message_ts,
                now=now,
                poster=poster,
            )
            return
        log.warning(
            "slack_approval_not_found",
            team_id=team_id,
            action_id=approval_action_id,
        )
        return

    # --- APPROVE: route through the tenant runtime for atomic decide + execute ---
    if choice is ApprovalChoice.APPROVE:
        _handle_approve_button(
            team_id=team_id,
            approval=approval,
            user_id=user_id,
            channel_id=channel_id,
            message_ts=message_ts,
            actor_now=now,
            poster=poster,
        )
        return

    # --- REJECT: record the decision and expire the action ---
    decision = approval_store.decide(
        tenant=tenant,
        approval_id=approval.approval_id,
        actor=actor,
        choice=ApprovalChoice.REJECT,
        exact_target=approval.exact_target,
        now=now,
    )
    log.info(
        "slack_approval_decision_recorded",
        team_id=team_id,
        approval_id=approval.approval_id,
        choice="reject",
        accepted=decision.accepted,
        reason=decision.reason,
    )

    # Expire the pending action so it cannot be confirmed by typing "yes".
    action_expiry_failed = False
    if decision.accepted and approval.action_id is not None:
        try:
            action_store = FileActionStore(session_dir)
            action_store.transition(
                tenant=tenant,
                action_id=approval.action_id,
                transition=ActionTransition(
                    expected=ActionStatus.AWAITING_CONFIRMATION,
                    next_status=ActionStatus.EXPIRED,
                    event_type="action_rejected",
                    event_payload={
                        "reason": "rejected_via_button",
                        "rejected_by": user_id,
                    },
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # Log the exception but don't let it break the rejection flow.
            # If the transition fails (e.g. race with worker), the action may
            # still execute; surface a warning so the user knows.
            log.warning(
                "slack_reject_action_transition_failed",
                team_id=team_id,
                action_id=approval.action_id,
                error=str(exc),
                exc_info=True,
            )
            action_expiry_failed = True
            # Update the card to warn the user that the action might still run.
            if channel_id and message_ts:
                try:
                    warning_poster: SlackPoster | None = poster
                    if warning_poster is None:
                        warning_poster = get_slack_poster(team_id=team_id)
                    if warning_poster is not None:
                        warn_blocks = design.warning_card(
                            title="Rejected, but action may still be running",
                            detail=(
                                "The reject decision was recorded, but we could not "
                                "expire the pending action. The action may still be "
                                "executing; check artifacts for confirmation."
                            ),
                            retry_hint="Check task status or view artifacts.",
                        )
                        warning_poster.update_blocks(
                            channel_id, message_ts, warn_blocks, "Rejected with warning"
                        )
                except (
                    SecretCodecError,
                    SlackApiError,
                    SlackStoreError,
                    AttributeError,
                    ValueError,
                ) as warn_exc:
                    log.warning(
                        "slack_reject_warning_update_failed",
                        team_id=team_id,
                        action_id=approval.action_id,
                        error=str(warn_exc),
                        exc_info=True,
                    )

    if action_expiry_failed:
        return

    if not decision.accepted:
        return

    # Update the original Slack message to reflect the rejection.
    if not channel_id or not message_ts:
        return

    resolved_poster: SlackPoster | None = poster
    if resolved_poster is None:
        try:
            resolved_poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError):
            return

    if resolved_poster is None:
        return

    decided_blocks = _approval_decided_blocks(
        approval=approval,
        choice=ApprovalChoice.REJECT,
        user_id=user_id,
        accepted=True,
        reason="rejected",
        decided_at=now,
    )
    fallback = f"Rejected by <@{user_id}>"
    try:
        resolved_poster.update_blocks(
            channel_id,
            message_ts,
            decided_blocks,
            fallback,
        )
    except (SlackApiError, AttributeError) as exc:
        log.warning(
            "slack_approval_update_message_failed",
            team_id=team_id,
            error=str(exc),
        )
        with contextlib.suppress(SlackApiError, AttributeError):
            resolved_poster.update_message(channel_id, message_ts, fallback)


def _handle_remote_approval_interaction(  # noqa: PLR0913
    *,
    team_id: str,
    choice: object,
    approval_action_id: str,
    payload: dict[str, object],
    user_id: str,
    channel_id: str | None,
    message_ts: str,
    now: object,
    poster: SlackPoster | None,
) -> None:
    """Handle an approval click whose Approval record lives in the remote runtime.

    Slack's local FileApprovalStore is empty when ``NIMBUS_SLACK_MODEL_MODE``
    routes turns to the remote ai_server. In that mode the destructive Action +
    Approval pair was written to the remote runtime's store, not the local
    session dir, so the existing local-lookup path silently fails. This handler
    is the remote counterpart:

    * **Approve** — synthesize a ``text="yes"`` turn whose ``thread_id``
      matches the original turn's ``conversation_id`` (recovered from the
      Slack button's ``container.thread_ts`` or ``message_ts``) and POST it via
      ``call_nimbus``. The remote runtime finds the pending action by session
      and executes it atomically. The Slack card is updated to the decided
      state and the runtime's reply is posted as a thread follow-up.
    * **Reject** — there is no remote reject endpoint today, so the card is
      updated with explicit instructions ("type ``no, <target>`` to cancel, or
      ignore — the action expires automatically"). The action's ``expires_at``
      timer in the remote runtime is the safety net.
    """
    from nimbus_runtime.domain import ApprovalChoice  # noqa: PLC0415

    container = payload.get("container") or {}
    thread_ts: str | None = None
    if isinstance(container, dict):
        raw_thread_ts = container.get("thread_ts")
        if isinstance(raw_thread_ts, str) and raw_thread_ts:
            thread_ts = raw_thread_ts
    session_id = thread_ts or message_ts

    if not channel_id or not session_id:
        log.warning(
            "slack_remote_approval_missing_context",
            team_id=team_id,
            action_id=approval_action_id,
        )
        return

    if choice is ApprovalChoice.APPROVE:
        _handle_remote_approve(
            team_id=team_id,
            approval_action_id=approval_action_id,
            channel_id=channel_id,
            message_ts=message_ts,
            session_id=session_id,
            user_id=user_id,
            now=now,
            poster=poster,
        )
        return

    _handle_remote_reject(
        team_id=team_id,
        approval_action_id=approval_action_id,
        channel_id=channel_id,
        message_ts=message_ts,
        user_id=user_id,
        now=now,
        poster=poster,
    )


def _handle_remote_approve(  # noqa: PLR0913
    *,
    team_id: str,
    approval_action_id: str,
    channel_id: str,
    message_ts: str,
    session_id: str,
    user_id: str,
    now: object,
    poster: SlackPoster | None,
) -> None:
    """POST a synthetic ``yes`` turn to the remote runtime and update the card."""
    from nimbus_runtime.domain import ApprovalChoice  # noqa: PLC0415

    synthetic_turn = NimbusTurnRequest(
        platform="slack",
        workspace_id=team_id,
        channel_id=channel_id,
        thread_id=session_id,
        message_id=message_ts,
        user_id=user_id,
        text="yes",
        idempotency_key=(f"slack:{team_id}:remote-button-approve:{approval_action_id}"),
        request_id=f"remote-button-approve-{approval_action_id}",
    )

    try:
        result = call_nimbus(synthetic_turn)
    except (httpx.HTTPError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(
            "slack_remote_approval_call_failed",
            team_id=team_id,
            action_id=approval_action_id,
            error=str(exc),
        )
        _post_remote_approval_error(
            team_id=team_id,
            channel_id=channel_id,
            message_ts=message_ts,
            detail=(
                "I could not reach the Nimbus AI runtime to execute the "
                f"approval: {exc}"
            ),
            poster=poster,
        )
        return

    # Only flip the card to "Approved" when the runtime actually executed
    # the action. A 200 OK alone is not proof: if the synthetic ``yes`` reaches
    # the runtime but no pending action matches (e.g. action expired before
    # the click, wrong session, already confirmed via text), the runtime
    # returns ``outcome=error`` or ``outcome=reply`` with no executed action.
    executed = result.outcome != "error" and any(
        summary.status == "succeeded" for summary in result.actions
    )

    log.info(
        "slack_remote_approval_executed",
        team_id=team_id,
        action_id=approval_action_id,
        outcome=result.outcome,
        executed=executed,
    )

    if not executed:
        _post_remote_approval_error(
            team_id=team_id,
            channel_id=channel_id,
            message_ts=message_ts,
            detail=(
                result.text.strip()
                or "Nimbus did not execute the approved action. "
                "The action may have expired or already been confirmed."
            ),
            poster=poster,
        )
        return

    resolved_poster: SlackPoster | None = poster
    if resolved_poster is None:
        try:
            resolved_poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError):
            return
    if resolved_poster is None:
        return

    # Update the original card to "Approved" — we don't have the local
    # Approval object, so let _approval_decided_blocks degrade to "—" for
    # target/risk; the channel still shows who approved and when.
    decided_blocks = _approval_decided_blocks(
        approval=None,
        choice=ApprovalChoice.APPROVE,
        user_id=user_id,
        accepted=True,
        reason="approved",
        decided_at=now,
    )
    fallback_approved = f"Approved by <@{user_id}>"
    try:
        resolved_poster.update_blocks(
            channel_id, message_ts, decided_blocks, fallback_approved
        )
    except (SlackApiError, AttributeError) as exc:
        log.warning(
            "slack_remote_approval_update_message_failed",
            team_id=team_id,
            error=str(exc),
        )
        with contextlib.suppress(SlackApiError, AttributeError):
            resolved_poster.update_message(channel_id, message_ts, fallback_approved)

    # Post the execution result as a thread follow-up so the user sees the
    # actual outcome (e.g. "Deleted both files.") instead of just "Approved".
    if result.text.strip():
        with contextlib.suppress(SlackApiError, AttributeError):
            resolved_poster.send_message(channel_id, result.text, thread_ts=session_id)
            runtime_telemetry.record_slack_reply(
                result="success", reason="remote_approval_executed"
            )


def _handle_remote_reject(  # noqa: PLR0913
    *,
    team_id: str,
    approval_action_id: str,
    channel_id: str,
    message_ts: str,
    user_id: str,
    now: object,
    poster: SlackPoster | None,
) -> None:
    """Update the card to explain how to cancel the remote action."""
    from nimbus_runtime.domain import ApprovalChoice  # noqa: PLC0415

    resolved_poster: SlackPoster | None = poster
    if resolved_poster is None:
        try:
            resolved_poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError):
            return
    if resolved_poster is None:
        return

    decided_blocks = _approval_decided_blocks(
        approval=None,
        choice=ApprovalChoice.REJECT,
        user_id=user_id,
        accepted=True,
        reason="rejected",
        decided_at=now,
    )
    decided_blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "Reject via button is not yet wired to the remote "
                        "runtime. The action will expire automatically. "
                        "Type `no` in this thread to cancel sooner."
                    ),
                }
            ],
        }
    )
    fallback = f"Rejected by <@{user_id}>"
    try:
        resolved_poster.update_blocks(channel_id, message_ts, decided_blocks, fallback)
    except (SlackApiError, AttributeError) as exc:
        log.warning(
            "slack_remote_approval_update_message_failed",
            team_id=team_id,
            error=str(exc),
        )
        with contextlib.suppress(SlackApiError, AttributeError):
            resolved_poster.update_message(channel_id, message_ts, fallback)
    log.info(
        "slack_remote_approval_reject_acknowledged",
        team_id=team_id,
        action_id=approval_action_id,
    )


def _post_remote_approval_error(
    *,
    team_id: str,
    channel_id: str,
    message_ts: str,
    detail: str,
    poster: SlackPoster | None,
) -> None:
    """Replace the approval card with a failure card when the remote call fails."""
    resolved_poster: SlackPoster | None = poster
    if resolved_poster is None:
        try:
            resolved_poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError):
            return
    if resolved_poster is None:
        return
    err_blocks = design.error_card(
        title="Approval execution failed",
        detail=detail,
        retry_hint="Try clicking Approve again, or run the command in the CLI.",
    )
    fallback = "Approval execution failed"
    with contextlib.suppress(SlackApiError, AttributeError):
        resolved_poster.update_blocks(channel_id, message_ts, err_blocks, fallback)


def _handle_approve_button(  # noqa: C901, PLR0912, PLR0913, PLR0915
    *,
    team_id: str,
    approval: object,
    user_id: str,
    channel_id: str | None,
    message_ts: str,
    actor_now: object,
    poster: SlackPoster | None,
) -> None:
    """Execute an approved action via the tenant runtime and update the card.

    Builds a synthetic ``"yes"`` turn whose ``conversation_id`` matches the
    approval's session, then calls ``run_tenant_runtime_turn``.  The runtime
    atomically records the approval decision and executes the storage
    operation (delete / copy / move / write) without a second round-trip.

    Falls back to showing only the "Approved" card when the tenant runtime
    is unavailable (e.g. workspace not in tenant-local mode).
    """
    import datetime as _dt  # noqa: PLC0415

    from nimbus_runtime.domain import ApprovalChoice  # noqa: PLC0415

    # Duck-typed access so tests can pass lightweight fake approval objects.
    approval_id = getattr(approval, "approval_id", None)
    session_id = getattr(approval, "session_id", None)
    if not isinstance(approval_id, str) or not isinstance(session_id, str):
        log.warning(
            "slack_approval_button_missing_fields",
            team_id=team_id,
            approval_id=str(approval_id),
        )
        return

    now = (
        actor_now if isinstance(actor_now, _dt.datetime) else _dt.datetime.now(_dt.UTC)
    )
    eff_channel = channel_id or ""
    eff_message_ts = message_ts or session_id

    # Synthetic turn: thread_id = approval.session_id so conversation_id
    # matches the action's session_id, letting the runtime find the pending
    # action and execute it atomically.  Bare "yes" is accepted by the
    # runtime's _BARE_YES_RE for all action kinds.
    synthetic_turn = NimbusTurnRequest(
        platform="slack",
        workspace_id=team_id,
        channel_id=eff_channel,
        thread_id=session_id,
        message_id=eff_message_ts,
        user_id=user_id,
        text="yes",
        idempotency_key=f"slack:{team_id}:button-approve:{approval_id}",
        request_id=f"button-approve-{approval_id}",
    )

    result: ChatTurnResult | None = None
    try:
        result = run_tenant_runtime_turn(
            team_id=team_id,
            turn=synthetic_turn,
            store=get_slack_store(),
        )
        log.info(
            "slack_approval_executed",
            team_id=team_id,
            approval_id=approval_id,
            outcome=result.outcome,
        )
    except (SlackTenantConfigMissingError, SlackTenantRuntimeError) as exc:
        log.warning(
            "slack_approval_runtime_unavailable",
            team_id=team_id,
            approval_id=approval_id,
            error=str(exc),
        )
    except Exception as exc:
        log.exception(
            "slack_approval_execution_failed",
            team_id=team_id,
            approval_id=approval_id,
            error=str(exc),
        )
        # Show an error card so users know the operation did not execute.
        if eff_channel and message_ts:
            _resolved: SlackPoster | None = poster
            if _resolved is None:
                with contextlib.suppress(Exception):
                    _resolved = get_slack_poster(team_id=team_id)
            if _resolved is not None:
                err_blocks = design.error_card(
                    title="Approval execution failed",
                    detail=f"The runtime could not execute the approved action: {exc}",
                    retry_hint="Try approving again or check workspace configuration.",
                )
                err_fallback = "Approval execution failed"
                with contextlib.suppress(SlackApiError, AttributeError):
                    _resolved.update_blocks(
                        eff_channel, message_ts, err_blocks, err_fallback
                    )
        return

    # Only flip the card to "Approved" when the runtime actually executed
    # the action. ``result is None`` means the tenant runtime was missing
    # (BYOK not configured) — flipping to Approved there would lie about
    # what happened. ``outcome=error`` or no succeeded action means the
    # synthetic ``yes`` reached the runtime but no pending action matched
    # (timer expired, already confirmed via text, wrong session).
    executed = (
        result is not None
        and result.outcome != "error"
        and any(summary.status == "succeeded" for summary in result.actions)
    )

    if not executed and eff_channel and message_ts:
        detail = (
            result.text.strip()
            if result is not None and result.text.strip()
            else (
                "Nimbus could not execute the approved action. The tenant "
                "runtime is unavailable or the action expired before the "
                "click was processed."
            )
        )
        _post_remote_approval_error(
            team_id=team_id,
            channel_id=eff_channel,
            message_ts=message_ts,
            detail=detail,
            poster=poster,
        )
        return

    # Resolve the poster for card update and result post.
    resolved_poster: SlackPoster | None = poster
    if resolved_poster is None:
        try:
            resolved_poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError):
            return
    if resolved_poster is None:
        return

    # Update the original card to show the approved state.
    decided_blocks = _approval_decided_blocks(
        approval=approval,
        choice=ApprovalChoice.APPROVE,
        user_id=user_id,
        accepted=True,
        reason="approved",
        decided_at=now,
    )
    fallback_approved = f"Approved by <@{user_id}>"
    if eff_channel and message_ts:
        try:
            resolved_poster.update_blocks(
                eff_channel, message_ts, decided_blocks, fallback_approved
            )
        except (SlackApiError, AttributeError) as exc:
            log.warning(
                "slack_approval_update_message_failed",
                team_id=team_id,
                error=str(exc),
            )
            with contextlib.suppress(SlackApiError, AttributeError):
                resolved_poster.update_message(
                    eff_channel, message_ts, fallback_approved
                )

    # Post the execution result as a follow-up thread message.
    if result is not None and result.text.strip() and eff_channel:
        try:
            resolved_poster.send_message(
                eff_channel,
                result.text,
                thread_ts=session_id,
            )
            runtime_telemetry.record_slack_reply(
                result="success", reason="approval_executed"
            )
        except (SlackApiError, AttributeError) as exc:
            log.warning(
                "slack_approval_result_post_failed",
                team_id=team_id,
                approval_id=approval_id,
                error=str(exc),
            )


def _approval_decided_blocks(  # noqa: PLR0913
    *,
    approval: object,
    choice: object,
    user_id: str,
    accepted: bool,
    reason: str,  # noqa: ARG001 — kept for API stability; not shown in card
    decided_at: object = None,
) -> list[dict[str, object]]:
    """Build Block Kit blocks for an already-decided approval card.

    Replaces the action buttons with a compact resolved-status block that
    shows who decided, when (HH:MM UTC), and what happens next — matching the
    spec:

        Approved by @alice  •  14:33 UTC
        Task will apply in the next worker cycle.

    or

        Rejected by @alice  •  14:33 UTC
        The operation was stopped. No files were deleted.
    """
    import datetime as _dt  # noqa: PLC0415

    from nimbus_runtime.domain import ApprovalChoice  # noqa: PLC0415

    is_approve = choice is ApprovalChoice.APPROVE and accepted
    decision_label = "Approved" if is_approve else "Rejected"

    # Narrow the type for attribute access (approval is typed as object in
    # the signature to avoid a top-level import).
    from nimbus_runtime.domain import Approval as _Approval  # noqa: PLC0415

    appr = approval if isinstance(approval, _Approval) else None
    target_text = appr.exact_target if appr else "—"
    risk_text = appr.risk_level.replace("_", " ").title() if appr else "—"

    # Format timestamp as "HH:MM UTC" when available.
    time_suffix = ""
    if isinstance(decided_at, _dt.datetime):
        utc_dt = (
            decided_at.astimezone(_dt.UTC)
            if decided_at.tzinfo is not None
            else decided_at
        )
        time_suffix = f"  •  {utc_dt.strftime('%H:%M')} UTC"

    # Consequence sentence: tell users what happens next or what was stopped.
    consequence = (
        "Task will apply in the next worker cycle."
        if is_approve
        else "The operation was stopped. No files were deleted."
    )

    return [
        design.branded_header(
            f"Approval {decision_label}", status="ok" if is_approve else "error"
        ),
        design.section(f"*Target:* `{target_text}`"),
        design.fields(
            f"*Risk*\n{risk_text}",
            f"*Decision*\n{decision_label}",
        ),
        design.context(f"{decision_label} by <@{user_id}>{time_suffix}"),
        design.section(consequence),
    ]


def _post_confirmation_card(
    *,
    team_id: str,
    turn: NimbusTurnRequest,
    confirmation: ConfirmationDetails,
    fallback_text: str,
    poster: SlackPoster | None,
) -> None:
    """Post a Block Kit approval card for a mutation awaiting confirmation.

    Uses the ``approval_request_card`` renderer (Approve / Reject buttons)
    so the user can respond with one click instead of typing the full phrase.
    Falls back to plain text if blocks cannot be sent.
    """
    expected = confirmation.expected_reply
    target_display = (
        expected[5:].strip() if expected.lower().startswith("yes, ") else expected
    )
    consequence = _CONFIRMATION_CONSEQUENCES.get(
        confirmation.kind,
        "This action cannot be undone unless a restore plan is available.",
    )
    risk_level = "critical" if confirmation.kind == "delete_file" else "destructive"

    blocks = approval_request_card(
        action_id=confirmation.action_id,
        target_display=target_display,
        size_display=None,
        sha256=None,
        requested_by=turn.user_id,
        expires_at=confirmation.expires_at,
        risk_level=risk_level,
        consequence=consequence,
    )
    fallback = fallback_text or target_display

    resolved_poster: SlackPoster | None = poster
    if resolved_poster is None:
        try:
            resolved_poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError) as exc:
            log.warning(
                "slack_confirmation_poster_unavailable",
                team_id=team_id,
                error=str(exc),
            )
            return

    try:
        try:
            resolved_poster.send_blocks(
                turn.channel_id, blocks, fallback, thread_ts=turn.thread_id
            )
        except AttributeError:
            resolved_poster.send_message(
                turn.channel_id, fallback, thread_ts=turn.thread_id
            )
        runtime_telemetry.record_slack_reply(
            result="success", reason="confirmation_card"
        )
    except SlackApiError as exc:
        error = exc.response.get("error") if exc.response else "unknown_error"
        runtime_telemetry.record_slack_reply(result="failure", reason=str(error))
        log.warning(
            "slack_confirmation_card_post_failed",
            team_id=team_id,
            channel_id=turn.channel_id,
            action_id=confirmation.action_id,
            slack_error=error,
        )
        with contextlib.suppress(SlackApiError, AttributeError):
            resolved_poster.send_message(
                turn.channel_id, fallback, thread_ts=turn.thread_id
            )


def _post_result(
    *,
    team_id: str,
    turn: NimbusTurnRequest,
    result: ChatTurnResult,
    poster: SlackPoster | None,
) -> None:
    """Post a non-empty result back to Slack with operator-visible failures."""
    if result.confirmation_required and result.confirmation is not None:
        _post_confirmation_card(
            team_id=team_id,
            turn=turn,
            confirmation=result.confirmation,
            fallback_text=result.text,
            poster=poster,
        )
        return
    if not result.text.strip():
        log.warning(
            "slack_empty_result_suppressed",
            team_id=team_id,
            channel_id=turn.channel_id,
            request_id=result.request_id,
            outcome=result.outcome,
            model=result.model,
        )
        runtime_telemetry.record_slack_reply(result="skipped", reason="empty_text")
        return
    resolved_poster = poster or get_slack_poster(team_id=team_id)
    try:
        resolved_poster.send_message(
            turn.channel_id,
            result.text,
            thread_ts=turn.thread_id,
        )
        runtime_telemetry.record_slack_reply(result="success", reason="posted")
    except SlackApiError as exc:
        error = exc.response.get("error") if exc.response else "unknown_error"
        runtime_telemetry.record_slack_reply(result="failure", reason=str(error))
        log.exception(
            "slack_reply_post_failed",
            team_id=team_id,
            channel_id=turn.channel_id,
            thread_ts=turn.thread_id,
            request_id=result.request_id,
            slack_error=error,
        )
        msg = f"Slack reply post failed: {error}"
        raise RuntimeError(msg) from exc


def _post_and_return(
    *,
    team_id: str,
    turn: NimbusTurnRequest,
    poster: SlackPoster | None,
    blocks: list[dict[str, object]],
) -> ChatTurnResult:
    """Post Block Kit blocks to Slack and return an empty-text result.

    Follows the same pattern as streaming save: post the response directly so
    that the caller's ``_post_result`` step is a no-op (empty text is skipped).
    Falls back to plain-text if the poster does not support blocks or the
    send fails.
    """
    fallback = blocks_to_fallback_text(blocks)

    resolved_poster: SlackPoster | None = poster
    if resolved_poster is None:
        try:
            resolved_poster = get_slack_poster(team_id=team_id)
        except (SecretCodecError, SlackStoreError, ValueError) as exc:
            log.warning(
                "slack_blocks_poster_unavailable",
                team_id=team_id,
                channel_id=turn.channel_id,
                error=str(exc),
            )
            return _command_result(turn=turn, text=fallback)

    try:
        try:
            resolved_poster.send_blocks(
                turn.channel_id, blocks, fallback, thread_ts=turn.thread_id
            )
        except AttributeError:
            resolved_poster.send_message(
                turn.channel_id, fallback, thread_ts=turn.thread_id
            )
        runtime_telemetry.record_slack_reply(result="success", reason="blocks")
    except SlackApiError as exc:
        error = exc.response.get("error") if exc.response else "unknown_error"
        runtime_telemetry.record_slack_reply(result="failure", reason=str(error))
        log.warning(
            "slack_blocks_post_failed",
            team_id=team_id,
            channel_id=turn.channel_id,
            slack_error=error,
        )
        return _command_result(turn=turn, text=fallback)

    return _command_result(turn=turn, text="")


def _file_service(team_id: str) -> SlackFileSyncService:
    """Resolve the production file sync service lazily."""
    return get_file_sync_service(team_id=team_id)


def _setup_install_url() -> str | None:
    """Return the workspace install URL if the public base URL is configured."""
    base_url = os.environ.get(NIMBUS_SLACK_PUBLIC_BASE_URL, "").rstrip("/")
    if not base_url:
        return None
    return f"{base_url}/slack/install"


def _setup_text() -> str:
    """Return plain-text setup guidance for the no-base-URL fallback path.

    The primary path now posts a Block Kit card via ``design.setup_card``;
    this text is only used when ``NIMBUS_SLACK_PUBLIC_BASE_URL`` is not set,
    where there's no clickable URL to embed in a button.
    """
    return (
        "Nimbus setup is browser-based so secrets never enter Slack. "
        f"Set `{NIMBUS_SLACK_PUBLIC_BASE_URL}` and then open "
        "`/slack/install` on the deployed Nimbus Slack service."
    )


def _blocks_result(
    *, turn: NimbusTurnRequest, blocks: list[dict[str, object]]
) -> ChatTurnResult:
    """Return a result carrying fallback text extracted from blocks.

    Used in degraded streaming paths where blocks cannot be posted directly.
    The caller's ``_post_result`` will send the fallback as a plain-text reply.
    """
    return _command_result(turn=turn, text=blocks_to_fallback_text(blocks))


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
