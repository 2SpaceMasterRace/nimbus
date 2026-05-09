"""Unit tests for the manifest drift verifier."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from cloud_storage_api.exceptions import (
    AuthenticationError,
    ContainerNotFoundError,
    ObjectNotFoundError,
    StorageBackendError,
)
from nimbus_runtime.domain import (
    DriftReport,
    ManifestObjectEntry,
    ManifestReport,
    TenantIdentity,
    VerifiedActor,
)
from nimbus_runtime.drift_verifier import verify_manifest
from nimbus_runtime.stores import FileArtifactStore, FileSessionEventStore

if TYPE_CHECKING:
    from cloud_storage_api.models import ObjectInfo

pytestmark = pytest.mark.unit

# ── Constants ─────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
_SHA256_A = "a" * 64
_SHA256_B = "b" * 64
_CONTAINER = "my-bucket"
_PREFIX = "backups/team-2/"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="cli", workspace_id="W001")


def _actor(tenant: TenantIdentity | None = None) -> VerifiedActor:
    t = tenant or _tenant()
    return VerifiedActor(
        tenant=t,
        user_id="U001",
        auth_source="cli_local",
        bridge_id=None,
        verified_at=_NOW,
    )


def _obj_entry(
    *,
    file_id: str = "F001",
    name: str = "photo.jpg",
    object_key: str = "backups/team-2/photo.jpg",
    sha256_hex: str = _SHA256_A,
    size_bytes: int = 1024,
) -> ManifestObjectEntry:
    return ManifestObjectEntry(
        file_id=file_id,
        name=name,
        object_key=object_key,
        size_bytes=size_bytes,
        sha256_hex=sha256_hex,
        disposition="new",
    )


def _manifest(
    entries: tuple[ManifestObjectEntry, ...] | None = None,
    *,
    container: str = _CONTAINER,
    prefix: str = _PREFIX,
) -> ManifestReport:
    return ManifestReport(
        source_platform="slack",
        workspace_id="W001",
        channel_id="C001",
        destination_container=container,
        destination_prefix=prefix,
        scanned_count=len(entries) if entries else 1,
        matched_count=len(entries) if entries else 1,
        total_count=len(entries) if entries else 1,
        truncated=False,
        object_entries=entries if entries is not None else (_obj_entry(),),
        failed_files=(),
        verifier_artifact_id=None,
    )


def _object_info(
    *,
    sha256: str | None = None,
    size_bytes: int = 1024,
) -> MagicMock:
    from cloud_storage_api.models import ObjectInfo

    metadata = {_SHA256_META_KEY: sha256} if sha256 else None
    return ObjectInfo(
        object_name="key",
        size_bytes=size_bytes,
        metadata=metadata,
    )


_SHA256_META_KEY = "sha256"


def _file_artifact_store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path / "artifacts.db")


def _file_event_store(tmp_path: Path) -> FileSessionEventStore:
    return FileSessionEventStore(tmp_path / "events.db")


# ── Tests: metadata path ──────────────────────────────────────────────────────


def test_all_match_via_metadata(tmp_path: Path) -> None:
    """All objects match when storage metadata sha256 equals manifest sha256."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=1024,
        metadata={"sha256": _SHA256_A},
    )
    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.has_drift is False
    assert report.match_count == 1
    assert report.mismatch_count == 0
    assert report.missing_count == 0
    assert report.entries[0].status == "match"
    assert report.entries[0].observed_sha256 == _SHA256_A


def test_mismatch_via_metadata(tmp_path: Path) -> None:
    """Reports mismatch and sets has_drift when metadata sha256 differs."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=1024,
        metadata={"sha256": _SHA256_B},
    )
    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.has_drift is True
    assert report.mismatch_count == 1
    assert report.entries[0].status == "mismatch"
    assert report.entries[0].observed_sha256 == _SHA256_B


def test_metadata_comparison_is_case_insensitive(tmp_path: Path) -> None:
    """SHA-256 comparison lowercases both sides before comparing."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=1024,
        metadata={"sha256": _SHA256_A.upper()},
    )
    actor = _actor()
    entry = _obj_entry(sha256_hex=_SHA256_A.lower())
    report = verify_manifest(
        _manifest((entry,)),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.entries[0].status == "match"


# ── Tests: stream-hash path ───────────────────────────────────────────────────


def test_stream_hash_match(tmp_path: Path) -> None:
    """Downloads and hashes small objects when metadata sha256 is absent."""
    import hashlib

    from cloud_storage_api.models import ObjectInfo

    content = b"hello world"
    real_sha = hashlib.sha256(content).hexdigest()

    def fake_download(_container: str, key: str, local_path: str) -> ObjectInfo:
        Path(local_path).write_bytes(content)
        return ObjectInfo(object_name=key, size_bytes=len(content))

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=len(content),
        metadata=None,
    )
    storage.download_file.side_effect = fake_download

    entry = _obj_entry(sha256_hex=real_sha, size_bytes=len(content))
    actor = _actor()
    report = verify_manifest(
        _manifest((entry,)),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.entries[0].status == "match"
    assert report.has_drift is False


def test_stream_hash_mismatch(tmp_path: Path) -> None:
    """Reports mismatch when hashed content differs from manifest sha256."""
    from cloud_storage_api.models import ObjectInfo

    def fake_download(_container: str, key: str, local_path: str) -> ObjectInfo:
        Path(local_path).write_bytes(b"tampered")
        return ObjectInfo(object_name=key)

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=8,
        metadata=None,
    )
    storage.download_file.side_effect = fake_download

    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.entries[0].status == "mismatch"
    assert report.has_drift is True


