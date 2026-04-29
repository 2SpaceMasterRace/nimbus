"""Shared Nimbus runtime for wrapper-facing chat turns."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
import weakref
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import structlog

from ai_client_api import (
    AIClient,
    AIClientConfigError,
    AIClientError,
    AIProviderError,
    AIRateLimitError,
    AIResponse,
    AIStepBudgetExceededError,
    AIStreamEvent,
    AITimeoutError,
    Conversation,
    Tool,
)
from nimbus_protocol import NimbusEvent, StreamEventType
from nimbus_runtime.domain import (
    Action,
    ActionFailure,
    ActionKind,
    ActionStatus,
    ActionTransition,
    ActorAuthSource,
    Artifact,
    ArtifactPayload,
    DeleteFileInput,
    DeleteFileResult,
    DeleteReport,
    ObjectRef,
    SessionEvent,
    TenantIdentity,
    UploadAttachmentInput,
    UploadAttachmentResult,
    UploadReport,
    VerifiedActor,
)
from nimbus_runtime.models import (
    ActionSummary,
    ArtifactSummary,
    ChatTurnInput,
    ChatTurnResult,
    ConfirmationDetails,
    TurnAttachment,
    TurnOutcome,
)
from nimbus_runtime.policy import PolicyContext, PolicyDecision, authorize_action
from nimbus_runtime.postgres import load_session as load_postgres_session
from nimbus_runtime.postgres import postgres_enabled
from nimbus_runtime.postgres import save_session as save_postgres_session
from nimbus_runtime.slack_tools import build_slack_tools
from nimbus_runtime.stores import (
    ActionStore,
    ArtifactStore,
    FileActionStore,
    FileArtifactStore,
    FileSessionEventStore,
    PostgresActionStore,
    PostgresArtifactStore,
    PostgresSessionEventStore,
    SessionEventStore,
)
from nimbus_runtime.telemetry import RuntimeTelemetry, runtime_telemetry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from cloud_storage_api import CloudStorageClient

log: Any = structlog.get_logger()

_PENDING_DELETE_TTL_SECONDS = int(
    os.environ.get("NIMBUS_PENDING_DELETE_TTL_SECONDS", "900")
)
_RUNTIME_MODEL_NAME = "nimbus-runtime"
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:]+$")
_MAX_SESSION_FILENAME_STEM_LENGTH = 128
_HASHED_SESSION_STEM_PREFIX = "sha256-"
_MAX_FAILURE_DETAILS = 3
_MIN_QUOTED_TEXT_LENGTH = 2
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
_DELETE_REQUEST_RE = re.compile(r"^\s*delete\s+(?P<remote_path>.+?)\s*$", re.IGNORECASE)
_DELETE_CONFIRM_RE = re.compile(
    r"^\s*yes\s*,\s*delete\s+(?P<remote_path>.+?)\s*$",
    re.IGNORECASE,
)
_UPLOAD_REQUEST_RE = re.compile(
    r"^\s*upload\s+(?:all\s+files\s+in\s+this\s+channel|these\s+files|attached\s+files)\s+to\s+(?P<prefix>.+?)\s*$",
    re.IGNORECASE,
)

_session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)


@dataclass(slots=True)
class _UploadBatch:
    """Accumulated outcome for one multi-attachment upload turn."""

    uploaded: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    actions: list[ActionSummary] = field(default_factory=list)
    artifacts: list[ArtifactSummary] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _UploadAttempt:
    """Outcome for one attachment in an upload turn."""

    uploaded_path: str | None
    failure: str | None
    action: Action
    artifact: Artifact | None = None


@dataclass(frozen=True, slots=True)
class _UploadWork:
    """Stable context for one attachment upload action."""

    turn: ChatTurnInput
    tenant: TenantIdentity
    actor: VerifiedActor
    attachment: TurnAttachment
    remote_path: str


@dataclass(frozen=True, slots=True)
class _ArtifactDraft:
    """Artifact fields that are known before the artifact ID exists."""

    tenant: TenantIdentity
    session_id: str
    action_id: str | None
    kind: Literal["delete_report", "upload_report"]
    payload: ArtifactPayload


def get_session_lock(session_id: str) -> asyncio.Lock:
    """Return the shared per-session lock, creating it on first use."""
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
    return lock


def _validate_session_id(session_id: str) -> None:
    if not session_id or not _SAFE_SESSION_ID_RE.match(session_id):
        msg = (
            f"session_id {session_id!r} contains unsafe characters. Only "
            "alphanumerics, hyphens, underscores, dots, and colons are allowed."
        )
        raise ValueError(msg)


def _session_file_stem(session_id: str) -> str:
    _validate_session_id(session_id)
    if len(session_id) <= _MAX_SESSION_FILENAME_STEM_LENGTH:
        return session_id
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"{_HASHED_SESSION_STEM_PREFIX}{digest}"


def _session_path(session_dir: Path, session_id: str) -> Path:
    return session_dir / f"{_session_file_stem(session_id)}.json"


def _load_session(
    session_dir: Path,
    session_id: str,
    system_prompt: str,
) -> Conversation:
    """Load a persisted conversation or create a fresh one."""
    if postgres_enabled():
        return load_postgres_session(session_id, system_prompt)
    _validate_session_id(session_id)
    path = _session_path(session_dir, session_id)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Conversation.from_json(data)
        except (ValueError, TypeError, KeyError, OSError):
            pass
    return Conversation(system=system_prompt, session_id=session_id)


def _save_session(session_dir: Path, session_id: str, conv: Conversation) -> None:
    if postgres_enabled():
        save_postgres_session(session_id, conv)
        return
    _validate_session_id(session_id)
    path = _session_path(session_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(conv.to_json(), indent=2), encoding="utf-8")
    tmp.replace(path)


def _message_with_attachment_context(
    *, text: str, attachments: tuple[TurnAttachment, ...]
) -> str:
    if not attachments:
        return text
    attachment_block = json.dumps(
        [
            {
                "platform_file_id": attachment.platform_file_id,
                "filename": attachment.filename,
                "content_type": attachment.content_type,
                "size_bytes": attachment.size_bytes,
            }
            for attachment in attachments
        ],
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    )
    return (
        f"{text}\n\n"
        "Wrapper-provided attachments for this turn (metadata only):\n"
        f"{attachment_block}"
    )


def _strip_wrapping_quotes(text: str) -> str:
    stripped = text.strip().strip("`")
    if (
        len(stripped) >= _MIN_QUOTED_TEXT_LENGTH
        and stripped[0] == stripped[-1]
        and stripped[0] in {'"', "'"}
    ):
        return stripped[1:-1].strip()
    return stripped


def _extract_delete_target(text: str) -> str | None:
    match = _DELETE_REQUEST_RE.match(text)
    if match is None:
        return None
    remote_path = _strip_wrapping_quotes(match.group("remote_path"))
    return remote_path or None


def _extract_delete_confirmation(text: str) -> str | None:
    match = _DELETE_CONFIRM_RE.match(text)
    if match is None:
        return None
    remote_path = _strip_wrapping_quotes(match.group("remote_path"))
    return remote_path or None


def _extract_upload_prefix(text: str) -> str | None:
    match = _UPLOAD_REQUEST_RE.match(text)
    if match is None:
        return None
    prefix = _strip_wrapping_quotes(match.group("prefix"))
    return prefix or None


def _ai_error_kind(exc: Exception) -> str:
    for exc_type, label in (
        (AIClientConfigError, "config_error"),
        (AIRateLimitError, "rate_limit"),
        (AITimeoutError, "timeout"),
        (AIStepBudgetExceededError, "step_budget_exceeded"),
        (AIProviderError, "provider_error"),
        (AIClientError, "client_error"),
    ):
        if isinstance(exc, exc_type):
            return label
    return exc.__class__.__name__.lower()


def _artifact_payload_to_summary(payload: ArtifactPayload) -> dict[str, object]:
    """Return a small JSON-safe artifact payload for wrapper responses."""
    if isinstance(payload, DeleteReport):
        return {
            "remote_path": payload.remote_path,
            "deleted": payload.deleted,
            "version_id": payload.version_id,
        }
    if isinstance(payload, UploadReport):
        return {
            "remote_path": payload.remote_path,
            "filename": payload.filename,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
        }
    msg = f"unsupported artifact payload: {type(payload).__name__}"
    raise TypeError(msg)


def _protocol_event(event: SessionEvent) -> NimbusEvent:
    """Project one durable runtime event into the public protocol shape."""
    turn_id = event.payload.get("request_id")
    return NimbusEvent(
        session_id=event.session_id,
        sequence=event.sequence,
        event_id=event.event_id,
        event_type=event.event_type,
        payload=dict(event.payload),
        turn_id=turn_id if isinstance(turn_id, str) else None,
        created_at=event.created_at.astimezone(UTC).isoformat(),
    )


def _json_safe_stream_payload(event: AIStreamEvent) -> dict[str, object]:
    """Return an event-store-safe payload for one provider stream event."""
    return {
        "provider_sequence": event.sequence,
        **{key: _json_safe_value(value) for key, value in event.payload.items()},
    }


def _ai_response_to_payload(response: AIResponse) -> dict[str, object]:
    """Serialize an ``AIResponse`` into a replayable protocol payload."""
    return {
        "text": response.text,
        "model": response.model,
        "tokens": {
            "input_tokens": response.tokens.input_tokens,
            "output_tokens": response.tokens.output_tokens,
            "total": response.tokens.total,
        },
        "tool_calls": [
            {
                "id": call.id,
                "name": call.name,
                "arguments": _json_safe_value(call.arguments),
                "result_summary": call.result_summary,
                "success": call.success,
                "latency_ms": call.latency_ms,
            }
            for call in response.tool_calls
        ],
        "latency_ms": response.latency_ms,
        "stop_reason": response.stop_reason,
        "steps": response.steps,
        "fallback_used": response.fallback_used,
    }


def _json_safe_value(value: object) -> object:
    """Convert common provider payload values into JSON-compatible values."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, AIResponse):
        return _ai_response_to_payload(value)
    if isinstance(value, MappingABC):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, SequenceABC) and not isinstance(
        value,
        bytes | bytearray | str,
    ):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, set | frozenset):
        return [_json_safe_value(item) for item in value]
    return str(value)


