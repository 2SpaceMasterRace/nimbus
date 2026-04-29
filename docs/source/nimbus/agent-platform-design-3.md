# Nimbus Agent Platform Design 3.0

This document is the third architecture pass for Nimbus. It keeps the 2.0
decision that Nimbus should be a multiplayer, agent-first session engine, then
adds only the new ideas that improve the product without fighting the existing
direction.

The most important update is this:

> Nimbus should have a small, typed runtime kernel with a server-authoritative
> event log, but it should treat events, actions, time, backpressure, and
> client streams with the discipline of infrastructure systems.

Do not build a big-company architecture for a small project. Build a small
architecture that has the same bones as a serious platform.

## What This 3.0 Pass Adds

The 2.0 design already had:

- multiplayer sessions as collaborative documents
- typed operation protocol
- server-authoritative session events
- action ledger
- deterministic simulation testing
- SharedWorker-style web client projection
- Riot-style replay and reconnect
- HRT-style typed APIs and streaming operation envelopes

The 3.0 pass adds:

- cell-aware blast-radius thinking from Roblox
- schematized event contracts inspired by Roblox analytics ingestion
- trust-and-safety feedback loops inspired by Roblox and Block
- durable execution tradeoffs from Netflix Temporal and WAL work
- prioritized load shedding from Netflix reliability work
- WebSocket/proxy reconnect lessons from Netflix and Riot
- workflow-first MCP/tool design from Block
- pre-seeded test/simulation data from Rippling
- explicit time-source semantics from Rippling
- Python process lifecycle and memory guardrails from Rippling
- system-programming performance rules for hot paths
- explicit pros/cons where the new material conflicts with our current plan

## Current Product Statement

Nimbus is an agent-first operations platform for cloud files.

```text
Users and agents collaborate in live sessions.
Sessions accept operations.
The server records ordered events.
Actions are durable state machines.
Policy authorizes work.
Executors perform side effects.
Verification produces artifacts.
Clients render projections.
DST proves invariants under failure.
```

Slack, CLI, web, and MCP are clients. OpenRouter and S3 are providers. The
runtime kernel is the product.

## New Lessons Worth Adopting

| Source area | Lesson | Nimbus decision |
| --- | --- | --- |
| Roblox cellular infrastructure | Small failures propagate when traffic, retries, or dependencies cross blast boundaries. | Add tenant/session cells as a scale concept, but keep one-process local deployment now. |
| Roblox platform reliability | Measure reliability from the consumer's point of view and include dependency quality. | Define session/action SLIs from the client perspective: operation accepted, event rendered, action verified. |
| Roblox analytics ingestion | Schemas, ownership metadata, retention, and schema compatibility matter once events become the platform. | Add a session event schema registry concept before event volume grows. |
| Roblox AI moderation | AI safety needs automated screening, human review for rare cases, golden sets, adversarial sampling, and active learning. | Add agent-safety eval corpora, red-team prompts, and human review paths for policy-ambiguous actions. |
| Database Internals | Reliable systems start with storage mechanics: log records, page/cache boundaries, checksums, compaction, and recovery. | Treat the session store like a small database: append, validate, replay, checkpoint, compact, and repair before adding distributed infra. |
| DDIA 2e | Distributed systems are dataflow, failure semantics, evolution, and explicit trade-offs. | Separate system-of-record events from derived projections; choose consistency per workflow; version schemas; make duplicate delivery safe. |
| Netflix Temporal | Durable execution is valuable for long-running workflows with sleeps, retries, human approvals, and crash recovery. | Keep in-house action ledger now; document Temporal as the upgrade path for long workflows. |
| Netflix WAL | Append a durable fact before applying downstream mutations. | Treat `SessionEventStore` as the write-ahead log for session projections and action execution. |
| Netflix prioritized load shedding | Not all requests have equal value during overload. | Add operation priority classes and shed background work before confirmations or cheap reads. |
| Netflix WebSocket proxy | Realtime systems need heartbeats, idle cleanup, reconnect, and registry correctness. | Build reconnect-by-sequence and client presence as first-class session-edge concepts. |
| Block MCP playbook | LLM tools should be workflow-first, concise, bounded, and recoverable. | Expose high-level Nimbus tools, not raw storage or internal API calls. |
| Block red team | Treat AI inputs and outputs as untrusted; never use the model for access control. | Keep policy outside the model and add prompt-injection red-team cases. |
| Block skills | Decide what the agent must not decide; encode deterministic parts in scripts/rules. | Runtime rules and action scoring are deterministic; the model handles interpretation and planning. |
| Rippling PreSeeding | Expensive setup should be amortized with reusable snapshots/artifacts. | Pre-seed DST scenarios, fake stores, and storage fixtures for fast local feedback. |
| Rippling monotonic time | Time semantics differ across platforms; choose and inject the clock deliberately. | Add a `Clock` protocol and distinguish wall time, monotonic time, and session simulation time. |
| Rippling pre-fork | Python process lifecycle can save cost, but needs fail-fast guardrails for global clients and threads. | Keep provider clients lazy and explicit; later use pre-fork only with connection/thread checks. |
| Low-latency systems programming | Performance comes from data layout, allocation discipline, bounded queues, batching, and measuring tail latency. | Make session hot paths compact, bounded, append-only, and measurable before changing languages. |

