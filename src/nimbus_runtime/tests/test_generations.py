"""Tests for protected roots and generation manifests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from cloud_storage_api import ObjectInfo
from cloud_storage_api.exceptions import ObjectNotFoundError
from hypothesis import given
from hypothesis import strategies as st
from nimbus_runtime.domain import (
    GenerationManifest,
    ObjectPointer,
    ProtectedRoot,
    TenantIdentity,
    VerifiedActor,
)
from nimbus_runtime.generations import (
    FileGenerationStore,
    FileProtectedRootStore,
    canonicalize_object_pointers,
    create_generation,
    diff_generation_manifests,
    manifest_digest_for,
    verify_generation_manifest,
)
from nimbus_runtime.stores import FileArtifactStore, FileSessionEventStore

pytestmark = pytest.mark.unit


class _FakeStorage:
    def __init__(self, objects: list[ObjectInfo]) -> None:
        self._objects = {obj.object_name: obj for obj in objects}

    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        return [
            obj
            for obj in self._objects.values()
            if obj.object_name.startswith(prefix) and container == "bucket"
        ]

    def get_file_info(self, _container: str, object_name: str) -> ObjectInfo:
        try:
            return self._objects[object_name]
        except KeyError as exc:
            raise ObjectNotFoundError(object_name) from exc


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="cli", workspace_id="demo")


def _actor(tenant: TenantIdentity) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id="cli",
        auth_source="cli_local",
        bridge_id=None,
        verified_at=datetime(2026, 5, 21, tzinfo=UTC),
    )


def _manual_root() -> tuple[TenantIdentity, VerifiedActor, ProtectedRoot]:
    tenant = _tenant()
    actor = _actor(tenant)
    return (
        tenant,
        actor,
        ProtectedRoot(
            root_id="root-demo",
            tenant=tenant,
            provider="s3",
            container="bucket",
            prefix="docs/",
            display_name="demo docs",
            protected_by=actor,
            created_at=actor.verified_at,
            updated_at=actor.verified_at,
            metadata={},
        ),
    )


def _root(tmp_path: Path) -> tuple[TenantIdentity, VerifiedActor, ProtectedRoot]:
    tenant = _tenant()
    actor = _actor(tenant)
    root = FileProtectedRootStore(tmp_path).protect(
        tenant=tenant,
        provider="s3",
        container="bucket",
        prefix="docs",
        display_name="demo docs",
        actor=actor,
        now=actor.verified_at,
    )
    return tenant, actor, root


@given(st.permutations(["docs/a.txt", "docs/b.txt", "docs/c.txt"]))
@pytest.mark.property
def test_manifest_digest_is_stable_under_listing_order(
    names: tuple[str, ...],
) -> None:
    """Canonical manifest digests must not depend on provider listing order."""
    _, _, root = _manual_root()
    pointers = tuple(
        ObjectPointer(
            provider="s3",
            container="bucket",
            object_name=name,
            content_sha256=f"{index:064x}",
            size_bytes=index + 1,
        )
        for index, name in enumerate(names)
    )

    digest = manifest_digest_for(root=root, objects=pointers)
    reversed_digest = manifest_digest_for(root=root, objects=tuple(reversed(pointers)))

    assert digest == reversed_digest
    assert tuple(p.object_name for p in canonicalize_object_pointers(pointers)) == (
        "docs/a.txt",
        "docs/b.txt",
        "docs/c.txt",
    )


def test_create_generation_writes_manifest_and_proof(tmp_path: Path) -> None:
    """A generation commit should leave both manifest evidence and proof."""
    tenant, actor, root = _root(tmp_path)
    storage = _FakeStorage(
        [
            ObjectInfo(
                object_name="docs/b.txt",
                size_bytes=2,
                metadata={"sha256": "b" * 64},
            ),
            ObjectInfo(
                object_name="docs/a.txt",
                size_bytes=1,
                metadata={"sha256": "a" * 64},
            ),
        ]
    )

    result = create_generation(
        root=root,
        storage=storage,  # type: ignore[arg-type]
        artifact_store=FileArtifactStore(tmp_path),
        generation_store=FileGenerationStore(tmp_path),
        actor=actor,
        session_id="sess-generation",
        now=actor.verified_at,
    )

    assert result.generation.object_count == 2
    assert (
        result.generation.manifest_artifact_id == result.manifest_artifact.artifact_id
    )
    assert result.manifest_artifact.payload_digest is not None
    assert result.proof_artifact.kind == "proof_receipt"

    stored = FileGenerationStore(tmp_path).get(
        tenant=tenant,
        generation_id=result.generation.generation_id,
    )
    assert stored == result.generation


def test_generation_diff_classifies_added_removed_changed(tmp_path: Path) -> None:
    """Generation diffs should be deterministic and object-level."""
    _, actor, root = _root(tmp_path)
    artifact_store = FileArtifactStore(tmp_path)
    generation_store = FileGenerationStore(tmp_path)
    before = create_generation(
        root=root,
        storage=_FakeStorage(
            [
                ObjectInfo(
                    object_name="docs/a.txt",
                    size_bytes=1,
                    metadata={"sha256": "a" * 64},
                ),
                ObjectInfo(
                    object_name="docs/old.txt",
                    size_bytes=3,
                    metadata={"sha256": "c" * 64},
                ),
            ]
        ),  # type: ignore[arg-type]
        artifact_store=artifact_store,
        generation_store=generation_store,
        actor=actor,
        session_id="sess-generation",
        now=actor.verified_at,
    )
    after = create_generation(
        root=root,
        storage=_FakeStorage(
            [
                ObjectInfo(
                    object_name="docs/a.txt",
                    size_bytes=2,
                    metadata={"sha256": "b" * 64},
                ),
                ObjectInfo(
                    object_name="docs/new.txt",
                    size_bytes=4,
                    metadata={"sha256": "d" * 64},
                ),
            ]
        ),  # type: ignore[arg-type]
        artifact_store=artifact_store,
        generation_store=generation_store,
        actor=actor,
        session_id="sess-generation",
        now=actor.verified_at,
    )

    assert isinstance(before.manifest_artifact.payload, GenerationManifest)
    assert isinstance(after.manifest_artifact.payload, GenerationManifest)
    diff = diff_generation_manifests(
        before=before.manifest_artifact.payload,
        after=after.manifest_artifact.payload,
    )

    assert (diff.added_count, diff.removed_count, diff.changed_count) == (1, 1, 1)
    assert [entry.status for entry in diff.entries] == [
        "changed",
        "added",
        "removed",
    ]


def test_verify_generation_manifest_reports_missing_and_unknown(
    tmp_path: Path,
) -> None:
    """Verification fails closed in strict mode when hashes are unavailable."""
    _, actor, root = _root(tmp_path)
    result = create_generation(
        root=root,
        storage=_FakeStorage(
            [
                ObjectInfo(
                    object_name="docs/hashed.txt",
                    size_bytes=1,
                    metadata={"sha256": "a" * 64},
                ),
                ObjectInfo(object_name="docs/no-hash.txt", size_bytes=1),
            ]
        ),  # type: ignore[arg-type]
        artifact_store=FileArtifactStore(tmp_path),
        generation_store=FileGenerationStore(tmp_path),
        actor=actor,
        session_id="sess-generation",
        now=actor.verified_at,
    )
    live_storage = _FakeStorage(
        [ObjectInfo(object_name="docs/no-hash.txt", size_bytes=1)]
    )

    assert isinstance(result.manifest_artifact.payload, GenerationManifest)
    report = verify_generation_manifest(
        manifest=result.manifest_artifact.payload,
        manifest_artifact_id=result.manifest_artifact.artifact_id,
        storage=live_storage,  # type: ignore[arg-type]
        artifact_store=FileArtifactStore(tmp_path),
        event_store=FileSessionEventStore(tmp_path),
        actor=actor,
        session_id="sess-generation",
        strict=True,
        now=actor.verified_at,
    )

    assert report.has_drift is True
    assert report.missing_count == 1
    assert report.unknown_count == 1
