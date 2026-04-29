"""FastAPI router for the AI server.

Routes
------
 ``GET  /health``                        — liveness probe, no auth required.
 ``POST /chat/turn``                     — wrapper-facing canonical chat turn.
 ``GET  /sessions/{session_id}/history`` — retrieve conversation history.
 ``DELETE /sessions/{session_id}``       — delete (reset) a session.

Authentication
--------------
The session-management endpoints use ``X-API-Key`` auth. ``/chat/turn`` uses
stronger signed-request auth with freshness and replay checks for
wrapper-to-service calls.

Session management
------------------
Conversations are keyed by the caller-supplied or derived ``session_id`` and
persisted as JSON files under ``AI_SESSION_DIR``. Each request serializes
access to its session through a per-session ``asyncio.Lock`` so concurrent
turns do not interleave.

Tools
-----
``/chat/turn`` binds real chat-safe storage behavior when ``NIMBUS_CONTAINER``
or ``AWS_BUCKET_NAME`` is configured. The AI-facing tool surface for the
wrapper path remains read-only; destructive delete and bounded attachment
uploads are handled explicitly in the shared runtime so the wrapper contract
stays confirmation-safe and machine-readable.

Blocking I/O
------------
``OpenRouterClient.send_message`` calls ``pydantic-ai``'s ``run_sync``, which
blocks the calling thread. The shared runtime offloads it to a thread pool via
``asyncio.to_thread`` so the event loop stays responsive.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import structlog
from cloud_storage_api import (
    CloudStorageClient,  # noqa: TC002 - FastAPI must resolve this dependency annotation at runtime
)
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Path as ApiPath
from nimbus_runtime.models import ChatTurnInput as RuntimeChatTurnInput
from openrouter_ai_client_impl.config import DEFAULT_SYSTEM_PROMPT, OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient
from pydantic import BaseModel, Field, ValidationError, model_validator

from ai_client_api import (
    AIClientConfigError,
    AIClientError,
    AIProviderError,
    AIRateLimitError,
    AIStepBudgetExceededError,
    AITimeoutError,
)
from ai_server.auth import require_api_key, require_signed_service_request
from ai_server.request_state import delete_state, get_state, put_state
from ai_server.sessions import (
    delete_session,
    load_session,
    session_exists,
)
from aws_client_impl import get_client_impl as get_storage_client_impl
from nimbus_runtime import (
    NimbusRuntime,
    TurnAttachment,
    get_session_lock,
    runtime_telemetry,
)

log: Any = structlog.get_logger()

router = APIRouter(tags=["ai"])

_DEFAULT_SESSION_DIR = Path.home() / ".nimbus" / "sessions" / "ai_server"
ActionStateKind = Literal[
    "list_files",
    "get_file_info",
    "upload_attachment",
    "delete_file",
    "summarize_prefix",
    "spawn_child_session",
]
ActionStateStatus = Literal[
    "proposed",
    "awaiting_confirmation",
    "authorized",
    "queued",
    "executing",
    "verifying",
    "succeeded",
    "failed_retryable",
    "failed_terminal",
    "expired",
    "cancelled",
]
ArtifactStateKind = Literal["delete_report", "upload_report"]

# ---------------------------------------------------------------------------
# Per-principal rate limiting (FM10) — token bucket keyed by caller identity
# ---------------------------------------------------------------------------
#
# Capacity and refill rate are intentionally conservative for a shared
# free-tier system.  Override via env vars ``AI_RATE_LIMIT_CAPACITY`` and
# ``AI_RATE_LIMIT_RPM`` if needed.

_RATE_LIMIT_CAPACITY: int = int(os.environ.get("AI_RATE_LIMIT_CAPACITY", "10"))
_RATE_LIMIT_RPM: float = float(os.environ.get("AI_RATE_LIMIT_RPM", "10"))
# Convert RPM → tokens-per-second for the refill arithmetic.
_RATE_LIMIT_REFILL_RATE: float = _RATE_LIMIT_RPM / 60.0
_IDEMPOTENCY_TTL_SECONDS = int(os.environ.get("AI_IDEMPOTENCY_TTL_SECONDS", "3600"))
_IDEMPOTENCY_RECORD_SCHEMA_VERSION = 1
_IDEMPOTENT_TURN_STATE_NAMESPACE = "idempotent_turns"
_SAFE_CHAT_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,64}$"
_SAFE_ATTACHMENT_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"
_SAFE_CONTENT_TYPE_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$"
)
_SAFE_SHA256_HEX_PATTERN = r"^[A-Fa-f0-9]{64}$"
_MAX_ATTACHMENTS_PER_TURN = 10
_MAX_ATTACHMENT_SIZE_BYTES = 20 * 1024 * 1024
_MAX_INLINE_ATTACHMENT_BYTES_PER_TURN = 20 * 1024 * 1024
_MAX_ATTACHMENT_BASE64_LENGTH = ((_MAX_ATTACHMENT_SIZE_BYTES + 2) // 3) * 4


def _decoded_base64_size(content_base64: str) -> int:
    """Return the decoded byte length implied by a base64 payload string."""
    padding = len(content_base64) - len(content_base64.rstrip("="))
    return (len(content_base64) // 4) * 3 - padding


@dataclass
class _TokenBucket:
    """Simple token-bucket state for one caller principal."""

    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


@dataclass
class _CachedTurnResponse:
    """Cached wrapper-facing response for best-effort idempotent replay."""

    response: ChatTurnResponse
    request_fingerprint: str
    expires_at: float


@dataclass(frozen=True)
class _CachedTurnLookup:
    """Lookup result for a cached wrapper-facing turn response."""

    response: ChatTurnResponse | None
    backend: Literal["memory", "persistent"] | None = None


class _IdempotencyConflictError(ValueError):
    """Raised when a key is reused for a different logical request."""


# Module-level registry.  CPython dict operations are GIL-atomic; safe in a
# single-event-loop async server.  Entries accumulate (one per unique caller
# principal) but are small objects — bounded by the number of active users.
_rate_buckets: dict[str, _TokenBucket] = {}
_idempotent_turns: dict[str, _CachedTurnResponse] = {}


def _cleanup_rate_buckets(now: float) -> None:
    """Drop idle token buckets once they are equivalent to a fresh bucket."""
    if _RATE_LIMIT_REFILL_RATE <= 0:
        return
    full_refill_seconds = float(_RATE_LIMIT_CAPACITY) / _RATE_LIMIT_REFILL_RATE
    expired_keys = [
        principal_key
        for principal_key, bucket in _rate_buckets.items()
        if (now - bucket.last_refill) >= full_refill_seconds
    ]
    for principal_key in expired_keys:
        del _rate_buckets[principal_key]


def _check_rate_limit(principal_key: str | None, *, _now: float | None = None) -> bool:
    """Return ``True`` if the request is allowed, ``False`` if over the limit.

    Requests without a principal key are always allowed (backwards-compatible for
    callers that omit the field).  The bucket is replenished continuously at
    ``_RATE_LIMIT_REFILL_RATE`` tokens/second up to ``_RATE_LIMIT_CAPACITY``.

    Args:
        principal_key: Caller identity key used to isolate per-principal buckets.
        _now: Monotonic clock value to use instead of ``time.monotonic()``.
            Intended for testing only; production callers must omit this parameter.

    """
    if principal_key is None:
        return True
    now = _now if _now is not None else time.monotonic()
    _cleanup_rate_buckets(now)
    bucket = _rate_buckets.get(principal_key)
    if bucket is None:
        # First request: consume one token immediately, start full - 1.
        _rate_buckets[principal_key] = _TokenBucket(
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


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ChatAttachmentReference(BaseModel):
    """Wrapper-owned attachment metadata for one chat turn.

    This is intentionally a metadata-only reference model. The wrapper remains
    responsible for Slack file fetches and for any future byte-carrying upload
    flow. Nimbus currently uses these references to reason about attached files
    safely without dereferencing arbitrary external URLs.
    """

    platform_file_id: str = Field(
        description="Wrapper-provided platform file identifier, e.g. a Slack file ID.",
        min_length=1,
        max_length=128,
        pattern=_SAFE_ATTACHMENT_ID_PATTERN,
    )
    filename: str = Field(
        description="Original attachment filename from the chat platform.",
        min_length=1,
        max_length=255,
    )
    content_type: str = Field(
        description="Attachment MIME type, e.g. 'text/csv'.",
        min_length=3,
        max_length=128,
        pattern=_SAFE_CONTENT_TYPE_PATTERN,
    )
    size_bytes: int = Field(
        description="Declared attachment size in bytes.",
        ge=1,
        le=_MAX_ATTACHMENT_SIZE_BYTES,
    )
    content_base64: str | None = Field(
        default=None,
        description=(
            "Optional base64-encoded attachment bytes for runtime-managed "
            "upload flows. Metadata-only turns should omit this field."
        ),
        max_length=_MAX_ATTACHMENT_BASE64_LENGTH,
    )
    sha256_hex: str | None = Field(
        default=None,
        description="Optional SHA-256 hex digest of the decoded attachment bytes.",
        pattern=_SAFE_SHA256_HEX_PATTERN,
    )


class ChatTurnRequest(BaseModel):
    """Canonical wrapper-facing request body for one chat turn."""

    platform: str = Field(
        description="Chat platform name, e.g. 'slack'.",
        min_length=1,
        max_length=16,
        pattern=r"^[a-z][a-z0-9_-]{0,15}$",
    )
    workspace_id: str = Field(
        description="Workspace/team identifier from the chat platform.",
        min_length=1,
        max_length=64,
        pattern=_SAFE_CHAT_ID_PATTERN,
    )
    channel_id: str = Field(
        description="Channel or DM identifier from the chat platform.",
        min_length=1,
        max_length=64,
        pattern=_SAFE_CHAT_ID_PATTERN,
    )
    thread_id: str | None = Field(
        default=None,
        description=(
            "Conversation/thread anchor from the chat platform. If omitted, "
            "the message_id becomes the conversation anchor."
        ),
        max_length=64,
        pattern=_SAFE_CHAT_ID_PATTERN,
    )
    message_id: str = Field(
        description="Unique source message identifier from the chat platform.",
        min_length=1,
        max_length=64,
        pattern=_SAFE_CHAT_ID_PATTERN,
    )
    user_id: str = Field(
        description="Platform user identifier for the actor who sent the turn.",
        min_length=1,
        max_length=64,
        pattern=_SAFE_CHAT_ID_PATTERN,
    )
    text: str = Field(
        description="Plain-text message body to send to Nimbus.",
        min_length=1,
        max_length=4096,
    )
    idempotency_key: str = Field(
        description="Caller-generated idempotency key for safe retries.",
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9_.:-]{1,256}$",
    )
    request_id: str | None = Field(
        default=None,
        description="Optional caller-generated correlation/request ID.",
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]{1,128}$",
    )
    attachments: list[ChatAttachmentReference] = Field(
        default_factory=list,
        description=(
            "Optional wrapper-owned attachment references for this turn. These "
            "are validated metadata records, not arbitrary download URLs or raw "
            "filesystem paths."
        ),
        max_length=_MAX_ATTACHMENTS_PER_TURN,
    )

    @model_validator(mode="after")
    def _validate_inline_attachment_budget(self) -> ChatTurnRequest:
        inline_decoded_bytes = sum(
            _decoded_base64_size(attachment.content_base64)
            for attachment in self.attachments
            if attachment.content_base64 is not None
        )
        if inline_decoded_bytes > _MAX_INLINE_ATTACHMENT_BYTES_PER_TURN:
            msg = (
                "attachments carrying inline bytes exceed the per-turn decoded "
                f"size cap of {_MAX_INLINE_ATTACHMENT_BYTES_PER_TURN} bytes"
            )
            raise ValueError(msg)
        return self


class ConfirmationState(BaseModel):
    """Explicit confirmation state returned to wrapper callers."""

    action_id: str = Field(description="Stable runtime-generated pending action ID.")
    kind: Literal["delete_file"] = Field(
        description="Destructive action kind that requires confirmation."
    )
    prompt: str = Field(
        description="Exact confirmation prompt for the wrapper to post."
    )
    expected_reply: str = Field(
        description="Exact text reply the same actor must send to confirm."
    )
    expires_at: str = Field(description="UTC timestamp when this confirmation expires.")


class ActionState(BaseModel):
    """Small wrapper-visible summary of a durable Nimbus action."""

    action_id: str = Field(description="Stable runtime-generated action ID.")
    kind: ActionStateKind = Field(description="Nimbus action kind, e.g. 'delete_file'.")
    status: ActionStateStatus = Field(description="Current durable action state.")
    target: dict[str, object] | None = Field(
        default=None,
        description="Provider target for this action, when one exists.",
    )


class ArtifactState(BaseModel):
    """Small wrapper-visible summary of a Nimbus artifact."""

    artifact_id: str = Field(description="Stable runtime-generated artifact ID.")
    kind: ArtifactStateKind = Field(description="Artifact kind, e.g. 'upload_report'.")
    action_id: str | None = Field(
        default=None,
        description="Action that produced this artifact, if applicable.",
    )
    payload: dict[str, object] | None = Field(
        default=None,
        description="Small inline artifact payload for wrapper display.",
    )


class ChatTurnResponse(BaseModel):
    """Canonical wrapper-facing response body for one chat turn."""

    request_id: str = Field(description="Correlation/request ID for this turn.")
    conversation_id: str = Field(
        description="Normalized conversation identity used by Nimbus state."
    )
    text: str = Field(description="Reply text for the wrapper to post back.")
    outcome: Literal["reply", "confirmation_required", "partial_success", "error"] = (
        Field(description="Machine-readable outcome class for the turn.")
    )
    confirmation_required: bool = Field(
        description="Whether the wrapper should treat this as a confirmation prompt."
    )
    confirmation: ConfirmationState | None = Field(
        default=None,
        description="Explicit runtime-managed confirmation state for destructive work.",
    )
    suggested_next_actions: list[str] = Field(
        default_factory=list,
        description="Optional safe follow-up suggestions for the user.",
    )
    actions: list[ActionState] = Field(
        default_factory=list,
        description="Durable Nimbus actions created or updated by this turn.",
    )
    artifacts: list[ArtifactState] = Field(
        default_factory=list,
        description="Artifacts created by this turn, such as verification reports.",
    )
    model: str = Field(
        description=(
            "AI model name, or `nimbus-runtime` for direct runtime-managed actions."
        )
    )
    steps: int = Field(
        description=(
            "Number of model-call rounds taken; zero for direct runtime-managed "
            "actions."
        )
    )
    fallback_used: bool = Field(
        description="True if the primary model failed and the fallback was used."
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


def get_storage_client() -> CloudStorageClient:
    """Construct the cloud-storage client used by wrapper chat tools."""
    return get_storage_client_impl()


def get_nimbus_runtime(
    client: Annotated[OpenRouterClient, Depends(get_ai_client)],
    storage: Annotated[CloudStorageClient, Depends(get_storage_client)],
) -> NimbusRuntime:
    """Construct the shared Nimbus runtime used by HTTP handlers."""
    return NimbusRuntime(
        ai_client=client,
        storage=storage,
        session_dir=_session_dir(),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        tool_container=_tool_container(),
        telemetry=runtime_telemetry,
    )


def _session_dir() -> Path:
    """Return the session-storage directory, reading ``AI_SESSION_DIR`` at call time."""
    raw = os.environ.get("AI_SESSION_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_SESSION_DIR


def _tool_container() -> str | None:
    """Return the container pinned to wrapper chat tools, if configured."""
    for env_name in ("NIMBUS_CONTAINER", "AWS_BUCKET_NAME"):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return None


def _wrapper_principal_key(req: ChatTurnRequest) -> str:
    """Return the rate-limit principal for a wrapper-facing chat turn."""
    return f"{req.platform}:{req.workspace_id}:{req.user_id}"


def _compose_conversation_id(req: ChatTurnRequest) -> str:
    """Return the normalized Nimbus conversation ID for a wrapper turn."""
    anchor = req.thread_id or req.message_id
    return f"{req.platform}:{req.workspace_id}:{req.channel_id}:{anchor}"


def _new_request_id(explicit_request_id: str | None) -> str:
    """Return the caller-supplied request ID or generate a new one."""
    if explicit_request_id:
        return explicit_request_id
    return f"req-{uuid.uuid4().hex}"


def _sha256_json(payload: dict[str, object]) -> str:
    """Return a stable digest for a JSON-compatible payload."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256-{hashlib.sha256(encoded).hexdigest()}"


