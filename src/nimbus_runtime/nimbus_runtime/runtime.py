"""Shared Nimbus runtime for wrapper-facing chat turns and legacy chats."""

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
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

from ai_client_api import (
    AIClient,
    AIClientConfigError,
    AIClientError,
    AIProviderError,
    AIRateLimitError,
    AIResponse,
    AIStepBudgetExceededError,
    AITimeoutError,
    Conversation,
    Tool,
)
from nimbus_runtime.models import (
    ChatTurnInput,
    ChatTurnResult,
    ConfirmationDetails,
    TurnAttachment,
    TurnOutcome,
)
from nimbus_runtime.slack_tools import build_slack_tools
from nimbus_runtime.state_store import delete_state, get_state, put_state
from nimbus_runtime.telemetry import RuntimeTelemetry, runtime_telemetry

if TYPE_CHECKING:
    from cloud_storage_api import CloudStorageClient

log: Any = structlog.get_logger()

_PENDING_DELETE_NAMESPACE = "pending_delete_actions"
_PENDING_DELETE_TTL_SECONDS = int(
    os.environ.get("NIMBUS_PENDING_DELETE_TTL_SECONDS", "900")
)
_RUNTIME_MODEL_NAME = "nimbus-runtime"
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:]+$")
_MAX_SESSION_FILENAME_STEM_LENGTH = 128
_HASHED_SESSION_STEM_PREFIX = "sha256-"
_MAX_FAILURE_DETAILS = 3
_MIN_QUOTED_TEXT_LENGTH = 2
_DELETE_REQUEST_RE = re.compile(r"^\s*delete\s+(?P<remote_path>.+?)\s*$", re.IGNORECASE)
_DELETE_CONFIRM_RE = re.compile(
    r"^\s*yes\s*,\s*delete\s+(?P<remote_path>.+?)\s*$",
    re.IGNORECASE,
)
_UPLOAD_REQUEST_RE = re.compile(
    r"^\s*upload\s+(?:all\s+files\s+in\s+this\s+channel|these\s+files|attached\s+files)\s+to\s+(?P<prefix>.+?)\s*$",
    re.IGNORECASE,
)

