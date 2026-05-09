"""Protected roots, generation manifests, and storage snapshot verification.

The generation layer is intentionally provider-neutral. The MVP creates
generations from the existing ``CloudStorageClient`` listing contract and stores
the resulting manifest as a normal Nimbus artifact; richer provider SDK details
stay behind capability adapters.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

from cloud_storage_api.exceptions import (
    AuthenticationError,
    ContainerNotFoundError,
    ObjectNotFoundError,
    StorageBackendError,
)

from nimbus_runtime.domain import (
    ActorAuthSource,
    Artifact,
    DriftObjectEntry,
    DriftObjectStatus,
    DriftReport,
    Generation,
    GenerationManifest,
    GenerationStatus,
    ObjectPointer,
    ProofReceipt,
    ProtectedRoot,
    ProviderName,
    TenantIdentity,
    VerifiedActor,
)
from nimbus_runtime.proof import (
    artifact_payload_digest,
    deterministic_receipt_id,
    digest_value,
    to_jsonable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from cloud_storage_api import CloudStorageClient, ObjectInfo

    from nimbus_runtime.stores import ArtifactStore, SessionEventStore

_DB_FILENAME = "nimbus_generations.sqlite3"
_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5_000
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_SHA256_METADATA_KEY = "sha256"


@dataclass(frozen=True, slots=True)
class GenerationCreateResult:
    """Artifacts and durable row created for a generation snapshot."""

    generation: Generation
    manifest_artifact: Artifact
    proof_artifact: Artifact


@dataclass(frozen=True, slots=True)
class GenerationDiffEntry:
    """One object-level difference between two generation manifests."""

    status: str
    object_name: str
    before: ObjectPointer | None
    after: ObjectPointer | None


@dataclass(frozen=True, slots=True)
class GenerationDiff:
    """Deterministic object diff between two generation manifests."""

    before_generation_id: str
    after_generation_id: str
    added_count: int
    removed_count: int
    changed_count: int
    unchanged_count: int
    entries: tuple[GenerationDiffEntry, ...]


class ProtectedRootStore(Protocol):
    """Durable store for tenant-scoped protected roots."""

    def protect(  # noqa: PLR0913 - root identity is explicit at the boundary.
        self,
        *,
        tenant: TenantIdentity,
        provider: ProviderName,
        container: str,
        prefix: str,
        display_name: str,
        actor: VerifiedActor,
        metadata: Mapping[str, object] | None = None,
        now: datetime | None = None,
    ) -> ProtectedRoot:
        """Create or return the protected root for one provider scope."""

    def get(self, *, tenant: TenantIdentity, root_id: str) -> ProtectedRoot | None:
        """Return one root if it belongs to ``tenant``."""

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        limit: int = 100,
    ) -> Sequence[ProtectedRoot]:
        """Return recent roots for a tenant."""


class GenerationStore(Protocol):
    """Durable store for immutable generation rows."""

    def create_or_get(self, *, generation: Generation) -> Generation:
        """Persist a generation by ID or return the existing row."""

    def get(self, *, tenant: TenantIdentity, generation_id: str) -> Generation | None:
        """Return one generation if it belongs to ``tenant``."""

    def latest_for_root(
        self,
        *,
        tenant: TenantIdentity,
        root_id: str,
    ) -> Generation | None:
        """Return the newest generation for one protected root."""

    def list_for_root(
        self,
        *,
        tenant: TenantIdentity,
        root_id: str,
        limit: int = 100,
    ) -> Sequence[Generation]:
        """Return generations for one protected root, newest first."""

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        limit: int = 100,
    ) -> Sequence[Generation]:
        """Return recent generations across all protected roots."""


class FileProtectedRootStore:
    """SQLite-backed protected-root store for local demo and tests."""

    def __init__(self, root: Path) -> None:
        """Create a protected-root store under ``root``."""
        self._root = root
        self._db_path = root / _DB_FILENAME
        self._lock = _path_lock(self._db_path)
        self._init_db()

    def protect(  # noqa: PLR0913 - root identity is explicit at the boundary.
        self,
        *,
        tenant: TenantIdentity,
        provider: ProviderName,
        container: str,
        prefix: str,
        display_name: str,
        actor: VerifiedActor,
        metadata: Mapping[str, object] | None = None,
        now: datetime | None = None,
    ) -> ProtectedRoot:
        """Create or return the protected root for one provider scope."""
        timestamp = now or datetime.now(UTC)
        normalized_prefix = normalize_prefix(prefix)
        root_id = protected_root_id(
            tenant=tenant,
            provider=provider,
            container=container,
            prefix=normalized_prefix,
        )
        root = ProtectedRoot(
            root_id=root_id,
            tenant=tenant,
            provider=provider,
            container=container,
            prefix=normalized_prefix,
            display_name=display_name,
            protected_by=actor,
            created_at=timestamp,
            updated_at=timestamp,
            metadata=dict(metadata or {}),
        )
        with self._lock:
            con = self._connect()
            try:
                existing = self._read_with_connection(
                    con,
                    tenant=tenant,
                    root_id=root_id,
                )
                if existing is not None:
                    return existing
                con.execute(
                    """
                    INSERT INTO protected_roots (
                        root_id, tenant_id, provider, container, prefix,
                        updated_at, payload_json, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        root.root_id,
                        tenant.tenant_id,
                        root.provider,
                        root.container,
                        root.prefix,
                        _datetime_to_json(root.updated_at),
                        _json_dumps(_protected_root_to_json(root)),
                        _SCHEMA_VERSION,
                    ),
                )
                con.commit()
                return root
            finally:
                con.close()

    def get(self, *, tenant: TenantIdentity, root_id: str) -> ProtectedRoot | None:
        """Return one root if it belongs to ``tenant``."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_with_connection(
                    con,
                    tenant=tenant,
                    root_id=root_id,
                )
            finally:
                con.close()

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        limit: int = 100,
    ) -> Sequence[ProtectedRoot]:
        """Return recent roots for a tenant."""
        bounded = max(1, min(limit, 500))
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT payload_json FROM protected_roots
                    WHERE tenant_id = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (tenant.tenant_id, bounded),
                ).fetchall()
                return tuple(
                    _protected_root_from_json(_json_loads_object(row["payload_json"]))
                    for row in rows
                )
            finally:
                con.close()

    def _init_db(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            con = self._connect()
            try:
                _init_schema(con)
                con.commit()
            finally:
                con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path)
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return con

    def _read_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        root_id: str,
    ) -> ProtectedRoot | None:
        row = con.execute(
            """
            SELECT payload_json FROM protected_roots
            WHERE tenant_id = ? AND root_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, root_id),
        ).fetchone()
        if row is None:
            return None
        return _protected_root_from_json(_json_loads_object(row["payload_json"]))