def _idempotency_cache_key(req: ChatTurnRequest, *, conversation_id: str) -> str:
    """Return the actor-scoped cache key for best-effort idempotent replay."""
    return _sha256_json(
        {
            "schema_version": _IDEMPOTENCY_RECORD_SCHEMA_VERSION,
            "platform": req.platform,
            "workspace_id": req.workspace_id,
            "user_id": req.user_id,
            "conversation_id": conversation_id,
            "idempotency_key": req.idempotency_key,
        }
    )


def _idempotency_request_fingerprint(
    req: ChatTurnRequest,
    *,
    conversation_id: str,
) -> str:
    """Return a stable fingerprint for the logical request parameters."""
    body = req.model_dump(mode="json", exclude={"request_id"})
    body["conversation_id"] = conversation_id
    body["schema_version"] = _IDEMPOTENCY_RECORD_SCHEMA_VERSION
    return _sha256_json(body)


def _idempotency_record(
    *,
    response: ChatTurnResponse,
    request_fingerprint: str,
) -> dict[str, object]:
    """Return the persistent value stored for one idempotent turn."""
    return {
        "schema_version": _IDEMPOTENCY_RECORD_SCHEMA_VERSION,
        "request_fingerprint": request_fingerprint,
        "response": response.model_dump(mode="json"),
    }


