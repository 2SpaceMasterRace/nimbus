"""Proof receipt helpers for Nimbus evidence artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from nimbus_runtime.domain import (
        Artifact,
        ArtifactPayload,
        ProofReceipt,
        SessionEvent,
        TenantIdentity,
    )

type JsonValue = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)


_DIGEST_PREFIX = "sha256:"
_RECEIPT_PREFIX = "rec-"


def canonical_json_bytes(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes for a Nimbus proof value."""
    return json.dumps(
        _to_jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def digest_value(value: object) -> str:
    """Return a stable SHA-256 digest string for a JSON-like value."""
    return _DIGEST_PREFIX + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def artifact_payload_digest(payload: ArtifactPayload) -> str:
    """Return the deterministic digest of one artifact payload."""
    return digest_value(payload)


def ensure_artifact_digest(artifact: Artifact) -> Artifact:
    """Return ``artifact`` with a payload digest populated and checked."""
    computed = artifact_payload_digest(artifact.payload)
    if artifact.payload_digest is None:
        return replace(artifact, payload_digest=computed)
    if artifact.payload_digest != computed:
        msg = (
            f"artifact {artifact.artifact_id!r} payload digest mismatch: "
            f"stored {artifact.payload_digest}, computed {computed}"
        )
        raise ValueError(msg)
    return artifact


def deterministic_receipt_id(  # noqa: PLR0913 - receipt identity names each binding explicitly.
    *,
    tenant: TenantIdentity,
    subject: str,
    task_id: str | None,
    action_id: str | None,
    manifest_artifact_id: str | None,
    verifier_artifact_id: str | None,
    linked_artifact_ids: Sequence[str],
) -> str:
    """Return the retry-stable proof receipt ID for one evidence bundle."""
    seed = {
        "tenant_id": tenant.tenant_id,
        "subject": subject,
        "task_id": task_id,
        "action_id": action_id,
        "manifest_artifact_id": manifest_artifact_id,
        "verifier_artifact_id": verifier_artifact_id,
        "linked_artifact_ids": sorted(set(linked_artifact_ids)),
    }
    digest = hashlib.sha256(canonical_json_bytes(seed)).hexdigest()
    return f"{_RECEIPT_PREFIX}{digest[:32]}"


def validate_proof_receipt_links(
    *,
    receipt: ProofReceipt,
    artifacts_by_id: Mapping[str, Artifact],
) -> tuple[str, ...]:
    """Return validation failures for a receipt and its linked artifacts."""
    failures: list[str] = []
    if not receipt.linked_artifact_ids:
        failures.append("receipt links no evidence artifacts")
    for artifact_id in receipt.linked_artifact_ids:
        artifact = artifacts_by_id.get(artifact_id)
        if artifact is None:
            failures.append(f"linked artifact {artifact_id!r} is missing")
            continue
        try:
            checked = ensure_artifact_digest(artifact)
        except ValueError as exc:
            failures.append(str(exc))
            continue
        expected = receipt.artifact_digests.get(artifact_id)
        if expected is None:
            failures.append(f"receipt lacks digest for artifact {artifact_id!r}")
        elif expected != checked.payload_digest:
            failures.append(
                f"linked artifact {artifact_id!r} digest changed: "
                f"receipt {expected}, current {checked.payload_digest}"
            )
    failures.extend(
        f"receipt has unlinked digest for artifact {artifact_id!r}"
        for artifact_id in receipt.artifact_digests
        if artifact_id not in receipt.linked_artifact_ids
    )
    if (
        receipt.manifest_artifact_id is not None
        and receipt.manifest_artifact_id not in receipt.linked_artifact_ids
    ):
        failures.append("manifest artifact is not linked")
    if (
        receipt.verifier_artifact_id is not None
        and receipt.verifier_artifact_id not in receipt.linked_artifact_ids
    ):
        failures.append("verifier artifact is not linked")
    return tuple(failures)


def event_range(events: Sequence[SessionEvent]) -> tuple[int | None, int | None]:
    """Return the inclusive durable sequence range for a set of events."""
    if not events:
        return (None, None)
    sequences = [event.sequence for event in events]
    return (min(sequences), max(sequences))


def _to_jsonable(value: object) -> JsonValue:  # noqa: PLR0911
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _to_jsonable(asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _to_jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, frozenset | set):
        return sorted((_to_jsonable(item) for item in value), key=str)
    return str(value)


def to_jsonable(value: object) -> JsonValue:
    """Return a JSON-compatible representation for proof CLI output."""
    return _to_jsonable(value)