class FileGenerationStore:
    """SQLite-backed generation store for local demo and tests."""

    def __init__(self, root: Path) -> None:
        """Create a generation store under ``root``."""
        self._root = root
        self._db_path = root / _DB_FILENAME
        self._lock = _path_lock(self._db_path)
        self._init_db()

    def create_or_get(self, *, generation: Generation) -> Generation:
        """Persist a generation by ID or return the existing row."""
        with self._lock:
            con = self._connect()
            try:
                existing = self._read_with_connection(
                    con,
                    tenant=generation.tenant,
                    generation_id=generation.generation_id,
                )
                if existing is not None:
                    return existing
                con.execute(
                    """
                    INSERT INTO generations (
                        generation_id, tenant_id, root_id, manifest_digest,
                        created_at, payload_json, schema_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        generation.generation_id,
                        generation.tenant.tenant_id,
                        generation.root_id,
                        generation.manifest_digest,
                        _datetime_to_json(generation.created_at),
                        _json_dumps(_generation_to_json(generation)),
                        _SCHEMA_VERSION,
                    ),
                )
                con.commit()
                return generation
            finally:
                con.close()

    def get(self, *, tenant: TenantIdentity, generation_id: str) -> Generation | None:
        """Return one generation if it belongs to ``tenant``."""
        with self._lock:
            con = self._connect()
            try:
                return self._read_with_connection(
                    con,
                    tenant=tenant,
                    generation_id=generation_id,
                )
            finally:
                con.close()

    def latest_for_root(
        self,
        *,
        tenant: TenantIdentity,
        root_id: str,
    ) -> Generation | None:
        """Return the newest generation for one protected root."""
        with self._lock:
            con = self._connect()
            try:
                row = con.execute(
                    """
                    SELECT payload_json FROM generations
                    WHERE tenant_id = ? AND root_id = ?
                    ORDER BY created_at DESC, generation_id DESC
                    LIMIT 1
                    """,
                    (tenant.tenant_id, root_id),
                ).fetchone()
                if row is None:
                    return None
                return _generation_from_json(_json_loads_object(row["payload_json"]))
            finally:
                con.close()

    def list_for_root(
        self,
        *,
        tenant: TenantIdentity,
        root_id: str,
        limit: int = 100,
    ) -> Sequence[Generation]:
        """Return generations for one protected root, newest first."""
        bounded = max(1, min(limit, 500))
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT payload_json FROM generations
                    WHERE tenant_id = ? AND root_id = ?
                    ORDER BY created_at DESC, generation_id DESC
                    LIMIT ?
                    """,
                    (tenant.tenant_id, root_id, bounded),
                ).fetchall()
                return tuple(
                    _generation_from_json(_json_loads_object(row["payload_json"]))
                    for row in rows
                )
            finally:
                con.close()

    def list_for_tenant(
        self,
        *,
        tenant: TenantIdentity,
        limit: int = 100,
    ) -> Sequence[Generation]:
        """Return recent generations across all protected roots."""
        bounded = max(1, min(limit, 500))
        with self._lock:
            con = self._connect()
            try:
                rows = con.execute(
                    """
                    SELECT payload_json FROM generations
                    WHERE tenant_id = ?
                    ORDER BY created_at DESC, generation_id DESC
                    LIMIT ?
                    """,
                    (tenant.tenant_id, bounded),
                ).fetchall()
                return tuple(
                    _generation_from_json(_json_loads_object(row["payload_json"]))
                    for row in rows
                )
            finally:
                con.close()

    def _init_db(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            con = self._connect()
            try:
                _init_schema(con)
                con.commit()
            finally:
                con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path)
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        return con

    def _read_with_connection(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        generation_id: str,
    ) -> Generation | None:
        row = con.execute(
            """
            SELECT payload_json FROM generations
            WHERE tenant_id = ? AND generation_id = ?
            LIMIT 1
            """,
            (tenant.tenant_id, generation_id),
        ).fetchone()
        if row is None:
            return None
        return _generation_from_json(_json_loads_object(row["payload_json"]))