def test_large_object_no_metadata_is_unknown(tmp_path: Path) -> None:
    """Objects >100 MB with no metadata are marked 'unknown', not drift."""
    from cloud_storage_api.models import ObjectInfo

    huge = 101 * 1024 * 1024
    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=huge,
        metadata=None,
    )

    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.entries[0].status == "unknown"
    assert report.has_drift is False
    assert report.unknown_count == 1


def test_large_object_no_metadata_strict_is_drift(tmp_path: Path) -> None:
    """Unknown objects count as drift when strict=True."""
    from cloud_storage_api.models import ObjectInfo

    huge = 101 * 1024 * 1024
    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=huge,
        metadata=None,
    )

    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
        strict=True,
    )

    assert report.has_drift is True


# ── Tests: missing / bucket-missing ──────────────────────────────────────────


def test_missing_object_is_drift(tmp_path: Path) -> None:
    """ObjectNotFoundError maps to 'missing' status and triggers has_drift."""
    storage = MagicMock()
    storage.get_file_info.side_effect = ObjectNotFoundError("no such key")

    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.entries[0].status == "missing"
    assert report.missing_count == 1
    assert report.has_drift is True


def test_bucket_missing_marks_all_entries(tmp_path: Path) -> None:
    """ContainerNotFoundError propagates 'bucket_missing' to every entry."""
    storage = MagicMock()
    storage.get_file_info.side_effect = ContainerNotFoundError("no bucket")

    entries = tuple(_obj_entry(file_id=f"F{i}", name=f"f{i}.jpg") for i in range(3))
    actor = _actor()
    report = verify_manifest(
        _manifest(entries),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.bucket_missing is True
    assert report.has_drift is True
    assert all(e.status == "bucket_missing" for e in report.entries)
    assert storage.get_file_info.call_count == 1


def test_authentication_error_re_raised(tmp_path: Path) -> None:
    """AuthenticationError propagates without being silently swallowed."""
    storage = MagicMock()
    storage.get_file_info.side_effect = AuthenticationError("bad creds")

    actor = _actor()
    with pytest.raises(AuthenticationError):
        verify_manifest(
            _manifest(),
            "ART-001",
            storage,
            _file_artifact_store(tmp_path),
            _file_event_store(tmp_path),
            actor=actor,
            session_id="sess-001",
            now=_NOW,
        )


def test_storage_backend_error_re_raised(tmp_path: Path) -> None:
    """StorageBackendError propagates without masking as drift."""
    storage = MagicMock()
    storage.get_file_info.side_effect = StorageBackendError("timeout")

    actor = _actor()
    with pytest.raises(StorageBackendError):
        verify_manifest(
            _manifest(),
            "ART-001",
            storage,
            _file_artifact_store(tmp_path),
            _file_event_store(tmp_path),
            actor=actor,
            session_id="sess-001",
            now=_NOW,
        )


# ── Tests: artifact and event persistence ────────────────────────────────────


def test_artifact_persisted_on_match(tmp_path: Path) -> None:
    """A drift_report artifact is always written, even when there is no drift."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=1024,
        metadata={"sha256": _SHA256_A},
    )

    artifact_store = _file_artifact_store(tmp_path)
    actor = _actor()
    verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        artifact_store,
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    artifacts = artifact_store.list_for_session(
        tenant=actor.tenant, session_id="sess-001"
    )
    assert len(artifacts) == 1
    assert artifacts[0].kind == "drift_report"
    assert isinstance(artifacts[0].payload, DriftReport)


def test_drift_detected_event_emitted_on_mismatch(tmp_path: Path) -> None:
    """A 'drift_detected' event is appended to the session when drift exists."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=1024,
        metadata={"sha256": _SHA256_B},
    )

    event_store = _file_event_store(tmp_path)
    actor = _actor()
    verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        event_store,
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    events = event_store.list_events(tenant=actor.tenant, session_id="sess-001")
    drift_events = [e for e in events if e.event_type == "drift_detected"]
    assert len(drift_events) == 1
    assert drift_events[0].payload["mismatch_count"] == 1


