# Nimbus Agent Platform Design

Nimbus should be treated as an agent-first operations platform, not as a chat
wrapper around cloud storage. The product promise is simple:

> A user can start a session anywhere, give Nimbus intent in natural language,
> and trust the agent to gather context, propose safe cloud-file actions,
> verify its work, and leave an audit trail that teammates can inspect or
> continue.

The current repository already has the right foundation: provider-neutral AI
and storage contracts, a concrete OpenRouter implementation, a concrete S3
implementation, a FastAPI HTTP adapter, and `nimbus_runtime` as a shared
runtime. This document describes the long-term system design and the low-level
design changes that let Nimbus grow from a homework integration into a serious
agent platform.

The north star is deliberately small:

```text
Core nouns:
  Tenant, Actor, Session, Action, Object, Artifact

Core verbs:
  Propose, Authorize, Execute, Verify, Observe
```

Everything else is an adapter. Slack, CLI, web, MCP, OpenRouter, S3, Dropbox,
Google Drive, GitHub, and future execution sandboxes should all connect to the
same session/action kernel instead of creating parallel product semantics.

## Product Thesis

Nimbus is Stripe-like infrastructure for AI actions over business files:
natural-language intent at the edge, durable actions at the core, verified
identity and policy in the middle, and auditable execution across storage
providers.

Users should experience Nimbus as a background teammate:

- start a session from Slack, CLI, web, or an MCP-capable client
- ask it to inspect, upload, organize, clean up, summarize, or delete files
- see what it is doing while it works
- approve destructive or expensive actions
- receive proof of completion, not just a confident message
- let another teammate join the same session and continue

The product should feel magical, but the implementation should be boring:
explicit state machines, durable events, bounded queues, idempotent actions,
and narrow interfaces.

## Lessons Applied From Industry

This design intentionally borrows from companies and systems that have already
crossed the boundary from clever prototype to reliable platform.

| Source | Lesson | Nimbus design choice |
| --- | --- | --- |
| Stripe Minions | Agents become useful when they run in the workflow, use the same tools humans use, and produce reviewable work. | Sessions contain prompts, actions, artifacts, checks, and review state; Slack/web/CLI are all clients of the same session. |
| Ramp Inspect | Background agents need full environment, context, verification tools, fast startup, snapshots, and multiplayer clients. | Nimbus sessions are resumable, event-sourced, multiplayer-ready, and centered on verification artifacts. |
| Linear Agent | Put agents inside the work graph instead of making users leave their workflow. | Slack threads, CLI sessions, future web sessions, and MCP clients all attach to one runtime model. |
| Anthropic effective agents | Start with simple workflows and add autonomy only where it earns its cost. | The model proposes; policy and runtime authorize; high-risk actions require confirmation. |
| OpenAI agent platform | Tools, tracing, structured outputs, and evals are product infrastructure. | Tools are schema-first, outcomes are typed, sessions are observable, and agent behaviors get acceptance/eval fixtures. |
| Designing Data-Intensive Applications | Reliability comes from understanding data, state, replication, and failure semantics. | Action state transitions and idempotency are designed before shared stores and queues are introduced. |
| S3 | Durability and correctness are properties of the system under constant failure, not happy-path code. | Nimbus verifies side effects, records durable action events, and red-teams crash/duplicate/ambiguous-provider paths. |
| WhatsApp | Simplicity and operational reliability can beat broad feature surfaces. | Keep the kernel small; add product surfaces only over stable primitives. |
| Uber | Growth forces platform/product separation and backpressure. | `nimbus_runtime` becomes the platform kernel; channel adapters stay thin. |
| Ousterhout | Deep modules with shallow interfaces reduce complexity. | Stores, policy, execution, and verification should expose tiny APIs with strong internal behavior. |
| Linux/Kubernetes/Google/Meta | Large systems survive through maintainership, design docs, reviewable changes, and release discipline. | Add small design docs, narrow diffs, generated contracts when useful, and explicit ownership boundaries. |
| Windsurf/Claude Code/Steve Yegge | Background agents, context hydration, parallel sessions, and agent-native tooling are the new IDE substrate. | Nimbus gets child sessions, context hydration, session tools, and eventual sandbox execution. |

These are not aesthetic inspirations. They are checks against overengineering:
each proposed primitive must solve a known failure mode or unlock a real product
workflow.

## Current Baseline

Today the repository has these important pieces:

