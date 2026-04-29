# Nimbus Agent Platform Design 2.0

This document is the second architecture pass for Nimbus. It folds in the
lessons from the earlier design discussion plus the later Figma, Riot Games,
Hudson River Trading, Stripe, Ramp, Linear, OpenAI, Anthropic, S3, DDIA, and
systems-programming threads.

The conclusion is sharper than the first pass:

> Nimbus should be a multiplayer, agent-first session engine for safe cloud
> operations. Chat is only one client. The core product is a live, replayable
> session where humans and agents collaborate on durable, authorized,
> verifiable actions over cloud data.

The implementation should be devastatingly simple:

```text
Core nouns:
  Tenant, Actor, Session, Operation, Event, Action, Object, Artifact

Core verbs:
  Propose, Authorize, Execute, Verify, Observe, Replay
```

Everything else is an adapter, provider, store, or client.

## Executive Summary

Nimbus 1.0 is a chat/runtime wrapper around cloud storage. Nimbus 2.0 should be
an agent operating layer:

```text
User intent
  -> verified actor
  -> session operation
  -> server-authoritative event
  -> policy decision
  -> durable action
  -> execution
  -> verification artifact
  -> realtime client update
```

The hard product interaction is not "send a prompt and get a response." It is:

> Multiple humans and agents safely coordinate work in one live session without
> losing state, duplicating side effects, crossing tenants, or disagreeing about
> what happened.

That is the Figma move. Figma built a custom graphics/collaboration engine
because React alone could not deliver the core product experience. Nimbus needs
a custom session/action engine because ordinary request/response chat cannot
deliver trustworthy multiplayer agent work.

The system can start as a modular Python monolith. It should still have the
internal shape of a serious distributed product:

- server-authoritative session event log
- typed operation protocol
- durable action ledger
- verified actor identity
- policy gate
- deterministic action state machine
- immutable artifacts
- realtime event projection for clients
- deterministic simulation testing
- clean store/provider/executor interfaces

Do not start by adding Kubernetes, Temporal, Kafka, Bazel, protobuf, Rust, or a
vector database. Start by building the kernel those tools could support later.

## What Changed Since The 1.0 Design

The 1.0 design correctly identified the action ledger as the core primitive.
The 2.0 design keeps that, but broadens the unit of product experience from
`Action` to `Session`.

The updated model:

```text
Session is the collaborative document.
Operation is what a client asks to do.
Event is what the server says happened.
Action is a durable side-effecting unit of work.
Artifact is proof or work product.
Projection is how a client renders events.
```

This unlocks multiplayer, reconnect, replay, web/Slack/CLI consistency, and
deterministic simulation.

## Industry Lessons Applied

These lessons are used as design constraints, not as name-dropping.

