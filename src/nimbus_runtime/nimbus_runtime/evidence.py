"""Content-addressed evidence payload exports and compact previews."""

from __future__ import annotations

import gzip
import hashlib
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from nimbus_runtime.proof import (
    artifact_payload_digest,
    canonical_json_bytes,
    digest_value,
    to_jsonable,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from nimbus_runtime.domain import Artifact, TenantIdentity

EVIDENCE_SCHEMA_VERSION = 1
_DIGEST_PREFIX = "sha256:"
_HOT_RETENTION = "hot"
_GZIP_ENCODING = "gzip"
_PREVIEW_FIELD_LIMIT = 6
_PREVIEW_VALUE_LIMIT = 96
_PREVIEW_PRIORITY_FIELDS = ("status", "outcome", "remote_path")


@dataclass(frozen=True, slots=True)
class EvidenceObjectRecord:
    """Pointer and verification metadata for one exported artifact payload."""

    schema_version: int
    artifact_id: str
    tenant_id: str
    session_id: str
    kind: str
    payload_digest: str
    uncompressed_digest: str
    stored_digest: str
    object_uri: str
    content_encoding: str
    uncompressed_bytes: int
    stored_bytes: int
    retention_class: str
    verification_status: str
    exported_at: datetime


@dataclass(frozen=True, slots=True)
class EvidencePreview:
    """Small, safe summary for an evidence artifact without loading bundles."""

    schema_version: int
    artifact_id: str
    kind: str
    payload_digest: str
    evidence_uri: str
    evidence_available: bool
    title: str
    summary: str
    fields: Mapping[str, str]
    generated_at: datetime
    next_step: str


@dataclass(frozen=True, slots=True)
class EvidenceBundleRecord:
    """Compacted index of exported evidence payload objects."""

    schema_version: int
    bundle_id: str
    tenant_id: str
    artifact_count: int
    payload_digests: tuple[str, ...]
    source_object_uris: tuple[str, ...]
    bundle_uri: str
    stored_digest: str
    stored_bytes: int
    verification_status: str
    compacted_at: datetime
    next_step: str


def export_artifact_payload(
    *,
    artifact: Artifact,
    root: Path,
    exported_at: datetime,
    retention_class: str = _HOT_RETENTION,
) -> EvidenceObjectRecord:
    """Write canonical artifact payload bytes to a tenant-scoped object path."""
    payload_digest = artifact.payload_digest or artifact_payload_digest(
        artifact.payload
    )
    payload_bytes = canonical_json_bytes(artifact.payload)
    uncompressed_digest = _digest_bytes(payload_bytes)
    if payload_digest != uncompressed_digest:
        msg = (
            f"artifact {artifact.artifact_id!r} payload digest mismatch: "
            f"artifact {payload_digest}, canonical {uncompressed_digest}"
        )
        raise ValueError(msg)
    stored_bytes = gzip.compress(payload_bytes, mtime=0)
    stored_digest = _digest_bytes(stored_bytes)
    object_path = _payload_object_path(
        root=root,
        tenant=artifact.tenant,
        payload_digest=payload_digest,
    )
    _write_content_addressed(path=object_path, content=stored_bytes)
    if _digest_bytes(object_path.read_bytes()) != stored_digest:
        msg = f"evidence object verification failed for {artifact.artifact_id!r}"
        raise ValueError(msg)
    return EvidenceObjectRecord(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        artifact_id=artifact.artifact_id,
        tenant_id=artifact.tenant.tenant_id,
        session_id=artifact.session_id,
        kind=artifact.kind,
        payload_digest=payload_digest,
        uncompressed_digest=uncompressed_digest,
        stored_digest=stored_digest,
        object_uri=_payload_object_uri(
            tenant=artifact.tenant,
            payload_digest=payload_digest,
        ),
        content_encoding=_GZIP_ENCODING,
        uncompressed_bytes=len(payload_bytes),
        stored_bytes=len(stored_bytes),
        retention_class=retention_class,
        verification_status="verified",
        exported_at=exported_at,
    )


def verify_evidence_object(*, record: EvidenceObjectRecord, root: Path) -> bool:
    """Return whether an exported payload object still matches its record."""
    object_path = _payload_object_path_for_record(root=root, record=record)
    if not object_path.exists():
        return False
    stored = object_path.read_bytes()
    if _digest_bytes(stored) != record.stored_digest:
        return False
    try:
        uncompressed = gzip.decompress(stored)
    except OSError:
        return False
    return _digest_bytes(uncompressed) == record.uncompressed_digest


def preview_artifact(
    *,
    artifact: Artifact,
    root: Path,
    generated_at: datetime,
) -> EvidencePreview:
    """Return a compact human-reviewable artifact preview."""
    payload_digest = artifact.payload_digest or artifact_payload_digest(
        artifact.payload
    )
    evidence_path = _payload_object_path(
        root=root,
        tenant=artifact.tenant,
        payload_digest=payload_digest,
    )
    evidence_available = evidence_path.exists()
    payload = to_jsonable(artifact.payload)
    fields = _preview_fields(payload if isinstance(payload, dict) else {})
    return EvidencePreview(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        artifact_id=artifact.artifact_id,
        kind=artifact.kind,
        payload_digest=payload_digest,
        evidence_uri=_payload_object_uri(
            tenant=artifact.tenant,
            payload_digest=payload_digest,
        ),
        evidence_available=evidence_available,
        title=f"{artifact.kind} evidence {artifact.artifact_id}",
        summary=_preview_summary(kind=artifact.kind, fields=fields),
        fields=fields,
        generated_at=generated_at,
        next_step=(
            "Export the artifact with `nimbus evidence export` before compaction."
            if not evidence_available
            else "Use `nimbus evidence compact` to bundle exported evidence."
        ),
    )


def compact_evidence_records(
    *,
    records: Sequence[EvidenceObjectRecord],
    root: Path,
    compacted_at: datetime,
) -> EvidenceBundleRecord:
    """Write a compressed bundle index for exported evidence records."""
    if not records:
        msg = "at least one evidence record is required for compaction"
        raise ValueError(msg)
    tenant_id = records[0].tenant_id
    if any(record.tenant_id != tenant_id for record in records):
        msg = "cannot compact evidence records from multiple tenants"
        raise ValueError(msg)
    failed = [
        record.object_uri
        for record in records
        if not verify_evidence_object(record=record, root=root)
    ]
    if failed:
        msg = "cannot compact missing or corrupt evidence objects: " + ", ".join(failed)
        raise ValueError(msg)
    payload_digests = tuple(record.payload_digest for record in records)
    source_uris = tuple(record.object_uri for record in records)
    bundle_id = _bundle_id(tenant_id=tenant_id, payload_digests=payload_digests)
    index = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "tenant_id": tenant_id,
        "payload_digests": list(payload_digests),
        "source_object_uris": list(source_uris),
    }
    bundle_bytes = gzip.compress(canonical_json_bytes(index), mtime=0)
    stored_digest = _digest_bytes(bundle_bytes)
    bundle_path = _bundle_path(
        root=root,
        tenant_id=tenant_id,
        stored_digest=stored_digest,
    )
    _write_content_addressed(path=bundle_path, content=bundle_bytes)
    return EvidenceBundleRecord(
        schema_version=EVIDENCE_SCHEMA_VERSION,
        bundle_id=bundle_id,
        tenant_id=tenant_id,
        artifact_count=len(records),
        payload_digests=payload_digests,
        source_object_uris=source_uris,
        bundle_uri=_bundle_uri(tenant_id=tenant_id, stored_digest=stored_digest),
        stored_digest=stored_digest,
        stored_bytes=len(bundle_bytes),
        verification_status="verified",
        compacted_at=compacted_at,
        next_step="Keep source objects until retention policy permits deletion.",
    )


