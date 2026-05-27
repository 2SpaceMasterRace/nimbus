"""Shared Nimbus runtime for wrapper-facing chat turns."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import os
import re
import tempfile
import threading
import time
import uuid
import weakref
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence as SequenceABC
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import structlog
from cloud_storage_api import ObjectNotFoundError

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
    Approval,
    ApprovalChoice,
    ApprovalStatus,
    Artifact,
    ArtifactPayload,
    CopyFileInput,
    CopyFileResult,
    DeleteFileInput,
    DeleteFileResult,
    DeleteReport,
    ManifestReport,
    MoveFileInput,
    MoveFileResult,
    ObjectRef,
    ObjectVerificationReport,
    Plan,
    PlanRiskLevel,
    PlanStatus,
    PlanTransition,
    ProofReceipt,
    RestorePlan,
    RestoreStrategy,
    SessionEvent,
    StorageMutationReport,
    TenantIdentity,
    UploadAttachmentInput,
    UploadAttachmentResult,
    UploadReport,
    VerifiedActor,
    WriteFileInput,
    WriteFileResult,
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
from nimbus_runtime.policy import (
    PolicyContext,
    PolicyDecision,
    approval_actor_ids_for_action,
    authorize_action,
    authorize_action_with_record,
)
from nimbus_runtime.postgres import load_session as load_postgres_session
from nimbus_runtime.postgres import postgres_enabled
from nimbus_runtime.postgres import save_session as save_postgres_session
from nimbus_runtime.proof import deterministic_receipt_id
from nimbus_runtime.slack_tools import build_slack_tools
from nimbus_runtime.stores import (
    ActionStore,
    ApprovalStore,
    ArtifactStore,
    FileActionStore,
    FileApprovalStore,
    FileArtifactStore,
    FilePlanStore,
    FileSessionEventStore,
    PlanStore,
    PostgresActionStore,
    PostgresApprovalStore,
    PostgresArtifactStore,
    PostgresPlanStore,
    PostgresSessionEventStore,
    SessionEventStore,
)
from nimbus_runtime.telemetry import RuntimeTelemetry, runtime_telemetry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from cloud_storage_api import CloudStorageClient

log: Any = structlog.get_logger()

_ConfirmationKind = Literal["delete_file", "copy_file", "move_file", "write_file"]
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
_SHA256_HEX_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
_DELETE_REQUEST_RE = re.compile(r"^\s*delete\s+(?P<remote_path>.+?)\s*$", re.IGNORECASE)
_DELETE_CONFIRM_RE = re.compile(
    r"^\s*yes\s*,\s*delete\s+(?P<remote_path>.+?)\s*$",
    re.IGNORECASE,
)
_UPLOAD_REQUEST_RE = re.compile(
    r"^\s*upload\s+(?:all\s+files\s+in\s+this\s+channel|these\s+files|attached\s+files)\s+to\s+(?P<prefix>.+?)\s*$",
    re.IGNORECASE,
)
_APPROVAL_TEXT_NORMALIZE_RE = re.compile(r"\s+")
_BARE_YES_RE = re.compile(r"^\s*yes[!.]?\s*$", re.IGNORECASE)
_STORAGE_MUTATION_ACTION_KINDS = frozenset(
    {
        ActionKind.DELETE_FILE,
        ActionKind.COPY_FILE,
        ActionKind.MOVE_FILE,
        ActionKind.WRITE_FILE,
    }
)

_session_locks: weakref.WeakValueDictionary[str, threading.Lock] = (
    weakref.WeakValueDictionary()
)
_session_locks_mu = threading.Lock()
_SESSION_LOCK_TIMEOUT_SECONDS = 30.0


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
class _StorageMutationExecution:
    """Outcome of executing one approved storage mutation action."""

    action: Action
    reply: str
    artifact: Artifact | None = None


@dataclass(frozen=True, slots=True)
class _ArtifactDraft:
    """Artifact fields that are known before the artifact ID exists."""

    tenant: TenantIdentity
    session_id: str
    action_id: str | None
    kind: Literal["delete_report", "storage_mutation_report", "upload_report"]
    payload: ArtifactPayload


@dataclass(frozen=True, slots=True)
class _RestoreSourceEvidence:
    """Best-effort pre-delete evidence used to build a restore story."""

    version_id: str | None
    size_bytes: int | None
    sha256_hex: str | None
    unavailable_reason: str | None = None

    @property
    def available(self) -> bool:
        """Return whether metadata was available before the delete."""
        return self.unavailable_reason is None


@asynccontextmanager
async def get_session_lock(session_id: str) -> AsyncIterator[None]:
    """Async context manager that serializes turns on the same conversation.

    Uses a threading.Lock so the mutex works across asyncio.run() boundaries —
    each Slack event dispatch creates its own event loop, so asyncio.Lock would
    not coordinate between concurrent events on the same conversation.
    """
    with _session_locks_mu:
        lock = _session_locks.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _session_locks[session_id] = lock

    acquired = await asyncio.to_thread(
        lock.acquire,
        blocking=True,
        timeout=_SESSION_LOCK_TIMEOUT_SECONDS,
    )
    if not acquired:
        msg = (
            f"session lock for {session_id!r} not acquired within "
            f"{_SESSION_LOCK_TIMEOUT_SECONDS}s"
        )
        raise TimeoutError(msg)
    try:
        yield
    finally:
        lock.release()


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


def _usage_path(session_dir: Path, session_id: str) -> Path:
    """Return the sidecar usage-log path for a session."""
    return session_dir / f"{_session_file_stem(session_id)}_usage.json"


def _json_object_from_text(raw: str) -> dict[str, object]:
    """Parse one JSON object from text."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        msg = "expected JSON object"
        raise TypeError(msg)
    parsed: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            msg = "expected JSON object string keys"
            raise TypeError(msg)
        parsed[key] = item
    return parsed


def _normalize_approval_text(text: str) -> str:
    """Collapse approval replies for exact, case-insensitive comparison."""
    return _APPROVAL_TEXT_NORMALIZE_RE.sub(" ", text.strip()).casefold()


def _required_nonempty_proposal_str(
    proposal: MappingABC[str, object],
    key: str,
) -> str:
    value = proposal.get(key)
    if isinstance(value, str) and value:
        return value
    msg = f"{key} must be a non-empty string"
    raise ValueError(msg)


def _proposal_bool(proposal: MappingABC[str, object], key: str) -> bool:
    value = proposal.get(key)
    if isinstance(value, bool):
        return value
    msg = f"{key} must be a boolean"
    raise ValueError(msg)


def _proposal_int(proposal: MappingABC[str, object], key: str) -> int:
    value = proposal.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = f"{key} must be a non-negative integer"
        raise ValueError(msg)
    return value


def _json_int(value: object) -> int:
    """Return an integer JSON value, defaulting malformed values to zero."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _json_float(value: object) -> float:
    """Return a numeric JSON value, defaulting malformed values to zero."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _update_session_usage(
    session_dir: Path,
    session_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
) -> None:
    """Accumulate token and cost data for one session in a sidecar JSON file.

    Reads the existing file (if any), increments the counters, and atomically
    replaces it.  Failures are swallowed so that a broken write never aborts
    a turn.
    """
    import contextlib  # noqa: PLC0415

    try:
        path = _usage_path(session_dir, session_id)
        existing: dict[str, object] = {}
        if path.is_file():
            with contextlib.suppress(TypeError, ValueError, OSError):
                existing = _json_object_from_text(path.read_text(encoding="utf-8"))
        existing["input_tokens"] = (
            _json_int(existing.get("input_tokens")) + input_tokens
        )
        existing["output_tokens"] = (
            _json_int(existing.get("output_tokens")) + output_tokens
        )
        if cost_usd is not None:
            existing["cost_usd_estimate"] = (
                _json_float(existing.get("cost_usd_estimate")) + cost_usd
            )
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # noqa: BLE001, S110
        pass


