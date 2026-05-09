"""Storage change stacks and restack conflict detection.

The stack layer is Nimbus's storage-version-control kernel.  It keeps planned
storage mutations reviewable before execution, records immutable revisions, and
fails closed when the live target no longer matches the digest that was
approved.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

from nimbus_runtime.domain import (
    Artifact,
    ConflictArtifact,
    GenerationManifest,
    Plan,
    PlanRiskLevel,
    ProofReceipt,
    RuntimeOperation,
    StorageChange,
    StorageChangeRevision,
    StorageChangeStack,
    StorageChangeStackEntry,
    StorageChangeStatus,
    StorageMutationReport,
    TenantIdentity,
    VerifiedActor,
)
from nimbus_runtime.proof import (
    artifact_payload_digest,
    deterministic_receipt_id,
    digest_value,
)
from nimbus_runtime.stores import (
    _SCHEMA_VERSION,
    _actor_from_json,
    _actor_to_json,
    _datetime_from_json,
    _datetime_to_json,
    _json_dumps,
    _json_loads_object,
    _required_int,
    _required_str,
    _row_int,
    _row_optional_str,
    _row_str,
    _SQLiteStore,
    _tenant_from_json,
    _tenant_to_json,
)

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping, Sequence

    from nimbus_runtime.stores import ArtifactStore


class StackStorageClient(Protocol):
    """Minimal storage executor needed for stack apply.

    This deliberately matches the existing ``CloudStorageClient`` subset while
    avoiding an SDK dependency in the runtime module.
    """

    def get_file_info(self, container: str, object_name: str) -> object:
        """Return provider object metadata for verification."""

    def delete_file(self, container: str, object_name: str) -> object:
        """Delete one already-verified object."""


@dataclass(frozen=True, slots=True)
class StorageStackState:
    """Full durable stack projection used by CLI, tests, and future Slack cards."""

    stack: StorageChangeStack
    entries: tuple[StorageChangeStackEntry, ...]
    changes: tuple[StorageChange, ...]
    revisions: tuple[StorageChangeRevision, ...]
    operations: tuple[RuntimeOperation, ...]


@dataclass(frozen=True, slots=True)
class StackApplyResult:
    """Outcome of attempting to apply one stack."""

    stack: StorageChangeStack
    applied_count: int
    blocked_count: int
    failed_count: int
    status: str
    next_step: str


class FileStorageStackStore(_SQLiteStore):
    """SQLite-backed store for ordered storage change stacks."""

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        _SQLiteStore._ensure_schema(con)  # noqa: SLF001 - same-package extension.
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS storage_stacks (
                stack_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                plan_id TEXT,
                title TEXT NOT NULL,
                status TEXT NOT NULL,
                actor_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                UNIQUE (tenant_id, plan_id)
            );
            CREATE INDEX IF NOT EXISTS storage_stacks_by_tenant
                ON storage_stacks (tenant_id, updated_at);
            CREATE TABLE IF NOT EXISTS storage_changes (
                change_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                stack_id TEXT NOT NULL,
                current_revision_id TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS storage_changes_by_stack
                ON storage_changes (tenant_id, stack_id, created_at);
            CREATE TABLE IF NOT EXISTS storage_change_revisions (
                revision_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                change_id TEXT NOT NULL,
                stack_id TEXT NOT NULL,
                base_generation_id TEXT,
                target_digest TEXT,
                risk_level TEXT NOT NULL,
                operation TEXT NOT NULL,
                target_json TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS storage_revisions_by_change
                ON storage_change_revisions (tenant_id, change_id, created_at);
            CREATE TABLE IF NOT EXISTS storage_stack_entries (
                tenant_id TEXT NOT NULL,
                stack_id TEXT NOT NULL,
                change_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                schema_version INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, stack_id, change_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS storage_stack_entries_position
                ON storage_stack_entries (tenant_id, stack_id, position);
            CREATE TABLE IF NOT EXISTS runtime_operations (
                operation_id TEXT NOT NULL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                stack_id TEXT NOT NULL,
                change_id TEXT,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                summary TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                schema_version INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS runtime_operations_by_stack
                ON runtime_operations (tenant_id, stack_id, created_at);
            """
        )

    def create_from_plan(
        self,
        *,
        plan: Plan,
        actor: VerifiedActor,
        now: datetime | None = None,
    ) -> StorageStackState:
        """Create or return a deterministic stack for one approved/proposed plan."""
        timestamp = now or datetime.now(UTC)
        stack_id = stack_id_for_plan(plan)
        existing = self.get_state(tenant=plan.tenant, stack_id=stack_id)
        if existing is not None:
            return existing
        stack_metadata = _stack_metadata_from_plan(plan)
        stack = StorageChangeStack(
            stack_id=stack_id,
            tenant=plan.tenant,
            plan_id=plan.plan_id,
            title=plan.title,
            status="proposed",
            created_by=actor,
            created_at=timestamp,
            updated_at=timestamp,
            metadata=stack_metadata,
        )
        revisions = _initial_revisions_for_plan(
            plan=plan,
            stack_id=stack_id,
            now=timestamp,
        )
        changes = tuple(
            StorageChange(
                change_id=revision.change_id,
                tenant=plan.tenant,
                stack_id=stack_id,
                current_revision_id=revision.revision_id,
                status="proposed",
                title=_change_title(revision),
                created_at=timestamp,
                updated_at=timestamp,
            )
            for revision in revisions
        )
        entries = tuple(
            StorageChangeStackEntry(
                stack_id=stack_id,
                change_id=change.change_id,
                position=index,
            )
            for index, change in enumerate(changes, start=1)
        )
        operation = _operation(
            tenant=plan.tenant,
            stack_id=stack_id,
            change_id=None,
            kind="stack_proposed",
            status="proposed",
            summary=(
                f"Proposed {len(changes)} storage change(s) from plan {plan.plan_id}."
            ),
            metadata={"plan_id": plan.plan_id},
            now=timestamp,
        )
        with self._transaction() as con:
            existing_row = self._stack_row(con, tenant=plan.tenant, stack_id=stack_id)
            if existing_row is not None:
                continue_state = self._state_from_connection(
                    con,
                    tenant=plan.tenant,
                    stack_id=stack_id,
                )
                if continue_state is None:
                    msg = f"stack {stack_id!r} disappeared during creation"
                    raise RuntimeError(msg)
                return continue_state
            self._insert_stack(con, stack)
            for change in changes:
                self._insert_change(con, change)
            for revision in revisions:
                self._insert_revision(con, tenant=plan.tenant, revision=revision)
            for entry in entries:
                self._insert_entry(con, tenant=plan.tenant, entry=entry)
            self._insert_operation(con, operation)
        created = self.get_state(tenant=plan.tenant, stack_id=stack_id)
        if created is None:
            msg = f"stack {stack_id!r} was not readable after creation"
            raise RuntimeError(msg)
        return created

    def get_state(
        self,
        *,
        tenant: TenantIdentity,
        stack_id: str,
    ) -> StorageStackState | None:
        """Return one stack with its ordered changes, revisions, and log."""
        with self._lock:
            con = self._connect()
            try:
                return self._state_from_connection(
                    con,
                    tenant=tenant,
                    stack_id=stack_id,
                )
            finally:
                con.close()

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        limit: int = 100,
    ) -> tuple[StorageChangeStack, ...]:
        """Return recent stacks for one tenant."""
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT * FROM storage_stacks
                    WHERE tenant_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (tenant.tenant_id, limit),
                ).fetchall()
            finally:
                con.close()
        return tuple(self._stack_from_row(row) for row in rows)

    def approve(
        self,
        *,
        tenant: TenantIdentity,
        stack_id: str,
        actor: VerifiedActor,
        now: datetime | None = None,
    ) -> StorageStackState | None:
        """Approve a proposed stack and every proposed change in it."""
        timestamp = now or datetime.now(UTC)
        with self._transaction() as con:
            stack = self._stack_from_row_or_none(
                self._stack_row(con, tenant=tenant, stack_id=stack_id)
            )
            if stack is None or stack.status != "proposed":
                return None
            updated = replace(stack, status="approved", updated_at=timestamp)
            self._update_stack(con, updated)
            changes = self._changes_from_connection(
                con,
                tenant=tenant,
                stack_id=stack_id,
            )
            for change in changes:
                if change.status == "proposed":
                    self._update_change(
                        con,
                        replace(
                            change,
                            status="approved",
                            updated_at=timestamp,
                        ),
                    )
            self._insert_operation(
                con,
                _operation(
                    tenant=tenant,
                    stack_id=stack_id,
                    change_id=None,
                    kind="stack_approved",
                    status="approved",
                    summary=f"Stack {stack_id} approved by {actor.user_id}.",
                    metadata={"actor_id": actor.user_id},
                    now=timestamp,
                ),
            )
        return self.get_state(tenant=tenant, stack_id=stack_id)

    def abandon(
        self,
        *,
        tenant: TenantIdentity,
        stack_id: str,
        actor: VerifiedActor,
        now: datetime | None = None,
    ) -> StorageStackState | None:
        """Abandon a stack that has not been applied."""
        timestamp = now or datetime.now(UTC)
        with self._transaction() as con:
            stack = self._stack_from_row_or_none(
                self._stack_row(con, tenant=tenant, stack_id=stack_id)
            )
            if stack is None or stack.status == "applied":
                return None
            updated = replace(stack, status="abandoned", updated_at=timestamp)
            self._update_stack(con, updated)
            for change in self._changes_from_connection(
                con,
                tenant=tenant,
                stack_id=stack_id,
            ):
                if change.status != "applied":
                    self._update_change(
                        con,
                        replace(
                            change,
                            status="abandoned",
                            updated_at=timestamp,
                        ),
                    )
            self._insert_operation(
                con,
                _operation(
                    tenant=tenant,
                    stack_id=stack_id,
                    change_id=None,
                    kind="stack_abandoned",
                    status="abandoned",
                    summary=f"Stack {stack_id} abandoned by {actor.user_id}.",
                    metadata={"actor_id": actor.user_id},
                    now=timestamp,
                ),
            )
        return self.get_state(tenant=tenant, stack_id=stack_id)

    def restack(  # noqa: PLR0913 - explicit durable bindings aid reviewability.
        self,
        *,
        tenant: TenantIdentity,
        stack_id: str,
        manifest: GenerationManifest,
        artifact_store: ArtifactStore,
        actor: VerifiedActor,
        now: datetime | None = None,
    ) -> StorageStackState | None:
        """Compare approved targets with a newer manifest and record conflicts."""
        timestamp = now or datetime.now(UTC)
        live_digests = {
            pointer.object_name: pointer.content_sha256 for pointer in manifest.objects
        }
        state = self.get_state(tenant=tenant, stack_id=stack_id)
        if state is None:
            return None
        if state.stack.status in {"applied", "abandoned"}:
            return state
        latest_by_change = _latest_revision_by_change(state.revisions)
        prepared: list[
            tuple[
                StorageChange,
                StorageChangeRevision,
                str,
                str | None,
                ConflictArtifact | None,
                StorageChangeRevision | None,
            ]
        ] = []
        for entry in state.entries:
            change = _change_by_id(state.changes, entry.change_id)
            revision = latest_by_change[entry.change_id]
            object_name = _target_object_name(revision.target)
            expected = revision.target_digest
            observed = live_digests.get(object_name)
            if observed != expected:
                conflict = _conflict_artifact(
                    tenant=tenant,
                    stack_id=stack_id,
                    change_id=change.change_id,
                    object_name=object_name,
                    expected_digest=expected,
                    observed_digest=observed,
                    reason=(
                        "target digest changed after the storage change was planned"
                    ),
                    now=timestamp,
                )
                artifact_store.create(
                    artifact=Artifact(
                        artifact_id=conflict.conflict_id,
                        tenant=tenant,
                        session_id=_stack_session_id(state.stack),
                        action_id=None,
                        kind="conflict_artifact",
                        uri=None,
                        payload=conflict,
                        created_at=timestamp,
                    ),
                    actor=actor,
                )
                prepared.append(
                    (change, revision, object_name, observed, conflict, None)
                )
                continue
            prepared.append(
                (
                    change,
                    revision,
                    object_name,
                    observed,
                    None,
                    _next_revision(
                        revision=revision,
                        reason="restack_after_drift_check",
                        now=timestamp,
                    ),
                )
            )
        conflict_created = any(item[4] is not None for item in prepared)
        with self._transaction() as con:
            for (
                change,
                revision,
                object_name,
                observed,
                conflict_item,
                new_revision,
            ) in prepared:
                expected = revision.target_digest
                if conflict_item is not None:
                    self._update_change(
                        con,
                        replace(
                            change,
                            status="conflicted",
                            updated_at=timestamp,
                        ),
                    )
                    self._insert_operation(
                        con,
                        _operation(
                            tenant=tenant,
                            stack_id=stack_id,
                            change_id=change.change_id,
                            kind="restack_conflict",
                            status="conflicted",
                            summary=f"Conflict on {object_name}.",
                            metadata={
                                "conflict_artifact_id": conflict_item.conflict_id,
                                "expected_digest": expected,
                                "observed_digest": observed,
                            },
                            now=timestamp,
                        ),
                    )
                    continue
                if new_revision is None:
                    msg = "prepared restack item has no revision or conflict"
                    raise RuntimeError(msg)
                self._insert_revision(con, tenant=tenant, revision=new_revision)
                self._update_change(
                    con,
                    replace(
                        change,
                        current_revision_id=new_revision.revision_id,
                        updated_at=timestamp,
                    ),
                )
                self._insert_operation(
                    con,
                    _operation(
                        tenant=tenant,
                        stack_id=stack_id,
                        change_id=change.change_id,
                        kind="restack_rebased",
                        status="proposed",
                        summary=(
                            f"Rebased {object_name} onto manifest "
                            f"{manifest.generation_id}."
                        ),
                        metadata={"generation_id": manifest.generation_id},
                        now=timestamp,
                    ),
                )
            stack = self._stack_from_row(
                cast(
                    "sqlite3.Row",
                    self._stack_row(con, tenant=tenant, stack_id=stack_id),
                )
            )
            if conflict_created:
                stack = replace(stack, status="conflicted", updated_at=timestamp)
            else:
                stack = replace(stack, updated_at=timestamp)
            self._update_stack(con, stack)
        return self.get_state(tenant=tenant, stack_id=stack_id)

    def apply(  # noqa: C901, PLR0912, PLR0913, PLR0915
        self,
        *,
        tenant: TenantIdentity,
        stack_id: str,
        actor: VerifiedActor,
        storage: StackStorageClient | None,
        artifact_store: ArtifactStore | None,
        now: datetime | None = None,
    ) -> StackApplyResult | None:
        """Apply an approved stack with failure-stop semantics.

        ``report_duplicate`` changes are evidence-only and can be marked applied
        locally. ``delete_duplicate`` changes use the provided storage client and
        verify the approved digest immediately before delete. Archive-before-delete
        remains blocked until a provider-neutral copy primitive is available.
        """
        timestamp = now or datetime.now(UTC)
        state = self.get_state(tenant=tenant, stack_id=stack_id)
        if state is None:
            return None
        if state.stack.status != "approved":
            return StackApplyResult(
                stack=state.stack,
                applied_count=0,
                blocked_count=len(state.changes),
                failed_count=0,
                status="blocked",
                next_step=(
                    f"approve stack {stack_id} and resolve conflicts before applying"
                ),
            )
        latest_by_change = _latest_revision_by_change(state.revisions)
        applied = 0
        blocked = 0
        failed = 0
        next_step = "No further action required."
        conflict_to_write: ConflictArtifact | None = None
        delete_evidence: list[tuple[StorageChange, StorageChangeRevision]] = []
        with self._transaction() as con:
            for entry in state.entries:
                change = _change_by_id(state.changes, entry.change_id)
                if change.status != "approved":
                    blocked += 1
                    next_step = f"resolve change {change.change_id} before retrying"
                    break
                revision = latest_by_change[change.change_id]
                operation = revision.operation
                target = revision.target
                object_name = _target_object_name(target)
                container = _target_container(target)
                if operation == "report_duplicate":
                    self._mark_change_applied(
                        con,
                        change=change,
                        revision=revision,
                        actor=actor,
                        now=timestamp,
                        metadata={"mode": "report_only"},
                    )
                    applied += 1
                    continue
                if operation == "archive_then_delete":
                    blocked += 1
                    next_step = (
                        "provider-neutral copy support is required before "
                        "archive-before-delete can execute"
                    )
                    self._insert_operation(
                        con,
                        _operation(
                            tenant=tenant,
                            stack_id=stack_id,
                            change_id=change.change_id,
                            kind="change_apply_blocked",
                            status="blocked",
                            summary=next_step,
                            metadata={"object_name": object_name},
                            now=timestamp,
                        ),
                    )
                    break
                if operation != "delete_duplicate":
                    blocked += 1
                    next_step = f"unsupported stack operation: {operation}"
                    break
                if storage is None:
                    blocked += 1
                    next_step = (
                        "configure S3 storage credentials before applying deletes"
                    )
                    break
                observed = _observed_sha256(
                    storage.get_file_info(container, object_name)
                )
                expected = revision.target_digest
                if expected and observed != expected:
                    blocked += 1
                    next_step = (
                        "target changed after approval; run stack restack with a "
                        "fresh manifest"
                    )
                    conflict_to_write = self._mark_conflict_for_apply(
                        con,
                        tenant=tenant,
                        change=change,
                        revision=revision,
                        observed_digest=observed,
                        now=timestamp,
                    )
                    break
                delete_result = storage.delete_file(container, object_name)
                self._mark_change_applied(
                    con,
                    change=change,
                    revision=revision,
                    actor=actor,
                    now=timestamp,
                    metadata={
                        "mode": "delete",
                        "delete_result": _delete_result_json(delete_result),
                    },
                )
                applied += 1
                delete_evidence.append((change, revision))
            current_stack = self._stack_from_row(
                cast(
                    "sqlite3.Row",
                    self._stack_row(con, tenant=tenant, stack_id=stack_id),
                )
            )
            if blocked:
                self._update_stack(
                    con,
                    replace(current_stack, updated_at=timestamp),
                )
            elif failed:
                self._update_stack(
                    con,
                    replace(
                        current_stack,
                        status="failed",
                        updated_at=timestamp,
                    ),
                )
            else:
                self._update_stack(
                    con,
                    replace(
                        current_stack,
                        status="applied",
                        updated_at=timestamp,
                    ),
                )
                next_step = "Validate proof receipts with `nimbus proof show latest`."
        if artifact_store is not None and conflict_to_write is not None:
            artifact_store.create(
                artifact=Artifact(
                    artifact_id=conflict_to_write.conflict_id,
                    tenant=tenant,
                    session_id=_stack_session_id(state.stack),
                    action_id=None,
                    kind="conflict_artifact",
                    uri=None,
                    payload=conflict_to_write,
                    created_at=timestamp,
                ),
                actor=actor,
            )
        if artifact_store is not None:
            for change, revision in delete_evidence:
                _write_delete_evidence(
                    artifact_store=artifact_store,
                    stack=state.stack,
                    change=change,
                    revision=revision,
                    actor=actor,
                    now=timestamp,
                )
        refreshed = self.get_state(tenant=tenant, stack_id=stack_id)
        if refreshed is None:
            return None
        status = "applied" if refreshed.stack.status == "applied" else "blocked"
        return StackApplyResult(
            stack=refreshed.stack,
            applied_count=applied,
            blocked_count=blocked,
            failed_count=failed,
            status=status,
            next_step=next_step,
        )

    def _mark_change_applied(  # noqa: PLR0913
        self,
        con: sqlite3.Connection,
        *,
        change: StorageChange,
        revision: StorageChangeRevision,
        actor: VerifiedActor,
        now: datetime,
        metadata: Mapping[str, object],
    ) -> None:
        self._update_change(
            con,
            replace(change, status="applied", updated_at=now),
        )
        self._insert_operation(
            con,
            _operation(
                tenant=change.tenant,
                stack_id=change.stack_id,
                change_id=change.change_id,
                kind="change_applied",
                status="applied",
                summary=(
                    f"Applied {revision.operation} to "
                    f"{_target_object_name(revision.target)}."
                ),
                metadata={"actor_id": actor.user_id, **dict(metadata)},
                now=now,
            ),
        )

    def _mark_conflict_for_apply(  # noqa: PLR0913
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        change: StorageChange,
        revision: StorageChangeRevision,
        observed_digest: str | None,
        now: datetime,
    ) -> ConflictArtifact:
        conflict = _conflict_artifact(
            tenant=tenant,
            stack_id=change.stack_id,
            change_id=change.change_id,
            object_name=_target_object_name(revision.target),
            expected_digest=revision.target_digest,
            observed_digest=observed_digest,
            reason="target digest changed during apply verifier gate",
            now=now,
        )
        self._update_change(
            con,
            replace(change, status="conflicted", updated_at=now),
        )
        stack = self._stack_from_row(
            cast(
                "sqlite3.Row",
                self._stack_row(con, tenant=tenant, stack_id=change.stack_id),
            )
        )
        self._update_stack(con, replace(stack, status="conflicted", updated_at=now))
        self._insert_operation(
            con,
            _operation(
                tenant=tenant,
                stack_id=change.stack_id,
                change_id=change.change_id,
                kind="apply_conflict",
                status="conflicted",
                summary=f"Verifier conflict for {conflict.object_name}.",
                metadata={"conflict_artifact_id": conflict.conflict_id},
                now=now,
            ),
        )
        return conflict

    def _state_from_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        stack_id: str,
    ) -> StorageStackState | None:
        stack = self._stack_from_row_or_none(
            self._stack_row(con, tenant=tenant, stack_id=stack_id)
        )
        if stack is None:
            return None
        entries = self._entries_from_connection(
            con,
            tenant=tenant,
            stack_id=stack_id,
        )
        changes = self._changes_from_connection(
            con,
            tenant=tenant,
            stack_id=stack_id,
        )
        revisions = self._revisions_from_connection(
            con,
            tenant=tenant,
            stack_id=stack_id,
        )
        operations = self._operations_from_connection(
            con,
            tenant=tenant,
            stack_id=stack_id,
        )
        return StorageStackState(
            stack=stack,
            entries=entries,
            changes=changes,
            revisions=revisions,
            operations=operations,
        )

    @staticmethod
    def _stack_row(
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        stack_id: str,
    ) -> sqlite3.Row | None:
        row = con.execute(
            """
            SELECT * FROM storage_stacks
            WHERE tenant_id = ? AND stack_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, stack_id),
        ).fetchone()
        return cast("sqlite3.Row | None", row)

    @staticmethod
    def _insert_stack(con: sqlite3.Connection, stack: StorageChangeStack) -> None:
        con.execute(
            """
            INSERT INTO storage_stacks (
                stack_id, tenant_id, tenant_json, plan_id, title, status,
                actor_json, metadata_json, created_at, updated_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stack.stack_id,
                stack.tenant.tenant_id,
                _json_dumps(_tenant_to_json(stack.tenant)),
                stack.plan_id,
                stack.title,
                stack.status,
                _json_dumps(_actor_to_json(stack.created_by)),
                _json_dumps({"values": dict(stack.metadata)}),
                _datetime_to_json(stack.created_at),
                _datetime_to_json(stack.updated_at),
                _SCHEMA_VERSION,
            ),
        )

    @staticmethod
    def _update_stack(con: sqlite3.Connection, stack: StorageChangeStack) -> None:
        con.execute(
            """
            UPDATE storage_stacks
            SET status = ?, metadata_json = ?, updated_at = ?
            WHERE tenant_id = ? AND stack_id = ?
            """,
            (
                stack.status,
                _json_dumps({"values": dict(stack.metadata)}),
                _datetime_to_json(stack.updated_at),
                stack.tenant.tenant_id,
                stack.stack_id,
            ),
        )

    @staticmethod
    def _insert_change(con: sqlite3.Connection, change: StorageChange) -> None:
        con.execute(
            """
            INSERT INTO storage_changes (
                change_id, tenant_id, tenant_json, stack_id, current_revision_id,
                status, title, created_at, updated_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                change.change_id,
                change.tenant.tenant_id,
                _json_dumps(_tenant_to_json(change.tenant)),
                change.stack_id,
                change.current_revision_id,
                change.status,
                change.title,
                _datetime_to_json(change.created_at),
                _datetime_to_json(change.updated_at),
                _SCHEMA_VERSION,
            ),
        )

    @staticmethod
    def _update_change(con: sqlite3.Connection, change: StorageChange) -> None:
        con.execute(
            """
            UPDATE storage_changes
            SET current_revision_id = ?, status = ?, updated_at = ?
            WHERE tenant_id = ? AND change_id = ?
            """,
            (
                change.current_revision_id,
                change.status,
                _datetime_to_json(change.updated_at),
                change.tenant.tenant_id,
                change.change_id,
            ),
        )

    @staticmethod
    def _insert_revision(
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        revision: StorageChangeRevision,
    ) -> None:
        con.execute(
            """
            INSERT INTO storage_change_revisions (
                revision_id, tenant_id, change_id, stack_id, base_generation_id,
                target_digest, risk_level, operation, target_json, reason,
                created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision.revision_id,
                tenant.tenant_id,
                revision.change_id,
                revision.stack_id,
                revision.base_generation_id,
                revision.target_digest,
                revision.risk_level.value,
                revision.operation,
                _json_dumps({"values": dict(revision.target)}),
                revision.reason,
                _datetime_to_json(revision.created_at),
                _SCHEMA_VERSION,
            ),
        )

    @staticmethod
    def _insert_entry(
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        entry: StorageChangeStackEntry,
    ) -> None:
        con.execute(
            """
            INSERT INTO storage_stack_entries (
                tenant_id, stack_id, change_id, position, schema_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                tenant.tenant_id,
                entry.stack_id,
                entry.change_id,
                entry.position,
                _SCHEMA_VERSION,
            ),
        )

    @staticmethod
    def _insert_operation(con: sqlite3.Connection, operation: RuntimeOperation) -> None:
        con.execute(
            """
            INSERT INTO runtime_operations (
                operation_id, tenant_id, tenant_json, stack_id, change_id, kind,
                status, summary, metadata_json, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation.operation_id,
                operation.tenant.tenant_id,
                _json_dumps(_tenant_to_json(operation.tenant)),
                operation.stack_id,
                operation.change_id,
                operation.kind,
                operation.status,
                operation.summary,
                _json_dumps({"values": dict(operation.metadata)}),
                _datetime_to_json(operation.created_at),
                _SCHEMA_VERSION,
            ),
        )

    @staticmethod
    def _stack_from_row_or_none(
        row: sqlite3.Row | None,
    ) -> StorageChangeStack | None:
        return None if row is None else FileStorageStackStore._stack_from_row(row)

    @staticmethod
    def _stack_from_row(row: sqlite3.Row) -> StorageChangeStack:
        _ensure_row_schema(row)
        metadata = _values_mapping(
            _json_loads_object(_row_str(row, "metadata_json"), field="metadata")
        )
        actor = _actor_from_json(
            _json_loads_object(_row_str(row, "actor_json"), field="actor")
        )
        if actor is None:
            msg = "stack row is missing actor"
            raise TypeError(msg)
        return StorageChangeStack(
            stack_id=_row_str(row, "stack_id"),
            tenant=_tenant_from_json(
                _json_object(_row_str(row, "tenant_json"), "tenant")
            ),
            plan_id=_row_optional_str(row, "plan_id"),
            title=_row_str(row, "title"),
            status=cast("StorageChangeStatus", _row_str(row, "status")),
            created_by=actor,
            created_at=_datetime_from_json(_row_str(row, "created_at")),
            updated_at=_datetime_from_json(_row_str(row, "updated_at")),
            metadata=metadata,
        )

    @staticmethod
    def _change_from_row(row: sqlite3.Row) -> StorageChange:
        _ensure_row_schema(row)
        return StorageChange(
            change_id=_row_str(row, "change_id"),
            tenant=_tenant_from_json(
                _json_object(_row_str(row, "tenant_json"), "tenant")
            ),
            stack_id=_row_str(row, "stack_id"),
            current_revision_id=_row_str(row, "current_revision_id"),
            status=cast("StorageChangeStatus", _row_str(row, "status")),
            title=_row_str(row, "title"),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
            updated_at=_datetime_from_json(_row_str(row, "updated_at")),
        )

    @staticmethod
    def _revision_from_row(row: sqlite3.Row) -> StorageChangeRevision:
        _ensure_row_schema(row)
        target = _values_mapping(
            _json_loads_object(_row_str(row, "target_json"), field="target")
        )
        return StorageChangeRevision(
            revision_id=_row_str(row, "revision_id"),
            change_id=_row_str(row, "change_id"),
            stack_id=_row_str(row, "stack_id"),
            base_generation_id=_row_optional_str(row, "base_generation_id"),
            target_digest=_row_optional_str(row, "target_digest"),
            risk_level=PlanRiskLevel(_row_str(row, "risk_level")),
            operation=_row_str(row, "operation"),
            target=target,
            reason=_row_str(row, "reason"),
            created_at=_datetime_from_json(_row_str(row, "created_at")),
        )

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> StorageChangeStackEntry:
        _ensure_row_schema(row)
        return StorageChangeStackEntry(
            stack_id=_row_str(row, "stack_id"),
            change_id=_row_str(row, "change_id"),
            position=_row_int(row, "position"),
        )

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> RuntimeOperation:
        _ensure_row_schema(row)
        metadata = _values_mapping(
            _json_loads_object(_row_str(row, "metadata_json"), field="metadata")
        )
        return RuntimeOperation(
            operation_id=_row_str(row, "operation_id"),
            tenant=_tenant_from_json(
                _json_object(_row_str(row, "tenant_json"), "tenant")
            ),
            stack_id=_row_str(row, "stack_id"),
            change_id=_row_optional_str(row, "change_id"),
            kind=_row_str(row, "kind"),
            status=_row_str(row, "status"),
            summary=_row_str(row, "summary"),
            metadata=metadata,
            created_at=_datetime_from_json(_row_str(row, "created_at")),
        )

    def _entries_from_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        stack_id: str,
    ) -> tuple[StorageChangeStackEntry, ...]:
        rows = con.execute(
            """
            SELECT * FROM storage_stack_entries
            WHERE tenant_id = ? AND stack_id = ?
            ORDER BY position ASC
            """,
            (tenant.tenant_id, stack_id),
        ).fetchall()
        return tuple(self._entry_from_row(row) for row in rows)

    def _changes_from_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        stack_id: str,
    ) -> tuple[StorageChange, ...]:
        rows = con.execute(
            """
            SELECT * FROM storage_changes
            WHERE tenant_id = ? AND stack_id = ?
            ORDER BY created_at ASC, change_id ASC
            """,
            (tenant.tenant_id, stack_id),
        ).fetchall()
        return tuple(self._change_from_row(row) for row in rows)

    def _revisions_from_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        stack_id: str,
    ) -> tuple[StorageChangeRevision, ...]:
        rows = con.execute(
            """
            SELECT * FROM storage_change_revisions
            WHERE tenant_id = ? AND stack_id = ?
            ORDER BY created_at ASC, revision_id ASC
            """,
            (tenant.tenant_id, stack_id),
        ).fetchall()
        return tuple(self._revision_from_row(row) for row in rows)

    def _operations_from_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        stack_id: str,
    ) -> tuple[RuntimeOperation, ...]:
        rows = con.execute(
            """
            SELECT * FROM runtime_operations
            WHERE tenant_id = ? AND stack_id = ?
            ORDER BY created_at ASC, operation_id ASC
            """,
            (tenant.tenant_id, stack_id),
        ).fetchall()
        return tuple(self._operation_from_row(row) for row in rows)