```text
cloud_storage_api
  provider-neutral CloudStorageClient contract

aws_client_impl
  boto3-backed S3 implementation

aws_client_service
  FastAPI storage service

aws_client_adapter
  CloudStorageClient over generated HTTP client

ai_client_api
  provider-neutral AIClient, Conversation, Tool, AIResponse

openrouter_ai_client_impl
  OpenRouter-backed AI client, pydantic-ai loop, CLI, storage tools

nimbus_runtime
  transport-neutral chat orchestration, sessions, confirmations,
  attachment handling, telemetry

ai_server
  FastAPI HTTP adapter, HMAC auth, idempotency, rate limiting
```

That shape is good. The main gap is that runtime behavior is still turn-centric
instead of session/action-centric. Deletes and uploads are direct branches in
the runtime. The next foundation should make actions first-class durable
objects.

## Target System Shape

Nimbus should be a modular monolith first. It can be split later along already
explicit boundaries, but a single deployable keeps the early system simple.

```text
                +---------------------+
                |       Clients       |
                | Slack Web CLI MCP   |
                +----------+----------+
                           |
                           v
                +---------------------+
                |     Session API     |
                | auth, idempotency,  |
                | realtime sync, HTTP |
                +----------+----------+
                           |
                           v
                +---------------------+
                |   Nimbus Runtime    |
                | context, planner,   |
                | policy, actions,    |
                | execution, render   |
                +---+------+------+---+
                    |      |      |
       +------------+      |      +-------------+
       v                   v                    v
+-------------+    +---------------+    +----------------+
| Tool Plane  |    | Stores        |    | Providers      |
| storage,    |    | sessions,     |    | OpenRouter,    |
| policy,     |    | actions,      |    | S3, future     |
| verify      |    | artifacts     |    | GCS/Drive/etc  |
+-------------+    +---------------+    +----------------+
```

Future scale can split this logical architecture into an API fleet, worker
fleet, shared stores, and queues. The code should not require that split to
exist today.

## Core Domain Model

### Tenant

A tenant is the isolation boundary. In Slack, a tenant is usually
`platform + workspace_id`. In CLI, it can be a local workspace identity. In an
enterprise deployment, it maps to an organization.

Tenant identity must prefix every durable and cached lookup.

```python
@dataclass(frozen=True, slots=True)
class TenantIdentity:
    platform: str
    workspace_id: str

    @property
    def tenant_id(self) -> str:
        return f"{self.platform}:{self.workspace_id}"
```

### Actor

An actor is a verified human or service principal. Nimbus should not treat a
raw `user_id` string as proof of identity.

```python
@dataclass(frozen=True, slots=True)
class VerifiedActor:
    tenant: TenantIdentity
    user_id: str
    auth_source: Literal[
        "slack_signed_event",
        "cli_local",
        "github_oauth",
        "oidc",
        "service_account",
    ]
    bridge_id: str | None
    verified_at: datetime

    @property
    def principal_key(self) -> str:
        return f"{self.tenant.tenant_id}:{self.user_id}"
```

The current HMAC wrapper auth proves that a trusted wrapper sent the request.
The wrapper must verify the upstream user event, then sign normalized actor
claims for Nimbus. Nimbus trusts only those signed claims.

### Session

A session is the multiplayer container for work. It is not just a conversation
history. It contains prompts, model responses, actions, artifacts, verification
events, and participants.

```text
Session
  session_id
  tenant_id
  title
  status
  participants
  created_at
  updated_at
```

Sessions are the product unit users see in Slack/web/CLI. Actions are the
operational unit the system executes.

### Action

An action is the durable unit of work.

```python
class ActionStatus(StrEnum):
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
```

```python
@dataclass(frozen=True, slots=True)
class Action:
    action_id: str
    tenant: TenantIdentity
    session_id: str
    actor: VerifiedActor
    kind: Literal[
        "list_files",
        "get_file_info",
        "upload_attachment",
        "delete_file",
        "summarize_prefix",
        "spawn_child_session",
    ]
    target: ObjectRef | None
    status: ActionStatus
    idempotency_key: str
    input: Mapping[str, object]
    result: Mapping[str, object] | None
    failure: Mapping[str, object] | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
```

The model can propose an action. Only policy/runtime can authorize it. Only an
executor can execute it. Only verification can mark it proven.

### Object

Object references are stable, tenant-scoped pointers to provider objects.

```python
@dataclass(frozen=True, slots=True)
class ObjectRef:
    provider: Literal["s3", "gcs", "dropbox", "drive"]
    container: str
    object_name: str
    version_id: str | None = None
```

The object container remains pinned by configuration or tenant policy. The
model must not be allowed to choose an arbitrary bucket because of prompt text.

### Artifact

Artifacts are evidence and work products created during a session:

- upload reports
- before/after manifests
- object listings
- verification reports
- screenshots or previews for future web workflows
- action summaries
- child-session reports

```python
@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    tenant: TenantIdentity
    session_id: str
    action_id: str | None
    kind: Literal[
        "manifest",
        "upload_report",
        "delete_report",
        "verification_report",
        "screenshot",
        "model_trace",
    ]
    uri: str | None
    payload: Mapping[str, object] | None
    created_at: datetime
```

Artifacts are how Nimbus proves work to humans and support tooling.

## Session Event Log

The session event log is the system's narrative truth. Every important product
moment should become a durable event.

```text
prompt_added
context_hydrated
model_response_received
action_proposed
confirmation_required
action_authorized
action_queued
action_started
artifact_created
verification_started
verification_passed
verification_failed
action_completed
action_failed
child_session_spawned
participant_joined
participant_left
```

This gives us:

- realtime client sync
- support/debugging
- audit trails
- replayable sessions
- metrics without scraping logs
- eventual worker orchestration
- multiplayer authorship

Low-level interface:

```python
class SessionEventStore(Protocol):
    def append(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        event_type: str,
        actor: VerifiedActor | None,
        payload: Mapping[str, object],
    ) -> SessionEvent:
        """Append one durable event and return it with a sequence number."""

    def list_events(
        self,
        *,
        tenant: TenantIdentity,
        session_id: str,
        after_sequence: int | None = None,
        limit: int = 200,
    ) -> Sequence[SessionEvent]:
        """Return ordered events for session replay or realtime sync."""
```

## Action State Machine

The action state machine is the reliability heart of Nimbus.

```text
                +----------------------+
                |       proposed       |
                +----+------------+----+
                     |            |
         safe action |            | risky/expensive action
                     v            v
              +-----------+   +-----------------------+
              |authorized |   | awaiting_confirmation |
              +-----+-----+   +----+-------------+----+
                    |              |             |
                    |              | confirm     | expire/cancel
                    |              v             v
                    |        +-----------+   +---------+
                    +------> |authorized |   | expired |
                             +-----+-----+   +---------+
                                   |
                                   v
                              +--------+
                              | queued |
                              +---+----+
                                  |
                                  v
                            +-----------+
                            | executing |
                            +-----+-----+
                                  |
                    +-------------+-------------+
                    |                           |
                    v                           v
              +-----------+             +------------------+
              | verifying |             | failed_retryable |
              +-----+-----+             +---------+--------+
                    |                             |
                    v                             v
              +-----------+                   queued
              | succeeded |
              +-----------+
```

Invalid transitions should be rejected in both the domain layer and store
layer. A queue or retry can ask to execute an action, but the store decides
whether the transition is still valid.

CAS-style transition:

```python
class ActionStore(Protocol):
    def transition(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
        expected: ActionStatus,
        next_status: ActionStatus,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> Action | None:
        """Atomically move an action if it is still in the expected status.

        Return the updated action if this caller won the transition, or None if
        another request/worker already moved it.
        """
```

In Postgres this becomes:

```text
UPDATE actions
SET status = $3,
    updated_at = now()
WHERE tenant_id = $1
  AND action_id = $2
  AND status = $4
RETURNING *;
```

That one pattern prevents duplicate workers from executing the same action.

## Policy Model

Policy starts as a pure function. Do not add a policy DSL or OPA before the
rules become complex.

```python
class PolicyDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_ADMIN_APPROVAL = "require_admin_approval"
```

```python
def authorize_action(
    *,
    actor: VerifiedActor,
    action: Action,
    context: PolicyContext,
) -> PolicyDecision:
    """Return the least surprising safe policy decision for an action."""
```

Initial policy rules:

- `list_files` and `get_file_info` are allowed within the pinned container.
- `delete_file` requires exact same-actor confirmation.
- `upload_attachment` requires size, content type, and digest validation.
- model-proposed actions never bypass policy.
- unknown action kinds fail closed.
- cross-tenant object refs fail closed.
- ambiguous object targets require clarification.

Future policy rules:

- workspace admins can approve team-wide destructive actions
- certain prefixes are read-only
- upload size caps vary by tenant plan
- deletes above a risk threshold require two-person approval
- legal hold prevents delete
- service accounts can run scheduled cleanup with restricted prefixes

## Identity And Confirmation

The current signed wrapper route proves the bridge is trusted. For destructive
actions, Nimbus also needs a verified human actor.

```text
Slack signs event
  |
  v
Bridge verifies Slack signature and extracts user/workspace/channel
  |
  v
Bridge signs normalized Nimbus request with actor claims
  |
  v
Nimbus verifies bridge signature
  |
  v
Nimbus binds action authorization to VerifiedActor
```

