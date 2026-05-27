# nimbus-runtime

Transport-neutral chat orchestration for Nimbus.

`nimbus_runtime` is the shared runtime beneath the HTTP AI server and any future
Slack, Discord, CLI, or webhook adapters. It owns session loading/persistence,
per-conversation locking, AI turn execution, storage tool policy, attachment
handling, destructive-action confirmation, and runtime telemetry.

If you are building a new chat frontend, this is the package to integrate with.
Construct `NimbusRuntime`, convert your transport event into `ChatTurnInput`,
call `run_chat_turn()`, and render the returned `ChatTurnResult`.
For token-by-token UX, call `stream_chat_turn()` and replay missed events with
`replay_events()`.

## Role

This is the transport-neutral runtime. It owns behavior that should be shared by
Slack, HTTP, CLI, and future adapters: sessions, confirmation policy,
attachment handling, tool exposure, and telemetry.

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `ai-client-api` | Model-provider-neutral AI contract |
| `nimbus-protocol` | Shared request/result DTOs, stream events, and error shapes |
| `cloud-storage-api` | Optional storage tool contract |
| `pydantic` | Runtime request/response value models |
| `structlog` | Structured runtime logging |

## Architecture

```text
transport adapter
  ai_server, Slack app, CLI, future frontend
        |
        | ChatTurnInput
        v
nimbus_runtime
        |
        | AIClient                    | CloudStorageClient
        v                             v
openrouter_ai_client_impl        aws_client_impl or aws_client_adapter
```

The runtime deliberately does not import FastAPI, Slack SDKs, OpenRouter SDK
types, or boto3. Provider and transport details stay outside the orchestration
core.

## Public API

### NimbusRuntime

Create one runtime per process and reuse it:

```python
from pathlib import Path

from aws_client_impl.s3_client import get_client_impl as get_storage_client
from nimbus_runtime import NimbusRuntime
from openrouter_ai_client_impl.openrouter_client import get_client_impl as get_ai_client

runtime = NimbusRuntime(
    ai_client=get_ai_client(),
    storage=get_storage_client(),
    session_dir=Path("~/.nimbus/sessions/ai_server").expanduser(),
    system_prompt="You are Nimbus, a cloud-storage assistant.",
    tool_container="my-s3-bucket",
)
```

Use `storage=None` to run without storage tools.

### run_chat_turn

`run_chat_turn()` performs the full turn:

1. Serializes concurrent work for the same `conversation_id`.
2. Loads or creates the persisted conversation.
3. Detects confirmation replies and guarded direct actions.
4. Binds storage tools when a storage client and tool container are available.
5. Calls the configured `AIClient`.
6. Persists the updated session atomically.
7. Records telemetry and returns a transport-neutral result.

```python
from nimbus_runtime.models import ChatTurnInput

turn = ChatTurnInput(
    request_id="req-001",
    conversation_id="slack:T123TEAM:C456CHAN:U789USER",
    platform="slack",
    workspace_id="T123TEAM",
    channel_id="C456CHAN",
    thread_id=None,
    message_id="1713840000.123456",
    user_id="U789USER",
    text="List the files in the bucket.",
    attachments=(),
)

result = await runtime.run_chat_turn(turn)
print(result.outcome)
print(result.text)
```

### get_session_lock

`get_session_lock(session_id)` returns the shared `asyncio.Lock` for a
conversation. `run_chat_turn()` acquires this lock internally. Transport adapters
only need to use it directly for operations that must coordinate with active
turns, such as deleting a session.

```python
from nimbus_runtime import get_session_lock

async with get_session_lock(session_id):
    await delete_session_file(session_id)
```

### stream_chat_turn and replay_events

`stream_chat_turn()` appends every emitted provider/runtime event to the session
event log and yields the public `NimbusEvent` projection as it happens. Event
sequences are allocated by the durable session store, not by the provider.

```python
async for event in runtime.stream_chat_turn(turn):
    if event.event_type == "text.delta":
        print(event.payload["delta"], end="")
```

Clients that reconnect can resume from their last seen sequence:

```python
events = runtime.replay_events(
    platform="slack",
    workspace_id="T123TEAM",
    session_id="slack:T123TEAM:C456CHAN:1713840000.123456",
    after_sequence=42,
)
```

The current streaming path is model-backed. Direct runtime-managed actions such
as delete confirmations and attachment uploads still return `ChatTurnResult`
through `run_chat_turn()`.

## Capability Registry