| Source | Core lesson | Nimbus 2.0 consequence |
| --- | --- | --- |
| Figma | Build a custom engine for the impossible-feeling product interaction. | Build a custom multiplayer session/action engine. React/Slack/CLI are shells. |
| Riot Messaging Service | Realtime clients need edge/routing separation, reconnect, resource-version messages, load tests, and known capacity. | Add Session Edge, Session Authority, ordered events, reconnect by sequence, and session load tests. |
| Riot Chronobreak | Record inputs and replay deterministic state to recover and debug. | Build deterministic replay and DST for session/action flows. |
| Riot online services | Operate the whole product, not a pile of services. Version environments immutably. | Add a versioned `RuntimeSpec` for model, tools, policy, limits, stores, and feature flags. |
| Riot patcher | Use manifests, content addressing, SQLite cache, verification, repair, and bounded concurrency. | Use artifact manifests, digest checks, local session stores, and repair/reconciliation paths. |
| HRTWorker | Hide cross-context messaging behind normal async APIs; multiplex calls; support streaming and cancel. | Add a `SessionWorker` client abstraction and an operation protocol: `CALL`, `RESPONSE`, `COMPLETE`, `ERROR`, `CANCEL`. |
| HRT HeraclesQL | Avoid text templates for critical rules; use typed structured expressions. | Add typed runtime rules later for alerts, invariants, and DST assertions. |
| HRT typing | Types reduce blast radius in large Python systems. | Use strong ID/domain types in the kernel instead of naked strings. |
| HRT lazy imports | Fast time-to-useful matters; avoid paying startup cost for unused imports. | Lazy-load provider clients and heavy tools; make side effects explicit. |
| Stripe Minions | Agents must work inside existing workflows and produce reviewable outputs. | Sessions include artifacts, action timelines, verification, and review state. |
| Ramp Inspect | Background agents need full context, all tools, fast startup, snapshots, multiplayer, and verification. | Nimbus sessions are resumable, event-sourced, multiplayer, and verification-first. |
| Linear Agent | Agents are most useful inside the work graph. | Slack thread, CLI, web, and MCP all attach to the same session. |
| OpenAI/Anthropic agent guidance | Tools, structured outputs, tracing, evals, and simple workflows beat vague autonomy. | Model proposes; runtime validates; policy authorizes; verification proves. |
| DDIA/Kleppmann | Distributed systems are data and failure semantics. | Define invariants, stores, transitions, idempotency, and replay before infra. |
| S3 | Durability is engineered through constant suspicion of failure. | Never return success before durable result and verification event. |
| Ousterhout | Deep modules with shallow interfaces. | `ActionStore`, `SessionEventStore`, `PolicyEngine`, and `Executor` should be small APIs with strong internals. |

## Product Model

Nimbus is not a general file chatbot. It is an agent workbench for cloud
operations.

Real user jobs:

- "Find duplicate CSV exports and show me a cleanup plan."
- "Upload these Slack attachments to `invoices/april/`."
- "Summarize what changed in `reports/` this week."
- "Delete the old backup, but show me exactly what you will delete first."
- "Compare three folders and tell me which one is safe to archive."
- "Let my teammate join this session and approve the final delete."
- "Show me every action Nimbus took yesterday."

The product contract should be:

```text
Start or join a session.
Submit operations.
Observe ordered events.
Approve or cancel actions.
Inspect artifacts.
Replay the session if something seems wrong.
```

## Top-Level Architecture

```text
Clients
  Slack bot
  CLI
  Web app
  MCP host
  future browser extension
        |
        v
Session Edge
  HTTP, WebSocket/SSE, auth, presence, reconnect
        |
        v
Session Router
  session_id -> Session Authority
        |
        v
Session Authority
  validates operations
  assigns sequence numbers
  appends events
  applies policy
  creates actions
  broadcasts events
        |
        +-------------------+
        |                   |
        v                   v
Action Executor       Projection/Replay
  storage actions       client state
  AI calls              support/debug
  verification          deterministic tests
        |
        v
Providers
  OpenRouter
  S3
  future GCS/Drive/Dropbox
```

In the current repo:

- `ai_server` is the first Session Edge.
- `nimbus_runtime` should become the Session Authority and runtime kernel.
- `openrouter_ai_client_impl` remains provider-specific AI execution.
- `aws_client_impl` remains provider-specific storage execution.
- future stores start file-backed/SQLite, then Postgres/Valkey when needed.

## The Session Document

Borrow the Figma mental model: a Nimbus session is a collaborative document.

```text
SessionDoc
  metadata
  participants
  prompts
  model messages
  operations
  events
  actions
  artifacts
  comments
  confirmations
  presence
  child sessions
```

Different fields need different consistency models:

| State | Consistency model |
| --- | --- |
| prompts | append-only server-ordered events |
| model messages | append-only server-ordered events |
| actions | server-authoritative state machine |
| artifacts | immutable records |
| comments | append-only first, editable later with versions |
| presence | ephemeral best-effort pub/sub |
| draft prompt text | local-only first, CRDT later if needed |
| session title | version-checked update or last-write-wins |

Do not use CRDTs for actions. Correctness state must be server-authoritative.
Use CRDTs later only for collaborative drafts or rich text.

## Operation Protocol

Clients do not mutate state directly. They submit operations. The Session
Authority validates them and emits events.