def stack_id_for_plan(plan: Plan) -> str:
    """Return the deterministic stack ID for one plan."""
    digest = digest_value(
        {
            "tenant_id": plan.tenant.tenant_id,
            "plan_id": plan.plan_id,
            "idempotency_key": plan.idempotency_key,
        }
    )
    return f"stk-{digest.removeprefix('sha256:')[:24]}"


def _initial_revisions_for_plan(
    *,
    plan: Plan,
    stack_id: str,
    now: datetime,
) -> tuple[StorageChangeRevision, ...]:
    duplicate_groups = plan.metadata.get("duplicate_groups")
    if plan.metadata.get("operation") == "candidate_cleanup" and isinstance(
        duplicate_groups, list
    ):
        return _cleanup_revisions(
            plan=plan,
            stack_id=stack_id,
            duplicate_groups=duplicate_groups,
            now=now,
        )
    target: dict[str, object] = {
        "provider": plan.target.provider if plan.target is not None else "s3",
        "container": plan.target.container if plan.target is not None else "",
        "object_name": plan.target.object_name if plan.target is not None else "",
        "version_id": plan.target.version_id if plan.target is not None else None,
    }
    change_id = _change_id(plan_id=plan.plan_id, operation="preview", target=target)
    return (
        StorageChangeRevision(
            revision_id=_revision_id(
                change_id=change_id,
                reason="initial_plan_revision",
                target=target,
            ),
            change_id=change_id,
            stack_id=stack_id,
            base_generation_id=_optional_metadata_str(plan.metadata, "generation_id"),
            target_digest=None,
            risk_level=plan.risk_level,
            operation="preview",
            target=target,
            reason="initial_plan_revision",
            created_at=now,
        ),
    )