def _response_from_idempotency_record(
    value: dict[str, object],
    *,
    expected_fingerprint: str,
) -> ChatTurnResponse | None:
    """Validate and decode one persistent idempotency record."""
    if value.get("schema_version") != _IDEMPOTENCY_RECORD_SCHEMA_VERSION:
        return None
    stored_fingerprint = value.get("request_fingerprint")
    if not isinstance(stored_fingerprint, str):
        return None
    if stored_fingerprint != expected_fingerprint:
        msg = "idempotency key was reused with different request parameters"
        raise _IdempotencyConflictError(msg)
    response = value.get("response")
    if not isinstance(response, dict):
        return None
    return ChatTurnResponse.model_validate(response)


def _get_cached_turn_response(
    cache_key: str,
    *,
    request_fingerprint: str,
) -> _CachedTurnLookup:
    """Return an unexpired cached turn response, if present."""
    now = time.monotonic()
    expired = [
        key for key, entry in _idempotent_turns.items() if entry.expires_at <= now
    ]
    for key in expired:
        del _idempotent_turns[key]
    entry = _idempotent_turns.get(cache_key)
    if entry is not None:
        if entry.request_fingerprint != request_fingerprint:
            msg = "idempotency key was reused with different request parameters"
            raise _IdempotencyConflictError(msg)
        return _CachedTurnLookup(response=entry.response, backend="memory")

    persisted = get_state(_IDEMPOTENT_TURN_STATE_NAMESPACE, cache_key)
    if persisted.cleaned_entries:
        log.info(
            "idempotency_state_cleaned",
            cache_key=cache_key,
            cleaned_entries=persisted.cleaned_entries,
        )
    if persisted.value is None:
        return _CachedTurnLookup(response=None)
    try:
        response = _response_from_idempotency_record(
            persisted.value,
            expected_fingerprint=request_fingerprint,
        )
    except _IdempotencyConflictError:
        raise
    except (TypeError, ValueError, ValidationError):
        delete_state(_IDEMPOTENT_TURN_STATE_NAMESPACE, cache_key)
        log.warning("idempotency_state_invalid_entry", cache_key=cache_key)
        return _CachedTurnLookup(response=None)
    if response is None:
        delete_state(_IDEMPOTENT_TURN_STATE_NAMESPACE, cache_key)
        log.warning("idempotency_state_invalid_entry", cache_key=cache_key)
        return _CachedTurnLookup(response=None)

    _idempotent_turns[cache_key] = _CachedTurnResponse(
        response=response,
        request_fingerprint=request_fingerprint,
        expires_at=time.monotonic() + float(_IDEMPOTENCY_TTL_SECONDS),
    )
    return _CachedTurnLookup(response=response, backend="persistent")


