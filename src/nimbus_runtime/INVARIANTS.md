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

## Tasks

- Tasks are the durable unit of background work across Slack, CLI, and future
  clients.
- Task creation is idempotent per tenant-scoped request fingerprint.
- Task status transitions are monotonic and must pass the domain transition
  table before the store writes them.
- Task lifecycle events are appended to the same ordered session event stream
  used by actions and artifacts.
- In the default SQLite store, task creation, task transitions, and their
  matching events commit or roll back together.
- Postgres-backed task stores must preserve the same idempotency, tenant scope,
  and compare-and-set transition contract as SQLite.

## Plans And Approvals

- Plans are durable previews for risky or expensive work. They are not proof
  that external state changed.
- Plan creation is idempotent per tenant-scoped preview fingerprint.
- Plan status transitions are monotonic and must pass the domain transition
  table before the store writes them.
- Destructive runtime-owned deletes create both a plan and an approval before
  any action can be authorized.
- Approval decisions are bound to tenant, session, action, plan, required
  actor, allowed actors, exact target, and expiry.
- Wrong actor, wrong target, unknown approval, duplicate click, and expired
  approval histories fail closed.
- Failed approval decisions append audit events but must not authorize an
  action, approve a plan, or execute provider side effects.
- An applied plan means the corresponding runtime action reached a durable
  terminal success state with evidence, not that a model claimed success.
- Delete approval is requested only after Nimbus records a best-effort restore
  preview in the plan metadata.
- Every delete report includes a `RestorePlan` that is either restorable by
  provider version, not required because nothing was deleted, or explicitly
  unavailable with limitations.

## Worker Leases

- Worker leases are short-lived coordination records, not authority to perform
  storage side effects by themselves.
- A worker may acquire a task only when the task exists and no unexpired lease
  is present.
- Heartbeats can extend only the current worker's unexpired lease.
- Expired leases can be taken over by another worker, and each takeover
  increments the task attempt counter.
- Releasing a lease requires the current worker ID; one worker cannot release
  another worker's active lease.
- A worker loop must cancel in-flight work when it can no longer renew its
  lease. Work may resume only through a later successful lease claim.
- The worker loop is not authority to mark work succeeded. Handlers must still
  update task state and record evidence through the runtime stores.

## Backup Workflows

- Channel backup success requires byte-level evidence: downloaded size must
  match the source file record, destination verification must match SHA-256 and
  size, and manifest evidence must be recorded.
- Existing manifest entries are idempotency evidence only when they match the
  current source file identity and size.
- Content-hash dedupe may reuse an existing object key only after verifying that
  object against the expected hash and size.
- Each channel backup run creates a `verification_report` artifact before its
  `manifest` artifact, and the manifest must reference the verifier artifact.
- Channel backup artifact IDs are stable per task and artifact kind, so a retry
  converges on the same receipts instead of creating duplicate evidence.
- The workflow owns task-state transitions, but source, manifest, and object
  operations remain adapter capabilities injected at the boundary.

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
- Delete success is incomplete without a restore story or an explicit warning
  that true restore is unavailable.
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
- Background workflow manifests are receipts, not authority. Their verifier
  artifact is the byte-level evidence for object-store claims.
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
- Postgres stores are the shared deployment backend and must not change the
  runtime domain model or client-observed task/lease semantics.
- Large artifacts and future event payloads must stay outside hot action/event
  records.
