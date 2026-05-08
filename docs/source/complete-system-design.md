# Complete System Design

```{admonition} Canonical Design Source
:class: important

This page is the canonical system design for Nimbus. It supersedes the three
earlier Nimbus agent-platform design drafts and consolidates their useful
lessons into one reviewable design: contracts, state ownership, invariants,
failure handling, observability, scale triggers, and implementation order.
```

Nimbus is a Python 3.12+ workspace for provider-agnostic cloud storage and a
cloud-storage-aware AI runtime. The project is not trying to look like a large
company by copying large-company infrastructure. The goal is stronger: build the
smallest production-credible system whose primitives teach software engineering
at scale.

The system should be explainable in seven lines:

```text
Clients submit storage requests or AI runtime turns.
Contracts define the public behavior.
Adapters translate transports without owning domain semantics.
The runtime turns user intent into authorized actions.
Actions model side effects.
Events and artifacts prove what happened.
Tests and telemetry keep the invariants honest.
```

Everything else in the design exists to strengthen one of those lines.

## Working System Model

Goal
: Nimbus should be a technically deep class project that still fits in one
  student-owned repository: provider-neutral storage, a concrete S3
  implementation, an HTTP service and generated adapter, a provider-neutral AI
  client contract, an OpenRouter implementation, and a shared runtime that can
  safely expose storage capabilities to chat-style interfaces.

Public contracts
: The public surface is larger than function signatures. It includes
  `CloudStorageClient`, storage HTTP routes, generated client behavior,
  `AIClient`, `Conversation`, `Tool`, `AIResponse`, the Nimbus wrapper request
  and response schema, environment variables, CLI behavior, persisted state
  shape, error messages, ordering guarantees, and documented operational
  assumptions.

Core invariants
: Tenant-scoped state never crosses tenants. Destructive work executes only
  after exact authorization. Duplicate logical requests converge on one result
  or one durable action state. Action transitions are monotonic. Success is not
  reported before the result and evidence are durable. Boundary data is
  validated against the bytes or structured payload actually received. Unknown
  auth, unknown policy, malformed records, and expired confirmations fail
  closed.

Failure modes
: Large listings, large uploads/downloads, duplicate delivery, provider
  timeouts, provider 429s, ambiguous storage outcomes, corrupt fallback state,
  process restart, Postgres outage, stale schema, disk pressure, thread-pool
  saturation, stale credentials, and accidental horizontal scaling.

Dependencies
: AWS S3 through boto3, the external `cloud_storage_api` package, generated
  OpenAPI client code, OpenRouter through the OpenAI-compatible SDK and
  pydantic-ai, FastAPI/Starlette, Render, Render Postgres, local JSON/SQLite
  fallback state under `AI_SESSION_DIR`, CircleCI, New Relic, Sentry,
  OpenTelemetry, LaunchDarkly, and Sphinx/MyST docs.

Verification plan
: Unit tests protect contracts and small invariants. Integration tests protect
  package wiring and service/adapter behavior. Property and fuzz tests protect
  parsers, state decoding, rate limits, and replay keys. BDD tests protect
  wrapper flows. End-to-end tests prove deployment shape. The next reliability
  frontier is deterministic simulation testing for duplicate delivery, crashes,
  ambiguous provider outcomes, replay, and overload.

## Executive Thesis

Nimbus has a strong package architecture and an incomplete production semantics
model.

The strong part is the dependency direction:

```text
cloud_storage_api
  -> aws_client_impl
  -> aws_client_service
  -> generated OpenAPI client
  -> aws_client_adapter

ai_client_api
  -> openrouter_ai_client_impl
  -> nimbus_runtime
  -> ai_server
```

The weak part is not "missing Kubernetes" or "missing Redis." The weak part is
that several clean abstractions still hide unbounded work or single-process
assumptions:

- `list_files` returns all matching objects rather than a bounded page.
- Downloads and uploads still put too much data-plane pressure on the app
  process.
- The wrapper idempotency key is closer to a response replay cache than a
  durable in-flight execution claim.
- Session locks, rate-limit buckets, and parts of request state are local to one
  process.
- Runtime actions and artifacts are now durable, but conversation turns,
  model-call events, and idempotent turn claims are not yet fully unified under
  the event/action kernel.
