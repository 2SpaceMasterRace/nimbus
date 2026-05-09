"""Core Nimbus session/action domain primitives."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

ProviderName = Literal["s3", "gcs", "dropbox", "drive"]
ArtifactKind = Literal[
    "conflict_artifact",
    "delete_report",
    "drift_report",
    "provider_health",
    "proof_receipt",
    "repair_receipt",
    "migration_decision_packet",
    "storage_mutation_report",
    "upload_report",
    "manifest",
    "verification_report",
]
DriftObjectStatus = Literal["match", "mismatch", "missing", "unknown", "bucket_missing"]
GenerationStatus = Literal["complete", "partial", "failed"]
StorageChangeStatus = Literal[
    "proposed",
    "approved",
    "applied",
    "abandoned",
    "conflicted",
    "failed",
]
ActorAuthSource = Literal[
    "slack_signed_event",
    "cli_local",
    "github_oauth",
    "oidc",
    "service_account",
]


@dataclass(frozen=True, slots=True)
class TenantIdentity:
    """Tenant isolation boundary for all durable Nimbus state."""

    platform: str
    workspace_id: str

    @property
    def tenant_id(self) -> str:
        """Return the canonical tenant key."""
        return f"{self.platform}:{self.workspace_id}"


@dataclass(frozen=True, slots=True)
class VerifiedActor:
    """Verified human or service principal that initiated a Nimbus operation."""

    tenant: TenantIdentity
    user_id: str
    auth_source: ActorAuthSource
    bridge_id: str | None
    verified_at: datetime

    @property
    def principal_key(self) -> str:
        """Return the tenant-scoped principal key."""
        return f"{self.tenant.tenant_id}:{self.user_id}"


@dataclass(frozen=True, slots=True)
class ObjectRef:
    """Tenant-scoped pointer to one provider object."""

    provider: ProviderName
    container: str
    object_name: str
    version_id: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectPointer:
    """Canonical provider-neutral identity for one storage object.

    ``ObjectRef`` is the existing runtime target shape used by current actions.
    ``ObjectPointer`` is the richer proof/version-control identity that future
    providers can satisfy without leaking provider SDK objects inward.
    """

    provider: ProviderName
    container: str
    object_name: str
    account_id: str | None = None
    region: str | None = None
    version_id: str | None = None
    content_sha256: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True, slots=True)
class ProtectedRoot:
    """Tenant-owned storage scope that Nimbus may snapshot and verify.

    The MVP supports S3-backed roots. The shape is provider-neutral so future
    providers can plug in at the capability boundary without changing runtime
    state or CLI contracts.
    """

    root_id: str
    tenant: TenantIdentity
    provider: ProviderName
    container: str
    prefix: str
    display_name: str
    protected_by: VerifiedActor
    created_at: datetime
    updated_at: datetime
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Generation:
    """Immutable snapshot record for one protected root."""

    generation_id: str
    tenant: TenantIdentity
    root_id: str
    manifest_artifact_id: str
    manifest_digest: str
    object_count: int
    total_bytes: int
    status: GenerationStatus
    created_by: VerifiedActor
    created_at: datetime
    base_generation_id: str | None
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class GenerationManifest:
    """Canonical manifest payload for a protected-root generation artifact."""

    root_id: str
    generation_id: str
    manifest_digest: str
    provider: ProviderName
    container: str
    prefix: str
    objects: tuple[ObjectPointer, ...]
    object_count: int
    total_bytes: int
    partial: bool
    created_at: datetime


class ActionKind(StrEnum):
    """Supported durable Nimbus action kinds."""

    LIST_FILES = "list_files"
    GET_FILE_INFO = "get_file_info"
    UPLOAD_ATTACHMENT = "upload_attachment"
    DELETE_FILE = "delete_file"
    COPY_FILE = "copy_file"
    MOVE_FILE = "move_file"
    WRITE_FILE = "write_file"
    SUMMARIZE_PREFIX = "summarize_prefix"
    SPAWN_CHILD_SESSION = "spawn_child_session"


class PolicyDecision(StrEnum):
    """Possible authorization outcomes for a Nimbus operation."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"
    REQUIRES_ADMIN_GRANT = "requires_admin_grant"
    REQUIRES_CLARIFICATION = "requires_clarification"

    # Backwards-compatible names used by earlier runtime slices.
    REQUIRE_CONFIRMATION = "requires_approval"
    REQUIRE_ADMIN_APPROVAL = "requires_admin_grant"