def load_session_usage(session_dir: Path, session_id: str) -> dict[str, object]:
    """Return the cumulative token/cost usage for a session, or an empty dict."""
    try:
        path = _usage_path(session_dir, session_id)
        if path.is_file():
            return _json_object_from_text(path.read_text(encoding="utf-8"))
    except (TypeError, ValueError, OSError):
        pass
    return {}


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
            "restore_plan": _restore_plan_summary(payload.restore_plan),
        }
    if isinstance(payload, UploadReport):
        return {
            "remote_path": payload.remote_path,
            "filename": payload.filename,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
        }
    if isinstance(payload, StorageMutationReport):
        return {
            "operation": payload.operation,
            "source_path": payload.source_path,
            "dest_path": payload.dest_path,
            "remote_path": payload.remote_path,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
            "overwrote": payload.overwrote,
            "source_deleted": payload.source_deleted,
            "dest_version_id": payload.dest_version_id,
            "verified": payload.verified,
            "verifier": payload.verifier,
        }
    if isinstance(payload, ObjectVerificationReport):
        failed_count = sum(1 for entry in payload.entries if not entry.verified)
        return {
            "verifier": payload.verifier,
            "subject": payload.subject,
            "verified": payload.verified,
            "entry_count": len(payload.entries),
            "failed_count": failed_count,
            "reason": payload.reason,
        }
    if isinstance(payload, ManifestReport):
        return {
            "source_platform": payload.source_platform,
            "workspace_id": payload.workspace_id,
            "channel_id": payload.channel_id,
            "destination_container": payload.destination_container,
            "destination_prefix": payload.destination_prefix,
            "scanned_count": payload.scanned_count,
            "matched_count": payload.matched_count,
            "saved_count": len(payload.object_entries),
            "failed_count": len(payload.failed_files),
            "truncated": payload.truncated,
            "verifier_artifact_id": payload.verifier_artifact_id,
        }
    if isinstance(payload, ProofReceipt):
        return {
            "receipt_id": payload.receipt_id,
            "subject": payload.subject,
            "outcome": payload.outcome,
            "summary": payload.summary,
            "task_id": payload.task_id,
            "action_id": payload.action_id,
            "manifest_artifact_id": payload.manifest_artifact_id,
            "verifier_artifact_id": payload.verifier_artifact_id,
            "linked_artifact_ids": list(payload.linked_artifact_ids),
            "event_range_start": payload.event_range_start,
            "event_range_end": payload.event_range_end,
            "next_steps": list(payload.next_steps),
        }
    msg = f"unsupported artifact payload: {type(payload).__name__}"
    raise TypeError(msg)


def _restore_plan_summary(plan: RestorePlan) -> dict[str, object]:
    """Return a JSON-safe restore plan summary for wrapper responses."""
    return {
        "original_key": plan.original_key,
        "strategy": plan.strategy.value,
        "restorable": plan.restorable,
        "trash_key": plan.trash_key,
        "version_id": plan.version_id,
        "sha256_hex": plan.sha256_hex,
        "size_bytes": plan.size_bytes,
        "deleted_by": plan.deleted_by,
        "deleted_at": None if plan.deleted_at is None else plan.deleted_at.isoformat(),
        "restore_command": plan.restore_command,
        "limitations": list(plan.limitations),
    }


def _delete_result_value(result: object, key: str) -> object | None:
    """Return a field from either dict-backed or attribute-backed delete results."""
    if isinstance(result, MappingABC):
        return result.get(key)
    return getattr(result, key, None)


def _delete_result_deleted(result: object) -> bool:
    """Return the provider-reported delete outcome, defaulting to prior behavior."""
    value = _delete_result_value(result, "deleted")
    return value if isinstance(value, bool) else True


def _delete_result_version_id(result: object) -> str | None:
    """Return the provider version ID from a delete result, if one exists."""
    value = _delete_result_value(result, "version_id")
    return value if isinstance(value, str) else None


def _object_info_value(info: object, key: str) -> object | None:
    """Return a field from either mapping-backed or attribute-backed metadata."""
    if isinstance(info, MappingABC):
        return info.get(key)
    return getattr(info, key, None)


def _object_info_size_bytes(info: object) -> int | None:
    """Return object size from provider metadata when it is trustworthy."""
    value = _object_info_value(info, "size_bytes")
    if isinstance(value, bool):
        return None
    return value if isinstance(value, int) and value >= 0 else None


def _object_info_version_id(info: object) -> str | None:
    """Return object version from provider metadata when present."""
    value = _object_info_value(info, "version_id")
    return value if isinstance(value, str) and value else None


def _object_info_sha256_hex(info: object) -> str | None:
    """Return a SHA-256 digest from provider metadata when one is exposed."""
    metadata = _object_info_value(info, "metadata")
    if isinstance(metadata, MappingABC):
        for key in ("sha256_hex", "sha256", "x-amz-meta-sha256"):
            value = metadata.get(key)
            if isinstance(value, str) and _SHA256_HEX_RE.fullmatch(value):
                return value.lower()
    integrity = _object_info_value(info, "integrity")
    if isinstance(integrity, str) and _SHA256_HEX_RE.fullmatch(integrity):
        return integrity.lower()
    return None


def _restore_source_from_metadata(info: object) -> _RestoreSourceEvidence:
    """Build restore evidence from provider metadata."""
    return _RestoreSourceEvidence(
        version_id=_object_info_version_id(info),
        size_bytes=_object_info_size_bytes(info),
        sha256_hex=_object_info_sha256_hex(info),
    )


def _restore_source_to_metadata(source: _RestoreSourceEvidence) -> dict[str, object]:
    """Serialize pre-delete restore evidence into plan metadata."""
    return {
        "version_id": source.version_id,
        "size_bytes": source.size_bytes,
        "sha256_hex": source.sha256_hex,
        "unavailable_reason": source.unavailable_reason,
    }


def _restore_source_from_plan_metadata(
    metadata: Mapping[str, object],
) -> _RestoreSourceEvidence:
    """Read pre-delete restore evidence from plan metadata."""
    raw = metadata.get("restore_source")
    if not isinstance(raw, MappingABC):
        return _RestoreSourceEvidence(
            version_id=None,
            size_bytes=None,
            sha256_hex=None,
            unavailable_reason="No pre-delete restore metadata was recorded.",
        )
    version_id = raw.get("version_id")
    size_bytes = raw.get("size_bytes")
    sha256_hex = raw.get("sha256_hex")
    unavailable_reason = raw.get("unavailable_reason")
    return _RestoreSourceEvidence(
        version_id=version_id if isinstance(version_id, str) and version_id else None,
        size_bytes=(
            size_bytes
            if isinstance(size_bytes, int) and not isinstance(size_bytes, bool)
            else None
        ),
        sha256_hex=(
            sha256_hex
            if isinstance(sha256_hex, str) and _SHA256_HEX_RE.fullmatch(sha256_hex)
            else None
        ),
        unavailable_reason=(
            unavailable_reason if isinstance(unavailable_reason, str) else None
        ),
    )


def _build_restore_plan(  # noqa: PLR0913 - restore receipts name every evidence field
    *,
    remote_path: str,
    source: _RestoreSourceEvidence,
    deleted: bool,
    delete_version_id: str | None,
    deleted_by: str,
    deleted_at: datetime,
) -> RestorePlan:
    """Create the restore story that accompanies a delete report."""
    if not deleted:
        return RestorePlan(
            original_key=remote_path,
            strategy=RestoreStrategy.NOT_REQUIRED,
            restorable=True,
            trash_key=None,
            version_id=delete_version_id or source.version_id,
            sha256_hex=source.sha256_hex,
            size_bytes=source.size_bytes,
            deleted_by=deleted_by,
            deleted_at=deleted_at,
            restore_command=None,
            limitations=("Provider reported no object was deleted.",),
        )
    if source.version_id is not None:
        return RestorePlan(
            original_key=remote_path,
            strategy=RestoreStrategy.S3_VERSION,
            restorable=True,
            trash_key=None,
            version_id=source.version_id,
            sha256_hex=source.sha256_hex,
            size_bytes=source.size_bytes,
            deleted_by=deleted_by,
            deleted_at=deleted_at,
            restore_command=(
                "Restore this object from provider version "
                f"`{source.version_id}` into `{remote_path}`."
            ),
            limitations=(
                "Requires provider support for version restore and caller "
                "permission to read that version.",
            ),
        )
    if delete_version_id is not None:
        return RestorePlan(
            original_key=remote_path,
            strategy=RestoreStrategy.S3_VERSION,
            restorable=True,
            trash_key=None,
            version_id=delete_version_id,
            sha256_hex=source.sha256_hex,
            size_bytes=source.size_bytes,
            deleted_by=deleted_by,
            deleted_at=deleted_at,
            restore_command=(
                "Provider returned delete version "
                f"`{delete_version_id}`; inspect bucket versions for "
                f"`{remote_path}` before restoring."
            ),
            limitations=(
                "Nimbus could not verify the prior object version through the "
                "current storage contract.",
            ),
        )
    limitations = [
        "Nimbus did not have a provider version ID for this object.",
        "The current CloudStorageClient contract has no copy-to-trash primitive.",
    ]
    if source.unavailable_reason is not None:
        limitations.append(source.unavailable_reason)
    return RestorePlan(
        original_key=remote_path,
        strategy=RestoreStrategy.UNAVAILABLE,
        restorable=False,
        trash_key=None,
        version_id=None,
        sha256_hex=source.sha256_hex,
        size_bytes=source.size_bytes,
        deleted_by=deleted_by,
        deleted_at=deleted_at,
        restore_command=None,
        limitations=tuple(limitations),
    )


