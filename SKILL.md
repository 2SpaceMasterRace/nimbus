---
name: nimbus-agent-serving-l7
description: Self-contained operating guide for rigorous Nimbus system design and implementation work inspired by Motus/LithosAI agent serving, continuous agent learning, and L7 production design discipline. Use when asked to evolve Nimbus toward production-grade agent serving, observability, evaluation, learning loops, model orchestration, sandboxes, tools, MCP, or durable runtime behavior.
---

# Nimbus Agent Serving L7 Skill

This skill is a self-contained briefing for a future Codex session working in
`/Users/nanodijkstra/Work/ospsd-team-2`.

It exists so the next chat can start from the same shared context without
performing web research. It distills the local Nimbus repository onboarding, the
Motus/LithosAI research already completed, the user collaboration contract, and
the engineering skills the user wants Codex to apply to itself.

This file intentionally favors completeness over normal skill brevity. Standard
Codex skills should usually be short and split detailed references into separate
files, but this project-specific handoff has a different requirement: a future
chat should be able to read one `SKILL.md` and operate without reopening the web.

## Source Material Already Distilled

Do not browse these by default in a future chat. Their relevant content is
summarized inside this skill.

Primary Nimbus local sources:

- Root `AGENTS.md` for repository-wide agent rules.
- Root `README.md`, `CONTRIBUTING.md`, `pyproject.toml`, `justfile`.
- `SYSTEM_DESIGN.md`, `DESIGN.md`, and maintained Sphinx docs.
- `src/nimbus_runtime`, `src/ai_server`, `src/nimbus_protocol`,
  `src/openrouter_ai_client_impl`, `src/nimbus_cli`, and related tests/readmes
  as the current implementation surface for runtime and adapter behavior.

Motus/LithosAI sources studied:

- `https://github.com/lithos-ai/motus`
- `https://www.lithosai.com/`
- `https://docs.motus.lithosai.com/getting-started/quickstart`
- `https://docs.motus.lithosai.com/architecture/overview`
- `https://docs.motus.lithosai.com/guides/serving`
- `https://docs.motus.lithosai.com/guides/tracing`
- `https://docs.motus.lithosai.com/cloud/sandbox`
- `https://www.lithosai.com/blog/motus-agent-tracing`
- `https://www.lithosai.com/blog/learning-agents`
- `https://www.lithosai.com/blog/open-source-2026`

The Motus repository was also cloned and inspected locally during the research
pass at commit `35be39fc5ca947f49615833dba1ebfd762eb4cf8`. Important files
included `README.md`, `AGENTS.md`, `pyproject.toml`,
`src/motus/runtime/agent_runtime.py`, `agent_task.py`, `agent_future.py`,
`task_instance.py`, `serve/server.py`, `serve/session.py`, `serve/worker.py`,
`agent/react_agent.py`, `agent/base_agent.py`, `tools/core/tool.py`,
`tools/core/function_tool.py`, `models/base.py`, `memory/compaction_base.py`,
and tracing code.

Matt Pocock skills studied:

- `https://github.com/mattpocock/skills/tree/main/skills/engineering`
- `https://github.com/mattpocock/skills/blob/main/skills/productivity/grill-me/SKILL.md`
- `https://github.com/mattpocock/skills/blob/main/skills/engineering/to-prd/SKILL.md`

The skills repository was cloned and inspected locally during the research pass
at commit `b8be62ffacb0118fa3eaa29a0923c87c8c11985c`. Important skill files
included `engineering/README.md`, `diagnose`, `grill-with-docs`,
`improve-codebase-architecture`, `tdd`, `to-issues`, `prototype`, `triage`,
`zoom-out`, `setup-matt-pocock-skills`, `LANGUAGE.md`, plus `grill-me` and
`to-prd`.

Use this skill when the user asks to design or implement anything related to:

- agent serving infrastructure;
- continuous agent learning or optimization;
- model orchestration across providers;
- production traces, spans, evaluations, and feedback loops;
- task graphs, durable workflows, worker leases, tool execution, or sandboxes;
- MCP/tool harnesses;
- AI/runtime kernel changes in Nimbus;
- Slack/CLI/HTTP agent workflows;
- L7-level system design or rigorous implementation planning.

## Non-Negotiable Operating Posture

The user will not be doing coding. Codex owns the engineering work end to end:
code reading, design, self-review, implementation, tests, docs, and verification.

Do not hand the user chores that Codex can do locally. Ask the user only when a
decision is genuinely product-defining, unsafe to infer, externally dependent,
or explicitly required by `AGENTS.md`.

The user expects a rigorous L7 engineer, not a pattern-matching assistant.
Challenge every requested design against codebase reality, state ownership,
hidden contracts, scale physics, operational cost, failure modes, and simpler
alternatives.