def _store_cached_turn_response(
    cache_key: str,
    *,
    request_fingerprint: str,
    response: ChatTurnResponse,
) -> None:
    """Cache a wrapper-facing response for best-effort idempotent replay."""
    _idempotent_turns[cache_key] = _CachedTurnResponse(
        response=response,
        request_fingerprint=request_fingerprint,
        expires_at=time.monotonic() + float(_IDEMPOTENCY_TTL_SECONDS),
    )
    cleaned_entries = put_state(
        _IDEMPOTENT_TURN_STATE_NAMESPACE,
        cache_key,
        value=_idempotency_record(
            response=response,
            request_fingerprint=request_fingerprint,
        ),
        expires_at=time.time() + float(_IDEMPOTENCY_TTL_SECONDS),
    )
    if cleaned_entries:
        log.info(
            "idempotency_state_cleaned",
            cache_key=cache_key,
            cleaned_entries=cleaned_entries,
        )


def _runtime_turn_input(
    *, request_id: str, conversation_id: str, req: ChatTurnRequest
) -> RuntimeChatTurnInput:
    return RuntimeChatTurnInput(
        request_id=request_id,
        conversation_id=conversation_id,
        platform=req.platform,
        workspace_id=req.workspace_id,
        channel_id=req.channel_id,
        thread_id=req.thread_id,
        message_id=req.message_id,
        user_id=req.user_id,
        text=req.text,
        idempotency_key=req.idempotency_key,
        attachments=tuple(
            TurnAttachment(
                platform_file_id=attachment.platform_file_id,
                filename=attachment.filename,
                content_type=attachment.content_type,
                size_bytes=attachment.size_bytes,
                content_base64=attachment.content_base64,
                sha256_hex=attachment.sha256_hex,
            )
            for attachment in req.attachments
        ),
    )


