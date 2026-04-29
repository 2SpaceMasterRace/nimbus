# Reliability Property Catalog

This catalog translates the `agent-platform-design*.md` reliability model into
properties reviewers and test authors can check. A property is a claim the
system should preserve across ordinary success, retries, duplicate delivery,
crashes, provider ambiguity, and overload.

Some properties are already implemented in today's wrapper/runtime. Most are
design targets for the action/event kernel described in the three
agent-platform design passes.

## Reading the catalog

Each property has three parts:

- **Claim:** the externally visible guarantee.
- **Why it matters:** the product or safety reason.
- **Test shape:** the kind of test that should eventually exercise it.

## Safety properties

### Tenant isolation

**Claim:** a tenant cannot read, mutate, cache, summarize, replay, or receive
events for another tenant's data.

**Why it matters:** tenant identity is the platform isolation boundary. A
cross-tenant object reference or session event leak is a security incident.

**Test shape:** property tests for key derivation, route tests for tenant
scoping, and DST scenarios that mix tenants in action IDs, object refs, and
idempotency keys.

### Verified actor before authority

**Claim:** side-effecting work is authorized only for a verified actor, not a
raw caller-provided `user_id`.

**Why it matters:** HMAC proves the wrapper, but the product needs the wrapper
to verify the upstream human or service actor.

**Test shape:** route tests with malformed or missing actor claims, plus
policy tests that reject unverified or mismatched actors.

### Same-actor destructive confirmation

**Claim:** a destructive action executes only when the same verified actor
authorizes the same action, same target, same tenant, and same session before
expiration.

**Why it matters:** delete is not just another tool call. It needs human intent
bound to an exact target.

**Test shape:** runtime tests for wrong actor, wrong target, expired
confirmation, duplicate confirmation, and replayed confirmation token.

### Model proposes, runtime decides

**Claim:** the model can propose work and request context, but it cannot grant
itself access, bypass confirmation, or mark an action succeeded.

**Why it matters:** model output is untrusted. Access control and state
transitions must be deterministic runtime behavior.

**Test shape:** tool-schema tests, policy tests, and red-team prompts where the
model asks for raw provider calls, broader buckets, or direct delete authority.

### Exact target side effects

**Claim:** an action may mutate only the exact object or set of objects that
policy authorized.

**Why it matters:** prefix confusion, stale listings, Unicode object names, and
prompt injection can all make a user think one target was chosen while the
runtime mutates another.

**Test shape:** property tests around object reference normalization and DST
scenarios where listings change between proposal, confirmation, execution, and
verification.

### Durable before visible

**Claim:** Nimbus does not report action success before the result and event
are durably recorded.

**Why it matters:** users and teammates treat the session timeline as truth.
Visible success without durable state creates irrecoverable confusion after a
crash.

**Test shape:** crash-point simulations after provider success and before
event append; route tests that verify response construction follows persistence.

### Event log owns truth

**Claim:** prompts, model updates, tool requests, confirmations, action
transitions, verification results, comments, and future branch/PR updates are
durable events; client state is a projection.

**Why it matters:** recovery, reconnect, support views, metrics, and replay all
need one authoritative narrative.

**Test shape:** projection tests that rebuild state from events and compare it
with live state after every operation.

### Per-session order, not global order

**Claim:** events are linearly ordered within a session, but Nimbus does not
require one global order across all sessions.

**Why it matters:** the product needs users in one session to agree on what
happened. Global ordering would add cost without improving that workflow.

**Test shape:** event-store tests for unique `(tenant_id, session_id,
sequence)` and concurrent sessions that can advance independently.

### Monotonic action status

**Claim:** action status transitions never move backward or skip required
guards.

**Why it matters:** retries, duplicate workers, and stale clients must not
revive cancelled or failed work.

**Test shape:** table-driven transition tests and property tests over generated
transition histories.

### Action transaction boundary is complete

**Claim:** side-effecting actions carry enough record data to authorize,
execute, retry, reconcile, audit, and expire safely.

**Why it matters:** a delete or overwrite is a transaction-like unit of work,
not a chat transcript line.

**Test shape:** model/schema tests that require tenant, session, action, actor,
target reference, target digest, policy decision, confirmation, idempotency,
status, attempt, and expiry where appropriate.

### At most one winner for a transition

**Claim:** competing workers or retries cannot both claim the same action
transition.

**Why it matters:** at-least-once queues and duplicate requests are normal.
Compare-and-set transition semantics prevent duplicate side effects.

**Test shape:** concurrent store tests and DST races with two workers claiming
one `queued` action.

### Idempotent logical request

**Claim:** replaying the same logical request with the same idempotency key
returns the cached response or current logical state instead of re-executing
the operation.

**Why it matters:** Slack events, wrapper requests, HTTP clients, and queues can
all deliver duplicates.

**Test shape:** property tests for cache keys and route tests that send the
same signed request twice.

### Idempotency and action creation are atomic

**Claim:** checking an idempotency key and creating the corresponding action or
committed event is one atomic decision inside the authority store.

**Why it matters:** if lookup and creation are split, duplicate requests can
race and create duplicate side effects.

**Test shape:** concurrent store tests and DST duplicate-delivery scenarios.

### Malformed input rejected early

**Claim:** malformed wrapper input, non-string text, invalid attachment bytes,
bad content types, and mismatched digests are rejected before reaching the
model or provider.

**Why it matters:** permissive coercion turns invalid transport data into
apparently valid user intent.

**Test shape:** fuzz harnesses for parsers and property tests for attachment
validation.

### Durable records validate bytes

**Claim:** persisted events and artifacts validate schema version, actual
payload length, content digest, and known record framing before replay.

**Why it matters:** a durable event log is only useful if recovery can
distinguish valid records from corrupt or unknown records.

