"""Unit tests for the StorageAgent operation layer."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from cloud_storage_api.models import ObjectInfo
from nimbus_runtime.agent import (
    OperationNotPermittedError,
    StorageAgent,
    StorageAgentError,
    StorageAgentRequest,
)
from nimbus_runtime.domain import (
    Artifact,
    ManifestObjectEntry,
    ManifestReport,
    OperationMode,
    TenantIdentity,
    UploadReport,
    VerifiedActor,
)
from nimbus_runtime.stores import FileArtifactStore, FileSessionEventStore

if TYPE_CHECKING:
    from cloud_storage_api.models import DeleteResult

pytestmark = pytest.mark.unit

# ── Timestamps / constants ────────────────────────────────────────────────────

_NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC)
_CONTAINER = "my-bucket"


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


def _obj(name: str, size: int = 512) -> ObjectInfo:
    return ObjectInfo(object_name=name, size_bytes=size)


# ── Fake storage client ───────────────────────────────────────────────────────


@dataclass
class FakeStorage:
    """Minimal CloudStorageClient stand-in for agent tests."""

    _files: list[ObjectInfo] = field(default_factory=list)
    _file_data: dict[str, bytes] = field(default_factory=dict)

    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        return [f for f in self._files if f.object_name.startswith(prefix)]

    def get_file_info(self, container: str, object_name: str) -> ObjectInfo:
        for f in self._files:
            if f.object_name == object_name:
                return f
        return ObjectInfo(object_name=object_name, size_bytes=0)

    def delete_file(self, container: str, object_name: str) -> DeleteResult:
        self._files = [f for f in self._files if f.object_name != object_name]
        return {"deleted": True, "version_id": None, "request_charged": None}

    def upload_obj(
        self, container: str, file_obj: io.BytesIO, remote_path: str
    ) -> ObjectInfo:
        data = file_obj.read()
        self._file_data[remote_path] = data
        info = ObjectInfo(object_name=remote_path, size_bytes=len(data))
        self._files.append(info)
        return info

    def download_file(
        self, container: str, object_name: str, file_name: str
    ) -> ObjectInfo:

        data = self._file_data.get(object_name, b"test-content")
        with open(file_name, "wb") as fh:  # noqa: PTH123
            fh.write(data)
        return ObjectInfo(object_name=object_name, size_bytes=len(data))

    def upload_file(
        self, container: str, local_path: str, remote_path: str
    ) -> ObjectInfo:
        return ObjectInfo(object_name=remote_path)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def storage() -> FakeStorage:
    return FakeStorage(
        _files=[
            _obj("reports/q1.csv", 1024),
            _obj("reports/q2.csv", 2048),
            _obj("archive/old.tar.gz", 4096),
        ]
    )


@pytest.fixture
def artifact_store(tmp_path: Path) -> FileArtifactStore:
    event_store = FileSessionEventStore(tmp_path / "events.db")
    return FileArtifactStore(tmp_path / "artifacts.db", event_store=event_store)


@pytest.fixture
def event_store(tmp_path: Path) -> FileSessionEventStore:
    return FileSessionEventStore(tmp_path / "events.db")


@pytest.fixture
def agent(
    storage: FakeStorage,
    artifact_store: FileArtifactStore,
    event_store: FileSessionEventStore,
) -> StorageAgent:
    return StorageAgent(
        storage=storage,  # type: ignore[arg-type]
        artifact_store=artifact_store,
        event_store=event_store,
    )


def _req(
    operation: str,
    *,
    mode: OperationMode = OperationMode.READ_ONLY,
    params: dict[str, object] | None = None,
    actor: VerifiedActor | None = None,
    container: str = _CONTAINER,
    now: datetime | None = _NOW,
) -> StorageAgentRequest:
    return StorageAgentRequest(
        session_id="s-test-001",
        operation=operation,  # type: ignore[arg-type]
        mode=mode,
        actor=actor or _actor(),
        container=container,
        params=params or {},
        now=now,
    )


# ── Access-control tests ──────────────────────────────────────────────────────


class TestModePermissions:
    def test_read_only_allows_list(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("list", mode=OperationMode.READ_ONLY))
        assert resp.success

    def test_read_only_allows_scan(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("scan", mode=OperationMode.READ_ONLY))
        assert resp.success

    def test_read_only_allows_search(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("search", mode=OperationMode.READ_ONLY))
        assert resp.success

    def test_read_only_rejects_delete(self, agent: StorageAgent) -> None:
        with pytest.raises(OperationNotPermittedError) as exc_info:
            agent.execute(_req("delete", mode=OperationMode.READ_ONLY))
        assert exc_info.value.operation == "delete"
        assert exc_info.value.mode is OperationMode.READ_ONLY

    def test_read_only_rejects_stage_upload(self, agent: StorageAgent) -> None:
        with pytest.raises(OperationNotPermittedError):
            agent.execute(_req("stage_upload", mode=OperationMode.READ_ONLY))

    def test_plan_allows_propose_plan(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("propose_plan", mode=OperationMode.PLAN))
        assert resp.success

    def test_plan_rejects_delete(self, agent: StorageAgent) -> None:
        with pytest.raises(OperationNotPermittedError):
            agent.execute(_req("delete", mode=OperationMode.PLAN))

    def test_apply_allows_delete(
        self, agent: StorageAgent, storage: FakeStorage
    ) -> None:
        resp = agent.execute(
            _req(
                "delete",
                mode=OperationMode.APPLY,
                params={"object_key": "reports/q1.csv"},
            )
        )
        assert resp.success

    def test_watch_allows_scan(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("scan", mode=OperationMode.WATCH))
        assert resp.success

    def test_watch_rejects_write_artifact(self, agent: StorageAgent) -> None:
        with pytest.raises(OperationNotPermittedError):
            agent.execute(_req("write_artifact", mode=OperationMode.WATCH))

    def test_review_allows_propose_plan(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("propose_plan", mode=OperationMode.REVIEW))
        assert resp.success

    def test_policy_admin_allows_write_artifact(
        self, agent: StorageAgent, artifact_store: FileArtifactStore
    ) -> None:
        payload = UploadReport(
            remote_path="test.txt",
            filename="test.txt",
            size_bytes=10,
            sha256_hex="a" * 64,
        )
        resp = agent.execute(
            _req(
                "write_artifact",
                mode=OperationMode.POLICY_ADMIN,
                params={"kind": "upload_report", "payload": payload},
            )
        )
        assert resp.success


# ── Scan operation ────────────────────────────────────────────────────────────


class TestOpScan:
    def test_scan_returns_all_files(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("scan"))
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["count"] == 3
        assert payload["container"] == _CONTAINER

    def test_scan_with_prefix_filters_results(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("scan", params={"prefix": "reports/"}))
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["count"] == 2

    def test_scan_objects_have_key_and_size(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("scan"))
        payload = resp.payload
        assert isinstance(payload, dict)
        objects = payload["objects"]
        assert isinstance(objects, list)
        assert all("key" in o and "size_bytes" in o for o in objects)

    def test_scan_artifact_id_is_none(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("scan"))
        assert resp.artifact_id is None


# ── List operation ────────────────────────────────────────────────────────────


class TestOpList:
    def test_list_returns_keys_and_count(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("list"))
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["count"] == 3
        assert isinstance(payload["keys"], list)
        assert len(payload["keys"]) == 3

    def test_list_with_prefix(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("list", params={"prefix": "archive/"}))
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["count"] == 1


# ── Search operation ──────────────────────────────────────────────────────────


class TestOpSearch:
    def test_search_without_search_store_returns_empty(
        self, agent: StorageAgent
    ) -> None:
        resp = agent.execute(_req("search", params={"query": "quarterly"}))
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["count"] == 0
        assert payload["results"] == []
        assert payload["query"] == "quarterly"

    def test_search_with_search_store_calls_it(
        self,
        storage: FakeStorage,
        artifact_store: FileArtifactStore,
        event_store: FileSessionEventStore,
    ) -> None:
        mock_store = MagicMock()
        mock_store.search.return_value = []
        agent = StorageAgent(
            storage=storage,  # type: ignore[arg-type]
            artifact_store=artifact_store,
            event_store=event_store,
            search_store=mock_store,
        )
        resp = agent.execute(
            _req("search", params={"query": "report", "workspace_wide": True})
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["count"] == 0
        mock_store.search.assert_called_once()

    def test_search_with_visible_channels(
        self,
        storage: FakeStorage,
        artifact_store: FileArtifactStore,
        event_store: FileSessionEventStore,
    ) -> None:
        mock_store = MagicMock()
        mock_store.search.return_value = []
        agent = StorageAgent(
            storage=storage,  # type: ignore[arg-type]
            artifact_store=artifact_store,
            event_store=event_store,
            search_store=mock_store,
        )
        resp = agent.execute(
            _req(
                "search",
                params={"query": "q2", "visible_channel_ids": ["C001", "C002"]},
            )
        )
        assert resp.success
        mock_store.search.assert_called_once()


# ── Hash operation ────────────────────────────────────────────────────────────


class TestOpHash:
    def test_hash_returns_key_and_size(self, agent: StorageAgent) -> None:
        resp = agent.execute(
            _req(
                "hash",
                mode=OperationMode.READ_ONLY,
                params={"object_key": "reports/q1.csv"},
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["object_key"] == "reports/q1.csv"
        assert "size_bytes" in payload


# ── Propose-plan operation ────────────────────────────────────────────────────


class TestOpProposePlan:
    def test_propose_plan_returns_description(self, agent: StorageAgent) -> None:
        resp = agent.execute(
            _req(
                "propose_plan",
                mode=OperationMode.PLAN,
                params={"description": "delete old archives"},
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["description"] == "delete old archives"
        assert payload["status"] == "plan_proposed"


# ── Stage-upload operation ────────────────────────────────────────────────────


class TestOpStageUpload:
    def test_stage_upload_creates_staging_key(self, agent: StorageAgent) -> None:
        resp = agent.execute(
            _req(
                "stage_upload",
                mode=OperationMode.PLAN,
                params={"object_key": "myfile.txt"},
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert "staging_key" in payload
        assert "myfile.txt" in str(payload["staging_key"])
        assert payload["status"] == "staged"


# ── Promote-upload operation ──────────────────────────────────────────────────


class TestOpPromoteUpload:
    def test_promote_upload_moves_file(
        self, agent: StorageAgent, storage: FakeStorage
    ) -> None:
        # First stage an object in storage
        storage._file_data["_staging/U001/abc/staged.txt"] = b"hello world"
        storage._files.append(
            ObjectInfo(object_name="_staging/U001/abc/staged.txt", size_bytes=11)
        )
        resp = agent.execute(
            _req(
                "promote_upload",
                mode=OperationMode.APPLY,
                params={
                    "staging_key": "_staging/U001/abc/staged.txt",
                    "final_key": "final/staged.txt",
                },
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["status"] == "promoted"
        assert payload["final_key"] == "final/staged.txt"


# ── Prepare-delete operation ──────────────────────────────────────────────────


class TestOpPrepareDelete:
    def test_prepare_delete_returns_object_info(self, agent: StorageAgent) -> None:
        resp = agent.execute(
            _req(
                "prepare_delete",
                mode=OperationMode.APPLY,
                params={"object_key": "reports/q1.csv"},
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["object_key"] == "reports/q1.csv"
        assert payload["status"] == "prepared"


# ── Delete operation ──────────────────────────────────────────────────────────


class TestOpDelete:
    def test_delete_removes_file(
        self, agent: StorageAgent, storage: FakeStorage
    ) -> None:
        resp = agent.execute(
            _req(
                "delete",
                mode=OperationMode.APPLY,
                params={"object_key": "reports/q1.csv"},
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert payload["status"] == "deleted"
        assert payload["object_key"] == "reports/q1.csv"
        remaining = [f.object_name for f in storage._files]
        assert "reports/q1.csv" not in remaining


# ── Restore operation ─────────────────────────────────────────────────────────


class TestOpRestore:
    def test_restore_returns_message(self, agent: StorageAgent) -> None:
        resp = agent.execute(
            _req(
                "restore",
                mode=OperationMode.APPLY,
                params={"object_key": "reports/q1.csv", "version_id": "v1"},
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert "message" in payload
        assert payload["object_key"] == "reports/q1.csv"


# ── Write-artifact operation ──────────────────────────────────────────────────


class TestOpWriteArtifact:
    def test_write_artifact_returns_artifact_id(self, agent: StorageAgent) -> None:
        report = UploadReport(
            remote_path="upload/test.txt",
            filename="test.txt",
            size_bytes=42,
            sha256_hex="b" * 64,
        )
        resp = agent.execute(
            _req(
                "write_artifact",
                mode=OperationMode.APPLY,
                params={"kind": "upload_report", "payload": report},
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert "artifact_id" in payload
        assert resp.artifact_id is not None
        assert resp.artifact_id == payload["artifact_id"]


# ── Diff-manifest and Verify operations ──────────────────────────────────────


def _manifest_report() -> ManifestReport:
    entry = ManifestObjectEntry(
        file_id="F001",
        name="q1.csv",
        object_key="reports/q1.csv",
        size_bytes=1024,
        sha256_hex="a" * 64,
        disposition="new",
    )
    return ManifestReport(
        source_platform="cli",
        workspace_id="W001",
        channel_id="",
        destination_container=_CONTAINER,
        destination_prefix="reports/",
        scanned_count=1,
        matched_count=1,
        total_count=1,
        truncated=False,
        object_entries=(entry,),
        failed_files=(),
        verifier_artifact_id=None,
    )


class TestOpDiffManifest:
    def test_diff_manifest_raises_if_artifact_missing(
        self, agent: StorageAgent
    ) -> None:
        with pytest.raises(StorageAgentError, match="not found"):
            agent.execute(
                _req(
                    "diff_manifest",
                    mode=OperationMode.READ_ONLY,
                    params={"manifest_artifact_id": "nonexistent-id"},
                )
            )

    def test_diff_manifest_raises_if_wrong_type(
        self,
        agent: StorageAgent,
        artifact_store: FileArtifactStore,
    ) -> None:
        # Write an upload_report artifact (not a ManifestReport) and try to diff
        report = UploadReport(
            remote_path="file.txt",
            filename="file.txt",
            size_bytes=10,
            sha256_hex="c" * 64,
        )
        resp = agent.execute(
            _req(
                "write_artifact",
                mode=OperationMode.APPLY,
                params={"kind": "upload_report", "payload": report},
            )
        )
        artifact_id = resp.artifact_id
        # The payload is an UploadReport, not a ManifestReport — should raise
        with pytest.raises(StorageAgentError, match="wrong type"):
            agent.execute(
                _req(
                    "diff_manifest",
                    mode=OperationMode.READ_ONLY,
                    params={"manifest_artifact_id": artifact_id},
                )
            )


class TestOpDiffManifestSuccess:
    """Test the success path of diff_manifest when the artifact is found."""

    def test_diff_manifest_with_valid_manifest_artifact(
        self,
        storage: FakeStorage,
        artifact_store: FileArtifactStore,
        event_store: FileSessionEventStore,
    ) -> None:
        """diff_manifest succeeds when the artifact exists with a ManifestReport."""
        actor = _actor()
        # Store a ManifestReport artifact directly in the artifact_store
        manifest = _manifest_report()
        stored_artifact = artifact_store.create(
            artifact=Artifact(
                artifact_id="art-manifest-001",
                tenant=actor.tenant,
                session_id="s-test-001",
                action_id=None,
                kind="manifest",
                uri=None,
                payload=manifest,
                created_at=_NOW,
            ),
            actor=actor,
        )
        agent = StorageAgent(
            storage=storage,  # type: ignore[arg-type]
            artifact_store=artifact_store,
            event_store=event_store,
        )
        resp = agent.execute(
            _req(
                "diff_manifest",
                mode=OperationMode.READ_ONLY,
                params={"manifest_artifact_id": stored_artifact.artifact_id},
                actor=actor,
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert "manifest_artifact_id" in payload
        assert "has_drift" in payload


class TestOpVerify:
    def test_verify_raises_if_artifact_missing(self, agent: StorageAgent) -> None:
        with pytest.raises(StorageAgentError, match="not found"):
            agent.execute(
                _req(
                    "verify",
                    mode=OperationMode.APPLY,
                    params={"manifest_artifact_id": "no-such-id"},
                )
            )

    def test_verify_succeeds_with_valid_manifest_artifact(
        self,
        storage: FakeStorage,
        artifact_store: FileArtifactStore,
        event_store: FileSessionEventStore,
    ) -> None:
        """Verify succeeds when the artifact exists with a ManifestReport."""
        actor = _actor()
        manifest = _manifest_report()
        stored_artifact = artifact_store.create(
            artifact=Artifact(
                artifact_id="art-verify-001",
                tenant=actor.tenant,
                session_id="s-test-001",
                action_id=None,
                kind="manifest",
                uri=None,
                payload=manifest,
                created_at=_NOW,
            ),
            actor=actor,
        )
        agent = StorageAgent(
            storage=storage,  # type: ignore[arg-type]
            artifact_store=artifact_store,
            event_store=event_store,
        )
        resp = agent.execute(
            _req(
                "verify",
                mode=OperationMode.APPLY,
                params={
                    "manifest_artifact_id": stored_artifact.artifact_id,
                    "strict": False,
                },
                actor=actor,
            )
        )
        payload = resp.payload
        assert isinstance(payload, dict)
        assert "manifest_artifact_id" in payload
        assert "has_drift" in payload

    def test_verify_raises_if_wrong_type(
        self,
        agent: StorageAgent,
        artifact_store: FileArtifactStore,
    ) -> None:
        report = UploadReport(
            remote_path="f.txt",
            filename="f.txt",
            size_bytes=5,
            sha256_hex="d" * 64,
        )
        resp = agent.execute(
            _req(
                "write_artifact",
                mode=OperationMode.APPLY,
                params={"kind": "upload_report", "payload": report},
            )
        )
        artifact_id = resp.artifact_id
        with pytest.raises(StorageAgentError, match="wrong type"):
            agent.execute(
                _req(
                    "verify",
                    mode=OperationMode.APPLY,
                    params={"manifest_artifact_id": artifact_id},
                )
            )


# ── Response shape ────────────────────────────────────────────────────────────


class TestResponseShape:
    def test_response_session_id_matches_request(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("list"))
        assert resp.session_id == "s-test-001"

    def test_response_actor_id_matches_request(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("list"))
        assert resp.actor_id == "U001"

    def test_response_operation_matches_request(self, agent: StorageAgent) -> None:
        resp = agent.execute(_req("scan"))
        assert resp.operation == "scan"

    def test_response_executed_at_is_fixed_when_provided(
        self, agent: StorageAgent
    ) -> None:
        resp = agent.execute(_req("list", now=_NOW))
        assert resp.executed_at == _NOW

    def test_response_executed_at_uses_utc_when_not_provided(
        self, agent: StorageAgent
    ) -> None:
        req = _req("list", now=None)
        resp = agent.execute(req)
        assert resp.executed_at.tzinfo is not None
