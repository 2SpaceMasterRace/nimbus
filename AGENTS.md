# Agent Development Guide

Repository-wide guidance for coding agents. Explicit user instructions override this file. See [agents.md](https://agents.md/) for the format.

---

## Highest-Priority Rules

- Read the relevant code before editing. Do not guess from names alone.
- Before non-trivial implementation, write down the working system model: goals, public contract, invariants, failure modes, dependencies, and verification plan.
- Treat failures as the default case, not edge cases. Design timeouts, retries, idempotency, backpressure, and observability intentionally.
- For networked, AI, storage, or stateful work, name the runtime kernel concept being touched before coding: contract, adapter, session, operation, event, action, artifact, policy, executor, verifier, projection, or store.
- Be wary of the scale, complexity, and maturity of this codebase when making changes. Build an accurate mental model before editing so local fixes do not damage system-level design.
- Develop the correct intuitions and use the correct tools within the limits of the current context window. Ask the user targeted questions whenever needed instead of guessing past uncertainty; after one bounded search for an ambiguous file, doc, or requirement, ask instead of spending a long turn on the wrong target.
- Prefer the smallest correct change that fits the existing design.
- Prefer atomic, focused changes that are easy to review and revert.
- Preserve package boundaries and the dependency-injection pattern.
- Add or update tests when behavior changes.
- Run relevant verification before finishing.
- Strict typing and linting are part of the normal dev loop, not cleanup at the end.
- Do not bypass tests, lint, or type checks unless the user explicitly asks or the project tooling makes a check impossible.
- Leave unrelated user changes alone.
- Ask before changing public interfaces, adding dependencies, or altering the DI contract.
- Optimize for the long-term health of the project and its users, not just the fastest path to satisfy the immediate request.
- Treat public APIs, abstractions, documentation, and examples as part of the product; optimize for long-term clarity, compatibility, and developer experience, not just the fastest patch.
- Treat observable behavior as API surface, not just signatures: env vars, CLI output, persisted schema, error messages, ordering, and defaults can all create compatibility obligations.
- Validate boundary data against the bytes or structured payload actually received; do not trust caller-declared sizes, lengths, or digests when the real payload is available for verification.
- Reject malformed wrapper or transport input early and explicitly; never coerce `None` or non-string values into apparently valid user content.
- Explicitly translate external SDK transport failures (timeouts, connection resets, connection pool corruption, etc.) into domain errors and regression-test those mappings.
- Treat destructive or expensive work as an action transaction, not as a chat response: actor, tenant, target, policy decision, idempotency key, status, attempt, verification result, and artifact evidence all matter.
- Success is not visible until the state transition and the evidence needed to support it are durable.
- Bound long-lived in-memory registries (rate-limit buckets, nonce caches, session locks, replay caches) so memory growth tracks active workload rather than historical usage.
- Recovery paths and dangerous bulk operations must have exercised safeguards, not just documented playbooks; prefer fail-closed guards and staged rollout hooks for high-blast-radius changes.
- Use modern tooling and concepts deliberately when they reduce risk or cognitive load; do not add fashionable machinery without a concrete need.
- Build systems that would survive a serious production design review: explicit contracts, operational visibility, bounded failure modes, and a believable scale-up path.
- Avoid both toy shortcuts and speculative platformization. Start with the smallest production-credible primitive, document why it is enough today, and state the trigger for introducing heavier infrastructure such as Redis, queues, or additional databases.
- Keep one canonical system-design story. If multiple docs disagree, consolidate the current truth or clearly mark older material as historical.
- Keep `AGENTS.md` and `CONTRIBUTING.md` current when project structure, commands, or conventions materially change.

---

## Project Snapshot

This repository is a Python 3.12+ workspace for a provider-agnostic cloud storage library, a concrete AWS S3 implementation, an HTTP-backed storage service/adapter, a provider-agnostic AI client contract, an OpenRouter implementation, and an AI server for chat-style frontends.

Key packages:

- `cloud_storage_api` (external): abstract `CloudStorageClient` contract, domain types (`ObjectInfo`, `DeleteResult`), and domain exceptions. Installed from git, not vendored locally.
- `src/aws_client_impl/`: `boto3`-backed S3 implementation plus OAuth and token helpers.
- `src/aws_client_service/`: FastAPI service layer exposing the storage contract over HTTP.
- `src/aws_client_adapter/`: adapter that re-implements `CloudStorageClient` by calling the service through the generated client.
- `src/aws_s3_cloud_storage_service_client/`: autogenerated OpenAPI client. Never edit it by hand.
- `src/ai_client_api/`: provider-agnostic AI contract (`AIClient`, `Conversation`, `Tool`, `AIResponse`).
  No external dependencies. All provider-specific code lives in implementation packages.
- `src/openrouter_ai_client_impl/`: OpenRouter-backed AI implementation plus the `nimbus` CLI/REPL.
  Also contains `cloud_storage_tools.py` — the tool bindings that expose `CloudStorageClient`
  operations to the LLM.
- `src/nimbus_runtime/`: transport-neutral runtime kernel for chat orchestration, verified actors, durable actions, session events, artifacts, policy decisions, confirmation flows, attachment upload handling, and runtime telemetry.
- `src/ai_server/`: FastAPI HTTP wrapper around the AI client. Exposes `/chat`,
  `/sessions/{id}/history`, and `DELETE /sessions/{id}` endpoints. Handles per-session
  concurrency via `asyncio.Lock` and per-user rate limiting via a token bucket keyed by
  `user_id`. Session files are persisted atomically (write-tmp-then-rename).
- `tests/` and `src/*/tests/`: pytest suites.
- `docs/`: Sphinx documentation.
- `main.py`: CLI demo entry point.

Design intent:

- Program to `CloudStorageClient`, not to a concrete implementation.
- Program to `AIClient`, not to a concrete model provider.
- Keep dependency direction clean: interface inward, implementations and transports outward.
- Each implementation package provides a `get_client_impl()` factory. There is no global DI registry.
- Treat the repo as two connected axes: the storage vertical and the AI/runtime vertical.
- Keep channel adapters thin. Shared runtime, tool, and integration logic should live in reusable packages rather than Slack/CLI-specific glue.
- Treat `nimbus_runtime` as the product kernel: model proposes, runtime authorizes, actions execute, verifiers produce artifacts, and events tell the audit story.

---

## HW3 Delivery Priorities

- HW3 is about wiring AI and cross-vertical integrations into the existing architecture cleanly. Chat completion itself is not the hard part.
- Integrate other teams through shared, versioned API packages and explicit contracts. Do not vendor or copy-paste their interfaces into this repo.
- The deployed system must be observable and managed as code. Request latency, success rate, and failure rate are required deliverables, not stretch goals.
- Prefer reusable runtime/domain layers over channel-specific logic so the same capability surface can back CLI, Slack, or future frontends.
- If introducing MCP, define host, client, server, transport, auth, lifecycle, and failure boundaries explicitly before coding.
- Complete HW3 work should strengthen the runtime kernel, not merely add another wrapper path. Prefer changes that make actions durable, events replayable, artifacts inspectable, policies explicit, or telemetry actionable.

---

## Default Workflow

1. Read the relevant files and search for existing patterns before editing.
2. Build an accurate working model of the touched subsystem: what owns state, what the transport boundaries are, what invariants exist, and where failure is most likely.
3. For non-trivial work, define the contract first: user-visible behavior, invariants, state ownership, scale assumptions, failure model, timeout/retry/idempotency/backpressure plan, and how the change will be verified.
4. Make the target behavior explicit. For non-trivial changes, encode that behavior in tests.
5. Implement the smallest correct change that satisfies the request.
6. Run targeted verification first, then broader checks when the change warrants it.
7. Finish with a clear summary of what changed, how it was verified, and any remaining risk.

For networked or stateful work, explicitly decide what happens under timeout, partial failure, duplicate delivery, overload, and dependency outage before writing code.

For runtime work, explicitly decide whether the change belongs to the operation layer, action ledger, event log, artifact store, policy module, executor, verifier, projection, or HTTP/CLI adapter. If the answer is "route-local glue," re-check whether the behavior should live in `nimbus_runtime` instead.

When working from an issue, pull request, or GitHub URL, read the linked discussion and directly relevant cross-references before implementing.

Trust but verify user-provided assumptions. Research local precedent and ask targeted questions when scope, constraints, or trade-offs are unclear.

If a requested public-facing change has no clear local precedent or acceptable shape, align on scope before coding instead of guessing.

If a non-trivial public-facing change is underdefined and there is no clear issue, precedent, or acceptable local pattern to follow, help define the issue, proposal, or plan before implementing.

Use `AGENTS.md`, the root `CONTRIBUTING.md`, the root `pyproject.toml`, and existing scripts as the canonical sources for development commands and tool configuration.

Read `plans.md` when you need the broader direction for developer productivity and codebase foundations. The long-term goal is a codebase with strong lifecycle support across source control, environments, code generation, CI, release flow, and runtime tooling, and Nimbus should integrate tightly with those foundations rather than bypassing them.

Read the canonical system design or the closest maintained design doc before broad architecture, runtime, storage, observability, or deployment work. If the maintained design source is missing or contradicted by code, update the docs as part of the change.

Prefer dedicated search, read, and edit tools when available. Otherwise use fast, deterministic commands such as `rg`. Parallelize independent reads, searches, and checks when your tooling allows it.

For tool and dependency upgrades, prefer current official documentation and changelogs over memory or third-party blog posts. Use the latest stable guidance deliberately, not blindly.

---

## L7 System Design Mindset

Use this mindset for architecture, runtime, storage, AI, observability, deployment, or any change whose consequences cross a module boundary.

The job is not to draw an impressive diagram. The job is to find the first lie in the diagram.

### Mandatory Design Gate

For non-trivial design or implementation work, do not start by naming technologies. Start with this gate:

```text
1. What is the naked one-server / one-database version?
2. What exact requirement breaks that naked version?
3. What number proves it breaks: QPS, writes/sec, bytes/sec, objects, tenants, latency, memory, disk, or cost?
4. What is the first bottleneck: CPU, disk, WAL, hot key, lock, network hop, provider limit, cache miss rate, or human workflow?
5. What is the smallest primitive that removes that bottleneck?
6. What new failure mode does that primitive introduce?
7. How do we observe, test, and roll back that primitive?
```

If a change adds a cache, queue, database, worker, service, protocol, or generated contract without passing this gate, it is probably pattern matching rather than engineering.

### Start With The Spec, Not The Stack

Before choosing components, force the problem into a concrete spec:

- Who is the caller and what exact promise do they observe?
- What is the public contract: request, response, persisted state, ordering, defaults, errors, and retries?
- What are the safety invariants that must never break?
- What are the liveness guarantees the system should eventually provide?
- What are the expected read/write ratios, payload sizes, object counts, tenant counts, concurrency, and burst patterns?
- What are the latency goals at p50, p95, and p99?
- What is the retention period for sessions, events, artifacts, logs, and idempotency records?
- What is the cost budget or resource ceiling?
- What does the system do when the caller repeats, reorders, cancels, or times out?
- What consistency does each operation actually need: strong, read-your-writes, monotonic, eventual, or best-effort?
- What can the user safely see when the system is degraded?

If a spec answer is unknown, write the assumption down. Do not smuggle unknowns into implementation as if they were facts.

### Start Naked, Then Add Clothing

Begin with the simplest credible topology:

```text
one process
one durable store
one provider client
one explicit contract
one testable failure model
```

Then add complexity only when a specific requirement proves the naked system is out of room.

Examples:

- Add pagination because object listings can exceed memory, response size, or latency budgets.
- Add an idempotency table because duplicate delivery can overlap in time.
- Add a queue because action execution exceeds request deadlines or retry safety.
- Add Postgres because multiple writable processes need shared durable state.
- Add a cache only when measured or expected hit rate beats the cache's latency, staleness, and operational cost.

Do not start with the final cloud architecture. Start with the smallest system that can be correct, then let constraints pull the design outward.

### Do The Math

Every design must survive back-of-the-envelope math:

- requests per second and writes per second;
- read/write ratio;
- object count per tenant and total object count;
- bytes per upload/download and bytes per second;
- event/action/artifact rows per session and retention period;
- memory needed for worst-case result sets and in-flight requests;
- disk growth for sessions, SQLite, logs, artifacts, and temp files;
- thread, connection, and file-descriptor counts;
- provider rate limits and cost per successful operation;
- p50, p95, p99, and timeout budgets.

Rough math is acceptable. No math is not.

### Cost Every Box

Every new box in a diagram has a bill:

- dollar cost;
- latency cost;
- operational cost;
- cognitive cost;
- failure modes;
- migration cost;
- rollback cost;
- test cost.

Load balancers, queues, caches, extra services, generated clients, workflow engines, Redis, Postgres, object stores, and MCP servers are not free. If the design adds one, state what it buys and what it makes worse.

### Ask The Uncomfortable Questions

For every proposed design, ask:

- Why this primitive and not the simpler one?
- Why this primitive and not the stronger one?
- What breaks first if traffic grows 10x, 100x, or 1000x?
- What is the hottest key, lock, file, queue, table, partition, connection pool, or API dependency?
- If we add horizontal nodes, what coordination overhead, network hop, lock contention, duplicate work, or fan-out pressure did we just add?
- If every app server hits the same row, cache key, session file, tenant bucket, or action ID, did scaling out make the bottleneck worse?
- Does the database bottleneck on write-ahead log contention, index updates, fsync, connection pool saturation, or transaction conflicts?
- What happens if the slow dependency becomes 100x slower?
- What happens if a request succeeds remotely but the local process crashes before recording success?
- What happens if the same logical request arrives twice at the same time?
- What happens if retries arrive out of order?
- What is the cache hit rate? What happens to latency and correctness on misses, stale hits, invalidation, and cache outage?
- What state can be rebuilt, and what state is authoritative?
- What is bounded by active workload, and what accidentally grows with historical traffic?
- How does an operator know which dependency or invariant failed?
- What is the migration trigger for the next heavier piece of infrastructure?

Do not accept "we can scale it later" unless "later" has a specific signal and a safe migration path.

### Challenge Horizontal Scaling

"Add more nodes" is not a design. It is a hypothesis that must survive coordination math.

Before scaling horizontally, ask:

- What state must be shared across nodes?
- What requests must be serialized?
- What idempotency, nonce, session, lock, or rate-limit state is still process-local?
- What hot keys or hot tenants will every node stampede?
- What new network hops are in the critical path?
- What happens during partial deploy, split brain, or one slow node?
- Can one larger or better-tuned node handle the workload more simply for now?

Horizontal scaling is useful when the workload partitions cleanly or shared state is designed intentionally. It is harmful when it multiplies pressure on the same bottleneck.

### Challenge Caches

Do not assume cache equals fast.

For every cache, define:

- key scope;
- value shape and max size;
- TTL and invalidation rule;
- hit-rate assumption;
- miss behavior;
- stale-read tolerance;
- stampede protection;
- memory bound;
- failure behavior when the cache is down;
- observability for hits, misses, evictions, and stale data.

Use the latency equation mentally:

```text
effective latency =
  hit_rate * cache_latency
  + miss_rate * (cache_latency + source_latency)
```

If the hit rate is low, the cache can slow the system down while adding stale data and operational failure modes. Be willing to say "no cache yet."

### Work Top Down And Bottom Up

Top down:

- Start from the product promise, users, SLOs, threat model, and failure budget.
- Derive the public contracts and invariants.
- Derive the state model.
- Derive the minimum infrastructure that can preserve those contracts.

Bottom up:

- Trace one real request byte by byte and row by row.
- Identify every allocation, file write, SDK call, lock, retry, timeout, cache, queue, and event.
- Ask what happens if each step fails before, during, and after the side effect.
- Compare the actual behavior to the promised contract.

If top-down intent and bottom-up behavior disagree, the code wins operationally and the docs are lying. Fix one of them.

### Make Tradeoffs Explicit

Every non-trivial design choice should have a tradeoff record, even if it is only a short paragraph in the doc or PR description:

- Decision: what are we choosing?
- Alternatives: what else could work?
- Why now: what current pressure justifies this choice?
- Cost: what complexity, latency, operational burden, or migration risk does this add?
- Failure mode: how does this choice fail?
- Reversal: how would we back out or graduate from it?
- Trigger: what measurement tells us to revisit it?

Examples:

- SQLite vs Postgres: single-node durable coordination now; Postgres when multiple writable processes or worker fleets need shared state.
- Inline execution vs queue: simpler request path now; queue when action duration or retry behavior threatens HTTP latency/reliability.
- App-server download vs pre-signed URL: simpler contract now; pre-signed data plane when object size or concurrent downloads threaten memory/disk.
- JSON snapshot vs event log: easy human-readable state now; event log when replay, audit, or concurrent projections become correctness requirements.
- Regex command parsing vs typed operation envelope: fast direct path now; typed envelope when multiple clients or confirmations need stable machine contracts.

### Quantify Or Qualify

Avoid vague words unless they are tied to a number or bound:

- "large file" should become a byte limit;
- "many users" should become active users, concurrent users, and requests per second;
- "fast" should become p95/p99 target and timeout budget;
- "reliable" should become explicit failure semantics and alertable signals;
- "safe retry" should become idempotency key scope, state transition, and replay behavior;
- "bounded" should name the bound and what happens when it is exceeded.

When exact numbers are unavailable, use a rough order-of-magnitude estimate and label it as an assumption.

### Design The Architecture Of Failure

Ask "how does this break?" before "how does this work?"

For each dependency and state boundary, define user-visible behavior under:

- cache outage;
- database unavailable;
- disk full;
- provider timeout;
- provider success with lost response;
- duplicate ID or short-code collision;
- generated client/server schema mismatch;
- network link throttled or partitioned;
- partial regional outage;
- deployment with old and new code running together;
- corrupt persisted state;
- clock skew or expired tokens;
- one tenant creating a disproportionate hot spot.

Degraded behavior is still behavior. Name it.

### Treat Failures As Histories

Single exceptions are easy. Distributed bugs are histories. For stateful work, reason in sequences:

```text
create action
return confirmation_required
caller retries original request
confirmation arrives from wrong actor
provider times out after performing side effect
process crashes before artifact write
new process replays session state
```

Design and test the histories that would embarrass the system in production:

- duplicate delivery while the first request is still executing;
- side effect succeeds but response is lost;
- retry arrives after cancellation or expiration;
- stale confirmation points at a newer action;
- provider returns ambiguous timeout;
- projection is stale but action state is correct;
- corrupt state record appears during replay;
- one tenant attempts to reference another tenant's session, action, or artifact.

### Do Not Hide Behind Infrastructure

Infrastructure is not a substitute for a contract.

- Redis does not make idempotency correct unless key scope, TTL, conflict behavior, and write ordering are correct.
- A queue does not make execution reliable unless workers are idempotent and retries are bounded.
- Postgres does not make state coherent unless transactions match the invariants.
- Kubernetes does not make the service highly available if local files, process locks, or singleton volumes are still correctness dependencies.
- A cache does not make a slow path safe if cache misses stampede or stale reads violate the product contract.
- MCP does not make tools safe if the underlying capability model is unclear.

Add infrastructure only after the access pattern and failure semantics demand it.

### Review Like A Staff Engineer

When reviewing code or docs, lead with the load-bearing questions:

- What contract is this changing?
- Which invariant is now stronger or weaker?
- What new state exists, who owns it, and how is it cleaned up?
- What is the worst duplicate, timeout, crash, or partial-failure history?
- What is the largest payload, result set, queue, registry, or event this path can create?
- What is the operator supposed to look at when this fails at 3 a.m.?
- What test proves the important claim?

Praise is secondary. The useful gift is finding the assumption that would break under pressure while the change is still cheap to fix.

---

## Systems Mindset

- Write Python like systems software: make state transitions, invariants, ownership, and failure modes explicit.
- Failures are the default path in real systems, not an edge case to patch later.
- Retries, timeouts, idempotency, backpressure, circuit breakers, and load shedding are core system design tools; use them deliberately where they belong.
- Optimize for low cognitive load. Prefer code that makes intent, data flow, and invalid states obvious to the next maintainer.
- Prefer deep modules with shallow interfaces when that reduces exposed complexity.
- Prefer standard Python protocols and built-ins over bespoke helper method names when they make the API clearer.
- Prefer composition over inheritance; subclass only for real subtype relationships or established framework extension points.
- Be explicit about mutability, aliasing, and ownership of inputs; copy mutable inputs when mutation would otherwise surprise the caller.
- Prefer least-surprise behavior in stateful code. If an operation fails, either leave state unchanged or make the partial state transition explicit and tested.
- Prefer simple, deterministic behavior over cleverness, especially in storage, network, and recovery paths.
- Think in terms of durability, idempotence, ordering, retries, timeouts, and recovery after partial failure.
- Treat concurrency, async work, and shared mutable state as design concerns, not implementation details. Document ownership and bounds before adding them.
- Reason about saturation, queue growth, and tail latency, not just happy-path averages.
- Bound loops, retries, queues, and concurrency; avoid hidden unbounded work.
- Use async, queues, caching, MCP, and extra abstractions only when they reduce concrete complexity or failure risk.
- Prefer boring, proven infrastructure over impressive-looking stacks. Redis, Kafka, Postgres, workflow engines, vector stores, and graph databases are tools, not achievements; introduce them only when the access pattern, topology, or failure model clearly demands them.
- Design so today's simple primitive can graduate cleanly to a shared backend later. Preserve upgrade seams even when the current deployment only needs a local file, in-memory cache, or single-node assumption.
- Prefer strong primitives and clean extension points over narrow one-off features, but do not add abstractions without a concrete need.
- Use data-focused helper types judiciously; keep behavior close to the data when that improves the model.
- Separate control-plane work from data-plane work. Nimbus should authorize, record, and verify large storage operations; it should not blindly become the bottleneck for large byte streams when a streaming or pre-signed data-plane path is the right primitive.
- Choose consistency by workflow. Destructive authorization, idempotency claims, action transitions, and event sequence allocation need strong authority; metrics, presence, and read projections can be eventual.
- Treat the event log as the narrative truth when it exists. Projections, caches, client timelines, and metrics are rebuildable views, not alternate sources of truth.
- Store graduation must be access-pattern driven: JSON for simple local snapshots, SQLite for single-node durable coordination, Postgres for multi-process durable state, Valkey/Redis for hot shared coordination, queues for long-running action execution, and workflow engines only for timer-heavy multi-step workflows.
- During overload, preserve safety/control operations before expensive AI, bulk storage, or background work. Define priority and load-shedding behavior rather than letting the slowest dependency decide.

---

## Setup and Commands

Run commands from the repository root.

```shell
# Install all workspace packages and dev tools (use --frozen in CI)
uv sync --all-packages

# Full test suite
uv run pytest

# AI package tests
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/
uv run --package ai-server pytest src/ai_server/tests/

# Fast unit-focused pass
uv run pytest src/

# Filtered test runs
uv run pytest -m unit
uv run pytest -m "not e2e"
uv run pytest -k "upload"

# Integration and end-to-end helpers
uv run pytest tests/integration/
./scripts/run_integration_tests.sh
uv run pytest tests/e2e/ -m "not local_credentials"
./scripts/run_e2e_tests.sh

# Format, lint, and type check
uv run ruff format .
uv run ruff check --fix .
uv run mypy --strict .

# Run the service
uv run uvicorn aws_client_service.main:app --reload

# Build documentation
uv run sphinx-build docs/source docs/build/html
uv run sphinx-autobuild docs/source docs/build/html
```

After editing any Python file, run `ruff check --fix` and then `ruff format` on the touched paths before finishing.

---

## Code Standards

This project uses `ruff` with the `ALL` ruleset and `mypy --strict`. Follow the existing patterns in the touched package.

- Write simple, pragmatic, predictable code that is easy to test.
- Favor small, coherent designs over clever or sprawling ones.
- Keep functions and classes small and single-purpose.
- Keep strict typing and linting green as part of the inner loop when touching a module, not as a final cleanup pass.
- Prefer explicit contracts over ambient conventions. If a function depends on ordering, mutation, retries, or truncation, make that visible in the API, docstring, or tests.
- Place new code in the most logical module or type; prefer methods when behavior naturally belongs to the object.
- If logic is duplicated or a function becomes hard to follow, extract or move a focused helper instead of growing ad hoc complexity.
- Preserve or improve file organization: prefer primary or public entry points near the top of a module and lower-level helpers beneath the code that uses them when practical.
- Functions and classes should have docstrings.
- Comments should explain why, not what, and should be written as complete sentences.
- Imports belong at the top of the file except for circular-import, lazy-load, or `TYPE_CHECKING` cases.
- Guard type-only imports of heavy modules behind `if TYPE_CHECKING:`.
- Preserve existing logging and error-handling conventions in the touched package.
- Do not use production `assert`; validate explicitly and raise specific exception types.
- Do not add broad `except Exception` handlers or silent fallbacks.
- If a lint suppression is unavoidable, use a targeted rule code and an inline justification; avoid blanket suppressions.
- Use `typing.assert_never` or an explicit `AssertionError` for truly unreachable code.
- Validate important arguments, return values, and invariants at system boundaries.
- Keep interfaces narrow and call sites simple; prefer fewer branches and clearer signatures.
- Prefer explicit, descriptive names over new abbreviations.
- Avoid `Any` when a narrower type is practical; prefer unions, protocols, type variables, or schema-driven models.
- Use `TypedDict` or dataclasses for known structured mappings instead of `dict[str, Any]`.
- Prefer standard Python protocols and operations when they fit the model, instead of inventing custom interfaces.
- Prefer `collections.abc` protocols, standard container semantics, and existing ABCs before creating new abstractions.
- Make failure modes and state transitions explicit at system boundaries.
- Consider reliability and performance early, especially in storage, file, and network paths.
- Measure before optimizing. Benchmark or profile before making performance-driven changes, and record the reason for non-obvious optimizations.
- Avoid unbounded recursion, unbounded queues, or open-ended retry or loop behavior unless there is a clear bound.

### Python Preferences

- Prefer constants over repeated magic strings, dictionary keys, and path literals. Use immutable module-level constants when they improve clarity.
- Prefer mapping names such as `item_by_id` or `value_by_key` when they make lookup intent clearer.
- Prefer f-strings for ordinary string formatting.
- Prefer direct iteration over indexing; use `enumerate()`, `dict.items()`, and `zip()` when they make the code clearer.
- Use comprehensions and built-in functions such as `all()` and `any()` when they simplify the code without obscuring it.
- Prefer tuples for fixed records and lists for mutable sequences when those semantics fit the contract.
- Use context managers for resources and cleanup.
- Be explicit about text encodings and text-versus-bytes boundaries.
- Never use mutable default arguments.
- Use `is` and `is not` only for singleton checks such as `None`.
- Prefer abstract container types from `collections.abc` in annotations when they fit the contract.
- Use exception chaining with `raise ... from exc` when translating errors across layers.
- Include descriptive exception messages.

---

## Architecture Rules

These constraints preserve the adapter pattern and package layering:

- `cloud_storage_api` is an external package; do not vendor or modify it.
- `ai_client_api` is the provider-agnostic AI contract; keep model-provider specifics in implementation packages.
- `aws_client_impl` is the only package that may import `boto3`.
- `openrouter_ai_client_impl` is a concrete provider implementation, not the abstraction boundary.
- `nimbus_runtime` owns transport-neutral product semantics for chat turns, verified actors, durable actions, events, artifacts, policy, verification, and telemetry. HTTP, CLI, Slack, and future MCP surfaces should be thin edges over this kernel.
- `aws_client_service` must obtain a client through `get_client_impl()` and must not instantiate `S3Client` directly.
- `aws_client_adapter` should preserve the `CloudStorageClient` contract while communicating through the generated HTTP client; do not couple it to service internals.
- `ai_server` should stay an HTTP adapter around shared AI/runtime capabilities rather than accumulating channel-specific business logic.
- `aws_s3_cloud_storage_service_client` is autogenerated from the OpenAPI spec. Regenerate it when the API changes; do not edit it by hand.
- When the service API shape or behavior changes, update the relevant docs/examples and regenerate the client in the generated package.
- Preserve backward compatibility for shipped behavior and public APIs unless the user explicitly asks for a breaking change.
- Preserve the `get_client_impl()` factory contract unless the user explicitly asks to change it.
- For HW3 cross-vertical work, consume the other team's shared API as a dependency and adapt to it; do not duplicate their interface locally.
- Keep AI tool exposure schema-first and guardrail-aware; destructive actions need explicit confirmation or an equivalent safety interlock.
- Destructive tools should not be model-direct side effects. The model may propose; deterministic runtime policy and action state decide; executors perform; verifiers prove.
- Keep action and event payloads bounded and schema-versioned. Large proof, listings, summaries, or binary data should become artifacts or external object references rather than realtime/event payloads.
- If MCP is introduced, centralize capability exposure and make host/client/server boundaries explicit.
- Favor simple, coherent designs that keep behavior predictable and layers clean.

---

## Testing Expectations

All tests use `pytest`.

### Markers

Defined in `pyproject.toml`:

| Marker | Meaning |
| --- | --- |
| `unit` | Fast, isolated tests with no real I/O |
| `integration` | Dependency-injection and package wiring tests without real AWS |
| `regression` | Tests that guard against previously fixed bugs |
| `property` | Property-based tests that verify invariants over many inputs |
| `e2e` | Full workflow tests against real infrastructure |
| `circleci` | Safe to run in CI without local credentials |
| `local_credentials` | Requires local credential or token files |

If every test in a file shares the same marker, set `pytestmark = pytest.mark.<marker>` at module scope.

### Expectations

- Put lasting tests in `tests/` or `src/*/tests/`. Do not add throwaway verification scripts.
- Follow the style of nearby tests and put new tests in the file that best matches the source module or behavior under test.
- Prefer pytest functions named `test_*` over `unittest.TestCase` classes.
- Prefer fixtures over setup and teardown methods.
- Prefer `@pytest.mark.parametrize` when exercising the same behavior across multiple similar inputs.
- Use direct `assert` statements in tests.
- Prefer asserting whole objects or payloads when it keeps tests clearer and catches more regressions than many field-by-field assertions.
- Prefer deterministic test data; do not use randomness in unit tests.
- Mock only external boundaries such as `boto3`, outbound HTTP, and OAuth providers.
- Prefer `create_autospec` over loose mocks when mocking Python objects.
- Prefer `tmp_path` or `tmp_path_factory` for temporary filesystem test data.
- Prefer explicit dependency injection or parameters in tests over mutating process environment when the design already allows it.
- Prefer testing through public APIs and observable behavior rather than private helpers or underscored methods.
- Test the contract and observable behavior, not private implementation details.
- Cover the success path, failure path, and important edge cases.
- For stateful flows, test rollback and recovery semantics explicitly: what state changes on success, what state changes on failure, and what is retried on the next call.
- Ensure test names and docstrings match the behavior actually asserted.
- For configurable or optional behavior, cover both supported and unsupported or failure paths.
- Favor integration tests for wiring or cross-package behavior when they add confidence, but keep the suite fast and focused.
- When behavior spans package boundaries, public contracts, or transport layers, prefer integration tests over narrower unit tests.
- For HW3, cover at least one real cross-package or cross-vertical workflow through public contracts, not only mocked leaf functions.
- Live-network tests must be opt-in, clearly marked, and assert stable shape or contract rather than brittle provider-specific text.
- For critical storage or service behavior, prefer property-style tests that encode externally visible guarantees.
- For fault-prone paths, think in terms of acknowledged durability, retry safety, idempotence, consistency, and recovery after partial failure.
- For reliability-sensitive flows, test timeout handling, retry behavior, idempotent replay, concurrency ordering, and overload limits where practical.
- For action/event runtime changes, test duplicate delivery, wrong actor, expired confirmation, invalid state transition, crash/restart recovery where practical, and replay/projection equality once projections exist.
- For complex stateful or distributed behavior, write down the current system model: core components, state ownership, concurrency boundaries, and failure-prone paths.
- Maintain a property catalog for critical guarantees and organize it by safety and liveness properties.
- Keep deployment and topology assumptions current for reliability testing, especially leader/follower roles, quorum expectations, and external dependencies.
- Expand reliability workloads incrementally: start with a simple happy path, then add concurrent actors, failures, partitions, restarts, and timing perturbations.
- Before long-running reliability tests, rebuild the test environment if needed and validate the test harness locally.
- The normal suite enforces 80% coverage. Do not lower that threshold for unit or package test runs.
- Use `tempfile.TemporaryDirectory()` inside the test body for Hypothesis tests that write to disk — `tmp_path` is scoped per test function, not per example, so all examples in a single Hypothesis run share the same directory and contaminate each other.
- When a function internally calls `time.monotonic()` or `time.time()`, add a `_now: float | None = None` clock-injection parameter that defaults to the real clock in production. This makes it testable with Hypothesis without mocking.
- Fuzz harnesses live under `fuzz/` and use `PYTHONFUZZ_NO_ATHERIS=1` smoke mode in CI. Do not add production logic to fuzz files — they are excluded from ruff via `extend-exclude`.
- All new test files must have `pytestmark = pytest.mark.<marker>` at module scope. The CI `unit-tests` job filters to `-m "unit or regression"` — unmarked tests will not run in CI.

---

## Documentation

- Documentation should focus on user tasks and public APIs; keep implementation details in docstrings or design docs unless they affect user decisions.
- All fenced code blocks should include a language marker.
- Structure explanations with context first, then example code, then caveats or edge cases.
- Use realistic examples that show why a feature matters and that readers can adapt directly.
- Present the recommended approach first; introduce alternatives explicitly and explain trade-offs.
- Prefer Sphinx/MyST cross-references and canonical sources over duplicated summaries or plain code formatting for navigational links.
- Keep deprecated or superseded approaches out of the main user path; if older behavior must be mentioned, clearly de-emphasize it.
- Use Sphinx/MyST admonitions for notes and warnings rather than GitHub-specific callout syntax.
- Keep deployment, IaC, and telemetry docs current enough that a reviewer can reproduce the system and understand the latency/success/failure signals.
- Keep system-design docs converged. Do not leave multiple competing architecture drafts in the main reading path; maintain one canonical story and link supporting concept pages to it.
- Verify documentation changes with `uv run sphinx-build docs/source docs/build/html`.

---

## Environment

Never hardcode credentials or tokens. Use local environment variables or `credentials.env`, which is already gitignored.

```shell
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="..."
export OPENROUTER_API_KEY="..."
export OPENROUTER_MODEL="..."
export OPENROUTER_FALLBACK_MODEL="..."
# ai_server
export AI_SERVER_API_KEY="..."          # shared secret for session-history/session-delete X-API-Key auth
export AI_SERVER_SIGNING_SECRET="..."   # shared secret for signed POST /ai/chat/turn requests; share this with the bridge team
export AI_SESSION_DIR="/data/sessions"  # override default ~/.nimbus/sessions/ai_server
export AI_RATE_LIMIT_CAPACITY="10"      # per-user token bucket capacity (default: 10)
export AI_RATE_LIMIT_RPM="10"           # requests-per-minute refill rate (default: 10)
# nimbus CLI
export NIMBUS_CONTAINER="..."           # S3 bucket the LLM tools are pinned to
export NIMBUS_SAFE_ROOT="/home/user/workspace"
export NIMBUS_SESSION_DIR="~/.nimbus/sessions"
export GITHUB_CLIENT_ID="..."
export GITHUB_CLIENT_SECRET="..."
export CLOUD_STORAGE_SERVICE_BASE_URL="..."
export API_KEY="..."
```

Unit and integration tests should run without real credentials by mocking external calls.

### Fly.io deployment (ai_server)

Session files must survive redeploys. Mount a persistent volume:

```toml
# fly.toml
[[mounts]]
  source      = "nimbus_sessions"
  destination = "/data"
```

```bash
# Create the volume (once per region). The current app volume lives in ewr.
flyctl volumes create nimbus_sessions --region ewr --size 1

# Set the session directory and wrapper-signing secret as secrets.
# Keep AI_SERVER_API_KEY too if you still use the session-history/session-delete endpoints:
flyctl secrets set AI_SESSION_DIR=/data/sessions AI_SERVER_SIGNING_SECRET=<key> AI_SERVER_API_KEY=<key>

# Keep at least one machine running so the volume is always accessible:
flyctl scale count 1 --min 1
```

### mypy `exclude` for test directories

`pyproject.toml` sets `exclude = ["src/.*/tests/", "tests/"]` in `[tool.mypy]`
because multiple `src/*/tests/` packages share the top-level `tests` name under
mypy's flat namespace resolution — without the exclude, mypy reports duplicate
module errors for `test_auth`, `test_router`, etc. across packages. Excluding
test directories is safe: pytest still runs them via the filesystem; mypy only
type-checks production code.

---

## Git and Change Safety

- Never commit secrets, credentials, or token files.
- Never use destructive commands such as `git reset --hard` or `git checkout --` unless the user explicitly asks for them.
- Do not amend commits unless the user explicitly asks.
- Use non-interactive git commands.
- Leave unrelated user changes intact.
- If concurrent changes directly conflict with your task, stop and ask the user.
- Avoid opportunistic dependency upgrades or unrelated lockfile changes.
- Commit messages should explain why the change exists and the high-level what.
- Write commit subjects in the short, imperative, sentence-case style used in repositories such as `ssh-hypervisor`, `sshx`, and `classes.wtf`: `Fix KVM permission issue in CI`, `Add workflow_dispatch to CI`, `Update fly.toml to Fly Machines API`.
- Prefer a single descriptive line with no trailing period; avoid vague subjects such as `wip`, `misc`, or `updates`.
- When drafting a PR description or change summary, explain why the change is appropriate and call out any areas that merit careful review.
- Branch from `main`; do not push directly to `main`.
- Keep pull request titles under 70 characters and reference issues with `closes #N` or `related #N` when relevant.

---

## Ask Before

Ask the user before you:

- change how `cloud_storage_api` types are used across packages
- add a new third-party dependency
- modify CI/CD configuration, release automation, or root tool configuration
- modify the `get_client_impl()` factory contract
- make a cross-package refactor whose impact is not clearly local
- introduce a new public abstraction or broad user-visible behavior change without a clear established pattern in the codebase

---

## Special Requests

- If the user asks for a review, lead with findings: bugs, regressions, risks, and missing tests. Include file and line references where possible, keep the summary brief, and state explicitly if no findings were found.
- If the user asks for a simple command-driven fact, such as the current time, run the command and report the result.
- If the user asks whether an approach is good, best, or appropriate, answer that question directly before proposing or making code changes.