class ActionStatus(StrEnum):
    """Durable action state-machine statuses."""

    PROPOSED = "proposed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AUTHORIZED = "authorized"
    QUEUED = "queued"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class TaskStatus(StrEnum):
    """Durable background task state-machine statuses."""

    CREATED = "created"
    PLANNING = "planning"
    SCANNING = "scanning"
    DIFFING = "diffing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPLYING = "applying"
    VERIFYING = "verifying"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REJECTED = "rejected"


class PlanStatus(StrEnum):
    """Durable preview state for proposed Nimbus work."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    EXPIRED = "expired"
    APPLIED = "applied"
    CANCELED = "canceled"


class PlanRiskLevel(StrEnum):
    """Risk bucket that drives preview and approval behavior."""

    READ_ONLY = "read_only"
    SMALL_WRITE = "small_write"
    LARGE_WRITE = "large_write"
    DESTRUCTIVE = "destructive"
    ADMIN_SCOPE = "admin_scope"


class OperationMode(StrEnum):
    """Execution mode that governs what a StorageAgent request may do.

    ``READ_ONLY``   — scan, list, search, hash, diff_manifest only.
    ``PLAN``        — all read operations + propose_plan + stage_upload.
    ``APPLY``       — full write surface including promote, delete, restore.
    ``WATCH``       — read-only view of running tasks and their state.
    ``REVIEW``      — surfaces pending approvals alongside read operations.
    ``POLICY_ADMIN``— full surface, reserved for administrative actors.
    """

    READ_ONLY = "read_only"
    PLAN = "plan"
    APPLY = "apply"
    WATCH = "watch"
    REVIEW = "review"
    POLICY_ADMIN = "policy_admin"


class ApprovalStatus(StrEnum):
    """Durable approval state for risky or destructive work."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalChoice(StrEnum):
    """Human decision choices accepted by the runtime."""

    APPROVE = "approve"
    REJECT = "reject"


class RestoreStrategy(StrEnum):
    """How Nimbus believes a deleted object can be recovered."""

    NOT_REQUIRED = "not_required"
    S3_VERSION = "s3_version"
    TRASH_COPY = "trash_copy"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DeleteFileInput:
    """Typed input for a delete-file action."""

    remote_path: str


@dataclass(frozen=True, slots=True)
class UploadAttachmentInput:
    """Typed input for an attachment upload action."""

    platform_file_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256_hex: str | None
    remote_path: str


@dataclass(frozen=True, slots=True)
class CopyFileInput:
    """Typed input for a copy-file action."""

    source_path: str
    dest_path: str
    overwrite: bool


@dataclass(frozen=True, slots=True)
class MoveFileInput:
    """Typed input for a move-file action."""

    source_path: str
    dest_path: str
    overwrite: bool


@dataclass(frozen=True, slots=True)
class WriteFileInput:
    """Typed input for a write-file action."""

    remote_path: str
    content_base64: str
    content_sha256_hex: str
    size_bytes: int
    encoding: str
    overwrite: bool


@dataclass(frozen=True, slots=True)
class PolicyDecisionRecord:
    """Durable evidence for one policy decision."""

    tenant_id: str
    actor_id: str
    operation: str
    target: str
    decision: PolicyDecision
    reason: str
    policy_version: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class DeleteFileResult:
    """Typed result for a delete-file action."""

    remote_path: str
    deleted: bool
    version_id: str | None
    artifact_id: str | None = None

    def with_artifact(self, artifact_id: str) -> DeleteFileResult:
        """Return this result linked to its evidence artifact."""
        return replace(self, artifact_id=artifact_id)


@dataclass(frozen=True, slots=True)
class UploadAttachmentResult:
    """Typed result for an attachment upload action."""

    remote_path: str
    size_bytes: int
    sha256_hex: str
    artifact_id: str | None = None

    def with_artifact(self, artifact_id: str) -> UploadAttachmentResult:
        """Return this result linked to its evidence artifact."""
        return replace(self, artifact_id=artifact_id)