Never start from technology names. Start from the contract, state, invariants,
math, and failure model.

## Priority Order

Follow this priority order for all future work:

1. The latest explicit user instruction.
2. The root `AGENTS.md`.
3. The local code and maintained docs.
4. This `SKILL.md`.
5. External docs or web research, only if the user explicitly asks to refresh or
   the system/developer instructions require browsing for current facts.

This file is intended to remove the need for web search. It contains the
relevant Motus/LithosAI and Matt Pocock skills context. Still read local code
before editing, because the codebase may have changed.

## First Five Minutes In A New Chat

When a future chat says "go over this SKILL.md", do this:

1. Read this file.
2. Read root `AGENTS.md`.
3. Check `git status --short` and identify existing user changes. Do not revert
   them.
4. Read only the local files relevant to the user's actual next command.
5. Before non-trivial work, write the working system model and 95% agreement
   checkpoint required by `AGENTS.md`.

Do not perform web search by default. This skill already captures the external
source material that matters for this project direction.

## Repository Working Model: Nimbus

Nimbus is a Python 3.12+ workspace for provider-agnostic cloud storage and an
AI/runtime kernel for guarded storage actions. The core architectural idea is:

```text
model proposes
runtime authorizes
actions execute
verifiers produce evidence
events tell the audit story
adapters render transport-specific UX
```

The repo has two connected axes:

- Storage vertical: provider-neutral object storage through `CloudStorageClient`.
- AI/runtime vertical: provider-neutral model calls plus guarded actions,
  sessions, events, tasks, artifacts, approvals, and transport adapters.

Key packages:

- `cloud_storage_api` is external. It defines `CloudStorageClient`, domain
  object/result types, and storage exceptions. It is not vendored locally.
- `src/aws_client_impl/` contains the boto3-backed S3 implementation.
- `src/aws_client_service/` contains the FastAPI storage service and mounts the
  AI router under `/ai`.
- `src/aws_client_adapter/` implements `CloudStorageClient` over the generated
  HTTP client.
- `src/aws_s3_cloud_storage_service_client/` is generated OpenAPI client code.
  Do not hand-edit it.
- `src/ai_client_api/` contains the provider-neutral `AIClient`,
  `Conversation`, `Tool`, `AIResponse`, stream events, and AI exceptions.
- `src/openrouter_ai_client_impl/` contains the OpenRouter implementation,
  pydantic-ai agent loop, provider failure mapping, streaming, cost estimation,
  and cloud-storage tool bindings.
- `src/nimbus_protocol/` contains dependency-light DTOs at channel boundaries:
  chat turns, events, approvals, permissions, error presentations.
- `src/nimbus_runtime/` is the product kernel: sessions, locks, events,
  actions, artifacts, approvals, policy, task ledger, worker leases, attachment
  handling, ACL-aware search projections, telemetry, and verification.
- `src/ai_server/` is the HTTP adapter around `nimbus_runtime`: HMAC auth,
  replay protection, rate limiting, idempotency, readiness, and route models.
- `src/nimbus_cli/` is the terminal adapter.
- `src/nimbus_slack/` is the Slack adapter and workspace control plane. Treat it
  as in progress; do not modify it unless the user asks for Slack work or the
  current feature necessarily touches Slack.
- `tests/`, `src/*/tests/`, `fuzz/`, `docs/`, `render.yaml`, `Dockerfile`,
  `scripts/`, and `justfile` provide test, docs, deployment, and ops surfaces.

Dependency direction:

```text
adapters -> protocol DTOs -> runtime kernel -> AI/storage contracts -> provider implementations
```

Do not let provider SDK objects, generated-client internals, or raw Slack
payloads leak into the runtime policy layer.

## Nimbus Design Intent

Program to contracts:

- Program to `CloudStorageClient`, not S3.
- Program to `AIClient`, not OpenRouter.
- Each implementation package provides `get_client_impl()`.
- There is no global dependency-injection registry.

Keep channel adapters thin:

- HTTP, Slack, and CLI adapters parse, authenticate, normalize, and render.
- Shared behavior belongs in `nimbus_runtime`.
- Shared boundary vocabulary belongs in `nimbus_protocol`.

Use durable product primitives:

- Tenant identity.
- Verified actor.
- Session event.
- Task.
- Action.
- Policy decision record.
- Approval.
- Artifact.
- Worker lease.
- Projection.
- Store.
- Verifier.

The naked production topology is one process plus one durable store. Local
JSON/SQLite-style state is acceptable for development. Render production uses
Postgres when state must survive restarts or coordinate cross-process authority.

Do not add Redis, a queue, a new database, another service, or generated
protocol machinery without naming the exact requirement and number that breaks
the one-process/one-store version.

## Nimbus Invariants To Preserve

These are the important invariants from the local design docs:

- A model cannot directly mutate storage.
- Every mutation is represented by an action.
- Every action has tenant, actor, target, policy decision, status, and
  idempotency key.
- Destructive actions require approval.
- Approval is bound to tenant, actor, task/action, exact target, and expiry.
- Wrong-actor approval fails closed.
- Expired approval fails closed.
- Success is not reported until verifier evidence is durable.
- Duplicate Slack/wrapper events converge on one result or one durable action.
- Tenant A cannot read, approve, or mutate Tenant B state.
- Search results are ACL-filtered before ranking or answering.
- Cancellation prevents future side effects but preserves audit history.
- Provider ambiguity is represented explicitly, not hidden as success.
- Long-running loops need pagination, retry bounds, and cancellation checks.

Future aspirational invariants already named by the repo:

- Manifest artifacts are verifiable against live storage on demand.
- Multiple candidate plans may be proposed; at most one is approved per task.
- Workspace state at a past timestamp is reconstructible from events alone.

## Current Nimbus Runtime Shape

`NimbusRuntime.run_chat_turn()` serializes per conversation through
`get_session_lock()`, then routes direct runtime-managed cases before model
fallback:

- pending mutation confirmation;
- delete confirmation;
- new delete proposal;
- attachment upload;
- model-backed reply using wrapper tools.

`stream_chat_turn()` currently handles model-backed streaming and persists
provider events to the session event store. Direct runtime actions still use
`run_chat_turn()`.

`ai_server.chat_turn()` adds the HTTP envelope:

- signed wrapper auth;
- request freshness and replay protection;
- per-principal token bucket rate limiting;
- idempotency cache and in-flight claim;
- conversion to `ChatTurnInput`;
- conversion from runtime result to `ChatTurnResponse`;
- known AI/runtime exception to HTTP error mapping.

`TaskWorkerLoop` is a durable worker primitive:

- bounded tenant-scoped scans;
- short-lived task leases;
- heartbeat;
- lost-lease handling;
- handler-owned state transitions;
- lease is coordination state, not authorization.

`ChannelBackupWorkflow` is the model pattern for deterministic background work:

- scan bounded source listing;
- diff against manifest;
- upload/dedupe;
- verify byte-level evidence;
- record artifacts;
- transition task to done or failed.

This is the pattern to emulate for stable, known procedures.

## Motus/LithosAI Distillation

The user asked Codex to study Motus and LithosAI deeply. The following is the
self-contained distilled model from that research.

### Motus Product Claim

Motus is an open-source agent-serving project. Its public promise is:

```text
higher capability
lower cost
faster agents
self-managed or cloud deployment
same code from local serve to cloud deploy
```

It takes a "no-framework" posture: users can write plain Python, Motus-native
agents, OpenAI Agents SDK agents, Anthropic SDK tool runners, or Google ADK
agents, then serve or deploy them with the same operational substrate.

### Motus Programming Models

Motus has two programming models on one runtime:

1. `ReActAgent`: use when the problem is open-ended and the model should decide
   the next step. The loop is model call -> optional tool calls -> tool results
   -> next model call -> final answer.
2. `Workflow`: use when the procedure is known and should be stable. Decorate
   plain Python functions with `@agent_task`; Motus infers data-flow
   dependencies and runs independent work in parallel.

This distinction matters for Nimbus:

- ReAct-style model agency is appropriate for exploratory triage, summarization,
  classification, or proposal generation.
- Workflow-style deterministic recipes are appropriate for storage operations
  where safety, policy, verifiers, idempotency, and evidence matter.

When in doubt for Nimbus, prefer deterministic workflow behind typed runtime
interfaces. Let the model propose, not own the side effect.

### Motus Task Runtime

Motus source at commit `35be39f` uses:

- `GraphScheduler`: in-process scheduler holding task state, dependency edges,
  futures, hooks, and tracer.
- `AgentTaskDefinition`: returned by `@agent_task`, captures default retry,
  timeout, retry delay, task type, and `num_returns`.
- `AgentFuture`: lazy future with operator overloading. Some operations extend
  the graph without blocking; sync barrier operations resolve the future.
- `TaskInstance`: concrete unit of work with status, prerequisites, policy,
  retry count, task type, result futures, and creation stack.
- Hooks: task lifecycle events for tracing and instrumentation.

Motus runtime strengths:

- no explicit DAG wiring;
- natural Python data-flow syntax;
- automatic parallelism for independent tasks;
- retry/timeout policy per task or call;
- trace capture at model/tool/task level;
- uniform tool/model task observability.

Motus runtime limitations for Nimbus:

- core graph scheduling is in-process, not by itself durable;
- state lives in scheduler memory;
- retries are task-runtime retries, not necessarily side-effect-safe
  action-level retries;