Confirmation is not text. Text is only one user interface for an authorization.
Internally, confirmation means:

```text
VerifiedActor authorizes exact Action(action_id)
```

The confirmation record should bind:

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

For Slack, the better UX is an interactive button carrying `action_id` and a
short-lived token. Text confirmation can remain for CLI and simple wrappers,
but it must resolve to the same internal authorization transition.

## Context Hydration

Agent quality depends on starting with the right context. Nimbus should hydrate
context deterministically before the model starts guessing.

Context inputs:

- tenant and actor identity
- session history and recent events
- active pending actions
- object metadata relevant to the prompt
- attachment metadata and digests
- policy summary
- storage container/prefix constraints
- previous action outcomes
- known provider health/cost posture

Context should be structured, bounded, and marked as trusted or untrusted. File
names, object metadata, and tool results are untrusted data because they can
contain prompt-injection text.

```text
User prompt
  |
  v
ContextHydrator
  +--> actor/session/action state
  +--> object metadata
  +--> policy summary
  +--> attachment refs
  +--> recent artifacts
  |
  v
Planner / model loop
```

Do not reach for a vector database first. Use exact metadata lookups,
prefix-aware listings, recent session events, and simple search before adding
semantic retrieval.

## Tool Plane

Tools should be schema-first and policy-aware.

Tool categories:

| Category | Examples | Rules |
| --- | --- | --- |
| Read-only storage | `list_files`, `get_file_info` | Safe to expose to model with bounds. |
| Proposed actions | `propose_delete`, `propose_upload` | Model can propose, not execute. |
| Runtime actions | `authorize_action`, `execute_action` | Runtime-only, not exposed directly to model. |
| Verification | `verify_upload`, `verify_delete`, `diff_manifest` | Deterministic tools used after execution. |
| Session tools | `create_artifact`, `spawn_child_session`, `get_action_status` | Useful for background and parallel work. |
| Diagnostics | provider status, budget posture | Read-only and bounded. |

The core principle:

```text
The model may propose work and request context.
The runtime decides whether work may happen.
The executor performs work.
The verifier proves what happened.
```

## Execution Plane

Start with inline execution. Shape it so a queue/worker can replace it later.

```python
class ActionExecutor(Protocol):
    async def execute(self, action: Action) -> ActionExecutionResult:
        """Execute one already-authorized action and return a structured result."""
```

Local implementation:

```text
Runtime creates action
Runtime transitions to executing
Executor calls storage client
Verifier checks result
Runtime records action events/artifacts
Runtime returns response
```

Future worker implementation:

```text
Runtime creates/authorizes action
Runtime enqueues action_id
Worker loads action from store
Worker CAS transitions queued -> executing
Worker executes and verifies
Worker records result
Clients observe session events
```

Queue payloads must contain only:

```text
tenant_id
action_id
attempt
```

The durable store remains the source of truth. Queue messages are hints, not
authority.

## Verification Layer

Ramp's Inspect closes the loop by proving code changes with tests, telemetry,
feature flags, visual checks, and screenshots. Nimbus needs the same product
shape for cloud-file operations.

| Action | Verification |
| --- | --- |
| Upload attachment | decoded bytes match declared size, optional SHA-256 digest matches, destination object exists, stored metadata matches expected size/digest where provider supports it |
| Delete file | target object was exactly the object authorized, delete result persisted, follow-up metadata/listing reconciles absence or delete marker |
| List files | result includes count, returned count, truncation flag, prefix, and time of listing |
| Get file info | object name/version match request, missing-object error is domain-shaped |
| Summarize prefix | summary cites object refs/artifacts, not just ungrounded model text |

Verification outputs become artifacts, not just log lines.

## User Flows

### Slack Cleanup Flow

User:

```text
@nimbus find duplicate CSV exports under reports/ and show me a cleanup plan
```

System:

```text
1. Bridge verifies Slack event and signs Nimbus request.
2. Nimbus creates or resumes the Slack thread session.
3. Runtime hydrates context for reports/.
4. Model proposes list/info actions.
5. Runtime executes read-only inspection.
6. Runtime creates a manifest artifact with candidate duplicates.
7. Nimbus replies with a cleanup plan and asks whether to delete candidates.
```

User:

```text
Delete the duplicates you found
```

System:

```text
1. Runtime creates delete actions in awaiting_confirmation.
2. Slack renders confirm buttons with action IDs.
3. User confirms.
4. Runtime validates same actor, same tenant, same session, same target.
5. Executor deletes each object.
6. Verifier checks final object state.
7. Nimbus posts a before/after artifact and action summary.
```