`nimbus_runtime.capabilities` is the shared Nimbus tool catalog. It describes
live, partial, and roadmap capabilities with a stable name, risk level,
operation modes, approval requirement, Claude-style analogue, and visible
surfaces such as Slack, CLI, worker, runtime, or model tool.

Adapters must read from this registry instead of keeping separate "what can
Nimbus do?" lists. The registry is intentionally descriptive first: roadmap
entries such as automation templates, richer user-choice prompts, speculative
candidate plans, and parallel candidate agents are visible before execution
exists so the product contract is explicit.

```python
from nimbus_runtime.capabilities import CapabilitySurface, all_capabilities

slack_capabilities = all_capabilities(surface=CapabilitySurface.SLACK)
```

## Task Ledger

Long-running work is represented by a durable `Task`. A task is the shared
background-work aggregate that Slack, CLI, and future clients can watch.
Actions, artifacts, and ordered session events remain the evidence beneath that
task.

`TaskStatus` is an explicit state machine:

```text
created -> planning -> scanning -> diffing -> awaiting_approval
awaiting_approval -> applying -> verifying -> done
```

Terminal states are `done`, `failed`, `canceled`, `expired`, and `rejected`.
Stores expose idempotent task creation and compare-and-set task transitions so
duplicate wrapper delivery converges instead of starting duplicate work.

### Worker Leases

Background workers coordinate through short-lived task leases. A worker can
acquire a task only when the task exists and no unexpired lease is present.
Heartbeats extend only the owning worker's lease. If a worker crashes and the
lease expires, another worker can take over the task and the attempt counter
increments.

The lease record is coordination state, not permission to mutate storage.
Executors still need task state, policy decisions, action records, and verifier
artifacts before user-visible success.

`TaskWorkerLoop` is the first executor primitive. It scans one tenant at a time,
claims a bounded batch of executable tasks, runs a typed async handler under the
lease, heartbeats while the handler is running, and releases the lease when the
handler completes successfully. If heartbeat renewal fails, the loop cancels the
handler and lets lease expiry become the recovery path.

Handlers own workflow-specific behavior. The loop does not decide that an upload
or delete succeeded; handlers must move task state, create actions/artifacts, and
run verifiers through the normal runtime stores.

### Backup Channel Workflow

`ChannelBackupWorkflow` is the deterministic recipe behind prompts such as
"back up every PDF in this channel." It runs under a `TaskLeaseContext`, scans a
bounded channel listing, filters files by MIME type or filename suffix, compares
against a tenant/channel manifest, uploads missing files, dedupes by verified
content hash, verifies destination hash and size, records manifest evidence, and
then moves the task to `done` or `failed`.

The workflow is transport-neutral. Slack provides source files, a manifest
store, and an object sink; the runtime owns the recipe and the task-state
contract. A model may classify a prompt into this recipe, but it cannot claim
success without the workflow's byte-level evidence.

Every run writes two immutable artifacts:

| Artifact | Purpose |
| --- | --- |
| `verification_report` | Records the SHA-256 and size verifier result for each uploaded or deduped object. |
| `manifest` | Summarizes scanned, matched, saved, skipped, and failed files, and links back to the verifier artifact. |

The manifest is the user-facing receipt. The verifier is the machine-checkable
evidence that the receipt rests on.

### Store Backends

`FileTaskStore` and `FileWorkerLeaseStore` use one SQLite database under the
runtime session directory. This is the local and test fallback: it gives Nimbus a
real transaction boundary without requiring a separate service.

`PostgresTaskStore` and `PostgresWorkerLeaseStore` preserve the same public
contract for Render and other shared deployments. Task creation remains
tenant-scoped and idempotent, transitions stay compare-and-set, workers cannot
lease missing tasks, and lease ownership/expiry rules match SQLite.

`FileSearchIndexStore` and `PostgresSearchIndexStore` provide the first
rebuildable knowledge projection. They store tenant-scoped file metadata and
extracted text chunks. Search callers must pass a `SearchActorScope` from a
trusted adapter or policy layer; the store filters by tenant and ACL before text
scoring and returns cited chunk results. Extracted text is treated as untrusted
data, so search results carry `untrusted_extracted_text` warnings and should be
used as evidence, not as executable instructions.

The important benefit is that Slack, CLI, and future adapters can depend on one
runtime contract. Moving from local development to Postgres should change the
store backend, not the product semantics.

### Search Projection

The search layer is a projection over files and extracted content. It is
rebuildable and must not become the source of truth for storage, permissions, or
task state.

Current contract:

