"""Unit tests for the deterministic channel backup workflow."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from nimbus_runtime.backup import (
    BackupChannelRef,
    BackupDestination,
    BackupFileScan,
    BackupManifestEntry,
    BackupSourceFile,
    ChannelBackupRequest,
    ChannelBackupResult,
    ChannelBackupWorkflow,
)
from nimbus_runtime.domain import (
    Artifact,
    ManifestReport,
    ObjectVerificationReport,
    Task,
    TaskStatus,
    TenantIdentity,
    VerifiedActor,
)
from nimbus_runtime.stores import (
    FileArtifactStore,
    FileSessionEventStore,
    FileTaskStore,
    FileWorkerLeaseStore,
)
from nimbus_runtime.worker import TaskLeaseContext, TaskWorkerConfig, TaskWorkerLoop

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TENANT_ID = "T123TEAM"
_CHANNEL_ID = "C123CHAN"
_PDF_BYTES = b"%PDF-1.7 legal contract"
_PNG_BYTES = b"\x89PNG diagram"


@dataclass
class _Source:
    files: tuple[BackupSourceFile, ...]
    content_by_file_id: dict[str, bytes]
    downloads: list[str] = field(default_factory=list)

    def list_files(
        self,
        _channel: BackupChannelRef,
        *,
        page_size: int,
        max_pages: int,
    ) -> BackupFileScan:
        """Return a bounded fake source listing."""
        del page_size, max_pages
        return BackupFileScan(
            files=self.files,
            total_count=len(self.files),
            truncated=False,
        )

    def download_file(self, file: BackupSourceFile, *, max_bytes: int) -> bytes:
        """Return fake source bytes by file ID."""
        self.downloads.append(file.file_id)
        content = self.content_by_file_id[file.file_id]
        if len(content) > max_bytes:
            return content[: max_bytes + 1]
        return content


@dataclass
class _Sink:
    objects: dict[tuple[str, str], bytes] = field(default_factory=dict)
    uploads: list[str] = field(default_factory=list)
    failing_verify_keys: set[str] = field(default_factory=set)

    def upload_bytes(
        self,
        *,
        destination: BackupDestination,
        key: str,
        content: bytes,
    ) -> None:
        """Record fake object bytes."""
        self.uploads.append(key)
        self.objects[(destination.container, key)] = content

    def verify_object(
        self,
        *,
        destination: BackupDestination,
        key: str,
        content_sha256: str,
        size_bytes: int,
    ) -> bool:
        """Verify fake object hash and size."""
        if key in self.failing_verify_keys:
            return False
        content = self.objects.get((destination.container, key))
        return (
            content is not None
            and len(content) == size_bytes
            and hashlib.sha256(content).hexdigest() == content_sha256
        )


@dataclass
class _ManifestStore:
    entries_by_file_id: dict[str, BackupManifestEntry] = field(default_factory=dict)

    def list_entries(
        self,
        *,
        tenant: TenantIdentity,
        channel: BackupChannelRef,
    ) -> tuple[BackupManifestEntry, ...]:
        """Return fake manifest entries for one tenant/channel."""
        return tuple(
            entry
            for entry in self.entries_by_file_id.values()
            if entry.tenant == tenant and entry.channel == channel
        )

    def record_entry(self, entry: BackupManifestEntry) -> None:
        """Record fake manifest evidence by source file ID."""
        self.entries_by_file_id[entry.file_id] = entry


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="slack", workspace_id=_TENANT_ID)


def _actor(tenant: TenantIdentity) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id="U123USER",
        auth_source="slack_signed_event",
        bridge_id="slack",
        verified_at=_NOW,
    )


def _task(*, tenant: TenantIdentity, actor: VerifiedActor) -> Task:
    return Task(
        task_id="task-backup",
        tenant=tenant,
        session_id=f"{tenant.tenant_id}:task-backup",
        created_by=actor,
        status=TaskStatus.CREATED,
        intent="backup_channel",
        source_ref="slack:T123TEAM:C123CHAN:thread",
        idempotency_key="idem-backup",
        metadata={"channel_id": _CHANNEL_ID},
        failure_detail=None,
        created_at=_NOW,
        updated_at=_NOW,
        expires_at=None,
    )


def _file(
    file_id: str,
    name: str,
    content: bytes,
    *,
    content_type: str | None = "application/pdf",
) -> BackupSourceFile:
    return BackupSourceFile(
        file_id=file_id,
        name=name,
        content_type=content_type,
        size_bytes=len(content),
        created_at=_NOW - timedelta(days=1),
    )


def _channel() -> BackupChannelRef:
    return BackupChannelRef(
        platform="slack",
        workspace_id=_TENANT_ID,
        channel_id=_CHANNEL_ID,
        workspace_name="Acme Co",
        channel_name="#legal-contracts",
    )


def _destination() -> BackupDestination:
    return BackupDestination(container="legal-bucket", prefix="archives")


def _request() -> ChannelBackupRequest:
    tenant = _tenant()
    return ChannelBackupRequest(
        tenant=tenant,
        channel=_channel(),
        destination=_destination(),
        include_content_types=frozenset({"application/pdf"}),
    )


def _existing_entry(
    *,
    request: ChannelBackupRequest,
    file: BackupSourceFile,
    content: bytes,
    object_key: str = "archives/slack/Acme-Co/legal-contracts/F0/existing.pdf",
) -> BackupManifestEntry:
    return BackupManifestEntry(
        tenant=request.tenant,
        channel=request.channel,
        file_id=file.file_id,
        name=file.name,
        content_type=file.content_type,
        size_bytes=len(content),
        content_sha256=hashlib.sha256(content).hexdigest(),
        destination=request.destination,
        object_key=object_key,
        saved_at=_NOW,
    )


def _expected_artifact_id(request: ChannelBackupRequest, kind: str) -> str:
    identity = f"{request.tenant.tenant_id}:task-backup:{kind}"
    return f"art-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex}"


def _create_task_store(tmp_path: Path) -> tuple[FileTaskStore, FileWorkerLeaseStore]:
    task_store = FileTaskStore(tmp_path)
    lease_store = FileWorkerLeaseStore(tmp_path)
    tenant = _tenant()
    actor = _actor(tenant)
    task_store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key="idem-backup",
        create=lambda: _task(tenant=tenant, actor=actor),
    )
    return task_store, lease_store


def _run_workflow(
    *,
    tmp_path: Path,
    source: _Source,
    sink: _Sink,
    manifest: _ManifestStore,
    request: ChannelBackupRequest,
) -> tuple[ChannelBackupResult, Task | None, tuple[Artifact, ...]]:
    event_store = FileSessionEventStore(tmp_path)
    artifact_store = FileArtifactStore(tmp_path, event_store=event_store)
    task_store, lease_store = _create_task_store(tmp_path)
    results: list[ChannelBackupResult] = []
    clock_tick = 0

    def clock() -> datetime:
        nonlocal clock_tick
        clock_tick += 1
        return _NOW + timedelta(microseconds=clock_tick)

    workflow = ChannelBackupWorkflow(
        source=source,
        sink=sink,
        manifest_store=manifest,
        artifact_store=artifact_store,
        clock=clock,
    )

    async def handler(context: TaskLeaseContext) -> None:
        results.append(await workflow.run(context=context, request=request))

    worker = TaskWorkerLoop(
        task_store=task_store,
        lease_store=lease_store,
        handler=handler,
        config=TaskWorkerConfig(tenant=request.tenant, worker_id="worker-a"),
    )
    run_result = asyncio.run(worker.run_once())

    assert run_result.claimed == 1
    assert len(results) == 1
    task = task_store.get(tenant=request.tenant, task_id="task-backup")
    artifacts = artifact_store.list_for_session(
        tenant=request.tenant,
        session_id="slack:T123TEAM:task-backup",
    )
    return results[0], task, tuple(artifacts)


def test_backup_channel_uploads_matching_files_and_records_manifest(
    tmp_path: Path,
) -> None:
    """The workflow should upload verified in-scope files and skip other files."""
    pdf = _file("F1", "contract.pdf", _PDF_BYTES)
    png = _file("F2", "diagram.png", _PNG_BYTES, content_type="image/png")
    source = _Source(
        files=(pdf, png),
        content_by_file_id={"F1": _PDF_BYTES, "F2": _PNG_BYTES},
    )
    sink = _Sink()
    manifest = _ManifestStore()
    request = _request()

    result, task, artifacts = _run_workflow(
        tmp_path=tmp_path,
        source=source,
        sink=sink,
        manifest=manifest,
        request=request,
    )

    assert task is not None
    assert task.status is TaskStatus.DONE
    assert result.scanned_count == 2
    assert result.matched_count == 1
    assert result.uploaded_count == 1
    assert result.deduped_count == 0
    assert result.failed_files == ()
    assert source.downloads == ["F1"]
    assert sink.uploads == ["archives/slack/Acme-Co/legal-contracts/F1/contract.pdf"]
    assert manifest.entries_by_file_id["F1"].object_key == sink.uploads[0]
    assert result.verifier_artifact_id == artifacts[0].artifact_id
    assert result.manifest_artifact_id == artifacts[1].artifact_id
    assert result.verifier_artifact_id == _expected_artifact_id(
        request,
        "verification_report",
    )
    assert result.manifest_artifact_id == _expected_artifact_id(request, "manifest")
    assert [artifact.kind for artifact in artifacts] == [
        "verification_report",
        "manifest",
        "proof_receipt",
    ]
    verifier = artifacts[0].payload
    manifest_report = artifacts[1].payload
    assert isinstance(verifier, ObjectVerificationReport)
    assert verifier.verified is True
    assert verifier.entries[0].object_key == sink.uploads[0]
    assert isinstance(manifest_report, ManifestReport)
    assert manifest_report.verifier_artifact_id == artifacts[0].artifact_id
    assert manifest_report.object_entries[0].disposition == "uploaded"


def test_backup_channel_skips_already_saved_file(tmp_path: Path) -> None:
    """Existing same-size manifest records should make the run idempotent."""
    request = _request()
    pdf = _file("F1", "contract.pdf", _PDF_BYTES)
    existing = _existing_entry(request=request, file=pdf, content=_PDF_BYTES)
    manifest = _ManifestStore(entries_by_file_id={"F1": existing})
    source = _Source(files=(pdf,), content_by_file_id={"F1": _PDF_BYTES})
    sink = _Sink()

    result, task, artifacts = _run_workflow(
        tmp_path=tmp_path,
        source=source,
        sink=sink,
        manifest=manifest,
        request=request,
    )

    assert task is not None
    assert task.status is TaskStatus.DONE
    assert result.uploaded_count == 0
    assert len(result.skipped_files) == 1
    assert result.skipped_files[0].reason == "already_saved"
    assert source.downloads == []
    assert sink.uploads == []
    manifest_report = artifacts[1].payload
    assert isinstance(manifest_report, ManifestReport)
    assert manifest_report.object_entries[0].disposition == "already_saved"
    assert manifest_report.object_entries[0].object_key == existing.object_key


def test_backup_channel_dedupes_files_by_verified_content_hash(
    tmp_path: Path,
) -> None:
    """Duplicate content should reuse the first verified object key."""
    first = _file("F1", "contract.pdf", _PDF_BYTES)
    second = _file("F2", "contract-copy.pdf", _PDF_BYTES)
    source = _Source(
        files=(first, second),
        content_by_file_id={"F1": _PDF_BYTES, "F2": _PDF_BYTES},
    )
    sink = _Sink()
    manifest = _ManifestStore()

    result, task, artifacts = _run_workflow(
        tmp_path=tmp_path,
        source=source,
        sink=sink,
        manifest=manifest,
        request=_request(),
    )

    assert task is not None
    assert task.status is TaskStatus.DONE
    assert result.uploaded_count == 1
    assert result.deduped_count == 1
    assert sink.uploads == ["archives/slack/Acme-Co/legal-contracts/F1/contract.pdf"]
    assert manifest.entries_by_file_id["F2"].object_key == sink.uploads[0]
    assert result.saved_files[1].deduped_from_key == sink.uploads[0]
    verifier = artifacts[0].payload
    manifest_report = artifacts[1].payload
    assert isinstance(verifier, ObjectVerificationReport)
    assert [entry.verified for entry in verifier.entries] == [True, True]
    assert isinstance(manifest_report, ManifestReport)
    assert [entry.disposition for entry in manifest_report.object_entries] == [
        "uploaded",
        "deduped",
    ]


def test_backup_channel_records_failed_upload_verification_artifact(
    tmp_path: Path,
) -> None:
    """A failed verifier should fail the task and leave machine-checkable evidence."""
    pdf = _file("F1", "contract.pdf", _PDF_BYTES)
    source = _Source(files=(pdf,), content_by_file_id={"F1": _PDF_BYTES})
    expected_key = "archives/slack/Acme-Co/legal-contracts/F1/contract.pdf"
    sink = _Sink(failing_verify_keys={expected_key})
    manifest = _ManifestStore()

    result, task, artifacts = _run_workflow(
        tmp_path=tmp_path,
        source=source,
        sink=sink,
        manifest=manifest,
        request=_request(),
    )

    assert task is not None
    assert task.status is TaskStatus.FAILED
    assert result.failed_files[0].file.file_id == "F1"
    assert expected_key in result.failed_files[0].reason
    assert manifest.entries_by_file_id == {}
    verifier = artifacts[0].payload
    manifest_report = artifacts[1].payload
    assert isinstance(verifier, ObjectVerificationReport)
    assert verifier.verified is False
    assert verifier.reason == "one or more files failed backup or verification"
    assert len(verifier.entries) == 1
    assert verifier.entries[0].object_key == expected_key
    assert verifier.entries[0].verified is False
    assert verifier.entries[0].reason == "uploaded_object_mismatch"
    assert isinstance(manifest_report, ManifestReport)
    assert manifest_report.object_entries == ()
    assert manifest_report.failed_files[0].file_id == "F1"


def test_backup_channel_fails_when_downloaded_bytes_do_not_match_declared_size(
    tmp_path: Path,
) -> None:
    """The workflow should validate real bytes before recording evidence."""
    declared_large = BackupSourceFile(
        file_id="F1",
        name="contract.pdf",
        content_type="application/pdf",
        size_bytes=len(_PDF_BYTES) + 100,
        created_at=_NOW,
    )
    source = _Source(files=(declared_large,), content_by_file_id={"F1": _PDF_BYTES})
    sink = _Sink()
    manifest = _ManifestStore()

    result, task, artifacts = _run_workflow(
        tmp_path=tmp_path,
        source=source,
        sink=sink,
        manifest=manifest,
        request=_request(),
    )

    assert task is not None
    assert task.status is TaskStatus.FAILED
    assert task.failure_detail == "1 file(s) failed"
    assert result.uploaded_count == 0
    assert len(result.failed_files) == 1
    assert "declared size" in result.failed_files[0].reason
    assert sink.uploads == []
    assert manifest.entries_by_file_id == {}
    verifier = artifacts[0].payload
    manifest_report = artifacts[1].payload
    assert isinstance(verifier, ObjectVerificationReport)
    assert verifier.verified is False
    assert verifier.reason == "workflow failed before object verification"
    assert isinstance(manifest_report, ManifestReport)
    assert manifest_report.failed_files[0].file_id == "F1"