**Test shape:** fuzz and recovery tests for truncated, oversized, wrong-digest,
unknown-version, and malformed event records.

### Tool output is untrusted data

**Claim:** object names, metadata, tool results, and attachment filenames are
treated as data, not instructions.

**Why it matters:** prompt injection can arrive indirectly through storage
contents.

**Test shape:** golden safety cases with malicious filenames and tool outputs.

### Resource growth is bounded

**Claim:** in-memory registries grow with active workload, not historical
traffic.

**Why it matters:** rate-limit buckets, nonce caches, locks, idempotency state,
and replay caches can otherwise become unbounded memory leaks.

**Test shape:** unit tests for expiry/cleanup behavior and load tests that
create many expired keys.

## Liveness properties

### Accepted operation becomes observable

**Claim:** once the Session Authority accepts an operation, the caller can
observe a corresponding event, completion, error, or cancellation.

**Why it matters:** users should not be left guessing whether work was lost.

**Test shape:** operation protocol tests and reconnect scenarios from sequence
numbers.

### Reconnect catches up by sequence

**Claim:** a client that reconnects with the last seen sequence receives all
later events in order or a structured error that forces full replay.

**Why it matters:** Slack, web, and CLI clients need stable recovery from
network loss.

**Test shape:** projection tests with missing sequences and DST reconnect
scenarios.

### Replay equals live projection

**Claim:** applying events live and replaying the same ordered events from the
beginning produce the same session projection.

**Why it matters:** replay is the foundation for support views, web reconnect,
deterministic tests, and future migrations.

**Test shape:** projection property tests and simulation histories compared
against live state.

### Wrong projection is rebuildable

**Claim:** if a Slack message, web timeline, latest-action cache, metrics view,
or support projection is wrong, Nimbus can rebuild it from durable events.

**Why it matters:** projections are allowed to be cached and stale only because
the event log remains authoritative.

**Test shape:** corrupt or clear projection state and assert replay reconstructs
the expected state.

### Retryable failure remains inspectable

**Claim:** when a retryable action fails, its state, attempt count, next retry
time, and last error class remain visible.

**Why it matters:** hidden retries are hard to support and easy to duplicate.

**Test shape:** executor tests with provider failures and action timeline
assertions.

### Ambiguous provider outcome is reconciled

**Claim:** after a provider timeout that may have performed a side effect,
Nimbus reconciles state before claiming success or retrying unsafe work.

**Why it matters:** a timeout after delete is not the same as a definite
failure.

**Test shape:** fake provider scripts that delete then timeout, upload then
fail metadata read, or succeed while the event store fails.

### Overload preserves control paths

**Claim:** overload sheds bulk/background and AI-heavy work before safety,
confirmation, cancellation, and cheap status reads.

**Why it matters:** a user must still be able to cancel or inspect risky work
when the system is busy.

**Test shape:** load-shedding unit tests and integration tests with priority
classes.

### Strong consistency only where needed

**Claim:** destructive authorization, action transitions, idempotency, session
event order, and spend limits use strong consistency inside their authority
boundary; presence, token streaming, metrics, and analytics may be eventual.

**Why it matters:** this preserves safety without pretending every product view
needs global consistency.

**Test shape:** design review checklist plus targeted tests for each strong
consistency boundary.

### Shutdown does not strand accepted work silently

**Claim:** a crash or shutdown after action creation leaves durable state that
can be resumed, reconciled, cancelled, or reported.

**Why it matters:** stateful agent work survives process boundaries only when
the durable record is authoritative.

**Test shape:** crash-point DST around action create, queue enqueue, provider
call, verification, and artifact write.

## Operational properties

### Metrics derive from stable events

**Claim:** core product and reliability metrics can be derived from durable
events where practical.

**Why it matters:** logs and dashboards are views. The session/action event
stream should be the audit source.

**Test shape:** metrics projection tests from fixed event histories.

### Metric cardinality is bounded

**Claim:** metrics do not use raw session IDs, user IDs, object names, prompts,
or secret-bearing error messages as labels.

**Why it matters:** high-cardinality metrics can break observability systems
and leak sensitive data.

**Test shape:** telemetry tests that assert allowed label names and value
classes.

### Schema changes preserve old replay

**Claim:** older events and artifacts can still be parsed, replayed, or
migrated after schema changes.

**Why it matters:** an event log becomes product memory. Breaking replay breaks
support, auditing, and deterministic tests.

**Test shape:** fixture histories with older schema versions and compatibility
tests for optional/removal behavior.

### Store choice follows topology

**Claim:** JSON/file state is acceptable for single-writer deployment; SQLite
is the next local transactional primitive; Postgres becomes required for
multiple writable processes.

**Why it matters:** reliability comes from matching the primitive to the
topology, not adding infrastructure for appearance.

**Test shape:** design review checklist and migration tests when store
interfaces change.

### Store graduation preserves invariants

**Claim:** every store stage preserves durable append, atomic sequence
allocation, action CAS transitions, atomic idempotency decisions,
read-after-sequence replay, projection rebuilds, checkpoint/compaction safety,
and malformed-record rejection.

**Why it matters:** moving from JSON to SQLite or Postgres should not change
the product contract.

**Test shape:** store contract tests reused across file, SQLite, and future
Postgres implementations.

## Review checklist

Use this checklist when a change touches session, action, storage, or AI
runtime behavior:

- Which tenant owns the state?
- Which actor is verified?
- Which operation or action is the idempotency key scoped to?
- What is durable before the response is visible?
- What happens after a timeout, duplicate delivery, crash, and retry?
- Which state can grow without bound?
- Which event lets a user or support engineer understand what happened?
- Which test checks the failure path, not only the happy path?
