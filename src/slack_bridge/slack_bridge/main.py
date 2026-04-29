"""FastAPI application for the Nimbus Slack Bridge.

The Slack bridge is a standalone middleman service that receives Slack
Events API webhooks and slash-command form posts, normalizes them into
the Nimbus signed wrapper contract, and posts AI responses back to
Slack through the shared ``chat_client_api`` interface.

Request flow for ``POST /slack/events``:

1. Verify the Slack request signature using ``SLACK_SIGNING_SECRET``. Any
   missing, stale, or mismatched signature short-circuits with ``401``
   before any payload parsing.
2. Parse JSON. Reject non-object bodies with ``400``.
3. For ``url_verification`` payloads, echo the challenge.
4. For ``event_callback`` payloads, validate shape, drop bot-authored or
   non-message events, dedupe Slack retries by ``team_id:event_id``, and
   schedule the Nimbus dispatch as a FastAPI ``BackgroundTask``. The HTTP
   response always returns within Slack's 3-second ACK window so Slack
   does not retry the delivery.
5. For all other payload types, return a stable ``{"ok": true}`` ack.

Request flow for ``POST /slack/commands``:

1. Same signature verification as the events endpoint.
2. Parse the ``application/x-www-form-urlencoded`` body and validate the
   required slash-command fields.
3. Dedupe Slack retries by ``team_id:trigger_id`` and schedule a
   ``BackgroundTask`` that invokes Nimbus and posts the reply via the
   chat client.
4. Ack with HTTP 200 and an empty body so Slack does not show a stale
   placeholder; the AI reply lands as a normal channel message once the
   background task completes.

The bridge runs on a single Fly machine today (``min=1, max=1``); the
in-memory dedupe cache is sized for that topology. Multi-machine
deployments must move dedupe to a shared store before scaling out.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl

import structlog
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status

from slack_bridge.dedupe import EventDedupeCache
from slack_bridge.flow import handle_slack_command, handle_slack_event
from slack_bridge.telemetry import (
    record_dispatch,
    record_event_callback,
    record_inbound,
    record_slash_command,
    record_slash_inbound,
)
from slack_bridge.verify import verify_slack_secret

if TYPE_CHECKING:
    from collections.abc import Mapping

log: Any = structlog.get_logger()

app = FastAPI(title="Nimbus Slack Bridge", version="0.1.0")

_SLACK_TIMESTAMP_HEADER = "x-slack-request-timestamp"
_SLACK_SIGNATURE_HEADER = "x-slack-signature"
_DISPATCHED_EVENT_TYPES = frozenset({"message", "app_mention"})
# Required fields on a Slack slash-command form POST. ``text`` is
# allowed to be empty (e.g. the user typed ``/nimbus`` with no args), so
# it is *not* required to be present and non-empty.
_SLASH_REQUIRED_FIELDS = ("team_id", "trigger_id", "channel_id", "user_id", "command")

_dedupe_cache = EventDedupeCache()
_slash_dedupe_cache = EventDedupeCache()


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe used by deploy verification and load balancers."""
    return {"status": "ok"}