### CLI Upload Flow

User:

```shell
nimbus --session invoices
> upload attached files to invoices/2026-04/
```

System:

```text
1. CLI creates a local actor identity.
2. Runtime validates local attachment bytes.
3. Upload actions are created with idempotency keys.
4. Executor uploads through CloudStorageClient.
5. Verifier checks stored object info.
6. CLI shows object names, sizes, and verification status.
```

### Web Multiplayer Flow

```text
1. Product manager opens a Nimbus session from the web app.
2. Designer joins the same session from Slack.
3. Engineer joins from CLI.
4. Each prompt is attributed to its actor.
5. The agent creates artifacts and action proposals.
6. Any authorized reviewer can approve actions allowed by policy.
7. All clients receive the same ordered session events.
```

### Background Child Session Flow

User:

```text
Compare cleanup opportunities in logs/, exports/, and tmp/ separately.
```

System:

```text
1. Parent session creates three child sessions.
2. Each child inspects one prefix with a bounded budget.
3. Children produce manifest artifacts.
4. Parent session summarizes the artifacts.
5. User approves only the safe cleanup subset.
```

Child sessions must have hard limits:

- max child sessions per parent
- max runtime
- max model calls
- max storage reads
- no destructive execution without explicit parent/user authorization

## API Shape

The existing `/ai/chat/turn` route can evolve without losing compatibility.

Future response:

```json
{
  "request_id": "req_123",
  "session_id": "slack:T123:C456:1713840000.123456",
  "text": "I found 3 duplicate CSV exports. Review the attached plan.",
  "outcome": "confirmation_required",
  "actions": [
    {
      "action_id": "act_001",
      "kind": "delete_file",
      "status": "awaiting_confirmation",
      "target": {
        "provider": "s3",
        "container": "team-bucket",
        "object_name": "reports/old.csv"
      }
    }
  ],
  "artifacts": [
    {
      "artifact_id": "art_001",
      "kind": "manifest",
      "summary": "Cleanup plan with 3 candidates"
    }
  ],
  "confirmation": {
    "action_id": "act_001",
    "kind": "delete_file",
    "prompt": "Delete reports/old.csv?",
    "expires_at": "2026-04-30T18:30:00Z"
  },
  "suggested_next_actions": [
    "confirm cleanup",
    "show details",
    "cancel"
  ]
}
```

Add future endpoints when the session model is ready:

```text
POST   /ai/sessions
GET    /ai/sessions/{session_id}
GET    /ai/sessions/{session_id}/events
POST   /ai/sessions/{session_id}/prompts
GET    /ai/actions/{action_id}
POST   /ai/actions/{action_id}/confirm
POST   /ai/actions/{action_id}/cancel
GET    /ai/artifacts/{artifact_id}
```

Do not add these routes before the internal stores and models exist. The route
shape should follow the kernel, not create the kernel accidentally in HTTP.

## Store Interfaces

Start with file-backed or SQLite-backed stores. Make the interfaces match a
future Postgres/Valkey deployment.

```text
SessionEventStore
ActionStore
ArtifactStore
IdempotencyStore
ReplayStore
RateLimitStore
ConversationStore
```

### ActionStore

```python
class ActionStore(Protocol):
    def create_or_get_by_idempotency(
        self,
        *,
        tenant: TenantIdentity,
        idempotency_key: str,
        create: Callable[[], ActionCreate],
    ) -> Action:
        """Create an action once for a logical request."""

    def transition(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
        expected: ActionStatus,
        next_status: ActionStatus,
        event_type: str,
        event_payload: Mapping[str, object],
    ) -> Action | None:
        """Atomically transition an action and append the matching event."""

    def get(
        self,
        *,
        tenant: TenantIdentity,
        action_id: str,
    ) -> Action | None:
        """Load one tenant-scoped action."""
```

### IdempotencyStore

```python
class IdempotencyStore(Protocol):
    def get(self, tenant: TenantIdentity, key: str) -> IdempotencyRecord | None:
        """Return an unexpired idempotency record."""

    def put_if_absent(
        self,
        tenant: TenantIdentity,
        key: str,
        value: Mapping[str, object],
        expires_at: datetime,
    ) -> IdempotencyRecord:
        """Persist one logical request result exactly once."""
```

### RateLimitStore

```python
class RateLimitStore(Protocol):
    def allow(
        self,
        *,
        key: str,
        capacity: int,
        refill_per_second: float,
        now: float | None = None,
    ) -> bool:
        """Return whether this request may consume one token."""
```

## Data Store Evolution

### Stage 0: Current Local Files