- it does not replace Nimbus's durable action ledger, approval store, artifact
  store, policy store, or idempotency guarantees.

Use Motus as inspiration for ergonomics and tracing, not as a substitute for
Nimbus's durable kernel.

### Motus Serving

Self-managed `motus serve` exposes an agent as a FastAPI session API:

- create/list/get/delete sessions;
- send messages;
- long-poll or stream status;
- resume human-in-the-loop interrupts;
- webhooks;
- health endpoint with worker counts.

Important implementation detail:

- Each message spawns a fresh worker subprocess.
- A semaphore limits concurrent workers.
- Session state is held in memory.
- Concurrent sends to a running/interrupted session return conflict.
- Timeout kills the worker process and transitions the session to error.
- Sessions do not persist across server restart in the self-managed local
  implementation.

Implication for Nimbus:

- Process-per-turn isolation is interesting for dangerous agent code or
  untrusted tool execution.
- In-memory sessions are not enough for Nimbus production state.
- Nimbus already has stronger durable state expectations.
- If adopting a similar worker model, use it behind a durable task/action/store
  contract, not as the source of truth.

### Motus Human In The Loop

Motus HITL uses an interrupt primitive:

```text
agent calls interrupt(payload)
worker pauses
server records pending interrupt
session enters interrupted
client shows payload and collects reply
client posts resume
worker continues exactly where it paused
```

Implication for Nimbus:

- The UX pattern is useful.
- Nimbus should not rely on a paused coroutine as the authoritative approval
  state for destructive work.
- Nimbus approvals must remain durable, actor-bound, target-bound, expiring,
  and fail-closed.

### Motus Tools And Guardrails

Motus tools:

- `@tool` turns Python functions into LLM tools.
- Type annotations and Pydantic models generate JSON schema.
- MCP sessions can be exposed as regular tools.
- Tools can be prefixed, allowlisted, blocklisted, and guarded.
- Tool input/output guardrails can rewrite, normalize, or block values.
- Tool approval can be required before execution.
- Tool calls are traced as task spans.

Implication for Nimbus:

- Strong typed schemas are good.
- Guardrails are useful but are not a replacement for policy decisions.
- For Nimbus, tool schema parsing should produce precise domain types early.
- Destructive or expensive work must become an action transaction, not a tool
  callback hidden inside a model loop.

### Motus MCP

Motus `get_mcp()` can connect stdio or HTTP MCP servers and expose their tools
through the same tool interface. It supports:

- local child process MCP;
- remote HTTP MCP;
- prefixing;
- allowlist/blocklist;
- guardrails;
- per-tool approval.

Implication for Nimbus:

- MCP should be treated as a tool source adapter.
- Raw MCP tool schemas/results must not leak into `nimbus_runtime`.
- Any MCP integration should compile external tool capabilities into Nimbus
  capabilities, policy decisions, actions, artifacts, and events.

### Motus Sandboxes

Motus `Sandbox` is an abstract execution environment:

- local Docker sandbox;
- cloud sandbox;
- local shell for prototyping.

Cloud sandbox model:

- one sandbox per session;
- lazy boot on first use;
- reuse across turns;
- pause after idle time;
- workspace directory persists for session lifetime;
- non-workspace filesystem and background processes do not persist;
- outbound public internet allowed;
- private IP ranges blocked;
- inbound public internet blocked;
- shell commands capped server-side.

Implication for Nimbus:

- Sandboxes are appropriate for untrusted code execution, file processing,
  external CLIs, and agent tools that need isolation.
- A sandbox is not a durable store.
- Anything the product promises to retain must be copied into Nimbus's durable
  artifact/store layer.
- Network policy and workspace persistence semantics are part of the public
  contract if Nimbus exposes sandboxes.

### Motus Memory

Motus memory has:

- `BasicMemory`: append-only in-memory, no compaction.
- `CompactionMemory`: auto-compacts when token threshold is exceeded.
- Clean turn-boundary detection to avoid orphaning tool results.
- Optional JSONL conversation log restore.
- Future `BackgroundMemory` concept for cross-session memory.

Implication for Nimbus:

- Context compaction must preserve semantic turn boundaries:
  user message, assistant+tool calls+all tool results, assistant final answer.
- Summaries are not authoritative state.
- If Nimbus learns long-term facts, separate memory projection from action/event
  truth. Treat it as rebuildable and ACL-filtered.

### Motus Tracing

Motus tracing records structured spans for:

- model calls;
- tool calls;
- task dependencies;
- retries;
- errors;
- sandbox interactions;
- sub-agent actions;
- task transitions.

Trace collection levels:

- disabled;
- basic: task names, timing, parent relationships;
- detailed: full messages, tool args, model outputs.

Local tracing can write a self-contained HTML viewer. Cloud tracing streams the
same span schema with project/build/session identifiers. Local runs do not
upload traces to cloud by default.

