# Deterministic Simulation Testing

Deterministic simulation testing, or DST, is the testing strategy described in
the agent-platform design docs for proving the future Nimbus session/action
kernel under hostile scheduling. Instead of sprinkling sleeps into concurrent
tests, a seeded scheduler controls time, task order, provider responses,
retries, crashes, duplicate messages, malformed records, and client reconnects.

DST is not needed for every small patch. It becomes valuable when Nimbus moves
from turn-centric runtime behavior to durable sessions, operation envelopes,
action ledgers, projections, queues, and workers.

## Why Nimbus needs it

The hard bugs are not single-line exceptions. They are histories:

- a wrapper retries while the original request is still executing
- two workers claim the same action
- S3 performs a delete but the HTTP response times out
- a confirmation arrives after a newer action was created
- a client reconnects while an artifact is being written
- a process crashes after appending an event but before broadcasting it
- an event record is truncated or carries the wrong payload digest
- an idempotency lookup races with action creation

Example tests can cover one or two of these. DST lets the codebase explore
many schedules and still reproduce the exact failing seed.

## Simulation components

The design docs name the core pieces:

| Component | Role |
| --- | --- |
| `SimulationClock` | Controls wall time, monotonic time, and expiry decisions. |
| `DeterministicScheduler` | Orders tasks, sleeps, retries, crashes, and reconnects from a seed. |
| `SimulatedSessionEventStore` | Appends and lists ordered session events. |
| `SimulatedActionStore` | Enforces action transitions with CAS semantics. |
| `SimulatedIdempotencyStore` | Models duplicate request behavior. |
| `SimulatedEventBus` | Delivers, delays, duplicates, or drops client events. |
| `SimulatedAIProvider` | Returns scripted model/tool plans without network calls. |
| `SimulatedStorageProvider` | Performs exact provider outcomes, including ambiguous timeouts. |
| `FaultInjector` | Chooses crash points, duplicates, reorderings, and provider failures. |
| `InvariantChecker` | Verifies safety and liveness claims after each history. |

Start with in-memory implementations. Add SQLite snapshots only when setup
cost becomes a problem.

## First useful scenarios

### Duplicate request

```text
1. Actor A submits a prompt with idempotency key K.
2. The wrapper retries the same signed request.
3. Runtime accepts one logical operation.
4. Both callers receive the same response or current operation state.
```

Properties:

- one idempotency key creates at most one operation/action
- replayed nonce is rejected when the nonce is reused
- duplicate request does not duplicate side effects

### Wrong actor confirmation

```text
1. Actor A asks Nimbus to delete reports/old.csv.
2. Runtime creates an awaiting_confirmation action.
3. Actor B confirms the action.
4. Runtime rejects Actor B.
5. Actor A confirms before expiration.
6. Runtime authorizes and executes once.
```

Properties:

- same-actor confirmation
- exact target binding
- action status does not move backward

### Ambiguous delete

```text
1. Worker transitions a delete action to executing.
2. Simulated storage deletes the object.
3. The provider response is lost as a timeout.
4. Worker crashes before writing success.
5. Reconciler restarts and checks object state.
6. Runtime records a reconciled terminal result.
```

Properties:

- success is not visible before durable event
- provider ambiguity is reconciled
- retry does not delete a different target

### Reconnect and replay

```text
1. Client observes events through sequence 10.
2. Runtime appends events 11 through 15.
3. Event bus drops messages 13 and 14.
4. Client reconnects with after=10.
5. Runtime returns 11 through 15 in order.
6. Client projection equals replay projection.
```

Properties:

- ordered event delivery
- missing sequence triggers catch-up
- replay equals live projection

### Malformed event record

```text
1. Runtime appends events 1 through 5.
2. Fault injector truncates event 4 or changes its payload digest.
3. Projection replay attempts to read the log.
4. Store rejects the malformed record explicitly.
5. Runtime does not guess, coerce, or publish derived state from it.
```

Properties:

- durable records validate actual bytes
- unknown or corrupt records fail closed
- repair/recovery path is explicit

### Store graduation contract

```text
1. Run the same generated operation history against file-backed stores.
2. Run it against SQLite-backed stores.
3. Compare events, actions, idempotency decisions, and projections.
4. Assert the product contract is unchanged.
```

Properties:

- storage backend change preserves invariants
- sequence allocation remains atomic
- action transition semantics remain compare-and-set

## Invariants to check

The initial invariant checker should focus on the core platform claims:

- no cross-tenant event or object reference is visible
- malformed input never reaches the provider
- one idempotency key creates at most one logical operation
- destructive action does not execute without same-actor authorization
- action status transitions are valid and monotonic
- a terminal action never later succeeds with a conflicting outcome
- success is not visible before durable event append
- replayed projection equals live projection
- cancelled operation emits no later success
- expired confirmation cannot authorize an action
- duplicate queue message cannot execute an action twice
- resource registries are bounded after cleanup
- malformed records fail closed
- store backend changes do not change observable session/action semantics

## Test data pre-seeding

Pre-seeding keeps simulation startup fast. Useful seeds:

- `seed_basic_tenant`
- `seed_slack_session`
- `seed_cli_session`
- `seed_storage_bucket`
- `seed_duplicate_files`
- `seed_pending_delete`
- `seed_large_artifact_manifest`
- `seed_provider_timeout_after_delete`

These can start as deterministic fixture builders. Once a SQLite store exists,
the same shapes can become reusable snapshots.

## What not to simulate first

Do not begin with a huge distributed system model. The smallest useful DST
harness can run in one process with fake stores and fake providers.

Defer:

- multi-region placement
- Temporal workflows
- real network partitions
- real OpenRouter or S3 calls
- probabilistic load generators
- massive analytics pipelines

The first harness should make the action/session invariants obvious and
reproducible.

## Implementation path

1. Add a `Clock` protocol to new runtime code.
2. Define `SessionEvent` and `Action` transition rules.
3. Build in-memory simulated stores with deterministic sequence assignment.
4. Add a scripted fake storage provider.
5. Add a seeded scheduler that can duplicate, delay, and reorder operations.
6. Encode the first four scenarios above.
7. Add invariant checking after every scenario.
8. Record failing seeds in regression tests.
9. Add pre-seeded fixtures after setup becomes slow.

The success condition is not that every simulated operation succeeds. The
success condition is that Nimbus preserves its invariants and leaves enough
durable evidence for humans and software to recover.
