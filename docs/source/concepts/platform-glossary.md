# Nimbus Platform Glossary

This glossary is distilled from root `SYSTEM_DESIGN.md`, with the
{doc}`../complete-system-design` Sphinx companion as the docs-reader view.
Current code and package docs are used as cross-checks for "what exists today";
the root design file is the primary source for the platform vocabulary.

<div class="nimbus-glossary-shell">
  <div class="nimbus-glossary-kicker">TABLE OF CONTENTS</div>
  <div class="nimbus-glossary-columns">
    <div>
      <h2>Platform Kernel</h2>
      <a href="#nimbus">Nimbus</a>
      <a href="#agent-operating-layer">Agent Operating Layer</a>
      <a href="#modular-monolith">Modular Monolith</a>
      <a href="#collaborative-document">Collaborative Document</a>
      <a href="#server-authoritative-state">Server-Authoritative State</a>
      <a href="#event-sourced-agent-session-runtime">Event-Sourced Agent Session Runtime</a>
      <a href="#runtime-kernel">Runtime Kernel</a>
      <a href="#tenant">Tenant</a>
      <a href="#actor">Actor</a>
      <a href="#verifiedactor">VerifiedActor</a>
      <a href="#session">Session</a>
      <a href="#session-authority">Session Authority</a>
      <a href="#session-edge">Session Edge</a>
      <a href="#operation">Operation</a>
      <a href="#operation-envelope">Operation Envelope</a>
      <a href="#event">Event</a>
      <a href="#session-event-log">Session Event Log</a>
      <a href="#projection">Projection</a>
      <a href="#runtime-spec">RuntimeSpec</a>
    </div>
    <div>
      <h2>Actions And Artifacts</h2>
      <a href="#action">Action</a>
      <a href="#action-ledger">Action Ledger</a>
      <a href="#action-status">ActionStatus</a>
      <a href="#compare-and-set-cas-transition">Compare-and-Set Transition</a>
      <a href="#artifact">Artifact</a>
      <a href="#artifact-manifest">Artifact Manifest</a>
      <a href="#verification">Verification</a>
      <a href="#reconciler">Reconciler</a>
      <a href="#child-session">Child Session</a>
      <a href="#multiplayer-session">Multiplayer Session</a>
      <a href="#sessionworker">SessionWorker</a>
    </div>
    <div>
      <h2>Storage</h2>
      <a href="#cloudstorageclient">CloudStorageClient</a>
      <a href="#container">Container</a>
      <a href="#object-name">Object Name</a>
      <a href="#objectref">ObjectRef</a>
      <a href="#prefix">Prefix</a>
      <a href="#objectinfo">ObjectInfo</a>
      <a href="#deleteresult">DeleteResult</a>
      <a href="#storage-location-transparency">Storage Location Transparency</a>
      <a href="#generated-openapi-client">Generated OpenAPI Client</a>
      <a href="#service-adapter">Service Adapter</a>
      <a href="#multipart-upload">Multipart Upload</a>
    </div>
    <div>
      <h2>AI Runtime</h2>
      <a href="#aiclient">AIClient</a>
      <a href="#conversation">Conversation</a>
      <a href="#tool">Tool</a>
      <a href="#tool-plane">Tool Plane</a>
      <a href="#agentic-loop">Agentic Loop</a>
      <a href="#bounded-agentic-loop">Bounded Agentic Loop</a>
      <a href="#openrouterclient">OpenRouterClient</a>
      <a href="#model-fallback">Model Fallback</a>
      <a href="#tool-result-sandboxing">Tool-Result Sandboxing</a>
      <a href="#context-hydration">Context Hydration</a>
      <a href="#agent-autonomy-ladder">Agent Autonomy Ladder</a>
      <a href="#mcp">MCP</a>
    </div>
    <div>
      <h2>Safety And Auth</h2>
      <a href="#hmac-wrapper-auth">HMAC Wrapper Auth</a>
      <a href="#canonical-request">Canonical Request</a>
      <a href="#nonce">Nonce</a>
      <a href="#idempotency-key">Idempotency Key</a>
      <a href="#confirmation-flow">Confirmation Flow</a>
      <a href="#same-actor-confirmation">Same-Actor Confirmation</a>
      <a href="#policy-engine">Policy Engine</a>
      <a href="#fail-closed">Fail Closed</a>
      <a href="#least-privilege-capability">Least-Privilege Capability</a>
      <a href="#safe-root">Safe Root</a>
      <a href="#container-pinning">Container Pinning</a>
      <a href="#attachment-validation">Attachment Validation</a>
    </div>
    <div>
      <h2>Reliability</h2>
      <a href="#durable-before-visible">Durable Before Visible</a>
      <a href="#write-ahead-event-log">Write-Ahead Event Log</a>
      <a href="#action-transaction-boundary">Action Transaction Boundary</a>
      <a href="#projection-rule">Projection Rule</a>
      <a href="#consistency-choice">Consistency Choice</a>
      <a href="#record-framing">Record Framing</a>
      <a href="#atomic-session-write">Atomic Session Write</a>
      <a href="#per-session-lock">Per-Session Lock</a>
      <a href="#token-bucket">Token Bucket</a>
      <a href="#backpressure">Backpressure</a>
      <a href="#operation-priority">Operation Priority</a>
      <a href="#load-shedding">Load Shedding</a>
      <a href="#ambiguous-provider-outcome">Ambiguous Provider Outcome</a>
      <a href="#bounded-registry">Bounded Registry</a>
      <a href="#cell">Cell</a>
      <a href="#clock">Clock</a>
      <a href="#store-graduation-contract">Store Graduation Contract</a>
    </div>
    <div>
      <h2>Testing</h2>
      <a href="#unit-test">Unit Test</a>
      <a href="#integration-test">Integration Test</a>
      <a href="#e2e-test">E2E Test</a>
      <a href="#property-based-testing">Property-Based Testing</a>
      <a href="#fuzz-harness">Fuzz Harness</a>
      <a href="#bdd-acceptance-test">BDD Acceptance Test</a>
      <a href="#deterministic-simulation-testing">Deterministic Simulation Testing</a>
      <a href="#golden-safety-set">Golden Safety Set</a>
      <a href="#invariant-checker">Invariant Checker</a>
      <a href="#pre-seeded-fixture">Pre-Seeded Fixture</a>
    </div>
    <div>
      <h2>Operations And Scale</h2>
      <a href="#render-postgres">Render Postgres</a>
      <a href="#runtime-telemetry">Runtime Telemetry</a>
      <a href="#structured-logging">Structured Logging</a>
      <a href="#sli">SLI</a>
      <a href="#runtime-alert-rule">Runtime Alert Rule</a>
      <a href="#client-strategy">Client Strategy</a>
      <a href="#data-residency">Data Residency</a>
      <a href="#postgres">Postgres</a>
      <a href="#sqlite">SQLite</a>
      <a href="#valkey">Valkey</a>
      <a href="#queue-and-workers">Queue And Workers</a>
      <a href="#temporal">Temporal</a>
      <a href="#schema-compatibility">Schema Compatibility</a>
    </div>
  </div>
