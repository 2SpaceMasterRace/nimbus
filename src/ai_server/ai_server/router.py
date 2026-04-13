"""FastAPI router for the AI server.

Routes
------
``GET  /health``  — liveness probe, no auth required.
``POST /chat``    — send a message to the AI agent, receive a reply.

Authentication
--------------
Every ``/chat`` call must carry the shared secret in ``X-API-Key``.
The expected value comes from ``AI_SERVER_API_KEY`` in the environment.

Session management
------------------
Conversations are keyed by the caller-supplied ``session_id`` (e.g. a
Slack channel ID or thread timestamp) and persisted as JSON files under
``AI_SESSION_DIR``.  Each request loads the file, appends the new turn,
runs the model, and saves the result.  Concurrent requests for the
*same* ``session_id`` are not locked — acceptable for the MVP; add a
per-session ``asyncio.Lock`` when that becomes a concern.

Tools
-----
Tool calling is disabled in this MVP — the LLM converses in plain text.
``build_slack_tools()`` in ``slack_tools.py`` is the scaffold; uncomment
the line in ``chat()`` once a real ``CloudStorageClient`` is wired in.

Blocking I/O
------------
``OpenRouterClient.send_message`` calls ``pydantic-ai``'s ``run_sync``,
which blocks the calling thread.  We offload it to a thread-pool via
``asyncio.to_thread`` so the event loop stays responsive.

TODO: migrate to ``agent.run()`` (native async) in a follow-up; that
removes the thread-pool overhead and makes back-pressure cleaner.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from openrouter_ai_client_impl.config import DEFAULT_SYSTEM_PROMPT, OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient
from pydantic import BaseModel, Field

from ai_client_api import (
    AIClientConfigError,
    AIClientError,
    AIProviderError,
    AIRateLimitError,
    AIStepBudgetExceededError,
    AITimeoutError,
)
from ai_server.auth import require_api_key
from ai_server.sessions import load_session, save_session

log: Any = structlog.get_logger()

router = APIRouter(tags=["ai"])

_DEFAULT_SESSION_DIR = Path.home() / ".nimbus" / "sessions" / "ai_server"


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Body accepted by ``POST /chat``."""

    message: str = Field(
        description="The user's message to forward to the AI.",
        min_length=1,
        max_length=4096,
    )
    session_id: str = Field(
        description=(
            "Opaque conversation key.  Use a Slack channel ID or thread "
            "timestamp so the AI remembers prior turns in that channel."
        ),
        min_length=1,
        max_length=128,
    )
    user_id: str | None = Field(
        default=None,
        description=(
            "Optional caller identity (e.g. Slack user ID).  Logged for "
            "observability; not used in the AI prompt yet."
        ),
        max_length=128,
    )


class ChatResponse(BaseModel):
    """Body returned by ``POST /chat``."""

    response: str = Field(description="The AI's reply.")
    session_id: str = Field(description="Echo of the request ``session_id``.")
    model: str = Field(description="Model that produced this reply.")
    steps: int = Field(description="Number of model-call rounds taken.")
    fallback_used: bool = Field(
        description="True if the primary model failed and the fallback was used.",
    )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def get_ai_client() -> OpenRouterClient:
    """Construct an ``OpenRouterClient`` from the process environment.

    Overridden in tests via ``app.dependency_overrides[get_ai_client]``.
    """
    return OpenRouterClient(OpenRouterConfig.from_env())


def _session_dir() -> Path:
    """Return the session-storage directory, reading ``AI_SESSION_DIR`` at call time."""
    raw = os.environ.get("AI_SESSION_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_SESSION_DIR


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — no authentication required."""
    return {"status": "ok", "service": "ai-server"}


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    _auth: Annotated[str, Depends(require_api_key)],
    client: Annotated[OpenRouterClient, Depends(get_ai_client)],
) -> ChatResponse:
    """Accept a message, run the AI agent loop, and return the reply.

    Session history is loaded from disk before the call and saved back
    after it, so the AI remembers prior turns within the same
    ``session_id``.

    Args:
        req: Validated request body.
        _auth: Injected API-key guard (return value unused).
        client: Injected ``OpenRouterClient``.

    Returns:
        ``ChatResponse`` containing the AI reply and metadata.

    Raises:
        HTTPException 422: Unsafe ``session_id`` or step-budget exceeded.
        HTTPException 429: Upstream AI provider rate limit.
        HTTPException 502: Upstream AI provider error.
        HTTPException 503: Server-side misconfiguration.
        HTTPException 504: Upstream AI provider timeout.

    """
    session_dir = _session_dir()
    log.info("chat_request", session_id=req.session_id, user_id=req.user_id)

    try:
        conv = load_session(session_dir, req.session_id, DEFAULT_SYSTEM_PROMPT)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    conv.add_user(req.message)

    # ``send_message`` calls ``agent.run_sync`` internally -- blocking.
    # ``asyncio.to_thread`` moves it off the event loop so other requests
    # can be served while the LLM is thinking (typically 5-30 s).
    #
    # To enable file tools once a real CloudStorageClient is wired in,
    # import build_slack_tools from ai_server.slack_tools and pass
    # tools=build_slack_tools() instead of tools=None.
    try:
        ai_response = await asyncio.to_thread(
            client.send_message,
            conv,
            tools=None,
        )
    except AIClientConfigError as exc:
        log.exception("ai_config_error", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service is misconfigured — check server logs.",
        ) from exc
    except AIRateLimitError as exc:
        log.warning("ai_rate_limit", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI provider is rate-limited.  Try again shortly.",
        ) from exc
    except AITimeoutError as exc:
        log.warning("ai_timeout", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="AI provider timed out.",
        ) from exc
    except AIStepBudgetExceededError as exc:
        log.warning("ai_step_budget_exceeded", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="AI required too many steps.  Simplify the request.",
        ) from exc
    except (AIProviderError, AIClientError) as exc:
        log.exception("ai_provider_error", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream AI provider error.",
        ) from exc

    # Persist the updated conversation.  A save failure is logged but does
    # not fail the request — the reply is already computed.
    try:
        save_session(session_dir, req.session_id, conv)
    except Exception:
        log.exception("session_save_failed", session_id=req.session_id)

    log.info(
        "chat_response",
        session_id=req.session_id,
        model=ai_response.model,
        steps=ai_response.steps,
        tokens=ai_response.tokens.total,
        fallback_used=ai_response.fallback_used,
    )

    return ChatResponse(
        response=ai_response.text,
        session_id=req.session_id,
        model=ai_response.model,
        steps=ai_response.steps,
        fallback_used=ai_response.fallback_used,
    )