Use current JSON-file persistence for a single-machine deployment. This is
appropriate for the current Fly.io topology and classroom demo.

### Stage 1: SQLite

SQLite is a strong next local primitive for session/action state:

- transactional
- inspectable
- fast enough for one process
- naturally maps to Cloudflare Durable Object-style per-session databases
- less fragile than many JSON files as event volume grows

Use SQLite if the file-backed event/action store starts becoming awkward before
we are ready for Postgres.

### Stage 2: Postgres

Postgres becomes the first serious durable shared backend.

Use it for:

- tenants
- actors
- sessions
- messages
- actions
- action events
- artifacts metadata
- idempotency records that must survive process restarts

Critical indexes:

```sql
CREATE UNIQUE INDEX actions_tenant_action_id
    ON actions (tenant_id, action_id);

CREATE UNIQUE INDEX actions_tenant_idempotency
    ON actions (tenant_id, idempotency_key);

CREATE INDEX actions_ready_queue
    ON actions (status, next_attempt_at)
    WHERE status IN ('queued', 'failed_retryable');

CREATE INDEX session_events_ordered
    ON session_events (tenant_id, session_id, sequence);
```

### Stage 3: Valkey

Valkey should hold hot, ephemeral coordination:

- rate limit buckets
- nonce replay hot cache
- short-lived idempotency cache
- session presence
- realtime fanout hints

Valkey should not be the only source of durable action truth.

### Stage 4: Queue And Workers

Introduce a queue when inline execution threatens API latency or reliability.

Candidate systems:

- SQS or Cloud Tasks for simple at-least-once work
- Celery/RQ for Python-native early worker pools
- Temporal only when workflows become long-running, cross-provider, and timer-heavy

Queue messages contain action IDs, not full action payloads.

## Scalability Model

A million registered users does not mean a million concurrent users. Design for
active workload and tenant isolation:

```text
registered users: 1,000,000
daily active users: 50,000-200,000
peak concurrent users: 2,000-20,000
peak action rate: hundreds to low thousands/sec
```

Scale plan:

```text
Single modular service
  |
  v
API fleet + shared Postgres
  |
  v
Valkey for hot coordination
  |
  v
Queue + worker fleet for long-running actions
  |
  v
Regional placement and tenant sharding if needed
```

The bottlenecks should be provider cost and provider latency, not missing
system foundations.

Backpressure layers:

| Layer | Controls |
| --- | --- |
| Ingress | request size limit, signed auth, nonce replay, idempotency, global/tenant/user rate limits |
| Runtime | max active turns per session, max actions per session, max child sessions, step budget |
| Queue | per-kind queues, bounded depth, tenant fairness, retry budgets |
| Worker | provider-specific concurrency caps, circuit breakers, timeouts |
| Product | free-tier caps, admin policies, staged execution, async responses |

Degradation order:

```text
1. throttle bulk uploads and large summaries
2. throttle AI-heavy background work
3. keep cheap reads available as long as possible
4. fail destructive actions closed
5. return queued/accepted status instead of holding HTTP requests open
```

## Reliability Model

Reliability is not an observability dashboard. It is a set of invariants that
survive failure.

Core invariants:

1. A tenant cannot read, mutate, cache, or summarize another tenant's data.
2. A destructive action executes only after the verified actor authorizes that
   exact action.
3. Retrying the same logical request returns the same action/result or current
   action state.
4. Action status transitions are monotonic.
5. Nimbus never reports success before the result and event are durably
   recorded.
6. Unknown auth, unknown policy, malformed input, and expired confirmation fail
   closed.
7. Resource growth is bounded by active workload, not historical traffic.
8. Every important state transition has a durable event.

Failure handling:

| Failure | Expected behavior |
| --- | --- |
| duplicate Slack event | same idempotency key maps to same session/action |
| replayed signed request | rejected by nonce/replay state |
| process crash after action creation | action is visible and resumable |
| worker crash before provider call | action remains queued/executing and can be reconciled |
| provider timeout after side effect | verifier/reconciler determines final state before claiming success |
| OpenRouter 429 storm | circuit breaker/fallback/degraded response; no unbounded retry |
| storage outage | retry only safe operations; keep action state explicit |
| Valkey unavailable | fall back to durable store or fail closed for side-effecting paths |
| Postgres unavailable | stop side-effecting work; do not execute from cache only |

## Antithesis-Style Red Team Plan

The correctness plan should assume hostile scheduling and constant failure.

Crash points:

```text
after action create, before response
after confirmation_required response, before event append
after authorization transition, before queue enqueue
after queue enqueue, before response
after worker claims action, before provider call
after provider success, before DB success write
during artifact write
during session summary update
```

