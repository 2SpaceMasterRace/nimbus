"""Deterministic backup-channel workflow primitives for Nimbus."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from nimbus_runtime.domain import (
    Artifact,
    ManifestFailureEntry,
    ManifestObjectEntry,
    ManifestReport,
    ObjectVerificationEntry,
    ObjectVerificationReport,
    ProofReceipt,
    Task,
    TaskStatus,
    TaskTransition,
    TenantIdentity,
    is_valid_task_transition,
)
from nimbus_runtime.proof import deterministic_receipt_id

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from nimbus_runtime.stores import ArtifactStore
    from nimbus_runtime.worker import TaskLeaseContext

_DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
_DEFAULT_PAGE_SIZE = 100
_DEFAULT_MAX_PAGES = 3
_MAX_PAGE_SIZE = 1_000
_MAX_SCAN_PAGES = 100
_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ChannelBackupError(RuntimeError):
    """Raised when a backup-channel workflow cannot complete safely."""


class ChannelBackupStateError(ChannelBackupError):
    """Raised when task state changes make the workflow unsafe to continue."""


@dataclass(frozen=True, slots=True)
class BackupChannelRef:
    """Source channel identity for a backup workflow."""

    platform: str
    workspace_id: str
    channel_id: str
    workspace_name: str | None = None
    channel_name: str | None = None


@dataclass(frozen=True, slots=True)
class BackupDestination:
    """Destination object-store location for a channel backup."""

    container: str
    prefix: str = ""


@dataclass(frozen=True, slots=True)
class BackupSourceFile:
    """One source file visible to the backup workflow."""

    file_id: str
    name: str
    content_type: str | None
    size_bytes: int
    created_at: datetime | None = None
    metadata: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class BackupFileScan:
    """Bounded source-channel file listing."""

    files: tuple[BackupSourceFile, ...]
    total_count: int | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class BackupManifestEntry:
    """Evidence that one source file maps to one verified object key."""

    tenant: TenantIdentity
    channel: BackupChannelRef
    file_id: str
    name: str
    content_type: str | None
    size_bytes: int
    content_sha256: str
    destination: BackupDestination
    object_key: str
    saved_at: datetime


@dataclass(frozen=True, slots=True)
class BackupSavedFile:
    """A file saved or deduped by the workflow."""

    file: BackupSourceFile
    manifest: BackupManifestEntry
    uploaded: bool
    deduped_from_key: str | None = None


@dataclass(frozen=True, slots=True)
class BackupSkippedFile:
    """A source file skipped for a non-error reason."""

    file: BackupSourceFile
    reason: str
    existing_key: str | None = None


@dataclass(frozen=True, slots=True)
class BackupFailedFile:
    """A source file that could not be backed up safely."""

    file: BackupSourceFile
    reason: str


@dataclass(frozen=True, slots=True)
class ChannelBackupResult:
    """Summary and evidence from one channel backup workflow run."""

    channel: BackupChannelRef
    destination: BackupDestination
    scanned_count: int
    matched_count: int
    total_count: int | None
    truncated: bool
    saved_files: tuple[BackupSavedFile, ...]
    skipped_files: tuple[BackupSkippedFile, ...]
    failed_files: tuple[BackupFailedFile, ...]
    manifest_artifact_id: str
    verifier_artifact_id: str

    @property
    def uploaded_count(self) -> int:
        """Return how many source files caused a new object upload."""
        return sum(1 for saved in self.saved_files if saved.uploaded)

    @property
    def deduped_count(self) -> int:
        """Return how many files reused an existing verified object."""
        return sum(1 for saved in self.saved_files if not saved.uploaded)


@dataclass(frozen=True, slots=True)
class ChannelBackupRequest:
    """Configuration for a deterministic channel backup recipe."""

    tenant: TenantIdentity
    channel: BackupChannelRef
    destination: BackupDestination
    include_content_types: frozenset[str] = frozenset()
    include_filename_suffixes: tuple[str, ...] = ()
    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES
    page_size: int = _DEFAULT_PAGE_SIZE
    max_pages: int = _DEFAULT_MAX_PAGES
    dedupe_by_hash: bool = True

    def __post_init__(self) -> None:
        """Validate workflow bounds and tenant/channel consistency."""
        if self.tenant.platform != self.channel.platform:
            msg = "request tenant platform must match channel platform"
            raise ValueError(msg)
        if self.tenant.workspace_id != self.channel.workspace_id:
            msg = "request tenant workspace must match channel workspace"
            raise ValueError(msg)
        if self.max_file_bytes < 1:
            msg = "max_file_bytes must be positive"
            raise ValueError(msg)
        if not 1 <= self.page_size <= _MAX_PAGE_SIZE:
            msg = "page_size must be between 1 and 1000"
            raise ValueError(msg)
        if not 1 <= self.max_pages <= _MAX_SCAN_PAGES:
            msg = "max_pages must be between 1 and 100"
            raise ValueError(msg)

    def matches(self, file: BackupSourceFile) -> bool:
        """Return whether a source file is in scope for this request."""
        if (
            self.include_content_types
            and file.content_type not in self.include_content_types
        ):
            return False
        if not self.include_filename_suffixes:
            return True
        lowered = file.name.lower()
        return any(
            lowered.endswith(suffix.lower())
            for suffix in self.include_filename_suffixes
        )


class ChannelBackupSource(Protocol):
    """Source-system capability needed by the backup workflow."""

    def list_files(
        self,
        channel: BackupChannelRef,
        *,
        page_size: int,
        max_pages: int,
    ) -> BackupFileScan:
        """Return a bounded source file scan."""

    def download_file(
        self,
        file: BackupSourceFile,
        *,
        max_bytes: int,
    ) -> bytes:
        """Download one source file with a hard byte bound."""


class ChannelBackupObjectSink(Protocol):
    """Destination object-store capability needed by the backup workflow."""

    def upload_bytes(
        self,
        *,
        destination: BackupDestination,
        key: str,
        content: bytes,
    ) -> None:
        """Upload object bytes to the destination."""

    def verify_object(
        self,
        *,
        destination: BackupDestination,
        key: str,
        content_sha256: str,
        size_bytes: int,
    ) -> bool:
        """Return whether the destination object matches expected evidence."""


class ChannelBackupManifestStore(Protocol):
    """Durable manifest capability for channel backup evidence."""

    def list_entries(
        self,
        *,
        tenant: TenantIdentity,
        channel: BackupChannelRef,
    ) -> Sequence[BackupManifestEntry]:
        """Return existing manifest entries for one tenant-scoped channel."""

    def record_entry(self, entry: BackupManifestEntry) -> None:
        """Persist or replace manifest evidence for one source file."""


@dataclass(frozen=True, slots=True)
class _SaveOutcome:
    """Internal result of saving one source file."""

    saved_file: BackupSavedFile | None
    failed_file: BackupFailedFile | None
    verification_entries: tuple[ObjectVerificationEntry, ...]


@dataclass(frozen=True, slots=True)
class _WorkflowArtifacts:
    """Artifacts created for one workflow run."""

    verifier: Artifact
    manifest: Artifact
    proof: Artifact


@dataclass(frozen=True, slots=True)
class _ManifestArtifactDraft:
    """Inputs needed to create one manifest artifact."""

    request: ChannelBackupRequest
    scan: BackupFileScan
    matched_files: Sequence[BackupSourceFile]
    saved: Sequence[BackupSavedFile]
    skipped: Sequence[BackupSkippedFile]
    failed: Sequence[BackupFailedFile]
    existing_by_file_id: Mapping[str, BackupManifestEntry]
    verifier_artifact_id: str


@dataclass(frozen=True, slots=True)
class ChannelBackupWorkflow:
    """Run the deterministic backup-channel recipe under a task lease."""

    source: ChannelBackupSource
    sink: ChannelBackupObjectSink
    manifest_store: ChannelBackupManifestStore
    artifact_store: ArtifactStore
    clock: Callable[[], datetime] | None = None

    async def run(
        self,
        *,
        context: TaskLeaseContext,
        request: ChannelBackupRequest,
    ) -> ChannelBackupResult:
        """Execute one backup-channel workflow."""
        self._assert_same_tenant(context=context, request=request)
        self._advance(
            context=context,
            next_status=TaskStatus.SCANNING,
            event_type="channel_backup_scanning",
            payload={"channel_id": request.channel.channel_id},
        )
        scan = self.source.list_files(
            request.channel,
            page_size=request.page_size,
            max_pages=request.max_pages,
        )
        matched_files = tuple(file for file in scan.files if request.matches(file))

        self._advance(
            context=context,
            next_status=TaskStatus.DIFFING,
            event_type="channel_backup_diffing",
            payload={
                "scanned_count": len(scan.files),
                "matched_count": len(matched_files),
                "truncated": scan.truncated,
            },
        )
        existing_entries = self.manifest_store.list_entries(
            tenant=request.tenant,
            channel=request.channel,
        )
        existing_by_file_id = {entry.file_id: entry for entry in existing_entries}
        entry_by_hash = {entry.content_sha256: entry for entry in existing_entries}
        pending_files: list[BackupSourceFile] = []
        skipped: list[BackupSkippedFile] = []
        failed: list[BackupFailedFile] = []
        saved: list[BackupSavedFile] = []
        verification_entries: list[ObjectVerificationEntry] = []

        for file in matched_files:
            existing = existing_by_file_id.get(file.file_id)
            if existing is not None and existing.size_bytes == file.size_bytes:
                skipped.append(
                    BackupSkippedFile(
                        file=file,
                        reason="already_saved",
                        existing_key=existing.object_key,
                    )
                )
                continue
            if file.size_bytes > request.max_file_bytes:
                failed.append(
                    BackupFailedFile(
                        file=file,
                        reason=(
                            f"source declares {file.size_bytes} bytes, above "
                            f"the {request.max_file_bytes} byte limit"
                        ),
                    )
                )
                continue
            pending_files.append(file)

        if not pending_files:
            artifacts = self._create_workflow_artifacts(
                context=context,
                draft=_ManifestArtifactDraft(
                    request=request,
                    scan=scan,
                    matched_files=matched_files,
                    saved=saved,
                    skipped=skipped,
                    failed=failed,
                    existing_by_file_id=existing_by_file_id,
                    verifier_artifact_id="",
                ),
                verification_entries=(),
            )
            self._finish_without_uploads(
                context=context,
                failed=failed,
                saved=saved,
                skipped=skipped,
                artifacts=artifacts,
            )
            return ChannelBackupResult(
                channel=request.channel,
                destination=request.destination,
                scanned_count=len(scan.files),
                matched_count=len(matched_files),
                total_count=scan.total_count,
                truncated=scan.truncated,
                saved_files=tuple(saved),
                skipped_files=tuple(skipped),
                failed_files=tuple(failed),
                manifest_artifact_id=artifacts.manifest.artifact_id,
                verifier_artifact_id=artifacts.verifier.artifact_id,
            )

        self._advance(
            context=context,
            next_status=TaskStatus.APPLYING,
            event_type="channel_backup_applying",
            payload={"pending_count": len(pending_files)},
        )
        for file in pending_files:
            try:
                outcome = self._save_one(
                    request=request,
                    file=file,
                    entry_by_hash=entry_by_hash,
                )
            except ChannelBackupError as exc:
                outcome = _SaveOutcome(
                    saved_file=None,
                    failed_file=BackupFailedFile(file=file, reason=str(exc)),
                    verification_entries=(),
                )
            verification_entries.extend(outcome.verification_entries)
            if outcome.failed_file is not None:
                failed.append(outcome.failed_file)
                continue
            if outcome.saved_file is None:
                msg = f"backup save produced no result for {file.file_id!r}"
                raise ChannelBackupError(msg)
            saved.append(outcome.saved_file)
            entry_by_hash[outcome.saved_file.manifest.content_sha256] = (
                outcome.saved_file.manifest
            )

        self._advance(
            context=context,
            next_status=TaskStatus.VERIFYING,
            event_type="channel_backup_verifying",
            payload={
                "saved_count": len(saved),
                "failed_count": len(failed),
            },
        )
        artifacts = self._create_workflow_artifacts(
            context=context,
            draft=_ManifestArtifactDraft(
                request=request,
                scan=scan,
                matched_files=matched_files,
                saved=saved,
                skipped=skipped,
                failed=failed,
                existing_by_file_id=existing_by_file_id,
                verifier_artifact_id="",
            ),
            verification_entries=verification_entries,
        )
        self._advance(
            context=context,
            next_status=TaskStatus.FAILED if failed else TaskStatus.DONE,
            event_type="channel_backup_failed" if failed else "channel_backup_done",
            payload={
                "uploaded_count": sum(1 for item in saved if item.uploaded),
                "deduped_count": sum(1 for item in saved if not item.uploaded),
                "skipped_count": len(skipped),
                "failed_count": len(failed),
                "manifest_artifact_id": artifacts.manifest.artifact_id,
                "verifier_artifact_id": artifacts.verifier.artifact_id,
                "proof_receipt_id": artifacts.proof.artifact_id,
            },
            failure_detail=f"{len(failed)} file(s) failed" if failed else None,
        )
        return ChannelBackupResult(
            channel=request.channel,
            destination=request.destination,
            scanned_count=len(scan.files),
            matched_count=len(matched_files),
            total_count=scan.total_count,
            truncated=scan.truncated,
            saved_files=tuple(saved),
            skipped_files=tuple(skipped),
            failed_files=tuple(failed),
            manifest_artifact_id=artifacts.manifest.artifact_id,
            verifier_artifact_id=artifacts.verifier.artifact_id,
        )

    def _save_one(
        self,
        *,
        request: ChannelBackupRequest,
        file: BackupSourceFile,
        entry_by_hash: Mapping[str, BackupManifestEntry],
    ) -> _SaveOutcome:
        content = self.source.download_file(file, max_bytes=request.max_file_bytes)
        if len(content) > request.max_file_bytes:
            msg = (
                f"downloaded {len(content)} bytes, above the "
                f"{request.max_file_bytes} byte limit"
            )
            raise ChannelBackupError(msg)
        if len(content) != file.size_bytes:
            msg = (
                f"declared size {file.size_bytes} bytes did not match "
                f"downloaded size {len(content)} bytes"
            )
            raise ChannelBackupError(msg)
        digest = hashlib.sha256(content).hexdigest()
        verification_entries: list[ObjectVerificationEntry] = []
        duplicate = entry_by_hash.get(digest) if request.dedupe_by_hash else None
        if duplicate is not None:
            duplicate_verified = self.sink.verify_object(
                destination=duplicate.destination,
                key=duplicate.object_key,
                content_sha256=digest,
                size_bytes=len(content),
            )
            verification_entries.append(
                ObjectVerificationEntry(
                    file_id=file.file_id,
                    object_key=duplicate.object_key,
                    size_bytes=len(content),
                    sha256_hex=digest,
                    verified=duplicate_verified,
                    reason=None if duplicate_verified else "dedupe_candidate_mismatch",
                )
            )
            if duplicate_verified:
                manifest = self._manifest_entry(
                    request=request,
                    file=file,
                    object_key=duplicate.object_key,
                    digest=digest,
                    size_bytes=len(content),
                )
                self.manifest_store.record_entry(manifest)
                return _SaveOutcome(
                    saved_file=BackupSavedFile(
                        file=file,
                        manifest=manifest,
                        uploaded=False,
                        deduped_from_key=duplicate.object_key,
                    ),
                    failed_file=None,
                    verification_entries=tuple(verification_entries),
                )

        key = self._object_key(request=request, file=file)
        self.sink.upload_bytes(
            destination=request.destination,
            key=key,
            content=content,
        )
        uploaded_verified = self.sink.verify_object(
            destination=request.destination,
            key=key,
            content_sha256=digest,
            size_bytes=len(content),
        )
        verification_entries.append(
            ObjectVerificationEntry(
                file_id=file.file_id,
                object_key=key,
                size_bytes=len(content),
                sha256_hex=digest,
                verified=uploaded_verified,
                reason=None if uploaded_verified else "uploaded_object_mismatch",
            )
        )
        if not uploaded_verified:
            return _SaveOutcome(
                saved_file=None,
                failed_file=BackupFailedFile(
                    file=file,
                    reason=f"uploaded object {key!r} failed hash/size verification",
                ),
                verification_entries=tuple(verification_entries),
            )
        manifest = self._manifest_entry(
            request=request,
            file=file,
            object_key=key,
            digest=digest,
            size_bytes=len(content),
        )
        self.manifest_store.record_entry(manifest)
        return _SaveOutcome(
            saved_file=BackupSavedFile(file=file, manifest=manifest, uploaded=True),
            failed_file=None,
            verification_entries=tuple(verification_entries),
        )

    def _manifest_entry(
        self,
        *,
        request: ChannelBackupRequest,
        file: BackupSourceFile,
        object_key: str,
        digest: str,
        size_bytes: int,
    ) -> BackupManifestEntry:
        return BackupManifestEntry(
            tenant=request.tenant,
            channel=request.channel,
            file_id=file.file_id,
            name=file.name,
            content_type=file.content_type,
            size_bytes=size_bytes,
            content_sha256=digest,
            destination=request.destination,
            object_key=object_key,
            saved_at=self._now(),
        )

    def _finish_without_uploads(
        self,
        *,
        context: TaskLeaseContext,
        failed: Sequence[BackupFailedFile],
        saved: Sequence[BackupSavedFile],
        skipped: Sequence[BackupSkippedFile],
        artifacts: _WorkflowArtifacts,
    ) -> None:
        self._advance(
            context=context,
            next_status=TaskStatus.FAILED if failed else TaskStatus.DONE,
            event_type="channel_backup_failed" if failed else "channel_backup_done",
            payload={
                "uploaded_count": sum(1 for item in saved if item.uploaded),
                "deduped_count": sum(1 for item in saved if not item.uploaded),
                "skipped_count": len(skipped),
                "failed_count": len(failed),
                "manifest_artifact_id": artifacts.manifest.artifact_id,
                "verifier_artifact_id": artifacts.verifier.artifact_id,
                "proof_receipt_id": artifacts.proof.artifact_id,
            },
            failure_detail=f"{len(failed)} file(s) failed" if failed else None,
        )

    def _create_workflow_artifacts(
        self,
        *,
        context: TaskLeaseContext,
        draft: _ManifestArtifactDraft,
        verification_entries: Sequence[ObjectVerificationEntry],
    ) -> _WorkflowArtifacts:
        verifier = self._create_verifier_artifact(
            context=context,
            entries=verification_entries,
            failed=draft.failed,
        )
        manifest = self._create_manifest_artifact(
            context=context,
            draft=_ManifestArtifactDraft(
                request=draft.request,
                scan=draft.scan,
                matched_files=draft.matched_files,
                saved=draft.saved,
                skipped=draft.skipped,
                failed=draft.failed,
                existing_by_file_id=draft.existing_by_file_id,
                verifier_artifact_id=verifier.artifact_id,
            ),
        )
        proof = self._create_proof_receipt_artifact(
            context=context,
            verifier=verifier,
            manifest=manifest,
        )
        return _WorkflowArtifacts(verifier=verifier, manifest=manifest, proof=proof)

    def _create_verifier_artifact(
        self,
        *,
        context: TaskLeaseContext,
        entries: Sequence[ObjectVerificationEntry],
        failed: Sequence[BackupFailedFile],
    ) -> Artifact:
        verified = not failed and all(entry.verified for entry in entries)
        reason = None
        if failed and not entries:
            reason = "workflow failed before object verification"
        elif not verified:
            reason = "one or more files failed backup or verification"
        return self.artifact_store.create(
            artifact=Artifact(
                artifact_id=_workflow_artifact_id(
                    context=context,
                    kind="verification_report",
                ),
                tenant=context.task.tenant,
                session_id=context.task.session_id,
                action_id=None,
                kind="verification_report",
                uri=None,
                payload=ObjectVerificationReport(
                    verifier="sha256_size",
                    subject="channel_backup",
                    verified=verified,
                    entries=tuple(entries),
                    reason=reason,
                ),
                created_at=self._now(),
            ),
            actor=context.task.created_by,
        )

    def _create_manifest_artifact(
        self,
        *,
        context: TaskLeaseContext,
        draft: _ManifestArtifactDraft,
    ) -> Artifact:
        return self.artifact_store.create(
            artifact=Artifact(
                artifact_id=_workflow_artifact_id(
                    context=context,
                    kind="manifest",
                ),
                tenant=context.task.tenant,
                session_id=context.task.session_id,
                action_id=None,
                kind="manifest",
                uri=None,
                payload=ManifestReport(
                    source_platform=draft.request.channel.platform,
                    workspace_id=draft.request.channel.workspace_id,
                    channel_id=draft.request.channel.channel_id,
                    destination_container=draft.request.destination.container,
                    destination_prefix=draft.request.destination.prefix,
                    scanned_count=len(draft.scan.files),
                    matched_count=len(draft.matched_files),
                    total_count=draft.scan.total_count,
                    truncated=draft.scan.truncated,
                    object_entries=(
                        *tuple(
                            _manifest_object_from_saved_file(item)
                            for item in draft.saved
                        ),
                        *tuple(
                            _manifest_object_from_skipped_file(
                                item,
                                draft.existing_by_file_id,
                            )
                            for item in draft.skipped
                            if item.file.file_id in draft.existing_by_file_id
                        ),
                    ),
                    failed_files=tuple(
                        ManifestFailureEntry(
                            file_id=item.file.file_id,
                            name=item.file.name,
                            reason=item.reason,
                        )
                        for item in draft.failed
                    ),
                    verifier_artifact_id=draft.verifier_artifact_id,
                ),
                created_at=self._now(),
            ),
            actor=context.task.created_by,
        )

    def _create_proof_receipt_artifact(
        self,
        *,
        context: TaskLeaseContext,
        verifier: Artifact,
        manifest: Artifact,
    ) -> Artifact:
        linked_ids = (verifier.artifact_id, manifest.artifact_id)
        receipt_id = deterministic_receipt_id(
            tenant=context.task.tenant,
            subject="channel_backup",
            task_id=context.task.task_id,
            action_id=None,
            manifest_artifact_id=manifest.artifact_id,
            verifier_artifact_id=verifier.artifact_id,
            linked_artifact_ids=linked_ids,
        )
        artifact_digests = {
            verifier.artifact_id: verifier.payload_digest or "",
            manifest.artifact_id: manifest.payload_digest or "",
        }
        return self.artifact_store.create(
            artifact=Artifact(
                artifact_id=receipt_id,
                tenant=context.task.tenant,
                session_id=context.task.session_id,
                action_id=None,
                kind="proof_receipt",
                uri=None,
                payload=ProofReceipt(
                    receipt_id=receipt_id,
                    tenant=context.task.tenant,
                    subject="channel_backup",
                    outcome=(
                        "verified"
                        if getattr(verifier.payload, "verified", False)
                        else "incomplete"
                    ),
                    summary=(
                        "Channel backup wrote a verifier artifact and a manifest "
                        "artifact. This receipt binds both artifacts so CLI and "
                        "Slack can validate the same evidence."
                    ),
                    task_id=context.task.task_id,
                    action_id=None,
                    manifest_artifact_id=manifest.artifact_id,
                    verifier_artifact_id=verifier.artifact_id,
                    linked_artifact_ids=linked_ids,
                    artifact_digests=artifact_digests,
                    session_id=context.task.session_id,
                    event_range_start=None,
                    event_range_end=None,
                    policy_version="runtime-default-v1",
                    idempotency_key=context.task.idempotency_key,
                    next_steps=(
                        "Run `nimbus proof show latest --json` for "
                        "machine-readable proof.",
                        "Run `nimbus verify manifest "
                        f"{manifest.artifact_id}` to check live storage drift.",
                    ),
                    created_at=self._now(),
                ),
                created_at=self._now(),
            ),
            actor=context.task.created_by,
        )

    def _advance(
        self,
        *,
        context: TaskLeaseContext,
        next_status: TaskStatus,
        event_type: str,
        payload: Mapping[str, object],
        failure_detail: str | None = None,
    ) -> Task:
        current = context.current_task()
        if current is None:
            msg = f"task {context.task.task_id!r} disappeared during backup"
            raise ChannelBackupStateError(msg)
        if current.status == next_status:
            return current
        if not is_valid_task_transition(
            expected=current.status,
            next_status=next_status,
        ):
            msg = (
                f"cannot move backup task {current.task_id!r} from "
                f"{current.status.value} to {next_status.value}"
            )
            raise ChannelBackupStateError(msg)
        updated = context.task_store.transition(
            tenant=current.tenant,
            task_id=current.task_id,
            transition=TaskTransition(
                expected=current.status,
                next_status=next_status,
                event_type=event_type,
                event_payload=dict(payload),
                failure_detail=failure_detail,
            ),
        )
        if updated is None:
            msg = f"task {current.task_id!r} changed state during backup"
            raise ChannelBackupStateError(msg)
        return updated

    def _object_key(
        self,
        *,
        request: ChannelBackupRequest,
        file: BackupSourceFile,
    ) -> str:
        parts = [part for part in (request.destination.prefix.strip("/"),) if part]
        parts.extend(
            [
                _safe_segment(request.channel.platform, "source"),
                _safe_segment(
                    request.channel.workspace_name or request.channel.workspace_id,
                    request.channel.workspace_id,
                ),
                _safe_segment(
                    request.channel.channel_name or request.channel.channel_id,
                    request.channel.channel_id,
                ),
                _safe_segment(file.file_id, "file"),
                _safe_filename(file.name, file.file_id),
            ]
        )
        return "/".join(parts)

    def _assert_same_tenant(
        self,
        *,
        context: TaskLeaseContext,
        request: ChannelBackupRequest,
    ) -> None:
        if context.task.tenant != request.tenant:
            msg = "backup request tenant does not match task tenant"
            raise ChannelBackupStateError(msg)

    def _now(self) -> datetime:
        if self.clock is None:
            return datetime.now(UTC)
        timestamp = self.clock()
        if not isinstance(timestamp, datetime):
            msg = "backup workflow clock must return datetime"
            raise TypeError(msg)
        return timestamp


def _safe_segment(value: str, fallback: str) -> str:
    normalized = _SAFE_SEGMENT_RE.sub("-", value.strip()).strip("-._")
    return normalized[:120] if normalized else fallback


def _safe_filename(value: str, fallback: str) -> str:
    return _safe_segment(value.replace("/", "-"), fallback)


def _workflow_artifact_id(*, context: TaskLeaseContext, kind: str) -> str:
    identity = f"{context.task.tenant.tenant_id}:{context.task.task_id}:{kind}"
    return f"art-{uuid.uuid5(uuid.NAMESPACE_URL, identity).hex}"


def _manifest_object_from_saved_file(item: BackupSavedFile) -> ManifestObjectEntry:
    return ManifestObjectEntry(
        file_id=item.file.file_id,
        name=item.file.name,
        object_key=item.manifest.object_key,
        size_bytes=item.manifest.size_bytes,
        sha256_hex=item.manifest.content_sha256,
        disposition="uploaded" if item.uploaded else "deduped",
        deduped_from_key=item.deduped_from_key,
    )


def _manifest_object_from_skipped_file(
    item: BackupSkippedFile,
    existing_by_file_id: Mapping[str, BackupManifestEntry],
) -> ManifestObjectEntry:
    existing = existing_by_file_id[item.file.file_id]
    return ManifestObjectEntry(
        file_id=item.file.file_id,
        name=item.file.name,
        object_key=existing.object_key,
        size_bytes=existing.size_bytes,
        sha256_hex=existing.content_sha256,
        disposition=item.reason,
        deduped_from_key=None,
    )