def _cleanup_revisions(
    *,
    plan: Plan,
    stack_id: str,
    duplicate_groups: Sequence[object],
    now: datetime,
) -> tuple[StorageChangeRevision, ...]:
    revisions: list[StorageChangeRevision] = []
    operation = _operation_for_strategy(
        _optional_metadata_str(plan.metadata, "candidate_strategy") or "report_only"
    )
    for group_index, group_value in enumerate(duplicate_groups):
        group = _mapping(group_value, field="duplicate_groups")
        keep = _mapping(group.get("keep"), field="keep")
        duplicates = _sequence(group.get("duplicates"), field="duplicates")
        for duplicate_index, duplicate_value in enumerate(duplicates):
            duplicate = _mapping(duplicate_value, field="duplicates")
            target = {
                "provider": "s3",
                "container": _optional_metadata_str(plan.metadata, "container")
                or _optional_metadata_str(plan.metadata, "destination_container")
                or "",
                "object_name": _required_metadata_str(duplicate, "object_name"),
                "content_sha256": _required_metadata_str(
                    duplicate,
                    "content_sha256",
                ),
                "size_bytes": _optional_metadata_int(duplicate, "size_bytes"),
                "keep_object_name": _required_metadata_str(keep, "object_name"),
                "manifest_artifact_id": _optional_metadata_str(
                    plan.metadata,
                    "manifest_artifact_id",
                ),
                "group_index": group_index,
                "duplicate_index": duplicate_index,
            }
            change_id = _change_id(
                plan_id=plan.plan_id,
                operation=operation,
                target=target,
            )
            revisions.append(
                StorageChangeRevision(
                    revision_id=_revision_id(
                        change_id=change_id,
                        reason="initial_cleanup_plan_revision",
                        target=target,
                    ),
                    change_id=change_id,
                    stack_id=stack_id,
                    base_generation_id=_optional_metadata_str(
                        plan.metadata,
                        "generation_id",
                    ),
                    target_digest=_required_metadata_str(
                        duplicate,
                        "content_sha256",
                    ),
                    risk_level=plan.risk_level,
                    operation=operation,
                    target=target,
                    reason="initial_cleanup_plan_revision",
                    created_at=now,
                )
            )
    return tuple(
        sorted(revisions, key=lambda revision: _target_object_name(revision.target))
    )