## Database Internals And DDIA Applied

Database Internals is the mechanism book for Nimbus: it pushes the session
engine toward a few storage primitives that are easy to reason about under
crash, replay, and repair.

DDIA 2e is the trade-off book for Nimbus: it pushes the architecture to choose
data models, encodings, consistency, replication, transactions, and streaming
semantics per workflow instead of treating "distributed" as one generic mode.

The architectural sentence for Nimbus 3.0:

> Nimbus is an event-sourced agent session runtime: commands are validated at
> the boundary, effects are committed through an idempotent action ledger, and
> all user-visible state is a replayable projection of durable session events.

Direct consequences:

1. **The session event log is the system of record.**
   Prompts, model updates, tool requests, confirmations, action transitions,
   verification results, comments, and branch/PR updates are durable events.
   Client UI, Slack messages, summaries, stats, search indexes, and live
   socket state are derived projections.

2. **Destructive actions are transactions, not chat messages.**
   Delete, overwrite, push, merge, and bulk-copy flows require actor identity,
   target digest, policy decision, confirmation token, idempotency key, expiry,
   action transition, and verification event.

3. **WAL thinking comes before distributed-systems machinery.**
   The first robust store can be file-backed or SQLite-backed, but it must
   support durable append, per-session sequence numbers, validated record
   framing, replay, checkpoints, and compaction. Kafka, Temporal, or a
   dedicated streaming system can arrive later without changing the session
   contract.

4. **Schema evolution is part of the API.**
   Every persisted event, action, artifact, operation, and tool schema carries a
   version. Old events remain readable. New fields are additive by default.
   Breaking changes require an explicit migration or compatibility adapter.

5. **Consistency is chosen per workflow.**
   Destructive authorization, action state transitions, idempotency records,
   per-session event ordering, and spend limits need strong consistency within
   their authority boundary. Presence, token streaming, metrics, previews, and
   usage charts can be eventually consistent.

6. **Replay and duplicate delivery are default cases.**
   Slack events, webhooks, model callbacks, socket reconnects, browser retries,
   and queue redelivery may happen more than once. Idempotency keys and event
   sequence numbers make duplicates boring.

7. **Stores graduate by access pattern, not taste.**
   JSON files are acceptable for one-machine teaching deployments. SQLite is
   the next local authority store. Postgres appears when multiple processes or
   shared durability are needed. Valkey is for ephemeral hot state. Queues are
   for long-running or retryable work.

## Lessons Intentionally Not Adopted Yet