def normalize_prefix(prefix: str) -> str:
    """Return a stable object-prefix representation."""
    clean = prefix.strip().lstrip("/")
    if clean and not clean.endswith("/"):
        return f"{clean}/"
    return clean


def protected_root_id(
    *,
    tenant: TenantIdentity,
    provider: ProviderName,
    container: str,
    prefix: str,
) -> str:
    """Return the deterministic ID for a tenant/provider/container/prefix root."""
    seed = {
        "tenant_id": tenant.tenant_id,
        "provider": provider,
        "container": container,
        "prefix": normalize_prefix(prefix),
    }
    digest = hashlib.sha256(_canonical_json_bytes(seed)).hexdigest()
    return f"root-{digest[:24]}"


def canonicalize_object_pointers(
    pointers: Sequence[ObjectPointer],
) -> tuple[ObjectPointer, ...]:
    """Return pointers in manifest-canonical order."""
    return tuple(
        sorted(
            pointers,
            key=lambda pointer: (
                pointer.provider,
                pointer.container,
                pointer.object_name,
                pointer.version_id or "",
            ),
        )
    )


def manifest_digest_for(
    *,
    root: ProtectedRoot,
    objects: Sequence[ObjectPointer],
) -> str:
    """Return the stable digest for a root/object set."""
    canonical_objects = canonicalize_object_pointers(objects)
    return digest_value(
        {
            "schema_version": _SCHEMA_VERSION,
            "root_id": root.root_id,
            "provider": root.provider,
            "container": root.container,
            "prefix": root.prefix,
            "objects": [
                _object_pointer_to_json(pointer) for pointer in canonical_objects
            ],
        }
    )