@dataclass(frozen=True, slots=True)
class CopyFileResult:
    """Typed result for a copy-file action."""

    source_path: str
    dest_path: str
    overwrote: bool
    dest_size_bytes: int | None
    dest_version_id: str | None
    artifact_id: str | None = None

    def with_artifact(self, artifact_id: str) -> CopyFileResult:
        """Return this result linked to its evidence artifact."""
        return replace(self, artifact_id=artifact_id)


@dataclass(frozen=True, slots=True)
class MoveFileResult:
    """Typed result for a move-file action."""

    source_path: str
    dest_path: str
    overwrote: bool
    source_deleted: bool
    delete_version_id: str | None
    dest_size_bytes: int | None
    dest_version_id: str | None
    artifact_id: str | None = None

    def with_artifact(self, artifact_id: str) -> MoveFileResult:
        """Return this result linked to its evidence artifact."""
        return replace(self, artifact_id=artifact_id)


@dataclass(frozen=True, slots=True)
class WriteFileResult:
    """Typed result for a write-file action."""

    remote_path: str
    bytes_written: int
    sha256_hex: str
    encoding: str
    overwrote: bool
    dest_version_id: str | None
    artifact_id: str | None = None

    def with_artifact(self, artifact_id: str) -> WriteFileResult:
        """Return this result linked to its evidence artifact."""
        return replace(self, artifact_id=artifact_id)


@dataclass(frozen=True, slots=True)
class ActionFailure:
    """Typed failure recorded for an action transition."""

    detail: str
    remote_path: str | None = None


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """Recovery story for a delete action."""

    original_key: str
    strategy: RestoreStrategy
    restorable: bool
    trash_key: str | None
    version_id: str | None
    sha256_hex: str | None
    size_bytes: int | None
    deleted_by: str | None
    deleted_at: datetime | None
    restore_command: str | None
    limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeleteReport:
    """Evidence payload for a completed delete action."""

    remote_path: str
    deleted: bool
    version_id: str | None
    restore_plan: RestorePlan


@dataclass(frozen=True, slots=True)
class UploadReport:
    """Evidence payload for a completed upload action."""

    remote_path: str
    filename: str
    size_bytes: int
    sha256_hex: str


@dataclass(frozen=True, slots=True)
class StorageMutationReport:
    """Evidence payload for a completed runtime-owned storage mutation."""

    operation: str
    source_path: str | None
    dest_path: str | None
    remote_path: str | None
    size_bytes: int | None
    sha256_hex: str | None
    overwrote: bool
    source_deleted: bool | None
    dest_version_id: str | None
    verified: bool
    verifier: str


@dataclass(frozen=True, slots=True)
class ManifestObjectEntry:
    """One object represented in a durable manifest artifact."""

    file_id: str
    name: str
    object_key: str
    size_bytes: int
    sha256_hex: str
    disposition: str
    deduped_from_key: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestFailureEntry:
    """One source file that could not become a manifest object entry."""

    file_id: str
    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class ManifestReport:
    """Durable manifest summary for a background workflow."""

    source_platform: str
    workspace_id: str
    channel_id: str
    destination_container: str
    destination_prefix: str
    scanned_count: int
    matched_count: int
    total_count: int | None
    truncated: bool
    object_entries: tuple[ManifestObjectEntry, ...]
    failed_files: tuple[ManifestFailureEntry, ...]
    verifier_artifact_id: str | None


@dataclass(frozen=True, slots=True)
class ObjectVerificationEntry:
    """Hash and size verification result for one object."""

    file_id: str
    object_key: str
    size_bytes: int
    sha256_hex: str
    verified: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectVerificationReport:
    """Durable verifier result for object-store evidence."""

    verifier: str
    subject: str
    verified: bool
    entries: tuple[ObjectVerificationEntry, ...]
    reason: str | None = None