- Observability exists, but it does not yet give an operator a decisive answer
  about readiness, dependency health, saturation, or duplicate execution.

The right next move is to harden the existing modular monolith. Add explicit
limits, stronger local durability, better invariants, and better verification
before introducing heavier infrastructure.

## Product And Non-Goals

Nimbus has three product layers.

| Layer | User | Promise |
| --- | --- | --- |
| Storage library | Python caller | Store, inspect, list, download, and delete objects through a provider-neutral interface. |
| Storage service | HTTP caller or generated adapter | Use the same storage contract over a transport boundary. |
| AI runtime | CLI or chat wrapper | Ask natural-language questions about storage and perform guarded actions with confirmation and audit evidence. |

This project should be resume-worthy because its internal design is serious, not
because it pretends to be an enterprise platform. The project should demonstrate
adapter boundaries, failure translation, idempotency, event/action state,
observability, testing discipline, and scale-up triggers.

Non-goals for the next implementation slice:

- no Kubernetes migration;
- no microservice split before package boundaries are mature;
- no Temporal until workflows need durable sleeps, multi-day approvals, or
  complex retry graphs;
- no Kafka for local ordered session events;
- no Redis/Valkey until cross-process hot coordination exists;
- no GraphQL until a support console needs complex read projections;
- no protobuf until cross-language contracts or generated clients justify it;
- no vector database before exact metadata search and artifacts are exhausted;
- no autonomous destructive workflows.

## Architecture

The current deployment is a modular monolith: one FastAPI process can serve the
storage API, mount the AI router under `/ai`, and serve built docs under
`/guide/`. Render deployments store runtime state in Render Postgres; local
development can fall back to `AI_SESSION_DIR` files and SQLite.

```text
                    +-----------------------------+
                    | Python callers / adapters   |
                    +--------------+--------------+
                                   |
                          cloud_storage_api
                                   |
                      +------------+------------+
                      |                         |
               aws_client_impl          aws_client_adapter
                      |                         |
                    boto3              generated OpenAPI client
                      |                         |
                    AWS S3 <--- aws_client_service FastAPI


 chat wrapper / CLI
        |
        v
  ai_server / CLI edge
        |
        v
  nimbus_runtime
        |
        +--> ai_client_api -> openrouter_ai_client_impl -> OpenRouter
        |
        +--> cloud_storage_api -> storage implementation or adapter
```

The design rule is:

```text
Contracts face inward.
Provider SDKs and transports live outward.
The runtime owns product semantics.
Adapters stay thin.
```

## Package Responsibilities

| Package | Owns | Must not own |
| --- | --- | --- |
| `cloud_storage_api` | Provider-neutral storage contract, domain types, domain exceptions | Provider SDK behavior |
| `aws_client_impl` | boto3-backed S3 implementation, multipart upload, OAuth/token helpers | HTTP routing or generated-client behavior |
| `aws_client_service` | FastAPI storage endpoints, auth dependencies, docs mount, AI router mount | Direct construction of `S3Client` outside `get_client_impl()` |
| `aws_s3_cloud_storage_service_client` | Generated OpenAPI client | Hand edits |
| `aws_client_adapter` | `CloudStorageClient` over HTTP through the generated client | Service internals |
| `ai_client_api` | Provider-neutral AI contract, conversation, tools, responses, AI errors | Provider SDK imports |
| `openrouter_ai_client_impl` | OpenRouter implementation, pydantic-ai loop, CLI, storage tool bindings | Provider-neutral contract definitions |
| `nimbus_runtime` | Session orchestration, safe actions, confirmation flows, artifacts, telemetry | FastAPI request parsing or Slack-specific logic |
| `ai_server` | HTTP wrapper boundary, HMAC auth, request validation, idempotency, rate limiting, session endpoints | Runtime business rules |

This split is the project's strongest architectural asset. Preserve it.

## Runtime Kernel

The AI/runtime side should be understood as a small kernel, not as a chat route.

Core nouns:

| Noun | Meaning |
| --- | --- |
| Tenant | Isolation boundary for state, rate limits, idempotency, policy, and audit. |
| Actor | Verified human or service principal that caused work. |
| Session | Ordered container for prompts, model responses, actions, events, and artifacts. |
| Operation | Client request to change or observe runtime state. Currently implicit; target is an internal envelope. |
| Event | Durable fact appended by the runtime. Events are replayable and ordered per session. |
| Action | Durable unit of side-effecting work with a state machine. |
| Artifact | Evidence or work product produced by an action, verification step, or model summary. |
| PolicyDecision | Deterministic authorization result. The model may propose; policy decides. |
| RuntimeSpec | Versioned snapshot of runtime behavior: model policy, tools, limits, confirmation policy, prompt version, and feature flags. |

The runtime kernel rule:

```text
Model proposes.
Runtime authorizes.
Action store records.
Executor performs.
Verifier proves.
Event log tells the story.
```

Today, the runtime already has serious pieces of this kernel: tenant and actor
types, action status transitions, SQLite-backed action/event/artifact stores,
delete confirmation, upload artifacts, and wrapper response summaries.

The target is to complete the kernel by making every wrapper turn and every
important state transition visible through durable events, idempotency claims,
and replayable projections.

## Public Contracts

### Storage Contract

`CloudStorageClient` is intentionally provider-neutral. It is the right external
boundary for callers that should not care whether storage is S3, GCS, Dropbox,
or another backend.

The design tension is that the current contract is eager:

- `list_files(container, prefix)` returns a full list;
- `download_file(container, object_name, file_name)` writes a full local file;
- `upload_file` and `upload_obj` do not expose a policy object for byte limits,
  timeout budgets, idempotency keys, or integrity requirements.

This is acceptable for HW-sized objects. It becomes the first scale cliff for
large prefixes and large files. The future contract needs pagination and a
streaming or pre-signed URL path without breaking existing callers.

### Storage HTTP Contract

The service correctly keeps HTTP behavior thin over the storage contract. It
maps domain errors into HTTP status codes and lets the generated client and
adapter rebuild Python domain objects.

The missing production shape:

- machine-readable error codes, not prose parsing;
- service-level upload/download byte caps;
- paginated list responses;
- explicit timeouts;
- readiness checks beyond liveness;
- a data-plane strategy for large objects.

### AI Contract

`AIClient` is the right boundary. It keeps model-provider code out of the
provider-neutral API and makes OpenRouter replaceable.

The next design concept is a model execution policy:

- prompt version;
- model and fallback model;
- timeout budget across primary plus fallback;
- max tool steps;
- retry and fallback eligibility;
- per-tenant cost budget;
- tool safety policy;
- evaluation set version.

This policy should be recorded with model calls and action artifacts so a future
reviewer can explain why the model was allowed to do what it did.

### Wrapper Contract

`POST /ai/chat/turn` is the canonical boundary for chat frontends. The wrapper
normalizes platform events into a Nimbus request, signs the raw body, and
receives a machine-readable response.

The design is good because the runtime does not ingest raw Slack-shaped payloads
or arbitrary external URLs. It receives normalized fields: platform, workspace,
channel, thread/message, user, text, idempotency key, request ID, and attachment
metadata/bytes.

The main gap is that idempotency needs to be a durable execution claim, not only
a completed-response replay cache.

## Main Workflows

### Storage Library Call

```text
caller
  -> cloud_storage_api.CloudStorageClient
  -> aws_client_impl.S3Client
  -> boto3
  -> AWS S3
```

Expected behavior:

- validate container and object names;
- translate provider failures to domain errors;
- return provider-neutral domain objects;
- avoid leaking boto3 exceptions across the boundary.

### HTTP-Backed Storage Call

```text
caller
  -> aws_client_adapter.CloudStorageServiceAdapter
  -> generated OpenAPI client
  -> aws_client_service
  -> CloudStorageClient
```

Expected behavior:

- preserve the `CloudStorageClient` contract;
- map HTTP status and error codes back to domain exceptions;
- enforce request limits at the HTTP boundary;
- avoid coupling the adapter to service internals.

### Wrapper Chat Turn

```text
wrapper
  -> signed POST /ai/chat/turn
  -> ai_server auth, validation, rate limit, idempotency
  -> nimbus_runtime session lock
  -> direct action path or model path
  -> response plus action/artifact summaries
```

Expected behavior:

- reject unsigned, stale, replayed, malformed, or oversized requests;
- preserve per-session ordering;
- make duplicates converge;
- keep destructive actions out of the model's direct authority.