</div>

## Platform Kernel

### Nimbus

Nimbus is the project-level product: a cloud-storage and AI runtime that lets
users ask for file operations in natural language while preserving explicit
storage, AI, auth, runtime, and transport boundaries.

In the current codebase Nimbus is a Python workspace. In the canonical design it
is also the name of the future agent-first operations platform.

See also: {doc}`../architecture-overview`, {doc}`../DESIGN`.

### Agent Operating Layer

The agent operating layer is the product model where user intent becomes a
verified actor, a session operation, a server-authoritative event, a policy
decision, a durable action, execution, verification, and a realtime client
update.

This is the core shift from "chat completion plus tools" to "humans and agents
coordinate safe cloud operations in one live session."

### Modular Monolith

The modular monolith is the recommended early deployment shape. Nimbus can run
as one process while keeping explicit internal boundaries for session edge,
runtime kernel, stores, providers, policy, execution, verification, and
projections.

The canonical design defers microservices until the package boundaries and access
patterns justify a split.

### Collaborative Document

A collaborative document is the Figma-inspired mental model for a Nimbus
session. A session contains prompts, model messages, operations, events,
actions, artifacts, comments, confirmations, presence, and child sessions.

Different fields have different consistency needs. Actions are
server-authoritative; presence can be ephemeral; draft text can stay local
until richer collaboration requires more.

### Server-Authoritative State