Duplicate delivery:

```text
same Slack event twice
same bridge request twice
same Nimbus signed request twice
same confirmation callback twice
same queue message twice
two workers racing on one action
```

Reordering:

```text
confirmation arrives before first response is observed
retry arrives while original is still executing
queue attempt 2 arrives before attempt 1
expired confirmation arrives after a new action is created
```

Clock skew:

```text
bridge clock ahead or behind
API nodes disagree
confirmation expires during network delay
worker receives delayed message
```

Malicious input:

```text
huge base64
wrong declared size
sha256 mismatch
Unicode/path traversal object names
filenames containing prompt injection
tool result pretending to be system instructions
None or non-string values in user text
extra JSON fields
cross-tenant IDs mixed together
```

Load/saturation:

```text
one tenant floods 100k upload actions
many tenants do small reads during the flood
OpenRouter returns 429 to every call
S3 latency rises 100x
queue backlog grows for one action kind
```

The expected result is not that every operation succeeds. The expected result
is that invariants hold, overload is explicit, and humans can inspect the final
state.

## Security Model

Threats:

- forged wrapper requests
- spoofed actor IDs
- replayed confirmation tokens
- prompt injection through object names or tool output
- cross-tenant lookup bugs
- unbounded attachment payloads
- model attempts to choose a different bucket
- admin/support endpoint bypass
- leaked secrets in logs or artifacts
- malicious files or exfiltration attempts

Controls:

- signed service-to-service requests
- verified actor envelope
- tenant-scoped keys everywhere
- nonce replay state
- idempotency records
- object/container pinning
- schema validation with `extra="forbid"`
- decoded-byte validation for attachments
- confirmation tokens stored as hashes
- policy fail-closed behavior
- audit/action events
- redaction in logs and artifacts

Enterprise features that should fit later:

- SSO/OIDC
- SCIM
- RBAC
- prefix-level permissions
- admin audit export
- data retention policies
- regional data residency
- no-training/no-retention provider settings

## Agent Autonomy Ladder

Do not jump from chat to full autonomy. Increase autonomy by action class.

```text
Level 0: Answer with no tools.
Level 1: Read-only tools with bounded outputs.
Level 2: Propose actions and create plans/artifacts.
Level 3: Execute safe actions automatically.
Level 4: Execute destructive/expensive actions after confirmation.
Level 5: Execute policy-approved background workflows.
Level 6: Spawn bounded child sessions for parallel work.
```

Examples:

- `list_files` can reach Level 3 quickly.
- `delete_file` should stay Level 4 or higher with strong policy.
- bulk cleanup may require Level 4 plus artifact review.
- child sessions start at read-only research until the parent/user approves
  execution.

## Client Strategy

### Slack

Slack is the viral product surface:

- users work where the problem appears
- other people see Nimbus working
- threads naturally map to sessions
- Block Kit can render status, artifacts, confirmation buttons, and summaries

Slack should not contain product logic. It normalizes events, verifies Slack,
signs actor claims, renders session events, and sends prompts.

### CLI

CLI is the power-user and development surface:

- easy to test
- easy for agents to compose
- maps well to DHH/Mitchell-style agent-driven workflows
- ideal for debugging exact session/action state

The CLI should eventually use `nimbus_runtime` directly or the same session API
instead of maintaining separate semantics.

### Web

Web is the trust and review surface:

- session timeline
- action graph
- artifact viewer
- before/after manifests
- multiplayer presence
- admin/support views

Web should make the audit trail visible, not hide it.

### MCP

MCP should arrive after the core tools and action semantics stabilize. It gives
Nimbus a standard connector surface for other agent hosts without making the
internal runtime depend on MCP.

## Build And Tooling Strategy

Start with the current Python stack:

```text
uv
ruff
mypy --strict
pytest
FastAPI
Pydantic/dataclasses
Sphinx/MyST docs
```

Add a canonical task surface:

```text
just setup
just test
just lint
just docs
just nimbus
just smoke-wrapper
```

Use protobuf when stable contracts cross language/process boundaries:

- `VerifiedActor`
- `Action`
- `ActionEvent`
- `ObjectRef`
- `Artifact`
- `PolicyDecision`

Use Bazel when the repo has enough multi-language/codegen/build pressure:

- Python backend
- TypeScript web
- generated protobuf/OpenAPI clients
- maybe Go/Rust workers
- hermetic CI needs

Do not migrate to Bazel just to look serious. Make the package boundaries clean
enough that Bazel can be introduced without rewriting the architecture.

## What To Add, Subtract, And Modify