### Delete Flow

```text
user: delete reports/old.csv
runtime creates delete action awaiting confirmation
response tells user exact expected confirmation
user confirms exact target
runtime validates same actor, same session, same target
action transitions through authorized, executing, verifying, succeeded/failed
artifact records delete evidence
```

This is the right shape. It should evolve from text-pattern confirmation toward
action IDs or UI confirmation payloads, but the state machine is correct.

### Attachment Upload Flow

```text
wrapper sends attachment metadata and bounded inline bytes
runtime validates decoded bytes against declared size and sha256 when provided
runtime creates upload actions
storage client uploads each object
runtime verifies metadata and records upload artifacts
response reports success, partial success, or error
```

This is a good example of boundary validation. The runtime checks the real
decoded payload rather than trusting declared metadata.

## State Ownership

| State | Current owner | Current storage | Current correctness scope | Target |
| --- | --- | --- | --- | --- |
| Conversation history | `nimbus_runtime`, `ai_server.sessions` | JSON files | One process, one volume | Event-backed source of truth plus export/cache |
| Session locks | `nimbus_runtime` | In-process weak dict | One process | Shared lock or single-worker guard until shared state exists |
| Nonce replay state | `ai_server.auth`, `request_state` | Memory plus JSON files | Restart survival, not multi-process atomicity | Durable claim store scoped by signer/tenant |
| Idempotent turn responses | `ai_server.router`, `request_state` | Memory plus JSON files | Completed response replay | Durable turn ledger with in-flight claim |
| Action state | `nimbus_runtime.stores` | SQLite | One local DB file | Same interface, later Postgres if topology requires |
| Session events | `nimbus_runtime.stores` | SQLite | Action/artifact events today | All important runtime state transitions |
| Artifacts | `nimbus_runtime.stores` | SQLite payloads | Small evidence reports | Immutable manifests; large payloads in object storage |
| Rate limits | `ai_server.router` | Process memory | One process | Shared backend only when multiple workers exist |
| OAuth/session tokens | `aws_client_service` | JSON files | Local demo service | Encrypted/expiring token store if used beyond demo |

The central design move is to make SQLite the local runtime coordination
primitive. JSON files should remain useful for human-readable conversation
exports and compatibility, but not for idempotency or action correctness.

## Consistency Model

Nimbus does not need one consistency model for everything.

| Area | Required consistency | Reason |
| --- | --- | --- |
| Destructive action authorization | Strong inside session authority | Wrong actor or stale confirmation is a safety bug. |
| Action state transition | Compare-and-set | Two workers or retries must not both win. |
| Idempotency claim | Unique and durable before execution | Duplicate delivery must not duplicate side effects. |
| Event sequence | Ordered per tenant/session | Replay and client catch-up depend on sequence. |
| Artifacts | Durable before success is visible | Evidence must exist before humans see success. |
| Presence/typing indicators | Ephemeral/eventual | Product polish, not correctness. |
| Metrics | Eventual | Operational signals tolerate delay. |
| Conversation export | Eventually consistent projection | The event log should be the durable truth. |

This is why CRDTs are not the right primitive for actions. Collaboration can be
eventual in the UI, but side effects require authority.

## Data Plane And Control Plane

The app server should eventually stop being the primary path for large object
bytes.

Control-plane work:

- authenticate the caller;
- authorize the actor and target;
- create an action;
- set limits, expiry, and idempotency;
- return a capability or accepted action;
- verify completion;
- record artifacts and events.

Data-plane work:

- move bytes between client and object storage;
- stream large uploads/downloads;
- preserve content length, content type, checksums, and version IDs.

The credible scale-up design is pre-signed upload/download sessions:

```text
client requests upload
Nimbus authorizes and creates action
Nimbus returns short-lived pre-signed URL plus required digest/size policy
client uploads directly to S3
Nimbus verifies metadata/digest/version
Nimbus records artifact and final action state
```

This teaches real systems concepts without adding fake platform complexity:
capability URLs, leases, expiry, idempotency, verification, and ambiguous
outcome recovery.

## Safety And Liveness Invariants

Safety properties:

- A tenant cannot read, mutate, summarize, cache, replay, or receive events for
  another tenant's state.
