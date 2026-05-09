# Agent Development Guide

Repository-wide guidance for coding agents. Explicit user instructions override this file. See [agents.md](https://agents.md/) for the format.

---

## Highest-Priority Rules

- Read the relevant code before editing. Do not guess from names alone.
- Before non-trivial implementation, write down the working system model: goals, public contract, invariants, failure modes, dependencies, and verification plan.
- Do not be a rubber stamp. Push back constructively on every request by checking the user's goal against the codebase reality, hidden API contracts, failure modes, operational cost, and simpler alternatives. For trivial requests this may be one sentence; for non-trivial work it should become an explicit agreement checkpoint before implementation.
- Review and design in this order: correctness first, then clarity, then style and local conventions, then deduplication, then tests. If the behavior is wrong, style polish is noise.
- Parse, do not merely validate: turn raw boundary input into precise domain types or rejected errors as early as possible, then pass trusted representations inward. Do not scatter repeated "just in case" checks across execution code.
- Make illegal states hard to represent. Prefer explicit value objects, enums, dataclasses, Pydantic models, protocols, and narrow exception types over unstructured dictionaries, booleans whose meaning changes by context, or `None` sentinels that force every caller to rediscover invariants.
- APIs must do the same thing every time. Treat status codes, response shapes, ordering, defaults, env vars, metrics, persisted records, and error messages as compatibility surface.
- Treat failures as the default case, not edge cases. Design timeouts, retries, idempotency, backpressure, and observability intentionally.
- For every stateful or networked feature, reason through crash, retry, timeout, duplicate delivery, partial success, dependency outage, overload, rollback, and replay before coding.
- For networked, AI, storage, or stateful work, name the runtime kernel concept being touched before coding: contract, adapter, session, operation, event, action, artifact, policy, executor, verifier, projection, or store.
- Be wary of the scale, complexity, and maturity of this codebase when making changes. Build an accurate mental model before editing so local fixes do not damage system-level design.
- Develop the correct intuitions and use the correct tools within the limits of the current context window. Ask the user targeted questions whenever needed instead of guessing past uncertainty; after one bounded search for an ambiguous file, doc, or requirement, ask instead of spending a long turn on the wrong target.
- Prefer the smallest correct change that fits the existing design.
- Prefer atomic, focused changes that are easy to review and revert.
- Preserve package boundaries and the dependency-injection pattern.
- Add or update tests when behavior changes.
- Update the relevant docs, README sections, examples, and operational notes whenever behavior, commands, architecture, configuration, or public contracts change. Docs are part of the deliverable, not a cleanup task.
- Try to break your own change before handing it off. Exercise the success path, malformed input, boundary values, concurrency where relevant, dependency failure, and retry behavior until the feature behaves production-credibly.
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
- Start with the naked one-process/one-durable-store design and add infrastructure only when a named requirement and rough number prove the simpler system is out of room.
- Treat platform, CI/CD, observability, secrets, migrations, backups, and rollback as part of the system design. A feature is not really done if it cannot be deployed, observed, reverted, and recovered.
- When a fix depends on manual configuration outside the repo (Slack app settings, cloud dashboards, secrets, DNS, OAuth callbacks, webhooks, vendor consoles, etc.), call it out immediately as a required user/operator action with the exact page, setting name, and value to enter. Do not bury the manual step behind code analysis, docs edits, or verification details.
- Keep AI safety deterministic where possible: the model may propose actions, but policy, actor identity, confirmation state, tool schemas, idempotency, execution, verification, and audit evidence must be owned by typed runtime code.
- Backups and recovery must be tested independently from the production agent path. A backup that has never been restored is only a hope.
- Avoid both toy shortcuts and speculative platformization. Start with the smallest production-credible primitive, document why it is enough today, and state the trigger for introducing heavier infrastructure such as Redis, queues, or additional databases.
- Keep one canonical system-design story. If multiple docs disagree, consolidate the current truth or clearly mark older material as historical.
- Keep `AGENTS.md`, `CONTRIBUTING.md`, `README.md`, and the relevant Sphinx docs current when project structure, commands, public behavior, operations, or conventions materially change.

---

## Collaboration Contract

Agents should be helpful, but not blindly agreeable. The goal is not to slow work down; the goal is to avoid spending a long turn confidently building the wrong thing.

For every user request, apply this pushback ladder:

1. Restate the concrete outcome the user appears to want.
2. Name the closest existing code, docs, command, or contract that governs the work.
3. Challenge at least one assumption: scope, public API behavior, state ownership, failure mode, compatibility, cost, or simpler path.
4. Identify what would make the request unsafe, underdefined, unnecessarily broad, or misaligned with the architecture.
5. For simple, low-risk tasks, proceed after this quick sanity check.
6. For non-trivial code, architecture, runtime, storage, AI, security, deployment, or public API work, do not start implementation until the agent and user are about 95% aligned on the target behavior and constraints.

The 95% agreement checkpoint should be explicit and numbered:

1. Goal: what user-visible or caller-visible behavior changes?
2. Non-goals: what tempting adjacent work is intentionally out of scope?
3. Contract: inputs, outputs, errors, ordering, defaults, persistence, metrics, and compatibility.
4. State: who owns it, where it is stored, how it is migrated, and how it is recovered.
5. Failure model: timeout, retry, duplicate delivery, partial success, crash, overload, dependency outage, and rollback.
6. Design pressure: why the naked one-process/one-store version is enough, or what exact number proves it is not.
7. Tests: unit, regression, property, deterministic simulation, BDD/eval, integration, e2e, load, or smoke checks as appropriate.
8. Docs and operations: README/Sphinx updates, env vars, readiness, telemetry, deployment, backup/restore, and rollout/rollback.

If the request conflicts with these constraints, say so directly, explain the trade-off, and offer the smallest safer alternative. If evidence is missing after one bounded search, ask a targeted question instead of guessing past uncertainty.

---

## Project Snapshot

This repository is a Python 3.12+ workspace for Nimbus: a provider-agnostic cloud-storage library and service, an AI/runtime kernel for guarded storage actions, HTTP and CLI adapters, and deployment/operations glue for a Render-hosted system.

Agents must keep this snapshot current. Whenever a change adds, removes, renames, or materially changes a package, route, deployment target, state backend, public command, or major architectural responsibility, update this section in the same change as the code/docs that made it stale.

Key packages:

- `cloud_storage_api` (external): abstract `CloudStorageClient` contract, domain types (`ObjectInfo`, `DeleteResult`), and domain exceptions. Installed from git, not vendored locally.
- `src/aws_client_impl/`: `boto3`-backed S3 implementation plus OAuth and token helpers.
- `src/aws_client_service/`: FastAPI service layer exposing the storage contract over HTTP and mounting the AI router under `/ai`.
- `src/aws_client_adapter/`: adapter that re-implements `CloudStorageClient` by calling the service through the generated client.
- `src/aws_s3_cloud_storage_service_client/`: autogenerated OpenAPI client. Never edit it by hand.
- `src/ai_client_api/`: provider-agnostic AI contract (`AIClient`, `Conversation`, `Tool`, `AIResponse`) and shared AI-domain exceptions. Provider SDK behavior belongs outside this package.
- `src/openrouter_ai_client_impl/`: OpenRouter-backed `AIClient` implementation, pydantic-ai agent loop, and `cloud_storage_tools.py` bindings that expose `CloudStorageClient` operations to the model under runtime guardrails.
- `src/nimbus_protocol/`: shared Nimbus DTOs, stream events, error presentations, approvals, and permission shapes used by HTTP, CLI, Slack, and runtime code.
- `src/nimbus_runtime/`: transport-neutral product kernel for chat turns, verified actors, durable actions, durable policy decision records, session events, artifacts, approval/confirmation flows, attachment handling, state stores, provider capability/health evidence, content-addressed evidence payload exports, ACL-aware search/knowledge projections, and runtime telemetry.
- `src/ai_server/`: FastAPI router mounted under `/ai` by `aws_client_service.main`. Exposes health/readiness endpoints, signed wrapper chat-turn routes, session-history/delete routes, rate limiting, replay/idempotency protection, and local/Postgres state routing.
- `src/nimbus_cli/`: Python terminal adapter for local in-process and remote Nimbus profiles, onboarding, secret/profile storage, and human-facing command output.
- `src/nimbus_slack/`: Slack Events API adapter and workspace control plane. Treat this as in-progress channel code; do not modify the Slack bridge unless the user explicitly asks for Slack work.
- `tests/` and `src/*/tests/`: pytest suites for unit, regression, integration, property, BDD, eval, and e2e coverage.
- `fuzz/`: fuzz smoke harnesses for deserialization and security-sensitive parsing paths.
- `docs/`: Sphinx/MyST documentation, including architecture, deployment, testing, CLI, Slack, and API guides.
- `render.yaml`, `Dockerfile`, `scripts/render/`, and `scripts/db/`: deployment and migration support for Render services and Postgres-backed runtime state.
- `justfile`: lightweight task entry points for setup, format, lint, tests, docs, BDD, and wrapper smoke checks.
- `main.py`: CLI demo entry point.

Design intent:

- Program to `CloudStorageClient`, not to a concrete implementation.
- Program to `AIClient`, not to a concrete model provider.
- Keep dependency direction clean: interface inward, implementations and transports outward.
- Each implementation package provides a `get_client_impl()` factory. There is no global DI registry.
- Treat the repo as two connected axes: the storage vertical and the AI/runtime vertical.
- Keep channel adapters thin. Shared runtime, tool, and integration logic should live in reusable packages rather than Slack/CLI-specific glue.
- Treat `nimbus_runtime` as the product kernel: model proposes, runtime authorizes, actions execute, verifiers produce artifacts, and events tell the audit story.
- Treat `nimbus_protocol` as the shared vocabulary at channel boundaries. Do not let provider SDK objects, raw Slack payloads, or generated-client internals leak into runtime policy.
- The naked local topology is one process plus one durable store. Local JSON/SQLite-style state is acceptable for development; Render production uses Postgres where cross-process durability or deployment restart behavior requires shared authority.
- The deployment story is code: Render readiness, migrations, Postgres state, OpenTelemetry/New Relic, Sentry, and feature-flag/kill-switch behavior are part of the product surface.

---

## Default Workflow

1. Read the relevant files and search for existing patterns before editing.
2. Build an accurate working model of the touched subsystem: what owns state, what the transport boundaries are, what invariants exist, where failure is most likely, and what observable behavior callers already depend on.
3. For non-trivial work, define the contract first: user-visible behavior, invariants, state ownership, scale assumptions, failure model, timeout/retry/idempotency/backpressure plan, observability, rollout/rollback shape, and how the change will be verified.
4. Start with the simplest working version that could be correct in one process and one durable store. Add a cache, queue, worker, database, service, protocol, or generated contract only when a named requirement and rough number justify it.
5. Make the target behavior explicit. For behavior changes, write or update tests before or alongside the implementation. The tests should prove the contract, not just execute the lines.
6. Implement the smallest correct change that satisfies the contract. Prefer precise domain types, parser/smart-constructor style boundary handling, and stable error mapping over ad hoc validation in the middle of execution.
7. Update every durable product surface touched by the change: `README.md`, Sphinx docs, examples, environment-variable docs, deployment notes, generated-client instructions, and this `Project Snapshot` when architecture or commands changed.
8. Try to break the feature. Exercise malformed inputs, empty and maximum-sized values, duplicate delivery, retries, concurrency, dependency failure, timeout, restart/replay, and overload where relevant. Iterate on the design until it behaves about 95% like the intended production behavior, with remaining risks named explicitly.
9. Run targeted verification first, then broader checks when the change warrants it. Keep `ruff`, formatting, `mypy --strict`, unit/regression tests, docs build, property/BDD/eval tests, and integration/e2e checks in the loop according to risk.
10. Finish with a clear summary of what changed, what contract is now stronger or weaker, how it was verified, what docs changed, and any remaining operational or correctness risk.

For networked or stateful work, explicitly decide what happens under timeout, partial failure, duplicate delivery, overload, and dependency outage before writing code.

For runtime work, explicitly decide whether the change belongs to the operation layer, action ledger, event log, artifact store, policy module, executor, verifier, projection, or HTTP/CLI adapter. If the answer is "route-local glue," re-check whether the behavior should live in `nimbus_runtime` instead.

For API, schema, or protocol work, ask whether callers could reasonably depend on the current observable behavior even if it is not documented. Hyrum's Law applies to env vars, CLI output, payload key order when rendered, status codes, exception classes, pagination defaults, metric names, and log fields.

For AI-facing work, keep user protection deterministic. Define the tool schema, policy decision, confirmation state, actor/tenant binding, idempotency key, execution boundary, verifier, artifact evidence, and safe user-visible fallback before letting the model near side effects.

When working from an issue, pull request, or GitHub URL, read the linked discussion and directly relevant cross-references before implementing.

Trust but verify user-provided assumptions. Research local precedent and ask targeted questions when scope, constraints, or trade-offs are unclear.

If a requested public-facing change has no clear local precedent or acceptable shape, align on scope before coding instead of guessing.

If a non-trivial public-facing change is underdefined and there is no clear issue, precedent, or acceptable local pattern to follow, help define the issue, proposal, or plan before implementing.

Use `AGENTS.md`, the root `CONTRIBUTING.md`, the root `pyproject.toml`, and existing scripts as the canonical sources for development commands and tool configuration.

Read `plans.md` when you need the broader direction for developer productivity and codebase foundations. The long-term goal is a codebase with strong lifecycle support across source control, environments, code generation, CI, release flow, and runtime tooling, and Nimbus should integrate tightly with those foundations rather than bypassing them.

Read the canonical system design or the closest maintained design doc before broad architecture, runtime, storage, observability, or deployment work. If the maintained design source is missing or contradicted by code, update the docs as part of the change.

Prefer dedicated search, read, and edit tools when available. Otherwise use fast, deterministic commands such as `rg`. Parallelize independent reads, searches, and checks when your tooling allows it.

For tool and dependency upgrades, prefer current official documentation and changelogs over memory or third-party blog posts. Use the latest stable guidance deliberately, not blindly.

Before handing off production-adjacent work, ask the operational closeout questions: how does `/ready` behave, what dashboard/metric/log proves success or failure, what migration or rollback is needed, what secret/config changed, what backup/restore path protects the state, and what alert would wake an operator for the right reason?

---

## Production System Design Mindset

Use this mindset for architecture, runtime, storage, AI, observability, deployment, or any change whose consequences cross a module boundary.

The job is not to draw an impressive diagram. The job is to find the first lie in the diagram.

Good design here means a small system whose contracts survive real pressure: retries, crashes, duplicate delivery, slow dependencies, partial writes, stale clients, operational mistakes, and users asking the AI to do something dangerous. Gall's Law beats premature platformization: make the simple system correct first, then graduate it when numbers, not vibes, show it is out of room.

Use the standard laws as checks, not slogans:

- Hyrum's Law: every observable behavior can become API surface.
- Murphy's Law: if a dependency, disk, network, migration, or human runbook can fail, design the failure path.
- Amdahl's Law: parallelism cannot outrun the sequential bottleneck, hot lock, hot key, WAL, or provider limit.
- CAP/PACELC: when state crosses processes or regions, name the consistency, availability, latency, and partition behavior instead of claiming all of them.
- Postel's Law, with a security caveat: be conservative and deterministic in what Nimbus sends; accept only what can be parsed into explicit safe types, and reject ambiguous input early.
- Goodhart's Law: coverage, latency, and success-rate numbers are useful only while they still represent the real product promise.
- Brooks's Law, Ringelmann Effect, and Conway's Law: team and communication structure are design constraints, so prefer boundaries that a small team can understand and operate.

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

### Define The Guarantees

For each important behavior, state the guarantee in a form that can become a test, dashboard, alert, or runbook check:

- Safety: what must never happen, such as cross-tenant state access, unconfirmed destructive action, leaked secret, corrupt persisted state, or double-executed side effect.
- Liveness: what should eventually happen, such as a pending confirmation expiring, an in-flight idempotency claim resolving, or a failed dependency returning a clear degraded response.
- Ordering: what must be serialized, what can run concurrently, and what ordering is visible to callers.
- Durability: what must be written before success is visible, and what evidence proves it after restart.
- Recovery: what state can be rebuilt, what state is authoritative, and how to restore from independent backup.
- Degradation: what users safely see when AI, storage, Postgres, Slack, telemetry, or feature flags are unavailable.

If a guarantee matters, encode it in at least one of: type shape, state transition, unit/regression test, property test, deterministic simulation, BDD/eval scenario, readiness check, metric, or operational runbook.

### Deterministic Simulation Mindset

For stateful runtime, storage, and adapter work, prefer deterministic failure exploration over one-off happy-path tests. Ask:

- Do invariants hold sequentially?
- Do invariants hold under concurrent actors?
- Do invariants hold under partial failure, crash, timeout, and restart?
- Is "failure plus safe retry" observationally equivalent to "no failure" for idempotent operations?
- Can the same seed or fixture replay the failure exactly?

Use injected clocks, bounded fakes, fake stores, deterministic providers, and explicit state machines before reaching for expensive canaries. Canary and staging tests still matter, but they should confirm the simulated contract rather than discover basic invariants for the first time.

### Latency, Throughput, Consistency

Every system design answer should name the shape of the workload:

- Latency budget: p50, p95, p99, timeout, queueing, and user-visible fallback.
- Throughput: steady QPS, burst QPS, writes/sec, bytes/sec, concurrent sessions, and provider/API rate limits.
- Consistency: strong, read-your-writes, monotonic, eventual, or best-effort for each state transition.
- Retention and growth: events, actions, artifacts, sessions, idempotency records, logs, metrics, and backups over months or years.
- Encoding and serialization: JSON size, base64 expansion, UTF-8 versus bytes, streaming versus buffering, compression, schema versioning, and generated-client compatibility.

Do the simple math. Ten million requests per second is not just "more replicas"; it is a question about hot keys, shared state, WAL/fsync pressure, connection pools, network fan-out, provider quotas, queue depth, and what work can be shed safely.

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

Before proposing another replica, ask whether one better-shaped node, better batching, fewer round trips, a narrower transaction, or a simpler data plane solves the problem with less coordination risk. Each new node adds network hops, partial deploy states, duplicate work, connection pressure, and more ways to observe inconsistent state.

### Avoid Hot Spots And Herds

The thundering herd problem is a design bug, not an ops surprise.

For any shared dependency or key, define:

- the hottest tenant, conversation, action ID, session file, DB row, cache key, bucket prefix, provider model, and rate-limit bucket;
- whether callers can stampede after cold start, cache expiry, deploy, outage recovery, webhook retry, or model/provider slowdown;
- whether request collapsing, single-flight work, jittered retries, adaptive backoff, pagination, bounded queues, or load shedding is needed;
- what metric shows queue depth, rejected work, collapsed requests, retry count, and saturation;
- what user-visible degraded response prevents retries from becoming self-inflicted denial of service.

Do not add caching as a reflex. Add request collapsing or adaptive caching only when the workload has a measured or strongly expected repeated-read pattern and the stale-read semantics are acceptable.

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

### Recovery Is A Feature

Failover is not magic. It is a state transition under stress.

For recoverable systems, define:

- authoritative state versus rebuildable projections;
- backup frequency, retention, encryption, and restore procedure;
- the last durable point before an acknowledged success;
- how in-flight work is resumed, cancelled, expired, or reconciled after restart;
- how duplicate work is detected after provider success with lost local response;
- whether old and new code can run together during rolling deploys;
- the operator command or runbook that verifies recovery actually worked.

Prefer self-healing where it is deterministic and observable: idempotent migrations, startup checks, stale-claim expiry, bounded replay, health-gated deploys, and explicit readiness failures. Do not silently "heal" by discarding state unless the contract says that state is rebuildable and the loss is visible.

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

## Platform, GitOps, and SRE

Treat the platform as product code. Deployment manifests, migrations, readiness checks, dashboards, alerts, secrets, and rollback paths are reviewed with the same seriousness as Python modules.

- Prefer GitOps-style changes: infrastructure, deployment commands, runtime settings, dashboards-as-code where available, and migration scripts should be reviewed, versioned, and reproducible from the repo.
- Keep deployment topology explicit. If Render staging/production, local development, or future self-hosted targets behave differently, document the difference and test the contract that must remain the same.
- Health and readiness are separate. `/health` should prove the process is alive; `/ready` should fail closed when required secrets, schema, Postgres, storage configuration, provider access, or migrations are not ready for traffic.
- Migrations must be idempotent, ordered, and deploy-safe. State whether old and new application versions can run against the schema during rollout.
- Secrets must flow through environment/configuration systems, never code or logs. Adding or renaming a secret requires docs, Render/CircleCI notes, and local-development guidance.
- Backups must have restore tests. For any authoritative store, document backup retention, restore command, expected RPO/RTO, and how to verify a restored system without trusting the production agent.
- Rollouts need a rollback story. Name the kill switch, feature flag, deploy hook, migration reversal or forward-fix plan, and the metrics that decide whether to stop.
- Observability is part of done: structured logs, request IDs, actor/tenant IDs where safe, latency, success/failure counts, saturation, retry counts, idempotency outcomes, provider errors, and tool/action outcomes.
- Alerts should map to user harm and operator action. Avoid alerting on vanity metrics; use SLIs and SLOs that track the product contract.
- Load tests are workload documents. When adding Locust or another load tool, model signed chat turns, storage-tool paths, duplicate delivery, provider latency, and burst behavior; record the target QPS, p95/p99, error budget, and expected bottleneck.
- CI should be layered: fast local checks for every edit, package tests for touched modules, property/BDD/eval checks for runtime contracts, integration/e2e checks for boundaries, and deployment smoke checks after readiness passes.
- CI should block on regressions that represent real risk: strict typing, ruff, docs build, unit/regression tests, coverage floor, security-relevant fuzz smoke, and contract tests for public APIs.
- Prefer small reversible changes and short-lived branches. Long-lived divergence makes rollback, blame, and deploy confidence worse.
- Keep generated artifacts and clients reproducible. Never hand-edit generated code; update the generator inputs and include regenerated output when the public API changes.
- Treat dependency updates as production changes. Review changelogs, lockfile impact, new transitive deps, license/security posture, and rollback path.

---

## Setup and Commands

Run commands from the repository root.

```shell
# Install all workspace packages and dev tools (use --frozen in CI)
uv sync --all-packages

# Canonical shortcuts when available
just setup
just format
just lint
just test
just docs-build

# Full test suite
uv run pytest

# AI/runtime package tests
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/
uv run --package ai-server pytest src/ai_server/tests/
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/
uv run --package nimbus-protocol pytest src/nimbus_protocol/tests/
uv run --package nimbus-cli pytest src/nimbus_cli/tests/
uv run --package nimbus-slack pytest src/nimbus_slack/tests/

# Fast unit-focused pass
uv run pytest src/

# Filtered test runs
uv run pytest -m unit
uv run pytest -m "not e2e"
uv run pytest -k "upload"
uv run pytest tests/bdd -q --no-cov
uv run pytest -m eval tests/evals -q --no-cov

# Integration and end-to-end helpers
uv run pytest tests/integration/
./scripts/run_integration_tests.sh
uv run pytest tests/e2e/ -m "not local_credentials"
./scripts/run_e2e_tests.sh

# Fuzz smoke harnesses
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_conversation.py
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_session_id.py
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_request_state.py

# Format, lint, and type check
uv run ruff format .
uv run ruff check --fix .
uv run mypy --strict .

# Run the service
uv run uvicorn aws_client_service.main:app --reload
uv run uvicorn nimbus_slack.main:app --reload --port 8081

# Build documentation
uv run sphinx-build docs/source docs/build/html
uv run sphinx-autobuild docs/source docs/build/html
```

After editing any Python file, run `ruff check --fix` and then `ruff format` on the touched paths before finishing. For docs-only changes, still run a targeted docs check when the changed file is part of the Sphinx tree.

---

## Code Standards

This project uses `ruff` with the `ALL` ruleset and `mypy --strict`. Follow the existing patterns in the touched package.

When reviewing implementation quality, use this exact order:

1. Correctness: does the code do what the contract says under success, failure, retry, and boundary input?
2. Clarity: can the next maintainer understand the data flow, state transition, and reason for the design?
3. Style: does it match local Python, typing, logging, error-handling, and package conventions?
4. Deduplication: is this solving a problem already solved nearby, and would sharing reduce real complexity?
5. Tests: do the tests assert meaningful behavior and failure histories rather than merely increasing coverage?

Pythonic systems code in this repo should synthesize the local books and references rather than copy any one style wholesale:

1. From *A Philosophy of Software Design*: prefer deep modules with small interfaces, information hiding, precise names, comments that capture non-obvious design intent, and strategic changes over tactical patches.
2. From *Fluent Python*: use the Python data model, protocols, context managers, iterators, generators, dataclasses, and standard containers idiomatically; respect text/bytes boundaries and mutability/aliasing semantics.
3. From *Robust Python*: use types to communicate intent, model domain invariants directly, prefer parse-first boundary objects, use Pydantic/runtime validation at system edges, and keep static analysis in the normal workflow.
4. From *High Performance Python*: build a cost model before optimizing, profile before changing hot paths, account for allocation/copying/serialization overhead, and choose lists, tuples, dicts, sets, arrays, streaming, or batching based on workload shape.
5. From *Grokking Concurrency*: distinguish concurrency from parallelism, decompose work deliberately, bound shared resources, design synchronization, avoid races/deadlocks/starvation, and make async I/O cancellation and backpressure explicit.
6. From *Software Engineering at Google*: optimize for code health over time, Hyrum's Law, small reviewable changes, behavior-focused tests, documentation as code, static analysis in the developer loop, and consistency as a scaling tool.

- Write simple, pragmatic, predictable code that is easy to test.
- Favor small, coherent designs over clever or sprawling ones.
- Keep functions and classes small and single-purpose.
- Keep strict typing and linting green as part of the inner loop when touching a module, not as a final cleanup pass.
- Prefer explicit contracts over ambient conventions. If a function depends on ordering, mutation, retries, or truncation, make that visible in the API, docstring, or tests.
- Prefer functions that accept already-parsed, meaningful domain values over functions that accept raw strings or dictionaries and repeatedly validate them. Boundary parsing should preserve what it learned in the returned type.
- Use Pydantic models at transport/config boundaries, dataclasses or small value objects for internal domain records, and protocols/ABCs for behavior contracts. Do not use loose `dict[str, Any]` as a substitute for a schema.
- Place new code in the most logical module or type; prefer methods when behavior naturally belongs to the object.
- If logic is duplicated or a function becomes hard to follow, extract or move a focused helper instead of growing ad hoc complexity.
- Preserve or improve file organization: prefer primary or public entry points near the top of a module and lower-level helpers beneath the code that uses them when practical.
- Functions and classes should have docstrings.
- Comments should explain why, not what, and should be written as complete sentences.
- Imports belong at the top of the file except for circular-import, lazy-load, or `TYPE_CHECKING` cases.
- Guard type-only imports of heavy modules behind `if TYPE_CHECKING:`.
- Preserve existing logging and error-handling conventions in the touched package.
- Do not use production `assert`; validate explicitly and raise specific exception types.
- Use `assert_never`, `AssertionError`, or an equivalent explicit crash only for truly unreachable states after invariants have been established. Do not silently default an impossible state into apparently valid behavior.
- Do not add broad `except Exception` handlers or silent fallbacks.
- If a lint suppression is unavoidable, use a targeted rule code and an inline justification; avoid blanket suppressions.
- Validate important arguments, return values, and invariants at system boundaries.
- If runtime type enforcement such as `beartype` is considered, treat it as a dependency and boundary-policy decision: ask first, document where it runs, measure overhead on hot paths, and keep static typing plus explicit parsing as the primary contract.
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
- `nimbus_protocol` is the shared DTO/event/error vocabulary; keep it provider-neutral, channel-neutral, version-conscious, and free of SDK transport types.
- `aws_client_impl` is the only package that may import `boto3`.
- `openrouter_ai_client_impl` is a concrete provider implementation, not the abstraction boundary.
- `nimbus_runtime` owns transport-neutral product semantics for chat turns, verified actors, durable actions, events, artifacts, policy, verification, and telemetry. HTTP, CLI, Slack, and future MCP surfaces should be thin edges over this kernel.
- `nimbus_cli` is an adapter for humans and remote/local profiles; keep runtime policy and provider-specific behavior out of CLI rendering code.
- `nimbus_slack` is an in-progress Slack adapter and control plane. Do not touch Slack bridge files unless the user explicitly asks for Slack work; when asked, keep Slack-specific normalization and rendering out of runtime policy.
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
- Keep transport input parsing at the edge. HTTP, CLI, and Slack payloads should become typed runtime requests before policy or execution code sees them.
- Keep destructive and expensive operations transactional in the action sense: actor, tenant, target, policy, idempotency, status, attempt, verifier result, and artifact evidence must stay connected.
- Keep runtime state backend choices explicit. Local state is acceptable for single-process development; Postgres is the shared authority when deployment/restart/cross-process behavior needs durable coordination.
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
| `bdd` | Behavior-driven acceptance scenarios written in Gherkin |
| `eval` | Deterministic golden evals for AI/runtime safety behavior |
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
- For boundary parsing, test that malformed, missing, `None`, wrong-type, oversized, duplicate, and conflicting input is rejected before execution changes state.
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
- For AI safety behavior, use deterministic fake models or eval fixtures. Assert typed outcomes, tool availability, action state, policy decisions, and artifact evidence rather than brittle prose.
- For deterministic simulation-style tests, inject clocks, stores, provider responses, and failure schedules. Record the seed or scenario name so a failure can be replayed.
- For idempotent operations, add tests for the equivalence of "success without failure" and "ambiguous failure followed by safe retry" whenever the system claims retry safety.
- For complex stateful or distributed behavior, write down the current system model: core components, state ownership, concurrency boundaries, and failure-prone paths.
- Maintain a property catalog for critical guarantees and organize it by safety and liveness properties.
- Keep deployment and topology assumptions current for reliability testing, especially leader/follower roles, quorum expectations, and external dependencies.
- Expand reliability workloads incrementally: start with a simple happy path, then add concurrent actors, failures, partitions, restarts, and timing perturbations.
- Before long-running reliability tests, rebuild the test environment if needed and validate the test harness locally.
- Use BDD scenarios for wrapper-facing behavior that product users or another team depends on.
- Use load tests for capacity questions, not correctness questions. When a Locust scenario exists or is added, it should model realistic signed requests, payload sizes, provider latency, duplicate delivery, and burst behavior; keep it out of every-push CI unless it is fast and deterministic.
- Do not let coverage become Goodhart's Law. Coverage is a floor; meaningful assertions over contracts, failure histories, and public behavior are the goal.
- The normal suite enforces 80% coverage. Do not lower that threshold for unit or package test runs.
- Use `tempfile.TemporaryDirectory()` inside the test body for Hypothesis tests that write to disk — `tmp_path` is scoped per test function, not per example, so all examples in a single Hypothesis run share the same directory and contaminate each other.
- When a function internally calls `time.monotonic()` or `time.time()`, add a `_now: float | None = None` clock-injection parameter that defaults to the real clock in production. This makes it testable with Hypothesis without mocking.
- Fuzz harnesses live under `fuzz/` and use `PYTHONFUZZ_NO_ATHERIS=1` smoke mode in CI. Do not add production logic to fuzz files — they are excluded from ruff via `extend-exclude`.
- All new test files must have `pytestmark = pytest.mark.<marker>` at module scope. The CI `unit-tests` job filters to `-m "unit or regression"` — unmarked tests will not run in CI.

---

## Documentation

- Update `README.md` when commands, package responsibilities, architecture, environment variables, deployment, or user-visible behavior change. The README is the front door; do not leave it behind a code change.
- Documentation should focus on user tasks and public APIs; keep implementation details in docstrings or design docs unless they affect user decisions.
- All fenced code blocks should include a language marker.
- Structure explanations with context first, then example code, then caveats or edge cases.
- Use realistic examples that show why a feature matters and that readers can adapt directly.
- Present the recommended approach first; introduce alternatives explicitly and explain trade-offs.
- Prefer Sphinx/MyST cross-references and canonical sources over duplicated summaries or plain code formatting for navigational links.
- Explain trade-offs, rollout, rollback, observability, and failure behavior in design docs for non-trivial architecture changes.
- Include exact commands when documenting development, deployment, migration, smoke-test, backup, or restore procedures.
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
export AI_SESSION_DIR=".nimbus-dev/sessions"  # local fallback when Postgres is disabled
export NIMBUS_STATE_BACKEND="postgres" # set on Render
export DATABASE_URL="..."              # Render Postgres connection string
export AI_RATE_LIMIT_CAPACITY="10"      # per-user token bucket capacity (default: 10)
export AI_RATE_LIMIT_RPM="10"           # requests-per-minute refill rate (default: 10)
export NEW_RELIC_LICENSE_KEY="..."
export SENTRY_DSN="..."
export LAUNCHDARKLY_SDK_KEY="..."       # production only
# nimbus CLI
export NIMBUS_HOME="~/.nimbus"
export NIMBUS_CONTAINER="..."           # S3 bucket the LLM tools are pinned to
export NIMBUS_SAFE_ROOT="/home/user/workspace"
export NIMBUS_SESSION_DIR="~/.nimbus/sessions"
export GITHUB_CLIENT_ID="..."
export GITHUB_CLIENT_SECRET="..."
export CLOUD_STORAGE_SERVICE_BASE_URL="..."
export API_KEY="..."
# nimbus_slack (only when explicitly working on Slack)
export SLACK_SIGNING_SECRET="..."
export SLACK_CLIENT_ID="..."
export SLACK_CLIENT_SECRET="..."
export SLACK_BOT_TOKEN="..."
export NIMBUS_SLACK_PUBLIC_BASE_URL="..."
export NIMBUS_SLACK_STATE_SECRET="..."
export NIMBUS_SLACK_SECRET_KEY="..."
export NIMBUS_SLACK_STORE_BACKEND="postgres"
export NIMBUS_SLACK_DATABASE_URL="..."
export NIMBUS_SLACK_STATE_DIR=".nimbus-dev/slack"
export NIMBUS_SLACK_MODEL_MODE="auto"
export NIMBUS_SLACK_SESSION_DIR=".nimbus-dev/slack-sessions"
export NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE="100"
export NIMBUS_SLACK_FILE_SCAN_MAX_PAGES="10"
export NIMBUS_SLACK_MAX_FILE_BYTES="10485760"
```

Unit and integration tests should run without real credentials by mocking external calls.

### Render deployment

`render.yaml` defines the current Render deployment:

- `hw3-stage` auto-deploys to Render staging.
- `hw-3` deploys to Render production through a CircleCI deploy hook.
- Backend and Slack services use `/ready` for health-gated deploys.
- Render backend deployments set `NIMBUS_STATE_BACKEND=postgres` and `DATABASE_URL`.
- Render Slack deployments set `NIMBUS_SLACK_STORE_BACKEND=postgres` and the Slack store database URL.
- `scripts/render/start.sh` and `scripts/render/start-slack.sh` run idempotent migrations before serving traffic.
- Run `uv run python scripts/db/migrate.py` before serving backend traffic when migrating manually.
- Readiness should fail closed for missing secrets, stale schema, unavailable Postgres, and unavailable required provider/storage configuration.
- Production changes should include a smoke path for `/health`, `/ready`, unsigned protected routes, a signed chat turn where relevant, and duplicate/replay behavior where relevant.
- Use Render/CircleCI/Doppler for shared secrets; do not commit secret values.
- Treat free-tier infrastructure as demo-grade. If data durability matters, document the paid Postgres/backup plan and restore verification.

### mypy `exclude` for test directories

`pyproject.toml` sets `exclude = ["src/.*/tests/", "tests/", "fuzz/"]` in `[tool.mypy]`
because multiple `src/*/tests/` packages share the top-level `tests` name under
mypy's flat namespace resolution — without the exclude, mypy reports duplicate
module errors for `test_auth`, `test_router`, etc. across packages. Excluding
test directories is safe: pytest still runs them via the filesystem; mypy only
type-checks production code. The fuzz harnesses are excluded because they are
security/testing tools with optional fuzzing dependencies, not production
runtime modules.

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
- For platform changes, commit the code, config, docs, and smoke/rollback instructions together so the repo remains the source of truth.
- Keep migrations, generated clients, deployment manifests, and docs in the same review as the API or schema change that requires them.
- Prefer rollout-safe commits: a reviewer should be able to deploy, observe, and revert one change without also needing unrelated cleanup.
- Write commit subjects in the short, imperative, sentence-case style used in repositories such as `ssh-hypervisor`, `sshx`, and `classes.wtf`: `Fix KVM permission issue in CI`, `Add workflow_dispatch to CI`, `Update render.yaml deploy health checks`.
- Prefer a single descriptive line with no trailing period; avoid vague subjects such as `wip`, `misc`, or `updates`.
- When drafting a PR description or change summary, explain why the change is appropriate and call out any areas that merit careful review.
- PR descriptions for non-trivial work should name the contract, tests, docs, rollout/rollback notes, observability changes, and remaining risk.
- Branch from `main`; do not push directly to `main`.
- Keep pull request titles under 70 characters and reference issues with `closes #N` or `related #N` when relevant.

---

## Ask Before

Ask the user before you:

- change how `cloud_storage_api` types are used across packages
- add a new third-party dependency
- modify CI/CD configuration, release automation, or root tool configuration
- change Render service topology, state backend selection, migration behavior, backup/restore policy, or production readiness semantics
- add a queue, cache, worker fleet, new database, new external SaaS dependency, or new public protocol
- modify the `get_client_impl()` factory contract
- make a cross-package refactor whose impact is not clearly local
- introduce a new public abstraction or broad user-visible behavior change without a clear established pattern in the codebase

---

## Special Requests

- If the user asks for a review, lead with findings in this order: correctness, clarity, style/conventions, deduplication, and tests. Prioritize bugs, regressions, API contract drift, operational risk, and missing meaningful tests. Include file and line references where possible, keep the summary brief, and state explicitly if no findings were found.
- If the user asks for a simple command-driven fact, such as the current time, run the command and report the result.
- If the user asks whether an approach is good, best, or appropriate, answer that question directly before proposing or making code changes.