class ProviderOutcome(StrEnum):
    """Provider-level outcome taxonomy shared by probes, CLI, and telemetry."""

    SUCCESS = "success"
    AUTH_FAILURE = "auth_failure"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    THROTTLED = "throttled"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_HEALTH_DEGRADED = "provider_health_degraded"
    OUTCOME_AMBIGUOUS = "outcome_ambiguous"
    STALE_MANIFEST = "stale_manifest"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderProbeResult:
    """One bounded live probe against a configured storage provider."""

    probe_name: str
    operation: str
    provider: ProviderName
    container: str
    prefix: str
    object_name: str | None
    region: str | None
    outcome: ProviderOutcome
    latency_ms: int
    item_count: int | None
    request_id: str | None
    error_message: str | None
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderHealthReport:
    """Live-provider health evidence derived from Nimbus probes.

    Status pages and news can add advisory context later, but this payload is
    only authoritative for what Nimbus directly observed against the configured
    bucket/prefix.
    """

    report_id: str
    tenant: TenantIdentity
    provider: ProviderName
    container: str
    prefix: str
    region: str | None
    status: str
    health_score: int
    confidence: str
    evidence_source: str
    generated_at: datetime
    expires_at: datetime
    probes: tuple[ProviderProbeResult, ...]
    advisory_context: tuple[str, ...]
    next_operator_step: str


@dataclass(frozen=True, slots=True)
class RepairReceipt:
    """Evidence that one policy-authorized replica repair preserved hash."""

    receipt_id: str
    lane_id: str
    tenant: TenantIdentity
    source_object_name: str
    replica_object_name: str
    source_sha256: str
    destination_sha256: str
    authority: str
    outcome: str
    repaired_at: datetime
    next_step: str


@dataclass(frozen=True, slots=True)
class DriftObjectEntry:
    """Per-object comparison result from a manifest drift check."""

    object_key: str
    file_id: str
    name: str
    expected_sha256: str
    observed_sha256: str | None
    status: DriftObjectStatus
    size_bytes: int | None
    via_action_id: str | None
    via_actor_id: str | None


@dataclass(frozen=True, slots=True)
class DriftReport:
    """Drift detection result comparing a manifest against live storage."""

    manifest_artifact_id: str
    tenant: TenantIdentity
    checked_at: datetime
    container: str
    prefix: str
    total_count: int
    match_count: int
    mismatch_count: int
    missing_count: int
    unknown_count: int
    bucket_missing: bool
    has_drift: bool
    entries: tuple[DriftObjectEntry, ...]
    via_action_id: str | None


@dataclass(frozen=True, slots=True)
class ProofReceipt:
    """User and machine-verifiable receipt for completed Nimbus work.

    A receipt does not replace verifier or manifest artifacts. It binds the
    proof story together so Slack, CLI, and future reviewers can validate that
    user-visible success is backed by durable evidence.
    """

    receipt_id: str
    tenant: TenantIdentity
    subject: str
    outcome: str
    summary: str
    task_id: str | None
    action_id: str | None
    manifest_artifact_id: str | None
    verifier_artifact_id: str | None
    linked_artifact_ids: tuple[str, ...]
    artifact_digests: Mapping[str, str]
    session_id: str
    event_range_start: int | None
    event_range_end: int | None
    policy_version: str
    idempotency_key: str | None
    next_steps: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MigrationDecisionPacket:
    """Reviewable packet for S3 region/replica migration decisions.

    The MVP packet is evidence, not automatic migration. It records measured
    source facts, explicit assumptions, safety checks, rollback shape, and the
    route-switch plan that would require approval before mutation.
    """

    packet_id: str
    tenant: TenantIdentity
    root_id: str
    source_provider: ProviderName
    source_container: str
    source_prefix: str
    candidate_provider: ProviderName
    candidate_container: str
    candidate_prefix: str
    candidate_region: str | None
    object_count: int
    total_bytes: int
    source_list_latency_ms: int
    estimated_monthly_storage_cost_usd: float
    assumptions: tuple[str, ...]
    safety_checks: tuple[str, ...]
    rollback_plan: str
    route_switch_plan: str
    recommendation: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConflictArtifact:
    """Reviewable conflict produced when a stack target changed after planning."""

    conflict_id: str
    tenant: TenantIdentity
    stack_id: str
    change_id: str
    object_name: str
    expected_digest: str | None
    observed_digest: str | None
    reason: str
    status: str
    next_step: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeOperation:
    """Durable operation-log row for stack proposal, restack, apply, or abandon."""

    operation_id: str
    tenant: TenantIdentity
    stack_id: str
    change_id: str | None
    kind: str
    status: str
    summary: str
    metadata: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StorageChangeRevision:
    """Immutable revision of one storage change."""

    revision_id: str
    change_id: str
    stack_id: str
    base_generation_id: str | None
    target_digest: str | None
    risk_level: PlanRiskLevel
    operation: str
    target: Mapping[str, object]
    reason: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StorageChange:
    """Reviewable unit of storage work inside a stack."""

    change_id: str
    tenant: TenantIdentity
    stack_id: str
    current_revision_id: str
    status: StorageChangeStatus
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StorageChangeStackEntry:
    """Ordering row for one change in a stack."""

    stack_id: str
    change_id: str
    position: int