| Idea | Why not now |
| --- | --- |
| Roblox-scale active/active cells | Useful scale target, but premature for one Fly.io machine and class demo. Keep the cell model conceptual. |
| Full analytics lake pipeline | Session events are not yet at data-lake scale. Add schemas and retention first. |
| Netflix Maestro/Temporal immediately | Our workflows are still small enough for an action ledger and executor. Temporal is a later upgrade. |
| GraphQL federation | The current problem is runtime correctness, not client API composition across many teams. REST/SSE is enough. |
| Rust/C++ hot-path rewrite | Nimbus is provider-latency and model-latency bound. Use Python with good data structures first. |
| eBPF/noisy-neighbor systems | Valuable at large fleet scale. Not useful before we have real production traffic. |
| ML-optimized Bloom filters/sketches | Useful for massive analytics, not for early session/action state. |
| Multi-region data residency implementation | Design for tenant region placement, but do not implement until customers require it. |
| Custom Python fork/lazy imports | Adopt explicit lazy provider loading; do not fork Python. |
| Lock-free queues | Use bounded asyncio/worker queues now. Lock-free structures are for measured microsecond paths. |

## Architecture 3.0

```text
Clients
  Slack
  CLI
  Web
  MCP
        |
        v
Session Edge
  auth
  rate limit
  WebSocket/SSE
  reconnect
  heartbeats
  presence
        |
        v
Session Authority
  validates operations
  assigns sequence numbers
  appends session events
  applies policy
  creates actions
  emits projections
        |
        +-------------------+
        |                   |
        v                   v
Action Runtime        Event/Artifact Runtime
  ledger                WAL-style event log
  executor              artifact manifests
  verifier              replay/projections
  reconciler            analytics/export
        |
        v
Providers
  OpenRouter
  S3
  future GCS/Drive/Dropbox
```

The local implementation can remain one process:

```text
FastAPI app
  ai_server routes
  nimbus_runtime kernel
  file/SQLite stores
  inline executor
```

The scale implementation can split by the same boundaries:

```text
API/session edge fleet
Session authority workers
Postgres action/event store
Valkey hot state
Queue action executor
Artifact object storage
Provider adapters
```

## Cell Model For Blast Radius

Roblox's cell model is useful conceptually even before we run many machines.
For Nimbus, a cell is a failure boundary for sessions and tenants.

```text
Cell
  session authorities
  local hot cache
  worker pool
  provider concurrency budget
  event stream partition
```

Cell goals:

- one hot tenant should not starve all tenants
- one failing provider path should not poison all sessions
- one broken runtime spec rollout should affect a bounded cohort
- one queue backlog should not block confirmations for everyone

Early implementation:

- add `tenant_id` and `session_id` to every rate-limit and idempotency key
- add operation priority classes
- add per-tenant/provider concurrency caps
- document cell placement as a future scaling boundary

Later implementation:

- route tenants/sessions to cells
- run multiple authority workers
- keep reads local to cell when possible
- replicate durable state through Postgres/queue infrastructure

## Event Schemas And Ownership

Roblox's analytics ingestion story applies directly once sessions become event
streams. The problem is not just volume. It is ownership, retention, schema
evolution, and downstream usability.

Nimbus event schemas should include:

```text
event_type
schema_version
owner
retention_class
privacy_class
compatibility_mode
payload_schema
```

Example:

```python
@dataclass(frozen=True, slots=True)
class EventSchema:
    event_type: str
    version: int
    owner: str
    retention_days: int
    privacy_class: Literal["public", "tenant", "sensitive", "secret"]
    compatibility: Literal["backward", "backward_transitive"]
```

Rules:

- adding optional fields is safe
- removing fields requires a deprecation window
- event names are stable
- payloads do not contain secrets or large artifacts
- large payloads become artifacts
- every event has an owner

Do not add protobuf yet. But design the schema discipline so protobuf can
replace or complement Pydantic/dataclass schemas later.

## Write-Ahead Event Log

Netflix's WAL work reinforces the `SessionEventStore` design. The event log is
not just a UI feed. It is the write-ahead record for every important state
transition.

Principle:

```text
Append durable event first.
Then update projections, caches, clients, and derived metrics.
```

For actions:

```text
operation_received
action_proposed
confirmation_required
action_authorized
action_started
provider_call_started
provider_call_ambiguous
verification_started
verification_passed
action_completed
```

If a process crashes after the event append but before broadcast, clients catch
up by sequence. If projection state is corrupt, rebuild it from events.

Event record contract:

- `sequence` is assigned by the Session Authority, never by the client
- `(tenant_id, session_id, sequence)` is unique and monotonically increasing
- `event_id` is globally unique enough for tracing and cross-store references
- `schema_version` controls payload decoding and migration
- `idempotency_key` links duplicate operations to one committed result
- payloads are bounded; large bodies live in immutable artifacts
- corrupt, malformed, or unknown-major-version records fail closed

The first store interface should stay narrow:

```text
SessionEventLog
  append(event_create) -> SessionEvent
  read_after(session_id, sequence, limit) -> EventPage
  read_all(session_id) -> Iterable[SessionEvent]
  checkpoint(session_id, projection_digest) -> CheckpointRef
  compact(session_id, through_sequence) -> CompactionResult
```

Record framing should validate what is actually stored, not what a caller says
is stored:

- magic/version prefix for log files or SQLite rows
- schema version
- payload length checked against actual bytes
- content digest for artifact references
- created-at timestamp plus monotonic sequence
- explicit migration path for old records

## Action Transaction Boundary

Action records should look like transaction records, not chat transcript
fragments:

```text
Action
  tenant_id
  session_id
  action_id
  action_kind
  actor_id
  target_ref
  target_digest
  policy_decision_id
  confirmation_id
  idempotency_key
  status
  attempt
  created_at
  expires_at
```

External side effects must be idempotent from Nimbus' point of view:

- retries reuse the same `action_id` and `idempotency_key`
- duplicate provider callbacks map to the same terminal transition
- authorization expires before execution if the target digest changes
- verification writes an artifact before the action becomes visible as
  `succeeded`
- failed retryable attempts record enough detail for a later worker to resume
  safely

This gives us database-style transaction boundaries without pretending the S3,
OpenRouter, Slack, and GitHub calls are one distributed ACID transaction.

## Projection Rule

> If a projection is wrong, rebuild it from the event log.

This keeps the system honest. The session log owns truth. Everything else is a
cache, index, view, or convenience:

- Slack thread messages are a projection
- web timeline state is a projection
- "latest action status" is a projection
- cost and usage stats are projections
- search indexes and future analytics are projections
- support/debug timelines are projections

Some projections can be stale. Some can be rebuilt asynchronously. The
destructive action ledger and event log cannot be stale relative to the
authority that executes the action.

## Consistency Choices

Use the weakest consistency that preserves the product contract.

| Workflow | Required behavior | Consistency choice |
| --- | --- | --- |
| Destructive confirmation | Only the verified actor can authorize the exact target before expiry. | Strong consistency inside the Session Authority/action store. |
| Action transition | No skipped, duplicated, or regressed states. | Compare-and-set transition with one committed event. |
| Session event order | Every client eventually agrees on what happened in one session. | Linear per-session sequence; no global order required. |
| Reconnect catch-up | A client that missed events can recover exactly. | Read-after-sequence from durable event log. |
| Token streaming | Useful live progress, but not source of truth. | Best-effort stream; final message/event is durable. |
| Presence | Who is viewing or typing now. | Ephemeral eventual consistency. |
| Metrics and usage | Trend accuracy over operational decisions. | Derived projection with delayed aggregation. |
| Cross-session analytics | Product insights and support queries. | Batch/stream derived data; never on the critical action path. |

This avoids the trap of demanding global consistency everywhere, then
discovering the product only needed strong ordering around a few safety-critical
transitions.

## Operation Priorities And Load Shedding

Netflix's prioritized load shedding adds a missing overload model.

Nimbus operation priorities:

| Priority | Examples | Overload behavior |
| --- | --- | --- |
| P0 safety/control | cancel action, revoke session, auth failure recording | never shed unless service is unavailable |
| P1 confirmation | authorize/cancel destructive action, get action status | preserve as long as possible |
| P2 cheap read | list action status, fetch recent events | shed after P3/P4 |
| P3 AI work | model-backed prompt, summarization | degrade or queue |
| P4 bulk/background | child sessions, large scans, large uploads | shed or delay first |

This avoids treating "start a 500-file cleanup scan" the same as "cancel this
delete."