def _operation_for_strategy(strategy: str) -> str:
    if strategy == "archive_before_delete":
        return "archive_then_delete"
    if strategy == "delete_extra_copies":
        return "delete_duplicate"
    return "report_duplicate"


def _stack_metadata_from_plan(plan: Plan) -> dict[str, object]:
    metadata = dict(plan.metadata)
    metadata.update(
        {
            "plan_id": plan.plan_id,
            "session_id": plan.session_id,
            "task_id": plan.task_id,
            "action_id": plan.action_id,
            "risk_level": plan.risk_level.value,
            "approval_binding": {
                "tenant_id": plan.tenant.tenant_id,
                "plan_id": plan.plan_id,
                "idempotency_key": plan.idempotency_key,
            },
        }
    )
    return metadata


def _change_title(revision: StorageChangeRevision) -> str:
    object_name = _target_object_name(revision.target)
    return f"{revision.operation.replace('_', ' ')}: {object_name}"


def _change_id(
    *,
    plan_id: str,
    operation: str,
    target: Mapping[str, object],
) -> str:
    digest = digest_value(
        {"plan_id": plan_id, "operation": operation, "target": dict(target)}
    )
    return f"chg-{digest.removeprefix('sha256:')[:24]}"


def _revision_id(
    *,
    change_id: str,
    reason: str,
    target: Mapping[str, object],
) -> str:
    digest = digest_value(
        {"change_id": change_id, "reason": reason, "target": dict(target)}
    )
    return f"rev-{digest.removeprefix('sha256:')[:24]}"