1. `SearchDocument` stores metadata facets such as workspace, channel, uploader,
   content type, bucket, saved state, duplicate group, hash, size, and indexing
   state.
2. `SearchChunk` stores bounded extracted text. Extraction failures are recorded
   by indexing the document with `SearchDocumentStatus.EXTRACTION_FAILED` and no
   fake text chunks.
3. `SearchActorScope` carries the verified actor plus explicit channel or
   workspace visibility granted by a trusted adapter or future policy engine.
4. `SearchQuery` applies structured filters before lexical scoring and bounds
   result count.
5. `SearchResult` returns citations of the form
   `source-uri:page:<n>:chunk:<index>` when a chunk supports the answer.

The invariant is strict: candidate documents are filtered by tenant and ACL
before scoring or answer generation. Postgres migrations include
`search_documents`, `search_chunks`, and a GIN-backed `search_vector` for
lexical retrieval. The SQLite fallback preserves the same logical model for
local development and deterministic tests.

## Data Models

The canonical transport DTOs live in `nimbus_protocol`. The
`nimbus_runtime.models` module re-exports them to keep older imports stable.

### ChatTurnInput

| Field | Type | Description |
| --- | --- | --- |
| `request_id` | `str` | Unique request identifier for tracing |
| `conversation_id` | `str` | Stable logical session key |
| `platform` | `str` | Source platform such as `slack` or `cli` |
| `workspace_id` | `str` | Platform workspace/team identifier |
| `channel_id` | `str` | Channel/conversation identifier |
| `thread_id` | `str \| None` | Optional thread identifier |
| `message_id` | `str` | Source message/event identifier |
| `user_id` | `str` | Source user identifier |
| `text` | `str` | User prompt text |
| `attachments` | `tuple[TurnAttachment, ...]` | Optional file references |

### TurnAttachment

| Field | Type | Description |
| --- | --- | --- |
| `platform_file_id` | `str` | Platform-specific file ID |
| `filename` | `str` | Original filename |
| `content_type` | `str` | MIME type |
| `size_bytes` | `int` | Declared file size |
| `content_base64` | `str \| None` | Optional inline content |
| `sha256_hex` | `str \| None` | Optional digest checked against decoded bytes |

### ChatTurnResult

| Field | Type | Description |
| --- | --- | --- |
| `request_id` | `str` | Echo of the request ID |
| `conversation_id` | `str` | Echo of the conversation ID |
| `text` | `str` | Text to render to the user |
| `outcome` | `TurnOutcome` | `reply`, `confirmation_required`, `partial_success`, or `error` |
| `confirmation_required` | `bool` | Whether the user must confirm before execution |
| `confirmation` | `ConfirmationDetails \| None` | Pending action details |
| `suggested_next_actions` | `tuple[str, ...]` | Optional follow-up prompts |
| `model` | `str` | Model that produced the response |
| `steps` | `int` | Agent loop steps used |
| `fallback_used` | `bool` | Whether the AI client used its fallback model |
| `actions` | `tuple[ActionSummary, ...]` | Durable actions touched by this turn |
| `artifacts` | `tuple[ArtifactSummary, ...]` | Evidence artifacts produced by this turn |

### Artifact Payloads

Runtime artifacts are typed records, not free-form model text:

| Kind | Payload | Use |
| --- | --- | --- |
| `delete_report` | `DeleteReport` | Delete action evidence, including restore plan or explicit restore warning |
| `upload_report` | `UploadReport` | Attachment upload evidence |
| `verification_report` | `ObjectVerificationReport` | Hash and size verifier evidence for background workflows |
| `manifest` | `ManifestReport` or `GenerationManifest` | Workflow receipt or protected-root generation manifest |
| `proof_receipt` | `ProofReceipt` | Deterministic receipt binding user-visible success to linked verifier, manifest, action, and artifact digests |
| `provider_health` | `ProviderHealthReport` | Live Nimbus LIST/HEAD probe evidence with outcome taxonomy, confidence, expiry, and operator next step |
| `repair_receipt` | `RepairReceipt` | S3 replica-lane repair evidence proving source and destination SHA-256 match after provider-side copy |
| `migration_decision_packet` | `MigrationDecisionPacket` | S3-only region/replica evaluation with measured source facts, assumptions, safety checks, rollback shape, and approval-gated route-switch plan |
| `conflict_artifact` | `ConflictArtifact` | Restack/apply evidence proving an approved storage target changed before mutation |

