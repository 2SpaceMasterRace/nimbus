# Nimbus Runtime Invariants

These invariants are the contract for the Nimbus 1.0 agent kernel. If a future
store, worker, Slack bridge, CLI, or web client cannot preserve them, it must
adapt at the boundary instead of weakening the runtime.

## Identity

- Every durable action and event is scoped by `TenantIdentity`.
- A raw `user_id` is not authority by itself; destructive actions bind to a
  `VerifiedActor`.
- A confirmation can authorize only the exact action created for the same
  tenant, session, actor, kind, and target.

## Actions

- Actions are the durable unit of side-effecting work.
- Action creation is idempotent per actor-scoped request fingerprint. Caller
  idempotency keys are never trusted by themselves.
- Action status transitions are monotonic and must pass the domain transition
  table before the store writes them.
- No model response can directly mark an action as authorized, executing, or
  succeeded.
- Provider success is not user-visible until the runtime records the matching
  action transition.
- Uploads and deletes both produce durable action summaries and verification
  artifacts.
- Action input, result, and failure payloads are typed domain records, not
  anonymous dictionaries.

## Events

- Session events are the narrative truth for support, replay, and future
  realtime clients.
- Events are ordered per `(tenant_id, session_id)` by store-assigned sequence
  number.
- In the default SQLite store, action creation, action transitions, artifact
  creation, and their matching events commit or roll back together.
- Clients may cache or project events, but projections are rebuildable from the
  durable event stream.

## Artifacts

- Artifacts are immutable evidence or work products for a session.
- A successful side-effecting action should create a verification artifact when
  practical.
- Artifact payloads stay small and structured; larger future reports should
  move to object storage and leave only metadata in the artifact record.

## Failure

- Unknown action kind, malformed action state, cross-tenant target, expired
  confirmation, and changed action state fail closed.
- Duplicate wrapper delivery must converge on the existing action/result rather
  than duplicate side effects.
- Reusing an idempotency key with different request parameters is a conflict,
  not a replay.
- Queue messages in a future worker system will be hints containing action IDs,
  not authority or full action payloads.

## Bounds

- The local store is a single SQLite database under the session directory. This
  is acceptable for the current single-node topology and gives transactional
  semantics without adding a service dependency.
- The next store may be Postgres without changing the runtime domain model.
- Large artifacts and future event payloads must stay outside hot action/event
  records.