Server-authoritative state is state that clients may render but may not mutate
directly. Operations ask the Session Authority to change state; events record
what the server accepted.

The canonical design explicitly avoids CRDTs for action correctness because
destructive state needs one authority.

### Event-Sourced Agent Session Runtime

The canonical design defines Nimbus as an event-sourced agent session runtime:
commands are validated at the boundary, effects are committed through an
idempotent action ledger, and user-visible state is a replayable projection of
durable session events.

This phrase ties together the database-internals framing, action ledger,
projection rule, and deterministic simulation plan.

### Runtime Kernel

The runtime kernel is the transport-neutral core that should own session,
action, policy, artifact, verification, and telemetry semantics.

Today that role is mostly `nimbus_runtime`. The canonical design argues that it
should grow from turn orchestration into the authoritative session/action
engine.

### Tenant

A tenant is the isolation boundary. In Slack it maps naturally to a workspace;
in CLI it can be a local workspace identity; in an enterprise deployment it maps
to an organization.

Tenant identity should prefix durable state, cache keys, rate-limit buckets,
idempotency records, metrics, and future cell placement. A cross-tenant lookup
is a correctness bug, not a convenience feature.

### Actor

An actor is the human or service principal asking Nimbus to do work. A raw
`user_id` is not enough on its own; Nimbus needs to know who verified that
identity and for which tenant.

Actors matter most for destructive actions, audit trails, and policy decisions.

### VerifiedActor

`VerifiedActor` is the proposed domain object for a normalized actor claim:
tenant, user identifier, auth source, optional bridge identifier, and
verification timestamp.

The current HMAC wrapper proves that a trusted wrapper signed a request. The
future `VerifiedActor` model records the wrapper's upstream verification of the
human or service actor.

### Session

A session is the product unit of collaborative work. It is more than chat
history: it should contain prompts, model responses, operations, events,
actions, artifacts, confirmations, participants, and child sessions.

Today sessions are persisted conversations under `AI_SESSION_DIR`. The design
target is a replayable session document whose events drive every client view.

See also: {doc}`../nimbus/sessions`.

### Session Authority

The Session Authority is the server-side owner of session correctness. Clients
submit operations; the authority validates them, assigns sequence numbers,
appends events, applies policy, creates actions, and broadcasts updates.

In the current repo this role is split across `ai_server` and
`nimbus_runtime`. The design target moves product behavior into the runtime
kernel and leaves `ai_server` as an edge adapter.

### Session Edge

The Session Edge is the transport-facing layer: HTTP routes, future
WebSocket/SSE streams, auth, presence, reconnect, and rate limits.

`ai_server` is the current HTTP edge for chat wrappers. Slack, CLI, web, and
MCP clients should all remain thin edges over the same runtime semantics.

### Operation

An operation is a client's requested mutation or query against a session: add a
prompt, authorize an action, cancel an action, fetch events, or request a
summary.

The canonical design distinguishes operations from events. Operations are requests;
events are server-authoritative facts about what actually happened.

### Operation Envelope

An operation envelope is the proposed typed transport shape for client/runtime
messages: `CALL`, `RESPONSE`, `COMPLETE`, `ERROR`, and `CANCEL`.

The envelope gives CLI, web workers, Slack bridges, and future MCP hosts one
common protocol for streaming progress, cancellation, and structured errors.

### Event

An event is a durable fact emitted by the server. Examples include
`operation_received`, `prompt_added`, `action_proposed`,
`confirmation_required`, `action_authorized`, `verification_passed`, and
`action_completed`.

Events are ordered per session and are the source for replay, realtime client
sync, metrics, support timelines, and deterministic tests.

### Session Event Log

The session event log is the ordered append-only record of a session. The 3.0
design treats it as the write-ahead record for important state transitions.

If a client disconnects after sequence 42, it should be able to reconnect and
ask for events after 42. If a support engineer needs to inspect a session, the
same log should explain the work.

### Projection

A projection is a derived view of the event stream. Web UI state, Slack
summaries, support timelines, metrics counters, and deterministic-test oracle
state are all projections.

Projection correctness means that applying events live and replaying them from
the beginning produce the same state.

### RuntimeSpec

`RuntimeSpec` is the proposed versioned snapshot of runtime behavior for a
session: model policy, tool surface, limits, confirmation rules, storage policy,
replay policy, artifact policy, and feature flags.