Every artifact persisted through the runtime store receives a deterministic
`payload_digest`. A proof receipt validates those digests before CLI or Slack can
present it as proof, so an incomplete artifact store produces an explicit
operator next step instead of a fake success claim.

Protected roots and generations are the first storage-version-control kernel.
`ProtectedRoot` names a tenant-scoped S3 bucket/prefix. `Generation` points to
an immutable `GenerationManifest` artifact whose manifest digest is canonical
under provider listing order. `nimbus verify <manifest-artifact-id>` checks the
manifest against live S3 and writes drift evidence; `nimbus blame <object>` reads
generation history to show object provenance without relying on conversation
memory.

Storage stacks are the reviewable mutation layer above plans. A stack contains
ordered `StorageChange` rows, immutable revisions, and `RuntimeOperation` log
entries. Candidate cleanup plans become stacks; `restack` writes conflict
artifacts when fresh manifests disagree with approved target digests; `apply`
stops at the first blocked or conflicted change and only writes mutation/proof
evidence after verifier gates pass.

Learning and self-healing are runtime kernels, not adapter shortcuts.
`LearningSignal` and `PolicyPatchProposal` require explicit capability deltas,
base policy bindings, and reviewer decisions. S3 replica healing compares
source and replica generation manifests through `ReplicaLane`; missing replicas
can be repaired only when policy allows it, while checksum mismatches and
unknown hashes stay blocked for reconciliation. Replay traces export ordered
events and artifacts with a formal status spec so CI can diff behavior exactly.
Provider health follows the same evidence rule: live Nimbus probes can mark a
configured bucket/prefix healthy, degraded, blocked, or unavailable. For S3,
Nimbus also includes AWS health-dashboard links as advisory context, but those
links cannot create proof of user work or replace the bounded LIST/HEAD probes.

### Evidence Payload Store

`nimbus_runtime.evidence` provides the local MVP for object-backed evidence.
`export_artifact_payload()` writes canonical artifact payload JSON into a
tenant-scoped content-addressed directory, compressed with deterministic gzip.
The returned `EvidenceObjectRecord` carries the artifact ID, tenant/session,
payload digest, compressed object digest, byte counts, encoding, retention
class, URI, and verification status. Repeated exports dedupe bytes by digest
without merging audit records.

`preview_artifact()` creates compact Slack/CLI-safe summaries that link to the
canonical payload digest and report whether the backing object exists.
`compact_evidence_records()` verifies each source payload object before writing
a compressed bundle index; it never deletes old payload objects. This keeps the
one-process/one-store development topology honest while leaving room for a
future encrypted S3-backed cold evidence provider.

### ConfirmationDetails

| Field | Type | Description |
| --- | --- | --- |
| `action_id` | `str` | Pending action ID |
| `kind` | `str` | Action kind, currently `delete_file` |
| `prompt` | `str` | Prompt to display to the user |
| `expected_reply` | `str` | Exact confirmation reply required |
| `expires_at` | `str` | ISO-8601 expiry time |

## Storage Tool Policy

The runtime exposes a small, wrapper-safe storage tool set to the AI:

| Tool | Purpose |
| --- | --- |
| `list_files(prefix="")` | List objects in the pinned container |
| `get_file_info(remote_path)` | Fetch metadata for one object |
| `delete_file(remote_path, confirm)` | Delete only after confirmation policy allows it |

The storage container is pinned when the runtime is built. The model cannot
select another bucket by prompt injection. Destructive actions are routed through
the confirmation flow instead of being exposed as ordinary one-shot tool calls.

Attachment uploads are handled by the runtime when the user asks to upload
attached files. The runtime validates inline bytes and optional SHA-256 digests
before writing to storage.

### Policy Engine

Runtime authorization is represented as data, not prompt text.
`authorize_action_with_record()` returns a `PolicyDecisionRecord` with tenant,
actor, operation, target, decision, reason, policy version, and timestamp.
Actions persist that record so later events and artifacts can explain why a
side effect was allowed, denied, or routed to approval.

The first policy slice supports:

| Policy surface | Behavior |
| --- | --- |
| Default scope | Current-channel work is the default. Workspace-scope work requires a live workspace-admin grant. |
| Uploads | Allowed only for the pinned container and bounded attachment size. |
| Deletes | Always produce `requires_approval` and create actor-bound approval records. |
| Delegation | Delete approvals may include the original actor, delegated admins, and matching channel owners. |
| Grant expiry | Expired grants are ignored fail-closed. |