def test_no_drift_event_when_all_match(tmp_path: Path) -> None:
    """No 'drift_detected' event is appended when every object matches."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=1024,
        metadata={"sha256": _SHA256_A},
    )

    event_store = _file_event_store(tmp_path)
    actor = _actor()
    verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        event_store,
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    events = event_store.list_events(tenant=actor.tenant, session_id="sess-001")
    assert not any(e.event_type == "drift_detected" for e in events)


# ── Tests: multi-object manifests ────────────────────────────────────────────


def test_mixed_statuses_counted_correctly(tmp_path: Path) -> None:
    """Counters are accurate across match, mismatch, missing, and unknown."""
    from cloud_storage_api.models import ObjectInfo

    entries = (
        _obj_entry(file_id="F1", object_key="k1", sha256_hex=_SHA256_A),
        _obj_entry(file_id="F2", object_key="k2", sha256_hex=_SHA256_A),
        _obj_entry(file_id="F3", object_key="k3", sha256_hex=_SHA256_A),
        _obj_entry(
            file_id="F4",
            object_key="k4",
            sha256_hex=_SHA256_A,
            size_bytes=200 * 1024 * 1024,
        ),
    )

    def fake_info(_container: str, key: str) -> ObjectInfo:
        if key == "k1":
            return ObjectInfo(
                object_name=key, size_bytes=1024, metadata={"sha256": _SHA256_A}
            )
        if key == "k2":
            return ObjectInfo(
                object_name=key, size_bytes=1024, metadata={"sha256": _SHA256_B}
            )
        if key == "k3":
            msg = "gone"
            raise ObjectNotFoundError(msg)
        # k4: large, no metadata
        return ObjectInfo(object_name=key, size_bytes=200 * 1024 * 1024, metadata=None)

    storage = MagicMock()
    storage.get_file_info.side_effect = fake_info

    actor = _actor()
    report = verify_manifest(
        _manifest(entries),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.total_count == 4
    assert report.match_count == 1
    assert report.mismatch_count == 1
    assert report.missing_count == 1
    assert report.unknown_count == 1
    assert report.has_drift is True


def test_empty_manifest_produces_clean_report(tmp_path: Path) -> None:
    """An empty manifest yields a clean report with no drift."""
    manifest = ManifestReport(
        source_platform="slack",
        workspace_id="W001",
        channel_id="C001",
        destination_container=_CONTAINER,
        destination_prefix=_PREFIX,
        scanned_count=0,
        matched_count=0,
        total_count=0,
        truncated=False,
        object_entries=(),
        failed_files=(),
        verifier_artifact_id=None,
    )
    storage = MagicMock()
    actor = _actor()
    report = verify_manifest(
        manifest,
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.total_count == 0
    assert report.has_drift is False
    storage.get_file_info.assert_not_called()


# ── Tests: report fields ──────────────────────────────────────────────────────


def test_report_fields_populated(tmp_path: Path) -> None:
    """DriftReport fields are set correctly from manifest and parameters."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=1024,
        metadata={"sha256": _SHA256_A},
    )

    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-MANIFEST-99",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-42",
        action_id="ACT-007",
        now=_NOW,
    )

    assert report.manifest_artifact_id == "ART-MANIFEST-99"
    assert report.tenant == actor.tenant
    assert report.checked_at == _NOW
    assert report.container == _CONTAINER
    assert report.prefix == _PREFIX
    assert report.via_action_id == "ACT-007"


def test_entry_actor_id_set(tmp_path: Path) -> None:
    """Each DriftObjectEntry records the actor's user_id in via_actor_id."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=1024,
        metadata={"sha256": _SHA256_A},
    )

    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.entries[0].via_actor_id == "U001"


# ── Tests: no-metadata + None size ───────────────────────────────────────────


def test_no_metadata_none_size_is_unknown(tmp_path: Path) -> None:
    """Objects with no metadata and None size are treated as unknown."""
    from cloud_storage_api.models import ObjectInfo

    storage = MagicMock()
    storage.get_file_info.return_value = ObjectInfo(
        object_name="key",
        size_bytes=None,
        metadata=None,
    )

    actor = _actor()
    report = verify_manifest(
        _manifest(),
        "ART-001",
        storage,
        _file_artifact_store(tmp_path),
        _file_event_store(tmp_path),
        actor=actor,
        session_id="sess-001",
        now=_NOW,
    )

    assert report.entries[0].status == "unknown"
    assert report.entries[0].observed_sha256 is None