def _approval_failure_text(reason: str, remote_path: str) -> str:
    """Return a user-facing explanation for a failed approval decision."""
    if reason == "wrong_actor":
        return (
            "Only the original requester or an allowed approver can approve "
            "this destructive action."
        )
    if reason == "expired":
        return (
            f"The approval for `{remote_path}` expired. "
            "Start the delete again if you still want to proceed."
        )
    if reason == "target_mismatch":
        return (
            f"The pending delete is for `{remote_path}`. "
            "Confirm that exact target to proceed."
        )
    if reason == "already_decided":
        return "This approval was already decided."
    return "Nimbus could not approve this destructive action."


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

    def __init__(  # noqa: PLR0913 - injected stores keep boundaries explicit
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
        plan_store: PlanStore | None = None,
        approval_store: ApprovalStore | None = None,
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
        if plan_store is not None:
            self._plan_store = plan_store
        elif postgres_enabled():
            self._plan_store = PostgresPlanStore(event_store=self._event_store)
        else:
            self._plan_store = FilePlanStore(
                session_dir,
                event_store=self._event_store,
            )
        if approval_store is not None:
            self._approval_store = approval_store
        elif postgres_enabled():
            self._approval_store = PostgresApprovalStore(event_store=self._event_store)
        else:
            self._approval_store = FileApprovalStore(
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
            pending = self._load_pending_storage_action(turn)
            confirmation_target = _extract_delete_confirmation(turn.text)
            delete_target = _extract_delete_target(turn.text)
            upload_prefix = _extract_upload_prefix(turn.text)

            if (
                pending is not None
                and pending.kind is not ActionKind.DELETE_FILE
                and self._confirmation_matches_action(turn.text, pending)
            ):
                result = await self._handle_storage_mutation_confirmation(
                    turn=turn,
                    pending=pending,
                )
            elif (
                pending is not None
                and pending.kind is ActionKind.DELETE_FILE
                and _BARE_YES_RE.match(turn.text)
            ):
                result = await self._handle_delete_confirmation(
                    turn=turn,
                    pending=pending,
                    confirmation_target=self._delete_remote_path(pending),
                )
            elif confirmation_target is not None:
                result = await self._handle_delete_confirmation(
                    turn=turn,
                    pending=(
                        pending
                        if pending is not None
                        and pending.kind is ActionKind.DELETE_FILE
                        else None
                    ),
                    confirmation_target=confirmation_target,
                )
            elif pending is not None and delete_target is not None:
                if pending.kind is ActionKind.DELETE_FILE and (
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
                    pending_remote_path = self._action_target_label(pending)
                    result = await self._persist_direct_result(
                        turn=turn,
                        text=(
                            "This conversation already has a pending destructive "
                            f"action for `{pending_remote_path}`. Confirm or wait "
                            "for it to expire before starting another delete."
                        ),
                        outcome="error",
                        suggested_next_actions=(
                            self._mutation_expected_reply(pending),
                        ),
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
                mutation_proposals: list[dict[str, object]] = []
                ai_response = await self._run_ai_interaction(
                    message=_message_with_attachment_context(
                        text=turn.text,
                        attachments=turn.attachments,
                    ),
                    session_id=turn.conversation_id,
                    tools=self._wrapper_tools(
                        turn=turn,
                        mutation_proposals=mutation_proposals,
                    ),
                )
                result = await self._result_for_model_response(
                    turn=turn,
                    pending=pending,
                    ai_response=ai_response,
                    mutation_proposals=mutation_proposals,
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
        conv = await asyncio.to_thread(
            _load_session, self._session_dir, session_id, self._system_prompt
        )
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
            await asyncio.to_thread(_save_session, self._session_dir, session_id, conv)
        except Exception:
            log.exception("runtime_session_save_failed", session_id=session_id)

        self._record_ai_response(ai_response, session_id=session_id)
        return ai_response

    async def _run_streaming_ai_interaction(
        self,
        turn: ChatTurnInput,
    ) -> AsyncIterator[NimbusEvent]:
        """Run one streaming model interaction and persist every emitted event."""
        conv = await asyncio.to_thread(
            _load_session,
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
        mutation_proposals: list[dict[str, object]] = []
        try:
            async for provider_event in self._ai_client.stream_message(
                conv,
                tools=self._wrapper_tools(
                    turn=turn,
                    mutation_proposals=mutation_proposals,
                ),
            ):
                event = self._append_provider_stream_event(
                    turn=turn,
                    provider_event=provider_event,
                )
                yield event
                if provider_event.kind == "request_completed":
                    response = provider_event.payload.get("response")
                    if isinstance(response, AIResponse):
                        self._record_ai_response(
                            response, session_id=turn.conversation_id
                        )
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
            await asyncio.to_thread(
                _save_session, self._session_dir, turn.conversation_id, conv
            )
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

    def _record_ai_response(
        self,
        ai_response: AIResponse,
        *,
        session_id: str | None = None,
    ) -> None:
        """Record telemetry for a completed model response.

        When ``session_id`` is provided, cumulative token and cost totals are
        also persisted to a per-session sidecar file so that ``task inspect``
        can surface live usage without querying an external metrics system.
        """
        self._telemetry.record_ai_response(
            model=ai_response.model,
            latency_ms=ai_response.latency_ms,
            fallback_used=ai_response.fallback_used,
            stop_reason=ai_response.stop_reason,
        )
        self._telemetry.record_ai_tokens(
            model=ai_response.model,
            input_tokens=ai_response.tokens.input_tokens,
            output_tokens=ai_response.tokens.output_tokens,
        )
        if ai_response.cost_usd_estimate is not None:
            self._telemetry.record_ai_cost(
                model=ai_response.model,
                cost_usd=ai_response.cost_usd_estimate,
            )
        if session_id is not None:
            _update_session_usage(
                self._session_dir,
                session_id,
                input_tokens=ai_response.tokens.input_tokens,
                output_tokens=ai_response.tokens.output_tokens,
                cost_usd=ai_response.cost_usd_estimate,
            )
        for tool_call in ai_response.tool_calls:
            self._telemetry.record_tool_call(
                tool_name=tool_call.name,
                success=tool_call.success,
                latency_ms=tool_call.latency_ms,
            )

    async def _result_for_model_response(
        self,
        *,
        turn: ChatTurnInput,
        pending: Action | None,
        ai_response: AIResponse,
        mutation_proposals: list[dict[str, object]],
    ) -> ChatTurnResult:
        if mutation_proposals:
            proposal = mutation_proposals[0]
            operation = proposal.get("operation")
            if pending is not None:
                pending_remote_path = self._action_target_label(pending)
                return await self._persist_direct_result(
                    turn=turn,
                    text=(
                        "This conversation already has a pending destructive "
                        f"action for `{pending_remote_path}`. Confirm or wait "
                        "for it to expire before starting another mutation."
                    ),
                    outcome="error",
                    suggested_next_actions=(self._mutation_expected_reply(pending),),
                    actions=(self._action_summary(pending),),
                )
            if operation == "delete_file":
                remote_path = proposal.get("remote_path")
                if isinstance(remote_path, str) and remote_path:
                    return await self._create_pending_delete(
                        turn=turn,
                        remote_path=remote_path,
                    )
            if operation in {"copy_file", "move_file", "write_file"}:
                return await self._create_pending_storage_mutation(
                    turn=turn,
                    proposal=proposal,
                )
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "Nimbus captured a model-requested storage mutation, but "
                    "that operation is not yet enabled through the runtime action "
                    "ledger. No storage was changed."
                ),
                outcome="error",
            )
        return ChatTurnResult(
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

    def _wrapper_tools(
        self,
        *,
        turn: ChatTurnInput | None = None,
        mutation_proposals: list[dict[str, object]] | None = None,
    ) -> list[Tool]:
        if (
            not self._storage_tools_enabled
            or self._storage is None
            or self._tool_container is None
        ):
            return []

        def collect_mutation_proposal(
            operation: str,
            args: Mapping[str, object],
        ) -> dict[str, object]:
            proposal = {
                "operation": operation,
                "status": "requires_runtime_action",
                **dict(args),
            }
            if mutation_proposals is not None:
                mutation_proposals.append(proposal)
            visible_args = {
                key: ("<redacted>" if key == "content_base64" else value)
                for key, value in dict(args).items()
            }
            return {
                "operation": operation,
                "proposal_required": True,
                "status": "requires_runtime_action",
                "message": (
                    "Nimbus accepted this as a storage mutation proposal. "
                    "The runtime action ledger must approve and execute it; "
                    "the model tool did not change storage."
                ),
                **visible_args,
            }

        mutation_handler = (
            collect_mutation_proposal
            if turn is not None and mutation_proposals is not None
            else None
        )
        return build_slack_tools(
            storage=self._storage,
            container=self._tool_container,
            include_delete_tool=True,
            mutation_proposal_handler=mutation_handler,
        )

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

    def _policy_context(self, *, turn: ChatTurnInput | None = None) -> PolicyContext:
        return PolicyContext(
            pinned_container=self._tool_container,
            max_upload_bytes=_MAX_UPLOAD_BYTES,
            current_channel_id=None if turn is None else turn.channel_id,
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
    def _derived_idempotency_key(*, prefix: str, action: Action) -> str:
        fingerprint = json.dumps(
            {
                "schema_version": 1,
                "prefix": prefix,
                "tenant": action.tenant.tenant_id,
                "action_idempotency_key": action.idempotency_key,
                "action_id": action.action_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"sha256-{hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _stable_record_id(*, prefix: str, idempotency_key: str) -> str:
        return f"{prefix}-{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"

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

    def _delete_prompt(
        self,
        action: Action,
        *,
        plan: Plan | None = None,
        approval: Approval | None = None,
    ) -> str:
        remote_path = self._delete_remote_path(action)
        plan_text = f" Plan `{plan.plan_id}` previews this change." if plan else ""
        approval_text = (
            f" Approval `{approval.approval_id}` is bound to this exact target."
            if approval
            else ""
        )
        return (
            f"I can delete `{remote_path}`, but this is destructive. "
            f"{plan_text}{approval_text} "
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

    def _mutation_expected_reply(self, action: Action) -> str:
        """Return the exact actor-visible confirmation phrase for one action."""
        if action.kind is ActionKind.DELETE_FILE:
            return self._delete_expected_reply(action)
        if isinstance(action.input, CopyFileInput):
            return f"yes, copy {action.input.source_path} to {action.input.dest_path}"
        if isinstance(action.input, MoveFileInput):
            return f"yes, move {action.input.source_path} to {action.input.dest_path}"
        if isinstance(action.input, WriteFileInput):
            return (
                f"yes, write {action.input.remote_path} "
                f"sha256:{action.input.content_sha256_hex}"
            )
        return f"yes, run {action.kind.value}"

    def _mutation_confirmation(self, action: Action) -> ConfirmationDetails:
        expires_at = action.expires_at or datetime.now(UTC)
        return ConfirmationDetails(
            action_id=action.action_id,
            kind=cast("_ConfirmationKind", action.kind.value),
            prompt=self._mutation_prompt(action),
            expected_reply=self._mutation_expected_reply(action),
            expires_at=expires_at.isoformat(),
        )

    def _mutation_prompt(
        self,
        action: Action,
        *,
        plan: Plan | None = None,
        approval: Approval | None = None,
    ) -> str:
        plan_text = f" Plan `{plan.plan_id}` previews this change." if plan else ""
        approval_text = (
            f" Approval `{approval.approval_id}` is bound to this exact target."
            if approval
            else ""
        )
        target = self._action_target_label(action)
        return (
            f"I can {self._action_verb(action)} `{target}`, but this changes "
            f"workspace storage.{plan_text}{approval_text} Reply with "
            f"`{self._mutation_expected_reply(action)}` if you want me to proceed."
        )

    @staticmethod
    def _confirmation_matches_action(text: str, action: Action) -> bool:
        if _BARE_YES_RE.match(text):
            return True
        return _normalize_approval_text(text) == _normalize_approval_text(
            NimbusRuntime._mutation_expected_reply_static(action)
        )

    @staticmethod
    def _mutation_expected_reply_static(action: Action) -> str:
        if action.kind is ActionKind.DELETE_FILE:
            remote_path = action.target.object_name if action.target else ""
            if isinstance(action.input, DeleteFileInput):
                remote_path = action.input.remote_path
            return f"yes, delete {remote_path}"
        if isinstance(action.input, CopyFileInput):
            return f"yes, copy {action.input.source_path} to {action.input.dest_path}"
        if isinstance(action.input, MoveFileInput):
            return f"yes, move {action.input.source_path} to {action.input.dest_path}"
        if isinstance(action.input, WriteFileInput):
            return (
                f"yes, write {action.input.remote_path} "
                f"sha256:{action.input.content_sha256_hex}"
            )
        return f"yes, run {action.kind.value}"

    @staticmethod
    def _action_verb(action: Action) -> str:
        return {
            ActionKind.COPY_FILE: "copy",
            ActionKind.DELETE_FILE: "delete",
            ActionKind.MOVE_FILE: "move",
            ActionKind.WRITE_FILE: "write",
        }.get(action.kind, "run")

    @staticmethod
    def _action_target_label(action: Action) -> str:
        if action.kind is ActionKind.DELETE_FILE:
            return NimbusRuntime._delete_remote_path(action)
        if isinstance(action.input, CopyFileInput | MoveFileInput):
            return f"{action.input.source_path} -> {action.input.dest_path}"
        if isinstance(action.input, WriteFileInput):
            return (
                f"{action.input.remote_path} "
                f"(sha256:{action.input.content_sha256_hex[:12]}..., "
                f"{action.input.size_bytes} bytes)"
            )
        if action.target is not None:
            return action.target.object_name
        return action.kind.value

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
        created = self._artifact_store.create(artifact=artifact, actor=actor)
        if draft.action_id is not None:
            self._create_action_proof_receipt(artifact=created, actor=actor)
        return created

    def _create_action_proof_receipt(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        receipt_id = deterministic_receipt_id(
            tenant=artifact.tenant,
            subject=artifact.kind,
            task_id=None,
            action_id=artifact.action_id,
            manifest_artifact_id=(
                artifact.artifact_id if artifact.kind == "manifest" else None
            ),
            verifier_artifact_id=(
                artifact.artifact_id
                if artifact.kind in {"verification_report", "storage_mutation_report"}
                else None
            ),
            linked_artifact_ids=(artifact.artifact_id,),
        )
        self._artifact_store.create(
            artifact=Artifact(
                artifact_id=receipt_id,
                tenant=artifact.tenant,
                session_id=artifact.session_id,
                action_id=artifact.action_id,
                kind="proof_receipt",
                uri=None,
                payload=ProofReceipt(
                    receipt_id=receipt_id,
                    tenant=artifact.tenant,
                    subject=artifact.kind,
                    outcome="succeeded",
                    summary=(
                        "Nimbus recorded a durable action artifact and this "
                        "receipt binds the user-visible success claim to that "
                        "evidence."
                    ),
                    task_id=None,
                    action_id=artifact.action_id,
                    manifest_artifact_id=(
                        artifact.artifact_id if artifact.kind == "manifest" else None
                    ),
                    verifier_artifact_id=(
                        artifact.artifact_id
                        if artifact.kind
                        in {"verification_report", "storage_mutation_report"}
                        else None
                    ),
                    linked_artifact_ids=(artifact.artifact_id,),
                    artifact_digests={
                        artifact.artifact_id: artifact.payload_digest or ""
                    },
                    session_id=artifact.session_id,
                    event_range_start=None,
                    event_range_end=None,
                    policy_version="runtime-default-v1",
                    idempotency_key=None,
                    next_steps=(
                        f"Run `nimbus artifact show {artifact.artifact_id}` "
                        "for the underlying evidence.",
                        f"Run `nimbus proof show {receipt_id} --json` for a "
                        "machine-readable proof bundle.",
                    ),
                    created_at=datetime.now(UTC),
                ),
                created_at=datetime.now(UTC),
            ),
            actor=actor,
        )

    def _create_delete_plan(
        self,
        *,
        action: Action,
        remote_path: str,
        restore_source: _RestoreSourceEvidence,
        now: datetime,
        expires_at: datetime,
    ) -> Plan:
        idempotency_key = self._derived_idempotency_key(
            prefix="delete-plan",
            action=action,
        )

        def create() -> Plan:
            return Plan(
                plan_id=self._stable_record_id(
                    prefix="plan",
                    idempotency_key=idempotency_key,
                ),
                tenant=action.tenant,
                session_id=action.session_id,
                task_id=None,
                action_id=action.action_id,
                created_by=action.actor,
                status=PlanStatus.PROPOSED,
                risk_level=PlanRiskLevel.DESTRUCTIVE,
                title=f"Delete {remote_path}",
                summary=(
                    f"Nimbus will delete `{remote_path}` from "
                    f"`{self._tool_container}` after approval."
                ),
                target=action.target,
                estimated_count=1,
                estimated_bytes=None,
                idempotency_key=idempotency_key,
                metadata={
                    "operation": ActionKind.DELETE_FILE.value,
                    "exact_target": remote_path,
                    "restore_source": _restore_source_to_metadata(restore_source),
                },
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )

        return self._plan_store.create_or_get_by_idempotency(
            tenant=action.tenant,
            idempotency_key=idempotency_key,
            create=create,
        )

    def _create_delete_approval(  # noqa: PLR0913 - approval binding is explicit.
        self,
        *,
        action: Action,
        plan: Plan,
        remote_path: str,
        policy_context: PolicyContext,
        now: datetime,
        expires_at: datetime,
    ) -> Approval:
        idempotency_key = self._derived_idempotency_key(
            prefix="delete-approval",
            action=action,
        )

        def create() -> Approval:
            return Approval(
                approval_id=self._stable_record_id(
                    prefix="appr",
                    idempotency_key=idempotency_key,
                ),
                tenant=action.tenant,
                session_id=action.session_id,
                task_id=None,
                plan_id=plan.plan_id,
                action_id=action.action_id,
                requested_by=action.actor,
                required_actor_id=action.actor.user_id,
                allowed_actor_ids=approval_actor_ids_for_action(
                    actor=action.actor,
                    action=action,
                    context=policy_context,
                    now=now,
                ),
                status=ApprovalStatus.PENDING,
                risk_level=PlanRiskLevel.DESTRUCTIVE,
                exact_target=remote_path,
                reason="delete_file_requires_exact_actor_bound_approval",
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )

        return self._approval_store.create_or_get_by_idempotency(
            tenant=action.tenant,
            idempotency_key=idempotency_key,
            create=create,
        )

    def _create_storage_mutation_plan(
        self,
        *,
        action: Action,
        now: datetime,
        expires_at: datetime,
    ) -> Plan:
        idempotency_key = self._derived_idempotency_key(
            prefix=f"{action.kind.value}-plan",
            action=action,
        )
        target = self._action_target_label(action)

        def create() -> Plan:
            return Plan(
                plan_id=self._stable_record_id(
                    prefix="plan",
                    idempotency_key=idempotency_key,
                ),
                tenant=action.tenant,
                session_id=action.session_id,
                task_id=None,
                action_id=action.action_id,
                created_by=action.actor,
                status=PlanStatus.PROPOSED,
                risk_level=PlanRiskLevel.DESTRUCTIVE,
                title=f"{self._action_verb(action).title()} {target}",
                summary=(
                    f"Nimbus will {self._action_verb(action)} `{target}` in "
                    f"`{self._tool_container}` after approval."
                ),
                target=action.target,
                estimated_count=1,
                estimated_bytes=(
                    action.input.size_bytes
                    if isinstance(action.input, WriteFileInput)
                    else None
                ),
                idempotency_key=idempotency_key,
                metadata={
                    "operation": action.kind.value,
                    "exact_target": self._approval_exact_target(action),
                    "target": target,
                },
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )

        return self._plan_store.create_or_get_by_idempotency(
            tenant=action.tenant,
            idempotency_key=idempotency_key,
            create=create,
        )

    def _create_storage_mutation_approval(
        self,
        *,
        action: Action,
        plan: Plan,
        now: datetime,
        expires_at: datetime,
    ) -> Approval:
        idempotency_key = self._derived_idempotency_key(
            prefix=f"{action.kind.value}-approval",
            action=action,
        )
        policy_context = self._policy_context()

        def create() -> Approval:
            return Approval(
                approval_id=self._stable_record_id(
                    prefix="appr",
                    idempotency_key=idempotency_key,
                ),
                tenant=action.tenant,
                session_id=action.session_id,
                task_id=None,
                plan_id=plan.plan_id,
                action_id=action.action_id,
                requested_by=action.actor,
                required_actor_id=action.actor.user_id,
                allowed_actor_ids=approval_actor_ids_for_action(
                    actor=action.actor,
                    action=action,
                    context=policy_context,
                    now=now,
                ),
                status=ApprovalStatus.PENDING,
                risk_level=PlanRiskLevel.DESTRUCTIVE,
                exact_target=self._approval_exact_target(action),
                reason=f"{action.kind.value}_requires_exact_actor_bound_approval",
                idempotency_key=idempotency_key,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )

        return self._approval_store.create_or_get_by_idempotency(
            tenant=action.tenant,
            idempotency_key=idempotency_key,
            create=create,
        )

    def _approval_exact_target(self, action: Action) -> str:
        if action.kind is ActionKind.DELETE_FILE:
            return self._delete_remote_path(action)
        return self._mutation_expected_reply(action)

    def _mark_plan_approved(
        self,
        *,
        tenant: TenantIdentity,
        approval: Approval,
        action: Action,
    ) -> None:
        if approval.plan_id is None:
            return
        self._plan_store.transition(
            tenant=tenant,
            plan_id=approval.plan_id,
            transition=PlanTransition(
                expected=PlanStatus.PROPOSED,
                next_status=PlanStatus.APPROVED,
                event_type="plan_approved",
                event_payload={
                    "approval_id": approval.approval_id,
                    "action_id": action.action_id,
                    "exact_target": self._approval_exact_target(action),
                },
            ),
        )

    def _mark_plan_applied(
        self,
        *,
        tenant: TenantIdentity,
        approval: Approval,
        action: Action,
        artifact: Artifact | None,
    ) -> None:
        if approval.plan_id is None:
            return
        self._plan_store.transition(
            tenant=tenant,
            plan_id=approval.plan_id,
            transition=PlanTransition(
                expected=PlanStatus.APPROVED,
                next_status=PlanStatus.APPLIED,
                event_type="plan_applied",
                event_payload={
                    "approval_id": approval.approval_id,
                    "action_id": action.action_id,
                    "artifact_id": None if artifact is None else artifact.artifact_id,
                },
            ),
        )

    def _inspect_restore_source(self, *, remote_path: str) -> _RestoreSourceEvidence:
        """Best-effort metadata read before a destructive delete."""
        if self._storage is None or self._tool_container is None:
            return _RestoreSourceEvidence(
                version_id=None,
                size_bytes=None,
                sha256_hex=None,
                unavailable_reason="Nimbus storage is not configured.",
            )
        try:
            info = self._storage.get_file_info(
                container=self._tool_container,
                object_name=remote_path,
            )
        except Exception as exc:  # noqa: BLE001 - storage clients raise provider-specific errors
            log.warning(
                "runtime_restore_source_inspection_failed",
                remote_path=remote_path,
                error=str(exc),
            )
            return _RestoreSourceEvidence(
                version_id=None,
                size_bytes=None,
                sha256_hex=None,
                unavailable_reason=(
                    f"Nimbus could not inspect the object before delete: {exc}"
                ),
            )
        return _restore_source_from_metadata(info)

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

    def _load_pending_storage_action(self, turn: ChatTurnInput) -> Action | None:
        """Return the newest unexpired storage mutation awaiting confirmation."""
        tenant = self._tenant_for_turn(turn)
        actions = self._action_store.list_for_session(
            tenant=tenant,
            session_id=turn.conversation_id,
        )
        pending = [
            action
            for action in actions
            if action.kind in _STORAGE_MUTATION_ACTION_KINDS
            and action.status is ActionStatus.AWAITING_CONFIRMATION
        ]
        newest: Action | None = None
        for action in pending:
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
                            "target": self._action_target_label(action),
                        },
                    ),
                )
                continue
            if newest is None or action.created_at > newest.created_at:
                newest = action
        return newest

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
            action = Action(
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
            policy_context = self._policy_context(turn=turn)
            return replace(
                action,
                policy_decision=authorize_action_with_record(
                    actor=actor,
                    action=action,
                    context=policy_context,
                    now=now,
                ),
            )

        action = self._action_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key=idempotency_key,
            create=create,
        )
        policy_context = self._policy_context(turn=turn)
        if action.policy_decision is None:
            policy_decision = authorize_action(
                actor=actor,
                action=action,
                context=policy_context,
            )
        else:
            policy_decision = action.policy_decision.decision
        if policy_decision is not PolicyDecision.REQUIRES_APPROVAL:
            return await self._persist_direct_result(
                turn=turn,
                text="Nimbus policy denied this delete request.",
                outcome="error",
                actions=(self._action_summary(action),),
            )
        restore_source = self._inspect_restore_source(remote_path=remote_path)
        plan = self._create_delete_plan(
            action=action,
            remote_path=remote_path,
            restore_source=restore_source,
            now=now,
            expires_at=expires_at,
        )
        approval = self._create_delete_approval(
            action=action,
            plan=plan,
            remote_path=remote_path,
            policy_context=policy_context,
            now=now,
            expires_at=expires_at,
        )
        return await self._persist_direct_result(
            turn=turn,
            text=self._delete_prompt(action, plan=plan, approval=approval),
            outcome="confirmation_required",
            suggested_next_actions=(self._delete_expected_reply(action),),
            confirmation=self._delete_confirmation(action),
            actions=(self._action_summary(action),),
        )

    async def _create_pending_storage_mutation(
        self,
        *,
        turn: ChatTurnInput,
        proposal: Mapping[str, object],
    ) -> ChatTurnResult:
        if self._storage is None or self._tool_container is None:
            return await self._persist_direct_result(
                turn=turn,
                text="Storage mutation unavailable: storage is not configured.",
                outcome="error",
            )
        try:
            action_kind, action_input, target_label, object_path = (
                self._mutation_input_from_proposal(proposal)
            )
        except ValueError as exc:
            return await self._persist_direct_result(
                turn=turn,
                text=f"Nimbus could not create that storage action: {exc}",
                outcome="error",
            )

        tenant = self._tenant_for_turn(turn)
        actor = self._actor_for_turn(turn, tenant=tenant)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=_PENDING_DELETE_TTL_SECONDS)
        idempotency_key = self._action_idempotency_key(
            turn=turn,
            actor=actor,
            action_kind=action_kind,
            target=target_label,
        )

        def create() -> Action:
            action = Action(
                action_id=f"act-{uuid.uuid4().hex}",
                tenant=tenant,
                session_id=turn.conversation_id,
                actor=actor,
                kind=action_kind,
                target=self._object_ref(object_path),
                status=ActionStatus.AWAITING_CONFIRMATION,
                idempotency_key=idempotency_key,
                input=action_input,
                result=None,
                failure=None,
                created_at=now,
                updated_at=now,
                expires_at=expires_at,
            )
            return replace(
                action,
                policy_decision=authorize_action_with_record(
                    actor=actor,
                    action=action,
                    context=self._policy_context(turn=turn),
                    now=now,
                ),
            )

        action = self._action_store.create_or_get_by_idempotency(
            tenant=tenant,
            idempotency_key=idempotency_key,
            create=create,
        )
        policy_decision = (
            action.policy_decision.decision
            if action.policy_decision is not None
            else authorize_action(
                actor=actor,
                action=action,
                context=self._policy_context(turn=turn),
            )
        )
        if policy_decision is not PolicyDecision.REQUIRES_APPROVAL:
            return await self._persist_direct_result(
                turn=turn,
                text="Nimbus policy denied this storage mutation request.",
                outcome="error",
                actions=(self._action_summary(action),),
            )
        plan = self._create_storage_mutation_plan(
            action=action,
            now=now,
            expires_at=expires_at,
        )
        approval = self._create_storage_mutation_approval(
            action=action,
            plan=plan,
            now=now,
            expires_at=expires_at,
        )
        return await self._persist_direct_result(
            turn=turn,
            text=self._mutation_prompt(action, plan=plan, approval=approval),
            outcome="confirmation_required",
            suggested_next_actions=(self._mutation_expected_reply(action),),
            confirmation=self._mutation_confirmation(action),
            actions=(self._action_summary(action),),
        )

    def _mutation_input_from_proposal(
        self,
        proposal: Mapping[str, object],
    ) -> tuple[ActionKind, CopyFileInput | MoveFileInput | WriteFileInput, str, str]:
        operation = proposal.get("operation")
        if operation == ActionKind.COPY_FILE.value:
            source = _required_nonempty_proposal_str(proposal, "source_path")
            dest = _required_nonempty_proposal_str(proposal, "dest_path")
            if source == dest:
                msg = "source_path and dest_path must differ"
                raise ValueError(msg)
            overwrite = _proposal_bool(proposal, "overwrite")
            return (
                ActionKind.COPY_FILE,
                CopyFileInput(source_path=source, dest_path=dest, overwrite=overwrite),
                f"{source}->{dest}:overwrite={overwrite}",
                dest,
            )
        if operation == ActionKind.MOVE_FILE.value:
            source = _required_nonempty_proposal_str(proposal, "source_path")
            dest = _required_nonempty_proposal_str(proposal, "dest_path")
            if source == dest:
                msg = "source_path and dest_path must differ"
                raise ValueError(msg)
            overwrite = _proposal_bool(proposal, "overwrite")
            return (
                ActionKind.MOVE_FILE,
                MoveFileInput(source_path=source, dest_path=dest, overwrite=overwrite),
                f"{source}->{dest}:overwrite={overwrite}",
                dest,
            )
        if operation == ActionKind.WRITE_FILE.value:
            remote_path = _required_nonempty_proposal_str(proposal, "remote_path")
            content_base64 = _required_nonempty_proposal_str(
                proposal,
                "content_base64",
            )
            content_sha256_hex = _required_nonempty_proposal_str(
                proposal,
                "content_sha256_hex",
            )
            size_bytes = _proposal_int(proposal, "content_bytes")
            encoding = _required_nonempty_proposal_str(proposal, "encoding")
            overwrite = _proposal_bool(proposal, "overwrite")
            return (
                ActionKind.WRITE_FILE,
                WriteFileInput(
                    remote_path=remote_path,
                    content_base64=content_base64,
                    content_sha256_hex=content_sha256_hex,
                    size_bytes=size_bytes,
                    encoding=encoding,
                    overwrite=overwrite,
                ),
                f"{remote_path}:sha256={content_sha256_hex}:overwrite={overwrite}",
                remote_path,
            )
        msg = f"unsupported storage mutation operation: {operation!r}"
        raise ValueError(msg)

    async def _handle_storage_mutation_confirmation(
        self,
        *,
        turn: ChatTurnInput,
        pending: Action,
    ) -> ChatTurnResult:
        if self._storage is None or self._tool_container is None:
            return await self._persist_direct_result(
                turn=turn,
                text="Storage mutation unavailable: storage is not configured.",
                outcome="error",
                actions=(self._action_summary(pending),),
            )
        tenant = self._tenant_for_turn(turn)
        actor = self._actor_for_turn(turn, tenant=tenant)
        expected_reply = self._mutation_expected_reply(pending)
        if not self._confirmation_matches_action(turn.text, pending):
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "That confirmation did not match the pending storage action. "
                    f"Reply with `{expected_reply}` to approve it."
                ),
                outcome="error",
                suggested_next_actions=(expected_reply,),
                actions=(self._action_summary(pending),),
            )
        approval = self._approval_store.find_pending_for_action(
            tenant=tenant,
            action_id=pending.action_id,
        )
        if approval is None:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "This storage action has no pending approval record. "
                    "Start it again to rebuild the safety envelope."
                ),
                outcome="error",
                actions=(self._action_summary(pending),),
            )
        decision = self._approval_store.decide(
            tenant=tenant,
            approval_id=approval.approval_id,
            actor=actor,
            choice=ApprovalChoice.APPROVE,
            exact_target=self._approval_exact_target(pending),
            now=datetime.now(UTC),
        )
        if not decision.accepted or decision.approval is None:
            return await self._persist_direct_result(
                turn=turn,
                text=_approval_failure_text(
                    decision.reason,
                    self._action_target_label(pending),
                ),
                outcome="error",
                suggested_next_actions=(expected_reply,),
                actions=(self._action_summary(pending),),
            )
        self._mark_plan_approved(
            tenant=tenant,
            approval=decision.approval,
            action=pending,
        )
        authorized = self._action_store.transition(
            tenant=tenant,
            action_id=pending.action_id,
            transition=ActionTransition(
                expected=ActionStatus.AWAITING_CONFIRMATION,
                next_status=ActionStatus.AUTHORIZED,
                event_type="action_authorized",
                event_payload={
                    "target": self._action_target_label(pending),
                    "authorized_by": turn.user_id,
                },
            ),
        )
        if authorized is None:
            return await self._persist_direct_result(
                turn=turn,
                text="This storage action is no longer waiting for confirmation.",
                outcome="error",
            )
        result = self._execute_storage_mutation(
            tenant=tenant,
            action=authorized,
            turn=turn,
        )
        if result.action.status is ActionStatus.SUCCEEDED:
            self._mark_plan_applied(
                tenant=tenant,
                approval=decision.approval,
                action=result.action,
                artifact=result.artifact,
            )
            return await self._persist_direct_result(
                turn=turn,
                text=result.reply,
                outcome="reply",
                actions=(self._action_summary(result.action),),
                artifacts=(
                    ()
                    if result.artifact is None
                    else (self._artifact_summary(result.artifact),)
                ),
            )
        return await self._persist_direct_result(
            turn=turn,
            text=result.reply,
            outcome="error",
            suggested_next_actions=(expected_reply,),
            actions=(self._action_summary(result.action),),
            artifacts=(
                ()
                if result.artifact is None
                else (self._artifact_summary(result.artifact),)
            ),
        )

    def _execute_storage_mutation(
        self,
        *,
        tenant: TenantIdentity,
        action: Action,
        turn: ChatTurnInput,
    ) -> _StorageMutationExecution:
        executing = self._action_store.transition(
            tenant=tenant,
            action_id=action.action_id,
            transition=ActionTransition(
                expected=ActionStatus.AUTHORIZED,
                next_status=ActionStatus.EXECUTING,
                event_type="action_started",
                event_payload={"target": self._action_target_label(action)},
            ),
        )
        if executing is None:
            return _StorageMutationExecution(
                action=action,
                reply="This storage action could not be started — state changed.",
            )
        try:
            if isinstance(executing.input, CopyFileInput):
                return self._execute_copy_file_action(
                    tenant=tenant,
                    action=executing,
                    turn=turn,
                    action_input=executing.input,
                )
            if isinstance(executing.input, MoveFileInput):
                return self._execute_move_file_action(
                    tenant=tenant,
                    action=executing,
                    turn=turn,
                    action_input=executing.input,
                )
            if isinstance(executing.input, WriteFileInput):
                return self._execute_write_file_action(
                    tenant=tenant,
                    action=executing,
                    turn=turn,
                    action_input=executing.input,
                )
        except Exception as exc:  # noqa: BLE001 - provider clients raise concrete SDK errors
            failed = self._fail_storage_mutation_action(
                tenant=tenant,
                action=executing,
                detail=str(exc),
                retryable=True,
            )
            return _StorageMutationExecution(
                action=failed or executing,
                reply=(
                    f"I could not {self._action_verb(executing)} "
                    f"`{self._action_target_label(executing)}` right now: {exc}"
                ),
            )
        failed = self._fail_storage_mutation_action(
            tenant=tenant,
            action=executing,
            detail="unsupported_action_input",
            retryable=False,
        )
        return _StorageMutationExecution(
            action=failed or executing,
            reply="Nimbus could not execute this unsupported storage action.",
        )

    def _execute_copy_file_action(
        self,
        *,
        tenant: TenantIdentity,
        action: Action,
        turn: ChatTurnInput,
        action_input: CopyFileInput,
    ) -> _StorageMutationExecution:
        self._refuse_existing_destination_if_needed(action_input)
        self._copy_storage_object(
            source=action_input.source_path,
            dest=action_input.dest_path,
        )
        info = self._storage_info(action_input.dest_path)
        result = CopyFileResult(
            source_path=action_input.source_path,
            dest_path=action_input.dest_path,
            overwrote=action_input.overwrite,
            dest_size_bytes=_object_info_size_bytes(info),
            dest_version_id=_object_info_version_id(info),
        )
        completed, artifact = self._complete_storage_mutation_action(
            tenant=tenant,
            action=action,
            turn=turn,
            result=result,
            report=StorageMutationReport(
                operation=ActionKind.COPY_FILE.value,
                source_path=action_input.source_path,
                dest_path=action_input.dest_path,
                remote_path=None,
                size_bytes=result.dest_size_bytes,
                sha256_hex=None,
                overwrote=action_input.overwrite,
                source_deleted=None,
                dest_version_id=result.dest_version_id,
                verified=True,
                verifier="destination_head",
            ),
        )
        return _StorageMutationExecution(
            action=completed,
            artifact=artifact,
            reply=f"Copied `{action_input.source_path}` to `{action_input.dest_path}`.",
        )

    def _execute_move_file_action(
        self,
        *,
        tenant: TenantIdentity,
        action: Action,
        turn: ChatTurnInput,
        action_input: MoveFileInput,
    ) -> _StorageMutationExecution:
        self._refuse_existing_destination_if_needed(action_input)
        self._copy_storage_object(
            source=action_input.source_path,
            dest=action_input.dest_path,
        )
        delete_result = self._delete_storage_object(action_input.source_path)
        source_deleted = _delete_result_deleted(delete_result)
        info = self._storage_info(action_input.dest_path)
        result = MoveFileResult(
            source_path=action_input.source_path,
            dest_path=action_input.dest_path,
            overwrote=action_input.overwrite,
            source_deleted=source_deleted,
            delete_version_id=_delete_result_version_id(delete_result),
            dest_size_bytes=_object_info_size_bytes(info),
            dest_version_id=_object_info_version_id(info),
        )
        completed, artifact = self._complete_storage_mutation_action(
            tenant=tenant,
            action=action,
            turn=turn,
            result=result,
            report=StorageMutationReport(
                operation=ActionKind.MOVE_FILE.value,
                source_path=action_input.source_path,
                dest_path=action_input.dest_path,
                remote_path=None,
                size_bytes=result.dest_size_bytes,
                sha256_hex=None,
                overwrote=action_input.overwrite,
                source_deleted=source_deleted,
                dest_version_id=result.dest_version_id,
                verified=True,
                verifier="destination_head_after_source_delete",
            ),
        )
        return _StorageMutationExecution(
            action=completed,
            artifact=artifact,
            reply=f"Moved `{action_input.source_path}` to `{action_input.dest_path}`.",
        )

    def _execute_write_file_action(
        self,
        *,
        tenant: TenantIdentity,
        action: Action,
        turn: ChatTurnInput,
        action_input: WriteFileInput,
    ) -> _StorageMutationExecution:
        existed = self._object_exists(action_input.remote_path)
        if existed and not action_input.overwrite:
            failed = self._fail_storage_mutation_action(
                tenant=tenant,
                action=action,
                detail="destination_exists_without_overwrite",
                retryable=False,
            )
            return _StorageMutationExecution(
                action=failed or action,
                reply=(
                    f"`{action_input.remote_path}` already exists. Start a new "
                    "write action with overwrite=true if replacement is intended."
                ),
            )
        payload = base64.b64decode(action_input.content_base64, validate=True)
        digest = hashlib.sha256(payload).hexdigest()
        if digest != action_input.content_sha256_hex:
            failed = self._fail_storage_mutation_action(
                tenant=tenant,
                action=action,
                detail="approved_content_hash_mismatch",
                retryable=False,
            )
            return _StorageMutationExecution(
                action=failed or action,
                reply="Approved write content failed its hash check before upload.",
            )
        if self._storage is None or self._tool_container is None:
            msg = "storage is not configured"
            raise ValueError(msg)
        self._storage.upload_obj(
            container=self._tool_container,
            file_obj=io.BytesIO(payload),
            remote_path=action_input.remote_path,
        )
        info = self._storage_info(action_input.remote_path)
        result = WriteFileResult(
            remote_path=action_input.remote_path,
            bytes_written=len(payload),
            sha256_hex=digest,
            encoding=action_input.encoding,
            overwrote=existed,
            dest_version_id=_object_info_version_id(info),
        )
        completed, artifact = self._complete_storage_mutation_action(
            tenant=tenant,
            action=action,
            turn=turn,
            result=result,
            report=StorageMutationReport(
                operation=ActionKind.WRITE_FILE.value,
                source_path=None,
                dest_path=None,
                remote_path=action_input.remote_path,
                size_bytes=len(payload),
                sha256_hex=digest,
                overwrote=existed,
                source_deleted=None,
                dest_version_id=result.dest_version_id,
                verified=_object_info_size_bytes(info) in {None, len(payload)},
                verifier="destination_head_size_and_content_hash",
            ),
        )
        return _StorageMutationExecution(
            action=completed,
            artifact=artifact,
            reply=f"Wrote {len(payload)} bytes to `{action_input.remote_path}`.",
        )

    def _complete_storage_mutation_action(
        self,
        *,
        tenant: TenantIdentity,
        action: Action,
        turn: ChatTurnInput,
        result: CopyFileResult | MoveFileResult | WriteFileResult,
        report: StorageMutationReport,
    ) -> tuple[Action, Artifact]:
        verifying = self._action_store.transition(
            tenant=tenant,
            action_id=action.action_id,
            transition=ActionTransition(
                expected=ActionStatus.EXECUTING,
                next_status=ActionStatus.VERIFYING,
                event_type="verification_started",
                event_payload={
                    "target": self._action_target_label(action),
                    "verified": report.verified,
                    "verifier": report.verifier,
                },
            ),
        )
        if verifying is None:
            msg = "action state changed before verification"
            raise RuntimeError(msg)
        artifact = self._create_artifact(
            _ArtifactDraft(
                tenant=tenant,
                session_id=turn.conversation_id,
                action_id=verifying.action_id,
                kind="storage_mutation_report",
                payload=report,
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
                    "target": self._action_target_label(action),
                    "artifact_id": artifact.artifact_id,
                    "verified": report.verified,
                },
                result=result.with_artifact(artifact.artifact_id),
            ),
        )
        return completed or verifying, artifact

    def _fail_storage_mutation_action(
        self,
        *,
        tenant: TenantIdentity,
        action: Action,
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
                event_payload={
                    "target": self._action_target_label(action),
                    "detail": detail,
                },
                failure=ActionFailure(
                    detail=detail,
                    remote_path=(
                        action.target.object_name if action.target is not None else None
                    ),
                ),
            ),
        )

    def _refuse_existing_destination_if_needed(
        self,
        action_input: CopyFileInput | MoveFileInput,
    ) -> None:
        if action_input.overwrite:
            return
        if self._object_exists(action_input.dest_path):
            msg = (
                f"destination {action_input.dest_path!r} already exists; "
                "start a new action with overwrite=true if replacement is intended"
            )
            raise ValueError(msg)

    def _object_exists(self, remote_path: str) -> bool:
        try:
            self._storage_info(remote_path)
        except ObjectNotFoundError:
            return False
        return True

    def _storage_info(self, remote_path: str) -> object:
        if self._storage is None or self._tool_container is None:
            msg = "storage is not configured"
            raise ValueError(msg)
        return self._storage.get_file_info(
            container=self._tool_container,
            object_name=remote_path,
        )

    def _copy_storage_object(self, *, source: str, dest: str) -> None:
        if self._storage is None or self._tool_container is None:
            msg = "storage is not configured"
            raise ValueError(msg)
        if hasattr(self._storage, "copy_object"):
            self._storage.copy_object(
                src_container=self._tool_container,
                src_key=source,
                dst_container=self._tool_container,
                dst_key=dest,
            )
            return
        if hasattr(self._storage, "read_object"):
            body = self._storage.read_object(container=self._tool_container, key=source)
            self._storage.upload_obj(
                container=self._tool_container,
                file_obj=io.BytesIO(body),
                remote_path=dest,
            )
            return
        with tempfile.NamedTemporaryFile(suffix=".nimbus-copy", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            self._storage.download_file(
                container=self._tool_container,
                object_name=source,
                file_name=tmp_path,
            )
            self._storage.upload_file(
                container=self._tool_container,
                local_path=tmp_path,
                remote_path=dest,
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _delete_storage_object(self, remote_path: str) -> object:
        if self._storage is None or self._tool_container is None:
            msg = "storage is not configured"
            raise ValueError(msg)
        if hasattr(self._storage, "force_delete"):
            return self._storage.force_delete(
                container=self._tool_container,
                key=remote_path,
            )
        return self._storage.delete_file(
            container=self._tool_container,
            object_name=remote_path,
        )

    async def _handle_delete_confirmation(
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
        if self._storage is None or self._tool_container is None:
            return await self._persist_direct_result(
                turn=turn,
                text="Delete is unavailable because Nimbus storage is not configured.",
                outcome="error",
                actions=(self._action_summary(pending),),
            )
        tenant = self._tenant_for_turn(turn)
        actor = self._actor_for_turn(turn, tenant=tenant)
        approval = self._approval_store.find_pending_for_action(
            tenant=tenant,
            action_id=pending.action_id,
        )
        if approval is None:
            return await self._persist_direct_result(
                turn=turn,
                text=(
                    "This delete has no pending approval record. "
                    "Start the delete again to rebuild the safety envelope."
                ),
                outcome="error",
                actions=(self._action_summary(pending),),
            )
        decision = self._approval_store.decide(
            tenant=tenant,
            approval_id=approval.approval_id,
            actor=actor,
            choice=ApprovalChoice.APPROVE,
            exact_target=confirmation_target,
            now=datetime.now(UTC),
        )
        if not decision.accepted or decision.approval is None:
            suggested_next_actions = (
                (expected_reply,)
                if decision.reason in {"wrong_actor", "target_mismatch"}
                else (f"delete {remote_path}",)
            )
            return await self._persist_direct_result(
                turn=turn,
                text=_approval_failure_text(decision.reason, remote_path),
                outcome="error",
                suggested_next_actions=suggested_next_actions,
                actions=(self._action_summary(pending),),
            )
        if decision.approval.plan_id is not None:
            self._plan_store.transition(
                tenant=tenant,
                plan_id=decision.approval.plan_id,
                transition=PlanTransition(
                    expected=PlanStatus.PROPOSED,
                    next_status=PlanStatus.APPROVED,
                    event_type="plan_approved",
                    event_payload={
                        "approval_id": decision.approval.approval_id,
                        "action_id": pending.action_id,
                        "exact_target": remote_path,
                    },
                ),
            )
        restore_source = _RestoreSourceEvidence(
            version_id=None,
            size_bytes=None,
            sha256_hex=None,
            unavailable_reason="No pre-delete restore metadata was recorded.",
        )
        if decision.approval.plan_id is not None:
            plan = self._plan_store.get(
                tenant=tenant,
                plan_id=decision.approval.plan_id,
            )
            if plan is not None:
                restore_source = _restore_source_from_plan_metadata(plan.metadata)
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

        deleted = _delete_result_deleted(result)
        delete_result = DeleteFileResult(
            remote_path=remote_path,
            deleted=deleted,
            version_id=_delete_result_version_id(result),
        )
        deleted_at = datetime.now(UTC)
        restore_plan = _build_restore_plan(
            remote_path=remote_path,
            source=restore_source,
            deleted=deleted,
            delete_version_id=delete_result.version_id,
            deleted_by=actor.user_id,
            deleted_at=deleted_at,
        )
        verifying = self._action_store.transition(
            tenant=tenant,
            action_id=executing.action_id,
            transition=ActionTransition(
                expected=ActionStatus.EXECUTING,
                next_status=ActionStatus.VERIFYING,
                event_type="verification_started",
                event_payload={
                    "remote_path": remote_path,
                    "deleted": deleted,
                    "restore_strategy": restore_plan.strategy.value,
                    "restorable": restore_plan.restorable,
                },
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
                        restore_plan=restore_plan,
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
                        "restore_strategy": restore_plan.strategy.value,
                        "restorable": restore_plan.restorable,
                    },
                    result=delete_result.with_artifact(artifact.artifact_id),
                ),
            )
        if completed is not None and decision.approval.plan_id is not None:
            self._plan_store.transition(
                tenant=tenant,
                plan_id=decision.approval.plan_id,
                transition=PlanTransition(
                    expected=PlanStatus.APPROVED,
                    next_status=PlanStatus.APPLIED,
                    event_type="plan_applied",
                    event_payload={
                        "approval_id": decision.approval.approval_id,
                        "action_id": completed.action_id,
                        "artifact_id": (
                            None if artifact is None else artifact.artifact_id
                        ),
                    },
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
        if action.policy_decision is not None:
            return action.policy_decision.decision is not PolicyDecision.ALLOW
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
            action = Action(
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
            return replace(
                action,
                policy_decision=authorize_action_with_record(
                    actor=actor,
                    action=action,
                    context=self._policy_context(turn=turn),
                    now=now,
                ),
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