Inspired by HRTWorker, cross-context and client-server work should use one
envelope:

```text
CALL(operation_id, method, args)
RESPONSE(operation_id, progress_or_event)
COMPLETE(operation_id, final_result)
ERROR(operation_id, structured_error)
CANCEL(operation_id)
```

Example:

```json
{
  "type": "CALL",
  "operation_id": "op_01HX",
  "method": "AddPrompt",
  "args": {
    "session_id": "ses_slack_T123_C456_1713840000",
    "text": "Find duplicate CSV exports under reports/",
    "idempotency_key": "slack-event-E123"
  }
}
```

Server response stream:

```json
{
  "type": "RESPONSE",
  "operation_id": "op_01HX",
  "data": {
    "event_type": "prompt_added",
    "sequence": 41
  }
}
```

```json
{
  "type": "RESPONSE",
  "operation_id": "op_01HX",
  "data": {
    "event_type": "action_proposed",
    "sequence": 42,
    "action_id": "act_123"
  }
}
```

```json
{
  "type": "COMPLETE",
  "operation_id": "op_01HX",
  "data": {
    "session_id": "ses_slack_T123_C456_1713840000",
    "latest_sequence": 48
  }
}
```

Operation methods:

```text
StartSession
JoinSession
AddPrompt
AddComment
AuthorizeAction
CancelAction
GetSessionEvents
CreateChildSession
AttachArtifact
SetSessionTitle
```

Only runtime-internal code can emit:

```text
ActionStarted
ActionSucceeded
VerificationPassed
VerificationFailed
```

## Event Protocol

Events are the durable facts of the session. They are ordered per session.

```python
@dataclass(frozen=True, slots=True)
class SessionEvent:
    tenant_id: TenantId
    session_id: SessionId
    sequence: SequenceNumber
    event_id: EventId
    event_type: EventType
    actor_id: ActorId | None
    resource: ResourceRef
    version: int
    payload: Mapping[str, object]
    created_at: datetime
```

Riot RMS-style resource messages keep realtime payloads small:

```json
{
  "sequence": 93,
  "event_type": "action_completed",
  "resource": "sessions/s_123/actions/act_456",
  "version": 7,
  "payload": {
    "status": "succeeded",
    "artifact_id": "art_verify_789"
  }
}
```

Large artifacts are fetched separately.

## Domain Types

The kernel should use strong types, not raw strings everywhere. This is an HRT
typing lesson: lightweight types catch cross-wiring mistakes without heavy
runtime objects.

```python
TenantId = NewType("TenantId", str)
SessionId = NewType("SessionId", str)
ActorId = NewType("ActorId", str)
ActionId = NewType("ActionId", str)
ArtifactId = NewType("ArtifactId", str)
EventId = NewType("EventId", str)
IdempotencyKey = NewType("IdempotencyKey", str)
SequenceNumber = NewType("SequenceNumber", int)
```

Core models:

```python
@dataclass(frozen=True, slots=True)
class TenantIdentity:
    tenant_id: TenantId
    platform: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class VerifiedActor:
    tenant: TenantIdentity
    actor_id: ActorId
    external_user_id: str
    auth_source: Literal[
        "slack_signed_event",
        "cli_local",
        "github_oauth",
        "oidc",
        "service_account",
    ]
    bridge_id: str | None
    verified_at: datetime
```

```python
@dataclass(frozen=True, slots=True)
class ObjectRef:
    tenant_id: TenantId
    provider: Literal["s3", "gcs", "dropbox", "drive"]
    container: str
    object_name: str
    version_id: str | None = None
```

## Runtime Spec

Riot's environment model points to a missing Nimbus primitive: a versioned spec
for the whole runtime, not scattered config and env vars.

```python
@dataclass(frozen=True, slots=True)
class RuntimeSpec:
    spec_version: str
    model_policy: ModelPolicy
    tool_surface: tuple[ToolSpec, ...]
    action_limits: ActionLimits
    confirmation_policy: ConfirmationPolicy
    storage_policy: StoragePolicy
    replay_policy: ReplayPolicy
    artifact_policy: ArtifactPolicy
    feature_flags: Mapping[str, bool]
```

