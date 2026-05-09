# System Design Map

This page maps the concept section to root `SYSTEM_DESIGN.md` and the
{doc}`../complete-system-design` Sphinx companion. It replaces the old reading
map for the three draft agent-platform design passes.

## The Seven-Line System

Nimbus should stay explainable in seven lines:

```text
Clients submit storage requests or AI runtime turns.
Contracts define the public behavior.
Adapters translate transports without owning domain semantics.
The runtime turns user intent into authorized actions.
Actions model side effects.
Events and artifacts prove what happened.
Tests and telemetry keep the invariants honest.
```

Most glossary terms exist to make one of those lines precise.

## Main Design Axes

Storage axis
: `cloud_storage_api` defines the provider-neutral contract.
  `aws_client_impl` implements it with S3. `aws_client_service` exposes it over
  HTTP. The generated client and `aws_client_adapter` turn that HTTP API back
  into the same Python contract.

AI runtime axis
: `ai_client_api` defines the model-provider-neutral contract.
  `openrouter_ai_client_impl` implements it. `nimbus_runtime` owns session,
  action, artifact, confirmation, and telemetry behavior. `ai_server` exposes
  the wrapper boundary.

Runtime kernel
: The runtime should converge around tenant, actor, session, operation, event,
  action, artifact, policy decision, runtime spec, and clock.

Reliability model
: The system should preserve tenant isolation, same-actor destructive
  authorization, idempotent duplicate handling, monotonic action transitions,
  durable-before-visible success, bounded resource growth, and fail-closed
  behavior under malformed input or unknown state.

Scale model
: Start as a modular monolith with SQLite. Promote to Postgres, Valkey, queues,
  workers, or workflow engines only when the access pattern requires them.

## Where To Read Next

- Root `SYSTEM_DESIGN.md` for the canonical architecture and roadmap.
- {doc}`../complete-system-design` for the Sphinx companion view.
- {doc}`platform-glossary` for terminology.
- {doc}`reliability-properties` for invariants and testable properties.
- {doc}`testing-techniques` for the current and target test stack.
- {doc}`deterministic-simulation-testing` for the future runtime reliability
  harness.