@dataclass(frozen=True, slots=True)
class StorageChangeStack:
    """Ordered stack of reviewable storage changes."""

    stack_id: str
    tenant: TenantIdentity
    plan_id: str | None
    title: str
    status: StorageChangeStatus
    created_by: VerifiedActor
    created_at: datetime
    updated_at: datetime
    metadata: Mapping[str, object]


ActionInput = (
    DeleteFileInput
    | UploadAttachmentInput
    | CopyFileInput
    | MoveFileInput
    | WriteFileInput
)
ActionResult = (
    DeleteFileResult
    | UploadAttachmentResult
    | CopyFileResult
    | MoveFileResult
    | WriteFileResult
)
ArtifactPayload = (
    ConflictArtifact
    | DeleteReport
    | DriftReport
    | GenerationManifest
    | UploadReport
    | StorageMutationReport
    | ManifestReport
    | MigrationDecisionPacket
    | ObjectVerificationReport
    | ProviderHealthReport
    | ProofReceipt
    | RepairReceipt
)


@dataclass(frozen=True, slots=True)
class ActionTransition:
    """Compare-and-set state transition with its durable event payload."""

    expected: ActionStatus
    next_status: ActionStatus
    event_type: str
    event_payload: Mapping[str, object]
    result: ActionResult | None = None
    failure: ActionFailure | None = None


@dataclass(frozen=True, slots=True)
class Task:
    """Durable background unit of Nimbus work."""

    task_id: str
    tenant: TenantIdentity
    session_id: str
    created_by: VerifiedActor
    status: TaskStatus
    intent: str
    source_ref: str | None
    idempotency_key: str
    metadata: Mapping[str, object]
    failure_detail: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class TaskTransition:
    """Compare-and-set task transition with its durable event payload."""

    expected: TaskStatus
    next_status: TaskStatus
    event_type: str
    event_payload: Mapping[str, object]
    failure_detail: str | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """Durable preview of work before Nimbus mutates external state."""

    plan_id: str
    tenant: TenantIdentity
    session_id: str
    task_id: str | None
    action_id: str | None
    created_by: VerifiedActor
    status: PlanStatus
    risk_level: PlanRiskLevel
    title: str
    summary: str
    target: ObjectRef | None
    estimated_count: int | None
    estimated_bytes: int | None
    idempotency_key: str
    metadata: Mapping[str, object]
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True, slots=True)
class PlanTransition:
    """Compare-and-set plan transition with its durable event payload."""

    expected: PlanStatus
    next_status: PlanStatus
    event_type: str
    event_payload: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Approval:
    """Actor-bound approval record for risky or destructive work."""

    approval_id: str
    tenant: TenantIdentity
    session_id: str
    task_id: str | None
    plan_id: str | None
    action_id: str | None
    requested_by: VerifiedActor
    required_actor_id: str
    allowed_actor_ids: tuple[str, ...]
    status: ApprovalStatus
    risk_level: PlanRiskLevel
    exact_target: str
    reason: str
    idempotency_key: str
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    decided_by: VerifiedActor | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalDecisionResult:
    """Result of attempting to decide an approval."""

    approval: Approval | None
    accepted: bool
    reason: str