Every session should record the runtime spec version used to process it. This
lets support answer:

- Which model policy was active?
- Which tools could the model see?
- Which confirmation policy applied?
- Were child sessions enabled?
- Which action schema version was used?

This prevents product drift across Slack, CLI, web, and deployments.

## Action Ledger

Actions remain the durable side-effect unit.

```text
proposed
  -> awaiting_confirmation
  -> authorized
  -> queued
  -> executing
  -> verifying
  -> succeeded

proposed -> failed_terminal
awaiting_confirmation -> expired
queued -> cancelled
executing -> failed_retryable
executing -> failed_terminal
verifying -> failed_retryable
verifying -> failed_terminal
```

Valid transitions are enforced with compare-and-set semantics.

```python
class ActionStore(Protocol):
    def transition(
        self,
        *,
        tenant_id: TenantId,
        action_id: ActionId,
        expected: ActionStatus,
        next_status: ActionStatus,
        event: ActionEventCreate,
    ) -> Action | None:
        """Move the action only if it is still in the expected state."""
```

No worker, client, or model gets to "set action succeeded." The executor may
produce a result; the authority records the state transition.

## Client SessionWorker

The web client should use an HRTWorker-inspired SharedWorker abstraction:

```text
Browser tab A
Browser tab B
Browser tab C
      |
      v
NimbusSessionWorker
  one server stream
  local event projection
  operation multiplexing
  cancellation
  reconnect/catch-up
```

Benefits:

- one WebSocket/SSE stream per browser profile instead of per tab
- no duplicate session event processing
- consistent local projection across tabs
- cancellable long operations
- lower server load during dashboard-heavy usage
- simpler web UI components

Client API:

```typescript
for await (const update of session.call("AddPrompt", args)) {
  render(update)
}
```

Under the hood:

```text
CALL -> RESPONSE* -> COMPLETE
CALL -> ERROR
CALL -> CANCEL
```

Fallback if SharedWorker is unavailable:

- one normal tab-local worker
- same operation protocol
- no cross-tab sharing

## Realtime Multiplayer

The server is authoritative. Clients hold projections.

Reconnect protocol:

```text
Client connects:
  session_id
  actor credentials
  last_seen_sequence

Server:
  verifies actor
  lists events after last_seen_sequence
  streams new events
```

Presence is separate:

```text
presence_joined
presence_heartbeat
presence_left
```

Presence events can be ephemeral. They should not affect replay or action
correctness.

## Projections

A projection is the materialized client view of a session event stream.

```python
class SessionProjection:
    def apply(self, event: SessionEvent) -> None:
        """Update local derived state from one ordered event."""
```

Projection invariants:

- applying events in sequence produces the same state everywhere
- replay from event 0 produces the same state as live application
- applying the same event twice is either rejected or idempotent
- missing sequence numbers trigger catch-up

Projection types:

```text
Client projection:
  UI state for web/CLI/Slack rendering

Support projection:
  compact action/session timeline

Metrics projection:
  counters/histograms from events

DST projection:
  oracle state used by deterministic tests
```

## Identity And Authority

The actor chain:

```text
Slack signs event
  -> bridge verifies Slack
  -> bridge normalizes actor claims
  -> bridge signs Nimbus request
  -> Nimbus verifies bridge
  -> Nimbus creates VerifiedActor
```

For destructive actions, confirmation binds:

```text
tenant_id
session_id
actor_id
action_id
action_kind
target_hash
expires_at
confirmation_token_hash
```

Text confirmation is one UI. Interactive buttons are better. Internally both
become `AuthorizeAction`.

## Least-Privilege Agent Capabilities

Riot's Key Conjurer lesson maps directly to agent safety: do not give broad,
long-lived credentials to agents.

Instead, issue short-lived capabilities:

```text
capability:
  action_id: act_123
  actor_id: actor_456
  provider: s3
  container: team-bucket
  allowed_operation: delete_file
  allowed_object: reports/old.csv
  expires_at: 2026-04-30T18:30:00Z
```