It answers "which rules processed this session?" after deployments, policy
changes, or model/tool upgrades.

## Actions And Artifacts

### Action

An action is the durable unit of side-effecting work. Deletes, uploads, large
summaries, child-session fanout, and future provider mutations should become
actions.

The core rule is simple: the model may propose actions, but the runtime
authorizes, executes, verifies, and records them.

### Action Ledger

The action ledger is the durable store of actions and their transitions. It is
the design replacement for route-local or memory-only pending work.

The ledger should preserve idempotency, support retries, reject invalid state
transitions, and let operators inspect work after a crash or duplicate delivery.

### ActionStatus

`ActionStatus` is the action state machine. The canonical design uses statuses such
as `proposed`, `awaiting_confirmation`, `authorized`, `queued`, `executing`,
`verifying`, `succeeded`, `failed_retryable`, `failed_terminal`, `expired`, and
`cancelled`.

Status transitions should be monotonic: an action should not move backward or
skip policy gates.

### Compare-and-Set (CAS) Transition

A CAS transition moves an action only if it is still in the expected state. It
prevents two workers, retries, or duplicate confirmations from both "winning"
the same transition.

For example, a worker may request `queued -> executing`. If another worker
already claimed the action, the transition returns no updated action.

### Artifact

An artifact is proof or work product created during a session: a cleanup
manifest, before/after listing, upload verification report, model trace, or
child-session summary.

Artifacts let Nimbus show evidence instead of asking users to trust a confident
message.

### Artifact Manifest

An artifact manifest is metadata for an immutable artifact: identifier, kind,
schema version, digest, chunk list, size, creating action, and verification
summary.

Large artifact content can move to object storage later. The manifest stays in
the durable session/action store.

### Verification

Verification is the deterministic check that a side effect really happened and
matches the authorized target. Uploads verify byte counts and optional digests;
deletes verify the exact authorized object and reconcile post-delete state.

Verification results should become events and artifacts, not just log lines.

### Reconciler

A reconciler is the future component that resolves ambiguous or stuck action
states after crashes, provider timeouts, or worker failures.

For example, if S3 deletes an object but the response is lost, a reconciler can
query object state and record the final outcome before Nimbus reports success.

### Child Session

A child session is a bounded background session spawned by a parent session for
parallel work such as research, large scans, or separate verification tasks.

Child sessions inherit policy and budgets from the parent; they should not be a
way to bypass limits.

### Multiplayer Session

A multiplayer session allows several humans and agents to observe and continue
the same work. Slack threads, web views, and CLI sessions should render the same
event stream rather than maintain separate product semantics.

### SessionWorker

`SessionWorker` is the proposed client abstraction inspired by SharedWorker
patterns: a local API that hides streaming, reconnect, cancellation, and
operation multiplexing from web or CLI code.

## Storage

### CloudStorageClient

`CloudStorageClient` is the external provider-neutral storage contract. It
defines upload, download, list, delete, and metadata operations without exposing
S3 or HTTP transport details to callers.

The whole storage vertical exists to preserve this contract across local S3 and
remote service-backed access.

### Container

A container is the namespace for object operations. In AWS S3 it maps to a
bucket. The public contract uses `container` so a future provider can map the
same idea to its own naming system.

### Object Name

An object name is the full key inside a container, such as
`reports/2026/april.csv`.

The implementation rejects empty names and names that start with `/`. HTTP
routes accept object names as path parameters, so slashes are part of the name,
not a nested filesystem.

### ObjectRef

`ObjectRef` is the proposed typed reference to a storage object: tenant,
provider, container, object name, and optional version.

It prevents the agent runtime from passing around unscoped bucket/key strings
when policy, confirmation, and verification need exact targets.

### Prefix

A prefix is the object-name prefix used for listing. `list_files(container,
prefix)` returns objects whose names start with that prefix. `""` means the
container root.

Nimbus should treat large prefix scans as potentially expensive work and apply
limits, pagination, artifacts, or background execution as needed.

### ObjectInfo

`ObjectInfo` is the provider-neutral metadata object returned by storage calls.
It can include name, version, content type, integrity value, encryption,
storage tier, size, update time, and provider metadata.

Fields may be `None` because not every provider or operation can populate every
metadata field.

### DeleteResult