def evidence_record_to_json(record: EvidenceObjectRecord) -> dict[str, object]:
    """Return a stable JSON object for an evidence object record."""
    return cast("dict[str, object]", to_jsonable(record))


def evidence_preview_to_json(preview: EvidencePreview) -> dict[str, object]:
    """Return a stable JSON object for an evidence preview."""
    return cast("dict[str, object]", to_jsonable(preview))


def evidence_bundle_to_json(bundle: EvidenceBundleRecord) -> dict[str, object]:
    """Return a stable JSON object for an evidence compaction bundle."""
    return cast("dict[str, object]", to_jsonable(bundle))


def _write_content_addressed(*, path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            msg = f"content-addressed object collision at {path}"
            raise ValueError(msg)
        return
    tmp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)


def _payload_object_path(
    *,
    root: Path,
    tenant: TenantIdentity,
    payload_digest: str,
) -> Path:
    return _payload_path_for_tenant_id(
        root=root,
        tenant_id=tenant.tenant_id,
        payload_digest=payload_digest,
    )


def _payload_object_path_for_record(
    *,
    root: Path,
    record: EvidenceObjectRecord,
) -> Path:
    return _payload_path_for_tenant_id(
        root=root,
        tenant_id=record.tenant_id,
        payload_digest=record.payload_digest,
    )