def generation_id_for(*, root_id: str, manifest_digest: str) -> str:
    """Return the retry-stable generation ID for one manifest digest."""
    digest = hashlib.sha256(
        _canonical_json_bytes({"root_id": root_id, "manifest_digest": manifest_digest})
    ).hexdigest()
    return f"gen-{digest[:32]}"


def create_generation(  # noqa: PLR0913 - generation commit binds store, actor, provider, and evidence.
    *,
    root: ProtectedRoot,
    storage: CloudStorageClient,
    artifact_store: ArtifactStore,
    generation_store: GenerationStore,
    actor: VerifiedActor,
    session_id: str,
    base_generation_id: str | None = None,
    now: datetime | None = None,
) -> GenerationCreateResult:
    """Create or return an immutable generation for the root's current listing."""
    timestamp = now or datetime.now(UTC)
    listed = storage.list_files(root.container, root.prefix)
    pointers = canonicalize_object_pointers(
        tuple(_pointer_from_object_info(root=root, info=info) for info in listed)
    )
    manifest_digest = manifest_digest_for(root=root, objects=pointers)
    generation_id = generation_id_for(
        root_id=root.root_id,
        manifest_digest=manifest_digest,
    )
    manifest = GenerationManifest(
        root_id=root.root_id,
        generation_id=generation_id,
        manifest_digest=manifest_digest,
        provider=root.provider,
        container=root.container,
        prefix=root.prefix,
        objects=pointers,
        object_count=len(pointers),
        total_bytes=sum(pointer.size_bytes or 0 for pointer in pointers),
        partial=False,
        created_at=timestamp,
    )
    manifest_artifact = artifact_store.create(
        artifact=Artifact(
            artifact_id=f"art-{generation_id}",
            tenant=root.tenant,
            session_id=session_id,
            action_id=None,
            kind="manifest",
            uri=None,
            payload=manifest,
            created_at=timestamp,
        ),
        actor=actor,
    )
    generation = generation_store.create_or_get(
        generation=Generation(
            generation_id=generation_id,
            tenant=root.tenant,
            root_id=root.root_id,
            manifest_artifact_id=manifest_artifact.artifact_id,
            manifest_digest=manifest_digest,
            object_count=manifest.object_count,
            total_bytes=manifest.total_bytes,
            status="complete",
            created_by=actor,
            created_at=timestamp,
            base_generation_id=base_generation_id,
            metadata={"provider": root.provider, "container": root.container},
        )
    )
    proof_artifact = _create_generation_proof_artifact(
        generation=generation,
        manifest_artifact=manifest_artifact,
        artifact_store=artifact_store,
        actor=actor,
        session_id=session_id,
        now=timestamp,
    )
    return GenerationCreateResult(
        generation=generation,
        manifest_artifact=manifest_artifact,
        proof_artifact=proof_artifact,
    )


def diff_generation_manifests(
    *,
    before: GenerationManifest,
    after: GenerationManifest,
) -> GenerationDiff:
    """Return a deterministic diff between two generation manifests."""
    before_by_name = {pointer.object_name: pointer for pointer in before.objects}
    after_by_name = {pointer.object_name: pointer for pointer in after.objects}
    entries: list[GenerationDiffEntry] = []
    for object_name in sorted(before_by_name.keys() | after_by_name.keys()):
        before_pointer = before_by_name.get(object_name)
        after_pointer = after_by_name.get(object_name)
        if before_pointer is None:
            status = "added"
        elif after_pointer is None:
            status = "removed"
        elif before_pointer == after_pointer:
            status = "unchanged"
        else:
            status = "changed"
        entries.append(
            GenerationDiffEntry(
                status=status,
                object_name=object_name,
                before=before_pointer,
                after=after_pointer,
            )
        )
    return GenerationDiff(
        before_generation_id=before.generation_id,
        after_generation_id=after.generation_id,
        added_count=sum(1 for entry in entries if entry.status == "added"),
        removed_count=sum(1 for entry in entries if entry.status == "removed"),
        changed_count=sum(1 for entry in entries if entry.status == "changed"),
        unchanged_count=sum(1 for entry in entries if entry.status == "unchanged"),
        entries=tuple(entries),
    )