`DeleteResult` is the provider-neutral outcome of a delete. The S3
implementation first checks existence; missing objects raise
`ObjectNotFoundError` instead of silently succeeding as a no-op.

### Storage Location Transparency

Storage location transparency means the caller programs to
`CloudStorageClient` and does not care whether the implementation calls S3
directly or talks to the storage service over HTTP.

The adapter restores the Python contract on top of the generated HTTP client.

### Generated OpenAPI Client

The generated OpenAPI client mirrors the storage service HTTP API. It is
transport-shaped and should not be hand-edited.

Domain behavior, error mapping, and contract restoration belong in
`aws_client_adapter`.

### Service Adapter

The service adapter is `CloudStorageServiceAdapter`: a Python implementation of
`CloudStorageClient` that talks to the FastAPI storage service through the
generated client.

It keeps remote storage callers from depending on HTTP response classes or
service internals.

### Multipart Upload

Multipart upload is the S3 path for large local files or file-like objects over
`100 MiB`. It lets S3 receive parts independently.

The reliability invariant is that an initiated multipart upload must be
completed or aborted; leaked parts can cost money.

## AI Runtime

### AIClient

`AIClient` is the provider-neutral model contract. It lets the rest of Nimbus
send prompts, bind tools, stream events, and receive `AIResponse` without
importing provider SDKs.

`openrouter_ai_client_impl` is one implementation, not the abstraction.

### Conversation

A conversation is the persisted model-facing message history. The current
runtime loads, appends to, bounds, and atomically writes conversation JSON for
each wrapper session.

In the future platform model, conversation messages become one part of the
larger session document.

### Tool

A tool is a schema-defined callable exposed to the model. In this repo tools
wrap storage operations such as list, get info, upload, and guarded delete.

Tools should be bounded, validated, and policy-aware. Raw provider calls are
not appropriate model tools.

### Tool Plane

The tool plane is the set of capabilities available around the model:
read-only storage tools, proposed-action tools, runtime-only actions,
verification tools, diagnostics, and future session tools.

The design split is: model proposes and asks for context; runtime authorizes;
executor performs; verifier proves.

### Agentic Loop

The agentic loop is the cycle of model response, tool call, tool result, and
next model step. The OpenRouter implementation runs this loop through
pydantic-ai and the OpenAI-compatible API.

### Bounded Agentic Loop

A bounded agentic loop stops after a configured step budget instead of looping
forever. Hitting the budget raises an AI-domain error rather than silently
continuing.

Boundedness protects provider cost, storage operations, and user trust.

### OpenRouterClient

`OpenRouterClient` is the concrete provider implementation for the AI contract.
It loads API/model configuration, translates provider errors into
`ai_client_api` exceptions, runs tool calls, and supports fallback models for
eligible provider failures.

### Model Fallback

Model fallback is the provider-level retry strategy that switches from a
primary model to a configured fallback for eligible rate-limit or 5xx failures.

It is not permission to retry unsafe side effects. Tool and action idempotency
still belong in the runtime.

### Tool-Result Sandboxing

Tool-result sandboxing wraps storage output as untrusted data before feeding it
back to the model. Object names and metadata may contain prompt injection, so
the model must not treat tool output as instructions.

The current implementation wraps and truncates tool results.

### Context Hydration

Context hydration is the planned process of gathering relevant state before a
model plans work: recent session events, object listings, artifacts, policy
limits, action status, and user intent.

The goal is to give the model enough context to be useful without giving it raw
authority.

### Agent Autonomy Ladder

The autonomy ladder is the design scale from no tools, to read-only tools, to
proposed actions, to safe automatic actions, to destructive confirmed actions,
to policy-approved background work, to bounded child sessions.

Nimbus should climb by action class, not by vibes.

### MCP

MCP is the Model Context Protocol. The canonical design treats it as a future
standard tool/host surface after Nimbus has stable internal action semantics.

Good MCP tools should be workflow-level, bounded, recoverable, and policy
aware. Raw S3 delete and raw event writes are intentionally bad MCP tools.

## Safety And Auth

### HMAC Wrapper Auth

HMAC wrapper auth is the current signed boundary for `POST /ai/chat/turn`.
The wrapper signs the method, path, timestamp, nonce, and body digest with
`AI_SERVER_SIGNING_SECRET`.