Low-level design:

```python
class OperationPriority(StrEnum):
    SAFETY = "safety"
    CONFIRMATION = "confirmation"
    READ = "read"
    AI = "ai"
    BULK = "bulk"
```

Every operation gets:

```text
priority
tenant_id
cost_estimate
deadline
idempotency_key
```

The rate limiter and executor should use priority when overloaded.

## Trust And Safety Feedback Loop

Roblox moderation and Block red-team posts point to the same lesson: AI safety
is a continuous system, not a one-time prompt.

Nimbus should maintain:

- golden safety set: known prompt-injection and policy cases
- adversarial set: generated red-team prompts and malicious object names
- uncertainty set: ambiguous cases where policy/model disagreed
- incident set: real production issues and support cases

For every model/runtime change:

```text
run golden set
run adversarial set
run DST scenarios
compare action decisions
fail if deterministic policy changed unexpectedly
```

Human review stays in the loop for:

- unclear ownership
- large destructive actions
- cross-tenant anomalies
- policy changes
- appeals or support review

This is not "moderation" in the consumer-social sense. It is operational safety
for delegated file actions.

## MCP And Agent Tool Design

Block's MCP playbook conflicts with raw CRUD tool exposure. It recommends
workflow-first tools. That matches our existing safety direction.

Good Nimbus MCP tools:

```text
start_cleanup_plan(prefix)
stage_attachment_upload(files, destination_prefix)
explain_action(action_id)
confirm_action(action_id, token)
list_recent_session_events(session_id)
create_support_bundle(session_id)
```

Bad Nimbus MCP tools:

```text
raw_s3_delete(bucket, key)
raw_get_session_json(session_id)
raw_write_event(payload)
raw_provider_call(method, args)
```

Tool rules:

- high-level workflow tools over raw APIs
- bounded output
- explicit recovery instructions on errors
- pagination for large output
- artifact references instead of huge JSON
- policy outside the model
- token-budget checks before returning data

## Agent Skills And Deterministic Boundaries

Block's skill design principle is crucial: decide what the agent should not
decide.

Nimbus deterministic zone:

- policy decisions
- action status transitions
- confirmation matching
- scoring/risk thresholds
- event sequence assignment
- idempotency behavior
- provider capability checks
- verification pass/fail criteria

Agent reasoning zone:

- explaining failures
- proposing cleanup plans
- summarizing artifacts
- suggesting next actions
- clarifying ambiguous user intent
- drafting human-readable reports

This split should be explicit in prompts, tool descriptions, and code.

## Time Semantics

Rippling and Riot both highlight that time is a real design problem.

Nimbus needs three clocks:

```text
WallClock
  timestamps shown to users, expiry timestamps, audit records

MonotonicClock
  latency, timeout, rate-limit refill

SimulationClock
  deterministic tests and replay scenarios
```

Low-level interface:

```python
class Clock(Protocol):
    def wall_now(self) -> datetime:
        """Return current wall-clock time."""

    def monotonic(self) -> float:
        """Return monotonic time for durations."""
```

Rules:

- never use wall time for latency or token-bucket math
- never use monotonic time for user-visible expiry strings
- DST uses `SimulationClock`
- expiration decisions use server/store time, not client time
- client clocks are untrusted

## Test Pre-Seeding And DST Speed

Rippling's PreSeeding points to a practical testing upgrade. Our DST harness
will need non-trivial setup: fake tenants, actors, sessions, stores, objects,
and provider scripts.

Use pre-seeded fixtures:

```text
seed_basic_tenant
seed_slack_session
seed_storage_bucket
seed_duplicate_files
seed_pending_delete
seed_large_artifact_manifest
```

Store them as deterministic fixture builders or SQLite snapshots once we have
SQLite stores. The goal is fast local feedback:

```text
DST scenario startup should be milliseconds, not seconds.
```

Do not add magical snapshot state before the store interfaces are stable.

## Python Process Lifecycle

Rippling's pre-fork work is a warning and an opportunity.

Opportunity:

- lazy imports and preloaded immutable runtime config can reduce startup cost
- pre-fork workers can share memory if we later run Gunicorn/Uvicorn workers
- periodic worker restarts can mitigate slow memory growth