def verify_generation_manifest(  # noqa: PLR0913 - verifier binds evidence stores and live provider.
    *,
    manifest: GenerationManifest,
    manifest_artifact_id: str,
    storage: CloudStorageClient,
    artifact_store: ArtifactStore,
    event_store: SessionEventStore,
    actor: VerifiedActor,
    session_id: str,
    strict: bool = False,
    now: datetime | None = None,
) -> DriftReport:
    """Verify a generation manifest against live storage and write drift evidence."""
    checked_at = now or datetime.now(UTC)
    entries: list[DriftObjectEntry] = []
    bucket_missing = False
    for pointer in manifest.objects:
        if bucket_missing:
            entries.append(
                _drift_entry(
                    pointer=pointer,
                    status="bucket_missing",
                    observed_sha256=None,
                    actor=actor,
                )
            )
            continue
        try:
            info = storage.get_file_info(manifest.container, pointer.object_name)
            entries.append(
                _entry_from_live_info(pointer=pointer, info=info, actor=actor)
            )
        except AuthenticationError:
            raise
        except ContainerNotFoundError:
            bucket_missing = True
            entries.append(
                _drift_entry(
                    pointer=pointer,
                    status="bucket_missing",
                    observed_sha256=None,
                    actor=actor,
                )
            )
        except ObjectNotFoundError:
            entries.append(
                _drift_entry(
                    pointer=pointer,
                    status="missing",
                    observed_sha256=None,
                    actor=actor,
                )
            )
        except StorageBackendError:
            raise

    match_count = sum(1 for entry in entries if entry.status == "match")
    mismatch_count = sum(1 for entry in entries if entry.status == "mismatch")
    missing_count = sum(1 for entry in entries if entry.status == "missing")
    unknown_count = sum(
        1 for entry in entries if entry.status in {"unknown", "bucket_missing"}
    )
    has_drift = bool(
        mismatch_count or missing_count or bucket_missing or (strict and unknown_count)
    )
    report = DriftReport(
        manifest_artifact_id=manifest_artifact_id,
        tenant=actor.tenant,
        checked_at=checked_at,
        container=manifest.container,
        prefix=manifest.prefix,
        total_count=len(entries),
        match_count=match_count,
        mismatch_count=mismatch_count,
        missing_count=missing_count,
        unknown_count=unknown_count,
        bucket_missing=bucket_missing,
        has_drift=has_drift,
        entries=tuple(entries),
        via_action_id=None,
    )
    artifact = artifact_store.create(
        artifact=Artifact(
            artifact_id=f"art-drift-{hashlib.sha256(_canonical_json_bytes(to_jsonable(report))).hexdigest()[:32]}",
            tenant=actor.tenant,
            session_id=session_id,
            action_id=None,
            kind="drift_report",
            uri=None,
            payload=report,
            created_at=checked_at,
        ),
        actor=actor,
    )
    event_store.append(
        tenant=actor.tenant,
        session_id=session_id,
        event_type="generation_verification_completed",
        actor=actor,
        payload={
            "manifest_artifact_id": manifest_artifact_id,
            "drift_artifact_id": artifact.artifact_id,
            "has_drift": has_drift,
            "strict": strict,
            "match_count": match_count,
            "mismatch_count": mismatch_count,
            "missing_count": missing_count,
            "unknown_count": unknown_count,
        },
    )
    return report


