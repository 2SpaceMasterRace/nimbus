"""Transport-neutral storage-agent operation layer for Nimbus.

Slack and CLI are thin clients. Every storage operation routes through
``StorageAgent.execute()``, which enforces mode-level access control before
touching storage or emitting events.

Usage::

    agent = StorageAgent(storage=client, artifact_store=store, event_store=events)
    request = StorageAgentRequest(
        session_id="s_123",
        operation="list",
        mode=OperationMode.READ_ONLY,
        actor=actor,
        container="my-bucket",
    )
    response = agent.execute(request)
"""

from __future__ import annotations

import io
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from nimbus_runtime.domain import (
    Artifact,
    ArtifactKind,
    ArtifactPayload,
    ManifestReport,
    OperationMode,
    VerifiedActor,
)
from nimbus_runtime.drift_verifier import verify_manifest
from nimbus_runtime.search import SearchActorScope, SearchQuery

if TYPE_CHECKING:
    from cloud_storage_api.client import CloudStorageClient

    from nimbus_runtime.search import SearchIndexStore
    from nimbus_runtime.stores import ArtifactStore, SessionEventStore


# ---------------------------------------------------------------------------
# Operation vocabulary
# ---------------------------------------------------------------------------

StorageOperation = Literal[
    "scan",
    "list",
    "search",
    "hash",
    "diff_manifest",
    "propose_plan",
    "stage_upload",
    "promote_upload",
    "prepare_delete",
    "delete",
    "restore",
    "verify",
    "write_artifact",
]

# Permissions table: maps each mode to the set of operations it allows.
_READ_OPS: frozenset[str] = frozenset(
    {"scan", "list", "search", "hash", "diff_manifest"}
)
_PLAN_OPS: frozenset[str] = _READ_OPS | frozenset({"propose_plan", "stage_upload"})
_APPLY_OPS: frozenset[str] = _PLAN_OPS | frozenset(
    {
        "promote_upload",
        "prepare_delete",
        "delete",
        "restore",
        "verify",
        "write_artifact",
    }
)

_MODE_ALLOWED: dict[OperationMode, frozenset[str]] = {
    OperationMode.READ_ONLY: _READ_OPS,
    OperationMode.PLAN: _PLAN_OPS,
    OperationMode.APPLY: _APPLY_OPS,
    OperationMode.WATCH: frozenset({"scan", "list", "search"}),
    OperationMode.REVIEW: frozenset({"scan", "list", "search", "propose_plan"}),
    OperationMode.POLICY_ADMIN: _APPLY_OPS,
}


# ---------------------------------------------------------------------------
# Request / response value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StorageAgentRequest:
    """One transport-neutral operation request bound to a session and actor."""

    session_id: str
    operation: StorageOperation
    mode: OperationMode
    actor: VerifiedActor
    container: str
    params: dict[str, object] = field(default_factory=dict)
    action_id: str | None = None
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class StorageAgentResponse:
    """Outcome of a completed ``StorageAgentRequest``."""

    session_id: str
    operation: StorageOperation
    mode: OperationMode
    actor_id: str
    success: bool
    artifact_id: str | None
    payload: object
    executed_at: datetime
    error: str | None = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class StorageAgentError(Exception):
    """Raised when ``StorageAgent`` rejects or cannot complete a request."""


class OperationNotPermittedError(StorageAgentError):
    """Raised when the requested operation is not allowed in the current mode."""

    def __init__(self, operation: str, mode: OperationMode) -> None:
        """Initialize with the rejected operation and the active mode."""
        super().__init__(f"Operation {operation!r} is not permitted in {mode!r} mode.")
        self.operation = operation
        self.mode = mode


# ---------------------------------------------------------------------------
# StorageAgent
# ---------------------------------------------------------------------------

_Handler = Callable[
    ["StorageAgent", StorageAgentRequest, datetime],
    tuple[object, str | None],
]