class NimbusRuntime:
    """Shared runtime that owns session orchestration and chat-safe actions."""

    def __init__(  # noqa: PLR0913 - explicit injected dependencies keep boundaries obvious
        self,
        *,
        ai_client: AIClient,
        storage: CloudStorageClient | None,
        session_dir: Path,
        system_prompt: str,
        tool_container: str | None,
        telemetry: RuntimeTelemetry | None = None,
        event_store: SessionEventStore | None = None,
        action_store: ActionStore | None = None,
        artifact_store: ArtifactStore | None = None,
        model_turns_enabled: bool = True,
        storage_tools_enabled: bool = True,
        delete_actions_enabled: bool = True,
        attachment_uploads_enabled: bool = True,
    ) -> None:
        """Construct the runtime with its injected dependencies."""
        self._ai_client = ai_client
        self._storage = storage
        self._session_dir = session_dir
        self._system_prompt = system_prompt
        self._tool_container = tool_container
        self._telemetry = telemetry or runtime_telemetry
        self._model_turns_enabled = model_turns_enabled
        self._storage_tools_enabled = storage_tools_enabled
        self._delete_actions_enabled = delete_actions_enabled
        self._attachment_uploads_enabled = attachment_uploads_enabled
        if event_store is not None:
            self._event_store = event_store
        elif postgres_enabled():
            self._event_store = PostgresSessionEventStore()
        else:
            self._event_store = FileSessionEventStore(session_dir)
        if action_store is not None:
            self._action_store = action_store
        elif postgres_enabled():
            self._action_store = PostgresActionStore(event_store=self._event_store)
        else:
            self._action_store = FileActionStore(
                session_dir,
                event_store=self._event_store,
            )
        if artifact_store is not None:
            self._artifact_store = artifact_store
        elif postgres_enabled():
            self._artifact_store = PostgresArtifactStore(event_store=self._event_store)
        else:
            self._artifact_store = FileArtifactStore(
                session_dir,
                event_store=self._event_store,
            )

    async def run_text_chat(
        self,
        *,
        message: str,
        session_id: str,
        user_id: str | None,
        tools: list[Tool] | None = None,
    ) -> AIResponse:
        """Run one legacy text chat interaction against the persisted session."""
        del user_id
        started = time.monotonic()
        async with get_session_lock(session_id):
            try:
                ai_response = await self._run_ai_interaction(
                    message=message,
                    session_id=session_id,
                    tools=tools,
                )
            except Exception:
                self._telemetry.record_wrapper_turn(
                    platform="api",
                    outcome="error",
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                raise
        self._telemetry.record_wrapper_turn(
            platform="api",
            outcome="reply",
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return ai_response

    async def run_chat_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
        """Run one wrapper-facing turn through the shared Nimbus runtime."""
        started = time.monotonic()
        result: ChatTurnResult | None = None
        async with get_session_lock(turn.conversation_id):
            pending = self._load_pending_delete(turn)
            confirmation_target = _extract_delete_confirmation(turn.text)
            delete_target = _extract_delete_target(turn.text)
            upload_prefix = _extract_upload_prefix(turn.text)

            if confirmation_target is not None:
                result = await self._handle_delete_confirmation(
                    turn=turn,
                    pending=pending,
                    confirmation_target=confirmation_target,
                )
            elif pending is not None and delete_target is not None:
                if (
                    pending.actor.user_id == turn.user_id
                    and self._delete_remote_path(pending) == delete_target
                ):
                    result = await self._persist_direct_result(
                        turn=turn,
                        text=self._delete_prompt(pending),
                        outcome="confirmation_required",
                        suggested_next_actions=(self._delete_expected_reply(pending),),
                        confirmation=self._delete_confirmation(pending),
                        actions=(self._action_summary(pending),),
                    )
                else:
                    pending_remote_path = self._delete_remote_path(pending)
                    result = await self._persist_direct_result(
                        turn=turn,
                        text=(
                            "This conversation already has a pending destructive "
                            f"action for `{pending_remote_path}`. Confirm or wait "
                            "for it to expire before starting another delete."
                        ),
                        outcome="error",
                        suggested_next_actions=(self._delete_expected_reply(pending),),
                        actions=(self._action_summary(pending),),
                    )
            elif delete_target is not None:
                if not self._delete_actions_enabled:
                    result = await self._persist_direct_result(
                        turn=turn,
                        text="Delete actions are temporarily disabled.",
                        outcome="error",
                    )
                    return self._record_result(
                        turn=turn,
                        result=result,
                        started=started,
                    )
                result = await self._create_pending_delete(
                    turn=turn,
                    remote_path=delete_target,
                )
            elif upload_prefix is not None:
                if not self._attachment_uploads_enabled:
                    result = await self._persist_direct_result(
                        turn=turn,
                        text="Attachment uploads are temporarily disabled.",
                        outcome="error",
                    )
                    return self._record_result(
                        turn=turn,
                        result=result,
                        started=started,
                    )
                result = await self._handle_attachment_upload(
                    turn=turn,
                    prefix=upload_prefix,
                )
            else:
                if not self._model_turns_enabled:
                    result = await self._persist_direct_result(
                        turn=turn,
                        text="Model-backed replies are temporarily disabled.",
                        outcome="error",
                    )
                    return self._record_result(
                        turn=turn,
                        result=result,
                        started=started,
                    )
                ai_response = await self._run_ai_interaction(
                    message=_message_with_attachment_context(
                        text=turn.text,
                        attachments=turn.attachments,
                    ),
                    session_id=turn.conversation_id,
                    tools=self._wrapper_tools(),
                )
                result = ChatTurnResult(
                    request_id=turn.request_id,
                    conversation_id=turn.conversation_id,
                    text=ai_response.text,
                    outcome="reply",
                    confirmation_required=False,
                    suggested_next_actions=(),
                    model=ai_response.model,
                    steps=ai_response.steps,
                    fallback_used=ai_response.fallback_used,
                )

        if result is None:
            msg = "runtime did not produce a turn result"
            raise AssertionError(msg)
        return self._record_result(turn=turn, result=result, started=started)

    async def stream_chat_turn(self, turn: ChatTurnInput) -> AsyncIterator[NimbusEvent]:
        """Run one model-backed turn and yield durable replayable events.

        This path is intentionally model-only for the first streaming slice.
        Direct runtime actions such as confirmation and upload still use
        ``run_chat_turn`` until their approval flows move onto typed protocol
        commands.
        """
        started = time.monotonic()
        outcome: TurnOutcome = "reply"
        try:
            async with get_session_lock(turn.conversation_id):
                if not self._model_turns_enabled:
                    outcome = "error"
                    error_event = self._append_turn_event(
                        turn=turn,
                        event_type=StreamEventType.TURN_FAILED.value,
                        payload={
                            "error": "Model-backed replies are temporarily disabled."
                        },
                    )
                    yield error_event
                    return
                start_event = self._append_turn_event(
                    turn=turn,
                    event_type=StreamEventType.TURN_STARTED.value,
                    payload={},
                )
                yield start_event
                async for event in self._run_streaming_ai_interaction(turn):
                    yield event
        except Exception:
            outcome = "error"
            raise
        finally:
            self._telemetry.record_wrapper_turn(
                platform=turn.platform,
                outcome=outcome,
                latency_ms=int((time.monotonic() - started) * 1000),
            )

    def replay_events(
        self,
        *,
        platform: str,
        workspace_id: str,
        session_id: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> tuple[NimbusEvent, ...]:
        """Return durable ordered events for a tenant-scoped session."""
        tenant = TenantIdentity(platform=platform, workspace_id=workspace_id)
        events = self._event_store.list_events(
            tenant=tenant,
            session_id=session_id,
            after_sequence=after_sequence,
            limit=limit,
        )
        return tuple(_protocol_event(event) for event in events)

    def _record_result(
        self,
        *,
        turn: ChatTurnInput,
        result: ChatTurnResult,
        started: float,
    ) -> ChatTurnResult:
        """Record wrapper telemetry for a completed runtime turn."""
        latency_ms = int((time.monotonic() - started) * 1000)
        self._telemetry.record_wrapper_turn(
            platform=turn.platform,
            outcome=result.outcome,
            latency_ms=latency_ms,
        )
        return result

    async def _run_ai_interaction(
        self,
        *,
        message: str,
        session_id: str,
        tools: list[Tool] | None,
    ) -> AIResponse:
        conv = _load_session(self._session_dir, session_id, self._system_prompt)
        conv.add_user(message)
        try:
            ai_response = await asyncio.to_thread(
                self._ai_client.send_message,
                conv,
                tools=tools,
            )
        except Exception as exc:
            self._telemetry.record_ai_failure(error_kind=_ai_error_kind(exc))
            raise
        try:
            _save_session(self._session_dir, session_id, conv)
        except Exception:
            log.exception("runtime_session_save_failed", session_id=session_id)

        self._record_ai_response(ai_response)
        return ai_response

    async def _run_streaming_ai_interaction(
        self,
        turn: ChatTurnInput,
    ) -> AsyncIterator[NimbusEvent]:
        """Run one streaming model interaction and persist every emitted event."""
        conv = _load_session(
            self._session_dir,
            turn.conversation_id,
            self._system_prompt,
        )
        conv.add_user(
            _message_with_attachment_context(
                text=turn.text,
                attachments=turn.attachments,
            )
        )
        try:
            async for provider_event in self._ai_client.stream_message(
                conv,
                tools=self._wrapper_tools(),
            ):
                event = self._append_provider_stream_event(
                    turn=turn,
                    provider_event=provider_event,
                )
                yield event
                if provider_event.kind == "request_completed":
                    response = provider_event.payload.get("response")
                    if isinstance(response, AIResponse):
                        self._record_ai_response(response)
        except Exception as exc:
            self._telemetry.record_ai_failure(error_kind=_ai_error_kind(exc))
            failed_event = self._append_turn_event(
                turn=turn,
                event_type=StreamEventType.TURN_FAILED.value,
                payload={"error": str(exc), "error_kind": _ai_error_kind(exc)},
            )
            yield failed_event
            raise
        try:
            _save_session(self._session_dir, turn.conversation_id, conv)
        except Exception:
            log.exception(
                "runtime_streaming_session_save_failed",
                session_id=turn.conversation_id,
            )

    def _append_provider_stream_event(
        self,
        *,
        turn: ChatTurnInput,
        provider_event: AIStreamEvent,
    ) -> NimbusEvent:
        """Persist one provider stream event under the Nimbus event vocabulary."""
        event_type = {
            "request_started": StreamEventType.PROVIDER_REQUEST_STARTED.value,
            "text_delta": StreamEventType.TEXT_DELTA.value,
            "text_completed": StreamEventType.TEXT_COMPLETED.value,
            "reasoning_delta": StreamEventType.REASONING_DELTA.value,
            "tool_call_started": StreamEventType.TOOL_CALL_STARTED.value,
            "tool_call_completed": StreamEventType.TOOL_CALL_COMPLETED.value,
            "request_completed": StreamEventType.TURN_COMPLETED.value,
            "model_fallback": StreamEventType.MODEL_FALLBACK.value,
            "error": StreamEventType.ERROR.value,
        }[provider_event.kind]
        payload = _json_safe_stream_payload(provider_event)
        return self._append_turn_event(
            turn=turn,
            event_type=event_type,
            payload=payload,
        )

    def _append_turn_event(
        self,
        *,
        turn: ChatTurnInput,
        event_type: str,
        payload: Mapping[str, object],
    ) -> NimbusEvent:
        """Append one runtime event and return its protocol projection."""
        tenant = self._tenant_for_turn(turn)
        actor = self._actor_for_turn(turn, tenant=tenant)
        event_payload = {
            "request_id": turn.request_id,
            "message_id": turn.message_id,
            **dict(payload),
        }
        event = self._event_store.append(
            tenant=tenant,
            session_id=turn.conversation_id,
            event_type=event_type,
            actor=actor,
            payload=event_payload,
        )
        return _protocol_event(event)

    def _record_ai_response(self, ai_response: AIResponse) -> None:
        """Record telemetry for a completed model response."""
        self._telemetry.record_ai_response(
            model=ai_response.model,
            latency_ms=ai_response.latency_ms,
            fallback_used=ai_response.fallback_used,
            stop_reason=ai_response.stop_reason,
        )
        for tool_call in ai_response.tool_calls:
            self._telemetry.record_tool_call(
                tool_name=tool_call.name,
                success=tool_call.success,
                latency_ms=tool_call.latency_ms,
            )

    def _wrapper_tools(self) -> list[Tool]:
        if (
            not self._storage_tools_enabled
            or self._storage is None
            or self._tool_container is None
        ):
            return []
        return build_slack_tools(storage=self._storage, container=self._tool_container)

    @staticmethod
    def _tenant_for_turn(turn: ChatTurnInput) -> TenantIdentity:
        return TenantIdentity(platform=turn.platform, workspace_id=turn.workspace_id)

    @staticmethod
    def _actor_for_turn(
        turn: ChatTurnInput,
        *,
        tenant: TenantIdentity,
    ) -> VerifiedActor:
        auth_source: ActorAuthSource = (
            "slack_signed_event" if turn.platform == "slack" else "cli_local"
        )
        return VerifiedActor(
            tenant=tenant,
            user_id=turn.user_id,
            auth_source=auth_source,
            bridge_id=turn.platform,
            verified_at=datetime.now(UTC),
        )

    def _object_ref(self, remote_path: str) -> ObjectRef:
        if self._tool_container is None:
            msg = "storage container is not configured"
            raise ValueError(msg)
        return ObjectRef(
            provider="s3",
            container=self._tool_container,
            object_name=remote_path,
        )

    def _policy_context(self) -> PolicyContext:
        return PolicyContext(
            pinned_container=self._tool_container,
            max_upload_bytes=_MAX_UPLOAD_BYTES,
        )

    @staticmethod
    def _action_idempotency_key(
        *,
        turn: ChatTurnInput,
        actor: VerifiedActor,
        action_kind: ActionKind,
        target: str,
    ) -> str:
        request_key = turn.idempotency_key or f"message:{turn.message_id}"
        fingerprint = json.dumps(
            {
                "schema_version": 1,
                "tenant": actor.tenant.tenant_id,
                "actor": actor.user_id,
                "conversation_id": turn.conversation_id,
                "request_key": request_key,
                "action_kind": action_kind.value,
                "target": target,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256-{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _delete_remote_path(action: Action) -> str:
        if action.target is not None:
            return action.target.object_name
        if isinstance(action.input, DeleteFileInput):
            return action.input.remote_path
        if isinstance(action.input, UploadAttachmentInput):
            return action.input.remote_path
        return ""

    def _delete_expected_reply(self, action: Action) -> str:
        return f"yes, delete {self._delete_remote_path(action)}"

    def _delete_prompt(self, action: Action) -> str:
        remote_path = self._delete_remote_path(action)
        return (
            f"I can delete `{remote_path}`, but this is destructive. "
            f"Reply with `{self._delete_expected_reply(action)}` if you want "
            "me to proceed."
        )

    def _delete_confirmation(self, action: Action) -> ConfirmationDetails:
        expires_at = action.expires_at or datetime.now(UTC)
        return ConfirmationDetails(
            action_id=action.action_id,
            kind="delete_file",
            prompt=self._delete_prompt(action),
            expected_reply=self._delete_expected_reply(action),
            expires_at=expires_at.isoformat(),
        )

    @staticmethod
    def _action_summary(action: Action) -> ActionSummary:
        target = None
        if action.target is not None:
            target = {
                "provider": action.target.provider,
                "container": action.target.container,
                "object_name": action.target.object_name,
                "version_id": action.target.version_id,
            }
        return ActionSummary(
            action_id=action.action_id,
            kind=action.kind.value,
            status=action.status.value,
            target=target,
        )

    @staticmethod
    def _artifact_summary(artifact: Artifact) -> ArtifactSummary:
        return ArtifactSummary(
            artifact_id=artifact.artifact_id,
            kind=artifact.kind,
            action_id=artifact.action_id,
            payload=_artifact_payload_to_summary(artifact.payload),
        )

    def _create_artifact(
        self,
        draft: _ArtifactDraft,
        *,
        actor: VerifiedActor | None,
    ) -> Artifact:
        artifact = Artifact(
            artifact_id=f"art-{uuid.uuid4().hex}",
            tenant=draft.tenant,
            session_id=draft.session_id,
            action_id=draft.action_id,
            kind=draft.kind,
            uri=None,
            payload=draft.payload,
            created_at=datetime.now(UTC),
        )
        return self._artifact_store.create(artifact=artifact, actor=actor)

    def _load_pending_delete(self, turn: ChatTurnInput) -> Action | None:
        tenant = self._tenant_for_turn(turn)
        action = self._action_store.find_latest_awaiting_confirmation(
            tenant=tenant,
            session_id=turn.conversation_id,
            kind=ActionKind.DELETE_FILE,
        )
        if action is None:
            return None
        if action.expires_at is not None and action.expires_at <= datetime.now(UTC):
            self._action_store.transition(
                tenant=tenant,
                action_id=action.action_id,
                transition=ActionTransition(
                    expected=ActionStatus.AWAITING_CONFIRMATION,
                    next_status=ActionStatus.EXPIRED,
                    event_type="action_expired",
                    event_payload={
                        "reason": "confirmation_expired",
                        "remote_path": self._delete_remote_path(action),
                    },
                ),
            )
            return None
        return action

    async def _create_pending_delete(
        self, *, turn: ChatTurnInput, remote_path: str
    ) -> ChatTurnResult:
        if self._storage is None or self._tool_container is None:
            return await self._persist_direct_result(
                turn=turn,
                text="Delete is unavailable because Nimbus storage is not configured.",
                outcome="error",
            )
        tenant = self._tenant_for_turn(turn)
        actor = self._actor_for_turn(turn, tenant=tenant)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=_PENDING_DELETE_TTL_SECONDS)
        target = self._object_ref(remote_path)
        idempotency_key = self._action_idempotency_key(
            turn=turn,
            actor=actor,
            action_kind=ActionKind.DELETE_FILE,
            target=remote_path,
        )

        def create() -> Action:
            return Action(
                action_id=f"act-{uuid.uuid4().hex}",
                tenant=tenant,
                session_id=turn.conversation_id,
                actor=actor,
                kind=ActionKind.DELETE_FILE,
                target=target,
                status=ActionStatus.AWAITING_CONFIRMATION,
                idempotency_key=idempotency_key,
                input=DeleteFileInput(remote_path=remote_path),
                result=None,
                failure=None,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )

        action = self._action_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key=idempotency_key,
            create=create,
        )
        if (
            authorize_action(actor=actor, action=action, context=self._policy_context())
            is not PolicyDecision.REQUIRE_CONFIRMATION
        ):
            return await self._persist_direct_result(
                turn=turn,
                text="Nimbus policy denied this delete request.",
                outcome="error",
                actions=(self._action_summary(action),),
            )
        return await self._persist_direct_result(
            turn=turn,
            text=self._delete_prompt(action),
            outcome="confirmation_required",
            suggested_next_actions=(self._delete_expected_reply(action),),
            confirmation=self._delete_confirmation(action),
            actions=(self._action_summary(action),),
        )

    async def _handle_delete_confirmation(  # noqa: C901, PLR0911 - exact guard failures
        self,
        *,
        turn: ChatTurnInput,
        pending: Action | None,
        confirmation_target: str,
    ) -> ChatTurnResult:
        if not self._delete_actions_enabled:
            return await self._persist_direct_result(
                turn=turn,
                text="Delete actions are temporarily disabled.",
                outcome="error",
            )
        if pending is None:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "There is no pending destructive action to confirm in this "
                    "conversation."
                ),
                outcome="error",
            )
        expected_reply = self._delete_expected_reply(pending)
        remote_path = self._delete_remote_path(pending)
        if pending.actor.user_id != turn.user_id:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "Only the original requester can confirm this delete. "
                    f"The pending action is still `{expected_reply}`."
                ),
                outcome="error",
                suggested_next_actions=(expected_reply,),
                actions=(self._action_summary(pending),),
            )
        if confirmation_target != remote_path:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    f"The pending delete is for `{remote_path}`. "
                    f"Reply with `{expected_reply}` to confirm that exact file."
                ),
                outcome="error",
                suggested_next_actions=(expected_reply,),
                actions=(self._action_summary(pending),),
            )
        if self._storage is None or self._tool_container is None:
            return await self._persist_direct_result(
                turn=turn,
                text="Delete is unavailable because Nimbus storage is not configured.",
                outcome="error",
                actions=(self._action_summary(pending),),
            )
        tenant = self._tenant_for_turn(turn)
        authorized = self._action_store.transition(
            tenant=tenant,
            action_id=pending.action_id,
            transition=ActionTransition(
                expected=ActionStatus.AWAITING_CONFIRMATION,
                next_status=ActionStatus.AUTHORIZED,
                event_type="action_authorized",
                event_payload={
                    "remote_path": remote_path,
                    "authorized_by": turn.user_id,
                },
            ),
        )
        if authorized is None:
            return await self._persist_direct_result(
                turn=turn,
                text="This delete is no longer waiting for confirmation.",
                outcome="error",
            )
        executing = self._action_store.transition(
            tenant=tenant,
            action_id=authorized.action_id,
            transition=ActionTransition(
                expected=ActionStatus.AUTHORIZED,
                next_status=ActionStatus.EXECUTING,
                event_type="action_started",
                event_payload={"remote_path": remote_path},
            ),
        )
        if executing is None:
            return await self._persist_direct_result(
                turn=turn,
                text="This delete could not be started because its state changed.",
                outcome="error",
                actions=(self._action_summary(authorized),),
            )
        try:
            result = self._storage.delete_file(
                container=self._tool_container,
                object_name=remote_path,
            )
        except Exception as exc:  # noqa: BLE001 - storage clients raise provider-specific errors
            log.warning(
                "runtime_delete_failed",
                conversation_id=turn.conversation_id,
                remote_path=remote_path,
                detail=str(exc),
            )
            failed = self._action_store.transition(
                tenant=tenant,
                action_id=executing.action_id,
                transition=ActionTransition(
                    expected=ActionStatus.EXECUTING,
                    next_status=ActionStatus.FAILED_RETRYABLE,
                    event_type="action_failed",
                    event_payload={"remote_path": remote_path, "detail": str(exc)},
                    failure=ActionFailure(detail=str(exc), remote_path=remote_path),
                ),
            )
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    f"I could not delete `{remote_path}` right now. "
                    "Start the delete again if you want to retry."
                ),
                outcome="error",
                suggested_next_actions=(f"delete {remote_path}",),
                actions=(self._action_summary(failed or executing),),
            )

        deleted = bool(getattr(result, "deleted", True))
        delete_result = DeleteFileResult(
            remote_path=remote_path,
            deleted=deleted,
            version_id=getattr(result, "version_id", None),
        )
        verifying = self._action_store.transition(
            tenant=tenant,
            action_id=executing.action_id,
            transition=ActionTransition(
                expected=ActionStatus.EXECUTING,
                next_status=ActionStatus.VERIFYING,
                event_type="verification_started",
                event_payload={"remote_path": remote_path, "deleted": deleted},
            ),
        )
        completed = None
        artifact = None
        if verifying is not None:
            artifact = self._create_artifact(
                _ArtifactDraft(
                    tenant=tenant,
                    session_id=turn.conversation_id,
                    action_id=verifying.action_id,
                    kind="delete_report",
                    payload=DeleteReport(
                        remote_path=remote_path,
                        deleted=deleted,
                        version_id=delete_result.version_id,
                    ),
                ),
                actor=verifying.actor,
            )
            completed = self._action_store.transition(
                tenant=tenant,
                action_id=verifying.action_id,
                transition=ActionTransition(
                    expected=ActionStatus.VERIFYING,
                    next_status=ActionStatus.SUCCEEDED,
                    event_type="action_completed",
                    event_payload={
                        "remote_path": remote_path,
                        "deleted": deleted,
                        "version_id": delete_result.version_id,
                        "artifact_id": artifact.artifact_id,
                    },
                    result=delete_result.with_artifact(artifact.artifact_id),
                ),
            )
        if deleted:
            reply = f"Deleted `{remote_path}`."
        else:
            reply = f"No file was deleted for `{remote_path}`."
        return await self._persist_direct_result(
            turn=turn,
            text=reply,
            outcome="reply",
            actions=(self._action_summary(completed or verifying or executing),),
            artifacts=() if artifact is None else (self._artifact_summary(artifact),),
        )

    async def _handle_attachment_upload(
        self, *, turn: ChatTurnInput, prefix: str
    ) -> ChatTurnResult:
        if self._storage is None or self._tool_container is None:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "Attachment upload is unavailable because Nimbus storage is "
                    "not configured."
                ),
                outcome="error",
            )
        if not turn.attachments:
            return await self._persist_direct_result(
                turn=turn,
                text="No attachments were provided for this upload request.",
                outcome="error",
            )

        normalized_prefix = _strip_wrapping_quotes(prefix)
        if not normalized_prefix:
            return await self._persist_direct_result(
                turn=turn,
                text="Upload destination prefix cannot be empty.",
                outcome="error",
            )

        tenant = self._tenant_for_turn(turn)
        actor = self._actor_for_turn(turn, tenant=tenant)
        batch = _UploadBatch()
        for attachment in turn.attachments:
            remote_path = self._remote_path_for_upload(
                prefix=normalized_prefix,
                filename=Path(attachment.filename).name or attachment.platform_file_id,
            )
            attempt = self._process_upload_attachment(
                _UploadWork(
                    turn=turn,
                    tenant=tenant,
                    actor=actor,
                    attachment=attachment,
                    remote_path=remote_path,
                )
            )
            self._record_upload_attempt(batch, attempt)

        if batch.uploaded and not batch.failures:
            text = self._upload_success_text(normalized_prefix, batch.uploaded)
            return await self._persist_direct_result(
                turn=turn,
                text=text,
                outcome="reply",
                suggested_next_actions=(f"list files under {normalized_prefix}",),
                actions=tuple(batch.actions),
                artifacts=tuple(batch.artifacts),
            )
        if batch.uploaded and batch.failures:
            text = self._upload_partial_text(
                normalized_prefix,
                batch.uploaded,
                batch.failures,
            )
            return await self._persist_direct_result(
                turn=turn,
                text=text,
                outcome="partial_success",
                suggested_next_actions=(f"list files under {normalized_prefix}",),
                actions=tuple(batch.actions),
                artifacts=tuple(batch.artifacts),
            )
        return await self._persist_direct_result(
            turn=turn,
            text=self._upload_failure_text(batch.failures),
            outcome="error",
            actions=tuple(batch.actions),
            artifacts=tuple(batch.artifacts),
        )

    def _process_upload_attachment(self, work: _UploadWork) -> _UploadAttempt:
        action = self._create_upload_action(
            turn=work.turn,
            tenant=work.tenant,
            actor=work.actor,
            attachment=work.attachment,
            remote_path=work.remote_path,
        )
        if action.status is ActionStatus.SUCCEEDED:
            return _UploadAttempt(
                uploaded_path=work.remote_path,
                failure=None,
                action=action,
            )
        if action.status is not ActionStatus.AUTHORIZED:
            return _UploadAttempt(
                uploaded_path=None,
                failure=self._existing_upload_failure_text(work.attachment, action),
                action=action,
            )
        if self._upload_policy_denies(actor=work.actor, action=action):
            failed = self._fail_upload_action(
                tenant=work.tenant,
                action=action,
                remote_path=work.remote_path,
                detail="policy_denied",
                retryable=False,
            )
            return _UploadAttempt(
                uploaded_path=None,
                failure=f"{work.attachment.filename} (policy denied)",
                action=failed or action,
            )
        try:
            payload = self._decode_attachment_bytes(work.attachment)
        except ValueError as exc:
            failed = self._fail_upload_action(
                tenant=work.tenant,
                action=action,
                remote_path=work.remote_path,
                detail=str(exc),
                retryable=False,
            )
            return _UploadAttempt(
                uploaded_path=None,
                failure=f"{work.attachment.filename} ({exc})",
                action=failed or action,
            )
        return self._execute_upload_attachment(
            work=work,
            action=action,
            payload=payload,
        )

    def _execute_upload_attachment(
        self,
        *,
        work: _UploadWork,
        action: Action,
        payload: bytes,
    ) -> _UploadAttempt:
        executing = self._action_store.transition(
            tenant=work.tenant,
            action_id=action.action_id,
            transition=ActionTransition(
                expected=ActionStatus.AUTHORIZED,
                next_status=ActionStatus.EXECUTING,
                event_type="action_started",
                event_payload={"remote_path": work.remote_path},
            ),
        )
        if executing is None:
            return _UploadAttempt(
                uploaded_path=None,
                failure=f"{work.attachment.filename} (action state changed)",
                action=action,
            )
        try:
            uploaded_path = self._upload_attachment_bytes(
                attachment=work.attachment,
                payload=payload,
                remote_path=work.remote_path,
            )
        except Exception as exc:  # noqa: BLE001 - storage clients raise provider-specific errors
            failed = self._fail_upload_action(
                tenant=work.tenant,
                action=executing,
                remote_path=work.remote_path,
                detail=str(exc),
                retryable=True,
            )
            return _UploadAttempt(
                uploaded_path=None,
                failure=f"{work.attachment.filename} ({exc})",
                action=failed or executing,
            )
        return self._verify_upload_attachment(
            work=work,
            action=executing,
            payload=payload,
            uploaded_path=uploaded_path,
        )

    def _verify_upload_attachment(
        self,
        *,
        work: _UploadWork,
        action: Action,
        payload: bytes,
        uploaded_path: str,
    ) -> _UploadAttempt:
        sha256_hex = hashlib.sha256(payload).hexdigest()
        result = UploadAttachmentResult(
            remote_path=uploaded_path,
            size_bytes=len(payload),
            sha256_hex=sha256_hex,
        )
        verifying = self._action_store.transition(
            tenant=work.tenant,
            action_id=action.action_id,
            transition=ActionTransition(
                expected=ActionStatus.EXECUTING,
                next_status=ActionStatus.VERIFYING,
                event_type="verification_started",
                event_payload={
                    "remote_path": uploaded_path,
                    "size_bytes": len(payload),
                    "sha256_hex": sha256_hex,
                },
            ),
        )
        if verifying is None:
            return _UploadAttempt(
                uploaded_path=None,
                failure=f"{work.attachment.filename} (action state changed)",
                action=action,
            )
        artifact = self._create_artifact(
            _ArtifactDraft(
                tenant=work.tenant,
                session_id=work.turn.conversation_id,
                action_id=verifying.action_id,
                kind="upload_report",
                payload=UploadReport(
                    remote_path=uploaded_path,
                    filename=work.attachment.filename,
                    size_bytes=len(payload),
                    sha256_hex=sha256_hex,
                ),
            ),
            actor=verifying.actor,
        )
        completed = self._action_store.transition(
            tenant=work.tenant,
            action_id=verifying.action_id,
            transition=ActionTransition(
                expected=ActionStatus.VERIFYING,
                next_status=ActionStatus.SUCCEEDED,
                event_type="action_completed",
                event_payload={
                    "remote_path": uploaded_path,
                    "size_bytes": len(payload),
                    "sha256_hex": sha256_hex,
                    "artifact_id": artifact.artifact_id,
                },
                result=result.with_artifact(artifact.artifact_id),
            ),
        )
        return _UploadAttempt(
            uploaded_path=uploaded_path,
            failure=None,
            action=completed or verifying,
            artifact=artifact,
        )

    def _fail_upload_action(
        self,
        *,
        tenant: TenantIdentity,
        action: Action,
        remote_path: str,
        detail: str,
        retryable: bool,
    ) -> Action | None:
        return self._action_store.transition(
            tenant=tenant,
            action_id=action.action_id,
            transition=ActionTransition(
                expected=action.status,
                next_status=(
                    ActionStatus.FAILED_RETRYABLE
                    if retryable
                    else ActionStatus.FAILED_TERMINAL
                ),
                event_type="action_failed",
                event_payload={"remote_path": remote_path, "detail": detail},
                failure=ActionFailure(detail=detail, remote_path=remote_path),
            ),
        )

    def _upload_policy_denies(
        self,
        *,
        actor: VerifiedActor,
        action: Action,
    ) -> bool:
        return (
            authorize_action(
                actor=actor,
                action=action,
                context=self._policy_context(),
            )
            is not PolicyDecision.ALLOW
        )

    @staticmethod
    def _existing_upload_failure_text(
        attachment: TurnAttachment,
        action: Action,
    ) -> str:
        if action.failure is not None:
            return f"{attachment.filename} ({action.failure.detail})"
        return f"{attachment.filename} (action state is {action.status.value})"

    def _record_upload_attempt(
        self,
        batch: _UploadBatch,
        attempt: _UploadAttempt,
    ) -> None:
        if attempt.uploaded_path is not None:
            batch.uploaded.append(attempt.uploaded_path)
        if attempt.failure is not None:
            batch.failures.append(attempt.failure)
        batch.actions.append(self._action_summary(attempt.action))
        if attempt.artifact is not None:
            batch.artifacts.append(self._artifact_summary(attempt.artifact))

    def _create_upload_action(
        self,
        *,
        turn: ChatTurnInput,
        tenant: TenantIdentity,
        actor: VerifiedActor,
        attachment: TurnAttachment,
        remote_path: str,
    ) -> Action:
        now = datetime.now(UTC)
        target = self._object_ref(remote_path)
        idempotency_key = self._action_idempotency_key(
            turn=turn,
            actor=actor,
            action_kind=ActionKind.UPLOAD_ATTACHMENT,
            target=f"{attachment.platform_file_id}:{remote_path}",
        )

        def create() -> Action:
            return Action(
                action_id=f"act-{uuid.uuid4().hex}",
                tenant=tenant,
                session_id=turn.conversation_id,
                actor=actor,
                kind=ActionKind.UPLOAD_ATTACHMENT,
                target=target,
                status=ActionStatus.AUTHORIZED,
                idempotency_key=idempotency_key,
                input=UploadAttachmentInput(
                    platform_file_id=attachment.platform_file_id,
                    filename=attachment.filename,
                    content_type=attachment.content_type,
                    size_bytes=attachment.size_bytes,
                    sha256_hex=attachment.sha256_hex,
                    remote_path=remote_path,
                ),
                result=None,
                failure=None,
                created_at=now,
                updated_at=now,
                expires_at=None,
            )

        return self._action_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key=idempotency_key,
            create=create,
        )

    def _decode_attachment_bytes(self, attachment: TurnAttachment) -> bytes:
        if attachment.content_base64 is None:
            msg = "attachment bytes were not provided"
            raise ValueError(msg)
        try:
            payload = base64.b64decode(attachment.content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            msg = "attachment bytes are not valid base64"
            raise ValueError(msg) from exc
        if len(payload) != attachment.size_bytes:
            msg = (
                "decoded byte length does not match declared size_bytes "
                f"({len(payload)} != {attachment.size_bytes})"
            )
            raise ValueError(msg)
        if attachment.sha256_hex is not None:
            digest = hashlib.sha256(payload).hexdigest()
            if digest.lower() != attachment.sha256_hex.lower():
                msg = "attachment sha256_hex does not match the decoded bytes"
                raise ValueError(msg)
        return payload

    def _upload_attachment_bytes(
        self,
        *,
        attachment: TurnAttachment,
        payload: bytes,
        remote_path: str,
    ) -> str:
        if self._storage is None or self._tool_container is None:
            msg = "storage is not configured"
            raise ValueError(msg)
        filename = Path(attachment.filename).name or attachment.platform_file_id
        scratch_dir = self._session_dir / "_attachment_ingestion"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix[:32]
        # ``delete=False`` means the file persists after the ``with`` block, so
        # the caller must clean it up explicitly.  We previously kept the
        # ``try/finally`` cleanup *outside* the ``with`` block, which leaked
        # the temp file on disk if ``handle.write(payload)`` itself failed
        # (e.g. ENOSPC, EPERM mid-write) — the cleanup branch was never
        # entered.  Wrap both the write and the upload in one try/finally so
        # the file is always reaped.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - manual close+unlink in finally
            dir=scratch_dir,
            prefix="nimbus-attachment-",
            suffix=suffix,
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            try:
                handle.write(payload)
            finally:
                handle.close()
            self._storage.upload_file(
                container=self._tool_container,
                local_path=str(temp_path),
                remote_path=remote_path,
            )
            return remote_path
        finally:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _remote_path_for_upload(*, prefix: str, filename: str) -> str:
        clean_prefix = prefix.rstrip("/")
        return f"{clean_prefix}/{filename}"

    @staticmethod
    def _upload_success_text(prefix: str, uploaded: list[str]) -> str:
        if len(uploaded) == 1:
            return f"Uploaded 1 attachment to `{uploaded[0]}`."
        return f"Uploaded {len(uploaded)} attachments to `{prefix.rstrip('/')}/`."

    @staticmethod
    def _upload_partial_text(
        prefix: str,
        uploaded: list[str],
        failures: list[str],
    ) -> str:
        details = "; ".join(failures[:_MAX_FAILURE_DETAILS])
        suffix = (
            ""
            if len(failures) <= _MAX_FAILURE_DETAILS
            else "; additional failures omitted"
        )
        return (
            f"Uploaded {len(uploaded)} attachment(s) to `{prefix.rstrip('/')}/`, "
            f"but skipped {len(failures)}: {details}{suffix}."
        )

    @staticmethod
    def _upload_failure_text(failures: list[str]) -> str:
        if not failures:
            return "No attachments could be uploaded."
        details = "; ".join(failures[:_MAX_FAILURE_DETAILS])
        suffix = (
            ""
            if len(failures) <= _MAX_FAILURE_DETAILS
            else "; additional failures omitted"
        )
        return f"No attachments were uploaded: {details}{suffix}."

    async def _persist_direct_result(  # noqa: PLR0913 - mirrors response fields
        self,
        *,
        turn: ChatTurnInput,
        text: str,
        outcome: TurnOutcome,
        suggested_next_actions: tuple[str, ...] = (),
        confirmation: ConfirmationDetails | None = None,
        actions: tuple[ActionSummary, ...] = (),
        artifacts: tuple[ArtifactSummary, ...] = (),
    ) -> ChatTurnResult:
        conv = _load_session(
            self._session_dir,
            turn.conversation_id,
            self._system_prompt,
        )
        conv.add_user(
            _message_with_attachment_context(
                text=turn.text,
                attachments=turn.attachments,
            )
        )
        conv.add_assistant(text)
        try:
            _save_session(self._session_dir, turn.conversation_id, conv)
        except Exception:
            log.exception(
                "runtime_session_save_failed",
                session_id=turn.conversation_id,
            )
        return ChatTurnResult(
            request_id=turn.request_id,
            conversation_id=turn.conversation_id,
            text=text,
            outcome=outcome,
            confirmation_required=outcome == "confirmation_required",
            suggested_next_actions=suggested_next_actions,
            model=_RUNTIME_MODEL_NAME,
            steps=0,
            fallback_used=False,
            confirmation=confirmation,
            actions=actions,
            artifacts=artifacts,
        )
