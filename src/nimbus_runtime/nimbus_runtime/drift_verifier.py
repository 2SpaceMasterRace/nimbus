"""Manifest drift verifier: detect external mutation of S3 objects.

Compares each object in a ``ManifestReport`` against live S3 storage and
reports which objects match, have drifted, have been deleted, or are too large
to verify without metadata.
"""

from __future__ import annotations

import hashlib
import tempfile
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from cloud_storage_api.exceptions import (
    AuthenticationError,
    ContainerNotFoundError,
    ObjectNotFoundError,
    StorageBackendError,
)

from nimbus_runtime.domain import (
    Artifact,
    DriftObjectEntry,
    DriftObjectStatus,
    DriftReport,
    ManifestReport,
    VerifiedActor,
)

if TYPE_CHECKING:
    from cloud_storage_api.client import CloudStorageClient

    from nimbus_runtime.stores import ArtifactStore, SessionEventStore

_SHA256_METADATA_KEY = "sha256"
_STREAM_HASH_MAX_BYTES = 100 * 1024 * 1024


def _sha256_file(path: str) -> str:
    """Return the lowercase hex SHA-256 digest of a local file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:  # noqa: PTH123
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_object(
    *,
    storage: CloudStorageClient,
    container: str,
    object_key: str,
    expected_sha256: str,
) -> tuple[str | None, int | None, DriftObjectStatus]:
    """Return (observed_sha256_or_None, size_bytes_or_None, status)."""
    info = storage.get_file_info(container, object_key)
    size = info.size_bytes

    if info.metadata and _SHA256_METADATA_KEY in info.metadata:
        observed = info.metadata[_SHA256_METADATA_KEY].lower()
        matches = observed == expected_sha256.lower()
        status: DriftObjectStatus = "match" if matches else "mismatch"
        return observed, size, status

    if size is not None and size <= _STREAM_HASH_MAX_BYTES:
        with tempfile.NamedTemporaryFile(delete=True) as tmp:
            storage.download_file(container, object_key, tmp.name)
            observed = _sha256_file(tmp.name)
        status = "match" if observed == expected_sha256.lower() else "mismatch"
        return observed, size, status

    return None, size, "unknown"


def verify_manifest(  # noqa: PLR0913
    manifest_report: ManifestReport,
    manifest_artifact_id: str,
    storage: CloudStorageClient,
    artifact_store: ArtifactStore,
    event_store: SessionEventStore,
    *,
    actor: VerifiedActor,
    session_id: str,
    action_id: str | None = None,
    now: datetime | None = None,
    strict: bool = False,
) -> DriftReport:
    """Compare every object in *manifest_report* against live storage.

    Writes a ``drift_report`` artifact and, when drift is detected, appends a
    ``drift_detected`` session event.

    Args:
        manifest_report: The manifest to verify.
        manifest_artifact_id: Artifact ID of the source manifest.
        storage: Storage client for the destination container.
        artifact_store: Durable store to persist the drift artifact.
        event_store: Event log to record the drift event.
        actor: Verified actor initiating the check.
        session_id: Session to attach the artifact and event to.
        action_id: Optional action ID that triggered this check.
        now: Override for the current timestamp (tests).
        strict: When ``True``, treat ``unknown`` objects as drift.

    Returns:
        The completed ``DriftReport``.

    Raises:
        AuthenticationError: When the storage provider rejects credentials.
        StorageBackendError: When the provider fails unexpectedly.

    """
    checked_at = now if now is not None else datetime.now(UTC)
    container = manifest_report.destination_container
    prefix = manifest_report.destination_prefix

    entries: list[DriftObjectEntry] = []
    bucket_missing = False

    for obj in manifest_report.object_entries:
        if bucket_missing:
            entries.append(
                DriftObjectEntry(
                    object_key=obj.object_key,
                    file_id=obj.file_id,
                    name=obj.name,
                    expected_sha256=obj.sha256_hex,
                    observed_sha256=None,
                    status="bucket_missing",
                    size_bytes=None,
                    via_action_id=action_id,
                    via_actor_id=actor.user_id,
                )
            )
            continue

        try:
            observed_sha256, size_bytes, status = _check_object(
                storage=storage,
                container=container,
                object_key=obj.object_key,
                expected_sha256=obj.sha256_hex,
            )
        except AuthenticationError:
            raise
        except ContainerNotFoundError:
            bucket_missing = True
            entries.append(
                DriftObjectEntry(
                    object_key=obj.object_key,
                    file_id=obj.file_id,
                    name=obj.name,
                    expected_sha256=obj.sha256_hex,
                    observed_sha256=None,
                    status="bucket_missing",
                    size_bytes=None,
                    via_action_id=action_id,
                    via_actor_id=actor.user_id,
                )
            )
            continue
        except ObjectNotFoundError:
            entries.append(
                DriftObjectEntry(
                    object_key=obj.object_key,
                    file_id=obj.file_id,
                    name=obj.name,
                    expected_sha256=obj.sha256_hex,
                    observed_sha256=None,
                    status="missing",
                    size_bytes=None,
                    via_action_id=action_id,
                    via_actor_id=actor.user_id,
                )
            )
            continue
        except StorageBackendError:
            raise

        entries.append(
            DriftObjectEntry(
                object_key=obj.object_key,
                file_id=obj.file_id,
                name=obj.name,
                expected_sha256=obj.sha256_hex,
                observed_sha256=observed_sha256,
                status=status,
                size_bytes=size_bytes,
                via_action_id=action_id,
                via_actor_id=actor.user_id,
            )
        )

    match_count = sum(1 for e in entries if e.status == "match")
    mismatch_count = sum(1 for e in entries if e.status == "mismatch")
    missing_count = sum(1 for e in entries if e.status == "missing")
    unknown_count = sum(1 for e in entries if e.status in {"unknown", "bucket_missing"})

    has_drift = bool(
        mismatch_count or missing_count or bucket_missing or (strict and unknown_count)
    )

    report = DriftReport(
        manifest_artifact_id=manifest_artifact_id,
        tenant=actor.tenant,
        checked_at=checked_at,
        container=container,
        prefix=prefix,
        total_count=len(entries),
        match_count=match_count,
        mismatch_count=mismatch_count,
        missing_count=missing_count,
        unknown_count=unknown_count,
        bucket_missing=bucket_missing,
        has_drift=has_drift,
        entries=tuple(entries),
        via_action_id=action_id,
    )

    artifact = artifact_store.create(
        artifact=Artifact(
            artifact_id=str(uuid.uuid4()),
            tenant=actor.tenant,
            session_id=session_id,
            action_id=action_id,
            kind="drift_report",
            uri=None,
            payload=report,
            created_at=checked_at,
        ),
        actor=actor,
    )

    if has_drift:
        event_store.append(
            tenant=actor.tenant,
            session_id=session_id,
            event_type="drift_detected",
            actor=actor,
            payload={
                "manifest_artifact_id": manifest_artifact_id,
                "drift_artifact_id": artifact.artifact_id,
                "container": container,
                "prefix": prefix,
                "mismatch_count": mismatch_count,
                "missing_count": missing_count,
                "unknown_count": unknown_count if strict else 0,
                "bucket_missing": bucket_missing,
            },
        )

    return report