@dataclass(frozen=True, slots=True)
class WorkerLease:
    """Short-lived claim that lets one worker execute a task at a time."""

    tenant: TenantIdentity
    task_id: str
    worker_id: str
    lease_until: datetime
    acquired_at: datetime
    heartbeat_at: datetime
    attempt: int


@dataclass(frozen=True, slots=True)
class Action:
    """Durable side-effecting unit of Nimbus work."""

    action_id: str
    tenant: TenantIdentity
    session_id: str
    actor: VerifiedActor
    kind: ActionKind
    target: ObjectRef | None
    status: ActionStatus
    idempotency_key: str
    input: ActionInput
    result: ActionResult | None
    failure: ActionFailure | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    policy_decision: PolicyDecisionRecord | None = None


@dataclass(frozen=True, slots=True)
class SessionEvent:
    """Ordered durable event in a Nimbus session."""

    tenant: TenantIdentity
    session_id: str
    sequence: int
    event_id: str
    event_type: str
    actor: VerifiedActor | None
    payload: Mapping[str, object]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Artifact:
    """Evidence or work product created during a Nimbus session."""

    artifact_id: str
    tenant: TenantIdentity
    session_id: str
    action_id: str | None
    kind: ArtifactKind
    uri: str | None
    payload: ArtifactPayload
    created_at: datetime
    payload_digest: str | None = None


class FutureTimestampError(ValueError):
    """Raised when a workspace projection is requested for a future timestamp."""


@dataclass(frozen=True, slots=True)
class ApprovalSummary:
    """Lightweight approval record for workspace projection snapshots."""

    approval_id: str
    task_id: str | None
    plan_id: str | None
    exact_target: str
    risk_level: str
    required_actor_id: str


@dataclass(frozen=True, slots=True)
class PlanSummary:
    """Lightweight plan record for workspace projection snapshots."""

    plan_id: str
    task_id: str | None
    title: str
    risk_level: str
    status: str


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshot:
    """Workspace state reconstructed from the event log at a past timestamp.

    This is a pure read projection: it has no authority over the live store.
    """

    tenant: TenantIdentity
    at: datetime
    tasks_by_status: Mapping[str, int]
    pending_approvals: tuple[ApprovalSummary, ...]
    pending_plans: tuple[PlanSummary, ...]
    artifact_count: int
    events_replayed: int
    computed_at: datetime
    computation_duration_ms: int