@app.post("/slack/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Receive a Slack Events API webhook and ack within Slack's 3 s window.

    Verification, payload parsing, and event filtering all run inline.
    Any successful event dispatch to Nimbus runs in a background task so
    the HTTP response is returned immediately regardless of downstream
    latency.
    """
    raw_body = await request.body()
    slack_timestamp = request.headers.get(_SLACK_TIMESTAMP_HEADER, "")
    slack_signature = request.headers.get(_SLACK_SIGNATURE_HEADER, "")
    if not verify_slack_secret(
        body=raw_body,
        timestamp=slack_timestamp,
        slack_signature=slack_signature,
    ):
        record_inbound(payload_type="unknown", result="rejected_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_slack_signature",
        )
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        record_inbound(payload_type="unknown", result="rejected_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_json",
        ) from exc
    if not isinstance(payload, dict):
        record_inbound(payload_type="unknown", result="rejected_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload_must_be_object",
        )
    payload_type = payload.get("type")
    payload_type_label = payload_type if isinstance(payload_type, str) else "unknown"
    if payload_type == "url_verification":
        return _handle_url_verification(payload, payload_type_label)
    if payload_type == "event_callback":
        return _handle_event_callback(
            payload,
            background_tasks,
            payload_type_label,
        )
    record_inbound(payload_type=payload_type_label, result="accepted")
    log.info("slack_event_unhandled_type", payload_type=payload_type)
    return {"ok": True}


def _handle_url_verification(
    payload: dict[str, Any],
    payload_type_label: str,
) -> dict[str, Any]:
    """Return Slack's URL verification challenge response."""
    challenge = payload.get("challenge")
    if not isinstance(challenge, str):
        record_inbound(payload_type=payload_type_label, result="rejected_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing_challenge",
        )
    record_inbound(payload_type=payload_type_label, result="accepted")
    log.info("slack_url_verification_handled")
    return {"challenge": challenge}


def _handle_event_callback(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    payload_type_label: str,
) -> dict[str, Any]:
    """Validate, dedupe, and schedule a Slack event-callback for dispatch.

    The HTTP response is always ``{"ok": true}`` once the request shape is
    valid: filtered/duplicate events still ack 200 so Slack does not retry,
    and any downstream failure is recorded by the background dispatcher
    rather than surfaced to Slack as a non-2xx (which would itself trigger
    a retry).
    """
    team_id = payload.get("team_id")
    event_id = payload.get("event_id")
    event = payload.get("event")
    if not isinstance(team_id, str) or not team_id:
        record_inbound(payload_type=payload_type_label, result="rejected_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing_team_id",
        )
    if not isinstance(event_id, str) or not event_id:
        record_inbound(payload_type=payload_type_label, result="rejected_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing_event_id",
        )
    if not isinstance(event, dict):
        record_inbound(payload_type=payload_type_label, result="rejected_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="missing_event",
        )
    record_inbound(payload_type=payload_type_label, result="accepted")
    if not _is_dispatchable(event):
        record_event_callback(outcome="filtered")
        log.info(
            "slack_event_filtered",
            team_id=team_id,
            event_id=event_id,
            event_type=event.get("type"),
            subtype=event.get("subtype"),
            has_bot_id=bool(event.get("bot_id")),
        )
        return {"ok": True}
    dedupe_key = f"{team_id}:{event_id}"
    if not _dedupe_cache.add(dedupe_key):
        record_event_callback(outcome="duplicate")
        log.info(
            "slack_event_duplicate",
            team_id=team_id,
            event_id=event_id,
        )
        return {"ok": True}
    record_event_callback(outcome="dispatched")
    log.info(
        "slack_event_dispatch_scheduled",
        team_id=team_id,
        event_id=event_id,
        event_type=event.get("type"),
    )
    background_tasks.add_task(
        _dispatch_with_logging,
        team_id=team_id,
        event_id=event_id,
        event=event,
    )
    return {"ok": True}


def _is_dispatchable(event: dict[str, Any]) -> bool:
    """Return ``True`` when ``event`` is a user-authored chat event we handle.

    The bridge only forwards plain ``message`` and ``app_mention`` events
    that originated from a real user. Bot-authored messages, edits, and
    other subtype-tagged events are dropped at the boundary so the AI
    server never sees them and the bridge cannot loop on its own posts.
    """
    if event.get("type") not in _DISPATCHED_EVENT_TYPES:
        return False
    if event.get("subtype"):
        return False
    if event.get("bot_id"):
        return False
    for key in ("user", "channel", "ts"):
        value = event.get(key)
        if not isinstance(value, str) or not value:
            return False
    return True


def _dispatch_with_logging(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, Any],
) -> None:
    """Run ``handle_slack_event`` in a background task and record any failure.

    A background fire-and-forget task must not let any exception escape the
    event loop silently, so this wrapper logs everything via ``structlog``
    and records dispatch latency and outcome via :mod:`slack_bridge.telemetry`.
    The broad ``except`` is intentional and is the single place where every
    failure mode of the dispatch path is consolidated for observability.
    """
    started = time.monotonic()
    try:
        handle_slack_event(team_id=team_id, event_id=event_id, event=event)
    except Exception:
        latency_ms = (time.monotonic() - started) * 1000.0
        record_dispatch(outcome="failure", latency_ms=latency_ms, source="event")
        log.exception(
            "slack_bridge_dispatch_failed",
            team_id=team_id,
            event_id=event_id,
        )
    else:
        latency_ms = (time.monotonic() - started) * 1000.0
        record_dispatch(outcome="success", latency_ms=latency_ms, source="event")


