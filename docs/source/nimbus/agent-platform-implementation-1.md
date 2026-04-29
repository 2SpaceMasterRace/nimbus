# Building The Nimbus Agent Kernel

This is the implementation note for the first real slice of the Nimbus Agent
Platform Design.

## ELI5

Before this change, Nimbus could chat, ask for confirmation before a delete,
and upload files. But those operations were still a little like sticky notes:
the runtime remembered enough to finish the immediate conversation, but the
work was not yet modeled as durable product state.

Now Nimbus keeps a small notebook.

When a user asks Nimbus to do something important, Nimbus writes down:

- who asked
- which workspace they belong to
- which session they are in
- what action was proposed
- whether it is waiting, running, verified, failed, or done
- what evidence proves the result

Deletes and uploads now go through the same basic lifecycle:

```text
user asks
  -> Nimbus creates an action
  -> policy decides what is allowed
  -> runtime executes it
  -> verifier creates an artifact
  -> action becomes succeeded or failed
```

The important part is that the model does not get to say "done" by itself. The
runtime records state, calls storage, and leaves a report behind.

## What Changed

The core runtime now has the first buildable pieces from Agent Platform Design
1.0:

| Piece | What it does |
| --- | --- |
| `TenantIdentity` | Defines the workspace/tenant boundary for durable state. |
| `VerifiedActor` | Represents the user or service principal who initiated work. |
| `ObjectRef` | Points to the exact cloud object being acted on. |
| `Action` | Durable unit of side-effecting work with typed input/result/failure payloads. |
| `ActionStatus` | State machine for action progress and failure. |
| `SessionEvent` | Ordered session fact for replay and future realtime clients. |
| `Artifact` | Evidence or work product created by an action. |
| `FileActionStore` | SQLite-backed action ledger with idempotent creation and compare-and-set transitions. |
| `FileSessionEventStore` | SQLite-backed ordered event log. |
| `FileArtifactStore` | SQLite-backed artifact store. |
| `authorize_action()` | Small fail-closed policy function. |

The wrapper response also grew additive fields:

```json
{
  "actions": [
    {
      "action_id": "act_...",
      "kind": "upload_attachment",
      "status": "succeeded",
      "target": {
        "provider": "s3",
        "container": "team-bucket",
        "object_name": "finance/april/report.txt"
      }
    }
  ],
  "artifacts": [
    {
      "artifact_id": "art_...",
      "kind": "upload_report",
      "action_id": "act_...",
      "payload": {
        "remote_path": "finance/april/report.txt",
        "size_bytes": 16
      }
    }
  ]
}
```

Existing clients can ignore these fields. Future Slack, CLI, and web clients
can render them as timelines, status cards, or audit views.

## Why We Built It This Way

The tempting version of an AI storage product is simple:

```text
prompt -> model -> tool call -> reply
```

That is fine for demos. It is not enough for a system that deletes, uploads,
or reorganizes business files. The real contract is:

```text
intent -> verified actor -> policy -> durable action -> execution -> proof
```

This change moves Nimbus toward that contract.

### Lessons From Real Incidents

The cleanup pass after the first implementation borrowed three practical
lessons from production systems:

- [Stripe idempotency](https://docs.stripe.com/api/idempotent_requests): replay
  only the same request parameters for the same key; mismatched parameters are a
  caller bug, not a new operation.
- [Slack's 2-22-22 incident](https://slack.engineering/slacks-incident-on-2-22-22/):
  retries and slow requests can amplify overload, so Nimbus keeps per-user rate
  limits and makes retries converge through idempotency instead of duplicating
  work.
- [GitLab's database outage](https://about.gitlab.com/blog/postmortem-of-database-outage-of-january-31/):
  recovery procedures matter more than heroic care. Nimbus uses a transactional
  ledger for action state plus events so a midpoint failure rolls back cleanly.

### Actions Are The Unit Of Work

A chat message is not a safe place to store operational truth. Messages are for
humans. Actions are for the system.

An action has an ID, tenant, actor, target, status, input, result, failure, and
idempotency key. That gives the runtime something stable to retry, inspect,
render, or hand to a future worker queue.

### Events Are The Story

Every important action transition appends a session event:

```text
action_created
action_authorized
action_started
verification_started
artifact_created
action_completed
```

This is not a full event-sourcing platform yet. It is the smallest useful log:
ordered facts per session. That is enough for replay tests, support timelines,
and future realtime clients.

### Artifacts Are Proof

The model can summarize. The runtime must prove.

Upload actions now create `upload_report` artifacts. Delete actions create
`delete_report` artifacts. The payloads are intentionally small and structured:
remote path, byte count, digest, delete result, and the action that produced
the report.

This matches the product feeling we want: Nimbus should not merely sound
confident. It should show its receipts.

### Policy Is Outside The Model

The first policy layer is deliberately boring:

- delete requires confirmation
- upload is allowed only inside the pinned container and size limit
- read-only action kinds are allowed
- unknown or cross-tenant work is denied

No DSL, no OPA, no rule engine. The point is to establish the boundary:
the model may propose, but policy decides.

### SQLite Is The First Durable Kernel

The current deployment is still a single-node/simple-topology system, but JSON
files were the wrong primitive for action ledgers. They made the happy path easy
and the crash path fuzzy. The local store now uses SQLite from the Python
standard library: one file, no service dependency, real unique constraints, and
one transaction for action state plus the matching event.

The important design choice is the shape:

```text
ActionStore
SessionEventStore
ArtifactStore
```

Those can graduate from local SQLite to Postgres without changing the runtime's
public model. Postgres is the next move when multiple writable app instances or
cross-region serving become real.

This is the lesson from the postmortems we studied: idempotency is not a cache,
and durability is not "we wrote most of the files." The commit boundary has to
be a single source of truth.

## Walkthrough: Delete

User:

```text
delete reports/2024/old.csv
```

Runtime:

```text
create Action(status=awaiting_confirmation)
append action_created
return confirmation_required
```

User:

```text
yes, delete reports/2024/old.csv
```

Runtime:

```text
verify same tenant/session/actor/target
transition awaiting_confirmation -> authorized
transition authorized -> executing
call CloudStorageClient.delete_file()
transition executing -> verifying
create delete_report artifact
transition verifying -> succeeded
return reply with action and artifact summaries
```

If another user confirms, or the target text does not match exactly, Nimbus
fails closed and does not call storage.

## Walkthrough: Upload

User:

```text
upload these files to finance/april
```

Runtime:

```text
create Action(status=authorized)
apply policy
decode inline attachment bytes
verify declared size and optional SHA-256
transition authorized -> executing
write temp file
call CloudStorageClient.upload_file()
delete temp file
transition executing -> verifying
create upload_report artifact
transition verifying -> succeeded
return reply with action and artifact summaries
```

Partial success is now clearer: successful files have succeeded actions and
artifacts; failed files stay explicit in the user response and action state
when an action was created.

Malformed attachment bytes also create a terminal failed action. That makes bad
client input auditable instead of disappearing as a response-only error.

## Idempotency

Wrapper idempotency follows the Stripe-shaped rule: the key is scoped to the
verified actor and conversation, and the request parameters are fingerprinted.
A duplicate request with the same key and same fingerprint replays the cached
response. The same key with different parameters returns a conflict instead of
quietly performing a second action.

Runtime action idempotency is also actor-scoped. The stored action key is a
SHA-256 digest over tenant, actor, conversation, caller request key, action kind,
and target. That prevents two users in one workspace from colliding on a
human-readable idempotency key.

## What This Unlocks

This is still a small modular-monolith implementation, but it creates the
right pressure lines:

- Slack can render action cards from `actions`.
- Web can render a session timeline from `SessionEvent`.
- CLI can inspect exact action state.
- Future workers can load action IDs and transition by compare-and-set.
- Future Postgres tables have obvious shapes.
- Future deterministic simulation can replay events and assert invariants.

The next implementation slices are now much less mysterious:

1. Add `GET /ai/sessions/{id}/events`.
2. Add `GET /ai/actions/{id}`.
3. Add artifact retrieval for larger reports.
4. Add a `SessionProjection` that rebuilds timeline state from events.
5. Move read-only tool calls into the same event/action vocabulary where useful.

## Design Tradeoff

This is intentionally not Temporal, Kafka, Postgres, or Kubernetes.

Those tools may become right later. Today the bottleneck is not infrastructure.
The bottleneck is whether Nimbus has the correct smallest unit of work. The
answer now is: yes, an action with events and artifacts.
