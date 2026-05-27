"""Candidate cleanup plan generation from durable manifests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from nimbus_runtime.domain import (
    Artifact,
    GenerationManifest,
    ManifestReport,
    Plan,
    PlanRiskLevel,
    PlanStatus,
    VerifiedActor,
)
from nimbus_runtime.proof import digest_value

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_MIN_DUPLICATE_GROUP_SIZE = 2


@dataclass(frozen=True, slots=True)
class CleanupObject:
    """One manifest object eligible for duplicate cleanup planning."""

    object_name: str
    display_name: str
    content_sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    """One content-hash group with more than one object."""

    content_sha256: str
    keep: CleanupObject
    duplicates: tuple[CleanupObject, ...]


def duplicate_groups_from_manifest(
    manifest: GenerationManifest | ManifestReport,
) -> tuple[DuplicateGroup, ...]:
    """Return deterministic duplicate groups from a manifest payload."""
    by_hash: dict[str, list[CleanupObject]] = {}
    for item in _objects_from_manifest(manifest):
        by_hash.setdefault(item.content_sha256, []).append(item)
    groups: list[DuplicateGroup] = []
    for content_sha256, objects in sorted(by_hash.items()):
        ordered = tuple(sorted(objects, key=lambda obj: obj.object_name))
        if len(ordered) < _MIN_DUPLICATE_GROUP_SIZE:
            continue
        groups.append(
            DuplicateGroup(
                content_sha256=content_sha256,
                keep=ordered[0],
                duplicates=ordered[1:],
            )
        )
    return tuple(groups)


def build_cleanup_plan_candidates(
    *,
    manifest_artifact: Artifact,
    actor: VerifiedActor,
    now: datetime | None = None,
) -> tuple[Plan, ...]:
    """Build candidate cleanup plans for duplicate objects in one manifest."""
    manifest = manifest_artifact.payload
    if not isinstance(manifest, GenerationManifest | ManifestReport):
        msg = "cleanup candidates require a manifest artifact"
        raise TypeError(msg)
    groups = duplicate_groups_from_manifest(manifest)
    if not groups:
        return ()
    timestamp = now or datetime.now(UTC)
    candidate_group_id = _candidate_group_id(
        manifest_artifact_id=manifest_artifact.artifact_id,
        groups=groups,
    )
    strategies = (
        (
            "archive_before_delete",
            "Archive duplicate copies, then delete originals",
            PlanRiskLevel.DESTRUCTIVE,
            _archive_restore_story(manifest_artifact.artifact_id),
        ),
        (
            "delete_extra_copies",
            "Delete duplicate copies directly",
            PlanRiskLevel.DESTRUCTIVE,
            "Restore requires S3 versioning or independent backup evidence.",
        ),
        (
            "report_only",
            "Keep all files and record duplicate report only",
            PlanRiskLevel.READ_ONLY,
            "No restore required because no storage mutation is planned.",
        ),
    )
    plans: list[Plan] = []
    for strategy, title, risk, restore_story in strategies:
        metadata = _candidate_metadata(
            candidate_group_id=candidate_group_id,
            strategy=strategy,
            manifest=manifest,
            manifest_artifact=manifest_artifact,
            groups=groups,
            restore_story=restore_story,
        )
        plan_id = _plan_id(candidate_group_id=candidate_group_id, strategy=strategy)
        plans.append(
            Plan(
                plan_id=plan_id,
                tenant=manifest_artifact.tenant,
                session_id=manifest_artifact.session_id,
                task_id=None,
                action_id=None,
                created_by=actor,
                status=PlanStatus.PROPOSED,
                risk_level=risk,
                title=title,
                summary=_summary_for(strategy=strategy, groups=groups),
                target=None,
                estimated_count=sum(len(group.duplicates) for group in groups),
                estimated_bytes=sum(
                    item.size_bytes for group in groups for item in group.duplicates
                ),
                idempotency_key=f"cleanup:{candidate_group_id}:{strategy}",
                metadata=metadata,
                created_at=timestamp,
                updated_at=timestamp,
                expires_at=None,
            )
        )
    return tuple(plans)


def _objects_from_manifest(
    manifest: GenerationManifest | ManifestReport,
) -> tuple[CleanupObject, ...]:
    if isinstance(manifest, GenerationManifest):
        return tuple(
            CleanupObject(
                object_name=pointer.object_name,
                display_name=PurePosixPath(pointer.object_name).name,
                content_sha256=pointer.content_sha256,
                size_bytes=pointer.size_bytes or 0,
            )
            for pointer in manifest.objects
            if pointer.content_sha256
        )
    return tuple(
        CleanupObject(
            object_name=entry.object_key,
            display_name=entry.name,
            content_sha256=entry.sha256_hex,
            size_bytes=entry.size_bytes,
        )
        for entry in manifest.object_entries
        if entry.sha256_hex
    )


def _candidate_metadata(  # noqa: PLR0913
    *,
    candidate_group_id: str,
    strategy: str,
    manifest: GenerationManifest | ManifestReport,
    manifest_artifact: Artifact,
    groups: Sequence[DuplicateGroup],
    restore_story: str,
) -> Mapping[str, object]:
    storage_scope = _manifest_scope(manifest)
    return {
        "operation": "candidate_cleanup",
        "candidate_group_id": candidate_group_id,
        "candidate_strategy": strategy,
        "manifest_artifact_id": manifest_artifact.artifact_id,
        **storage_scope,
        "restore_story": restore_story,
        "duplicate_group_count": len(groups),
        "target_count": sum(len(group.duplicates) for group in groups),
        "duplicate_groups": [
            {
                "content_sha256": group.content_sha256,
                "keep": _object_metadata(group.keep),
                "duplicates": [_object_metadata(item) for item in group.duplicates],
            }
            for group in groups
        ],
    }


def _manifest_scope(manifest: GenerationManifest | ManifestReport) -> dict[str, object]:
    if isinstance(manifest, GenerationManifest):
        return {
            "provider": manifest.provider,
            "container": manifest.container,
            "prefix": manifest.prefix,
            "generation_id": manifest.generation_id,
            "root_id": manifest.root_id,
        }
    return {
        "provider": "s3",
        "container": manifest.destination_container,
        "prefix": manifest.destination_prefix,
        "channel_id": manifest.channel_id,
    }


def _object_metadata(obj: CleanupObject) -> dict[str, object]:
    return {
        "object_name": obj.object_name,
        "display_name": obj.display_name,
        "content_sha256": obj.content_sha256,
        "size_bytes": obj.size_bytes,
    }


def _candidate_group_id(
    *,
    manifest_artifact_id: str,
    groups: Sequence[DuplicateGroup],
) -> str:
    digest = digest_value(
        {
            "manifest_artifact_id": manifest_artifact_id,
            "groups": [
                {
                    "content_sha256": group.content_sha256,
                    "keep": group.keep.object_name,
                    "duplicates": [item.object_name for item in group.duplicates],
                }
                for group in groups
            ],
        }
    )
    return f"cand-{digest.removeprefix('sha256:')[:24]}"


def _plan_id(*, candidate_group_id: str, strategy: str) -> str:
    digest = digest_value(
        {"candidate_group_id": candidate_group_id, "strategy": strategy}
    )
    return f"plan-{digest.removeprefix('sha256:')[:24]}"


def _summary_for(*, strategy: str, groups: Sequence[DuplicateGroup]) -> str:
    duplicates = sum(len(group.duplicates) for group in groups)
    if strategy == "archive_before_delete":
        return (
            f"Archive and remove {duplicates} duplicate objects from "
            f"{len(groups)} groups."
        )
    if strategy == "delete_extra_copies":
        return f"Delete {duplicates} duplicate objects from {len(groups)} groups."
    return f"Report {len(groups)} duplicate groups without mutating storage."


def _archive_restore_story(manifest_artifact_id: str) -> str:
    return (
        "Archive each duplicate under a Nimbus-owned archive prefix before delete; "
        f"verify against manifest {manifest_artifact_id} before any destructive step."
    )