def _operation(  # noqa: PLR0913
    *,
    tenant: TenantIdentity,
    stack_id: str,
    change_id: str | None,
    kind: str,
    status: str,
    summary: str,
    metadata: Mapping[str, object],
    now: datetime,
) -> RuntimeOperation:
    digest = digest_value(
        {
            "tenant_id": tenant.tenant_id,
            "stack_id": stack_id,
            "change_id": change_id,
            "kind": kind,
            "summary": summary,
            "created_at": _datetime_to_json(now),
        }
    )
    return RuntimeOperation(
        operation_id=f"op-{digest.removeprefix('sha256:')[:24]}",
        tenant=tenant,
        stack_id=stack_id,
        change_id=change_id,
        kind=kind,
        status=status,
        summary=summary,
        metadata=dict(metadata),
        created_at=now,
    )


def _latest_revision_by_change(
    revisions: Sequence[StorageChangeRevision],
) -> dict[str, StorageChangeRevision]:
    latest: dict[str, StorageChangeRevision] = {}
    for revision in revisions:
        current = latest.get(revision.change_id)
        if current is None or revision.created_at >= current.created_at:
            latest[revision.change_id] = revision
    return latest


def _next_revision(
    *,
    revision: StorageChangeRevision,
    reason: str,
    now: datetime,
) -> StorageChangeRevision:
    return replace(
        revision,
        revision_id=_revision_id(
            change_id=revision.change_id,
            reason=f"{reason}:{_datetime_to_json(now)}",
            target=revision.target,
        ),
        reason=reason,
        created_at=now,
    )