- Side-effecting work requires a verified actor.
- Destructive actions require exact same-actor, same-target authorization before
  expiry.
- The model cannot grant itself authority, broaden a bucket, bypass policy, or
  mark an action succeeded.
- Boundary data must be validated structurally; `None`, wrong types, mismatched
  sizes, and malformed records are rejected.
- Tool results and object names re-entering the model are treated as untrusted.
- Success is not visible until state and evidence are durable.
- Unknown policy, auth, schema version, or corrupt state fails closed.

Liveness properties:

- A valid non-destructive turn eventually returns a response or explicit error.
- A duplicate idempotent request eventually observes the existing result or
  current action state.
- A retryable action can be reconciled or retried without creating a new logical
  action.
- A session event projection can be rebuilt from durable events.
- Cleanup of expiring state is bounded by active workload, not historical traffic.
- Overload degrades lower-priority work before safety/control work.

## Failure Model

| Failure | Expected behavior |
| --- | --- |
| Duplicate wrapper event | Same idempotency key maps to one turn/action/result or current state. |
| Replayed signed request | Rejected by nonce/freshness state before runtime execution. |
| Process crash after action creation | Action remains visible and resumable. |
| Process crash after side effect before response | Verifier/reconciler determines outcome before claiming success. |
| Storage timeout after delete/upload | Mark outcome ambiguous or retryable; do not report success without verification. |
| OpenRouter 429 storm | Apply fallback only inside a bounded policy; avoid unbounded retries. |
| Huge attachment body | Reject before expensive processing; return 413 or validation error. |
| Corrupt session/event/action record | Quarantine or fail closed; do not silently coerce into valid state. |
| Session volume missing | Readiness fails; side-effecting runtime work should not start. |
| Multiple workers accidentally enabled | Either fail startup or require shared coordination backend. |
| S3 unavailable | Storage operations fail closed with domain/HTTP errors and telemetry. |
| Generated client transport failure | Adapter translates to domain error without leaking transport details. |

The expected result is not that every operation succeeds. The expected result is
that invariants hold, overload is explicit, and humans can inspect the final
state.

## Security Model

Current strengths:

- wrapper requests are signed over method, path, timestamp, nonce, and raw body
  digest;
- freshness and replay checks happen before runtime execution;
- Pydantic request models reject malformed fields;
- storage tools are pinned to a configured container;
- wrapper delete/upload flows are runtime-managed rather than direct model
  authority;
- attachment bytes are bounded and digest-checked when a digest is supplied.

Current limits:

- storage credentials are process-level credentials;
- storage service auth is mostly route access, not per-object authorization;
- OAuth/token storage is not an enterprise-grade secret store;
- tenant policy is coarse;
- prompt injection through object names and tool output is controlled by
  sandboxing/truncation, not eliminated.

Target security additions:

- typed `PolicyDecision` with deny/allow/confirm/admin-review outcomes;
- object/prefix-level policy for runtime actions;
- confirmation tokens or action IDs instead of free-form text as the internal
  confirmation primitive;
- redaction rules for logs, artifacts, and model context;
- audit export from the event/action/artifact store;
- fail-closed behavior for unknown actor, unknown tenant, unknown action, unknown
  schema, or expired confirmation.

## Observability And Operations

HW3 requires request latency, success rate, and failure rate. A serious design
needs those plus enough context to debug saturation and dependency failure.

Current signals:

- structured logs;
- Sentry setup;
- OpenTelemetry setup;
- in-memory runtime telemetry;
- liveness routes;
- deployment smoke checks.

Target signals:

| Signal | Why |
| --- | --- |
| Request latency by route and outcome | Finds p95/p99 regressions. |
| Storage latency by operation and provider | Separates S3 problems from app problems. |
| Model latency by provider/model/fallback path | Explains slow AI turns. |
| Action latency by kind and status | Shows where runtime work stalls. |
| Idempotency hit, conflict, and in-flight rates | Proves duplicate delivery behavior. |
| Verification failure and ambiguous outcome counts | Identifies dangerous storage uncertainty. |
| Rate-limit drops by tenant/user | Shows abuse or misconfigured wrappers. |
| Session volume writable/readiness check | Prevents running without durable state. |
| Queue depth later | Needed only once queue exists. |