`PolicyContext` is intentionally explicit: pinned container, upload byte limit,
current channel, requested scope, grants, and versioned policy config. Future
Slack, CLI, and admin surfaces should populate that context from verified state,
not from model output.

## Storage Agent

`StorageAgent` is a transport-neutral operation executor that enforces mode-level
access control before touching storage or emitting events. Slack and CLI are thin
clients over this class — both call `execute()` with the same
`StorageAgentRequest` shape.

```python
from nimbus_runtime import StorageAgent, StorageAgentRequest, OperationMode

agent = StorageAgent(
    storage=storage_client,
    artifact_store=artifact_store,
    event_store=event_store,
)
request = StorageAgentRequest(
    session_id="s_123",
    operation="list",
    mode=OperationMode.READ_ONLY,
    actor=actor,
    container="my-bucket",
)
response = agent.execute(request)
```

### Operation Modes

| Mode | Allowed operations |
| --- | --- |
| `READ_ONLY` | `scan`, `list`, `search`, `hash`, `diff_manifest` |
| `PLAN` | READ_ONLY + `propose_plan`, `stage_upload` |
| `APPLY` | PLAN + `promote_upload`, `prepare_delete`, `delete`, `restore`, `verify`, `write_artifact` |
| `WATCH` | `scan`, `list`, `search` |
| `REVIEW` | `scan`, `list`, `search`, `propose_plan` |
| `POLICY_ADMIN` | Same as `APPLY` |

Attempting an operation outside the mode's allowed set raises
`OperationNotPermittedError`.

### Two-Phase Upload

Uploads are intentionally staged before promotion:

1. `stage_upload` — writes to `_staging/{user_id}/{nonce}/{key}`, returns the
   staging key for review.
2. `promote_upload` — downloads the staged object, re-uploads to the final key,
   then deletes the staging entry.

## Action Ledger

Side-effecting runtime work is stored in `nimbus_runtime.sqlite3` under the
session directory. The ledger keeps actions, ordered session events, and
artifacts in one SQLite database so action state and audit events commit or roll
back together.

Action input, result, and failure payloads are typed domain records such as
`DeleteFileInput`, `UploadAttachmentInput`, `DeleteFileResult`,
`UploadAttachmentResult`, and `ActionFailure`. Wrapper responses still expose
small JSON-safe summaries.

The same event store also backs streaming replay. `turn.started`,
`text.delta`, `tool.call.started`, `tool.call.completed`, `turn.completed`, and
`turn.failed` are all ordinary session events.

## Undo And Recovery

Every runtime-owned delete report includes a `RestorePlan`. Nimbus reads
pre-delete object metadata before asking for approval and records any provider
version ID, size, and SHA-256-style digest it can see. If version metadata is
available, the restore plan points to that provider version. If no object was
deleted, the plan says restore is not required. If neither versioning nor a
copy-to-trash primitive is available through the current `CloudStorageClient`
contract, the report says restore is unavailable and explains why.

Today this is an evidence contract, not a full restore executor. The next
heavier primitive is a storage adapter capability that can copy the object to a
trash key before deletion or restore a specific provider version.

## Confirmation Flow

Delete requests are intentionally two-step:

1. User asks to delete a remote path.
2. Runtime stores a pending action, a `Plan`, and an actor-bound `Approval`.
3. Frontend renders the prompt, plan preview, or approval UI.
4. User sends the expected confirmation phrase.
5. Runtime checks the approval record for tenant, actor, exact target, status,
   and expiry before authorizing the action.
6. Runtime executes the delete, records verifier evidence, and applies the plan.

Pending confirmations expire after `NIMBUS_PENDING_DELETE_TTL_SECONDS`.

Plans are durable previews of proposed work. Approvals are separate records so
Slack, CLI, or future web clients can render different UI while sharing the
same safety contract. A destructive delete now follows this event history:

```text
action_created
plan_created
approval_requested
approval_decided
plan_approved
action_authorized
action_started
verification_started
artifact_created
action_completed
plan_applied
```

Wrong actor, stale target, duplicate click, missing approval, and expired
approval decisions fail closed. Failed decisions append audit events but do not
move the approval, plan, or action into an executable state.

## State Store

The state store is a JSON-file-backed expiring key-value store used by adapters
for small retry/replay state. Runtime-managed actions and confirmations live in
the SQLite action ledger instead.

```python
import time

from nimbus_runtime.state_store import delete_state, get_state, put_state

put_state(
    namespace="my_adapter",
    key="pending_action:conv-123",
    value={"action": "delete", "path": "reports/old.csv"},
    expires_at=time.time() + 900,
)

entry = get_state("my_adapter", "pending_action:conv-123")
if entry.value is not None:
    print(entry.value["path"])

delete_state("my_adapter", "pending_action:conv-123")
```