def _change_by_id(
    changes: Sequence[StorageChange],
    change_id: str,
) -> StorageChange:
    for change in changes:
        if change.change_id == change_id:
            return change
    msg = f"stack change {change_id!r} is missing"
    raise KeyError(msg)


def _target_object_name(target: Mapping[str, object]) -> str:
    return _required_metadata_str(target, "object_name")


def _target_container(target: Mapping[str, object]) -> str:
    return _required_metadata_str(target, "container")


def _conflict_artifact(  # noqa: PLR0913
    *,
    tenant: TenantIdentity,
    stack_id: str,
    change_id: str,
    object_name: str,
    expected_digest: str | None,
    observed_digest: str | None,
    reason: str,
    now: datetime,
) -> ConflictArtifact:
    digest = digest_value(
        {
            "tenant_id": tenant.tenant_id,
            "stack_id": stack_id,
            "change_id": change_id,
            "object_name": object_name,
            "expected_digest": expected_digest,
            "observed_digest": observed_digest,
            "reason": reason,
        }
    )
    return ConflictArtifact(
        conflict_id=f"conf-{digest.removeprefix('sha256:')[:24]}",
        tenant=tenant,
        stack_id=stack_id,
        change_id=change_id,
        object_name=object_name,
        expected_digest=expected_digest,
        observed_digest=observed_digest,
        reason=reason,
        status="open",
        next_step="Run `nimbus stack restack` after creating a fresh generation.",
        created_at=now,
    )