This proves that a trusted wrapper sent the normalized request.

### Canonical Request

The canonical request is the exact byte string signed by the wrapper:
method, path, timestamp, nonce, and SHA-256 digest of the raw body.

The server verifies the body actually received. It does not trust a caller's
declared digest if the bytes differ.

### Nonce

A nonce is a single-use value included in the signed wrapper request. Nimbus
rejects repeated nonces within the freshness window to prevent replay.

The current implementation uses in-memory and expiring file-backed state so the
defense survives process restarts in the single-machine deployment.

### Idempotency Key

An idempotency key identifies one logical request so retries can return the
same response or current state instead of executing the work twice.

Current chat turn idempotency is scoped by platform and workspace. Future
action idempotency should also include tenant and operation/action identity.

### Confirmation Flow

The confirmation flow is the two-step guard for destructive operations.
Current delete requests create pending confirmation state and return
`confirmation_required`; only the expected reply executes the delete.

Future design moves this into the action ledger:
`awaiting_confirmation -> authorized -> executing -> verifying -> succeeded`.

### Same-Actor Confirmation

Same-actor confirmation means the actor who authorizes a destructive action
must match the actor and target that created the pending action.

It prevents another user, stale button, retry, or cross-tenant request from
confirming work it did not initiate.

### Policy Engine

The policy engine is the deterministic runtime component that returns allow,
deny, require confirmation, or require admin approval.

The model must never be the access-control boundary. Unknown action kinds,
unknown auth, and ambiguous targets should fail closed.

### Fail Closed

Fail closed means refusing to perform side effects when the system cannot prove
the request is safe.

Examples: malformed wrapper input, expired confirmation, cross-tenant object
reference, unknown action kind, provider outage on a destructive path, or
policy engine uncertainty.

### Least-Privilege Capability

A least-privilege capability is a short-lived authority scoped to one actor,
action, provider, operation, object, and expiration.

It is the future execution-plane shape for side effects. If the provider SDK
requires broader process credentials in the MVP, the runtime still enforces the
narrow capability before calling the provider.

### Safe Root

`safe_root` is the local filesystem sandbox for upload/download tools. Paths
are resolved relative to it; absolute paths and `..` escapes are rejected.

This keeps model-suggested paths from reading secrets or writing outside the
allowed workspace.

### Container Pinning

Container pinning binds storage tools to a configured bucket/container. The
model cannot add a `container` argument through prompt injection because the
tool schema does not include one.

### Attachment Validation

Attachment validation checks inline uploaded bytes against declared size,
optional SHA-256 digest, filename length, MIME-like content type, per-file
limit, total-turn limit, and attachment count.

The runtime validates the bytes actually received before writing temporary
files or uploading to storage.

## Reliability

### Durable Before Visible

Durable before visible means Nimbus should not report success to the user
before the result and event have been durably recorded.

This is one of the core action-platform invariants because users and
teammates rely on the visible timeline as truth.

### Write-Ahead Event Log

A write-ahead event log records the fact before downstream projection, cache,
metric, or client updates. The event log becomes the durable source of truth
for session replay and action narratives.

The canonical design also requires sequence numbers, schema versions, idempotency
links, bounded payloads, and malformed-record rejection.

### Action Transaction Boundary

An action transaction boundary is the record shape around a side effect:
tenant, session, action kind, actor, target reference, target digest, policy
decision, confirmation, idempotency key, status, attempt, creation time, and
expiry.

It does not pretend S3, OpenRouter, Slack, and GitHub are one ACID database.
It gives Nimbus a transaction-like boundary it can validate, retry, reconcile,
and audit.

### Projection Rule

The projection rule is the 3.0 rule: if a projection is wrong, rebuild it from
the event log.

Slack thread messages, web timeline state, latest action status, cost stats,
search indexes, analytics, and support timelines are all derived views. The
event log and action ledger own truth.

### Consistency Choice

A consistency choice is the deliberate selection of strong or eventual
consistency per workflow.

Nimbus needs strong consistency for destructive authorization, action
transitions, idempotency decisions, per-session event order, and spend limits.
Presence, token streaming, metrics, previews, and analytics can be eventual or
best effort.

### Record Framing

Record framing is the storage-level contract for event logs or SQLite rows:
magic/version prefix, schema version, payload length checked against actual
bytes, content digest for artifact references, creation timestamp, monotonic
sequence, and an explicit migration path.