_ALLOWED_TRANSITIONS: Mapping[ActionStatus, frozenset[ActionStatus]] = {
    ActionStatus.PROPOSED: frozenset(
        {
            ActionStatus.AUTHORIZED,
            ActionStatus.AWAITING_CONFIRMATION,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.AWAITING_CONFIRMATION: frozenset(
        {
            ActionStatus.AUTHORIZED,
            ActionStatus.EXPIRED,
            ActionStatus.CANCELLED,
        }
    ),
    ActionStatus.AUTHORIZED: frozenset(
        {
            ActionStatus.QUEUED,
            ActionStatus.EXECUTING,
            ActionStatus.CANCELLED,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.QUEUED: frozenset(
        {
            ActionStatus.EXECUTING,
            ActionStatus.CANCELLED,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.EXECUTING: frozenset(
        {
            ActionStatus.VERIFYING,
            ActionStatus.FAILED_RETRYABLE,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.VERIFYING: frozenset(
        {
            ActionStatus.SUCCEEDED,
            ActionStatus.FAILED_RETRYABLE,
            ActionStatus.FAILED_TERMINAL,
        }
    ),
    ActionStatus.FAILED_RETRYABLE: frozenset(
        {
            ActionStatus.QUEUED,
            ActionStatus.FAILED_TERMINAL,
            ActionStatus.CANCELLED,
        }
    ),
    ActionStatus.SUCCEEDED: frozenset(),
    ActionStatus.FAILED_TERMINAL: frozenset(),
    ActionStatus.EXPIRED: frozenset(),
    ActionStatus.CANCELLED: frozenset(),
}


_ALLOWED_TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.CREATED: frozenset(
        {
            TaskStatus.PLANNING,
            TaskStatus.SCANNING,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.PLANNING: frozenset(
        {
            TaskStatus.SCANNING,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.APPLYING,
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.SCANNING: frozenset(
        {
            TaskStatus.DIFFING,
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.APPLYING,
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.DIFFING: frozenset(
        {
            TaskStatus.AWAITING_APPROVAL,
            TaskStatus.APPLYING,
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.AWAITING_APPROVAL: frozenset(
        {
            TaskStatus.APPLYING,
            TaskStatus.REJECTED,
            TaskStatus.EXPIRED,
            TaskStatus.CANCELED,
            TaskStatus.FAILED,
        }
    ),
    TaskStatus.APPLYING: frozenset(
        {
            TaskStatus.VERIFYING,
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.VERIFYING: frozenset(
        {
            TaskStatus.DONE,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
        }
    ),
    TaskStatus.DONE: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELED: frozenset(),
    TaskStatus.EXPIRED: frozenset(),
    TaskStatus.REJECTED: frozenset(),
}

_ALLOWED_PLAN_TRANSITIONS: Mapping[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.PROPOSED: frozenset(
        {
            PlanStatus.APPROVED,
            PlanStatus.SUPERSEDED,
            PlanStatus.REJECTED,
            PlanStatus.EXPIRED,
            PlanStatus.CANCELED,
        }
    ),
    PlanStatus.APPROVED: frozenset({PlanStatus.APPLIED, PlanStatus.CANCELED}),
    PlanStatus.SUPERSEDED: frozenset(),
    PlanStatus.REJECTED: frozenset(),
    PlanStatus.EXPIRED: frozenset(),
    PlanStatus.APPLIED: frozenset(),
    PlanStatus.CANCELED: frozenset(),
}

_ALLOWED_APPROVAL_TRANSITIONS: Mapping[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
        }
    ),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
}


def is_valid_action_transition(
    *, expected: ActionStatus, next_status: ActionStatus
) -> bool:
    """Return whether an action may move from ``expected`` to ``next_status``."""
    return next_status in _ALLOWED_TRANSITIONS[expected]


def validate_action_transition(
    *, expected: ActionStatus, next_status: ActionStatus
) -> None:
    """Raise ``ValueError`` when an action transition is not allowed."""
    if is_valid_action_transition(expected=expected, next_status=next_status):
        return
    msg = f"invalid action transition: {expected.value} -> {next_status.value}"
    raise ValueError(msg)


def is_valid_task_transition(*, expected: TaskStatus, next_status: TaskStatus) -> bool:
    """Return whether a task may move from ``expected`` to ``next_status``."""
    return next_status in _ALLOWED_TASK_TRANSITIONS[expected]


def validate_task_transition(*, expected: TaskStatus, next_status: TaskStatus) -> None:
    """Raise ``ValueError`` when a task transition is not allowed."""
    if is_valid_task_transition(expected=expected, next_status=next_status):
        return
    msg = f"invalid task transition: {expected.value} -> {next_status.value}"
    raise ValueError(msg)


def is_valid_plan_transition(*, expected: PlanStatus, next_status: PlanStatus) -> bool:
    """Return whether a plan may move from ``expected`` to ``next_status``."""
    return next_status in _ALLOWED_PLAN_TRANSITIONS[expected]


def validate_plan_transition(*, expected: PlanStatus, next_status: PlanStatus) -> None:
    """Raise ``ValueError`` when a plan transition is not allowed."""
    if is_valid_plan_transition(expected=expected, next_status=next_status):
        return
    msg = f"invalid plan transition: {expected.value} -> {next_status.value}"
    raise ValueError(msg)


def is_valid_approval_transition(
    *, expected: ApprovalStatus, next_status: ApprovalStatus
) -> bool:
    """Return whether an approval may move from ``expected`` to ``next_status``."""
    return next_status in _ALLOWED_APPROVAL_TRANSITIONS[expected]


def validate_approval_transition(
    *, expected: ApprovalStatus, next_status: ApprovalStatus
) -> None:
    """Raise ``ValueError`` when an approval transition is not allowed."""
    if is_valid_approval_transition(expected=expected, next_status=next_status):
        return
    msg = f"invalid approval transition: {expected.value} -> {next_status.value}"
    raise ValueError(msg)
