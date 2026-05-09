# nimbus-protocol

`nimbus_protocol` contains dependency-light protocol objects shared by the
Nimbus runtime, CLI, HTTP server, and chat adapters.

This package owns stable request, response, stream, approval, permission, and
error shapes. It does not own provider clients, stores, policy evaluation, tool
execution, HTTP routing, Slack handling, or CLI rendering.

The design rule is simple:

```text
Protocol objects describe what crossed a boundary.
Runtime objects decide what the system is allowed to do.
Adapters render or transport protocol objects.
```

## Current Surface

| Object | Purpose |
| --- | --- |
| `ChatTurnInput`, `ChatTurnResult` | Normalized runtime turn request/response |
| `NimbusEvent`, `StreamEventType` | Ordered live/replay event stream |
| `SessionRef` | External readable ID plus internal UUID for clients |
| `ApprovalRequest`, `ApprovalDecision` | Typed approval flow building blocks |
| `PermissionRule` | Durable client-side or actor-side permission intent |
| `NimbusError` | Three presentations: internal, protocol, and display |

`nimbus_runtime.models` re-exports the turn DTOs while the codebase migrates to
this package as the canonical protocol boundary.