Warning:

- global provider clients opened before fork can break copy-on-write and leak
  sockets
- background threads before fork are dangerous
- import side effects make startup order fragile

Guardrail:

```text
Provider clients must be lazy.
No network sockets during import.
No background threads during import.
RuntimeSpec loading must be explicit.
```

If we later use pre-fork:

- add a pre-fork guard checking active sockets/threads
- initialize clients after fork
- consider `max_requests` restarts
- measure RSS before and after

## System-Programming Hot Path Rules

The C++/HFT material should not make us rewrite Nimbus in C++. It should shape
how we think.

Hot paths:

- session event append
- event projection replay
- reconnect catch-up
- operation rate limiting
- idempotency lookup
- action transition
- artifact manifest diff
- WebSocket/SSE fanout

Rules:

- measure before optimizing
- optimize data structure before language
- avoid unbounded queues
- avoid per-event large allocations where easy
- keep event payloads small
- batch event writes/fanout where safe
- separate hot state from cold artifacts
- prefer arrays/lists for ordered event replay
- keep maps keyed by typed IDs
- do not hold locks across provider calls
- use backpressure instead of memory growth
- track p50/p95/p99, not only averages

Python can handle the early product if we keep the hot path simple and bounded.

## Analytics And Metrics

Roblox's analytics pipeline says event ownership and schema matter. Netflix and
HRT say cardinality discipline matters.

Derived metrics should come from session/action events:

```text
operation_started_total{kind, priority, tenant_tier}
operation_shed_total{kind, priority, reason}
event_publish_latency_ms{client_type}
session_reconnect_gap_events
action_transition_total{kind, from, to}
verification_failed_total{kind, reason}
provider_latency_ms{provider, operation}
provider_ambiguous_total{provider, operation}
artifact_size_bytes{kind}
```

Avoid labels:

- raw session ID
- raw user ID
- raw object key
- prompt text
- error messages with secrets

High-cardinality debugging belongs in traces/events, not metric labels.

## Data Residency And Tenant Placement

Rippling's multiregion work is relevant later because Nimbus handles business
files and audit trails.

Design now:

```text
Tenant has home_region.
Session and action durable state belongs to home_region.
Artifacts belong to home_region unless policy permits otherwise.
Provider calls respect tenant storage policy.
```

Do not implement multiregion today. Add the fields and avoid assumptions that
all tenants live in one global namespace forever.

## Durable Execution: Design Conflict

This is the main unresolved design choice.

### Option A: In-House Action Ledger First

Pros:

- smallest correct primitive
- easy to test locally
- fits current codebase
- no new infrastructure
- teaches the runtime model directly
- enough for deletes, uploads, confirmations, and short workflows

Cons:

- we own retry and timer semantics
- long-running workflows become harder
- worker crash recovery must be designed carefully
- visibility tooling must be built

### Option B: Temporal Earlier

Pros:

- durable timers and retries
- human-in-the-loop workflows map naturally
- crash recovery is built in
- workflow history is inspectable
- strong fit for long-running agent sessions

Cons:

- new infrastructure and mental model
- deterministic workflow constraints
- more operational surface
- overkill before action semantics are stable
- can hide domain design problems behind workflow plumbing

Recommendation:

```text
Use in-house ActionStore and SessionEventStore now.
Design them so Temporal can later become the ActionExecutor/workflow backend.
Revisit Temporal when workflows exceed a single request/short worker lifecycle
or require durable sleeps, multi-day approvals, or complex retries.
```

User decision needed later: whether Nimbus should adopt Temporal before
Postgres/worker scale or after the action semantics are proven.

## API Style: Design Conflict

### Option A: REST + SSE/WebSocket Events

Pros:

- simple
- matches current FastAPI app
- easy for Slack/CLI
- event log maps directly to reconnect
- easy to test

Cons:

- clients may overfetch for rich web views
- many endpoint shapes as product grows

### Option B: GraphQL

Pros:

- good for rich web clients
- lets clients request exact projections
- supports evolving UI composition

