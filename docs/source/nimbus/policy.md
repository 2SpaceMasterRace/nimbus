# Nimbus Policy Engine

Feature 12 moves Nimbus authorization from branch-local checks toward a
versioned, testable policy contract. The model may propose work, but policy
decides whether the runtime may execute, deny, or require approval.

## Contract

The runtime exports:

| Primitive | Purpose |
| --- | --- |
| `PolicyConfig` | Versioned policy settings such as default scope, approval expiry, and preview thresholds. |
| `PolicyGrant` | Verified actor role grant with optional channel and expiry. |
| `PolicyContext` | Runtime inputs: pinned container, upload limit, current channel, requested scope, grants, and config. |
| `PolicyDecisionRecord` | Durable decision evidence stored on actions. |
| `authorize_action_with_record()` | Authorizes one action and returns the durable record. |
| `approval_actor_ids_for_action()` | Computes who may approve a risky action. |

Every action can carry the decision record that allowed, denied, or routed it
to approval:

```text
tenant_id
actor_id
operation
target
decision
reason
policy_version
created_at
```

## Current Rules

| Scenario | Decision |
| --- | --- |
| Actor tenant differs from action tenant | `deny` |
| Target container differs from pinned runtime container | `deny` |
| Delete file | `requires_approval` |
| Workspace-scope work without live workspace-admin grant | `requires_admin_grant` |
| Bounded upload to pinned container | `allow` |
| Oversized or malformed upload | `deny` |

Delete approval actors are computed from policy context. The original actor is
always allowed; active delegated admins and matching channel owners may also be
allowed. Expired grants are ignored.

## Design Notes

Policy state must come from verified adapters or durable admin state, not from
model output. Slack and CLI should eventually populate `PolicyContext` from
workspace grants, channel ownership, and user identity. The first slice keeps
those grants injectable so tests can cover admin delegation without coupling
the runtime kernel to the unfinished Slack bridge.

The action ledger persists `policy_decision_json` in both SQLite and Postgres.
This makes the audit story explicit: operators can inspect an action and see
which versioned policy decision led to that state.