Metrics should avoid high-cardinality labels such as raw session IDs, object
names, prompts, and error strings. Use tenant hash or class, route, action kind,
provider, status, model, and coarse error kind.

Readiness should answer:

- Is `AI_SESSION_DIR` present and writable?
- Can the runtime open its SQLite store?
- Are required secrets configured?
- Is storage configured if storage-backed runtime tools are enabled?
- Is OpenRouter configured if model-backed turns are enabled?
- Is the process running in a supported worker topology?

## Performance And Backpressure

The system should define limits before traffic discovers them.

| Operation | Current risk | Target control |
| --- | --- | --- |
| List files | Full prefix loaded into memory | Pagination with max page size and continuation token. |
| Download | Temp file and adapter buffering | Streaming or pre-signed URL. |
| Upload | App-server body pressure | Byte caps, streaming, or pre-signed upload sessions. |
| Chat turn | Model call blocks request lifetime | Timeout budget, cancellation, 202/async strategy when needed. |
| Attachments | Inline base64 memory pressure | Decoded byte caps and future staged upload. |
| Duplicate delivery | Concurrent execution | Durable in-flight idempotency claim. |
| Background work | Hidden unbounded work | Operation priority, bounded queues only when introduced. |

Operation priority classes:

| Priority | Work | Degradation behavior |
| --- | --- | --- |
| P0 | Safety/control: cancel, health, auth failure, action status | Keep available as long as process is alive. |
| P1 | Confirmation and action state transitions | Prefer over model-heavy work. |
| P2 | Cheap metadata reads | Keep available if provider is healthy. |
| P3 | AI/model turns | Throttle during model/provider stress. |
| P4 | Bulk/background summaries/uploads | First to defer or reject under load. |

Backpressure should be explicit: return 429, 413, 503, or 202 with action state.
Do not hide overload in slow queues, unbounded retries, or thread-pool growth.

## Store Graduation Path

Do not add infrastructure because it looks serious. Add it when the access
pattern requires it.

| Stage | Store | Use when | Must preserve |
| --- | --- | --- | --- |
| 0 | JSON files | Human-readable sessions and very small local state | Atomic replace and corruption handling. |
| 1 | SQLite | Single-process durable coordination | Unique idempotency, CAS transitions, ordered events, WAL, replay. |
| 2 | Postgres | Multiple API processes or worker fleet need shared durable state | Transactions, row locks, migrations, backups, observability. |
| 3 | Valkey/Redis | Hot shared coordination and rate limits exceed SQL ergonomics | TTLs, bounded keys, fail-closed side-effect paths. |
| 4 | Queue/workers | Inline execution hurts latency or reliability | At-least-once delivery, idempotent workers, retry budgets. |
| 5 | Temporal/workflow engine | Workflows need durable timers, multi-day approvals, complex retries | Action identity, policy, verification, and audit semantics. |

Queue messages should contain action IDs, not full action payloads. The durable
store remains the source of truth.

## Testing And Reliability Strategy

The current test suite is already stronger than most class projects: unit,
integration, property, BDD, e2e, and fuzz-smoke layers exist. The next step is
to make reliability explicit.

Add a deterministic simulation testing path for the runtime kernel:

```text
SimulatedClock
DeterministicScheduler
SimulatedSessionEventStore
SimulatedActionStore
SimulatedArtifactStore
SimulatedStorageProvider
SimulatedModelProvider
FaultInjector
InvariantChecker
```

Initial scenarios:

- same wrapper turn delivered twice while the first execution is slow;
- confirmation from wrong actor;
- confirmation after expiry;
- storage delete succeeds but response is lost;
- storage upload succeeds but artifact write fails;
- model provider fails after a read-only tool call;
- corrupt event/action record during replay;
- stuck `EXECUTING` action on restart;
- large prefix list is paginated and bounded;
- multi-tenant IDs are mixed maliciously.

Key properties to assert:

- no cross-tenant event/action/artifact access;
- one idempotency key creates at most one logical turn/action;
- destructive action never executes without exact authorization;
- action status never moves backward;
- success is not visible before durable state and evidence;
- replayed projection equals live projection;
- cancelled/expired actions cannot later succeed;
- duplicate worker delivery cannot duplicate side effects.

## Scale Review