Cons:

- premature for current repo
- adds resolver complexity
- does not solve action correctness
- can obscure event/state-machine semantics

Recommendation:

```text
Use REST for commands and SSE/WebSocket for events.
Consider GraphQL only for the future web support console after the session
event model is stable.
```

## Schema Format: Design Conflict

### Option A: Pydantic/Dataclasses Now

Pros:

- native Python
- fast to evolve
- good docs and validation
- matches current codebase

Cons:

- weaker cross-language guarantees
- schema compatibility is social unless enforced

### Option B: Protobuf Now

Pros:

- strong schema compatibility discipline
- cross-language ready
- compact event representation
- good for analytics/event pipelines

Cons:

- code generation overhead
- more tooling complexity
- premature before event shapes stabilize

Recommendation:

```text
Use Pydantic/dataclasses now with explicit schema_version and compatibility
rules. Add protobuf when TypeScript/web/MCP/generated clients or analytics
pipelines need stable cross-language contracts.
```

## Store Graduation Contract

The store abstraction should preserve these invariants at every stage:

- append event durably before publishing it
- allocate per-session sequence numbers atomically
- enforce action compare-and-set transitions
- make idempotency lookup and action creation one atomic decision
- read events after a sequence for reconnect and replay
- rebuild projections from durable events
- checkpoint and compact without losing audit history required by policy
- reject malformed records instead of guessing

Stage 0 JSON files can be acceptable if they are less toy-like than plain
transcript dumps:

- write temp file then rename
- include schema version and payload digest
- bound file sizes by session and artifact policy
- keep large artifacts out of event payloads
- add a replay command that validates every record

Stage 1 SQLite should use one transaction for idempotency check, event append,
and action transition when they belong to the same operation.

Suggested tables:

```text
sessions(session_id, tenant_id, runtime_spec_version, created_at, status)
events(session_id, sequence, event_id, schema_version, event_type, payload_json, created_at)
actions(action_id, session_id, tenant_id, status, idempotency_key, target_digest, attempt)
artifacts(artifact_id, session_id, kind, schema_version, digest, uri, size_bytes)
idempotency(scope, key, committed_event_id, created_at, expires_at)
```

Stage 2 Postgres should partition first by tenant/session when data volume
demands it. Avoid global secondary indexes until a real query requires them;
projections and analytics can usually be derived from event streams.

## Store Choice: Design Conflict

### Option A: File/SQLite First

Pros:

- simple local development
- no external dependency
- easy DST
- good for single-process deployment
- SQLite gives transactions when JSON gets awkward

Cons:

- no multi-process shared state
- manual migration path
- limited operational tooling

### Option B: Postgres First

Pros:

- production-shared durability
- real transactions and indexes
- easier future workers
- better support/admin queries

Cons:

- infra cost and setup
- more migration overhead
- can slow early iteration

Recommendation:

```text
Keep file-backed current state for immediate slices.
Move to SQLite for local event/action store when event volume grows.
Move to Postgres when multiple processes or shared workers are introduced.
```

## Next Implementation Slices

The 3.0 roadmap, smallest useful order:

1. **Fix lint and keep docs green.**
   No architecture ambition matters if the repo cannot pass basic checks.

2. **Add runtime invariants doc.**
   Make tenant isolation, authorization, idempotency, replay, and boundedness
   explicit.

3. **Add `Clock` protocol.**
   Replace direct time calls in new runtime/session code first.

4. **Add typed IDs.**
   Introduce strong ID types in `nimbus_runtime` kernel modules.

5. **Add `SessionEvent` and event schema metadata.**
   Include `schema_version`, owner, retention, and privacy class.

6. **Add file-backed `SessionEventStore`.**
   Ordered append, list after sequence, durable-before-visible publish, and
   replay support.

7. **Add write-ahead record validation.**
   Validate schema version, payload length, content digest references, and
   unknown-version failure behavior.

8. **Add `SessionProjection`.**
   Rebuild session state from events and compare live vs replay.

9. **Add `OperationEnvelope`.**
   Internal protocol first: `CALL`, `RESPONSE`, `COMPLETE`, `ERROR`,
   `CANCEL`.

