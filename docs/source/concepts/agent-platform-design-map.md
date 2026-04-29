# Agent Platform Design Map

This page is the reading map for the glossary section. The primary source
corpus is the three agent-platform design passes:

- {doc}`../nimbus/agent-platform-design` establishes Nimbus as an
  agent-first operations platform with a small domain model: tenant, actor,
  session, action, object, and artifact.
- {doc}`../nimbus/agent-platform-design-2` sharpens the product model into a
  multiplayer session engine: clients submit operations, the server emits
  ordered events, actions become durable side effects, and clients render
  projections.
- {doc}`../nimbus/agent-platform-design-3` adds infrastructure discipline:
  event schemas, write-ahead log thinking, transaction boundaries, consistency
  choices, store graduation, load shedding, time semantics, and DST speed.

The rest of the codebase docs explain today's implementation. These three
files explain the platform Nimbus is trying to become.

## The seven-line system

The 3.0 design compresses Nimbus into seven lines:

```text
Clients submit operations.
Session authority appends events.
Actions model side effects.
Policy authorizes actions.
Executors do provider work.
Verifiers produce artifacts.
DST proves invariants.
```

Most glossary terms exist to make one of those lines precise.

## 1.0 design: action kernel

The first design pass moves Nimbus away from "chat around storage" and toward
durable agent work.

Core ideas:

- **Product thesis:** Nimbus is infrastructure for AI actions over business
  files.
- **Core nouns:** tenant, actor, session, action, object, artifact.
- **Core verbs:** propose, authorize, execute, verify, observe.
- **Runtime split:** model proposes; runtime decides; executor performs;
  verifier proves.
- **Action state machine:** destructive and expensive work becomes explicit
  durable state.
- **Policy model:** unknown or ambiguous work fails closed.
- **Verification layer:** upload, delete, list, and summarize actions produce
  proof.
- **Store evolution:** local files, SQLite, Postgres, Valkey, then queues and
  workers as topology demands.

The 1.0 pass is the source for most identity, action, policy, verification,
tool-plane, and client-strategy terms.

## 2.0 design: multiplayer session engine

The second pass makes session state the product surface.

Core ideas:

- **Session document:** a session contains prompts, model messages, operations,
  events, actions, artifacts, comments, confirmations, presence, and child
  sessions.
- **Operation protocol:** clients submit typed operations instead of mutating
  state directly.
- **Event protocol:** the server emits durable ordered facts with sequence
  numbers.
- **Realtime multiplayer:** clients reconnect by last seen sequence and hold
  local projections.
- **SessionWorker:** browser tabs share one stream, one projection, and one
  operation-multiplexing layer when possible.
- **Artifact manifests:** large proof/work products are immutable and fetched
  separately from event payloads.
- **DST:** simulation controls scheduler, time, duplicate delivery, crashes,
  provider responses, and reconnects.
- **Structured rules:** alerting and invariant checks should be typed and
  reusable.

The 2.0 pass is the source for operation envelopes, event projections,
reconnect, SharedWorker-style clients, artifact manifests, and DST scenarios.

## 3.0 design: infrastructure discipline

The third pass keeps the session/action direction and tightens the systems
model.

Core ideas:

- **Event-sourced runtime:** commands are validated at the boundary, effects
  are committed through an idempotent action ledger, and user-visible state is
  replayed from durable events.
- **Database-internals framing:** event logs need record framing, schema
  versions, payload length checks, digests, checkpoints, replay, and
  compaction.
- **Action transaction boundary:** side effects are not chat messages; they
  carry actor, target digest, policy, confirmation, idempotency, expiry,
  transition, and verification state.
- **Projection rule:** if a projection is wrong, rebuild it from the event log.
- **Consistency choices:** destructive authorization and action transitions
  need strong consistency inside their authority boundary; presence, token
  streaming, metrics, and analytics can be eventual projections.
- **Operation priorities:** safety and confirmation paths survive overload
  longer than bulk/background or AI-heavy work.
- **Trust and safety loop:** golden sets, adversarial sets, uncertainty sets,
  and incident sets guard agent behavior over time.
- **Store graduation contract:** every store stage must preserve durable append,
  atomic sequence allocation, CAS transitions, idempotency, replay, projection
  rebuilds, and malformed-record rejection.

The 3.0 pass is the source for write-ahead event log, consistency, store,
schema, time, load-shedding, metrics, data-residency, and durable-execution
terms.

## Current-code cross-check

Some glossary entries also mention current packages:

- `nimbus_runtime` is today's transport-neutral runtime.
- `ai_server` is today's HTTP session edge.
- `openrouter_ai_client_impl` is today's provider-specific AI implementation.
- `aws_client_impl`, `aws_client_service`, and `aws_client_adapter` are today's
  storage implementation, HTTP service, and service-backed adapter.

When the design docs and the current implementation differ, the glossary calls
that out as "today" versus "target" rather than pretending the future kernel
already exists.
