"""FastAPI application for the Nimbus Slack adapter."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections import OrderedDict
from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING, Annotated, Any

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
from urllib.parse import parse_qs

import sentry_sdk
import structlog
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from nimbus_runtime.observability import configure_observability

from nimbus_slack.crypto import SecretCodecError
from nimbus_slack.dedupe import SlackEventDedupe
from nimbus_slack.deps import check_slack_store_ready, get_slack_store
from nimbus_slack.flow import (
    handle_app_home_opened,
    handle_slack_event,
    handle_slack_interaction,
    should_handle_event,
)
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
    csp_header_value,
    render_install_success,
    render_setup_error,
    render_setup_form,
    render_setup_success,
)
from nimbus_slack.store import SlackInstallation, SlackStoreBackend, SlackStoreError
from nimbus_slack.verify import verify_slack_secret

log: Any = structlog.get_logger()


@contextlib.asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """Start background task workers on startup; cancel them on shutdown."""
    worker_tasks: list[asyncio.Task[None]] = []
    try:
        from nimbus_slack.runtime import (  # noqa: PLC0415
            NIMBUS_SLACK_MODEL_MODE_REMOTE,
            slack_model_mode,
        )
        from nimbus_slack.verifier import (  # noqa: PLC0415
            build_scheduled_verifier_tasks,
        )
        from nimbus_slack.worker import build_tenant_workers  # noqa: PLC0415

        mode = slack_model_mode()
        store = get_slack_store()
        team_ids = store.list_active_team_ids()
        if mode != NIMBUS_SLACK_MODEL_MODE_REMOTE:
            if team_ids:
                worker_tasks = build_tenant_workers(team_ids=team_ids)
                log.info(
                    "slack_workers_started",
                    count=len(worker_tasks),
                    team_ids=team_ids,
                )
            else:
                log.info("slack_workers_skipped_no_byok_tenants")
        else:
            log.info("slack_workers_skipped_remote_mode")
        verifier_tasks = build_scheduled_verifier_tasks(
            team_ids=team_ids,
            store=store,
        )
        if verifier_tasks:
            worker_tasks.extend(verifier_tasks)
            log.info("slack_scheduled_verifier_started", count=len(verifier_tasks))
    except Exception as exc:  # noqa: BLE001
        log.warning("slack_workers_startup_failed", error=str(exc))

    try:
        yield
    finally:
        for task in worker_tasks:
            task.cancel()
        if worker_tasks:
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            log.info("slack_workers_stopped", count=len(worker_tasks))


app = FastAPI(title="Nimbus Slack", version="0.1.0", lifespan=_lifespan)
configure_observability("nimbus-slack", app=app)
_dedupe = SlackEventDedupe()

NIMBUS_SLACK_SETUP_RATE_LIMIT_RPM = "NIMBUS_SLACK_SETUP_RATE_LIMIT_RPM"
NIMBUS_SLACK_SETUP_RATE_LIMIT_BURST = "NIMBUS_SLACK_SETUP_RATE_LIMIT_BURST"
NIMBUS_SLACK_SETUP_RATE_LIMIT_MAX_KEYS = "NIMBUS_SLACK_SETUP_RATE_LIMIT_MAX_KEYS"
_DEFAULT_SETUP_RATE_LIMIT_RPM = 10
_DEFAULT_SETUP_RATE_LIMIT_BURST = 10
_DEFAULT_SETUP_RATE_LIMIT_MAX_KEYS = 1024
_DEFAULT_SETUP_MAX_BODY_BYTES = 64 * 1024


class _SetupRateLimiter:
    """Per-key token bucket with bounded memory for setup-token endpoints.

    Uses :class:`OrderedDict` as an LRU map so that an open relay attempt with
    fresh client IPs cannot grow memory without bound — the size of the
    registry tracks active workload, not historical traffic.
    """

    def __init__(
        self,
        *,
        capacity: int,
        refill_per_second: float,
        max_keys: int,
    ) -> None:
        self._capacity = float(capacity)
        self._refill_per_second = refill_per_second
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._lock = Lock()

    def allow(self, key: str, *, _now: float | None = None) -> bool:
        """Return whether one request from ``key`` is allowed right now."""
        now = _now if _now is not None else time.monotonic()
        with self._lock:
            tokens, last = self._buckets.pop(key, (self._capacity, now))
            tokens = min(
                self._capacity,
                tokens + (now - last) * self._refill_per_second,
            )
            allowed = tokens >= 1.0
            if allowed:
                tokens -= 1.0
            self._buckets[key] = (tokens, now)
            self._buckets.move_to_end(key)
            while len(self._buckets) > self._max_keys:
                self._buckets.popitem(last=False)
            return allowed


def _build_setup_rate_limiter() -> _SetupRateLimiter:
    """Create the setup-token rate limiter from environment configuration."""
    rpm = _positive_int_env(
        NIMBUS_SLACK_SETUP_RATE_LIMIT_RPM,
        default=_DEFAULT_SETUP_RATE_LIMIT_RPM,
    )
    burst = _positive_int_env(
        NIMBUS_SLACK_SETUP_RATE_LIMIT_BURST,
        default=_DEFAULT_SETUP_RATE_LIMIT_BURST,
    )
    max_keys = _positive_int_env(
        NIMBUS_SLACK_SETUP_RATE_LIMIT_MAX_KEYS,
        default=_DEFAULT_SETUP_RATE_LIMIT_MAX_KEYS,
    )
    return _SetupRateLimiter(
        capacity=burst,
        refill_per_second=rpm / 60.0,
        max_keys=max_keys,
    )


def _positive_int_env(name: str, *, default: int) -> int:
    """Return a positive integer environment override or the default."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError as exc:
        msg = f"{name} must be a positive integer."
        raise ValueError(msg) from exc
    if parsed <= 0:
        msg = f"{name} must be a positive integer."
        raise ValueError(msg)
    return parsed