The executor should use action-scoped authority where possible. If provider
SDKs require broader process credentials in the MVP, enforce the narrow
capability in runtime before calling the provider.

## Artifact Manifests

Riot's patcher suggests a clean artifact design:

```text
ArtifactManifest
  artifact_id
  kind
  schema_version
  content_digest
  chunks
  size_bytes
  created_by_action
  verification
```

Artifacts should be immutable and content-addressed where practical.

Examples:

- duplicate-file cleanup manifest
- upload verification report
- before/after object listing
- model trace
- child-session summary

Large artifacts can move to object storage later. Metadata stays in the store.

## Verification

Every side-effecting action needs proof.

| Action | Verification |
| --- | --- |
| `upload_attachment` | base64 decodes, byte count matches, digest matches, provider object exists, provider metadata reconciles |
| `delete_file` | target exactly matches authorized object, delete result recorded, follow-up state reconciled |
| `list_files` | prefix, count, returned count, truncation, provider timestamp captured |
| `get_file_info` | object name/version match request, domain error if missing |
| `summarize_prefix` | cites artifacts/object refs; does not rely on ungrounded text |
| `spawn_child_session` | child budget and policy inherited; parent records child result |

Verification failures are first-class events, not hidden logs.

## Data Store Evolution

Use the smallest store that satisfies the topology.

### Stage 0: JSON Files

Current state. Good for the class deployment and simple sessions.

### Stage 1: SQLite

Good next step for one-process session/event/action state:

- transactions
- local inspection
- ordered event append
- corruption detection
- easy projection tests
- close to Cloudflare Durable Object's per-object SQLite mental model

SQLite is especially attractive for a local Session Authority prototype.

### Stage 2: Postgres

Use when multiple processes or durable shared state appear:

- sessions
- events
- actions
- actors
- artifacts metadata
- idempotency records
- audit queries

### Stage 3: Valkey

Use for ephemeral hot state:

- rate limits
- nonce replay cache
- presence
- short idempotency cache
- stream fanout hints

Do not use Valkey as the only action ledger.

### Stage 4: Queue And Workers

Use when inline execution hurts API latency or reliability:

- action execution
- AI calls
- large uploads
- repair/reconciliation
- child session fanout

Queue messages contain `tenant_id`, `action_id`, and `attempt`, not full
authority.

## Scaling Model

Scale by sessions and tenants.

```text
Single modular service
  -> one process, file/SQLite store

Session-authority split
  -> API edge plus per-session authority objects

Shared state
  -> Postgres for durable state, Valkey for hot state

Worker fleet
  -> queues for long-running actions

Regional sharding
  -> tenant/session placement near users/data
```

Hot-path targets:

| Path | Target |
| --- | --- |
| submit operation -> first event | sub-second, ideally provider independent |
| session reconnect catch-up | proportional to missed events, paged |
| event publish -> web render | tens to low hundreds of milliseconds |
| cheap read action | bounded by provider latency |
| destructive action | bounded by confirmation and verification |
| artifact fetch | CDN/object-store ready later |

Backpressure:

- per-tenant operation rate
- per-actor rate
- per-session active operation limit
- max queued actions per session
- max child sessions
- max AI calls per tenant
- max provider operations per tenant
- bounded artifact size

Degrade in this order:

```text
bulk uploads
large summaries
background child sessions
AI-heavy operations
cheap read-only storage actions
destructive actions fail closed
```

## Performance Strategy

HRT and Riot both teach: measure the right unit.

Nimbus units:

- time to first useful event
- publish-to-render latency
- operation completion latency
- reconnect catch-up time
- event replay time
- action verification time
- provider time-to-first-token
- provider storage latency
- artifact generation time

Optimizations that make sense early:

- lazy-load OpenRouter and storage clients
- avoid importing heavy provider SDKs for `--help` or docs
- pre-hydrate session context before model call
- use local projection cache in web client
- avoid duplicating WebSocket streams across tabs
- page event history
- store large artifacts outside event payloads

Optimizations to defer:

- Rust/WASM projection engine
- protobuf everywhere
- Bazel migration
- custom Python fork
- distributed cache hierarchy
- low-level kernel/huge-page tuning

Those are earned by measurement, not aesthetics.

## Deterministic Simulation Testing

We should build a DST harness. This is non-negotiable for the long-term
ambition of the product.

The simulation components:

```text
SimulatedClock
DeterministicScheduler(seed)
SimulatedSessionEventStore
SimulatedActionStore
SimulatedIdempotencyStore
SimulatedEventBus
SimulatedAIProvider
SimulatedStorageProvider
FaultInjector(seed)
InvariantChecker
```

The scheduler controls:

- task ordering
- sleeps/timers
- retries
- network delay
- provider responses
- crashes
- reconnects
- duplicate delivery

Example generated scenario:

```text
1. Actor A submits delete request.
2. Bridge retries same operation.
3. Runtime creates one action.
4. Actor B tries to confirm.
5. Runtime rejects.
6. Actor A confirms.
7. Worker transitions action to executing.
8. Storage deletes object but response is lost.
9. Worker crashes.
10. Reconciler restarts and checks object state.
11. Runtime records success with ambiguity note.
12. Client reconnects from sequence 4.
```

Invariants:

```text
No cross-tenant event is visible.
One idempotency key creates at most one action.
Delete does not execute without same-actor authorization.
Action state never moves backward.
Success is not visible before durable event.
Replay projection equals live projection.
Cancelled operation emits no later success.
Expired confirmation cannot authorize an action.
Queue duplicate cannot execute an action twice.
```

DST tests should live near `nimbus_runtime`, not as throwaway scripts. Start
small and deterministic. Add failures incrementally.

## Fault Injection Matrix

Crash points:

```text
after operation accepted
after event append before broadcast
after action create before response
after confirmation before queue
after queue before response
after worker claim before provider call
after provider success before result write
during artifact write
during projection replay
```

Duplicate delivery:

```text
same Slack event
same signed Nimbus request
same operation envelope
same confirmation token
same queue message
two workers claiming action
```

Reordering:

```text
confirmation before original response reaches client
retry while original executing
queue attempt 2 before attempt 1
old confirmation after new action
client reconnects during artifact creation
```

Provider ambiguity:

```text
OpenRouter timeout after tool call
S3 timeout after delete
S3 upload succeeds but metadata read fails
artifact store write succeeds but event append fails
```

## Alerting And Rules

Inspired by HeraclesQL, runtime alerts and invariants should be structured,
typed, and reusable. Do not build this first, but design for it.

Possible rule API:

```python
def destructive_action_without_authorization(v: RuntimeVectors) -> Rule:
    return v.action_started(kind="delete_file").unless_before(
        v.action_authorized(kind="delete_file")
    )
```

Useful rules:

- action stuck in `executing` beyond timeout
- verification failure rate above threshold
- provider ambiguity count above threshold
- session event sequence gaps
- idempotency replay miss after duplicate request
- cross-tenant lookup attempt
- delete action created without confirmation requirement

## Security And Anti-Abuse

Think like Riot anti-cheat: users and models will try to bypass constraints,
intentionally or accidentally.

Threats:

- forged wrapper request
- spoofed actor ID
- prompt injection through filenames/tool output
- model asks for a broader bucket
- stale confirmation replay
- cross-tenant action ID
- support/admin bypass
- unbounded child-session spawning
- cost abuse through repeated AI calls
- large attachment denial of service

Controls:

- verified actor envelope
- HMAC now, OIDC/workload identity later
- tenant-scoped keys everywhere
- action-scoped capabilities
- confirmation token hashes
- policy fail closed
- bounded child sessions
- attachment byte validation against actual payload
- artifact size limits
- untrusted data sandboxing in prompts
- durable audit events

## Real User Flows

### Slack Cleanup