@app.post("/slack/commands")
async def slack_commands(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    """Receive a Slack slash-command form POST and ack within Slack's 3 s window.

    The HTTP response is intentionally minimal: an empty 200 body so
    Slack does not render a placeholder. The AI reply lands as a normal
    channel message once the background task completes, mirroring the
    events flow.
    """
    raw_body = await request.body()
    slack_timestamp = request.headers.get(_SLACK_TIMESTAMP_HEADER, "")
    slack_signature = request.headers.get(_SLACK_SIGNATURE_HEADER, "")
    if not verify_slack_secret(
        body=raw_body,
        timestamp=slack_timestamp,
        slack_signature=slack_signature,
    ):
        record_slash_inbound(result="rejected_signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid_slack_signature",
        )
    form = _parse_slash_command_form(raw_body)
    if form is None:
        record_slash_inbound(result="rejected_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid_form_body",
        )
    missing_field = _missing_slash_field(form)
    if missing_field is not None:
        record_slash_inbound(result="rejected_payload")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"missing_{missing_field}",
        )
    record_slash_inbound(result="accepted")
    dedupe_key = f"{form['team_id']}:{form['trigger_id']}"
    if not _slash_dedupe_cache.add(dedupe_key):
        record_slash_command(outcome="duplicate")
        log.info(
            "slack_command_duplicate",
            team_id=form["team_id"],
            trigger_id=form["trigger_id"],
            command=form.get("command"),
        )
        return {}
    record_slash_command(outcome="dispatched")
    log.info(
        "slack_command_dispatch_scheduled",
        team_id=form["team_id"],
        trigger_id=form["trigger_id"],
        command=form.get("command"),
    )
    background_tasks.add_task(_dispatch_command_with_logging, form=dict(form))
    return {}


def _parse_slash_command_form(raw_body: bytes) -> dict[str, str] | None:
    """Decode a slash-command form body. Return ``None`` on decode failure."""
    try:
        decoded = raw_body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    parsed = parse_qsl(decoded, keep_blank_values=True, strict_parsing=False)
    return dict(parsed)


def _missing_slash_field(form: Mapping[str, str]) -> str | None:
    """Return the name of the first required field that is missing or blank."""
    for field in _SLASH_REQUIRED_FIELDS:
        value = form.get(field)
        if not isinstance(value, str) or not value:
            return field
    return None


def _dispatch_command_with_logging(*, form: dict[str, str]) -> None:
    """Run ``handle_slack_command`` in a background task and record any failure.

    Mirrors :func:`_dispatch_with_logging` for the slash-command path so
    that both inputs share a single consolidated dispatch-failure log
    site and a single dispatch counter (distinguished by ``source``).
    """
    started = time.monotonic()
    try:
        handle_slack_command(form)
    except Exception:
        latency_ms = (time.monotonic() - started) * 1000.0
        record_dispatch(
            outcome="failure", latency_ms=latency_ms, source="slash_command"
        )
        log.exception(
            "slack_bridge_slash_dispatch_failed",
            team_id=form.get("team_id"),
            trigger_id=form.get("trigger_id"),
            command=form.get("command"),
        )
    else:
        latency_ms = (time.monotonic() - started) * 1000.0
        record_dispatch(
            outcome="success", latency_ms=latency_ms, source="slash_command"
        )