Span model:

- model spans: input messages, output, tools available, token usage, cost;
- tool spans: args, return values, exceptions, duration;
- task spans: group model/tool spans into logical work.

Implication for Nimbus:

- Nimbus should converge on one trace/event/evidence vocabulary across local
  development and production.
- Traces are for debugging and learning, but durable events/artifacts are still
  the audit contract for user-visible side effects.
- Detailed traces may contain prompts, arguments, and sensitive data; collection
  level and redaction must be explicit.

### LithosAI Learning Agents

Learning Agents optimize deployed agents continuously from production traffic.
They can propose changes to:

- system prompt;
- tool configuration;
- model selection;
- reasoning flow;
- memory strategy;
- model orchestration.

The optimization loop:

1. Production traces produce feedback signals.
2. The platform constructs datasets:
   - regression sets from previously passing cases;
   - error-driven sets from failures or flagged requests;
   - traffic-sampled sets from real distribution;
   - cost-focused sets from expensive requests.
3. Candidate harness versions are evaluated.
4. Dominated candidates are pruned.
5. Pareto-optimal versions across quality/cost are presented.
6. Developer controls the deployment boundary.

Safety principle:

- Assume most candidates fail.
- A candidate cannot advance if it improves one metric but regresses previously
  passing cases.
- No version ships without explicit approval or preconfigured thresholds.

Implication for Nimbus:

- Learning must be evidence-driven, not prompt tinkering.
- Dataset construction is platform work; optimization is learning-agent work.
- The deployment boundary must be deterministic and auditable.
- Candidate versions need identifiers, configs, evaluation records, metrics,
  artifacts, approval state, and rollback path.

### LithosAI Open Source AI 2026 Thesis

The model frontier moves quickly. Static single-model integrations become stale
in weeks. Closed and open models differ in cost and capability by task type.
No single model dominates every workload.

The durable serving thesis:

- route each step to the cheapest model that clears the bar;
- benchmark new models against production traffic;
- keep cost and quality on a Pareto frontier;
- update harnesses from real traces, not guesses.

Implication for Nimbus:

- Do not hard-code "best model" assumptions into domain logic.
- Model orchestration should be a policy/config surface with versioned
  decisions and measurable outcomes.
- Cost, latency, quality, fallback, and provider outage behavior are product
  contracts, not implementation trivia.

## How To Use Motus Ideas In Nimbus

Do not copy Motus architecture blindly.

Motus gives patterns:

- graph ergonomics;
- process isolation;
- tracing span schema;
- HITL interrupt UX;
- sandbox abstraction;
- typed tool normalization;
- model client unification;
- continuous optimization loop.

Nimbus already has stronger primitives for:

- tenant isolation;
- signed wrapper auth;
- replay/idempotency protection;
- durable actions;
- durable artifacts;
- approvals;
- verifiers;
- task leases;
- Postgres/local store backends;
- operational readiness.

The right synthesis is:

```text
Motus-like serving ergonomics and observability
inside
Nimbus's durable runtime/action/evidence kernel
```

For known procedures, build deterministic Nimbus workflows first. For open-ended
agent behavior, let the model propose or classify work, then lower it into
runtime-owned actions and verifiers.

## L7 System Design Gate

For non-trivial architecture or implementation, write this before coding:

```text
1. What is the naked one-server / one-database version?
2. What exact requirement breaks that naked version?
3. What number proves it breaks: QPS, writes/sec, bytes/sec, objects, tenants,
   latency, memory, disk, or cost?
4. What is the first bottleneck: CPU, disk, WAL, hot key, lock, network hop,
   provider limit, cache miss rate, or human workflow?
5. What is the smallest primitive that removes that bottleneck?
6. What new failure mode does that primitive introduce?
7. How do we observe, test, and roll back that primitive?
```

Never answer "we will add more nodes" without naming:

- what state is shared;
- what must be serialized;
- what idempotency/nonce/session/lock state is process-local;
- what hot keys every node will stampede;
- what network hop moved onto the critical path;
- what happens during partial deploy or slow-node behavior;
- whether one better-shaped node is enough for now.

Never answer "add a cache" without naming:

- key scope;
- value shape and max size;
- TTL/invalidation;
- expected hit rate;
- miss behavior;
- stale-read tolerance;
- stampede protection;
- memory bound;
- outage behavior;
- metrics.

Use the latency equation:

```text
effective latency =
  hit_rate * cache_latency
  + miss_rate * (cache_latency + source_latency)
```

If hit rate is low, the cache can make the system slower and less correct.

## Required Working System Model

Before non-trivial implementation, write the model in this shape:

1. Goal: user-visible or caller-visible behavior change.
2. Non-goals: tempting adjacent work intentionally excluded.
3. Contract: inputs, outputs, errors, ordering, defaults, persistence, metrics,
   compatibility.
4. State: owner, storage, migration, recovery, authoritative source.
5. Failure model: timeout, retry, duplicate delivery, partial success, crash,
   overload, dependency outage, rollback.
6. Design pressure: why one process/one store is enough, or what exact number
   proves it is not.
7. Tests: unit, regression, property, deterministic simulation, BDD/eval,
   integration, e2e, load, smoke as appropriate.
8. Docs and operations: README/Sphinx/env/readiness/telemetry/deployment/
   backup/restore/rollout/rollback updates.

For runtime work, name the kernel concept being touched:

- contract;
- adapter;
- session;
- operation;
- event;
- action;
- artifact;
- policy;
- executor;
- verifier;
- projection;
- store.

If the answer is "route-local glue", re-check whether behavior belongs in
`nimbus_runtime`.

## Self-Grilling Procedure

The user asked Codex to use the Matt Pocock skills on itself. Apply this before
implementation.

Use `grill-me` inwardly:

- Walk the design tree branch by branch.
- Ask one hard question at a time internally.
- If codebase exploration can answer it, explore instead of asking the user.
- For each question, record the recommended answer.
- Only ask the user when the branch cannot be resolved locally and the decision
  affects product behavior, public contract, external configuration, or high
  blast-radius architecture.

Self-grill questions to run:

- What public behavior changes?
- What local contract already governs this behavior?
- Which existing module should own it?
- What state does it create or mutate?
- What is authoritative after restart?
- What happens on duplicate delivery?
- What happens if the provider succeeds but Nimbus crashes before recording?
- What happens if retries overlap?
- What happens if the slow dependency becomes 100x slower?
- What happens under partial failure?
- What must be idempotent?
- What is the exact evidence required before saying success?
- What user-visible degraded response is safe?
- What metric/log/event proves success?
- What metric/log/event proves failure?
- What test will fail if the guarantee is broken?
- What is the rollback path?
- What manual operator action, if any, is required outside the repo?
- What simpler design would work today?
- What number proves the simpler design runs out of room?

Do not expose every internal question to the user unless useful. Summarize the
resolved decisions in the 95% agreement checkpoint.

## PRD Procedure

Use `to-prd` as a synthesis tool when the request is broad, product-like, or
will cross modules.

Do not interview the user by default. Synthesize from current context and local
code understanding.

PRD shape:

```markdown
## Problem Statement

## Solution

## User Stories

1. As a <actor>, I want <feature>, so that <benefit>.

## Implementation Decisions

## Testing Decisions

## Out of Scope

## Further Notes
```

Adaptation for this repo:

- Do not publish an issue unless the user explicitly asks.
- Do not include fragile file paths in user-facing PRDs unless the user wants an
  implementation plan. Use module names and contracts instead.
- For implementation, maintain a private mapping from PRD decisions to concrete
  files after reading code.
- Identify deep modules: small stable interfaces hiding meaningful behavior.
- Prefer vertical slices, not horizontal layer-by-layer work.

## TDD Procedure

Use behavior-first, vertical-slice TDD when changing behavior.

Correct loop:

```text
RED: one test for one observable behavior
GREEN: minimal code to pass
REFACTOR: only while green
repeat
```

Avoid horizontal slicing:

```text
bad: write all tests, then all implementation
good: one tracer bullet at a time
```

Good tests:

- exercise public interfaces;
- describe behavior, not implementation;
- survive internal refactors;
- use existing fakes/helpers where appropriate;
- pin contracts and failure modes.

Bad tests:

- mock internal collaborators unnecessarily;
- assert private method calls;
- duplicate implementation logic;
- pass while the product behavior is wrong.

Nimbus-specific test posture:

- For runtime/action/store behavior, prefer deterministic fakes, injected clocks,
  state machines, property tests, or integration tests at store/runtime seams.
- For HTTP wrapper behavior, use FastAPI/TestClient-style route tests with
  signed requests where relevant.
- For provider failures, assert domain exception mapping and stable HTTP
  presentation.
- For destructive actions, test wrong actor, expired approval, duplicate
  delivery, and target mismatch.
- For stateful work, test restart/replay behavior where feasible.

## Diagnose Procedure

For bugs or regressions:

1. Build a fast deterministic feedback loop first.
2. Reproduce the actual user-described failure.
3. Generate 3-5 ranked falsifiable hypotheses.
4. Instrument one hypothesis at a time.
5. Write regression test at the correct seam.
6. Fix.
7. Re-run original repro and regression test.
8. Remove temporary debug instrumentation.
9. State the real cause.

If no valid feedback loop exists, stop and say exactly what is missing. Ask for
artifact/access/permission rather than guessing.