State lives under `AI_SESSION_DIR/_request_state/<namespace>/` so it survives
process restarts when `AI_SESSION_DIR` is backed by a persistent volume.

## Telemetry

`runtime_telemetry` is an in-memory metrics collector used by the runtime and
HTTP adapter. It records counters and histogram summaries with stable metric
names plus labels.

```python
from nimbus_runtime.telemetry import runtime_telemetry

runtime_telemetry.record_wrapper_turn(
    platform="slack",
    outcome="reply",
    latency_ms=342.0,
)

snapshot = runtime_telemetry.snapshot()
assert (
    snapshot["counters"]["nimbus_wrapper_turns_total|outcome=reply,platform=slack"]
    == 1
)

runtime_telemetry.reset()
```

Record methods:

| Method | Use |
| --- | --- |
| `record_wrapper_turn(platform, outcome, latency_ms)` | End of each chat turn |
| `record_idempotent_replay(backend)` | Cached idempotency response |
| `record_auth_result(mechanism, result, reason)` | Auth success/failure |
| `record_ai_response(model, latency_ms, fallback_used, stop_reason)` | AI call success |
| `record_ai_failure(error_kind)` | AI call failure |
| `record_tool_call(tool_name, success, latency_ms)` | Tool completion |
| `record_slack_turn(kind, outcome)` | Slack adapter turn routing |
| `record_slack_reply(result, reason)` | Slack reply-post result |
| `record_task_outcome(status, duration_seconds, tenant)` | Background task completion |
| `record_verifier_failure(verifier, error_kind)` | Drift verifier failure |
| `record_search_query(latency_ms, result_count, hit_count)` | Search query completion |
| `record_index_lag(lag_ms)` | Search index ingestion lag |
| `record_slack_dedupe(event_type)` | Deduplicated Slack event |
| `record_approval_fail_closed(reason)` | Approval evaluated fail-closed |

Service entry points should call `nimbus_runtime.observability.configure_observability`.
That shared bootstrap configures structured JSON logs, FastAPI/HTTPX
instrumentation, Sentry when `SENTRY_DSN` is present, OTLP traces/metrics for
New Relic or generic OTLP backends, and Pydantic Logfire when `LOGFIRE_TOKEN`
is present.

## Environment

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `AI_SESSION_DIR` | No | `~/.nimbus/sessions/ai_server` | Session and state directory |
| `NIMBUS_PENDING_DELETE_TTL_SECONDS` | No | `900` | Pending delete confirmation lifetime |
| `NIMBUS_CONTAINER` | Storage tools | `$AWS_BUCKET_NAME` | Bucket/container pinned to tools |
| `NEW_RELIC_LICENSE_KEY` | No | unset | Enables New Relic OTLP export |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | `https://otlp.nr-data.net:4318` | OTLP endpoint root |
| `OTEL_EXPORTER_OTLP_HEADERS` | No | unset | Generic OTLP headers for non-New-Relic backends |
| `SENTRY_DSN` | No | unset | Enables Sentry exception reporting |
| `LOGFIRE_TOKEN` | No | unset | Enables Pydantic Logfire export |

## Failure Model

| Failure | Runtime behavior |
| --- | --- |
| AI provider error | Returns `outcome="error"` instead of raising to transport |
| Tool execution error | Feeds error back through the AI/tool loop when possible |
| Expired confirmation | Treats the reply as a fresh request |
| Corrupt session file | Logs and resets that session instead of crashing the process |
| Concurrent same-session turns | Per-session lock serializes execution |
| Missing storage client/container | Runs without storage tools |
| Event append failure during action write | Rolls back the matching action change |

The SQLite-backed action ledger is production-credible for the current
single-node deployment. Move to Postgres or another shared transactional backend
before scaling to multiple writable instances.

## Tests

```bash
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/ -q
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/test_runtime.py -v
uv run pytest -m eval tests/evals -q
```

Tests use fake AI and storage clients. No OpenRouter key, AWS credentials, or
network access is required.

## Full Documentation

- `docs/source/nimbus/bridge-contract.md`
- `docs/source/nimbus/sessions.md`
- `docs/source/nimbus/attachments.md`
- `docs/source/nimbus/verification.md`
- `src/nimbus_protocol/README.md`
- `src/ai_server/README.md`