10. **Move delete flow to events/actions.**
   Replace bespoke pending delete state.

11. **Add idempotency records.**
    Make duplicate Slack events, HTTP retries, and worker retries converge on
    one committed result.

12. **Add first DST scenarios.**
    Duplicate request, wrong actor confirmation, provider timeout after
    delete, replay equality.

13. **Add events endpoint.**
    `GET /ai/sessions/{id}/events?after=N`.

14. **Add load-shedding policy.**
    Operation priority and bounded per-session/tenant queues.

15. **Move attachment upload to action/artifact model.**
    Verification report becomes an artifact.

16. **Add schema evolution checks.**
    Verify old event/action/artifact records still decode, and unknown major
    versions fail closed.

17. **Pre-seed DST fixtures.**
    Keep local failure simulation fast.

## Final Guardrail

Nimbus 3.0 should not become "all the architecture we have ever admired."

The system should remain explainable in seven lines:

```text
Clients submit operations.
Session authority appends events.
Actions model side effects.
Policy authorizes actions.
Executors do provider work.
Verifiers produce artifacts.
DST proves invariants.
```

Every new tool or technology must strengthen one of those lines.

## References

- [Roblox: Making infrastructure efficient and resilient](https://about.roblox.com/newsroom/2023/12/making-robloxs-infrastructure-efficient-resilient)
- [Roblox: Delivering large-scale platform reliability](https://about.roblox.com/newsroom/2022/04/delivering-large-scale-platform-reliability)
- [Roblox: Path to 2 trillion analytics events a day](https://about.roblox.com/newsroom/2025/06/roblox-path-to-2-trillion-analytics-events-a-day)
- [Roblox: AI moderation at massive scale](https://about.roblox.com/newsroom/2025/07/roblox-ai-moderation-massive-scale)
- [Database Internals](https://www.databass.dev/)
- [Designing Data-Intensive Applications, 2nd Edition](https://www.oreilly.com/library/view/designing-data-intensive-applications/9781098119058/)
- [Netflix: Temporal powers reliable cloud operations](https://netflixtechblog.com/how-temporal-powers-reliable-cloud-operations-at-netflix-73c69ccb5953)
- [Netflix: resilient data platform with write-ahead log](https://netflixtechblog.com/building-a-resilient-data-platform-with-write-ahead-log-at-netflix-127b6712359a)
- [Netflix: service-level prioritized load shedding](https://netflixtechblog.com/enhancing-netflix-reliability-with-service-level-prioritized-load-shedding-e735e6ce8f7d)
- [Netflix: WebSocket proxy evolution](https://netflixtechblog.com/pushy-to-the-limit-evolving-netflixs-websocket-proxy-for-the-future-b468bc0ff658)
- [Block: red-teaming an AI agent](https://engineering.block.xyz/blog/how-we-red-teamed-our-own-ai-agent-)
- [Block: MCP server playbook](https://engineering.block.xyz/blog/blocks-playbook-for-designing-mcp-servers)
- [Block: agent skills principles](https://engineering.block.xyz/blog/3-principles-for-designing-agent-skills)
- [Block: building for resilience](https://engineering.block.xyz/blog/building-for-resilience)
- [Rippling: PreSeeding tests](https://www.rippling.com/blog/preseeding-faster-way-to-run-tests)
- [Rippling: suspend-unaware monotonic time](https://www.rippling.com/blog/rust-suspend-time)
- [Rippling: multiregion data residency](https://www.rippling.com/blog/multiregion-data-residency-qcon-alex-strachan)
- [Rippling: Gunicorn pre-fork journey](https://www.rippling.com/blog/rippling-gunicorn-pre-fork-journey-memory-savings-and-cost-reduction)
- [CppCon: Efficiency with algorithms, performance with data structures](https://www.youtube.com/watch?v=fHNmRkzxHWs)
- [CppCon: When a microsecond is an eternity](https://www.youtube.com/watch?v=NH1Tta7purM)
- [CppCon: C++ atomics, from basic to advanced](https://www.youtube.com/watch?v=ZQFzMfHIxng)
