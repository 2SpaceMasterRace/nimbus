"""FastAPI application for the Nimbus Slack adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape
from typing import Annotated, Any
from urllib.parse import parse_qs

import structlog
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from nimbus_slack.crypto import SecretCodecError
from nimbus_slack.dedupe import SlackEventDedupe
from nimbus_slack.deps import get_slack_store
from nimbus_slack.flow import handle_slack_event, should_handle_event
from nimbus_slack.oauth import (
    SlackOAuthConfig,
    SlackOAuthError,
    build_authorize_url,
    create_oauth_state,
    exchange_code_for_installation,
    verify_oauth_state,
)
from nimbus_slack.setup import (
    TenantSetupError,
    TenantSetupInput,
    render_setup_form,
    render_setup_success,
)
from nimbus_slack.store import SlackInstallation, SlackStore, SlackStoreError
from nimbus_slack.verify import verify_slack_secret

log: Any = structlog.get_logger()
app = FastAPI(title="Nimbus Slack", version="0.1.0")
_dedupe = SlackEventDedupe()


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a liveness response."""
    return {"status": "ok", "service": "nimbus-slack"}


@app.get("/slack/install")
async def slack_install() -> RedirectResponse:
    """Start Slack OAuth installation for a workspace."""
    config = _oauth_config_or_503()
    state = create_oauth_state(config.state_secret)
    return RedirectResponse(
        build_authorize_url(config, state=state),
        status_code=status.HTTP_302_FOUND,
    )


@app.get("/slack/oauth/callback")
async def slack_oauth_callback(code: str, state: str) -> HTMLResponse:
    """Complete Slack OAuth installation and mint the first setup link."""
    config = _oauth_config_or_503()
    if not verify_oauth_state(state, config.state_secret):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired Slack OAuth state.",
        )
    try:
        oauth_installation = exchange_code_for_installation(
            config=config,
            code=code,
        )
    except SlackOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
    store = _store_or_503()
    store.upsert_installation(
        SlackInstallation(
            team_id=oauth_installation.team_id,
            enterprise_id=oauth_installation.enterprise_id,
            team_name=oauth_installation.team_name,
            bot_user_id=oauth_installation.bot_user_id,
            bot_token=oauth_installation.bot_token,
            scopes=oauth_installation.scopes,
            installed_by=oauth_installation.installed_by,
            installed_at=datetime.now(UTC),
        )
    )
    setup_token = store.create_setup_session(
        team_id=oauth_installation.team_id,
        user_id=oauth_installation.installed_by or "unknown",
    )
    setup_path = f"/slack/setup/{setup_token}"
    escaped_team_id = escape(oauth_installation.team_id)
    escaped_setup_path = escape(setup_path, quote=True)
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Nimbus Slack installed</title></head>
<body>
  <h1>Nimbus Slack is installed</h1>
  <p>Finish BYOK setup for workspace {escaped_team_id}.</p>
  <p><a href="{escaped_setup_path}">Continue setup</a></p>
</body>
</html>""",
        status_code=status.HTTP_200_OK,
    )


@app.get("/slack/setup/{token}")
async def slack_setup_form(token: str) -> HTMLResponse:
    """Render a one-time BYOK setup page."""
    store = _store_or_503()
    session = store.get_setup_session(token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Setup link is invalid, expired, or already used.",
        )
    return HTMLResponse(render_setup_form(team_id=session.team_id, token=token))


@app.post("/slack/setup/{token}")
async def slack_setup_submit(token: str, request: Request) -> HTMLResponse:
    """Persist BYOK configuration from a trusted one-time setup page."""
    store = _store_or_503()
    session = store.get_setup_session(token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Setup link is invalid, expired, or already used.",
        )
    try:
        setup_input = TenantSetupInput.from_mapping(await _parse_setup_payload(request))
        completed = store.complete_setup_session(
            token,
            setup_input.to_tenant_config(team_id=session.team_id),
        )
    except (TenantSetupError, SlackStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    if completed is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Setup link was already consumed.",
        )
    return HTMLResponse(render_setup_success(team_id=completed.team_id))


@app.post("/slack/events")
async def slack_events(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_request_timestamp: Annotated[
        str | None,
        Header(alias="X-Slack-Request-Timestamp"),
    ] = None,
    x_slack_signature: Annotated[
        str | None,
        Header(alias="X-Slack-Signature"),
    ] = None,
) -> dict[str, object]:
    """Verify and accept Slack Events API callbacks."""
    body = await request.body()
    if not x_slack_request_timestamp or not x_slack_signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Slack signature headers.",
        )
    if not verify_slack_secret(
        body=body,
        timestamp=x_slack_request_timestamp,
        slack_signature=x_slack_signature,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Slack signature.",
        )
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack payload must be a JSON object.",
        )
    if payload.get("type") == "url_verification":
        challenge = payload.get("challenge")
        if not isinstance(challenge, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack URL verification payload is missing challenge.",
            )
        return {"challenge": challenge}
    if payload.get("type") != "event_callback":
        return {"ok": True, "ignored": True}

    event_id = _require_str(payload, "event_id")
    team_id = _require_str(payload, "team_id")
    event = payload.get("event")
    if not isinstance(event, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack event_callback payload is missing event object.",
        )
    if not should_handle_event(event):
        return {"ok": True, "ignored": True}
    if not _dedupe.claim(f"slack:{team_id}:{event_id}"):
        return {"ok": True, "duplicate": True}
    background_tasks.add_task(
        _process_event_callback,
        team_id=team_id,
        event_id=event_id,
        event=event,
    )
    return {"ok": True}


def _process_event_callback(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, object],
) -> None:
    """Run the slow Nimbus turn after Slack has been acknowledged."""
    try:
        handle_slack_event(team_id=team_id, event_id=event_id, event=event)
    except (RuntimeError, TypeError, ValueError, ValidationError):
        log.exception("slack_event_processing_failed", event_id=event_id)


def _require_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Slack payload field {key!r} must be a non-empty string.",
        )
    return value


def _oauth_config_or_503() -> SlackOAuthConfig:
    """Return OAuth configuration or fail with an HTTP service error."""
    try:
        return SlackOAuthConfig.from_env()
    except SlackOAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _store_or_503() -> SlackStore:
    """Return the durable Slack store or fail with an HTTP service error."""
    try:
        return get_slack_store()
    except (SecretCodecError, SlackStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


async def _parse_setup_payload(request: Request) -> dict[str, object]:
    """Parse JSON or urlencoded setup form input without multipart dependency."""
    content_type = request.headers.get("content-type", "").lower()
    body = await request.body()
    if content_type.startswith("application/json"):
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            msg = "Setup payload must be valid JSON."
            raise TenantSetupError(msg) from exc
        if isinstance(parsed, dict):
            return {str(key): value for key, value in parsed.items()}
        msg = "Setup payload must be a JSON object."
        raise TenantSetupError(msg)
    if content_type.startswith("application/x-www-form-urlencoded"):
        try:
            body_text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = "Setup form payload must be UTF-8."
            raise TenantSetupError(msg) from exc
        form = parse_qs(body_text, keep_blank_values=True)
        return {key: values[-1] if values else "" for key, values in form.items()}
    msg = "Setup payload must be JSON or application/x-www-form-urlencoded."
    raise TenantSetupError(msg)