def _create_generation_proof_artifact(  # noqa: PLR0913
    *,
    generation: Generation,
    manifest_artifact: Artifact,
    artifact_store: ArtifactStore,
    actor: VerifiedActor,
    session_id: str,
    now: datetime,
) -> Artifact:
    linked_ids = (manifest_artifact.artifact_id,)
    receipt_id = deterministic_receipt_id(
        tenant=generation.tenant,
        subject=f"generation:{generation.root_id}",
        task_id=None,
        action_id=None,
        manifest_artifact_id=manifest_artifact.artifact_id,
        verifier_artifact_id=None,
        linked_artifact_ids=linked_ids,
    )
    return artifact_store.create(
        artifact=Artifact(
            artifact_id=receipt_id,
            tenant=generation.tenant,
            session_id=session_id,
            action_id=None,
            kind="proof_receipt",
            uri=None,
            payload=ProofReceipt(
                receipt_id=receipt_id,
                tenant=generation.tenant,
                subject=f"generation:{generation.root_id}",
                outcome="snapshotted",
                summary=(
                    f"Generation {generation.generation_id} captured "
                    f"{generation.object_count} objects."
                ),
                task_id=None,
                action_id=None,
                manifest_artifact_id=manifest_artifact.artifact_id,
                verifier_artifact_id=None,
                linked_artifact_ids=linked_ids,
                artifact_digests={
                    manifest_artifact.artifact_id: manifest_artifact.payload_digest
                    or artifact_payload_digest(manifest_artifact.payload)
                },
                session_id=session_id,
                event_range_start=None,
                event_range_end=None,
                policy_version="runtime-default-v1",
                idempotency_key=generation.generation_id,
                next_steps=(
                    f"nimbus verify {manifest_artifact.artifact_id}",
                    f"nimbus generation list {generation.root_id}",
                ),
                created_at=now,
            ),
            created_at=now,
        ),
        actor=actor,
    )


def _pointer_from_object_info(
    *,
    root: ProtectedRoot,
    info: ObjectInfo,
) -> ObjectPointer:
    return ObjectPointer(
        provider=root.provider,
        container=root.container,
        object_name=info.object_name,
        version_id=info.version_id,
        content_sha256=_sha256_from_info(info),
        size_bytes=info.size_bytes,
    )


def _entry_from_live_info(
    *,
    pointer: ObjectPointer,
    info: ObjectInfo,
    actor: VerifiedActor,
) -> DriftObjectEntry:
    observed_sha256 = _sha256_from_info(info)
    status: DriftObjectStatus
    if pointer.size_bytes is not None and info.size_bytes != pointer.size_bytes:
        status = "mismatch"
    elif pointer.content_sha256 is None or observed_sha256 is None:
        status = "unknown"
    elif observed_sha256.lower() == pointer.content_sha256.lower():
        status = "match"
    else:
        status = "mismatch"
    return _drift_entry(
        pointer=pointer,
        status=status,
        observed_sha256=observed_sha256,
        actor=actor,
        size_bytes=info.size_bytes,
    )


def _drift_entry(
    *,
    pointer: ObjectPointer,
    status: DriftObjectStatus,
    observed_sha256: str | None,
    actor: VerifiedActor,
    size_bytes: int | None = None,
) -> DriftObjectEntry:
    return DriftObjectEntry(
        object_key=pointer.object_name,
        file_id=pointer.object_name,
        name=pointer.object_name.rsplit("/", 1)[-1] or pointer.object_name,
        expected_sha256=pointer.content_sha256 or "",
        observed_sha256=observed_sha256,
        status=status,
        size_bytes=size_bytes if size_bytes is not None else pointer.size_bytes,
        via_action_id=None,
        via_actor_id=actor.user_id,
    )


def _sha256_from_info(info: ObjectInfo) -> str | None:
    metadata = info.metadata or {}
    metadata_sha = metadata.get(_SHA256_METADATA_KEY)
    if metadata_sha:
        return metadata_sha.lower()
    integrity = info.integrity or ""
    if integrity.startswith("sha256:"):
        return integrity.removeprefix("sha256:").lower()
    return None


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[resolved] = lock
        return lock