def _observed_sha256(info: object) -> str | None:
    metadata = getattr(info, "metadata", None) or {}
    if isinstance(metadata, dict):
        metadata_sha = metadata.get("sha256") or metadata.get("nimbus-sha256")
        if isinstance(metadata_sha, str):
            return metadata_sha.lower()
    integrity = getattr(info, "integrity", None)
    if isinstance(integrity, str) and integrity.startswith("sha256:"):
        return integrity.removeprefix("sha256:").lower()
    return None


def _delete_result_json(result: object) -> dict[str, object]:
    return {
        "deleted": bool(getattr(result, "deleted", False)),
        "version_id": getattr(result, "version_id", None),
        "request_charged": getattr(result, "request_charged", None),
    }


def _write_delete_evidence(  # noqa: PLR0913
    *,
    artifact_store: ArtifactStore,
    stack: StorageChangeStack,
    change: StorageChange,
    revision: StorageChangeRevision,
    actor: VerifiedActor,
    now: datetime,
) -> None:
    object_name = _target_object_name(revision.target)
    mutation = StorageMutationReport(
        operation="delete_duplicate",
        source_path=None,
        dest_path=None,
        remote_path=object_name,
        size_bytes=_optional_metadata_int(revision.target, "size_bytes"),
        sha256_hex=revision.target_digest,
        overwrote=False,
        source_deleted=True,
        dest_version_id=None,
        verified=True,
        verifier="stack_apply_digest_gate",
    )
    mutation_digest = digest_value(
        {
            "stack_id": stack.stack_id,
            "change_id": change.change_id,
            "revision_id": revision.revision_id,
            "payload": mutation,
        }
    ).removeprefix("sha256:")
    mutation_artifact = artifact_store.create(
        artifact=Artifact(
            artifact_id=f"art-stack-{mutation_digest[:24]}",
            tenant=stack.tenant,
            session_id=_stack_session_id(stack),
            action_id=None,
            kind="storage_mutation_report",
            uri=None,
            payload=mutation,
            created_at=now,
        ),
        actor=actor,
    )
    linked_ids = (mutation_artifact.artifact_id,)
    receipt_id = deterministic_receipt_id(
        tenant=stack.tenant,
        subject=f"stack:{stack.stack_id}:change:{change.change_id}",
        task_id=_optional_metadata_str(stack.metadata, "task_id"),
        action_id=_optional_metadata_str(stack.metadata, "action_id"),
        manifest_artifact_id=_optional_metadata_str(
            revision.target,
            "manifest_artifact_id",
        ),
        verifier_artifact_id=mutation_artifact.artifact_id,
        linked_artifact_ids=linked_ids,
    )
    receipt = ProofReceipt(
        receipt_id=receipt_id,
        tenant=stack.tenant,
        subject=f"stack:{stack.stack_id}:change:{change.change_id}",
        outcome="applied",
        summary=f"Deleted duplicate object {object_name} after digest verification.",
        task_id=_optional_metadata_str(stack.metadata, "task_id"),
        action_id=_optional_metadata_str(stack.metadata, "action_id"),
        manifest_artifact_id=_optional_metadata_str(
            revision.target,
            "manifest_artifact_id",
        ),
        verifier_artifact_id=mutation_artifact.artifact_id,
        linked_artifact_ids=linked_ids,
        artifact_digests={
            mutation_artifact.artifact_id: mutation_artifact.payload_digest
            or artifact_payload_digest(mutation_artifact.payload)
        },
        session_id=_stack_session_id(stack),
        event_range_start=None,
        event_range_end=None,
        policy_version="runtime-default-v1",
        idempotency_key=change.change_id,
        next_steps=("nimbus proof show latest", f"nimbus stack show {stack.stack_id}"),
        created_at=now,
    )
    artifact_store.create(
        artifact=Artifact(
            artifact_id=receipt.receipt_id,
            tenant=stack.tenant,
            session_id=_stack_session_id(stack),
            action_id=None,
            kind="proof_receipt",
            uri=None,
            payload=receipt,
            created_at=now,
        ),
        actor=actor,
    )