def _raise_ai_http_exception(exc: Exception) -> None:
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    if isinstance(exc, AIClientConfigError):
        log.exception("ai_config_error", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI service is misconfigured — check server logs.",
        ) from exc
    if isinstance(exc, AIRateLimitError):
        log.warning("ai_rate_limit", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI provider is rate-limited.  Try again shortly.",
        ) from exc
    if isinstance(exc, AITimeoutError):
        log.warning("ai_timeout", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="AI provider timed out.",
        ) from exc
    if isinstance(exc, AIStepBudgetExceededError):
        log.warning("ai_step_budget_exceeded", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="AI required too many steps.  Simplify the request.",
        ) from exc
    if isinstance(exc, (AIProviderError, AIClientError)):
        log.exception("ai_provider_error", detail=str(exc))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Upstream AI provider error.",
        ) from exc
    raise exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — no authentication required."""
    return {"status": "ok", "service": "ai-server"}


@router.post("/chat/turn", response_model=ChatTurnResponse)
async def chat_turn(
    req: ChatTurnRequest,
    _auth: Annotated[str, Depends(require_signed_service_request)],
    runtime: Annotated[NimbusRuntime, Depends(get_nimbus_runtime)],
) -> ChatTurnResponse:
    """Accept one canonical wrapper-facing chat turn and return a reply.

    This endpoint exists for chat wrappers such as the future Slack app. It
    derives the internal Nimbus conversation ID from the wrapper's normalized
    transport identifiers and uses signed request authentication instead of the
    legacy shared API key.
    """
    started = time.monotonic()
    request_id = _new_request_id(req.request_id)
    conversation_id = _compose_conversation_id(req)
    cache_key = _idempotency_cache_key(req, conversation_id=conversation_id)
    request_fingerprint = _idempotency_request_fingerprint(
        req,
        conversation_id=conversation_id,
    )

    try:
        cached = _get_cached_turn_response(
            cache_key,
            request_fingerprint=request_fingerprint,
        )
    except _IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if cached.response is not None:
        log.info(
            "chat_turn_idempotent_replay",
            request_id=request_id,
            conversation_id=conversation_id,
            idempotency_key=req.idempotency_key,
            state_backend=cached.backend,
        )
        runtime_telemetry.record_idempotent_replay(backend=str(cached.backend))
        runtime_telemetry.record_wrapper_turn(
            platform=req.platform,
            outcome=cached.response.outcome,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return cached.response

    rate_limit_key = _wrapper_principal_key(req)
    if not _check_rate_limit(rate_limit_key):
        log.warning(
            "rate_limit_exceeded",
            user_id=req.user_id,
            rate_limit_key=rate_limit_key,
        )
        runtime_telemetry.record_wrapper_turn(
            platform=req.platform,
            outcome="error",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Per-user rate limit exceeded. Try again shortly.",
        )
    try:
        turn_result = await runtime.run_chat_turn(
            _runtime_turn_input(
                request_id=request_id,
                conversation_id=conversation_id,
                req=req,
            )
        )
    except Exception as exc:  # noqa: BLE001 - map known AI/runtime errors below
        runtime_telemetry.record_wrapper_turn(
            platform=req.platform,
            outcome="error",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        _raise_ai_http_exception(exc)

    response = ChatTurnResponse(
        request_id=turn_result.request_id,
        conversation_id=turn_result.conversation_id,
        text=turn_result.text,
        outcome=turn_result.outcome,
        confirmation_required=turn_result.confirmation_required,
        confirmation=(
            None
            if turn_result.confirmation is None
            else ConfirmationState(
                action_id=turn_result.confirmation.action_id,
                kind=turn_result.confirmation.kind,
                prompt=turn_result.confirmation.prompt,
                expected_reply=turn_result.confirmation.expected_reply,
                expires_at=turn_result.confirmation.expires_at,
            )
        ),
        suggested_next_actions=list(turn_result.suggested_next_actions),
        actions=[
            ActionState(
                action_id=action.action_id,
                kind=cast("ActionStateKind", action.kind),
                status=cast("ActionStateStatus", action.status),
                target=None if action.target is None else dict(action.target),
            )
            for action in turn_result.actions
        ],
        artifacts=[
            ArtifactState(
                artifact_id=artifact.artifact_id,
                kind=cast("ArtifactStateKind", artifact.kind),
                action_id=artifact.action_id,
                payload=None if artifact.payload is None else dict(artifact.payload),
            )
            for artifact in turn_result.artifacts
        ],
        model=turn_result.model,
        steps=turn_result.steps,
        fallback_used=turn_result.fallback_used,
    )
    _store_cached_turn_response(
        cache_key,
        request_fingerprint=request_fingerprint,
        response=response,
    )
    return response


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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    # load_session returns a fresh (empty) Conversation when the file does
    # not exist — distinguish that from a real empty session by checking
    # whether a file is actually present.
    if not session_exists(session_dir, session_id):
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

    Acquires the per-session lock to prevent a concurrent turn from writing a
    stale session back after the delete.

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
        async with get_session_lock(session_id):
            removed = delete_session(session_dir, session_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    log.info("session_deleted", session_id=session_id, existed=removed)
    return SessionDeleteResponse(deleted=removed, session_id=session_id)
