"""FastAPI router for the AI server.

Routes
------
``GET  /health``                        — liveness probe, no auth required.
``POST /chat``                          — send a message to the AI agent.
``GET  /sessions/{session_id}/history`` — retrieve conversation history.
``DELETE /sessions/{session_id}``       — delete (reset) a session.

Authentication
--------------
Every route except ``/health`` requires the shared secret in ``X-API-Key``.
The expected value comes from ``AI_SERVER_API_KEY`` in the environment.

Session management
------------------
Conversations are keyed by the caller-supplied ``session_id`` (e.g. a
Slack channel ID or thread timestamp) and persisted as JSON files under
``AI_SESSION_DIR``.  Each ``/chat`` request serialises access to the same
session through a per-session ``asyncio.Lock`` so that concurrent Slack
messages in the same channel are queued, not interleaved.

Tools
-----
Tool calling is disabled in this MVP — the LLM converses in plain text.
``build_slack_tools()`` in ``slack_tools.py`` is the scaffold; pass
``tools=build_slack_tools(storage=...)`` in ``chat()`` once a real
``CloudStorageClient`` is wired in.

Blocking I/O
------------
``OpenRouterClient.send_message`` calls ``pydantic-ai``'s ``run_sync``,
which blocks the calling thread.  We offload it to a thread-pool via
``asyncio.to_thread`` so the event loop stays responsive.

Migrate to ``agent.run()`` (native async) in a follow-up; that removes
the thread-pool overhead and makes back-pressure cleaner.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Path as ApiPath
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
from ai_server.sessions import delete_session, load_session, save_session

log: Any = structlog.get_logger()

router = APIRouter(tags=["ai"])

_DEFAULT_SESSION_DIR = Path.home() / ".nimbus" / "sessions" / "ai_server"

# ---------------------------------------------------------------------------
# Per-user rate limiting (FM10) — token bucket keyed by user_id
# ---------------------------------------------------------------------------
#
# Capacity and refill rate are intentionally conservative for a shared
# free-tier system.  Override via env vars ``AI_RATE_LIMIT_CAPACITY`` and
# ``AI_RATE_LIMIT_RPM`` if needed.

_RATE_LIMIT_CAPACITY: int = int(os.environ.get("AI_RATE_LIMIT_CAPACITY", "10"))
_RATE_LIMIT_RPM: float = float(os.environ.get("AI_RATE_LIMIT_RPM", "10"))
# Convert RPM → tokens-per-second for the refill arithmetic.
_RATE_LIMIT_REFILL_RATE: float = _RATE_LIMIT_RPM / 60.0


@dataclass
class _TokenBucket:
    """Simple token-bucket state for one user_id."""

    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


# Module-level registry.  CPython dict operations are GIL-atomic; safe in a
# single-event-loop async server.  Entries accumulate (one per unique user_id)
# but are small objects — bounded by the number of active Slack/API users.
_rate_buckets: dict[str, _TokenBucket] = {}


def _check_rate_limit(user_id: str | None) -> bool:
    """Return ``True`` if the request is allowed, ``False`` if over the limit.

    Requests without a ``user_id`` are always allowed (backwards-compatible for
    callers that omit the field).  The bucket is replenished continuously at
    ``_RATE_LIMIT_REFILL_RATE`` tokens/second up to ``_RATE_LIMIT_CAPACITY``.
    """
    if user_id is None:
        return True
    now = time.monotonic()
    bucket = _rate_buckets.get(user_id)
    if bucket is None:
        # First request: consume one token immediately, start full - 1.
        _rate_buckets[user_id] = _TokenBucket(
            tokens=_RATE_LIMIT_CAPACITY - 1.0,
            last_refill=now,
        )
        return True
    elapsed = now - bucket.last_refill
    bucket.tokens = min(
        float(_RATE_LIMIT_CAPACITY),
        bucket.tokens + elapsed * _RATE_LIMIT_REFILL_RATE,
    )
    bucket.last_refill = now
    if bucket.tokens >= 1.0:
        bucket.tokens -= 1.0
        return True
    return False


# Per-session asyncio locks.  Keyed by session_id; created on first use and
# held for the duration of load → run → save so that concurrent requests for
# the same session are serialised rather than interleaved.  Locks are never
# removed — each is a small object and the session-ID space is bounded in
# practice by the number of Slack channels.
_session_locks: dict[str, asyncio.Lock] = {}


def _get_session_lock(session_id: str) -> asyncio.Lock:
    """Return the asyncio.Lock for *session_id*, creating it on first use.

    CPython dict operations are GIL-atomic, so the check-then-set is safe
    in a single-threaded event loop.  Async handlers never race here.
    """
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


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
            "observability; not stored in session history yet."
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


class MessageRecord(BaseModel):
    """Single turn in a conversation history response."""

    role: str = Field(description="Speaker role: 'user', 'assistant', or 'tool'.")
    content: str = Field(description="Message text or tool result.")


class SessionHistoryResponse(BaseModel):
    """Body returned by ``GET /sessions/{session_id}/history``."""

    session_id: str = Field(description="The requested session ID.")
    message_count: int = Field(description="Number of non-system messages.")
    messages: list[MessageRecord] = Field(
        description="Ordered conversation turns, oldest first."
    )


class SessionDeleteResponse(BaseModel):
    """Body returned by ``DELETE /sessions/{session_id}``."""

    deleted: bool = Field(description="True if a session file was found and removed.")
    session_id: str = Field(description="The deleted session ID.")


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def get_ai_client() -> OpenRouterClient:
    """Construct an ``OpenRouterClient`` from the process environment.

    Overridden in tests via ``app.dependency_overrides[get_ai_client]``.

    Raises:
        HTTPException 503: ``OPENROUTER_API_KEY`` is not set.  Raised here
            (in the dependency, not in the handler) so FastAPI surfaces a
            clean 503 rather than an unhandled 500, even for requests that
            would otherwise fail Pydantic validation.

    """
    try:
        return OpenRouterClient(OpenRouterConfig.from_env())
    except AIClientConfigError as exc:
        log.exception("ai_config_error_at_startup", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service is misconfigured — check server logs.",
        ) from exc


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

    Acquires the per-session lock before loading state so that concurrent
    requests for the same ``session_id`` (e.g. two rapid Slack messages in
    the same channel) are serialised rather than interleaved.

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

    if not _check_rate_limit(req.user_id):
        log.warning("rate_limit_exceeded", user_id=req.user_id)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Per-user rate limit exceeded. Try again shortly.",
        )

    async with _get_session_lock(req.session_id):
        try:
            conv = load_session(session_dir, req.session_id, DEFAULT_SYSTEM_PROMPT)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc

        conv.add_user(req.message)

        # ``send_message`` calls ``agent.run_sync`` internally — blocking.
        # ``asyncio.to_thread`` moves it off the event loop so the lock is
        # released to other coroutines while the LLM is thinking.
        #
        # To enable file tools once a real CloudStorageClient is wired in,
        # import build_slack_tools from ai_server.slack_tools and pass
        # tools=build_slack_tools(storage=...) instead of tools=None.
        try:
            ai_response = await asyncio.to_thread(
                client.send_message,
                conv,
                tools=None,
            )
        except AIClientConfigError as exc:
            # Can be raised by send_message if the client detects a config
            # problem at call time (e.g. expired or revoked API key).
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

        # Persist the updated conversation.  A save failure is logged but
        # does not fail the request — the reply is already computed.
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


@router.get("/sessions/{session_id}/history", response_model=SessionHistoryResponse)
async def get_session_history(
    session_id: Annotated[str, ApiPath(min_length=1, max_length=128)],
    _auth: Annotated[str, Depends(require_api_key)],
) -> SessionHistoryResponse:
    """Return the stored conversation history for *session_id*.

    Reads the persisted session file directly (does not acquire the session
    lock — atomic writes guarantee a consistent read even without locking).

    Args:
        session_id: Session to inspect.
        _auth: Injected API-key guard.

    Returns:
        ``SessionHistoryResponse`` with ordered message turns.

    Raises:
        HTTPException 404: No session found for *session_id*.
        HTTPException 422: *session_id* contains unsafe characters.

    """
    session_dir = _session_dir()
    try:
        conv = load_session(session_dir, session_id, DEFAULT_SYSTEM_PROMPT)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    # load_session returns a fresh (empty) Conversation when the file does
    # not exist — distinguish that from a real empty session by checking
    # whether a file is actually present.
    if not (session_dir / f"{session_id}.json").is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No session found for session_id={session_id!r}.",
        )

    # conv.messages() includes the pinned system message; skip it.
    non_system = [m for m in conv.messages() if m.role.value != "system"]
    records = [MessageRecord(role=m.role.value, content=m.content) for m in non_system]

    return SessionHistoryResponse(
        session_id=session_id,
        message_count=len(records),
        messages=records,
    )


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def delete_session_endpoint(
    session_id: Annotated[str, ApiPath(min_length=1, max_length=128)],
    _auth: Annotated[str, Depends(require_api_key)],
) -> SessionDeleteResponse:
    """Delete (reset) the stored conversation for *session_id*.

    Acquires the per-session lock to prevent a concurrent ``/chat`` request
    from writing a stale session back after the delete.

    Args:
        session_id: Session to delete.
        _auth: Injected API-key guard.

    Returns:
        ``SessionDeleteResponse`` — ``deleted=True`` if a file was removed,
        ``deleted=False`` if no session existed (idempotent).

    Raises:
        HTTPException 422: *session_id* contains unsafe characters.

    """
    session_dir = _session_dir()
    try:
        async with _get_session_lock(session_id):
            removed = delete_session(session_dir, session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    log.info("session_deleted", session_id=session_id, existed=removed)
    return SessionDeleteResponse(deleted=removed, session_id=session_id)