```text
User:
  @nimbus find duplicate CSV exports under reports/ and show me a cleanup plan

Flow:
  Slack bridge verifies event
  -> Nimbus AddPrompt operation
  -> prompt_added event
  -> context_hydrated event
  -> read-only list/info actions
  -> cleanup manifest artifact
  -> action proposals requiring confirmation
  -> Slack renders plan and confirm buttons
```

### Web Multiplayer Review

```text
PM starts session from web.
Engineer joins from CLI.
Designer joins from Slack.
All clients subscribe from last_seen_sequence.
Agent proposes actions.
Engineer comments on one action.
PM approves safe subset.
Runtime executes and verifies.
All clients render the same event stream.
```

### Multi-Tab Web

```text
Tab A opens session.
Tab B opens same session.
Both use NimbusSessionWorker.
Worker holds one server stream.
Tabs receive same local projection.
Tab B cancels an operation.
Worker sends CANCEL operation.
Server emits operation_cancelled.
Both tabs update.
```

### CLI Upload

```text
nimbus session invoices
> upload attached files to invoices/april/

Runtime:
  validates files
  creates upload actions
  uploads through CloudStorageClient
  verifies object metadata
  creates upload report artifact
  prints durable action IDs and status
```

### Child Session Research

```text
User asks:
  Compare cleanup opportunities in logs/, exports/, tmp/

Parent session:
  creates three bounded child sessions
  each child inspects one prefix
  children create manifests
  parent summarizes artifacts
  no child can execute destructive actions without parent/user authorization
```

## API Evolution

The existing `/ai/chat/turn` stays as compatibility. The 2.0 API should become
session-operation-oriented.

```text
POST /ai/sessions
GET  /ai/sessions/{session_id}
GET  /ai/sessions/{session_id}/events?after=N
POST /ai/sessions/{session_id}/operations
POST /ai/sessions/{session_id}/prompts
GET  /ai/actions/{action_id}
POST /ai/actions/{action_id}/confirm
POST /ai/actions/{action_id}/cancel
GET  /ai/artifacts/{artifact_id}
```

Operation response:

```json
{
  "operation_id": "op_123",
  "session_id": "ses_456",
  "accepted": true,
  "latest_sequence": 82
}
```

Events endpoint response:

```json
{
  "session_id": "ses_456",
  "from_sequence": 83,
  "events": [
    {
      "sequence": 83,
      "event_type": "action_started",
      "resource": "sessions/ses_456/actions/act_789",
      "version": 3,
      "payload": {
        "status": "executing"
      }
    }
  ],
  "next_after": 83
}
```

## Package Evolution

Proposed package/module split:

```text
nimbus_runtime.identity
  TenantIdentity, VerifiedActor, strong ID types

nimbus_runtime.operations
  OperationEnvelope, operation methods, validation

nimbus_runtime.events
  SessionEvent, event types, projection helpers

nimbus_runtime.actions
  Action, ActionStatus, ActionStore protocol

nimbus_runtime.policy
  PolicyEngine, decisions, confirmation requirements

nimbus_runtime.artifacts
  Artifact, ArtifactManifest, verification reports

nimbus_runtime.execution
  ActionExecutor, local inline executor

nimbus_runtime.simulation
  DST harness components

ai_server
  HTTP auth, request parsing, idempotency, session routes
```

Avoid a broad `utils.py`. Every module should own one domain idea.

## What To Add

- `TenantId`, `SessionId`, `ActionId`, `ActorId`, `SequenceNumber`
- `VerifiedActor`
- `RuntimeSpec`
- `OperationEnvelope`
- `SessionEvent`
- `SessionEventStore`
- `SessionProjection`
- `ActionStore`
- `ArtifactManifest`
- `Clock` protocol
- `ActionExecutor`
- DST harness
- `GET /ai/sessions/{id}/events?after=N`
- eventually browser `NimbusSessionWorker`

## What To Remove Or Avoid

- route-local product semantics
- raw string IDs in the runtime kernel
- model-direct destructive execution
- full payloads inside realtime events
- vector database before exact metadata/search is exhausted
- microservices before package boundaries are stable
- Bazel/protobuf before cross-language/codegen pressure exists
- CRDTs for action state
- unbounded child sessions or retries