_setup_rate_limiter = _build_setup_rate_limiter()


def _enforce_setup_rate_limit(request: Request) -> None:
    """Rate-limit setup-token attempts per client IP."""
    if request.client is None:
        return
    if _setup_rate_limiter.allow(request.client.host):
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many setup attempts; please slow down.",
    )


def _setup_html_response(html: str, *, status_code: int = 200) -> HTMLResponse:
    """Return an HTMLResponse with the locked-down setup CSP header attached."""
    return HTMLResponse(
        html,
        status_code=status_code,
        headers={
            "Content-Security-Policy": csp_header_value(),
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Return a liveness response."""
    return {"status": "ok", "service": "nimbus-slack"}


@app.get("/ready")
async def ready() -> dict[str, str]:
    """Return readiness only when the configured durable store is usable."""
    try:
        check_slack_store_ready()
    except (SecretCodecError, SlackStoreError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    return {"status": "ready", "service": "nimbus-slack"}


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
    return _setup_html_response(
        render_install_success(
            team_id=oauth_installation.team_id,
            setup_path=setup_path,
        )
    )


@app.get("/slack/setup/{token}")
async def slack_setup_form(token: str, request: Request) -> HTMLResponse:
    """Render a one-time BYOK setup page."""
    _enforce_setup_rate_limit(request)
    store = _store_or_503()
    session = store.get_setup_session(token)
    if session is None:
        return _setup_html_response(
            render_setup_error(
                title="Setup link unavailable",
                message="This setup link is invalid, expired, or already used.",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    installation = store.get_installation(session.team_id)
    team_name = installation.team_name if installation else None
    return _setup_html_response(
        render_setup_form(team_id=session.team_id, token=token, team_name=team_name)
    )


@app.post("/slack/setup/{token}")
async def slack_setup_submit(token: str, request: Request) -> HTMLResponse:
    """Persist BYOK configuration from a trusted one-time setup page."""
    _enforce_setup_rate_limit(request)
    store = _store_or_503()
    session = store.get_setup_session(token)
    if session is None:
        return _setup_html_response(
            render_setup_error(
                title="Setup link unavailable",
                message="This setup link is invalid, expired, or already used.",
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    raw_payload: dict[str, object] | None = None
    try:
        raw_payload = await _parse_setup_payload(request)
        setup_input = TenantSetupInput.from_mapping(raw_payload)
        completed = store.complete_setup_session(
            token,
            setup_input.to_tenant_config(team_id=session.team_id),
        )
    except TenantSetupError as exc:
        installation = store.get_installation(session.team_id)
        team_name = installation.team_name if installation else None
        return _setup_html_response(
            render_setup_form(
                team_id=session.team_id,
                token=token,
                team_name=team_name,
                error=str(exc),
                values=raw_payload,
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except SlackStoreError as exc:
        return _setup_html_response(
            render_setup_error(
                title="Setup could not be saved",
                message=str(exc),
            ),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_413_CONTENT_TOO_LARGE:
            return _setup_html_response(
                render_setup_error(
                    title="Setup payload too large",
                    message=str(exc.detail),
                ),
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            )
        raise
    if completed is None:
        return _setup_html_response(
            render_setup_error(
                title="Setup link already used",
                message="This setup link was already consumed. Start setup again from Slack.",  # noqa: E501
            ),
            status_code=status.HTTP_409_CONFLICT,
        )
    installation = store.get_installation(completed.team_id)
    team_name = installation.team_name if installation else None
    return _setup_html_response(
        render_setup_success(team_id=completed.team_id, team_name=team_name)
    )


@app.post("/slack/events")
async def slack_events(  # noqa: C901
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

    # App Home tab: user opened the Nimbus home surface — publish a fresh view.
    if event.get("type") == "app_home_opened" and event.get("tab") == "home":
        user_id = event.get("user")
        if isinstance(user_id, str) and user_id:
            dedupe_key = f"slack-home:{team_id}:{user_id}:{event_id}"
            if _dedupe.claim(dedupe_key):
                background_tasks.add_task(
                    _process_home_opened,
                    team_id=team_id,
                    user_id=user_id,
                )
        return {"ok": True}

    if not should_handle_event(event, team_id=team_id):
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


@app.post("/slack/interactive")
async def slack_interactive(
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
    """Receive Slack interactive payloads (button clicks, select menus).

    Slack POSTs a form-encoded body with a single ``payload`` field whose
    value is a JSON string. We verify the signature against the *raw* body
    (Slack does not re-sign the parsed JSON), parse the payload, and dispatch
    to a background worker so we can ACK in <3 seconds.

    Supported action_ids:
      - ``cmd:save_channel_files`` / ``cmd:diff_channel_files`` /
        ``cmd:list_channel_files`` / ``cmd:dedupe_report`` /
        ``cmd:changed_since_sync`` — re-runs the matching adapter command.
      - ``cmd:retry_save`` — convenience alias for save_channel_files.
      - ``approve:<action-id>`` / ``reject:<action-id>`` — destructive-action
        approval responses; approve routes through the tenant runtime for atomic
        decide + execute, reject records the decision and expires the action.
      - ``open_setup`` / ``open_docs`` / ``open_link`` — link-style buttons,
        no-op acks (the browser navigates on Slack's side).
    """
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

    form = parse_qs(body.decode("utf-8"))
    raw = form.get("payload", [""])[0]
    try:
        payload = json.loads(raw) if raw else None
    except (TypeError, ValueError) as exc:
        log.warning("slack_interactive_invalid_payload", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack interactive payload is not valid JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack interactive payload must be a JSON object.",
        )

    if payload.get("type") != "block_actions":
        # We only handle block_actions today (button clicks). Acknowledge
        # other interactive types (modals, shortcuts) without acting.
        return {"ok": True, "ignored": payload.get("type")}

    team = payload.get("team") or {}
    team_id = team.get("id") if isinstance(team, dict) else None
    if not isinstance(team_id, str) or not team_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Slack interactive payload is missing team.id.",
        )

    actions = payload.get("actions") or []
    if not isinstance(actions, list) or not actions:
        return {"ok": True, "ignored": "no_actions"}

    # Idempotency: each (team, user, action_ts) tuple maps to one interaction.
    action = actions[0]
    if not isinstance(action, dict):
        return {"ok": True, "ignored": "malformed_action"}
    action_ts = str(action.get("action_ts") or "")
    user = payload.get("user") or {}
    user_id = user.get("id") if isinstance(user, dict) else ""
    dedupe_key = f"slack-interactive:{team_id}:{user_id}:{action_ts}"
    if not _dedupe.claim(dedupe_key):
        return {"ok": True, "duplicate": True}

    background_tasks.add_task(
        _process_interactive_callback,
        team_id=team_id,
        payload=payload,
    )
    return {"ok": True}


def _process_interactive_callback(
    *,
    team_id: str,
    payload: dict[str, object],
) -> None:
    """Run the slow interactive action after Slack has been acknowledged."""
    try:
        handle_slack_interaction(team_id=team_id, payload=payload)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        log.exception(
            "slack_interactive_processing_failed",
            team_id=team_id,
            action_id=_first_action_id(payload),
        )


def _first_action_id(payload: dict[str, object]) -> str | None:
    """Best-effort extraction of the primary action_id for logging."""
    actions = payload.get("actions")
    if isinstance(actions, list) and actions and isinstance(actions[0], dict):
        action_id = actions[0].get("action_id")
        if isinstance(action_id, str):
            return action_id
    return None


def _process_home_opened(*, team_id: str, user_id: str) -> None:
    """Publish the App Home tab after Slack has been acknowledged."""
    try:
        handle_app_home_opened(team_id=team_id, user_id=user_id)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        log.exception(
            "slack_home_publish_failed",
            team_id=team_id,
            user_id=user_id,
        )


def _process_event_callback(
    *,
    team_id: str,
    event_id: str,
    event: dict[str, object],
) -> None:
    """Run the slow Nimbus turn after Slack has been acknowledged."""
    try:
        handle_slack_event(team_id=team_id, event_id=event_id, event=event)
    except Exception as exc:
        channel_id = event.get("channel")
        user_id = event.get("user")
        sentry_sdk.capture_exception(exc)
        log.exception(
            "slack_event_processing_failed",
            team_id=team_id,
            event_id=event_id,
            channel_id=channel_id if isinstance(channel_id, str) else None,
            user_id=user_id if isinstance(user_id, str) else None,
        )


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


def _store_or_503() -> SlackStoreBackend:
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
    max_body_bytes = _positive_int_env(
        "NIMBUS_SLACK_SETUP_MAX_BODY_BYTES",
        default=_DEFAULT_SETUP_MAX_BODY_BYTES,
    )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError as exc:
            msg = "Setup payload Content-Length must be an integer."
            raise TenantSetupError(msg) from exc
        if declared_size > max_body_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Setup payload is too large.",
            )
    body = await request.body()
    if len(body) > max_body_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Setup payload is too large.",
        )
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