The canonical design calls this out so durable logs do not become ambiguous transcript
dumps.

### Atomic Session Write

Atomic session write is the current JSON persistence pattern:
write the new session file to a temporary sibling and then replace the target.

Readers should never observe a half-written conversation.

### Per-Session Lock

A per-session lock serializes concurrent turns for the same conversation.
Different sessions can proceed independently.

The current lock registry uses weak references so memory tracks active work
rather than all historical sessions.

### Token Bucket

A token bucket is the current per-user rate limit shape for chat wrapper
traffic. It has a capacity and refill rate, so bursts are allowed up to a
bounded limit and sustained traffic is constrained.

Future designs add tenant, priority, and provider-aware limits.

### Backpressure

Backpressure is the system's way to slow or refuse incoming work before queues,
memory, provider calls, or session state grow without bound.

In Nimbus this includes request limits, rate limits, step budgets, action
budgets, queue depth, provider concurrency caps, and product-tier controls.

### Operation Priority

Operation priority ranks work during overload. The canonical design uses safety,
confirmation, read, AI, and bulk classes.

Cancelling a dangerous action should survive longer than starting a 500-file
scan.

### Load Shedding

Load shedding is deliberate refusal or delay of low-priority work under
saturation. It keeps the system available for safety, confirmations, cheap
reads, and user-visible recovery.

The expected result under overload is not universal success. It is explicit
degradation while preserving invariants.

### Ambiguous Provider Outcome

An ambiguous provider outcome occurs when Nimbus cannot tell whether a provider
side effect happened. A timeout after delete is the classic example: the object
may have been deleted even though the client never received an acknowledgement.

The runtime must reconcile before claiming success or retrying unsafe work.

### Bounded Registry

A bounded registry is an in-memory structure whose growth tracks active
workload, not historical traffic. Examples include per-session locks,
rate-limit buckets, nonce caches, idempotency caches, and replay caches.

Unbounded registries are reliability bugs disguised as dictionaries.

### Cell

A cell is a future blast-radius boundary for tenants and sessions: authority
workers, hot cache, worker pool, provider concurrency budget, and event stream
partition.

The codebase does not need cells today, but keys and metrics should include
tenant/session identifiers so cell placement is possible later.

### Clock

The canonical design splits time into wall clock, monotonic clock, and simulation
clock.

Wall time is for user-visible timestamps and expiry records. Monotonic time is
for durations, timeouts, and rate-limit refill. Simulation time is for
deterministic tests.

### Store Graduation Contract

The store graduation contract is the set of invariants every storage stage must
preserve: durable event append before publish, atomic per-session sequence
allocation, action CAS transitions, atomic idempotency lookup plus action
creation, read-after-sequence replay, projection rebuilds, checkpoint/compaction
without losing required audit history, and malformed-record rejection.

It is the reason the design moves from JSON to SQLite to Postgres by access
pattern rather than fashion.

## Testing

### Unit Test

A unit test checks a small behavior in isolation with deterministic inputs and
mocked external boundaries.

This repo uses unit tests heavily for models, auth helpers, storage adapter
mapping, runtime branches, and provider error translation.

### Integration Test

An integration test checks package wiring or transport behavior without using
real cloud/provider credentials by default.

Examples include FastAPI route tests with dependency overrides and generated
client plus adapter flows.

### E2E Test

An e2e test exercises a public workflow through real or near-real boundaries.
Live cloud/provider e2e tests are opt-in and marked so local unit runs stay
fast.

### Property-Based Testing

Property-based testing generates many inputs to verify a property, such as
session ID normalization, HMAC signing, rate-limit behavior, or conversation
bounding.

The point is not random chaos. It is compact statements of invariants over a
large input space.

### Fuzz Harness

A fuzz harness feeds malformed or surprising bytes into a boundary parser.
This repo uses fuzz smoke mode for conversation/session/request-state parsing
without making production logic live in fuzz files.

### BDD Acceptance Test

A BDD acceptance test describes user-facing behavior in Gherkin and binds it to
pytest step definitions.

It is useful when a behavior crosses product language, transport, and runtime
state.

### Deterministic Simulation Testing