## Architecture Improvement Vocabulary

Use these words precisely:

- Module: anything with an interface and implementation.
- Interface: everything callers must know, including types, invariants, errors,
  ordering, config, and performance. Not just signatures.
- Implementation: code inside a module.
- Seam: where an interface lives; a place behavior can vary without editing in
  place.
- Adapter: concrete thing satisfying an interface at a seam.
- Depth: leverage at the interface. Deep modules hide lots of behavior behind a
  small stable interface.
- Leverage: what callers gain from depth.
- Locality: what maintainers gain when change/bugs/tests concentrate in one
  place.

Deletion test:

- If deleting a module makes complexity vanish, it was likely a pass-through.
- If deleting it spreads complexity across callers, it was earning its keep.

One adapter means a hypothetical seam. Two adapters means a real seam.

## Implementation Slicing

Break broad work into thin vertical slices.

Each slice should:

- deliver a narrow complete path through relevant layers;
- be demoable or verifiable alone;
- include tests;
- avoid broad refactors;
- keep compatibility unless explicitly changed;
- update docs/ops surfaces if behavior changes.

Prefer this order:

1. Contract/types.
2. Deterministic domain/runtime behavior.
3. Store/backend behavior.
4. Adapter/HTTP/CLI/Slack presentation.
5. Observability/readiness/ops hooks.
6. Docs.
7. Broader verification.

But do not build all of one layer at once if a vertical tracer bullet is
possible.

## Patterns To Prefer In Nimbus

### Typed Boundary Parsing

Parse raw inputs once at the boundary into precise domain types or reject them.
Do not scatter `None`/string/dict checks across execution code.

Use:

- dataclasses;
- Pydantic models at transport boundaries;
- enums;
- protocols;
- narrow exception types;
- smart constructors or parser functions.

### Durable Action Transactions

For destructive or expensive work, record:

- actor;
- tenant;
- target;
- policy decision;
- idempotency key;
- status;
- attempt;
- verifier result;
- artifact evidence.

Do not report success until the state transition and evidence are durable.

### Runtime-Owned Tool Safety

The model may propose. The runtime owns:

- tool schema;
- policy decision;
- confirmation state;
- actor/tenant binding;
- idempotency;
- execution boundary;
- verifier;
- artifact;
- fallback response.

### Store Design

Stores should expose contracts that can be satisfied by local SQLite/file
backends and Postgres backends. Keep behavior equivalent across adapters.

When adding store behavior, decide:

- transaction boundary;
- idempotency semantics;
- compare-and-set semantics;
- tenant scoping;
- ordering;
- cleanup/retention;
- migration;
- readiness failure behavior.

### Worker Design

Worker loops should:

- claim through durable leases;
- heartbeat;
- lose lease safely;
- let handlers own workflow-specific task transitions;
- never treat lease ownership as authorization;
- expose bounded batch size and poll interval;
- avoid infinite unbounded loops without cancellation.

### Trace/Event Design

Separate:

- Trace span: debugging/learning detail, may include sensitive prompts/args,
  configurable collection level.
- Runtime event: durable user/audit story, stable protocol projection.
- Artifact: durable evidence supporting a claim.
- Metric: aggregate operational signal.

Do not use traces as the only proof for a user-visible state transition.

## Motus-Inspired Feature Ideas For Nimbus

These are candidate directions, not instructions to implement all at once.

### Agent Trace Substrate

Goal:

- Record model/tool/task spans with parent-child relationships, duration,
  inputs/outputs at configurable collection levels, token/cost data, errors, and
  task/action IDs.

Nimbus adaptation:

- Span schema should reference Nimbus tenant/session/task/action/artifact IDs.
- Detailed payload capture must be redacted or opt-in.
- Local trace viewer can be a dev aid, but production trace retention needs
  explicit policy.
- Runtime events/artifacts remain authoritative.

### Evaluation Dataset Builder

Goal:

- Build datasets from production traffic and durable outcomes:
  regression, error-driven, traffic-sampled, cost-focused.

Nimbus adaptation:

- Dataset rows must reference trace/session/action/artifact evidence.
- Tenant isolation and redaction come before sampling.
- Previously passing cases become regression gates.
- Evaluation results become artifacts or durable records.

### Harness Versioning

Goal:

- Version the agent harness: prompt, tools, model routing, memory policy,
  reasoning settings, guardrails.

Nimbus adaptation:

- Harness versions need stable IDs.
- Chat turns should record harness version.
- Candidate harnesses should run in evaluation before rollout.
- Rollback should select prior version deterministically.

### Model Orchestration Policy

Goal:

- Route each model step to the cheapest/capable provider based on task type,
  cost, latency, outage state, and quality evidence.

Nimbus adaptation:

- Keep orchestration outside domain logic.
- Model choice is a policy decision with metrics.
- Fallback behavior must be observable and tested.
- Provider failures map to AI domain exceptions.

### Sandbox Adapter

Goal:

- Provide safe execution for untrusted code, file processing, or external CLI
  tools.

Nimbus adaptation:

- Sandbox is an adapter behind a runtime interface.
- Workspace persistence is not product durability.
- Private network egress policy must be explicit.
- Outputs that matter become artifacts.
- Dangerous commands require policy and approval if user-impacting.

### MCP Capability Adapter

Goal:

- Connect external MCP tool sources without leaking them into runtime policy.

Nimbus adaptation:

- Discover MCP tools through an adapter.
- Compile allowlisted tools into Nimbus capabilities.
- Policy and action ledger wrap side effects.
- Store raw MCP metadata only if needed and safe.

### Workflow Graph Ergonomics

Goal:

- Improve developer ergonomics for deterministic workflows, potentially with a
  task graph abstraction.

Nimbus adaptation:

- Graph nodes must not bypass durable task/action/event/artifact semantics.
- Retried nodes must be idempotent or have compensating evidence.
- In-process graph scheduling is okay for local orchestration but not as the
  production source of truth for long-running side effects.

## Explicit Anti-Patterns

Avoid these:

- Adding Redis/queues/workers because "agents need scale" without numbers.
- Letting model tool calls directly mutate storage.
- Treating prompt text as policy.
- Hiding approval state in a model conversation.
- Treating trace logs as audit evidence.
- Returning success before durable verifier artifacts exist.
- Using cache as a reflex.
- Splitting implementation into horizontal layers that are not independently
  verifiable.
- Adding interfaces with only one adapter unless the seam is imminent and
  justified.
- Leaking provider SDK exceptions beyond contract packages.
- Coercing malformed wrapper input into user content.
- Trusting declared file sizes/digests when actual bytes are available.
- Ignoring readiness, rollback, backup/restore, telemetry, and docs.

## Verification Commands And Tooling

Canonical commands from this repo:

```bash
uv sync --all-packages
uv run pytest src/ -q
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict .
uv run sphinx-build docs/source docs/build/html
just test
just lint
just docs-build
just security-redteam
```

Package-focused examples:

```bash
uv run --package ai-server pytest src/ai_server/tests/ -q
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/ -q
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q
uv run --package nimbus-cli pytest src/nimbus_cli/tests/ -q
uv run --package nimbus-slack pytest src/nimbus_slack/tests/ -q
```

Pick targeted checks first, then broaden according to blast radius.

Do not bypass lint/type/tests unless impossible or explicitly requested.

## Documentation And Ops Closeout

If behavior, commands, architecture, configuration, or public contracts change,
update relevant docs:

- root `README.md`;
- `AGENTS.md` Project Snapshot if package/route/state/deployment responsibility
  changes;
- Sphinx docs under `docs/source/`;
- package READMEs;
- deployment/operations notes;
- examples or smoke scripts.

Before handing off production-adjacent work, answer:

- How does `/ready` behave?
- What metric/log/dashboard proves success?
- What metric/log/dashboard proves failure?
- What migration is needed?
- What rollback is needed?
- What secret/config changed?
- What backup/restore path protects state?
- What alert would wake the right operator?
- What manual external console step is required, if any?

If manual configuration outside the repo is required, call it out early with
exact page/setting/value.

## User Communication Style

The user wants rigor and autonomy.

During work:

- Give short progress updates.
- Explain what context is being gathered and what it changes.
- Do not stop at proposals when implementation is requested.
- Do not make the user code.
- Do not ask broad "what do you want?" questions when local evidence can answer.
- Ask targeted questions only for real decisions.

Before non-trivial implementation, provide the explicit 95% agreement checkpoint
from `AGENTS.md`.

After completion:

- Summarize what changed.
- Summarize the contract strengthened or changed.
- List verification performed.
- Name docs/ops changes.
- Name remaining risks.

## Final Mental Model

Nimbus should become a proof-carrying storage agent runtime, not a generic agent
framework clone.

The Motus/Lithos lessons to carry forward are:

- agents need serving infrastructure, not just prompts;
- traces are the substrate for debugging and learning;
- workflow graphs are powerful when the procedure is known;
- ReAct loops are useful for exploration, but dangerous for side effects;
- model orchestration must track cost, latency, and quality continuously;
- learning loops require datasets, regression gates, candidate versions, and
  explicit deployment control;
- sandboxes isolate execution but do not replace durable product state.

The Nimbus-specific answer is:

```text
thin adapters
typed protocol
durable runtime kernel
policy-owned side effects
verifier-backed artifacts
trace-informed learning
measured model orchestration
boring operational recovery
```

When implementing, make the smallest production-credible slice that strengthens
that story.