_session_locks: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True, slots=True)
class _PendingDeleteAction:
    action_id: str
    conversation_id: str
    user_id: str
    remote_path: str
    expires_at: float

    @property
    def expires_at_iso(self) -> str:
        return datetime.fromtimestamp(self.expires_at, tz=UTC).isoformat()

    @property
    def expected_reply(self) -> str:
        return f"yes, delete {self.remote_path}"

    @property
    def prompt(self) -> str:
        return (
            f"I can delete `{self.remote_path}`, but this is destructive. "
            f"Reply with `{self.expected_reply}` if you want me to proceed."
        )

    def to_confirmation(self) -> ConfirmationDetails:
        return ConfirmationDetails(
            action_id=self.action_id,
            kind="delete_file",
            prompt=self.prompt,
            expected_reply=self.expected_reply,
            expires_at=self.expires_at_iso,
        )

    def to_state_value(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "conversation_id": self.conversation_id,
            "user_id": self.user_id,
            "remote_path": self.remote_path,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_state_value(cls, data: dict[str, object]) -> _PendingDeleteAction | None:
        try:
            return cls(
                action_id=str(data["action_id"]),
                conversation_id=str(data["conversation_id"]),
                user_id=str(data["user_id"]),
                remote_path=str(data["remote_path"]),
                expires_at=float(str(data["expires_at"])),
            )
        except (KeyError, TypeError, ValueError):
            return None


def get_session_lock(session_id: str) -> asyncio.Lock:
    """Return the shared per-session lock, creating it on first use."""
    if session_id not in _session_locks:
        _session_locks[session_id] = asyncio.Lock()
    return _session_locks[session_id]


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
    ) -> None:
        """Construct the runtime with its injected dependencies."""
        self._ai_client = ai_client
        self._storage = storage
        self._session_dir = session_dir
        self._system_prompt = system_prompt
        self._tool_container = tool_container
        self._telemetry = telemetry or runtime_telemetry

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
            pending = self._load_pending_delete(turn.conversation_id)
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
                    pending.user_id == turn.user_id
                    and pending.remote_path == delete_target
                ):
                    result = await self._persist_direct_result(
                        turn=turn,
                        text=pending.prompt,
                        outcome="confirmation_required",
                        suggested_next_actions=(pending.expected_reply,),
                        confirmation=pending.to_confirmation(),
                    )
                else:
                    result = await self._persist_direct_result(
                        turn=turn,
                        text=(
                            "This conversation already has a pending destructive "
                            f"action for `{pending.remote_path}`. Confirm or wait "
                            "for it to expire before starting another delete."
                        ),
                        outcome="error",
                        suggested_next_actions=(pending.expected_reply,),
                    )
            elif delete_target is not None:
                result = await self._create_pending_delete(
                    turn=turn,
                    remote_path=delete_target,
                )
            elif upload_prefix is not None:
                result = await self._handle_attachment_upload(
                    turn=turn,
                    prefix=upload_prefix,
                )
            else:
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

        latency_ms = int((time.monotonic() - started) * 1000)
        if result is None:
            msg = "runtime did not produce a turn result"
            raise AssertionError(msg)
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
        return ai_response

    def _wrapper_tools(self) -> list[Tool]:
        if self._storage is None or self._tool_container is None:
            return []
        return build_slack_tools(storage=self._storage, container=self._tool_container)

    def _load_pending_delete(self, conversation_id: str) -> _PendingDeleteAction | None:
        stored = get_state(_PENDING_DELETE_NAMESPACE, conversation_id)
        if stored.value is None:
            return None
        action = _PendingDeleteAction.from_state_value(stored.value)
        if action is None:
            delete_state(_PENDING_DELETE_NAMESPACE, conversation_id)
            log.warning(
                "runtime_pending_delete_invalid",
                conversation_id=conversation_id,
            )
            return None
        return action

    async def _create_pending_delete(
        self, *, turn: ChatTurnInput, remote_path: str
    ) -> ChatTurnResult:
        action = _PendingDeleteAction(
            action_id=f"act-{uuid.uuid4().hex}",
            conversation_id=turn.conversation_id,
            user_id=turn.user_id,
            remote_path=remote_path,
            expires_at=time.time() + float(_PENDING_DELETE_TTL_SECONDS),
        )
        put_state(
            _PENDING_DELETE_NAMESPACE,
            turn.conversation_id,
            value=action.to_state_value(),
            expires_at=action.expires_at,
        )
        return await self._persist_direct_result(
            turn=turn,
            text=action.prompt,
            outcome="confirmation_required",
            suggested_next_actions=(action.expected_reply,),
            confirmation=action.to_confirmation(),
        )

    async def _handle_delete_confirmation(
        self,
        *,
        turn: ChatTurnInput,
        pending: _PendingDeleteAction | None,
        confirmation_target: str,
    ) -> ChatTurnResult:
        if pending is None:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "There is no pending destructive action to confirm in this "
                    "conversation."
                ),
                outcome="error",
            )
        if pending.user_id != turn.user_id:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "Only the original requester can confirm this delete. "
                    f"The pending action is still `{pending.expected_reply}`."
                ),
                outcome="error",
                suggested_next_actions=(pending.expected_reply,),
            )
        if confirmation_target != pending.remote_path:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    f"The pending delete is for `{pending.remote_path}`. "
                    f"Reply with `{pending.expected_reply}` to confirm that exact file."
                ),
                outcome="error",
                suggested_next_actions=(pending.expected_reply,),
            )
        if self._storage is None or self._tool_container is None:
            return await self._persist_direct_result(
                turn=turn,
                text="Delete is unavailable because Nimbus storage is not configured.",
                outcome="error",
            )
        try:
            result = self._storage.delete_file(
                container=self._tool_container,
                object_name=pending.remote_path,
            )
        except Exception as exc:  # noqa: BLE001 - storage clients raise provider-specific errors
            log.warning(
                "runtime_delete_failed",
                conversation_id=turn.conversation_id,
                remote_path=pending.remote_path,
                detail=str(exc),
            )
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    f"I could not delete `{pending.remote_path}` right now. "
                    f"Try confirming again with `{pending.expected_reply}`."
                ),
                outcome="error",
                suggested_next_actions=(pending.expected_reply,),
            )

        delete_state(_PENDING_DELETE_NAMESPACE, turn.conversation_id)
        deleted = bool(getattr(result, "deleted", True))
        if deleted:
            reply = f"Deleted `{pending.remote_path}`."
        else:
            reply = f"No file was deleted for `{pending.remote_path}`."
        return await self._persist_direct_result(
            turn=turn,
            text=reply,
            outcome="reply",
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

        uploaded: list[str] = []
        failures: list[str] = []
        for attachment in turn.attachments:
            try:
                payload = self._decode_attachment_bytes(attachment)
            except ValueError as exc:
                failures.append(f"{attachment.filename} ({exc})")
                continue
            try:
                remote_path = self._upload_attachment_bytes(
                    attachment=attachment,
                    payload=payload,
                    prefix=normalized_prefix,
                )
            except Exception as exc:  # noqa: BLE001 - storage clients raise provider-specific errors
                failures.append(f"{attachment.filename} ({exc})")
                continue
            uploaded.append(remote_path)

        if uploaded and not failures:
            text = self._upload_success_text(normalized_prefix, uploaded)
            return await self._persist_direct_result(
                turn=turn,
                text=text,
                outcome="reply",
                suggested_next_actions=(f"list files under {normalized_prefix}",),
            )
        if uploaded and failures:
            text = self._upload_partial_text(normalized_prefix, uploaded, failures)
            return await self._persist_direct_result(
                turn=turn,
                text=text,
                outcome="partial_success",
                suggested_next_actions=(f"list files under {normalized_prefix}",),
            )
        return await self._persist_direct_result(
            turn=turn,
            text=self._upload_failure_text(failures),
            outcome="error",
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
        prefix: str,
    ) -> str:
        if self._storage is None or self._tool_container is None:
            msg = "storage is not configured"
            raise ValueError(msg)
        filename = Path(attachment.filename).name or attachment.platform_file_id
        remote_path = self._remote_path_for_upload(prefix=prefix, filename=filename)
        scratch_dir = self._session_dir / "_attachment_ingestion"
        scratch_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(filename).suffix[:32]
        with tempfile.NamedTemporaryFile(
            dir=scratch_dir,
            prefix="nimbus-attachment-",
            suffix=suffix,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
        try:
            self._storage.upload_file(
                container=self._tool_container,
                local_path=str(temp_path),
                remote_path=remote_path,
            )
            return remote_path
        finally:
            with suppress(UnboundLocalError):
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

    async def _persist_direct_result(
        self,
        *,
        turn: ChatTurnInput,
        text: str,
        outcome: TurnOutcome,
        suggested_next_actions: tuple[str, ...] = (),
        confirmation: ConfirmationDetails | None = None,
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
        )