At 1 to 10 users, the current system mostly fails through configuration:
missing secrets, missing S3 credentials, OpenRouter rate limits, missing docs
build, missing or unmigrated Postgres.

At 10 to 100 active users, the first real bottlenecks are:

- memory and disk pressure from uploads/downloads;
- unbounded listing;
- duplicate in-flight idempotency;
- repeated provider client construction and connection setup;
- process-local rate limits;
- model fallback amplifying traffic during provider incidents.

At 100 to 10,000 active users, the topology becomes the problem:

- process-local locks no longer serialize sessions;
- local files cannot coordinate nonce/idempotency globally;
- a single volume creates placement constraints;
- observability must be backend-backed and alertable;
- action execution needs queues or workers;
- data-plane traffic must bypass the app server.

The growth path is:

```text
Single modular service with SQLite
  -> API fleet plus Postgres
  -> Valkey for hot rate-limit/coordination state
  -> queue plus worker fleet for long-running actions
  -> region-aware tenant placement only after real residency/latency pressure
```

The bottleneck should become provider cost or provider latency, not missing
runtime foundations.

## Implementation Roadmap

### P0: Keep The Canonical Design Honest

- Keep this page as the only full system design document.
- Keep `AGENTS.md`, `CONTRIBUTING.md`, and docs command references current.
- Keep Sphinx rendering green.
- Keep deleted or superseded design drafts out of the main reader path.

### P1: Correctness And Safety

- Add a durable turn ledger in SQLite with in-flight idempotency claims.
- Move nonce and idempotency coordination out of parallel JSON state where
  practical.
- Add request-body byte limits before signed auth reads full bodies.
- Add readiness checks for session directory, SQLite store, and configured
  dependencies.
- Add machine-readable storage service error codes.
- Fix multipart abort behavior across translated exception paths.
- Decide and document delete idempotency semantics.
- Add corruption quarantine/telemetry for session and request-state files.

### P2: Runtime Kernel Completion

- Add `Clock` abstraction: wall clock, monotonic clock, simulation clock.
- Add `RuntimeSpec` and record its version with turns/actions.
- Add internal `OperationEnvelope` for prompt, authorize, cancel, and attach
  operations.
- Append durable events for prompts, model calls, action transitions, artifacts,
  and verification results.
- Add `GET /ai/sessions/{id}/events?after=N`.
- Add `GET /ai/actions/{id}` and artifact retrieval once stores are authoritative.
- Add `SessionProjection.from_events()` and replay equality tests.

### P3: Storage Data-Plane Hardening

- Add paginated list APIs and adapter support.
- Add upload/download byte limits and 413 behavior.
- Add streaming download or pre-signed download design.
- Add pre-signed upload sessions for large attachments.
- Add artifact manifests with before/after object metadata, size, digest, and
  version IDs when available.

### P4: Reliability And Scale

- Add deterministic simulation testing for duplicate delivery and crash points.
- Add operation priorities and load-shedding rules.
- Add provider circuit-breaker semantics for repeated 429/5xx storms.
- Add reconciliation for stuck `EXECUTING` and ambiguous side-effect outcomes.
- Promote to Postgres only when multiple processes or workers need shared state.
- Introduce queues only when synchronous HTTP execution no longer fits.

## Review Questions

A serious design review should keep asking these until they have concrete
answers:

- What exact operation breaks first under load: listing, download, upload, model
  call, idempotency, session locking, or telemetry?
- What is the maximum accepted request body before auth reads it?
- What is the maximum accepted object size for each storage path?
- What is the page size and continuation-token contract for listing?
- What happens if the provider succeeds but Nimbus crashes before recording
  success?
- What happens if the same idempotency key arrives concurrently?
- What happens if a confirmation arrives after expiry or from another actor?
- What is the difference between retryable failure, terminal failure, and
  ambiguous side effect?
- Which state survives process restart?
- Which state remains correct under multiple workers?
- What does readiness prove?
- Which metrics let an operator distinguish S3 latency from model latency from
  runtime saturation?
- Which tests prove replay, duplicate delivery, and crash recovery?

If those questions are answered in code, docs, and tests, Nimbus becomes much
more than a class project. It becomes a compact example of how real systems are
designed: contracts first, state ownership explicit, failures expected, and
scale introduced only where the access pattern demands it.