### Add

- `VerifiedActor`, `TenantIdentity`, `ObjectRef`
- `Action`, `ActionStatus`, `ActionEvent`
- `ActionStore` and `SessionEventStore`
- `Artifact` model and artifact store
- policy module
- verification module
- executor abstraction
- session event stream
- action-focused docs and acceptance scenarios
- agent behavior eval fixtures

### Subtract

- route-local product semantics
- model-direct destructive execution
- broad agent framework abstractions before the kernel is stable
- vector DB by default
- premature microservices
- infrastructure chosen for status instead of pressure

### Modify

- `nimbus_runtime` becomes the product kernel
- `ai_server` remains HTTP/auth/idempotency adapter
- delete confirmation moves to the action ledger
- attachment upload moves to actions and artifacts
- `/ai/chat/turn` grows action/artifact fields
- CLI and Slack render the same session/action model

## Bottom-Up Implementation Plan

This is the recommended build order.

1. **Design invariants.** Add a short invariants document and keep it close to
   the runtime code.
2. **Identity primitives.** Add `TenantIdentity`, `VerifiedActor`, and
   principal-key helpers.
3. **Action primitives.** Add `ObjectRef`, `ActionStatus`, `Action`,
   `ActionEvent`, and transition validation.
4. **File-backed ActionStore.** Match the future Postgres interface; keep the
   implementation simple.
5. **SessionEventStore.** Append ordered events for prompts, actions, and
   artifacts.
6. **Port delete confirmation.** Replace bespoke pending delete state with
   `awaiting_confirmation -> authorized -> executing -> verifying -> succeeded`.
7. **Add verification reports.** For delete, record exact target and
   post-delete reconciliation.
8. **Port attachment upload.** Represent each upload as an action with
   verification and partial-success artifacts.
9. **Policy module.** Centralize allow/deny/confirmation decisions.
10. **Executor abstraction.** Keep local inline execution, but use queue-ready
    `execute(action)` semantics.
11. **Expose actions/artifacts in HTTP.** Extend response models after runtime
    state exists.
12. **Unify CLI/Slack semantics.** Make them clients of the session/action
    model.
13. **Add eval fixtures.** Include ambiguous intent, prompt injection,
    duplicate events, wrong-actor confirmation, and provider ambiguity.
14. **Introduce SQLite/Postgres only when store pressure appears.**
15. **Introduce queue/workers only when inline execution hurts latency or
    reliability.**

## Success Metrics

Product metrics:

- sessions started
- prompts per session
- actions proposed
- actions completed
- action completion rate
- confirmation acceptance/rejection rate
- partial-success rate
- repeat users
- multiplayer sessions
- child sessions created
- merged/accepted user outcomes for demo workflows

Reliability metrics:

- action latency by kind
- queue depth by kind and tenant
- duplicate request rate
- idempotency hit rate
- verification failure rate
- provider timeout/429/error rate
- ambiguous outcome count
- policy denial count
- cost per successful action

These metrics should be derived from durable events where practical. Logs and
dashboards are views, not the source of truth.

## Non-Goals For The Next Slice

- no Kubernetes migration
- no Temporal adoption
- no general-purpose policy language
- no vector database
- no Bazel migration
- no multi-service split
- no autonomous destructive workflows

The next slice should make the kernel real, not make the infrastructure louder.

## References

- [Stripe Minions: one-shot, end-to-end coding agents](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents)
- [Stripe Minions, Part 2](https://stripe.dev/blog/minions-stripes-one-shot-end-to-end-coding-agents-part-2)
- [Ramp Inspect: background agent](https://builders.ramp.com/post/why-we-built-our-background-agent)
- [How Linear uses Linear Agent](https://linear.app/now/how-we-use-linear-agent-at-linear)
- [OpenAI agent-building tools](https://openai.com/index/new-tools-for-building-agents/)
- [OpenAI Structured Outputs](https://openai.com/index/introducing-structured-outputs-in-the-api/)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: Model Context Protocol](https://www.anthropic.com/news/model-context-protocol)
- [Pragmatic Engineer: Designing Data-Intensive Applications](https://newsletter.pragmaticengineer.com/p/designing-data-intensive-applications)
- [Pragmatic Engineer: How S3 is built](https://newsletter.pragmaticengineer.com/p/how-aws-s3-is-built)
- [Pragmatic Engineer: Building WhatsApp](https://newsletter.pragmaticengineer.com/p/building-whatsapp-with-jean-lee)
- [Pragmatic Engineer: The Philosophy of Software Design](https://newsletter.pragmaticengineer.com/p/the-philosophy-of-software-design)