def _payload_path_for_tenant_id(
    *,
    root: Path,
    tenant_id: str,
    payload_digest: str,
) -> Path:
    digest = _digest_hex(payload_digest)
    return (
        root
        / _tenant_slug(tenant_id)
        / "objects"
        / "sha256"
        / digest[:2]
        / f"{digest}.payload.json.gz"
    )


def _bundle_path(*, root: Path, tenant_id: str, stored_digest: str) -> Path:
    digest = _digest_hex(stored_digest)
    return (
        root
        / _tenant_slug(tenant_id)
        / "bundles"
        / "sha256"
        / digest[:2]
        / f"{digest}.bundle.json.gz"
    )


def _payload_object_uri(*, tenant: TenantIdentity, payload_digest: str) -> str:
    return (
        f"nimbus-evidence://{_tenant_slug(tenant.tenant_id)}/objects/{payload_digest}"
    )


def _bundle_uri(*, tenant_id: str, stored_digest: str) -> str:
    return f"nimbus-evidence://{_tenant_slug(tenant_id)}/bundles/{stored_digest}"


def _digest_bytes(content: bytes) -> str:
    return _DIGEST_PREFIX + hashlib.sha256(content).hexdigest()


def _digest_hex(value: str) -> str:
    if not value.startswith(_DIGEST_PREFIX):
        msg = f"expected sha256 digest, got {value!r}"
        raise ValueError(msg)
    return value.removeprefix(_DIGEST_PREFIX)


def _tenant_slug(tenant_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in tenant_id)


def _bundle_id(*, tenant_id: str, payload_digests: Sequence[str]) -> str:
    digest = digest_value(
        {
            "tenant_id": tenant_id,
            "payload_digests": sorted(payload_digests),
        }
    )
    return f"bundle-{digest.removeprefix(_DIGEST_PREFIX)[:24]}"


def _preview_fields(payload: Mapping[str, object]) -> dict[str, str]:
    fields: dict[str, str] = {}
    ordered_keys = [key for key in _PREVIEW_PRIORITY_FIELDS if key in payload] + sorted(
        key for key in payload if key not in _PREVIEW_PRIORITY_FIELDS
    )
    for key in ordered_keys:
        if key in {"schema_version", "tenant"}:
            continue
        value = payload[key]
        if isinstance(value, str | int | float | bool) or value is None:
            fields[key] = _short_value(value)
        elif isinstance(value, list | tuple):
            fields[key] = f"{len(value)} items"
        elif isinstance(value, dict):
            fields[key] = f"{len(value)} fields"
        if len(fields) >= _PREVIEW_FIELD_LIMIT:
            break
    return fields


def _short_value(value: object) -> str:
    text = "null" if value is None else str(value)
    return (
        text
        if len(text) <= _PREVIEW_VALUE_LIMIT
        else text[: _PREVIEW_VALUE_LIMIT - 3] + "..."
    )


def _preview_summary(*, kind: str, fields: Mapping[str, str]) -> str:
    if kind == "provider_health" and "status" in fields:
        return f"Provider health is {fields['status']}."
    if kind == "upload_report" and "remote_path" in fields:
        return f"Upload evidence for {fields['remote_path']}."
    if kind == "repair_receipt" and "outcome" in fields:
        return f"Replica repair {fields['outcome']}."
    if kind == "proof_receipt" and "outcome" in fields:
        return f"Proof receipt outcome is {fields['outcome']}."
    return f"{kind} evidence is available for review."


__all__ = [
    "EVIDENCE_SCHEMA_VERSION",
    "EvidenceBundleRecord",
    "EvidenceObjectRecord",
    "EvidencePreview",
    "compact_evidence_records",
    "evidence_bundle_to_json",
    "evidence_preview_to_json",
    "evidence_record_to_json",
    "export_artifact_payload",
    "preview_artifact",
    "verify_evidence_object",
]