class StorageAgent:
    """Transport-neutral storage-agent operation executor.

    Enforces mode-level access control, then delegates to a handler that
    produces a ``(payload, artifact_id)`` pair. Slack and CLI are thin
    clients over this class — both call ``execute()`` with the same
    ``StorageAgentRequest`` shape.
    """

    def __init__(
        self,
        *,
        storage: CloudStorageClient,
        artifact_store: ArtifactStore,
        event_store: SessionEventStore,
        search_store: SearchIndexStore | None = None,
    ) -> None:
        """Inject the storage client and durable stores."""
        self._storage = storage
        self._artifact_store = artifact_store
        self._event_store = event_store
        self._search_store = search_store

    def execute(self, request: StorageAgentRequest) -> StorageAgentResponse:
        """Execute *request* after enforcing mode-level access control.

        Args:
            request: The operation to perform, including mode and actor.

        Returns:
            A ``StorageAgentResponse`` describing the outcome.

        Raises:
            OperationNotPermittedError: When the operation is not allowed in
                the caller's mode.
            StorageAgentError: For other agent-level failures.

        """
        _check_mode(request.operation, request.mode)
        now = request.now if request.now is not None else datetime.now(UTC)
        handler = _HANDLERS.get(request.operation)
        if handler is None:
            msg = f"No handler registered for operation {request.operation!r}."
            raise StorageAgentError(msg)
        payload, artifact_id = handler(self, request, now)
        return StorageAgentResponse(
            session_id=request.session_id,
            operation=request.operation,
            mode=request.mode,
            actor_id=request.actor.user_id,
            success=True,
            artifact_id=artifact_id,
            payload=payload,
            executed_at=now,
        )

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    def _persist_artifact(
        self,
        *,
        request: StorageAgentRequest,
        kind: str,
        payload: object,
        now: datetime,
    ) -> Artifact:
        return self._artifact_store.create(
            artifact=Artifact(
                artifact_id=str(uuid.uuid4()),
                tenant=request.actor.tenant,
                session_id=request.session_id,
                action_id=request.action_id,
                kind=cast("ArtifactKind", kind),
                uri=None,
                payload=cast("ArtifactPayload", payload),
                created_at=now,
            ),
            actor=request.actor,
        )

    # ------------------------------------------------------------------
    # Operation handlers
    # ------------------------------------------------------------------

    def _op_scan(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        prefix = str(request.params.get("prefix", ""))
        objects = self._storage.list_files(request.container, prefix)
        return {
            "container": request.container,
            "prefix": prefix,
            "count": len(objects),
            "objects": [
                {"key": o.object_name, "size_bytes": o.size_bytes} for o in objects
            ],
        }, None

    def _op_list(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        prefix = str(request.params.get("prefix", ""))
        objects = self._storage.list_files(request.container, prefix)
        return {"keys": [o.object_name for o in objects], "count": len(objects)}, None

    def _op_search(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        query_text = str(request.params.get("query", ""))
        if self._search_store is None:
            return {"query": query_text, "results": [], "count": 0}, None
        visible_channels = request.params.get("visible_channel_ids", [])
        workspace_wide = bool(request.params.get("workspace_wide", False))
        scope = SearchActorScope(
            actor=request.actor,
            visible_channel_ids=frozenset(
                str(c)
                for c in (
                    visible_channels if isinstance(visible_channels, list) else []
                )
            ),
            workspace_wide=workspace_wide,
        )
        sq = SearchQuery(text=query_text)
        results = self._search_store.search(scope=scope, query=sq)
        return {
            "query": query_text,
            "count": len(results),
            "results": [
                {
                    "document_id": r.document.document_id,
                    "source_uri": r.document.source_uri,
                    "title": r.document.title,
                    "score": r.score,
                    "citations": list(r.citations),
                }
                for r in results
            ],
        }, None

    def _op_hash(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        key = str(request.params.get("object_key", ""))
        info = self._storage.get_file_info(request.container, key)
        sha256 = info.metadata.get("sha256") if info.metadata else None
        return {
            "object_key": key,
            "sha256": sha256,
            "size_bytes": info.size_bytes,
        }, None

    def _op_diff_manifest(
        self, request: StorageAgentRequest, now: datetime
    ) -> tuple[object, str | None]:
        manifest_id = str(request.params.get("manifest_artifact_id", ""))
        artifact = self._artifact_store.get(
            tenant=request.actor.tenant, artifact_id=manifest_id
        )
        if artifact is None or not isinstance(artifact.payload, ManifestReport):
            msg = f"Manifest artifact {manifest_id!r} not found or has wrong type."
            raise StorageAgentError(msg)
        drift = verify_manifest(
            manifest_report=artifact.payload,
            manifest_artifact_id=manifest_id,
            storage=self._storage,
            artifact_store=self._artifact_store,
            event_store=self._event_store,
            actor=request.actor,
            session_id=request.session_id,
            action_id=request.action_id,
            now=now,
        )
        return {
            "manifest_artifact_id": manifest_id,
            "has_drift": drift.has_drift,
            "match_count": drift.match_count,
            "mismatch_count": drift.mismatch_count,
            "missing_count": drift.missing_count,
            "unknown_count": drift.unknown_count,
        }, None

    def _op_propose_plan(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        description = str(request.params.get("description", ""))
        return {
            "description": description,
            "mode": str(request.mode),
            "status": "plan_proposed",
        }, None

    def _op_stage_upload(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        object_key = str(request.params.get("object_key", ""))
        nonce = uuid.uuid4().hex
        staging_key = f"_staging/{request.actor.user_id}/{nonce}/{object_key}"
        return {
            "staging_key": staging_key,
            "object_key": object_key,
            "container": request.container,
            "status": "staged",
        }, None

    def _op_promote_upload(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        staging_key = str(request.params.get("staging_key", ""))
        final_key = str(request.params.get("final_key", ""))
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            self._storage.download_file(request.container, staging_key, tmp.name)
            with open(tmp.name, "rb") as fh:  # noqa: PTH123
                data = fh.read()
        self._storage.upload_obj(request.container, io.BytesIO(data), final_key)
        self._storage.delete_file(request.container, staging_key)
        return {
            "staging_key": staging_key,
            "final_key": final_key,
            "status": "promoted",
        }, None

    def _op_prepare_delete(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        key = str(request.params.get("object_key", ""))
        info = self._storage.get_file_info(request.container, key)
        return {
            "object_key": key,
            "size_bytes": info.size_bytes,
            "container": request.container,
            "status": "prepared",
        }, None

    def _op_delete(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        key = str(request.params.get("object_key", ""))
        self._storage.delete_file(request.container, key)
        return {"object_key": key, "status": "deleted"}, None

    def _op_restore(
        self, request: StorageAgentRequest, _now: datetime
    ) -> tuple[object, str | None]:
        key = str(request.params.get("object_key", ""))
        version_id = request.params.get("version_id", "")
        return {
            "object_key": key,
            "version_id": version_id,
            "message": "Delegate to NimbusRuntime restore flow for versioned recovery.",
        }, None

    def _op_verify(
        self, request: StorageAgentRequest, now: datetime
    ) -> tuple[object, str | None]:
        manifest_id = str(request.params.get("manifest_artifact_id", ""))
        artifact = self._artifact_store.get(
            tenant=request.actor.tenant, artifact_id=manifest_id
        )
        if artifact is None or not isinstance(artifact.payload, ManifestReport):
            msg = f"Manifest artifact {manifest_id!r} not found or has wrong type."
            raise StorageAgentError(msg)
        drift = verify_manifest(
            manifest_report=artifact.payload,
            manifest_artifact_id=manifest_id,
            storage=self._storage,
            artifact_store=self._artifact_store,
            event_store=self._event_store,
            actor=request.actor,
            session_id=request.session_id,
            action_id=request.action_id,
            now=now,
            strict=bool(request.params.get("strict", False)),
        )
        return {
            "manifest_artifact_id": manifest_id,
            "has_drift": drift.has_drift,
            "match_count": drift.match_count,
            "mismatch_count": drift.mismatch_count,
            "missing_count": drift.missing_count,
            "unknown_count": drift.unknown_count,
        }, None

    def _op_write_artifact(
        self, request: StorageAgentRequest, now: datetime
    ) -> tuple[object, str | None]:
        kind = str(request.params.get("kind", "raw"))
        payload = request.params.get("payload")
        artifact = self._persist_artifact(
            request=request,
            kind=kind,
            payload=payload,
            now=now,
        )
        return {"kind": kind, "artifact_id": artifact.artifact_id}, artifact.artifact_id


# ---------------------------------------------------------------------------
# Module-level helpers (used inside execute())
# ---------------------------------------------------------------------------


def _check_mode(operation: str, mode: OperationMode) -> None:
    allowed = _MODE_ALLOWED.get(mode, frozenset())
    if operation not in allowed:
        raise OperationNotPermittedError(operation, mode)


# Handler dispatch table — built after class definition so methods resolve.
# Accessing private methods via the class (not an instance) still triggers
# SLF001; the inline suppression on each entry is intentional.
_HANDLERS: dict[str, _Handler] = {
    "scan": StorageAgent._op_scan,  # noqa: SLF001
    "list": StorageAgent._op_list,  # noqa: SLF001
    "search": StorageAgent._op_search,  # noqa: SLF001
    "hash": StorageAgent._op_hash,  # noqa: SLF001
    "diff_manifest": StorageAgent._op_diff_manifest,  # noqa: SLF001
    "propose_plan": StorageAgent._op_propose_plan,  # noqa: SLF001
    "stage_upload": StorageAgent._op_stage_upload,  # noqa: SLF001
    "promote_upload": StorageAgent._op_promote_upload,  # noqa: SLF001
    "prepare_delete": StorageAgent._op_prepare_delete,  # noqa: SLF001
    "delete": StorageAgent._op_delete,  # noqa: SLF001
    "restore": StorageAgent._op_restore,  # noqa: SLF001
    "verify": StorageAgent._op_verify,  # noqa: SLF001
    "write_artifact": StorageAgent._op_write_artifact,  # noqa: SLF001
}