Deterministic simulation testing, or DST, is the planned harness where a
seeded scheduler controls task order, time, retries, crashes, duplicates, and
provider responses.

DST is how the future action/session engine should prove invariants under
hostile scheduling without relying on flaky sleeps.

See also: {doc}`deterministic-simulation-testing`.

### Golden Safety Set

A golden safety set is a stable corpus of known prompt-injection, policy,
confirmation, and ambiguous-intent cases.

Every model/tool/runtime change should preserve expected decisions unless the
change intentionally updates the policy contract.

### Invariant Checker

An invariant checker observes a scenario history or event stream and verifies
properties such as tenant isolation, same-actor delete authorization, monotonic
action transitions, durable-before-visible, and replay equality.

### Pre-Seeded Fixture

A pre-seeded fixture is a deterministic setup artifact for tests or DST:
tenant, actor, session, object set, pending delete, artifact manifest, or fake
provider script.

Pre-seeding keeps complex failure tests fast enough to run during normal
development.

## Operations And Scale

### Render Postgres

The current deployment uses Render Postgres so session, nonce, idempotency,
action, event, and artifact state survive redeploys and can be shared by future
replicas. `NIMBUS_STATE_BACKEND=postgres` and `DATABASE_URL` select this store.

The local file/SQLite fallback remains useful for development, but production
state should be backed by Postgres before multiple operators or wrappers depend
on it.

### Runtime Telemetry

Runtime telemetry is the in-memory counter and histogram collector used by
`nimbus_runtime` and `ai_server`. It records wrapper turns, idempotent replays,
auth outcomes, AI response latency, failures, and tool calls.

It is a backend-neutral seam, not a full production metrics system.

### Structured Logging

Structured logging means emitting machine-readable logs with stable fields
rather than only prose strings. The repo uses `structlog` for this style.

### SLI

A service level indicator is a user-visible reliability measurement. Nimbus
candidate SLIs include operation accepted, event rendered, action verified,
wrapper latency, provider error rate, and verification failure rate.

Metrics should avoid raw user IDs, session IDs, object keys, prompts, and
secret-bearing error messages as labels.

### Runtime Alert Rule

A runtime alert rule is a design idea for structured, typed invariants
over runtime vectors. Examples include destructive action without prior
authorization, action stuck in `executing`, provider ambiguity above threshold,
sequence gaps, and idempotency replay misses.

The rule system is a later tool, but the glossary keeps the concept because it
turns reliability claims into reusable checks.

### Client Strategy

Client strategy is the rule that Slack, CLI, web, and MCP are product clients,
not separate product kernels.

Slack is where work appears, CLI is the power-user and development surface, web
is the trust/review surface, and MCP arrives after core tool/action semantics
stabilize.

### Data Residency

Data residency is the 3.0 future constraint that tenants have a home region and
session/action durable state and artifacts belong to that region unless policy
permits otherwise.

The current repo does not implement multiregion placement, but the design warns
against assuming one global namespace forever.

### Postgres

Postgres is the first serious shared durable backend in the canonical design. It
becomes relevant when multiple writable processes, worker fleets, durable audit
queries, or cross-process idempotency are required.

It is not needed for the current single-machine class deployment.

### SQLite

SQLite is the next local state primitive after JSON files become awkward. It
gives transactions, ordered event append, local inspection, and deterministic
test support without external infrastructure.

### Valkey

Valkey is the future hot-state store for ephemeral coordination: rate-limit
buckets, nonce replay hot cache, presence, short idempotency cache, and stream
fanout hints.

It should not be the only durable action ledger.

### Queue And Workers

Queues and workers are the future execution split for long-running or
latency-heavy work. Queue messages should contain only tenant, action ID, and
attempt; the durable store remains the authority.

Introduce them when inline execution threatens API latency or reliability.

### Temporal

Temporal is a possible future durable-execution backend for workflows with
long sleeps, retries, human approvals, and crash recovery.

The design recommendation is to prove the action ledger first, then revisit
Temporal when workflows exceed short request/worker lifecycles.

### Schema Compatibility

Schema compatibility is the discipline for evolving event and artifact
payloads. The canonical design proposes event schema metadata: type, version,
owner, retention, privacy class, compatibility mode, and payload schema.

Adding optional fields is usually safe. Removing fields requires a deprecation
window because replay, clients, and analytics may depend on older payloads.