def _init_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS protected_roots (
            root_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            container TEXT NOT NULL,
            prefix TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            PRIMARY KEY (tenant_id, root_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS protected_roots_scope
            ON protected_roots (tenant_id, provider, container, prefix);

        CREATE TABLE IF NOT EXISTS generations (
            generation_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            root_id TEXT NOT NULL,
            manifest_digest TEXT NOT NULL,
            created_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            PRIMARY KEY (tenant_id, generation_id)
        );
        CREATE INDEX IF NOT EXISTS generations_by_root
            ON generations (tenant_id, root_id, created_at);
        """
    )


def _json_dumps(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _json_loads_object(raw: str) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        msg = "expected JSON object"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _protected_root_to_json(root: ProtectedRoot) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "root_id": root.root_id,
        "tenant": _tenant_to_json(root.tenant),
        "provider": root.provider,
        "container": root.container,
        "prefix": root.prefix,
        "display_name": root.display_name,
        "protected_by": _actor_to_json(root.protected_by),
        "created_at": _datetime_to_json(root.created_at),
        "updated_at": _datetime_to_json(root.updated_at),
        "metadata": dict(root.metadata),
    }


def _protected_root_from_json(data: Mapping[str, object]) -> ProtectedRoot:
    return ProtectedRoot(
        root_id=_required_str(data, "root_id"),
        tenant=_tenant_from_json(_required_mapping(data, "tenant")),
        provider=cast("ProviderName", _required_str(data, "provider")),
        container=_required_str(data, "container"),
        prefix=_required_str(data, "prefix"),
        display_name=_required_str(data, "display_name"),
        protected_by=_actor_from_json(_required_mapping(data, "protected_by")),
        created_at=_datetime_from_json(_required_str(data, "created_at")),
        updated_at=_datetime_from_json(_required_str(data, "updated_at")),
        metadata=_required_mapping(data, "metadata"),
    )


def _generation_to_json(generation: Generation) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "generation_id": generation.generation_id,
        "tenant": _tenant_to_json(generation.tenant),
        "root_id": generation.root_id,
        "manifest_artifact_id": generation.manifest_artifact_id,
        "manifest_digest": generation.manifest_digest,
        "object_count": generation.object_count,
        "total_bytes": generation.total_bytes,
        "status": generation.status,
        "created_by": _actor_to_json(generation.created_by),
        "created_at": _datetime_to_json(generation.created_at),
        "base_generation_id": generation.base_generation_id,
        "metadata": dict(generation.metadata),
    }


def _generation_from_json(data: Mapping[str, object]) -> Generation:
    return Generation(
        generation_id=_required_str(data, "generation_id"),
        tenant=_tenant_from_json(_required_mapping(data, "tenant")),
        root_id=_required_str(data, "root_id"),
        manifest_artifact_id=_required_str(data, "manifest_artifact_id"),
        manifest_digest=_required_str(data, "manifest_digest"),
        object_count=_required_int(data, "object_count"),
        total_bytes=_required_int(data, "total_bytes"),
        status=cast("GenerationStatus", _required_str(data, "status")),
        created_by=_actor_from_json(_required_mapping(data, "created_by")),
        created_at=_datetime_from_json(_required_str(data, "created_at")),
        base_generation_id=_optional_str(data, "base_generation_id"),
        metadata=_required_mapping(data, "metadata"),
    )


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


def _tenant_to_json(tenant: TenantIdentity) -> dict[str, object]:
    return {
        "platform": tenant.platform,
        "workspace_id": tenant.workspace_id,
    }


def _tenant_from_json(data: Mapping[str, object]) -> TenantIdentity:
    return TenantIdentity(
        platform=_required_str(data, "platform"),
        workspace_id=_required_str(data, "workspace_id"),
    )


def _actor_to_json(actor: VerifiedActor) -> dict[str, object]:
    return {
        "tenant": _tenant_to_json(actor.tenant),
        "user_id": actor.user_id,
        "auth_source": actor.auth_source,
        "bridge_id": actor.bridge_id,
        "verified_at": _datetime_to_json(actor.verified_at),
    }


def _actor_from_json(data: Mapping[str, object]) -> VerifiedActor:
    return VerifiedActor(
        tenant=_tenant_from_json(_required_mapping(data, "tenant")),
        user_id=_required_str(data, "user_id"),
        auth_source=cast("ActorAuthSource", _required_str(data, "auth_source")),
        bridge_id=_optional_str(data, "bridge_id"),
        verified_at=_datetime_from_json(_required_str(data, "verified_at")),
    )


def _datetime_to_json(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _datetime_from_json(value: str) -> datetime:
    return datetime.fromisoformat(value)


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


def _required_mapping(data: Mapping[str, object], key: str) -> dict[str, object]:
    value = data.get(key)
    if not isinstance(value, dict):
        msg = f"expected object field {key!r}"
        raise TypeError(msg)
    return cast("dict[str, object]", value)
