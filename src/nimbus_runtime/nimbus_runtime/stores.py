"""Nimbus action, artifact, and session event stores.

Render deployments use Postgres-backed stores. Local development and tests keep
the ``File*Store`` classes, which use SQLite under ``AI_SESSION_DIR`` to provide
one transaction boundary for idempotency, action state, and audit events.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

from nimbus_runtime.domain import (
    Action,
    ActionFailure,
    ActionInput,
    ActionKind,
    ActionResult,
    ActionStatus,
    ActionTransition,
    ActorAuthSource,
    Approval,
    ApprovalChoice,
    ApprovalDecisionResult,
    ApprovalStatus,
    Artifact,
    ArtifactKind,
    ArtifactPayload,
    ConflictArtifact,
    CopyFileInput,
    CopyFileResult,
    DeleteFileInput,
    DeleteFileResult,
    DeleteReport,
    DriftObjectEntry,
    DriftObjectStatus,
    DriftReport,
    GenerationManifest,
    ManifestFailureEntry,
    ManifestObjectEntry,
    ManifestReport,
    MigrationDecisionPacket,
    MoveFileInput,
    MoveFileResult,
    ObjectPointer,
    ObjectRef,
    ObjectVerificationEntry,
    ObjectVerificationReport,
    Plan,
    PlanRiskLevel,
    PlanStatus,
    PlanTransition,
    PolicyDecision,
    PolicyDecisionRecord,
    ProofReceipt,
    ProviderHealthReport,
    ProviderName,
    ProviderOutcome,
    ProviderProbeResult,
    RepairReceipt,
    RestorePlan,
    RestoreStrategy,
    SessionEvent,
    StorageMutationReport,
    Task,
    TaskStatus,
    TaskTransition,
    TenantIdentity,
    UploadAttachmentInput,
    UploadAttachmentResult,
    UploadReport,
    VerifiedActor,
    WorkerLease,
    WriteFileInput,
    WriteFileResult,
    validate_action_transition,
    validate_approval_transition,
    validate_plan_transition,
    validate_task_transition,
)
from nimbus_runtime.postgres import connect as pg_connect
from nimbus_runtime.postgres import transaction as pg_transaction
from nimbus_runtime.proof import artifact_payload_digest, ensure_artifact_digest

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence
    from pathlib import Path

    from psycopg import Connection as PostgresConnection
_DB_FILENAME = "nimbus_runtime.sqlite3"
_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5_000
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class SessionEventStore(Protocol):
    """Durable ordered event store for Nimbus sessions."""

    def append(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        event_type: str,
        actor: VerifiedActor | None,
        payload: Mapping[str, object],
    ) -> SessionEvent:
        """Append one event and return it with a sequence number."""

    def list_events(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> Sequence[SessionEvent]:
        """Return ordered events for one tenant-scoped session."""

    def list_for_tenant_before(
        self,
        *,
        tenant: TenantIdentity,
        before: datetime,
        limit: int = 10_000,
    ) -> Sequence[SessionEvent]:
        """Return tenant events with created_at <= before, ordered chronologically.

        Results are ordered by (created_at ASC, event_id ASC). Use this for
        time-travel projections that need cross-session coverage.
        """


class ActionStore(Protocol):
    """Durable store for action creation and state transitions."""

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Action],
    ) -> Action:
        """Create an action once for a logical tenant-scoped request."""

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
        transition: ActionTransition,
    ) -> Action | None:
        """Move an action only if it is still in the expected state."""

    def get(self, *, tenant: TenantIdentity, action_id: str) -> Action | None:
        """Return one tenant-scoped action if it exists."""

    def find_latest_awaiting_confirmation(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        kind: ActionKind,
    ) -> Action | None:
        """Return the newest action waiting for confirmation in one session."""

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Action]:
        """Return all actions for one tenant-scoped session."""


class TaskStore(Protocol):
    """Durable store for background task creation and state transitions."""

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Task],
    ) -> Task:
        """Create a task once for a logical tenant-scoped request."""

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        transition: TaskTransition,
    ) -> Task | None:
        """Move a task only if it is still in the expected state."""

    def get(self, *, tenant: TenantIdentity, task_id: str) -> Task | None:
        """Return one tenant-scoped task if it exists."""

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        status: TaskStatus | None = None,
        limit: int = 100,
    ) -> Sequence[Task]:
        """Return recent tasks for one tenant."""


class PlanStore(Protocol):
    """Durable store for preview plans."""

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Plan],
    ) -> Plan:
        """Create a plan once for a logical tenant-scoped preview."""

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        plan_id: str,
        transition: PlanTransition,
    ) -> Plan | None:
        """Move a plan only if it is still in the expected state."""

    def approve_candidate_group(
        self,
        *,
        tenant: TenantIdentity,
        plan_id: str,
        candidate_group_id: str,
        event_payload: Mapping[str, object],
    ) -> Plan | None:
        """Approve one candidate plan and supersede siblings atomically."""

    def get(self, *, tenant: TenantIdentity, plan_id: str) -> Plan | None:
        """Return one tenant-scoped plan if it exists."""

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Plan]:
        """Return plans for one tenant-scoped session."""


class ApprovalStore(Protocol):
    """Durable store for actor-bound approvals."""

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Approval],
    ) -> Approval:
        """Create an approval once for a logical tenant-scoped request."""

    def decide(  # noqa: PLR0913 - approval binding is explicit at the boundary
        self,
        *,
        tenant: TenantIdentity,
        approval_id: str,
        actor: VerifiedActor,
        choice: ApprovalChoice,
        exact_target: str,
        now: datetime,
        note: str | None = None,
    ) -> ApprovalDecisionResult:
        """Attempt to approve or reject one approval record."""

    def get(self, *, tenant: TenantIdentity, approval_id: str) -> Approval | None:
        """Return one tenant-scoped approval if it exists."""

    def find_pending_for_action(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
    ) -> Approval | None:
        """Return the pending approval for an action, if one exists."""


class WorkerLeaseStore(Protocol):
    """Durable coordination store for task worker leases."""

    def acquire(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> WorkerLease | None:
        """Claim a task if no active lease exists."""

    def heartbeat(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> WorkerLease | None:
        """Extend an active lease owned by ``worker_id``."""

    def release(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
    ) -> bool:
        """Release an active lease owned by ``worker_id``."""

    def get(self, *, tenant: TenantIdentity, task_id: str) -> WorkerLease | None:
        """Return the current lease for one tenant-scoped task."""


class ArtifactStore(Protocol):
    """Durable store for Nimbus evidence and work products."""

    def create(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None = None,
    ) -> Artifact:
        """Persist one immutable artifact."""

    def get(
        self,
        *,
        tenant: TenantIdentity,
        artifact_id: str,
    ) -> Artifact | None:
        """Return one artifact by ID, or ``None`` if not found."""

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Artifact]:
        """Return artifacts for one tenant-scoped session."""

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        kind: ArtifactKind | None = None,
        limit: int = 100,
    ) -> Sequence[Artifact]:
        """Return recent artifacts for one tenant, optionally by kind."""


@dataclass(frozen=True, slots=True)
class _EventAppend:
    """Append-only event payload inside one SQLite transaction."""

    tenant: TenantIdentity
    session_id: str
    event_type: str
    actor: VerifiedActor | None
    payload: Mapping[str, object]


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[resolved] = lock
        return lock


def _json_dumps(payload: Mapping[str, object] | None) -> str | None:
    if payload is None:
        return None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _json_loads_object(raw: str | None, *, field: str) -> dict[str, object] | None:
    if raw is None:
        return None
    value = json.loads(raw)
    if not isinstance(value, dict):
        msg = f"expected JSON object for {field}"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _required_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        msg = f"expected string field {key!r}"
        raise TypeError(msg)
    return value


def _optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"expected optional string field {key!r}"
    raise TypeError(msg)


def _required_int(data: Mapping[str, object], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"expected integer field {key!r}"
        raise TypeError(msg)
    return value


def _optional_int(data: Mapping[str, object], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"expected optional integer field {key!r}"
        raise TypeError(msg)
    return value


def _required_float(data: Mapping[str, object], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"expected numeric field {key!r}"
        raise TypeError(msg)
    return float(value)


def _required_bool(data: Mapping[str, object], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        msg = f"expected boolean field {key!r}"
        raise TypeError(msg)
    return value


def _required_mapping(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        msg = f"expected object field {key!r}"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _required_sequence(data: Mapping[str, object], key: str) -> list[object]:
    value = data.get(key)
    if not isinstance(value, list):
        msg = f"expected array field {key!r}"
        raise TypeError(msg)
    return value


def _optional_mapping(
    data: Mapping[str, object],
    key: str,
) -> dict[str, object] | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, dict):
        return cast("dict[str, object]", value)
    msg = f"expected optional object field {key!r}"
    raise TypeError(msg)


def _datetime_to_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime_from_json(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime_from_json(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _tenant_to_json(tenant: TenantIdentity) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "platform": tenant.platform,
        "workspace_id": tenant.workspace_id,
    }


def _tenant_from_json(data: Mapping[str, object]) -> TenantIdentity:
    return TenantIdentity(
        platform=_required_str(data, "platform"),
        workspace_id=_required_str(data, "workspace_id"),
    )


def _actor_to_json(actor: VerifiedActor | None) -> dict[str, object] | None:
    if actor is None:
        return None
    return {
        "schema_version": _SCHEMA_VERSION,
        "tenant": _tenant_to_json(actor.tenant),
        "user_id": actor.user_id,
        "auth_source": actor.auth_source,
        "bridge_id": actor.bridge_id,
        "verified_at": _datetime_to_json(actor.verified_at),
        "principal_key": actor.principal_key,
    }


def _actor_from_json(data: Mapping[str, object] | None) -> VerifiedActor | None:
    if data is None:
        return None
    return VerifiedActor(
        tenant=_tenant_from_json(_required_mapping(data, "tenant")),
        user_id=_required_str(data, "user_id"),
        auth_source=cast("ActorAuthSource", _required_str(data, "auth_source")),
        bridge_id=_optional_str(data, "bridge_id"),
        verified_at=_datetime_from_json(_required_str(data, "verified_at")),
    )


def _task_metadata_to_json(metadata: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "values": dict(metadata),
    }


def _task_metadata_from_json(data: Mapping[str, object] | None) -> dict[str, object]:
    if data is None:
        return {}
    values = data.get("values")
    if values is None:
        return {}
    if isinstance(values, dict):
        return cast("dict[str, object]", values)
    msg = "expected object field 'values'"
    raise TypeError(msg)


def _metadata_to_json(metadata: Mapping[str, object]) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "values": dict(metadata),
    }


def _metadata_from_json(data: Mapping[str, object] | None) -> dict[str, object]:
    if data is None:
        return {}
    values = data.get("values")
    if values is None:
        return {}
    if isinstance(values, dict):
        return cast("dict[str, object]", values)
    msg = "expected object field 'values'"
    raise TypeError(msg)


def _string_tuple_to_json(values: Sequence[str]) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "values": list(values),
    }


def _string_tuple_from_json(data: Mapping[str, object] | None) -> tuple[str, ...]:
    if data is None:
        return ()
    values = data.get("values")
    if values is None:
        return ()
    if not isinstance(values, list):
        msg = "expected array field 'values'"
        raise TypeError(msg)
    parsed: list[str] = []
    for value in values:
        if not isinstance(value, str):
            msg = "expected string actor id in 'values'"
            raise TypeError(msg)
        parsed.append(value)
    return tuple(parsed)


def _object_ref_to_json(target: ObjectRef | None) -> dict[str, object] | None:
    if target is None:
        return None
    return {
        "schema_version": _SCHEMA_VERSION,
        "provider": target.provider,
        "container": target.container,
        "object_name": target.object_name,
        "version_id": target.version_id,
    }


def _object_ref_from_json(data: Mapping[str, object] | None) -> ObjectRef | None:
    if data is None:
        return None
    return ObjectRef(
        provider=cast("ProviderName", _required_str(data, "provider")),
        container=_required_str(data, "container"),
        object_name=_required_str(data, "object_name"),
        version_id=_optional_str(data, "version_id"),
    )


def _action_input_to_json(payload: ActionInput) -> dict[str, object]:
    if isinstance(payload, DeleteFileInput):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.DELETE_FILE.value,
            "remote_path": payload.remote_path,
        }
    if isinstance(payload, UploadAttachmentInput):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.UPLOAD_ATTACHMENT.value,
            "platform_file_id": payload.platform_file_id,
            "filename": payload.filename,
            "content_type": payload.content_type,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
            "remote_path": payload.remote_path,
        }
    if isinstance(payload, CopyFileInput):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.COPY_FILE.value,
            "source_path": payload.source_path,
            "dest_path": payload.dest_path,
            "overwrite": payload.overwrite,
        }
    if isinstance(payload, MoveFileInput):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.MOVE_FILE.value,
            "source_path": payload.source_path,
            "dest_path": payload.dest_path,
            "overwrite": payload.overwrite,
        }
    if isinstance(payload, WriteFileInput):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.WRITE_FILE.value,
            "remote_path": payload.remote_path,
            "content_base64": payload.content_base64,
            "content_sha256_hex": payload.content_sha256_hex,
            "size_bytes": payload.size_bytes,
            "encoding": payload.encoding,
            "overwrite": payload.overwrite,
        }
    msg = f"unsupported action input: {type(payload).__name__}"
    raise TypeError(msg)


def _action_input_from_json(
    *, kind: ActionKind, data: Mapping[str, object]
) -> ActionInput:
    if kind is ActionKind.DELETE_FILE:
        return DeleteFileInput(remote_path=_required_str(data, "remote_path"))
    if kind is ActionKind.UPLOAD_ATTACHMENT:
        return UploadAttachmentInput(
            platform_file_id=_required_str(data, "platform_file_id"),
            filename=_required_str(data, "filename"),
            content_type=_required_str(data, "content_type"),
            size_bytes=_required_int(data, "size_bytes"),
            sha256_hex=_optional_str(data, "sha256_hex"),
            remote_path=_required_str(data, "remote_path"),
        )
    if kind is ActionKind.COPY_FILE:
        return CopyFileInput(
            source_path=_required_str(data, "source_path"),
            dest_path=_required_str(data, "dest_path"),
            overwrite=_required_bool(data, "overwrite"),
        )
    if kind is ActionKind.MOVE_FILE:
        return MoveFileInput(
            source_path=_required_str(data, "source_path"),
            dest_path=_required_str(data, "dest_path"),
            overwrite=_required_bool(data, "overwrite"),
        )
    if kind is ActionKind.WRITE_FILE:
        return WriteFileInput(
            remote_path=_required_str(data, "remote_path"),
            content_base64=_required_str(data, "content_base64"),
            content_sha256_hex=_required_str(data, "content_sha256_hex"),
            size_bytes=_required_int(data, "size_bytes"),
            encoding=_required_str(data, "encoding"),
            overwrite=_required_bool(data, "overwrite"),
        )
    msg = f"unsupported action input kind: {kind.value}"
    raise ValueError(msg)


def _action_result_to_json(payload: ActionResult | None) -> dict[str, object] | None:
    if payload is None:
        return None
    if isinstance(payload, DeleteFileResult):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.DELETE_FILE.value,
            "remote_path": payload.remote_path,
            "deleted": payload.deleted,
            "version_id": payload.version_id,
            "artifact_id": payload.artifact_id,
        }
    if isinstance(payload, UploadAttachmentResult):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.UPLOAD_ATTACHMENT.value,
            "remote_path": payload.remote_path,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
            "artifact_id": payload.artifact_id,
        }
    if isinstance(payload, CopyFileResult):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.COPY_FILE.value,
            "source_path": payload.source_path,
            "dest_path": payload.dest_path,
            "overwrote": payload.overwrote,
            "dest_size_bytes": payload.dest_size_bytes,
            "dest_version_id": payload.dest_version_id,
            "artifact_id": payload.artifact_id,
        }
    if isinstance(payload, MoveFileResult):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.MOVE_FILE.value,
            "source_path": payload.source_path,
            "dest_path": payload.dest_path,
            "overwrote": payload.overwrote,
            "source_deleted": payload.source_deleted,
            "delete_version_id": payload.delete_version_id,
            "dest_size_bytes": payload.dest_size_bytes,
            "dest_version_id": payload.dest_version_id,
            "artifact_id": payload.artifact_id,
        }
    if isinstance(payload, WriteFileResult):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": ActionKind.WRITE_FILE.value,
            "remote_path": payload.remote_path,
            "bytes_written": payload.bytes_written,
            "sha256_hex": payload.sha256_hex,
            "encoding": payload.encoding,
            "overwrote": payload.overwrote,
            "dest_version_id": payload.dest_version_id,
            "artifact_id": payload.artifact_id,
        }
    msg = f"unsupported action result: {type(payload).__name__}"
    raise TypeError(msg)


def _action_result_from_json(
    *, kind: ActionKind, data: Mapping[str, object] | None
) -> ActionResult | None:
    if data is None:
        return None
    if kind is ActionKind.DELETE_FILE:
        return DeleteFileResult(
            remote_path=_required_str(data, "remote_path"),
            deleted=_required_bool(data, "deleted"),
            version_id=_optional_str(data, "version_id"),
            artifact_id=_optional_str(data, "artifact_id"),
        )
    if kind is ActionKind.UPLOAD_ATTACHMENT:
        return UploadAttachmentResult(
            remote_path=_required_str(data, "remote_path"),
            size_bytes=_required_int(data, "size_bytes"),
            sha256_hex=_required_str(data, "sha256_hex"),
            artifact_id=_optional_str(data, "artifact_id"),
        )
    if kind is ActionKind.COPY_FILE:
        return CopyFileResult(
            source_path=_required_str(data, "source_path"),
            dest_path=_required_str(data, "dest_path"),
            overwrote=_required_bool(data, "overwrote"),
            dest_size_bytes=_optional_int(data, "dest_size_bytes"),
            dest_version_id=_optional_str(data, "dest_version_id"),
            artifact_id=_optional_str(data, "artifact_id"),
        )
    if kind is ActionKind.MOVE_FILE:
        return MoveFileResult(
            source_path=_required_str(data, "source_path"),
            dest_path=_required_str(data, "dest_path"),
            overwrote=_required_bool(data, "overwrote"),
            source_deleted=_required_bool(data, "source_deleted"),
            delete_version_id=_optional_str(data, "delete_version_id"),
            dest_size_bytes=_optional_int(data, "dest_size_bytes"),
            dest_version_id=_optional_str(data, "dest_version_id"),
            artifact_id=_optional_str(data, "artifact_id"),
        )
    if kind is ActionKind.WRITE_FILE:
        return WriteFileResult(
            remote_path=_required_str(data, "remote_path"),
            bytes_written=_required_int(data, "bytes_written"),
            sha256_hex=_required_str(data, "sha256_hex"),
            encoding=_required_str(data, "encoding"),
            overwrote=_required_bool(data, "overwrote"),
            dest_version_id=_optional_str(data, "dest_version_id"),
            artifact_id=_optional_str(data, "artifact_id"),
        )
    msg = f"unsupported action result kind: {kind.value}"
    raise ValueError(msg)


def _action_failure_to_json(payload: ActionFailure | None) -> dict[str, object] | None:
    if payload is None:
        return None
    return {
        "schema_version": _SCHEMA_VERSION,
        "detail": payload.detail,
        "remote_path": payload.remote_path,
    }


def _action_failure_from_json(
    data: Mapping[str, object] | None,
) -> ActionFailure | None:
    if data is None:
        return None
    return ActionFailure(
        detail=_required_str(data, "detail"),
        remote_path=_optional_str(data, "remote_path"),
    )


def _policy_decision_to_json(
    payload: PolicyDecisionRecord | None,
) -> dict[str, object] | None:
    if payload is None:
        return None
    return {
        "schema_version": _SCHEMA_VERSION,
        "tenant_id": payload.tenant_id,
        "actor_id": payload.actor_id,
        "operation": payload.operation,
        "target": payload.target,
        "decision": payload.decision.value,
        "reason": payload.reason,
        "policy_version": payload.policy_version,
        "created_at": _datetime_to_json(payload.created_at),
    }


def _policy_decision_from_json(
    data: Mapping[str, object] | None,
) -> PolicyDecisionRecord | None:
    if data is None:
        return None
    return PolicyDecisionRecord(
        tenant_id=_required_str(data, "tenant_id"),
        actor_id=_required_str(data, "actor_id"),
        operation=_required_str(data, "operation"),
        target=_required_str(data, "target"),
        decision=PolicyDecision(_required_str(data, "decision")),
        reason=_required_str(data, "reason"),
        policy_version=_required_str(data, "policy_version"),
        created_at=_datetime_from_json(_required_str(data, "created_at")),
    )


def _artifact_payload_to_json(  # noqa: C901, PLR0911
    payload: ArtifactPayload,
) -> dict[str, object]:
    if isinstance(payload, ConflictArtifact):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "conflict_artifact",
            "conflict_id": payload.conflict_id,
            "tenant": _tenant_to_json(payload.tenant),
            "stack_id": payload.stack_id,
            "change_id": payload.change_id,
            "object_name": payload.object_name,
            "expected_digest": payload.expected_digest,
            "observed_digest": payload.observed_digest,
            "reason": payload.reason,
            "status": payload.status,
            "next_step": payload.next_step,
            "created_at": _datetime_to_json(payload.created_at),
        }
    if isinstance(payload, DeleteReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "delete_report",
            "remote_path": payload.remote_path,
            "deleted": payload.deleted,
            "version_id": payload.version_id,
            "restore_plan": _restore_plan_to_json(payload.restore_plan),
        }
    if isinstance(payload, UploadReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "upload_report",
            "remote_path": payload.remote_path,
            "filename": payload.filename,
            "size_bytes": payload.size_bytes,
            "sha256_hex": payload.sha256_hex,
        }
    if isinstance(payload, StorageMutationReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "storage_mutation_report",
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
    if isinstance(payload, GenerationManifest):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "generation_manifest",
            "root_id": payload.root_id,
            "generation_id": payload.generation_id,
            "manifest_digest": payload.manifest_digest,
            "provider": payload.provider,
            "container": payload.container,
            "prefix": payload.prefix,
            "objects": [
                _object_pointer_to_json(pointer) for pointer in payload.objects
            ],
            "object_count": payload.object_count,
            "total_bytes": payload.total_bytes,
            "partial": payload.partial,
            "created_at": _datetime_to_json(payload.created_at),
        }
    if isinstance(payload, ManifestReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "manifest",
            "source_platform": payload.source_platform,
            "workspace_id": payload.workspace_id,
            "channel_id": payload.channel_id,
            "destination_container": payload.destination_container,
            "destination_prefix": payload.destination_prefix,
            "scanned_count": payload.scanned_count,
            "matched_count": payload.matched_count,
            "total_count": payload.total_count,
            "truncated": payload.truncated,
            "object_entries": [
                _manifest_object_entry_to_json(entry)
                for entry in payload.object_entries
            ],
            "failed_files": [
                _manifest_failure_entry_to_json(entry) for entry in payload.failed_files
            ],
            "verifier_artifact_id": payload.verifier_artifact_id,
        }
    if isinstance(payload, ObjectVerificationReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "verification_report",
            "verifier": payload.verifier,
            "subject": payload.subject,
            "verified": payload.verified,
            "entries": [
                _object_verification_entry_to_json(entry) for entry in payload.entries
            ],
            "reason": payload.reason,
        }
    if isinstance(payload, ProviderHealthReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "provider_health",
            "report_id": payload.report_id,
            "tenant": _tenant_to_json(payload.tenant),
            "provider": payload.provider,
            "container": payload.container,
            "prefix": payload.prefix,
            "region": payload.region,
            "status": payload.status,
            "health_score": payload.health_score,
            "confidence": payload.confidence,
            "evidence_source": payload.evidence_source,
            "generated_at": _datetime_to_json(payload.generated_at),
            "expires_at": _datetime_to_json(payload.expires_at),
            "probes": [
                _provider_probe_result_to_json(probe) for probe in payload.probes
            ],
            "advisory_context": list(payload.advisory_context),
            "next_operator_step": payload.next_operator_step,
        }
    if isinstance(payload, RepairReceipt):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "repair_receipt",
            "receipt_id": payload.receipt_id,
            "lane_id": payload.lane_id,
            "tenant": _tenant_to_json(payload.tenant),
            "source_object_name": payload.source_object_name,
            "replica_object_name": payload.replica_object_name,
            "source_sha256": payload.source_sha256,
            "destination_sha256": payload.destination_sha256,
            "authority": payload.authority,
            "outcome": payload.outcome,
            "repaired_at": _datetime_to_json(payload.repaired_at),
            "next_step": payload.next_step,
        }
    if isinstance(payload, DriftReport):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "drift_report",
            "manifest_artifact_id": payload.manifest_artifact_id,
            "tenant": _tenant_to_json(payload.tenant),
            "checked_at": _datetime_to_json(payload.checked_at),
            "container": payload.container,
            "prefix": payload.prefix,
            "total_count": payload.total_count,
            "match_count": payload.match_count,
            "mismatch_count": payload.mismatch_count,
            "missing_count": payload.missing_count,
            "unknown_count": payload.unknown_count,
            "bucket_missing": payload.bucket_missing,
            "has_drift": payload.has_drift,
            "entries": [
                _drift_object_entry_to_json(entry) for entry in payload.entries
            ],
            "via_action_id": payload.via_action_id,
        }
    if isinstance(payload, MigrationDecisionPacket):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "migration_decision_packet",
            "packet_id": payload.packet_id,
            "tenant": _tenant_to_json(payload.tenant),
            "root_id": payload.root_id,
            "source_provider": payload.source_provider,
            "source_container": payload.source_container,
            "source_prefix": payload.source_prefix,
            "candidate_provider": payload.candidate_provider,
            "candidate_container": payload.candidate_container,
            "candidate_prefix": payload.candidate_prefix,
            "candidate_region": payload.candidate_region,
            "object_count": payload.object_count,
            "total_bytes": payload.total_bytes,
            "source_list_latency_ms": payload.source_list_latency_ms,
            "estimated_monthly_storage_cost_usd": (
                payload.estimated_monthly_storage_cost_usd
            ),
            "assumptions": list(payload.assumptions),
            "safety_checks": list(payload.safety_checks),
            "rollback_plan": payload.rollback_plan,
            "route_switch_plan": payload.route_switch_plan,
            "recommendation": payload.recommendation,
            "created_at": _datetime_to_json(payload.created_at),
        }
    if isinstance(payload, ProofReceipt):
        return {
            "schema_version": _SCHEMA_VERSION,
            "type": "proof_receipt",
            "receipt_id": payload.receipt_id,
            "tenant": _tenant_to_json(payload.tenant),
            "subject": payload.subject,
            "outcome": payload.outcome,
            "summary": payload.summary,
            "task_id": payload.task_id,
            "action_id": payload.action_id,
            "manifest_artifact_id": payload.manifest_artifact_id,
            "verifier_artifact_id": payload.verifier_artifact_id,
            "linked_artifact_ids": list(payload.linked_artifact_ids),
            "artifact_digests": dict(payload.artifact_digests),
            "session_id": payload.session_id,
            "event_range_start": payload.event_range_start,
            "event_range_end": payload.event_range_end,
            "policy_version": payload.policy_version,
            "idempotency_key": payload.idempotency_key,
            "next_steps": list(payload.next_steps),
            "created_at": _datetime_to_json(payload.created_at),
        }
    msg = f"unsupported artifact payload: {type(payload).__name__}"
    raise TypeError(msg)


def _object_pointer_to_json(pointer: ObjectPointer) -> dict[str, object]:
    return {
        "provider": pointer.provider,
        "container": pointer.container,
        "object_name": pointer.object_name,
        "account_id": pointer.account_id,
        "region": pointer.region,
        "version_id": pointer.version_id,
        "content_sha256": pointer.content_sha256,
        "size_bytes": pointer.size_bytes,
    }


def _object_pointer_from_json(data: Mapping[str, object]) -> ObjectPointer:
    return ObjectPointer(
        provider=cast("ProviderName", _required_str(data, "provider")),
        container=_required_str(data, "container"),
        object_name=_required_str(data, "object_name"),
        account_id=_optional_str(data, "account_id"),
        region=_optional_str(data, "region"),
        version_id=_optional_str(data, "version_id"),
        content_sha256=_optional_str(data, "content_sha256"),
        size_bytes=_optional_int(data, "size_bytes"),
    )


def _manifest_object_entry_to_json(entry: ManifestObjectEntry) -> dict[str, object]:
    return {
        "file_id": entry.file_id,
        "name": entry.name,
        "object_key": entry.object_key,
        "size_bytes": entry.size_bytes,
        "sha256_hex": entry.sha256_hex,
        "disposition": entry.disposition,
        "deduped_from_key": entry.deduped_from_key,
    }


def _manifest_object_entry_from_json(
    data: Mapping[str, object],
) -> ManifestObjectEntry:
    return ManifestObjectEntry(
        file_id=_required_str(data, "file_id"),
        name=_required_str(data, "name"),
        object_key=_required_str(data, "object_key"),
        size_bytes=_required_int(data, "size_bytes"),
        sha256_hex=_required_str(data, "sha256_hex"),
        disposition=_required_str(data, "disposition"),
        deduped_from_key=_optional_str(data, "deduped_from_key"),
    )


def _manifest_failure_entry_to_json(entry: ManifestFailureEntry) -> dict[str, object]:
    return {
        "file_id": entry.file_id,
        "name": entry.name,
        "reason": entry.reason,
    }


def _manifest_failure_entry_from_json(
    data: Mapping[str, object],
) -> ManifestFailureEntry:
    return ManifestFailureEntry(
        file_id=_required_str(data, "file_id"),
        name=_required_str(data, "name"),
        reason=_required_str(data, "reason"),
    )


def _object_verification_entry_to_json(
    entry: ObjectVerificationEntry,
) -> dict[str, object]:
    return {
        "file_id": entry.file_id,
        "object_key": entry.object_key,
        "size_bytes": entry.size_bytes,
        "sha256_hex": entry.sha256_hex,
        "verified": entry.verified,
        "reason": entry.reason,
    }


def _object_verification_entry_from_json(
    data: Mapping[str, object],
) -> ObjectVerificationEntry:
    return ObjectVerificationEntry(
        file_id=_required_str(data, "file_id"),
        object_key=_required_str(data, "object_key"),
        size_bytes=_required_int(data, "size_bytes"),
        sha256_hex=_required_str(data, "sha256_hex"),
        verified=_required_bool(data, "verified"),
        reason=_optional_str(data, "reason"),
    )


def _provider_probe_result_to_json(probe: ProviderProbeResult) -> dict[str, object]:
    return {
        "probe_name": probe.probe_name,
        "operation": probe.operation,
        "provider": probe.provider,
        "container": probe.container,
        "prefix": probe.prefix,
        "object_name": probe.object_name,
        "region": probe.region,
        "outcome": probe.outcome.value,
        "latency_ms": probe.latency_ms,
        "item_count": probe.item_count,
        "request_id": probe.request_id,
        "error_message": probe.error_message,
        "observed_at": _datetime_to_json(probe.observed_at),
    }


def _provider_probe_result_from_json(
    data: Mapping[str, object],
) -> ProviderProbeResult:
    return ProviderProbeResult(
        probe_name=_required_str(data, "probe_name"),
        operation=_required_str(data, "operation"),
        provider=cast("ProviderName", _required_str(data, "provider")),
        container=_required_str(data, "container"),
        prefix=_required_str(data, "prefix"),
        object_name=_optional_str(data, "object_name"),
        region=_optional_str(data, "region"),
        outcome=ProviderOutcome(_required_str(data, "outcome")),
        latency_ms=_required_int(data, "latency_ms"),
        item_count=_optional_int(data, "item_count"),
        request_id=_optional_str(data, "request_id"),
        error_message=_optional_str(data, "error_message"),
        observed_at=_datetime_from_json(_required_str(data, "observed_at")),
    )


def _drift_object_entry_to_json(entry: DriftObjectEntry) -> dict[str, object]:
    return {
        "object_key": entry.object_key,
        "file_id": entry.file_id,
        "name": entry.name,
        "expected_sha256": entry.expected_sha256,
        "observed_sha256": entry.observed_sha256,
        "status": entry.status,
        "size_bytes": entry.size_bytes,
        "via_action_id": entry.via_action_id,
        "via_actor_id": entry.via_actor_id,
    }


def _drift_object_entry_from_json(
    data: Mapping[str, object],
) -> DriftObjectEntry:
    raw_status = _required_str(data, "status")
    valid: tuple[DriftObjectStatus, ...] = (
        "match",
        "mismatch",
        "missing",
        "unknown",
        "bucket_missing",
    )
    if raw_status not in valid:
        msg = f"invalid DriftObjectStatus: {raw_status!r}"
        raise ValueError(msg)
    return DriftObjectEntry(
        object_key=_required_str(data, "object_key"),
        file_id=_required_str(data, "file_id"),
        name=_required_str(data, "name"),
        expected_sha256=_required_str(data, "expected_sha256"),
        observed_sha256=_optional_str(data, "observed_sha256"),
        status=raw_status,
        size_bytes=_optional_int(data, "size_bytes"),
        via_action_id=_optional_str(data, "via_action_id"),
        via_actor_id=_optional_str(data, "via_actor_id"),
    )


def _artifact_payload_from_json(  # noqa: C901, PLR0911, PLR0912
    *, kind: str, data: Mapping[str, object]
) -> ArtifactPayload:
    if kind == "conflict_artifact":
        return ConflictArtifact(
            conflict_id=_required_str(data, "conflict_id"),
            tenant=_tenant_from_json(_required_mapping(data, "tenant")),
            stack_id=_required_str(data, "stack_id"),
            change_id=_required_str(data, "change_id"),
            object_name=_required_str(data, "object_name"),
            expected_digest=_optional_str(data, "expected_digest"),
            observed_digest=_optional_str(data, "observed_digest"),
            reason=_required_str(data, "reason"),
            status=_required_str(data, "status"),
            next_step=_required_str(data, "next_step"),
            created_at=_datetime_from_json(_required_str(data, "created_at")),
        )
    if kind == "delete_report":
        return DeleteReport(
            remote_path=_required_str(data, "remote_path"),
            deleted=_required_bool(data, "deleted"),
            version_id=_optional_str(data, "version_id"),
            restore_plan=_restore_plan_from_json(
                data.get("restore_plan"),
                original_key=_required_str(data, "remote_path"),
            ),
        )
    if kind == "upload_report":
        return UploadReport(
            remote_path=_required_str(data, "remote_path"),
            filename=_required_str(data, "filename"),
            size_bytes=_required_int(data, "size_bytes"),
            sha256_hex=_required_str(data, "sha256_hex"),
        )
    if kind == "storage_mutation_report":
        source_deleted_raw = data.get("source_deleted")
        if source_deleted_raw is not None and not isinstance(source_deleted_raw, bool):
            msg = "expected optional boolean field 'source_deleted'"
            raise TypeError(msg)
        return StorageMutationReport(
            operation=_required_str(data, "operation"),
            source_path=_optional_str(data, "source_path"),
            dest_path=_optional_str(data, "dest_path"),
            remote_path=_optional_str(data, "remote_path"),
            size_bytes=_optional_int(data, "size_bytes"),
            sha256_hex=_optional_str(data, "sha256_hex"),
            overwrote=_required_bool(data, "overwrote"),
            source_deleted=source_deleted_raw,
            dest_version_id=_optional_str(data, "dest_version_id"),
            verified=_required_bool(data, "verified"),
            verifier=_required_str(data, "verifier"),
        )
    if kind == "manifest" and data.get("type") == "generation_manifest":
        return GenerationManifest(
            root_id=_required_str(data, "root_id"),
            generation_id=_required_str(data, "generation_id"),
            manifest_digest=_required_str(data, "manifest_digest"),
            provider=cast("ProviderName", _required_str(data, "provider")),
            container=_required_str(data, "container"),
            prefix=_required_str(data, "prefix"),
            objects=tuple(
                _object_pointer_from_json(_entry_mapping(entry))
                for entry in _required_sequence(data, "objects")
            ),
            object_count=_required_int(data, "object_count"),
            total_bytes=_required_int(data, "total_bytes"),
            partial=_required_bool(data, "partial"),
            created_at=_datetime_from_json(_required_str(data, "created_at")),
        )
    if kind == "manifest":
        return ManifestReport(
            source_platform=_required_str(data, "source_platform"),
            workspace_id=_required_str(data, "workspace_id"),
            channel_id=_required_str(data, "channel_id"),
            destination_container=_required_str(data, "destination_container"),
            destination_prefix=_required_str(data, "destination_prefix"),
            scanned_count=_required_int(data, "scanned_count"),
            matched_count=_required_int(data, "matched_count"),
            total_count=(
                None
                if data.get("total_count") is None
                else _required_int(data, "total_count")
            ),
            truncated=_required_bool(data, "truncated"),
            object_entries=tuple(
                _manifest_object_entry_from_json(_entry_mapping(entry))
                for entry in _required_sequence(data, "object_entries")
            ),
            failed_files=tuple(
                _manifest_failure_entry_from_json(_entry_mapping(entry))
                for entry in _required_sequence(data, "failed_files")
            ),
            verifier_artifact_id=_optional_str(data, "verifier_artifact_id"),
        )
    if kind == "verification_report":
        return ObjectVerificationReport(
            verifier=_required_str(data, "verifier"),
            subject=_required_str(data, "subject"),
            verified=_required_bool(data, "verified"),
            entries=tuple(
                _object_verification_entry_from_json(_entry_mapping(entry))
                for entry in _required_sequence(data, "entries")
            ),
            reason=_optional_str(data, "reason"),
        )
    if kind == "provider_health":
        tenant_data = _required_mapping(data, "tenant")
        return ProviderHealthReport(
            report_id=_required_str(data, "report_id"),
            tenant=_tenant_from_json(tenant_data),
            provider=cast("ProviderName", _required_str(data, "provider")),
            container=_required_str(data, "container"),
            prefix=_required_str(data, "prefix"),
            region=_optional_str(data, "region"),
            status=_required_str(data, "status"),
            health_score=_required_int(data, "health_score"),
            confidence=_required_str(data, "confidence"),
            evidence_source=_required_str(data, "evidence_source"),
            generated_at=_datetime_from_json(_required_str(data, "generated_at")),
            expires_at=_datetime_from_json(_required_str(data, "expires_at")),
            probes=tuple(
                _provider_probe_result_from_json(_entry_mapping(entry))
                for entry in _required_sequence(data, "probes")
            ),
            advisory_context=tuple(
                _required_string_item("advisory_context", item)
                for item in _required_sequence(data, "advisory_context")
            ),
            next_operator_step=_required_str(data, "next_operator_step"),
        )
    if kind == "repair_receipt":
        tenant_data = _required_mapping(data, "tenant")
        return RepairReceipt(
            receipt_id=_required_str(data, "receipt_id"),
            lane_id=_required_str(data, "lane_id"),
            tenant=_tenant_from_json(tenant_data),
            source_object_name=_required_str(data, "source_object_name"),
            replica_object_name=_required_str(data, "replica_object_name"),
            source_sha256=_required_str(data, "source_sha256"),
            destination_sha256=_required_str(data, "destination_sha256"),
            authority=_required_str(data, "authority"),
            outcome=_required_str(data, "outcome"),
            repaired_at=_datetime_from_json(_required_str(data, "repaired_at")),
            next_step=_required_str(data, "next_step"),
        )
    if kind == "drift_report":
        tenant_data = _required_mapping(data, "tenant")
        return DriftReport(
            manifest_artifact_id=_required_str(data, "manifest_artifact_id"),
            tenant=_tenant_from_json(tenant_data),
            checked_at=_datetime_from_json(_required_str(data, "checked_at")),
            container=_required_str(data, "container"),
            prefix=_required_str(data, "prefix"),
            total_count=_required_int(data, "total_count"),
            match_count=_required_int(data, "match_count"),
            mismatch_count=_required_int(data, "mismatch_count"),
            missing_count=_required_int(data, "missing_count"),
            unknown_count=_required_int(data, "unknown_count"),
            bucket_missing=_required_bool(data, "bucket_missing"),
            has_drift=_required_bool(data, "has_drift"),
            entries=tuple(
                _drift_object_entry_from_json(_entry_mapping(entry))
                for entry in _required_sequence(data, "entries")
            ),
            via_action_id=_optional_str(data, "via_action_id"),
        )
    if kind == "migration_decision_packet":
        tenant_data = _required_mapping(data, "tenant")
        return MigrationDecisionPacket(
            packet_id=_required_str(data, "packet_id"),
            tenant=_tenant_from_json(tenant_data),
            root_id=_required_str(data, "root_id"),
            source_provider=cast(
                "ProviderName",
                _required_str(data, "source_provider"),
            ),
            source_container=_required_str(data, "source_container"),
            source_prefix=_required_str(data, "source_prefix"),
            candidate_provider=cast(
                "ProviderName",
                _required_str(data, "candidate_provider"),
            ),
            candidate_container=_required_str(data, "candidate_container"),
            candidate_prefix=_required_str(data, "candidate_prefix"),
            candidate_region=_optional_str(data, "candidate_region"),
            object_count=_required_int(data, "object_count"),
            total_bytes=_required_int(data, "total_bytes"),
            source_list_latency_ms=_required_int(data, "source_list_latency_ms"),
            estimated_monthly_storage_cost_usd=_required_float(
                data,
                "estimated_monthly_storage_cost_usd",
            ),
            assumptions=tuple(
                _required_string_item("assumptions", item)
                for item in _required_sequence(data, "assumptions")
            ),
            safety_checks=tuple(
                _required_string_item("safety_checks", item)
                for item in _required_sequence(data, "safety_checks")
            ),
            rollback_plan=_required_str(data, "rollback_plan"),
            route_switch_plan=_required_str(data, "route_switch_plan"),
            recommendation=_required_str(data, "recommendation"),
            created_at=_datetime_from_json(_required_str(data, "created_at")),
        )
    if kind == "proof_receipt":
        tenant_data = _required_mapping(data, "tenant")
        return ProofReceipt(
            receipt_id=_required_str(data, "receipt_id"),
            tenant=_tenant_from_json(tenant_data),
            subject=_required_str(data, "subject"),
            outcome=_required_str(data, "outcome"),
            summary=_required_str(data, "summary"),
            task_id=_optional_str(data, "task_id"),
            action_id=_optional_str(data, "action_id"),
            manifest_artifact_id=_optional_str(data, "manifest_artifact_id"),
            verifier_artifact_id=_optional_str(data, "verifier_artifact_id"),
            linked_artifact_ids=tuple(
                _required_string_item("linked_artifact_ids", item)
                for item in _required_sequence(data, "linked_artifact_ids")
            ),
            artifact_digests={
                key: _required_string_item("artifact_digests", value)
                for key, value in _required_mapping(data, "artifact_digests").items()
            },
            session_id=_required_str(data, "session_id"),
            event_range_start=_optional_int(data, "event_range_start"),
            event_range_end=_optional_int(data, "event_range_end"),
            policy_version=_required_str(data, "policy_version"),
            idempotency_key=_optional_str(data, "idempotency_key"),
            next_steps=tuple(
                _required_string_item("next_steps", item)
                for item in _required_sequence(data, "next_steps")
            ),
            created_at=_datetime_from_json(_required_str(data, "created_at")),
        )
    msg = f"unsupported artifact kind: {kind}"
    raise ValueError(msg)


def _restore_plan_to_json(plan: RestorePlan) -> dict[str, object]:
    return {
        "original_key": plan.original_key,
        "strategy": plan.strategy.value,
        "restorable": plan.restorable,
        "trash_key": plan.trash_key,
        "version_id": plan.version_id,
        "sha256_hex": plan.sha256_hex,
        "size_bytes": plan.size_bytes,
        "deleted_by": plan.deleted_by,
        "deleted_at": (
            None if plan.deleted_at is None else _datetime_to_json(plan.deleted_at)
        ),
        "restore_command": plan.restore_command,
        "limitations": list(plan.limitations),
    }


def _restore_plan_from_json(
    value: object,
    *,
    original_key: str,
) -> RestorePlan:
    if value is None:
        return RestorePlan(
            original_key=original_key,
            strategy=RestoreStrategy.UNAVAILABLE,
            restorable=False,
            trash_key=None,
            version_id=None,
            sha256_hex=None,
            size_bytes=None,
            deleted_by=None,
            deleted_at=None,
            restore_command=None,
            limitations=("Legacy delete report did not include restore evidence.",),
        )
    if not isinstance(value, dict):
        msg = "expected restore_plan to be an object"
        raise TypeError(msg)
    data = cast("Mapping[str, object]", value)
    limitations: list[str] = []
    for item in _required_sequence(data, "limitations"):
        if not isinstance(item, str):
            msg = "expected restore_plan limitation to be a string"
            raise TypeError(msg)
        limitations.append(item)
    return RestorePlan(
        original_key=_required_str(data, "original_key"),
        strategy=RestoreStrategy(_required_str(data, "strategy")),
        restorable=_required_bool(data, "restorable"),
        trash_key=_optional_str(data, "trash_key"),
        version_id=_optional_str(data, "version_id"),
        sha256_hex=_optional_str(data, "sha256_hex"),
        size_bytes=(
            None
            if data.get("size_bytes") is None
            else _required_int(data, "size_bytes")
        ),
        deleted_by=_optional_str(data, "deleted_by"),
        deleted_at=_optional_datetime_from_json(_optional_str(data, "deleted_at")),
        restore_command=_optional_str(data, "restore_command"),
        limitations=tuple(limitations),
    )


def _entry_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        msg = "expected artifact entry to be an object"
        raise TypeError(msg)
    return cast("Mapping[str, object]", value)


def _required_string_item(field: str, value: object) -> str:
    if not isinstance(value, str):
        msg = f"expected {field!r} item to be a string"
        raise TypeError(msg)
    return value


def _row_str(row: sqlite3.Row, key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        msg = f"expected SQLite text column {key!r}"
        raise TypeError(msg)
    return value


def _row_optional_str(row: sqlite3.Row, key: str) -> str | None:
    value = row[key]
    if value is None or isinstance(value, str):
        return value
    msg = f"expected optional SQLite text column {key!r}"
    raise TypeError(msg)


def _row_int(row: sqlite3.Row, key: str) -> int:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"expected SQLite integer column {key!r}"
        raise TypeError(msg)
    return cast("int", value)


def _row_optional_int(row: sqlite3.Row, key: str) -> int | None:
    value = row[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"expected optional SQLite integer column {key!r}"
        raise TypeError(msg)
    return cast("int", value)


def _safe_row(
    parser: Callable[[sqlite3.Row], object],
    row: sqlite3.Row,
) -> object | None:
    try:
        return parser(row)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


class _SQLiteStore:
    """Shared SQLite connection and schema helper."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._db_path = root / _DB_FILENAME
        self._lock = _path_lock(self._db_path)

    @property
    def db_path(self) -> Path:
        """Return the backing SQLite file path."""
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        self._root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._db_path, timeout=5.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = FULL")
        self._ensure_schema(con)
        return con

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
            except Exception:
                con.execute("ROLLBACK")
                raise
            else:
                con.execute("COMMIT")
            finally:
                con.close()

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS session_events (
                tenant_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_id TEXT NOT NULL PRIMARY KEY,
                event_type TEXT NOT NULL,
                actor_json TEXT,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                UNIQUE (tenant_id, session_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS session_events_by_session
                ON session_events (tenant_id, session_id, sequence);
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                session_id TEXT NOT NULL,
                actor_json TEXT NOT NULL,
                status TEXT NOT NULL,
                intent TEXT NOT NULL,
                source_ref TEXT,
                idempotency_key TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                failure_detail TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                schema_version INTEGER NOT NULL,
                UNIQUE (tenant_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS tasks_by_tenant
                ON tasks (tenant_id, updated_at);
            CREATE INDEX IF NOT EXISTS tasks_by_status
                ON tasks (tenant_id, status, updated_at);
            CREATE INDEX IF NOT EXISTS tasks_by_session
                ON tasks (tenant_id, session_id);
            CREATE TABLE IF NOT EXISTS plans (
                plan_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                session_id TEXT NOT NULL,
                task_id TEXT,
                action_id TEXT,
                actor_json TEXT NOT NULL,
                status TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL,
                target_json TEXT,
                estimated_count INTEGER,
                estimated_bytes INTEGER,
                idempotency_key TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                schema_version INTEGER NOT NULL,
                UNIQUE (tenant_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS plans_by_session
                ON plans (tenant_id, session_id, created_at);
            CREATE INDEX IF NOT EXISTS plans_by_action
                ON plans (tenant_id, action_id);
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                session_id TEXT NOT NULL,
                task_id TEXT,
                plan_id TEXT,
                action_id TEXT,
                requested_by_json TEXT NOT NULL,
                required_actor_id TEXT NOT NULL,
                allowed_actor_ids_json TEXT NOT NULL,
                status TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                exact_target TEXT NOT NULL,
                reason TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                decided_by_json TEXT,
                decided_at TEXT,
                decision_note TEXT,
                schema_version INTEGER NOT NULL,
                UNIQUE (tenant_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS approvals_by_action
                ON approvals (tenant_id, action_id, status);
            CREATE INDEX IF NOT EXISTS approvals_by_session
                ON approvals (tenant_id, session_id, created_at);
            CREATE TABLE IF NOT EXISTS worker_leases (
                tenant_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                lease_until TEXT NOT NULL,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, task_id)
            );
            CREATE INDEX IF NOT EXISTS worker_leases_by_worker
                ON worker_leases (tenant_id, worker_id);
            CREATE INDEX IF NOT EXISTS worker_leases_by_expiry
                ON worker_leases (tenant_id, lease_until);
            CREATE TABLE IF NOT EXISTS actions (
                action_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                session_id TEXT NOT NULL,
                actor_json TEXT NOT NULL,
                kind TEXT NOT NULL,
                target_json TEXT,
                status TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                input_json TEXT NOT NULL,
                result_json TEXT,
                failure_json TEXT,
                policy_decision_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT,
                schema_version INTEGER NOT NULL,
                UNIQUE (tenant_id, idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS actions_by_session
                ON actions (tenant_id, session_id, created_at);
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                session_id TEXT NOT NULL,
                action_id TEXT,
                kind TEXT NOT NULL,
                uri TEXT,
                payload_json TEXT NOT NULL,
                payload_digest TEXT,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS artifacts_by_session
                ON artifacts (tenant_id, session_id, created_at);
            """
        )
        action_columns = {
            str(row["name"])
            for row in con.execute("PRAGMA table_info(actions)").fetchall()
        }
        if "policy_decision_json" not in action_columns:
            con.execute("ALTER TABLE actions ADD COLUMN policy_decision_json TEXT")
        artifact_columns = {
            str(row["name"])
            for row in con.execute("PRAGMA table_info(artifacts)").fetchall()
        }
        if "payload_digest" not in artifact_columns:
            con.execute("ALTER TABLE artifacts ADD COLUMN payload_digest TEXT")
        con.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _append_event(con: sqlite3.Connection, event: _EventAppend) -> SessionEvent:
    row = con.execute(
        """
        SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
        FROM session_events
        WHERE tenant_id = ? AND session_id = ?
        """,
        (event.tenant.tenant_id, event.session_id),
    ).fetchone()
    sequence = _row_int(row, "next_sequence")
    session_event = SessionEvent(
        tenant=event.tenant,
        session_id=event.session_id,
        sequence=sequence,
        event_id=f"evt-{uuid.uuid4().hex}",
        event_type=event.event_type,
        actor=event.actor,
        payload=dict(event.payload),
        created_at=datetime.now(UTC),
    )
    con.execute(
        """
        INSERT INTO session_events (
            tenant_id, session_id, sequence, event_id, event_type,
            actor_json, payload_json, created_at, schema_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event.tenant.tenant_id,
            event.session_id,
            sequence,
            session_event.event_id,
            event.event_type,
            _json_dumps(_actor_to_json(event.actor)),
            _json_dumps(dict(event.payload)),
            _datetime_to_json(session_event.created_at),
            _SCHEMA_VERSION,
        ),
    )
    return session_event


class FileSessionEventStore(_SQLiteStore):
    """SQLite-backed ordered event store for local Nimbus deployments."""

    def __init__(self, root: Path) -> None:
        """Create an event store under ``root``."""
        super().__init__(root)

    def append(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        event_type: str,
        actor: VerifiedActor | None,
        payload: Mapping[str, object],
    ) -> SessionEvent:
        """Append one event and return it with a sequence number."""
        with self._transaction() as con:
            return _append_event(
                con,
                _EventAppend(
                    tenant=tenant,
                    session_id=session_id,
                    event_type=event_type,
                    actor=actor,
                    payload=payload,
                ),
            )

    def list_events(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> Sequence[SessionEvent]:
        """Return ordered events for one tenant-scoped session."""
        query = (
            "SELECT * FROM session_events "
            "WHERE tenant_id = ? AND session_id = ? AND sequence > ? "
            "ORDER BY sequence ASC LIMIT ?"
        )
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    query,
                    (
                        tenant.tenant_id,
                        session_id,
                        0 if after_sequence is None else after_sequence,
                        limit,
                    ),
                ).fetchall()
            finally:
                con.close()
        events = [
            event
            for row in rows
            if (event := _safe_row(self._event_from_row, row)) is not None
        ]
        return tuple(cast("SessionEvent", event) for event in events)

    def list_for_tenant_before(
        self,
        *,
        tenant: TenantIdentity,
        before: datetime,
        limit: int = 10_000,
    ) -> Sequence[SessionEvent]:
        """Return tenant events with created_at <= before, ordered chronologically."""
        bounded_limit = max(1, min(limit, 100_000))
        before_str = _datetime_to_json(before)
        query = (
            "SELECT * FROM session_events "
            "WHERE tenant_id = ? AND created_at <= ? "
            "ORDER BY created_at ASC, event_id ASC "
            "LIMIT ?"
        )
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    query,
                    (tenant.tenant_id, before_str, bounded_limit),
                ).fetchall()
            finally:
                con.close()
        events = [
            event
            for row in rows
            if (event := _safe_row(self._event_from_row, row)) is not None
        ]
        return tuple(cast("SessionEvent", event) for event in events)

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> SessionEvent:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported event schema version"
            raise ValueError(msg)
        actor_data = _json_loads_object(
            _row_optional_str(row, "actor_json"),
            field="actor",
        )
        payload_data = _json_loads_object(
            _row_str(row, "payload_json"),
            field="payload",
        )
        return SessionEvent(
            tenant=TenantIdentity(
                platform=_row_str(row, "tenant_id").split(":", maxsplit=1)[0],
                workspace_id=_row_str(row, "tenant_id").split(":", maxsplit=1)[1],
            ),
            session_id=_row_str(row, "session_id"),
            sequence=_row_int(row, "sequence"),
            event_id=_row_str(row, "event_id"),
            event_type=_row_str(row, "event_type"),
            actor=_actor_from_json(actor_data),
            payload={} if payload_data is None else payload_data,
            created_at=_datetime_from_json(_row_str(row, "created_at")),
        )


class FileTaskStore(_SQLiteStore):
    """SQLite-backed task store with transactional idempotency and events."""

    def __init__(
        self,
        root: Path,
        *,
        event_store: SessionEventStore | None = None,
    ) -> None:
        """Create a task store under ``root``."""
        super().__init__(root)
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Task],
    ) -> Task:
        """Create a task once for a logical tenant-scoped request."""
        external_event: tuple[Task, str, Mapping[str, object]] | None = None
        with self._transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            task = create()
            self._validate_new_task(
                tenant=tenant,
                idempotency_key=idempotency_key,
                task=task,
            )
            self._insert_task(con, task)
            event_payload: Mapping[str, object] = {
                "task_id": task.task_id,
                "status": task.status.value,
                "intent": task.intent,
            }
            if self._event_store_in_same_db():
                self._append_task_event_with_connection(
                    con,
                    task=task,
                    event_type="task_created",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (task, "task_created", event_payload)
        if external_event is not None:
            event_task, event_type, event_payload = external_event
            self._append_task_event(
                task=event_task,
                event_type=event_type,
                event_payload=event_payload,
            )
        return task

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        transition: TaskTransition,
    ) -> Task | None:
        """Move a task only if it is still in the expected state."""
        validate_task_transition(
            expected=transition.expected,
            next_status=transition.next_status,
        )
        external_event: tuple[Task, str, Mapping[str, object]] | None = None
        with self._transaction() as con:
            task = self._read_task_with_connection(
                con,
                tenant=tenant,
                task_id=task_id,
            )
            if task is None or task.status is not transition.expected:
                return None
            updated = replace(
                task,
                status=transition.next_status,
                failure_detail=(
                    transition.failure_detail
                    if transition.failure_detail is not None
                    else task.failure_detail
                ),
                updated_at=datetime.now(UTC),
            )
            self._update_task(con, updated)
            if self._event_store_in_same_db():
                self._append_task_event_with_connection(
                    con,
                    task=updated,
                    event_type=transition.event_type,
                    event_payload=transition.event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    updated,
                    transition.event_type,
                    transition.event_payload,
                )
        if external_event is not None:
            event_task, event_type, event_payload = external_event
            self._append_task_event(
                task=event_task,
                event_type=event_type,
                event_payload=event_payload,
            )
        return updated

    def get(self, *, tenant: TenantIdentity, task_id: str) -> Task | None:
        """Return one tenant-scoped task if it exists."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_task_with_connection(
                    con,
                    tenant=tenant,
                    task_id=task_id,
                )
            finally:
                con.close()

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        status: TaskStatus | None = None,
        limit: int = 100,
    ) -> Sequence[Task]:
        """Return recent tasks for one tenant."""
        if limit < 1:
            return ()
        bounded_limit = min(limit, 500)
        if status is None:
            query = (
                "SELECT * FROM tasks WHERE tenant_id = ? "
                "ORDER BY updated_at DESC LIMIT ?"
            )
            params: tuple[object, ...] = (tenant.tenant_id, bounded_limit)
        else:
            query = (
                "SELECT * FROM tasks WHERE tenant_id = ? AND status = ? "
                "ORDER BY updated_at DESC LIMIT ?"
            )
            params = (tenant.tenant_id, status.value, bounded_limit)
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(query, params).fetchall()
            finally:
                con.close()
        tasks = [
            task
            for row in rows
            if (task := _safe_row(self._task_from_row, row)) is not None
        ]
        return tuple(cast("Task", task) for task in tasks)

    def _event_store_in_same_db(self) -> bool:
        return (
            isinstance(self._event_store, FileSessionEventStore)
            and self._event_store.db_path == self._db_path
        )

    def _append_task_event_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        task: Task,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        payload = {
            "task_id": task.task_id,
            "status": task.status.value,
            "intent": task.intent,
            **dict(event_payload),
        }
        _append_event(
            con,
            _EventAppend(
                tenant=task.tenant,
                session_id=task.session_id,
                event_type=event_type,
                actor=task.created_by,
                payload=payload,
            ),
        )

    def _append_task_event(
        self,
        *,
        task: Task,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "task_id": task.task_id,
            "status": task.status.value,
            "intent": task.intent,
            **dict(event_payload),
        }
        self._event_store.append(
            tenant=task.tenant,
            session_id=task.session_id,
            event_type=event_type,
            actor=task.created_by,
            payload=payload,
        )

    @staticmethod
    def _insert_task(con: sqlite3.Connection, task: Task) -> None:
        con.execute(
            """
            INSERT INTO tasks (
                task_id, tenant_id, tenant_json, session_id, actor_json,
                status, intent, source_ref, idempotency_key, metadata_json,
                failure_detail, created_at, updated_at, expires_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _task_sql_values(task),
        )

    @staticmethod
    def _update_task(con: sqlite3.Connection, task: Task) -> None:
        con.execute(
            """
            UPDATE tasks
            SET status = ?,
                failure_detail = ?,
                updated_at = ?,
                expires_at = ?,
                schema_version = ?
            WHERE tenant_id = ? AND task_id = ?
            """,
            (
                task.status.value,
                task.failure_detail,
                _datetime_to_json(task.updated_at),
                None if task.expires_at is None else _datetime_to_json(task.expires_at),
                _SCHEMA_VERSION,
                task.tenant.tenant_id,
                task.task_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Task | None:
        row = con.execute(
            """
            SELECT * FROM tasks
            WHERE tenant_id = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._task_from_row, row)
        return cast("Task | None", parsed)

    def _read_task_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        task_id: str,
    ) -> Task | None:
        row = con.execute(
            """
            SELECT * FROM tasks
            WHERE tenant_id = ? AND task_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, task_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._task_from_row, row)
        return cast("Task | None", parsed)

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> Task:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported task schema version"
            raise ValueError(msg)
        tenant_data = _json_loads_object(_row_str(row, "tenant_json"), field="tenant")
        actor_data = _json_loads_object(_row_str(row, "actor_json"), field="actor")
        metadata_data = _json_loads_object(
            _row_str(row, "metadata_json"),
            field="metadata",
        )
        actor = _actor_from_json(actor_data)
        if actor is None:
            msg = "task actor cannot be null"
            raise TypeError(msg)
        if tenant_data is None:
            msg = "task row is missing tenant JSON"
            raise TypeError(msg)
        return Task(
            task_id=_row_str(row, "task_id"),
            tenant=_tenant_from_json(tenant_data),
            session_id=_row_str(row, "session_id"),
            created_by=actor,
            status=TaskStatus(_row_str(row, "status")),
            intent=_row_str(row, "intent"),
            source_ref=_row_optional_str(row, "source_ref"),
            idempotency_key=_row_str(row, "idempotency_key"),
            metadata=_task_metadata_from_json(metadata_data),
            failure_detail=_row_optional_str(row, "failure_detail"),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
            updated_at=_datetime_from_json(_row_str(row, "updated_at")),
            expires_at=_optional_datetime_from_json(
                _row_optional_str(row, "expires_at")
            ),
        )

    @staticmethod
    def _validate_new_task(
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        task: Task,
    ) -> None:
        if task.tenant != tenant:
            msg = "created task tenant does not match idempotency tenant"
            raise ValueError(msg)
        if task.idempotency_key != idempotency_key:
            msg = "created task idempotency key does not match request key"
            raise ValueError(msg)


def _task_sql_values(task: Task) -> tuple[object, ...]:
    return (
        task.task_id,
        task.tenant.tenant_id,
        _json_dumps(_tenant_to_json(task.tenant)),
        task.session_id,
        _json_dumps(_actor_to_json(task.created_by)),
        task.status.value,
        task.intent,
        task.source_ref,
        task.idempotency_key,
        _json_dumps(_task_metadata_to_json(task.metadata)),
        task.failure_detail,
        _datetime_to_json(task.created_at),
        _datetime_to_json(task.updated_at),
        None if task.expires_at is None else _datetime_to_json(task.expires_at),
        _SCHEMA_VERSION,
    )


class FilePlanStore(_SQLiteStore):
    """SQLite-backed plan store with transactional preview events."""

    def __init__(
        self,
        root: Path,
        *,
        event_store: SessionEventStore | None = None,
    ) -> None:
        """Create a plan store under ``root``."""
        super().__init__(root)
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Plan],
    ) -> Plan:
        """Create a plan once for a logical tenant-scoped preview."""
        external_event: tuple[Plan, str, Mapping[str, object]] | None = None
        with self._transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            plan = create()
            self._validate_new_plan(
                tenant=tenant,
                idempotency_key=idempotency_key,
                plan=plan,
            )
            self._insert_plan(con, plan)
            event_payload: Mapping[str, object] = {
                "plan_id": plan.plan_id,
                "status": plan.status.value,
                "risk_level": plan.risk_level.value,
                "title": plan.title,
                "action_id": plan.action_id,
            }
            if self._event_store_in_same_db():
                self._append_plan_event_with_connection(
                    con,
                    plan=plan,
                    event_type="plan_created",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (plan, "plan_created", event_payload)
        if external_event is not None:
            event_plan, event_type, event_payload = external_event
            self._append_plan_event(
                plan=event_plan,
                event_type=event_type,
                event_payload=event_payload,
            )
        return plan

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        plan_id: str,
        transition: PlanTransition,
    ) -> Plan | None:
        """Move a plan only if it is still in the expected state."""
        validate_plan_transition(
            expected=transition.expected,
            next_status=transition.next_status,
        )
        external_event: tuple[Plan, str, Mapping[str, object]] | None = None
        with self._transaction() as con:
            plan = self._read_plan_with_connection(
                con,
                tenant=tenant,
                plan_id=plan_id,
            )
            if plan is None or plan.status is not transition.expected:
                return None
            updated = replace(
                plan,
                status=transition.next_status,
                updated_at=datetime.now(UTC),
            )
            self._update_plan(con, updated)
            if self._event_store_in_same_db():
                self._append_plan_event_with_connection(
                    con,
                    plan=updated,
                    event_type=transition.event_type,
                    event_payload=transition.event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    updated,
                    transition.event_type,
                    transition.event_payload,
                )
        if external_event is not None:
            event_plan, event_type, event_payload = external_event
            self._append_plan_event(
                plan=event_plan,
                event_type=event_type,
                event_payload=event_payload,
            )
        return updated

    def approve_candidate_group(
        self,
        *,
        tenant: TenantIdentity,
        plan_id: str,
        candidate_group_id: str,
        event_payload: Mapping[str, object],
    ) -> Plan | None:
        """Approve one candidate plan and supersede siblings atomically."""
        external_events: list[tuple[Plan, str, Mapping[str, object]]] = []
        with self._transaction() as con:
            selected = self._read_plan_with_connection(
                con,
                tenant=tenant,
                plan_id=plan_id,
            )
            if selected is None or selected.status is not PlanStatus.PROPOSED:
                return None
            if selected.metadata.get("candidate_group_id") != candidate_group_id:
                return None
            now = datetime.now(UTC)
            approved = replace(
                selected,
                status=PlanStatus.APPROVED,
                updated_at=now,
            )
            validate_plan_transition(
                expected=selected.status,
                next_status=approved.status,
            )
            self._update_plan(con, approved)
            approve_payload: Mapping[str, object] = {
                "plan_id": plan_id,
                "candidate_group_id": candidate_group_id,
                **dict(event_payload),
            }
            self._record_plan_event(
                con,
                external_events,
                plan=approved,
                event_type="plan_approved",
                event_payload=approve_payload,
            )
            for sibling in self._candidate_siblings_with_connection(
                con,
                tenant=tenant,
                candidate_group_id=candidate_group_id,
                selected_plan_id=plan_id,
            ):
                superseded = replace(
                    sibling,
                    status=PlanStatus.SUPERSEDED,
                    updated_at=now,
                )
                validate_plan_transition(
                    expected=sibling.status,
                    next_status=superseded.status,
                )
                self._update_plan(con, superseded)
                self._record_plan_event(
                    con,
                    external_events,
                    plan=superseded,
                    event_type="plan_superseded",
                    event_payload={
                        "plan_id": sibling.plan_id,
                        "candidate_group_id": candidate_group_id,
                        "superseded_by": plan_id,
                    },
                )
        for event_plan, event_type, payload in external_events:
            self._append_plan_event(
                plan=event_plan,
                event_type=event_type,
                event_payload=payload,
            )
        return approved

    def get(self, *, tenant: TenantIdentity, plan_id: str) -> Plan | None:
        """Return one tenant-scoped plan if it exists."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_plan_with_connection(
                    con,
                    tenant=tenant,
                    plan_id=plan_id,
                )
            finally:
                con.close()

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        limit: int = 100,
    ) -> Sequence[Plan]:
        """Return recent plans for one tenant, newest first."""
        bounded_limit = max(1, min(limit, 500))
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT * FROM plans
                    WHERE tenant_id = ?
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (tenant.tenant_id, bounded_limit),
                ).fetchall()
            finally:
                con.close()
        plans = [
            plan
            for row in rows
            if (plan := _safe_row(self._plan_from_row, row)) is not None
        ]
        return tuple(cast("Plan", plan) for plan in plans)

    def _candidate_siblings_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        candidate_group_id: str,
        selected_plan_id: str,
    ) -> Sequence[Plan]:
        rows = con.execute(
            """
            SELECT * FROM plans
            WHERE tenant_id = ? AND status = ?
            ORDER BY created_at ASC
            """,
            (tenant.tenant_id, PlanStatus.PROPOSED.value),
        ).fetchall()
        siblings: list[Plan] = []
        for row in rows:
            parsed = _safe_row(self._plan_from_row, row)
            plan = cast("Plan | None", parsed)
            if plan is None:
                continue
            if plan.plan_id == selected_plan_id:
                continue
            if plan.metadata.get("candidate_group_id") == candidate_group_id:
                siblings.append(plan)
        return tuple(siblings)

    def _record_plan_event(
        self,
        con: sqlite3.Connection,
        external_events: list[tuple[Plan, str, Mapping[str, object]]],
        *,
        plan: Plan,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store_in_same_db():
            self._append_plan_event_with_connection(
                con,
                plan=plan,
                event_type=event_type,
                event_payload=event_payload,
            )
        elif self._event_store is not None:
            external_events.append((plan, event_type, event_payload))

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Plan]:
        """Return plans for one tenant-scoped session."""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT * FROM plans
                    WHERE tenant_id = ? AND session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (tenant.tenant_id, session_id),
                ).fetchall()
            finally:
                con.close()
        plans = [
            plan
            for row in rows
            if (plan := _safe_row(self._plan_from_row, row)) is not None
        ]
        return tuple(cast("Plan", plan) for plan in plans)

    def _event_store_in_same_db(self) -> bool:
        return (
            isinstance(self._event_store, FileSessionEventStore)
            and self._event_store.db_path == self._db_path
        )

    def _append_plan_event_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        plan: Plan,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        payload = {
            "plan_id": plan.plan_id,
            "status": plan.status.value,
            "risk_level": plan.risk_level.value,
            **dict(event_payload),
        }
        _append_event(
            con,
            _EventAppend(
                tenant=plan.tenant,
                session_id=plan.session_id,
                event_type=event_type,
                actor=plan.created_by,
                payload=payload,
            ),
        )

    def _append_plan_event(
        self,
        *,
        plan: Plan,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "plan_id": plan.plan_id,
            "status": plan.status.value,
            "risk_level": plan.risk_level.value,
            **dict(event_payload),
        }
        self._event_store.append(
            tenant=plan.tenant,
            session_id=plan.session_id,
            event_type=event_type,
            actor=plan.created_by,
            payload=payload,
        )

    @staticmethod
    def _insert_plan(con: sqlite3.Connection, plan: Plan) -> None:
        con.execute(
            """
            INSERT INTO plans (
                plan_id, tenant_id, tenant_json, session_id, task_id, action_id,
                actor_json, status, risk_level, title, summary, target_json,
                estimated_count, estimated_bytes, idempotency_key, metadata_json,
                created_at, updated_at, expires_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _plan_sql_values(plan),
        )

    @staticmethod
    def _update_plan(con: sqlite3.Connection, plan: Plan) -> None:
        con.execute(
            """
            UPDATE plans
            SET status = ?,
                updated_at = ?,
                expires_at = ?,
                schema_version = ?
            WHERE tenant_id = ? AND plan_id = ?
            """,
            (
                plan.status.value,
                _datetime_to_json(plan.updated_at),
                None if plan.expires_at is None else _datetime_to_json(plan.expires_at),
                _SCHEMA_VERSION,
                plan.tenant.tenant_id,
                plan.plan_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Plan | None:
        row = con.execute(
            """
            SELECT * FROM plans
            WHERE tenant_id = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._plan_from_row, row)
        return cast("Plan | None", parsed)

    def _read_plan_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        plan_id: str,
    ) -> Plan | None:
        row = con.execute(
            """
            SELECT * FROM plans
            WHERE tenant_id = ? AND plan_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, plan_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._plan_from_row, row)
        return cast("Plan | None", parsed)

    @staticmethod
    def _plan_from_row(row: sqlite3.Row) -> Plan:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported plan schema version"
            raise ValueError(msg)
        tenant_data = _json_loads_object(_row_str(row, "tenant_json"), field="tenant")
        actor_data = _json_loads_object(_row_str(row, "actor_json"), field="actor")
        metadata_data = _json_loads_object(
            _row_str(row, "metadata_json"),
            field="metadata",
        )
        target_data = _json_loads_object(
            _row_optional_str(row, "target_json"),
            field="target",
        )
        actor = _actor_from_json(actor_data)
        if actor is None:
            msg = "plan actor cannot be null"
            raise TypeError(msg)
        if tenant_data is None:
            msg = "plan row is missing tenant JSON"
            raise TypeError(msg)
        return Plan(
            plan_id=_row_str(row, "plan_id"),
            tenant=_tenant_from_json(tenant_data),
            session_id=_row_str(row, "session_id"),
            task_id=_row_optional_str(row, "task_id"),
            action_id=_row_optional_str(row, "action_id"),
            created_by=actor,
            status=PlanStatus(_row_str(row, "status")),
            risk_level=PlanRiskLevel(_row_str(row, "risk_level")),
            title=_row_str(row, "title"),
            summary=_row_str(row, "summary"),
            target=_object_ref_from_json(target_data),
            estimated_count=_row_optional_int(row, "estimated_count"),
            estimated_bytes=_row_optional_int(row, "estimated_bytes"),
            idempotency_key=_row_str(row, "idempotency_key"),
            metadata=_metadata_from_json(metadata_data),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
            updated_at=_datetime_from_json(_row_str(row, "updated_at")),
            expires_at=_optional_datetime_from_json(
                _row_optional_str(row, "expires_at")
            ),
        )

    @staticmethod
    def _validate_new_plan(
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        plan: Plan,
    ) -> None:
        if plan.tenant != tenant:
            msg = "created plan tenant does not match idempotency tenant"
            raise ValueError(msg)
        if plan.idempotency_key != idempotency_key:
            msg = "created plan idempotency key does not match request key"
            raise ValueError(msg)


class FileApprovalStore(_SQLiteStore):
    """SQLite-backed actor-bound approval store."""

    def __init__(
        self,
        root: Path,
        *,
        event_store: SessionEventStore | None = None,
    ) -> None:
        """Create an approval store under ``root``."""
        super().__init__(root)
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Approval],
    ) -> Approval:
        """Create an approval once for a logical tenant-scoped request."""
        external_event: tuple[Approval, VerifiedActor, str, Mapping[str, object]] | None
        external_event = None
        with self._transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            approval = create()
            self._validate_new_approval(
                tenant=tenant,
                idempotency_key=idempotency_key,
                approval=approval,
            )
            self._insert_approval(con, approval)
            event_payload: Mapping[str, object] = _approval_event_payload(
                approval,
                reason="approval_requested",
            )
            if self._event_store_in_same_db():
                self._append_approval_event_with_connection(
                    con,
                    approval=approval,
                    actor=approval.requested_by,
                    event_type="approval_requested",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    approval,
                    approval.requested_by,
                    "approval_requested",
                    event_payload,
                )
        if external_event is not None:
            approval, actor, event_type, event_payload = external_event
            self._append_approval_event(
                approval=approval,
                actor=actor,
                event_type=event_type,
                event_payload=event_payload,
            )
        return approval

    def decide(  # noqa: PLR0913 - approval binding is explicit at the boundary
        self,
        *,
        tenant: TenantIdentity,
        approval_id: str,
        actor: VerifiedActor,
        choice: ApprovalChoice,
        exact_target: str,
        now: datetime,
        note: str | None = None,
    ) -> ApprovalDecisionResult:
        """Attempt to approve or reject one approval record."""
        external_event: tuple[Approval, VerifiedActor, str, Mapping[str, object]] | None
        external_event = None
        with self._transaction() as con:
            approval = self._read_approval_with_connection(
                con,
                tenant=tenant,
                approval_id=approval_id,
            )
            if approval is None:
                return ApprovalDecisionResult(
                    approval=None,
                    accepted=False,
                    reason="approval_not_found",
                )
            reason = _approval_decision_guard(
                approval=approval,
                actor=actor,
                exact_target=exact_target,
                now=now,
            )
            next_status: ApprovalStatus | None
            accepted = reason == "approved"
            if reason == "approved" and choice is ApprovalChoice.APPROVE:
                next_status = ApprovalStatus.APPROVED
            elif reason == "approved" and choice is ApprovalChoice.REJECT:
                reason = "rejected"
                next_status = ApprovalStatus.REJECTED
                accepted = True
            elif reason == "expired":
                next_status = ApprovalStatus.EXPIRED
                accepted = False
            else:
                next_status = None
                accepted = False

            updated = approval
            event_type = "approval_decision_failed"
            if next_status is not None:
                validate_approval_transition(
                    expected=approval.status,
                    next_status=next_status,
                )
                updated = replace(
                    approval,
                    status=next_status,
                    updated_at=now,
                    decided_by=actor,
                    decided_at=now,
                    decision_note=note,
                )
                self._update_approval(con, updated)
                event_type = (
                    "approval_expired"
                    if next_status is ApprovalStatus.EXPIRED
                    else "approval_decided"
                )
            event_payload = _approval_event_payload(
                updated,
                choice=choice.value,
                decided_by=actor.user_id,
                reason=reason,
            )
            if self._event_store_in_same_db():
                self._append_approval_event_with_connection(
                    con,
                    approval=updated,
                    actor=actor,
                    event_type=event_type,
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (updated, actor, event_type, event_payload)
        if external_event is not None:
            approval_for_event, actor_for_event, event_type, event_payload = (
                external_event
            )
            self._append_approval_event(
                approval=approval_for_event,
                actor=actor_for_event,
                event_type=event_type,
                event_payload=event_payload,
            )
        return ApprovalDecisionResult(
            approval=updated,
            accepted=accepted,
            reason=reason,
        )

    def get(self, *, tenant: TenantIdentity, approval_id: str) -> Approval | None:
        """Return one tenant-scoped approval if it exists."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_approval_with_connection(
                    con,
                    tenant=tenant,
                    approval_id=approval_id,
                )
            finally:
                con.close()

    def find_pending_for_action(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
    ) -> Approval | None:
        """Return the pending approval for an action, if one exists."""
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT * FROM approvals
                    WHERE tenant_id = ? AND action_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (tenant.tenant_id, action_id, ApprovalStatus.PENDING.value),
                ).fetchone()
            finally:
                con.close()
        if row is None:
            return None
        parsed = _safe_row(self._approval_from_row, row)
        return cast("Approval | None", parsed)

    def find_pending_for_task(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
    ) -> Approval | None:
        """Return the most-recent pending approval linked to a task."""
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT * FROM approvals
                    WHERE tenant_id = ? AND task_id = ? AND status = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (tenant.tenant_id, task_id, ApprovalStatus.PENDING.value),
                ).fetchone()
            finally:
                con.close()
        if row is None:
            return None
        parsed = _safe_row(self._approval_from_row, row)
        return cast("Approval | None", parsed)

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        limit: int = 100,
    ) -> Sequence[Approval]:
        """Return recent approvals for one tenant, newest first."""
        bounded_limit = max(1, min(limit, 500))
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT * FROM approvals
                    WHERE tenant_id = ?
                    ORDER BY updated_at DESC LIMIT ?
                    """,
                    (tenant.tenant_id, bounded_limit),
                ).fetchall()
            finally:
                con.close()
        approvals = [
            approval
            for row in rows
            if (approval := _safe_row(self._approval_from_row, row)) is not None
        ]
        return tuple(cast("Approval", approval) for approval in approvals)

    def _event_store_in_same_db(self) -> bool:
        return (
            isinstance(self._event_store, FileSessionEventStore)
            and self._event_store.db_path == self._db_path
        )

    def _append_approval_event_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        approval: Approval,
        actor: VerifiedActor,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        _append_event(
            con,
            _EventAppend(
                tenant=approval.tenant,
                session_id=approval.session_id,
                event_type=event_type,
                actor=actor,
                payload=event_payload,
            ),
        )

    def _append_approval_event(
        self,
        *,
        approval: Approval,
        actor: VerifiedActor,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        self._event_store.append(
            tenant=approval.tenant,
            session_id=approval.session_id,
            event_type=event_type,
            actor=actor,
            payload=event_payload,
        )

    @staticmethod
    def _insert_approval(con: sqlite3.Connection, approval: Approval) -> None:
        con.execute(
            """
            INSERT INTO approvals (
                approval_id, tenant_id, tenant_json, session_id, task_id, plan_id,
                action_id, requested_by_json, required_actor_id,
                allowed_actor_ids_json, status, risk_level, exact_target, reason,
                idempotency_key, created_at, updated_at, expires_at,
                decided_by_json, decided_at, decision_note, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _approval_sql_values(approval),
        )

    @staticmethod
    def _update_approval(con: sqlite3.Connection, approval: Approval) -> None:
        con.execute(
            """
            UPDATE approvals
            SET status = ?,
                updated_at = ?,
                decided_by_json = ?,
                decided_at = ?,
                decision_note = ?,
                schema_version = ?
            WHERE tenant_id = ? AND approval_id = ?
            """,
            (
                approval.status.value,
                _datetime_to_json(approval.updated_at),
                _json_dumps(_actor_to_json(approval.decided_by)),
                (
                    None
                    if approval.decided_at is None
                    else _datetime_to_json(approval.decided_at)
                ),
                approval.decision_note,
                _SCHEMA_VERSION,
                approval.tenant.tenant_id,
                approval.approval_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Approval | None:
        row = con.execute(
            """
            SELECT * FROM approvals
            WHERE tenant_id = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._approval_from_row, row)
        return cast("Approval | None", parsed)

    def _read_approval_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        approval_id: str,
    ) -> Approval | None:
        row = con.execute(
            """
            SELECT * FROM approvals
            WHERE tenant_id = ? AND approval_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, approval_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._approval_from_row, row)
        return cast("Approval | None", parsed)

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> Approval:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported approval schema version"
            raise ValueError(msg)
        tenant_data = _json_loads_object(_row_str(row, "tenant_json"), field="tenant")
        requested_by_data = _json_loads_object(
            _row_str(row, "requested_by_json"),
            field="requested_by",
        )
        decided_by_data = _json_loads_object(
            _row_optional_str(row, "decided_by_json"),
            field="decided_by",
        )
        allowed_data = _json_loads_object(
            _row_str(row, "allowed_actor_ids_json"),
            field="allowed_actor_ids",
        )
        requested_by = _actor_from_json(requested_by_data)
        if requested_by is None:
            msg = "approval requester cannot be null"
            raise TypeError(msg)
        if tenant_data is None:
            msg = "approval row is missing tenant JSON"
            raise TypeError(msg)
        return Approval(
            approval_id=_row_str(row, "approval_id"),
            tenant=_tenant_from_json(tenant_data),
            session_id=_row_str(row, "session_id"),
            task_id=_row_optional_str(row, "task_id"),
            plan_id=_row_optional_str(row, "plan_id"),
            action_id=_row_optional_str(row, "action_id"),
            requested_by=requested_by,
            required_actor_id=_row_str(row, "required_actor_id"),
            allowed_actor_ids=_string_tuple_from_json(allowed_data),
            status=ApprovalStatus(_row_str(row, "status")),
            risk_level=PlanRiskLevel(_row_str(row, "risk_level")),
            exact_target=_row_str(row, "exact_target"),
            reason=_row_str(row, "reason"),
            idempotency_key=_row_str(row, "idempotency_key"),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
            updated_at=_datetime_from_json(_row_str(row, "updated_at")),
            expires_at=_datetime_from_json(_row_str(row, "expires_at")),
            decided_by=_actor_from_json(decided_by_data),
            decided_at=_optional_datetime_from_json(
                _row_optional_str(row, "decided_at")
            ),
            decision_note=_row_optional_str(row, "decision_note"),
        )

    @staticmethod
    def _validate_new_approval(
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        approval: Approval,
    ) -> None:
        if approval.tenant != tenant:
            msg = "created approval tenant does not match idempotency tenant"
            raise ValueError(msg)
        if approval.idempotency_key != idempotency_key:
            msg = "created approval idempotency key does not match request key"
            raise ValueError(msg)


def _plan_sql_values(plan: Plan) -> tuple[object, ...]:
    return (
        plan.plan_id,
        plan.tenant.tenant_id,
        _json_dumps(_tenant_to_json(plan.tenant)),
        plan.session_id,
        plan.task_id,
        plan.action_id,
        _json_dumps(_actor_to_json(plan.created_by)),
        plan.status.value,
        plan.risk_level.value,
        plan.title,
        plan.summary,
        _json_dumps(_object_ref_to_json(plan.target)),
        plan.estimated_count,
        plan.estimated_bytes,
        plan.idempotency_key,
        _json_dumps(_metadata_to_json(plan.metadata)),
        _datetime_to_json(plan.created_at),
        _datetime_to_json(plan.updated_at),
        None if plan.expires_at is None else _datetime_to_json(plan.expires_at),
        _SCHEMA_VERSION,
    )


def _approval_sql_values(approval: Approval) -> tuple[object, ...]:
    return (
        approval.approval_id,
        approval.tenant.tenant_id,
        _json_dumps(_tenant_to_json(approval.tenant)),
        approval.session_id,
        approval.task_id,
        approval.plan_id,
        approval.action_id,
        _json_dumps(_actor_to_json(approval.requested_by)),
        approval.required_actor_id,
        _json_dumps(_string_tuple_to_json(approval.allowed_actor_ids)),
        approval.status.value,
        approval.risk_level.value,
        approval.exact_target,
        approval.reason,
        approval.idempotency_key,
        _datetime_to_json(approval.created_at),
        _datetime_to_json(approval.updated_at),
        _datetime_to_json(approval.expires_at),
        _json_dumps(_actor_to_json(approval.decided_by)),
        (
            None
            if approval.decided_at is None
            else _datetime_to_json(approval.decided_at)
        ),
        approval.decision_note,
        _SCHEMA_VERSION,
    )


def _approval_decision_guard(
    *,
    approval: Approval,
    actor: VerifiedActor,
    exact_target: str,
    now: datetime,
) -> str:
    if approval.status is not ApprovalStatus.PENDING:
        return "already_decided"
    if approval.tenant != actor.tenant:
        return "tenant_mismatch"
    allowed_actor_ids = set(approval.allowed_actor_ids)
    allowed_actor_ids.add(approval.required_actor_id)
    if actor.user_id not in allowed_actor_ids:
        return "wrong_actor"
    if approval.expires_at <= now:
        return "expired"
    if approval.exact_target != exact_target:
        return "target_mismatch"
    return "approved"


def _approval_event_payload(
    approval: Approval,
    *,
    reason: str,
    choice: str | None = None,
    decided_by: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "approval_id": approval.approval_id,
        "status": approval.status.value,
        "risk_level": approval.risk_level.value,
        "exact_target": approval.exact_target,
        "required_actor_id": approval.required_actor_id,
        "plan_id": approval.plan_id,
        "action_id": approval.action_id,
        "reason": reason,
    }
    if choice is not None:
        payload["choice"] = choice
    if decided_by is not None:
        payload["decided_by"] = decided_by
    return payload


class FileWorkerLeaseStore(_SQLiteStore):
    """SQLite-backed task lease store for local worker coordination."""

    def acquire(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> WorkerLease | None:
        """Claim a task if no active lease exists."""
        with self._transaction() as con:
            if not self._task_exists_with_connection(
                con,
                tenant=tenant,
                task_id=task_id,
            ):
                return None
            current = self._read_lease_with_connection(
                con,
                tenant=tenant,
                task_id=task_id,
            )
            if current is not None and current.lease_until > now:
                return None
            lease = WorkerLease(
                tenant=tenant,
                task_id=task_id,
                worker_id=worker_id,
                lease_until=lease_until,
                acquired_at=now,
                heartbeat_at=now,
                attempt=1 if current is None else current.attempt + 1,
            )
            self._upsert_lease(con, lease)
            return lease

    def heartbeat(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> WorkerLease | None:
        """Extend an active lease owned by ``worker_id``."""
        with self._transaction() as con:
            current = self._read_lease_with_connection(
                con,
                tenant=tenant,
                task_id=task_id,
            )
            if (
                current is None
                or current.worker_id != worker_id
                or current.lease_until <= now
            ):
                return None
            lease = replace(current, lease_until=lease_until, heartbeat_at=now)
            self._upsert_lease(con, lease)
            return lease

    def release(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
    ) -> bool:
        """Release an active lease owned by ``worker_id``."""
        with self._transaction() as con:
            cursor = con.execute(
                """
                DELETE FROM worker_leases
                WHERE tenant_id = ? AND task_id = ? AND worker_id = ?
                """,
                (tenant.tenant_id, task_id, worker_id),
            )
            return cursor.rowcount == 1

    def get(self, *, tenant: TenantIdentity, task_id: str) -> WorkerLease | None:
        """Return the current lease for one tenant-scoped task."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_lease_with_connection(
                    con,
                    tenant=tenant,
                    task_id=task_id,
                )
            finally:
                con.close()

    @staticmethod
    def _upsert_lease(con: sqlite3.Connection, lease: WorkerLease) -> None:
        con.execute(
            """
            INSERT INTO worker_leases (
                tenant_id, task_id, worker_id, lease_until, acquired_at,
                heartbeat_at, attempt, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (tenant_id, task_id) DO UPDATE SET
                worker_id = excluded.worker_id,
                lease_until = excluded.lease_until,
                acquired_at = excluded.acquired_at,
                heartbeat_at = excluded.heartbeat_at,
                attempt = excluded.attempt,
                schema_version = excluded.schema_version
            """,
            _worker_lease_sql_values(lease),
        )

    @staticmethod
    def _task_exists_with_connection(
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        task_id: str,
    ) -> bool:
        row = con.execute(
            """
            SELECT 1 FROM tasks
            WHERE tenant_id = ? AND task_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, task_id),
        ).fetchone()
        return row is not None

    @staticmethod
    def _read_lease_with_connection(
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        task_id: str,
    ) -> WorkerLease | None:
        row = con.execute(
            """
            SELECT * FROM worker_leases
            WHERE tenant_id = ? AND task_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, task_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(FileWorkerLeaseStore._lease_from_row, row)
        return cast("WorkerLease | None", parsed)

    @staticmethod
    def _lease_from_row(row: sqlite3.Row) -> WorkerLease:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported worker lease schema version"
            raise ValueError(msg)
        return WorkerLease(
            tenant=TenantIdentity(
                platform=_row_str(row, "tenant_id").split(":", maxsplit=1)[0],
                workspace_id=_row_str(row, "tenant_id").split(":", maxsplit=1)[1],
            ),
            task_id=_row_str(row, "task_id"),
            worker_id=_row_str(row, "worker_id"),
            lease_until=_datetime_from_json(_row_str(row, "lease_until")),
            acquired_at=_datetime_from_json(_row_str(row, "acquired_at")),
            heartbeat_at=_datetime_from_json(_row_str(row, "heartbeat_at")),
            attempt=_row_int(row, "attempt"),
        )


def _worker_lease_sql_values(lease: WorkerLease) -> tuple[object, ...]:
    return (
        lease.tenant.tenant_id,
        lease.task_id,
        lease.worker_id,
        _datetime_to_json(lease.lease_until),
        _datetime_to_json(lease.acquired_at),
        _datetime_to_json(lease.heartbeat_at),
        lease.attempt,
        _SCHEMA_VERSION,
    )


class FileActionStore(_SQLiteStore):
    """SQLite-backed action store with transactional idempotency and events."""

    def __init__(
        self,
        root: Path,
        *,
        event_store: SessionEventStore | None = None,
    ) -> None:
        """Create an action store under ``root``."""
        super().__init__(root)
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Action],
    ) -> Action:
        """Create an action once for a logical tenant-scoped request."""
        external_event: tuple[Action, str, Mapping[str, object]] | None = None
        with self._transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            action = create()
            self._validate_new_action(
                tenant=tenant,
                idempotency_key=idempotency_key,
                action=action,
            )
            self._insert_action(con, action)
            event_payload: Mapping[str, object] = {
                "status": action.status.value,
                "kind": action.kind.value,
            }
            if self._event_store_in_same_db():
                self._append_action_event_with_connection(
                    con,
                    action=action,
                    event_type="action_created",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (action, "action_created", event_payload)
        if external_event is not None:
            event_action, event_type, event_payload = external_event
            self._append_action_event(
                action=event_action,
                event_type=event_type,
                event_payload=event_payload,
            )
        return action

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
        transition: ActionTransition,
    ) -> Action | None:
        """Move an action only if it is still in the expected state."""
        validate_action_transition(
            expected=transition.expected,
            next_status=transition.next_status,
        )
        external_event: tuple[Action, str, Mapping[str, object]] | None = None
        with self._transaction() as con:
            action = self._read_action_with_connection(
                con,
                tenant=tenant,
                action_id=action_id,
            )
            if action is None or action.status is not transition.expected:
                return None
            updated = replace(
                action,
                status=transition.next_status,
                result=(
                    transition.result
                    if transition.result is not None
                    else action.result
                ),
                failure=(
                    transition.failure
                    if transition.failure is not None
                    else action.failure
                ),
                updated_at=datetime.now(UTC),
            )
            self._update_action(con, updated)
            if self._event_store_in_same_db():
                self._append_action_event_with_connection(
                    con,
                    action=updated,
                    event_type=transition.event_type,
                    event_payload=transition.event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    updated,
                    transition.event_type,
                    transition.event_payload,
                )
        if external_event is not None:
            event_action, event_type, event_payload = external_event
            self._append_action_event(
                action=event_action,
                event_type=event_type,
                event_payload=event_payload,
            )
        return updated

    def get(self, *, tenant: TenantIdentity, action_id: str) -> Action | None:
        """Return one tenant-scoped action if it exists."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_action_with_connection(
                    con,
                    tenant=tenant,
                    action_id=action_id,
                )
            finally:
                con.close()

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Action]:
        """Return all known actions for one tenant-scoped session."""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT * FROM actions
                    WHERE tenant_id = ? AND session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (tenant.tenant_id, session_id),
                ).fetchall()
            finally:
                con.close()
        actions = [
            action
            for row in rows
            if (action := _safe_row(self._action_from_row, row)) is not None
        ]
        return tuple(cast("Action", action) for action in actions)

    def find_latest_awaiting_confirmation(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        kind: ActionKind,
    ) -> Action | None:
        """Return the newest action waiting for confirmation in one session."""
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT * FROM actions
                    WHERE tenant_id = ?
                      AND session_id = ?
                      AND kind = ?
                      AND status = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (
                        tenant.tenant_id,
                        session_id,
                        kind.value,
                        ActionStatus.AWAITING_CONFIRMATION.value,
                    ),
                ).fetchone()
            finally:
                con.close()
        if row is None:
            return None
        parsed = _safe_row(self._action_from_row, row)
        return cast("Action | None", parsed)

    def _event_store_in_same_db(self) -> bool:
        return (
            isinstance(self._event_store, FileSessionEventStore)
            and self._event_store.db_path == self._db_path
        )

    def _append_action_event_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        action: Action,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        payload = {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            "policy_decision": (
                None
                if action.policy_decision is None
                else action.policy_decision.decision.value
            ),
            **dict(event_payload),
        }
        _append_event(
            con,
            _EventAppend(
                tenant=action.tenant,
                session_id=action.session_id,
                event_type=event_type,
                actor=action.actor,
                payload=payload,
            ),
        )

    def _append_action_event(
        self,
        *,
        action: Action,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            "policy_decision": (
                None
                if action.policy_decision is None
                else action.policy_decision.decision.value
            ),
            **dict(event_payload),
        }
        self._event_store.append(
            tenant=action.tenant,
            session_id=action.session_id,
            event_type=event_type,
            actor=action.actor,
            payload=payload,
        )

    @staticmethod
    def _insert_action(con: sqlite3.Connection, action: Action) -> None:
        con.execute(
            """
            INSERT INTO actions (
                action_id, tenant_id, tenant_json, session_id, actor_json,
                kind, target_json, status, idempotency_key, input_json,
                result_json, failure_json, policy_decision_json, created_at,
                updated_at, expires_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _action_sql_values(action),
        )

    @staticmethod
    def _update_action(con: sqlite3.Connection, action: Action) -> None:
        con.execute(
            """
            UPDATE actions
            SET status = ?,
                result_json = ?,
                failure_json = ?,
                policy_decision_json = ?,
                updated_at = ?,
                expires_at = ?,
                schema_version = ?
            WHERE tenant_id = ? AND action_id = ?
            """,
            (
                action.status.value,
                _json_dumps(_action_result_to_json(action.result)),
                _json_dumps(_action_failure_to_json(action.failure)),
                _json_dumps(_policy_decision_to_json(action.policy_decision)),
                _datetime_to_json(action.updated_at),
                (
                    None
                    if action.expires_at is None
                    else _datetime_to_json(action.expires_at)
                ),
                _SCHEMA_VERSION,
                action.tenant.tenant_id,
                action.action_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Action | None:
        row = con.execute(
            """
            SELECT * FROM actions
            WHERE tenant_id = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._action_from_row, row)
        return cast("Action | None", parsed)

    def _read_action_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        action_id: str,
    ) -> Action | None:
        row = con.execute(
            """
            SELECT * FROM actions
            WHERE tenant_id = ? AND action_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, action_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._action_from_row, row)
        return cast("Action | None", parsed)

    @staticmethod
    def _action_from_row(row: sqlite3.Row) -> Action:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported action schema version"
            raise ValueError(msg)
        kind = ActionKind(_row_str(row, "kind"))
        tenant_data = _json_loads_object(_row_str(row, "tenant_json"), field="tenant")
        actor_data = _json_loads_object(_row_str(row, "actor_json"), field="actor")
        target_data = _json_loads_object(
            _row_optional_str(row, "target_json"),
            field="target",
        )
        input_data = _json_loads_object(_row_str(row, "input_json"), field="input")
        result_data = _json_loads_object(
            _row_optional_str(row, "result_json"),
            field="result",
        )
        failure_data = _json_loads_object(
            _row_optional_str(row, "failure_json"), field="failure"
        )
        policy_decision_data = _json_loads_object(
            _row_optional_str(row, "policy_decision_json"),
            field="policy_decision",
        )
        actor = _actor_from_json(actor_data)
        if actor is None:
            msg = "action actor cannot be null"
            raise TypeError(msg)
        if tenant_data is None or input_data is None:
            msg = "action row is missing required JSON fields"
            raise TypeError(msg)
        return Action(
            action_id=_row_str(row, "action_id"),
            tenant=_tenant_from_json(tenant_data),
            session_id=_row_str(row, "session_id"),
            actor=actor,
            kind=kind,
            target=_object_ref_from_json(target_data),
            status=ActionStatus(_row_str(row, "status")),
            idempotency_key=_row_str(row, "idempotency_key"),
            input=_action_input_from_json(kind=kind, data=input_data),
            result=_action_result_from_json(kind=kind, data=result_data),
            failure=_action_failure_from_json(failure_data),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
            updated_at=_datetime_from_json(_row_str(row, "updated_at")),
            expires_at=_optional_datetime_from_json(
                _row_optional_str(row, "expires_at")
            ),
            policy_decision=_policy_decision_from_json(policy_decision_data),
        )

    @staticmethod
    def _validate_new_action(
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        action: Action,
    ) -> None:
        if action.tenant != tenant:
            msg = "created action tenant does not match idempotency tenant"
            raise ValueError(msg)
        if action.idempotency_key != idempotency_key:
            msg = "created action idempotency key does not match request key"
            raise ValueError(msg)


def _action_sql_values(action: Action) -> tuple[object, ...]:
    return (
        action.action_id,
        action.tenant.tenant_id,
        _json_dumps(_tenant_to_json(action.tenant)),
        action.session_id,
        _json_dumps(_actor_to_json(action.actor)),
        action.kind.value,
        _json_dumps(_object_ref_to_json(action.target)),
        action.status.value,
        action.idempotency_key,
        _json_dumps(_action_input_to_json(action.input)),
        _json_dumps(_action_result_to_json(action.result)),
        _json_dumps(_action_failure_to_json(action.failure)),
        _json_dumps(_policy_decision_to_json(action.policy_decision)),
        _datetime_to_json(action.created_at),
        _datetime_to_json(action.updated_at),
        None if action.expires_at is None else _datetime_to_json(action.expires_at),
        _SCHEMA_VERSION,
    )


class FileArtifactStore(_SQLiteStore):
    """SQLite-backed immutable artifact store for local Nimbus deployments."""

    def __init__(
        self,
        root: Path,
        *,
        event_store: SessionEventStore | None = None,
    ) -> None:
        """Create an artifact store under ``root``."""
        super().__init__(root)
        self._event_store = event_store

    def create(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None = None,
    ) -> Artifact:
        """Persist one immutable artifact."""
        artifact = ensure_artifact_digest(artifact)
        external_event: tuple[Artifact, VerifiedActor | None] | None = None
        with self._transaction() as con:
            existing = self._read_artifact_with_connection(
                con,
                tenant=artifact.tenant,
                artifact_id=artifact.artifact_id,
            )
            if existing is not None:
                return existing
            self._insert_artifact(con, artifact)
            if self._event_store_in_same_db():
                self._append_artifact_event_with_connection(
                    con,
                    artifact=artifact,
                    actor=actor,
                )
            elif self._event_store is not None:
                external_event = (artifact, actor)
        if external_event is not None:
            artifact, actor = external_event
            self._append_artifact_event(artifact=artifact, actor=actor)
        return artifact

    def get(
        self,
        *,
        tenant: TenantIdentity,
        artifact_id: str,
    ) -> Artifact | None:
        """Return one artifact by ID, or ``None`` if not found."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_artifact_with_connection(
                    con,
                    tenant=tenant,
                    artifact_id=artifact_id,
                )
            finally:
                con.close()

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Artifact]:
        """Return artifacts for one tenant-scoped session."""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE tenant_id = ? AND session_id = ?
                    ORDER BY created_at ASC
                    """,
                    (tenant.tenant_id, session_id),
                ).fetchall()
            finally:
                con.close()
        artifacts = [
            artifact
            for row in rows
            if (artifact := _safe_row(self._artifact_from_row, row)) is not None
        ]
        return tuple(cast("Artifact", artifact) for artifact in artifacts)

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        kind: ArtifactKind | None = None,
        limit: int = 100,
    ) -> Sequence[Artifact]:
        """Return recent artifacts for one tenant, newest first."""
        with self._lock:
            con = self._connect()
            try:
                if kind is None:
                    rows = con.execute(
                        """
                        SELECT * FROM artifacts
                        WHERE tenant_id = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (tenant.tenant_id, limit),
                    ).fetchall()
                else:
                    rows = con.execute(
                        """
                        SELECT * FROM artifacts
                        WHERE tenant_id = ? AND kind = ?
                        ORDER BY created_at DESC
                        LIMIT ?
                        """,
                        (tenant.tenant_id, kind, limit),
                    ).fetchall()
            finally:
                con.close()
        artifacts = [
            artifact
            for row in rows
            if (artifact := _safe_row(self._artifact_from_row, row)) is not None
        ]
        return tuple(cast("Artifact", artifact) for artifact in artifacts)

    def _event_store_in_same_db(self) -> bool:
        return (
            isinstance(self._event_store, FileSessionEventStore)
            and self._event_store.db_path == self._db_path
        )

    @staticmethod
    def _insert_artifact(con: sqlite3.Connection, artifact: Artifact) -> None:
        con.execute(
            """
            INSERT INTO artifacts (
                artifact_id, tenant_id, tenant_json, session_id, action_id,
                kind, uri, payload_json, payload_digest, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact.artifact_id,
                artifact.tenant.tenant_id,
                _json_dumps(_tenant_to_json(artifact.tenant)),
                artifact.session_id,
                artifact.action_id,
                artifact.kind,
                artifact.uri,
                _json_dumps(_artifact_payload_to_json(artifact.payload)),
                artifact.payload_digest or artifact_payload_digest(artifact.payload),
                _datetime_to_json(artifact.created_at),
                _SCHEMA_VERSION,
            ),
        )

    def _read_artifact_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        artifact_id: str,
    ) -> Artifact | None:
        row = con.execute(
            """
            SELECT * FROM artifacts
            WHERE tenant_id = ? AND artifact_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, artifact_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(self._artifact_from_row, row)
        return cast("Artifact | None", parsed)

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> Artifact:
        if _row_int(row, "schema_version") != _SCHEMA_VERSION:
            msg = "unsupported artifact schema version"
            raise ValueError(msg)
        kind = _row_str(row, "kind")
        if kind not in {
            "conflict_artifact",
            "delete_report",
            "drift_report",
            "migration_decision_packet",
            "provider_health",
            "proof_receipt",
            "repair_receipt",
            "storage_mutation_report",
            "upload_report",
            "manifest",
            "verification_report",
        }:
            msg = f"unsupported artifact kind: {kind}"
            raise ValueError(msg)
        tenant_data = _json_loads_object(_row_str(row, "tenant_json"), field="tenant")
        payload_data = _json_loads_object(
            _row_str(row, "payload_json"),
            field="payload",
        )
        if tenant_data is None or payload_data is None:
            msg = "artifact row is missing required JSON fields"
            raise TypeError(msg)
        return Artifact(
            artifact_id=_row_str(row, "artifact_id"),
            tenant=_tenant_from_json(tenant_data),
            session_id=_row_str(row, "session_id"),
            action_id=_row_optional_str(row, "action_id"),
            kind=cast("ArtifactKind", kind),
            uri=_row_optional_str(row, "uri"),
            payload=_artifact_payload_from_json(kind=kind, data=payload_data),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
            payload_digest=_row_optional_str(row, "payload_digest"),
        )

    def _append_artifact_event_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        _append_event(
            con,
            _EventAppend(
                tenant=artifact.tenant,
                session_id=artifact.session_id,
                event_type="artifact_created",
                actor=actor,
                payload={
                    "artifact_id": artifact.artifact_id,
                    "action_id": artifact.action_id,
                    "kind": artifact.kind,
                },
            ),
        )

    def _append_artifact_event(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        if self._event_store is None:
            return
        self._event_store.append(
            tenant=artifact.tenant,
            session_id=artifact.session_id,
            event_type="artifact_created",
            actor=actor,
            payload={
                "artifact_id": artifact.artifact_id,
                "action_id": artifact.action_id,
                "kind": artifact.kind,
            },
        )


def _append_event_postgres(
    con: PostgresConnection[dict[str, object]],
    event: _EventAppend,
) -> SessionEvent:
    """Append an ordered session event using Postgres advisory locking."""
    lock_key = f"{event.tenant.tenant_id}:{event.session_id}"
    con.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
    row = con.execute(
        """
        SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
        FROM session_events
        WHERE tenant_id = %s AND session_id = %s
        """,
        (event.tenant.tenant_id, event.session_id),
    ).fetchone()
    if row is None:
        msg = "Postgres did not return the next event sequence"
        raise RuntimeError(msg)
    raw_sequence = row["next_sequence"]
    if not isinstance(raw_sequence, int | str):
        msg = "Postgres returned a non-integer event sequence"
        raise TypeError(msg)
    sequence = int(raw_sequence)
    session_event = SessionEvent(
        tenant=event.tenant,
        session_id=event.session_id,
        sequence=sequence,
        event_id=f"evt-{uuid.uuid4().hex}",
        event_type=event.event_type,
        actor=event.actor,
        payload=dict(event.payload),
        created_at=datetime.now(UTC),
    )
    con.execute(
        """
        INSERT INTO session_events (
            tenant_id, session_id, sequence, event_id, event_type,
            actor_json, payload_json, created_at, schema_version
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event.tenant.tenant_id,
            event.session_id,
            sequence,
            session_event.event_id,
            event.event_type,
            _json_dumps(_actor_to_json(event.actor)),
            _json_dumps(dict(event.payload)),
            _datetime_to_json(session_event.created_at),
            _SCHEMA_VERSION,
        ),
    )
    return session_event


class PostgresSessionEventStore:
    """Postgres-backed ordered event store for Render deployments."""

    def append(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        event_type: str,
        actor: VerifiedActor | None,
        payload: Mapping[str, object],
    ) -> SessionEvent:
        """Append one event and return it with a sequence number."""
        with pg_transaction() as con:
            return _append_event_postgres(
                con,
                _EventAppend(
                    tenant=tenant,
                    session_id=session_id,
                    event_type=event_type,
                    actor=actor,
                    payload=payload,
                ),
            )

    def list_events(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> Sequence[SessionEvent]:
        """Return ordered events for one tenant-scoped session."""
        with pg_connect() as con:
            rows = con.execute(
                """
                SELECT * FROM session_events
                WHERE tenant_id = %s AND session_id = %s AND sequence > %s
                ORDER BY sequence ASC LIMIT %s
                """,
                (
                    tenant.tenant_id,
                    session_id,
                    0 if after_sequence is None else after_sequence,
                    limit,
                ),
            ).fetchall()
        events = [
            event
            for row in rows
            if (
                event := _safe_row(
                    FileSessionEventStore._event_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("SessionEvent", event) for event in events)

    def list_for_tenant_before(
        self,
        *,
        tenant: TenantIdentity,
        before: datetime,
        limit: int = 10_000,
    ) -> Sequence[SessionEvent]:
        """Return tenant events with created_at <= before, ordered chronologically."""
        bounded_limit = max(1, min(limit, 100_000))
        before_str = _datetime_to_json(before)
        with pg_connect() as con:
            rows = con.execute(
                """
                SELECT * FROM session_events
                WHERE tenant_id = %s AND created_at <= %s
                ORDER BY created_at ASC, event_id ASC
                LIMIT %s
                """,
                (tenant.tenant_id, before_str, bounded_limit),
            ).fetchall()
        events = [
            event
            for row in rows
            if (
                event := _safe_row(
                    FileSessionEventStore._event_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("SessionEvent", event) for event in events)


class PostgresTaskStore:
    """Postgres-backed task store with transactional idempotency and events."""

    def __init__(self, *, event_store: SessionEventStore | None = None) -> None:
        """Create a task store using the configured ``DATABASE_URL``."""
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Task],
    ) -> Task:
        """Create a task once for a logical tenant-scoped request."""
        external_event: tuple[Task, str, Mapping[str, object]] | None = None
        with pg_transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            task = create()
            FileTaskStore._validate_new_task(  # noqa: SLF001
                tenant=tenant,
                idempotency_key=idempotency_key,
                task=task,
            )
            self._insert_task(con, task)
            event_payload: Mapping[str, object] = {
                "task_id": task.task_id,
                "status": task.status.value,
                "intent": task.intent,
            }
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_task_event_with_connection(
                    con,
                    task=task,
                    event_type="task_created",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (task, "task_created", event_payload)
        if external_event is not None:
            event_task, event_type, event_payload = external_event
            self._append_task_event(
                task=event_task,
                event_type=event_type,
                event_payload=event_payload,
            )
        return task

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        transition: TaskTransition,
    ) -> Task | None:
        """Move a task only if it is still in the expected state."""
        validate_task_transition(
            expected=transition.expected,
            next_status=transition.next_status,
        )
        external_event: tuple[Task, str, Mapping[str, object]] | None = None
        with pg_transaction() as con:
            task = self._read_task_with_connection(
                con,
                tenant=tenant,
                task_id=task_id,
            )
            if task is None or task.status is not transition.expected:
                return None
            updated = replace(
                task,
                status=transition.next_status,
                failure_detail=(
                    transition.failure_detail
                    if transition.failure_detail is not None
                    else task.failure_detail
                ),
                updated_at=datetime.now(UTC),
            )
            self._update_task(con, updated)
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_task_event_with_connection(
                    con,
                    task=updated,
                    event_type=transition.event_type,
                    event_payload=transition.event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    updated,
                    transition.event_type,
                    transition.event_payload,
                )
        if external_event is not None:
            event_task, event_type, event_payload = external_event
            self._append_task_event(
                task=event_task,
                event_type=event_type,
                event_payload=event_payload,
            )
        return updated

    def get(self, *, tenant: TenantIdentity, task_id: str) -> Task | None:
        """Return one tenant-scoped task if it exists."""
        with pg_connect() as con:
            return self._read_task_with_connection(
                con,
                tenant=tenant,
                task_id=task_id,
            )

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        status: TaskStatus | None = None,
        limit: int = 100,
    ) -> Sequence[Task]:
        """Return recent tasks for one tenant."""
        if limit < 1:
            return ()
        bounded_limit = min(limit, 500)
        with pg_connect() as con:
            if status is None:
                rows = con.execute(
                    """
                    SELECT * FROM tasks
                    WHERE tenant_id = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (tenant.tenant_id, bounded_limit),
                ).fetchall()
            else:
                rows = con.execute(
                    """
                    SELECT * FROM tasks
                    WHERE tenant_id = %s AND status = %s
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (tenant.tenant_id, status.value, bounded_limit),
                ).fetchall()
        tasks = [
            task
            for row in rows
            if (
                task := _safe_row(
                    FileTaskStore._task_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("Task", task) for task in tasks)

    @staticmethod
    def _insert_task(
        con: PostgresConnection[dict[str, object]],
        task: Task,
    ) -> None:
        con.execute(
            """
            INSERT INTO tasks (
                task_id, tenant_id, tenant_json, session_id, actor_json,
                status, intent, source_ref, idempotency_key, metadata_json,
                failure_detail, created_at, updated_at, expires_at, schema_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            _task_sql_values(task),
        )

    @staticmethod
    def _update_task(
        con: PostgresConnection[dict[str, object]],
        task: Task,
    ) -> None:
        con.execute(
            """
            UPDATE tasks
            SET status = %s,
                failure_detail = %s,
                updated_at = %s,
                expires_at = %s,
                schema_version = %s
            WHERE tenant_id = %s AND task_id = %s
            """,
            (
                task.status.value,
                task.failure_detail,
                _datetime_to_json(task.updated_at),
                None if task.expires_at is None else _datetime_to_json(task.expires_at),
                _SCHEMA_VERSION,
                task.tenant.tenant_id,
                task.task_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Task | None:
        row = con.execute(
            """
            SELECT * FROM tasks
            WHERE tenant_id = %s AND idempotency_key = %s
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileTaskStore._task_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Task | None", parsed)

    def _read_task_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        task_id: str,
    ) -> Task | None:
        row = con.execute(
            """
            SELECT * FROM tasks
            WHERE tenant_id = %s AND task_id = %s
            LIMIT 1
            """,
            (tenant.tenant_id, task_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileTaskStore._task_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Task | None", parsed)

    def _append_task_event_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        task: Task,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        payload = {
            "task_id": task.task_id,
            "status": task.status.value,
            "intent": task.intent,
            **dict(event_payload),
        }
        _append_event_postgres(
            con,
            _EventAppend(
                tenant=task.tenant,
                session_id=task.session_id,
                event_type=event_type,
                actor=task.created_by,
                payload=payload,
            ),
        )

    def _append_task_event(
        self,
        *,
        task: Task,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "task_id": task.task_id,
            "status": task.status.value,
            "intent": task.intent,
            **dict(event_payload),
        }
        self._event_store.append(
            tenant=task.tenant,
            session_id=task.session_id,
            event_type=event_type,
            actor=task.created_by,
            payload=payload,
        )


class PostgresPlanStore:
    """Postgres-backed plan store for Render deployments."""

    def __init__(self, *, event_store: SessionEventStore | None = None) -> None:
        """Create a plan store using the configured ``DATABASE_URL``."""
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Plan],
    ) -> Plan:
        """Create a plan once for a logical tenant-scoped preview."""
        external_event: tuple[Plan, str, Mapping[str, object]] | None = None
        with pg_transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            plan = create()
            FilePlanStore._validate_new_plan(  # noqa: SLF001
                tenant=tenant,
                idempotency_key=idempotency_key,
                plan=plan,
            )
            self._insert_plan(con, plan)
            event_payload: Mapping[str, object] = {
                "plan_id": plan.plan_id,
                "status": plan.status.value,
                "risk_level": plan.risk_level.value,
                "title": plan.title,
                "action_id": plan.action_id,
            }
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_plan_event_with_connection(
                    con,
                    plan=plan,
                    event_type="plan_created",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (plan, "plan_created", event_payload)
        if external_event is not None:
            event_plan, event_type, event_payload = external_event
            self._append_plan_event(
                plan=event_plan,
                event_type=event_type,
                event_payload=event_payload,
            )
        return plan

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        plan_id: str,
        transition: PlanTransition,
    ) -> Plan | None:
        """Move a plan only if it is still in the expected state."""
        validate_plan_transition(
            expected=transition.expected,
            next_status=transition.next_status,
        )
        external_event: tuple[Plan, str, Mapping[str, object]] | None = None
        with pg_transaction() as con:
            plan = self._read_plan_with_connection(
                con,
                tenant=tenant,
                plan_id=plan_id,
            )
            if plan is None or plan.status is not transition.expected:
                return None
            updated = replace(
                plan,
                status=transition.next_status,
                updated_at=datetime.now(UTC),
            )
            self._update_plan(con, updated)
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_plan_event_with_connection(
                    con,
                    plan=updated,
                    event_type=transition.event_type,
                    event_payload=transition.event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    updated,
                    transition.event_type,
                    transition.event_payload,
                )
        if external_event is not None:
            event_plan, event_type, event_payload = external_event
            self._append_plan_event(
                plan=event_plan,
                event_type=event_type,
                event_payload=event_payload,
            )
        return updated

    def approve_candidate_group(
        self,
        *,
        tenant: TenantIdentity,
        plan_id: str,
        candidate_group_id: str,
        event_payload: Mapping[str, object],
    ) -> Plan | None:
        """Approve one candidate plan and supersede siblings atomically."""
        external_events: list[tuple[Plan, str, Mapping[str, object]]] = []
        with pg_transaction() as con:
            selected = self._read_plan_with_connection(
                con,
                tenant=tenant,
                plan_id=plan_id,
            )
            if selected is None or selected.status is not PlanStatus.PROPOSED:
                return None
            if selected.metadata.get("candidate_group_id") != candidate_group_id:
                return None
            now = datetime.now(UTC)
            approved = replace(
                selected,
                status=PlanStatus.APPROVED,
                updated_at=now,
            )
            validate_plan_transition(
                expected=selected.status,
                next_status=approved.status,
            )
            self._update_plan(con, approved)
            self._record_plan_event(
                con,
                external_events,
                plan=approved,
                event_type="plan_approved",
                event_payload={
                    "plan_id": plan_id,
                    "candidate_group_id": candidate_group_id,
                    **dict(event_payload),
                },
            )
            for sibling in self._candidate_siblings_with_connection(
                con,
                tenant=tenant,
                candidate_group_id=candidate_group_id,
                selected_plan_id=plan_id,
            ):
                superseded = replace(
                    sibling,
                    status=PlanStatus.SUPERSEDED,
                    updated_at=now,
                )
                validate_plan_transition(
                    expected=sibling.status,
                    next_status=superseded.status,
                )
                self._update_plan(con, superseded)
                self._record_plan_event(
                    con,
                    external_events,
                    plan=superseded,
                    event_type="plan_superseded",
                    event_payload={
                        "plan_id": sibling.plan_id,
                        "candidate_group_id": candidate_group_id,
                        "superseded_by": plan_id,
                    },
                )
        for event_plan, event_type, payload in external_events:
            self._append_plan_event(
                plan=event_plan,
                event_type=event_type,
                event_payload=payload,
            )
        return approved

    def get(self, *, tenant: TenantIdentity, plan_id: str) -> Plan | None:
        """Return one tenant-scoped plan if it exists."""
        with pg_connect() as con:
            return self._read_plan_with_connection(
                con,
                tenant=tenant,
                plan_id=plan_id,
            )

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Plan]:
        """Return plans for one tenant-scoped session."""
        with pg_connect() as con:
            rows = con.execute(
                """
                SELECT * FROM plans
                WHERE tenant_id = %s AND session_id = %s
                ORDER BY created_at ASC
                """,
                (tenant.tenant_id, session_id),
            ).fetchall()
        plans = [
            plan
            for row in rows
            if (
                plan := _safe_row(
                    FilePlanStore._plan_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("Plan", plan) for plan in plans)

    @staticmethod
    def _insert_plan(
        con: PostgresConnection[dict[str, object]],
        plan: Plan,
    ) -> None:
        con.execute(
            """
            INSERT INTO plans (
                plan_id, tenant_id, tenant_json, session_id, task_id, action_id,
                actor_json, status, risk_level, title, summary, target_json,
                estimated_count, estimated_bytes, idempotency_key, metadata_json,
                created_at, updated_at, expires_at, schema_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            _plan_sql_values(plan),
        )

    @staticmethod
    def _update_plan(
        con: PostgresConnection[dict[str, object]],
        plan: Plan,
    ) -> None:
        con.execute(
            """
            UPDATE plans
            SET status = %s,
                updated_at = %s,
                expires_at = %s,
                schema_version = %s
            WHERE tenant_id = %s AND plan_id = %s
            """,
            (
                plan.status.value,
                _datetime_to_json(plan.updated_at),
                None if plan.expires_at is None else _datetime_to_json(plan.expires_at),
                _SCHEMA_VERSION,
                plan.tenant.tenant_id,
                plan.plan_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Plan | None:
        row = con.execute(
            """
            SELECT * FROM plans
            WHERE tenant_id = %s AND idempotency_key = %s
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FilePlanStore._plan_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Plan | None", parsed)

    def _read_plan_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        plan_id: str,
    ) -> Plan | None:
        row = con.execute(
            """
            SELECT * FROM plans
            WHERE tenant_id = %s AND plan_id = %s
            LIMIT 1
            """,
            (tenant.tenant_id, plan_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FilePlanStore._plan_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Plan | None", parsed)

    def _candidate_siblings_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        candidate_group_id: str,
        selected_plan_id: str,
    ) -> Sequence[Plan]:
        rows = con.execute(
            """
            SELECT * FROM plans
            WHERE tenant_id = %s AND status = %s
            ORDER BY created_at ASC
            """,
            (tenant.tenant_id, PlanStatus.PROPOSED.value),
        ).fetchall()
        siblings: list[Plan] = []
        for row in rows:
            parsed = _safe_row(
                FilePlanStore._plan_from_row,  # noqa: SLF001
                cast("sqlite3.Row", row),
            )
            plan = cast("Plan | None", parsed)
            if plan is None:
                continue
            if plan.plan_id == selected_plan_id:
                continue
            if plan.metadata.get("candidate_group_id") == candidate_group_id:
                siblings.append(plan)
        return tuple(siblings)

    def _record_plan_event(
        self,
        con: PostgresConnection[dict[str, object]],
        external_events: list[tuple[Plan, str, Mapping[str, object]]],
        *,
        plan: Plan,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if isinstance(self._event_store, PostgresSessionEventStore):
            self._append_plan_event_with_connection(
                con,
                plan=plan,
                event_type=event_type,
                event_payload=event_payload,
            )
        elif self._event_store is not None:
            external_events.append((plan, event_type, event_payload))

    def _append_plan_event_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        plan: Plan,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        payload = {
            "plan_id": plan.plan_id,
            "status": plan.status.value,
            "risk_level": plan.risk_level.value,
            **dict(event_payload),
        }
        _append_event_postgres(
            con,
            _EventAppend(
                tenant=plan.tenant,
                session_id=plan.session_id,
                event_type=event_type,
                actor=plan.created_by,
                payload=payload,
            ),
        )

    def _append_plan_event(
        self,
        *,
        plan: Plan,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "plan_id": plan.plan_id,
            "status": plan.status.value,
            "risk_level": plan.risk_level.value,
            **dict(event_payload),
        }
        self._event_store.append(
            tenant=plan.tenant,
            session_id=plan.session_id,
            event_type=event_type,
            actor=plan.created_by,
            payload=payload,
        )


class PostgresApprovalStore:
    """Postgres-backed actor-bound approval store for Render deployments."""

    def __init__(self, *, event_store: SessionEventStore | None = None) -> None:
        """Create an approval store using the configured ``DATABASE_URL``."""
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Approval],
    ) -> Approval:
        """Create an approval once for a logical tenant-scoped request."""
        external_event: tuple[Approval, VerifiedActor, str, Mapping[str, object]] | None
        external_event = None
        with pg_transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            approval = create()
            FileApprovalStore._validate_new_approval(  # noqa: SLF001
                tenant=tenant,
                idempotency_key=idempotency_key,
                approval=approval,
            )
            self._insert_approval(con, approval)
            event_payload: Mapping[str, object] = _approval_event_payload(
                approval,
                reason="approval_requested",
            )
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_approval_event_with_connection(
                    con,
                    approval=approval,
                    actor=approval.requested_by,
                    event_type="approval_requested",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    approval,
                    approval.requested_by,
                    "approval_requested",
                    event_payload,
                )
        if external_event is not None:
            approval, actor, event_type, event_payload = external_event
            self._append_approval_event(
                approval=approval,
                actor=actor,
                event_type=event_type,
                event_payload=event_payload,
            )
        return approval

    def decide(  # noqa: PLR0913 - approval binding is explicit at the boundary
        self,
        *,
        tenant: TenantIdentity,
        approval_id: str,
        actor: VerifiedActor,
        choice: ApprovalChoice,
        exact_target: str,
        now: datetime,
        note: str | None = None,
    ) -> ApprovalDecisionResult:
        """Attempt to approve or reject one approval record."""
        external_event: tuple[Approval, VerifiedActor, str, Mapping[str, object]] | None
        external_event = None
        with pg_transaction() as con:
            approval = self._read_approval_with_connection(
                con,
                tenant=tenant,
                approval_id=approval_id,
            )
            if approval is None:
                return ApprovalDecisionResult(
                    approval=None,
                    accepted=False,
                    reason="approval_not_found",
                )
            reason = _approval_decision_guard(
                approval=approval,
                actor=actor,
                exact_target=exact_target,
                now=now,
            )
            next_status: ApprovalStatus | None
            accepted = reason == "approved"
            if reason == "approved" and choice is ApprovalChoice.APPROVE:
                next_status = ApprovalStatus.APPROVED
            elif reason == "approved" and choice is ApprovalChoice.REJECT:
                reason = "rejected"
                next_status = ApprovalStatus.REJECTED
                accepted = True
            elif reason == "expired":
                next_status = ApprovalStatus.EXPIRED
                accepted = False
            else:
                next_status = None
                accepted = False

            updated = approval
            event_type = "approval_decision_failed"
            if next_status is not None:
                validate_approval_transition(
                    expected=approval.status,
                    next_status=next_status,
                )
                updated = replace(
                    approval,
                    status=next_status,
                    updated_at=now,
                    decided_by=actor,
                    decided_at=now,
                    decision_note=note,
                )
                self._update_approval(con, updated)
                event_type = (
                    "approval_expired"
                    if next_status is ApprovalStatus.EXPIRED
                    else "approval_decided"
                )
            event_payload = _approval_event_payload(
                updated,
                choice=choice.value,
                decided_by=actor.user_id,
                reason=reason,
            )
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_approval_event_with_connection(
                    con,
                    approval=updated,
                    actor=actor,
                    event_type=event_type,
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (updated, actor, event_type, event_payload)
        if external_event is not None:
            approval_for_event, actor_for_event, event_type, event_payload = (
                external_event
            )
            self._append_approval_event(
                approval=approval_for_event,
                actor=actor_for_event,
                event_type=event_type,
                event_payload=event_payload,
            )
        return ApprovalDecisionResult(
            approval=updated,
            accepted=accepted,
            reason=reason,
        )

    def get(self, *, tenant: TenantIdentity, approval_id: str) -> Approval | None:
        """Return one tenant-scoped approval if it exists."""
        with pg_connect() as con:
            return self._read_approval_with_connection(
                con,
                tenant=tenant,
                approval_id=approval_id,
            )

    def find_pending_for_action(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
    ) -> Approval | None:
        """Return the pending approval for an action, if one exists."""
        with pg_connect() as con:
            row = con.execute(
                """
                SELECT * FROM approvals
                WHERE tenant_id = %s AND action_id = %s AND status = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (tenant.tenant_id, action_id, ApprovalStatus.PENDING.value),
            ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileApprovalStore._approval_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Approval | None", parsed)

    @staticmethod
    def _insert_approval(
        con: PostgresConnection[dict[str, object]],
        approval: Approval,
    ) -> None:
        con.execute(
            """
            INSERT INTO approvals (
                approval_id, tenant_id, tenant_json, session_id, task_id, plan_id,
                action_id, requested_by_json, required_actor_id,
                allowed_actor_ids_json, status, risk_level, exact_target, reason,
                idempotency_key, created_at, updated_at, expires_at,
                decided_by_json, decided_at, decision_note, schema_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            _approval_sql_values(approval),
        )

    @staticmethod
    def _update_approval(
        con: PostgresConnection[dict[str, object]],
        approval: Approval,
    ) -> None:
        con.execute(
            """
            UPDATE approvals
            SET status = %s,
                updated_at = %s,
                decided_by_json = %s,
                decided_at = %s,
                decision_note = %s,
                schema_version = %s
            WHERE tenant_id = %s AND approval_id = %s
            """,
            (
                approval.status.value,
                _datetime_to_json(approval.updated_at),
                _json_dumps(_actor_to_json(approval.decided_by)),
                (
                    None
                    if approval.decided_at is None
                    else _datetime_to_json(approval.decided_at)
                ),
                approval.decision_note,
                _SCHEMA_VERSION,
                approval.tenant.tenant_id,
                approval.approval_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Approval | None:
        row = con.execute(
            """
            SELECT * FROM approvals
            WHERE tenant_id = %s AND idempotency_key = %s
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileApprovalStore._approval_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Approval | None", parsed)

    def _read_approval_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        approval_id: str,
    ) -> Approval | None:
        row = con.execute(
            """
            SELECT * FROM approvals
            WHERE tenant_id = %s AND approval_id = %s
            LIMIT 1
            """,
            (tenant.tenant_id, approval_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileApprovalStore._approval_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Approval | None", parsed)

    def _append_approval_event_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        approval: Approval,
        actor: VerifiedActor,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        _append_event_postgres(
            con,
            _EventAppend(
                tenant=approval.tenant,
                session_id=approval.session_id,
                event_type=event_type,
                actor=actor,
                payload=event_payload,
            ),
        )

    def _append_approval_event(
        self,
        *,
        approval: Approval,
        actor: VerifiedActor,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        self._event_store.append(
            tenant=approval.tenant,
            session_id=approval.session_id,
            event_type=event_type,
            actor=actor,
            payload=event_payload,
        )


class PostgresWorkerLeaseStore:
    """Postgres-backed task lease store for worker coordination."""

    def acquire(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> WorkerLease | None:
        """Claim a task if no active lease exists."""
        with pg_transaction() as con:
            row = con.execute(
                """
                INSERT INTO worker_leases (
                    tenant_id, task_id, worker_id, lease_until, acquired_at,
                    heartbeat_at, attempt, schema_version
                )
                SELECT %s, %s, %s, %s, %s, %s, %s, %s
                WHERE EXISTS (
                    SELECT 1 FROM tasks
                    WHERE tenant_id = %s AND task_id = %s
                )
                ON CONFLICT (tenant_id, task_id) DO UPDATE SET
                    worker_id = excluded.worker_id,
                    lease_until = excluded.lease_until,
                    acquired_at = excluded.acquired_at,
                    heartbeat_at = excluded.heartbeat_at,
                    attempt = worker_leases.attempt + 1,
                    schema_version = excluded.schema_version
                WHERE worker_leases.lease_until <= %s
                RETURNING *
                """,
                (
                    tenant.tenant_id,
                    task_id,
                    worker_id,
                    _datetime_to_json(lease_until),
                    _datetime_to_json(now),
                    _datetime_to_json(now),
                    1,
                    _SCHEMA_VERSION,
                    tenant.tenant_id,
                    task_id,
                    _datetime_to_json(now),
                ),
            ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileWorkerLeaseStore._lease_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("WorkerLease | None", parsed)

    def heartbeat(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
        lease_until: datetime,
        now: datetime,
    ) -> WorkerLease | None:
        """Extend an active lease owned by ``worker_id``."""
        with pg_transaction() as con:
            row = con.execute(
                """
                UPDATE worker_leases
                SET lease_until = %s,
                    heartbeat_at = %s,
                    schema_version = %s
                WHERE tenant_id = %s
                  AND task_id = %s
                  AND worker_id = %s
                  AND lease_until > %s
                RETURNING *
                """,
                (
                    _datetime_to_json(lease_until),
                    _datetime_to_json(now),
                    _SCHEMA_VERSION,
                    tenant.tenant_id,
                    task_id,
                    worker_id,
                    _datetime_to_json(now),
                ),
            ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileWorkerLeaseStore._lease_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("WorkerLease | None", parsed)

    def release(
        self,
        *,
        tenant: TenantIdentity,
        task_id: str,
        worker_id: str,
    ) -> bool:
        """Release an active lease owned by ``worker_id``."""
        with pg_transaction() as con:
            cursor = con.execute(
                """
                DELETE FROM worker_leases
                WHERE tenant_id = %s AND task_id = %s AND worker_id = %s
                """,
                (tenant.tenant_id, task_id, worker_id),
            )
            return cursor.rowcount == 1

    def get(self, *, tenant: TenantIdentity, task_id: str) -> WorkerLease | None:
        """Return the current lease for one tenant-scoped task."""
        with pg_connect() as con:
            row = con.execute(
                """
                SELECT * FROM worker_leases
                WHERE tenant_id = %s AND task_id = %s
                LIMIT 1
                """,
                (tenant.tenant_id, task_id),
            ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileWorkerLeaseStore._lease_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("WorkerLease | None", parsed)


class PostgresActionStore:
    """Postgres-backed action store with transactional idempotency and events."""

    def __init__(self, *, event_store: SessionEventStore | None = None) -> None:
        """Create an action store using the configured ``DATABASE_URL``."""
        self._event_store = event_store

    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], Action],
    ) -> Action:
        """Create an action once for a logical tenant-scoped request."""
        external_event: tuple[Action, str, Mapping[str, object]] | None = None
        with pg_transaction() as con:
            existing = self._get_by_idempotency_with_connection(
                con,
                tenant=tenant,
                idempotency_key=idempotency_key,
            )
            if existing is not None:
                return existing
            action = create()
            FileActionStore._validate_new_action(  # noqa: SLF001
                tenant=tenant,
                idempotency_key=idempotency_key,
                action=action,
            )
            self._insert_action(con, action)
            event_payload: Mapping[str, object] = {
                "status": action.status.value,
                "kind": action.kind.value,
            }
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_action_event_with_connection(
                    con,
                    action=action,
                    event_type="action_created",
                    event_payload=event_payload,
                )
            elif self._event_store is not None:
                external_event = (action, "action_created", event_payload)
        if external_event is not None:
            event_action, event_type, event_payload = external_event
            self._append_action_event(
                action=event_action,
                event_type=event_type,
                event_payload=event_payload,
            )
        return action

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
        transition: ActionTransition,
    ) -> Action | None:
        """Move an action only if it is still in the expected state."""
        validate_action_transition(
            expected=transition.expected,
            next_status=transition.next_status,
        )
        external_event: tuple[Action, str, Mapping[str, object]] | None = None
        with pg_transaction() as con:
            action = self._read_action_with_connection(
                con,
                tenant=tenant,
                action_id=action_id,
            )
            if action is None or action.status is not transition.expected:
                return None
            updated = replace(
                action,
                status=transition.next_status,
                result=(
                    transition.result
                    if transition.result is not None
                    else action.result
                ),
                failure=(
                    transition.failure
                    if transition.failure is not None
                    else action.failure
                ),
                updated_at=datetime.now(UTC),
            )
            self._update_action(con, updated)
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_action_event_with_connection(
                    con,
                    action=updated,
                    event_type=transition.event_type,
                    event_payload=transition.event_payload,
                )
            elif self._event_store is not None:
                external_event = (
                    updated,
                    transition.event_type,
                    transition.event_payload,
                )
        if external_event is not None:
            event_action, event_type, event_payload = external_event
            self._append_action_event(
                action=event_action,
                event_type=event_type,
                event_payload=event_payload,
            )
        return updated

    def get(self, *, tenant: TenantIdentity, action_id: str) -> Action | None:
        """Return one tenant-scoped action if it exists."""
        with pg_connect() as con:
            return self._read_action_with_connection(
                con,
                tenant=tenant,
                action_id=action_id,
            )

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Action]:
        """Return all known actions for one tenant-scoped session."""
        with pg_connect() as con:
            rows = con.execute(
                """
                SELECT * FROM actions
                WHERE tenant_id = %s AND session_id = %s
                ORDER BY created_at ASC
                """,
                (tenant.tenant_id, session_id),
            ).fetchall()
        actions = [
            action
            for row in rows
            if (
                action := _safe_row(
                    FileActionStore._action_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("Action", action) for action in actions)

    def find_latest_awaiting_confirmation(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        kind: ActionKind,
    ) -> Action | None:
        """Return the newest action waiting for confirmation in one session."""
        with pg_connect() as con:
            row = con.execute(
                """
                SELECT * FROM actions
                WHERE tenant_id = %s
                  AND session_id = %s
                  AND kind = %s
                  AND status = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (
                    tenant.tenant_id,
                    session_id,
                    kind.value,
                    ActionStatus.AWAITING_CONFIRMATION.value,
                ),
            ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileActionStore._action_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Action | None", parsed)

    @staticmethod
    def _insert_action(
        con: PostgresConnection[dict[str, object]],
        action: Action,
    ) -> None:
        con.execute(
            """
            INSERT INTO actions (
                action_id, tenant_id, tenant_json, session_id, actor_json,
                kind, target_json, status, idempotency_key, input_json,
                result_json, failure_json, policy_decision_json, created_at,
                updated_at, expires_at, schema_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            _action_sql_values(action),
        )

    @staticmethod
    def _update_action(
        con: PostgresConnection[dict[str, object]],
        action: Action,
    ) -> None:
        con.execute(
            """
            UPDATE actions
            SET status = %s,
                result_json = %s,
                failure_json = %s,
                policy_decision_json = %s,
                updated_at = %s,
                expires_at = %s,
                schema_version = %s
            WHERE tenant_id = %s AND action_id = %s
            """,
            (
                action.status.value,
                _json_dumps(_action_result_to_json(action.result)),
                _json_dumps(_action_failure_to_json(action.failure)),
                _json_dumps(_policy_decision_to_json(action.policy_decision)),
                _datetime_to_json(action.updated_at),
                (
                    None
                    if action.expires_at is None
                    else _datetime_to_json(action.expires_at)
                ),
                _SCHEMA_VERSION,
                action.tenant.tenant_id,
                action.action_id,
            ),
        )

    def _get_by_idempotency_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
    ) -> Action | None:
        row = con.execute(
            """
            SELECT * FROM actions
            WHERE tenant_id = %s AND idempotency_key = %s
            LIMIT 1
            """,
            (tenant.tenant_id, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileActionStore._action_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Action | None", parsed)

    def _read_action_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        action_id: str,
    ) -> Action | None:
        row = con.execute(
            """
            SELECT * FROM actions
            WHERE tenant_id = %s AND action_id = %s
            LIMIT 1
            """,
            (tenant.tenant_id, action_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileActionStore._action_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Action | None", parsed)

    def _append_action_event_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        action: Action,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        payload = {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            "policy_decision": (
                None
                if action.policy_decision is None
                else action.policy_decision.decision.value
            ),
            **dict(event_payload),
        }
        _append_event_postgres(
            con,
            _EventAppend(
                tenant=action.tenant,
                session_id=action.session_id,
                event_type=event_type,
                actor=action.actor,
                payload=payload,
            ),
        )

    def _append_action_event(
        self,
        *,
        action: Action,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> None:
        if self._event_store is None:
            return
        payload = {
            "action_id": action.action_id,
            "kind": action.kind.value,
            "status": action.status.value,
            "policy_decision": (
                None
                if action.policy_decision is None
                else action.policy_decision.decision.value
            ),
            **dict(event_payload),
        }
        self._event_store.append(
            tenant=action.tenant,
            session_id=action.session_id,
            event_type=event_type,
            actor=action.actor,
            payload=payload,
        )


class PostgresArtifactStore:
    """Postgres-backed immutable artifact store for Render deployments."""

    def __init__(self, *, event_store: SessionEventStore | None = None) -> None:
        """Create an artifact store using the configured ``DATABASE_URL``."""
        self._event_store = event_store

    def create(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None = None,
    ) -> Artifact:
        """Persist one immutable artifact."""
        artifact = ensure_artifact_digest(artifact)
        external_event: tuple[Artifact, VerifiedActor | None] | None = None
        with pg_transaction() as con:
            existing = self._read_artifact_with_connection(
                con,
                tenant=artifact.tenant,
                artifact_id=artifact.artifact_id,
            )
            if existing is not None:
                return existing
            self._insert_artifact(con, artifact)
            if isinstance(self._event_store, PostgresSessionEventStore):
                self._append_artifact_event_with_connection(
                    con,
                    artifact=artifact,
                    actor=actor,
                )
            elif self._event_store is not None:
                external_event = (artifact, actor)
        if external_event is not None:
            artifact, actor = external_event
            self._append_artifact_event(artifact=artifact, actor=actor)
        return artifact

    def get(
        self,
        *,
        tenant: TenantIdentity,
        artifact_id: str,
    ) -> Artifact | None:
        """Return one artifact by ID, or ``None`` if not found."""
        with pg_connect() as con:
            return self._read_artifact_with_connection(
                con,
                tenant=tenant,
                artifact_id=artifact_id,
            )

    def list_for_session(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
    ) -> Sequence[Artifact]:
        """Return artifacts for one tenant-scoped session."""
        with pg_connect() as con:
            rows = con.execute(
                """
                SELECT * FROM artifacts
                WHERE tenant_id = %s AND session_id = %s
                ORDER BY created_at ASC
                """,
                (tenant.tenant_id, session_id),
            ).fetchall()
        artifacts = [
            artifact
            for row in rows
            if (
                artifact := _safe_row(
                    FileArtifactStore._artifact_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("Artifact", artifact) for artifact in artifacts)

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        kind: ArtifactKind | None = None,
        limit: int = 100,
    ) -> Sequence[Artifact]:
        """Return recent artifacts for one tenant, newest first."""
        with pg_connect() as con:
            if kind is None:
                rows = con.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (tenant.tenant_id, limit),
                ).fetchall()
            else:
                rows = con.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE tenant_id = %s AND kind = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (tenant.tenant_id, kind, limit),
                ).fetchall()
        artifacts = [
            artifact
            for row in rows
            if (
                artifact := _safe_row(
                    FileArtifactStore._artifact_from_row,  # noqa: SLF001
                    cast("sqlite3.Row", row),
                )
            )
            is not None
        ]
        return tuple(cast("Artifact", artifact) for artifact in artifacts)

    @staticmethod
    def _insert_artifact(
        con: PostgresConnection[dict[str, object]],
        artifact: Artifact,
    ) -> None:
        con.execute(
            """
            INSERT INTO artifacts (
                artifact_id, tenant_id, tenant_json, session_id, action_id,
                kind, uri, payload_json, payload_digest, created_at, schema_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                artifact.artifact_id,
                artifact.tenant.tenant_id,
                _json_dumps(_tenant_to_json(artifact.tenant)),
                artifact.session_id,
                artifact.action_id,
                artifact.kind,
                artifact.uri,
                _json_dumps(_artifact_payload_to_json(artifact.payload)),
                artifact.payload_digest or artifact_payload_digest(artifact.payload),
                _datetime_to_json(artifact.created_at),
                _SCHEMA_VERSION,
            ),
        )

    def _read_artifact_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        tenant: TenantIdentity,
        artifact_id: str,
    ) -> Artifact | None:
        row = con.execute(
            """
            SELECT * FROM artifacts
            WHERE tenant_id = %s AND artifact_id = %s
            LIMIT 1
            """,
            (tenant.tenant_id, artifact_id),
        ).fetchone()
        if row is None:
            return None
        parsed = _safe_row(
            FileArtifactStore._artifact_from_row,  # noqa: SLF001
            cast("sqlite3.Row", row),
        )
        return cast("Artifact | None", parsed)

    def _append_artifact_event_with_connection(
        self,
        con: PostgresConnection[dict[str, object]],
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        _append_event_postgres(
            con,
            _EventAppend(
                tenant=artifact.tenant,
                session_id=artifact.session_id,
                event_type="artifact_created",
                actor=actor,
                payload={
                    "artifact_id": artifact.artifact_id,
                    "action_id": artifact.action_id,
                    "kind": artifact.kind,
                },
            ),
        )

    def _append_artifact_event(
        self,
        *,
        artifact: Artifact,
        actor: VerifiedActor | None,
    ) -> None:
        if self._event_store is None:
            return
        self._event_store.append(
            tenant=artifact.tenant,
            session_id=artifact.session_id,
            event_type="artifact_created",
            actor=actor,
            payload={
                "artifact_id": artifact.artifact_id,
                "action_id": artifact.action_id,
                "kind": artifact.kind,
            },
        )