def _stack_session_id(stack: StorageChangeStack) -> str:
    session_id = stack.metadata.get("session_id")
    return session_id if isinstance(session_id, str) else stack.stack_id


def _ensure_row_schema(row: sqlite3.Row) -> None:
    if _row_int(row, "schema_version") != _SCHEMA_VERSION:
        msg = "unsupported storage stack schema version"
        raise ValueError(msg)


def _json_object(raw: str, field: str) -> Mapping[str, object]:
    data = _json_loads_object(raw, field=field)
    if data is None:
        msg = f"expected JSON object for {field}"
        raise TypeError(msg)
    return data


def _values_mapping(data: Mapping[str, object] | None) -> dict[str, object]:
    if data is None:
        return {}
    values = data.get("values")
    if values is None:
        return dict(data)
    if not isinstance(values, dict):
        msg = "expected metadata values to be an object"
        raise TypeError(msg)
    return cast("dict[str, object]", values)


def _mapping(value: object, *, field: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        msg = f"expected {field} to contain objects"
        raise TypeError(msg)
    return cast("Mapping[str, object]", value)


def _sequence(value: object, *, field: str) -> Sequence[object]:
    if not isinstance(value, list | tuple):
        msg = f"expected {field} to be an array"
        raise TypeError(msg)
    return value


def _required_metadata_str(data: Mapping[str, object], key: str) -> str:
    return _required_str(data, key)


def _optional_metadata_str(data: Mapping[str, object], key: str) -> str | None:
    value = data.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"expected optional string metadata field {key!r}"
    raise TypeError(msg)


def _optional_metadata_int(data: Mapping[str, object], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    return _required_int(data, key)