## Bottom-Up Build Plan

This is the most practical path from the current repo.

1. **Add invariant doc.**
   Record tenant isolation, same-actor authorization, monotonic action state,
   idempotency, durable-before-visible, and replay equality.

2. **Add strong identity types.**
   `TenantIdentity`, `VerifiedActor`, and typed IDs in `nimbus_runtime`.

3. **Add `Clock` protocol.**
   Thread it through runtime, confirmations, idempotency, and stores.

4. **Add session events.**
   Define `SessionEvent`, event types, sequence numbers, and file-backed
   `SessionEventStore`.

5. **Add projection replay.**
   Build `SessionProjection.from_events()` and assert replay equals live state.

6. **Add operation envelope.**
   Represent `AddPrompt`, `AuthorizeAction`, `CancelAction` internally before
   adding new public routes.

7. **Add action store.**
   Move pending delete state into a durable action ledger.

8. **Port delete flow.**
   Use `awaiting_confirmation -> authorized -> executing -> verifying`.

9. **Port upload flow.**
   Represent attachment uploads as actions and verification artifacts.

10. **Add events endpoint.**
    `GET /ai/sessions/{id}/events?after=N` for reconnect and future clients.

11. **Add DST seed harness.**
    Start with duplicate confirmation, retry, wrong actor, and storage timeout.

12. **Add local SessionWorker demo later.**
    Build only after the server event protocol exists.

13. **Promote SQLite if JSON becomes awkward.**
    SQLite should be the next local store, not a distributed database.

14. **Promote Postgres/Valkey/queue when topology requires it.**
    Shared infra follows proven interfaces.

## Design Constraints

The system should stay simple enough that a new engineer can explain it:

```text
Clients submit operations.
The session authority validates them.
The authority appends ordered events.
Actions are durable state machines.
Executors perform authorized work.
Verifiers produce artifacts.
Clients render projections.
DST proves invariants under failure.
```

If a proposed component does not make one of those lines better, defer it.

## References

- [Stripe Minions: one-shot, end-to-end coding agents](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents)
- [Stripe Minions, Part 2](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2)
- [Ramp Inspect: background agent](https://builders.ramp.com/post/why-we-built-our-background-agent)
- [How Linear uses Linear Agent](https://linear.app/now/how-we-use-linear-agent-at-linear)
- [OpenAI agent-building tools](https://openai.com/index/new-tools-for-building-agents/)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: Model Context Protocol](https://www.anthropic.com/news/model-context-protocol)
- [Riot: Running Online Services Part VI](https://www.riotgames.com/en/news/running-online-services-riot-part-vi)
- [Riot Messaging Service](https://www.riotgames.com/en/news/riot-messaging-service)
- [Riot: Determinism in League of Legends](https://www.riotgames.com/en/news/determinism-league-legends-introduction)
- [Riot: The New League Patcher](https://www.riotgames.com/en/news/supercharging-data-delivery-new-league-patcher)
- [Riot: Key Conjurer](https://www.riotgames.com/en/news/key-conjurer-our-policy-least-privilege)
- [HRTWorker](https://www.hudsonrivertrading.com/hrtbeat/hrtworker-a-sharedworker-framework/)
- [HRT HeraclesQL](https://www.hudsonrivertrading.com/hrtbeat/heraclesql-a-python-dsl-for-writing-alerts/)
- [HRT: Python lazy imports](https://www.hudsonrivertrading.com/hrtbeat/inside-hrts-python-fork/)
- [HRT: Python type annotations](https://www.hudsonrivertrading.com/hrtbeat/building-robust-codebases-with-pythons-type-annotations/)
- [Pragmatic Engineer: Designing Data-Intensive Applications](https://newsletter.pragmaticengineer.com/p/designing-data-intensive-applications)
- [Pragmatic Engineer: How S3 is built](https://newsletter.pragmaticengineer.com/p/how-aws-s3-is-built)
- [Pragmatic Engineer: The Philosophy of Software Design](https://newsletter.pragmaticengineer.com/p/the-philosophy-of-software-design)
