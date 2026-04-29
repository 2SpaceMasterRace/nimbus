You need to become strong at interfaces, APIs, testing, integration, deployment, and disciplined engineering.

This repo expects you to think in layers:
- abstract contract
- concrete implementation
- HTTP service
- adapter/client
- tests
- docs
- CI/CD
- deployment


## The 80/20 Focus

If you only focus on the highest-leverage skills first, do these:
- Python + typing
- HTTP + FastAPI
- pytest
- interface/adapter/DI design
- Git + PR workflow
- uv + ruff + mypy
- S3/object storage basics
- AI tool calling
- deployment/env vars/secrets
- observability basics
- API contract negotiation
- writing small, testable changes


## Definition Of "Competent" Here

You're competent for this project when you can:
- clone and run the repo
- explain the architecture
- trace one request across layers
- add a small feature without breaking boundaries
- add or update tests
- run lint, type check, and targeted tests locally
- debug a failing integration test
- integrate one external API cleanly
- write the docs and PR description someone else needs
- deploy and verify a basic service
- explain latency, success rate, and failure rate for your system

## What plans.md adds to your learning list

You should add competence in:
- Release engineering: Nightly -> Beta -> Stable trains, changelogs, signed releases, automated publishing.
- Platform engineering: Nix, Terraform, Nomad, k0s/Kubernetes, reproducible environments, IaC.
- Advanced testing: property-based testing, mutation testing, contract testing, snapshot testing, fault injection, deterministic simulation.
- Security and supply chain: secret scanning, SBOMs, Semgrep, dependency/CVE scanning, OIDC, Vault, trusted publishing.
- Observability: OpenTelemetry, Sentry, Datadog, Prometheus/Jaeger, traces, error budgets, per-endpoint metrics.
- Reliability engineering: resumable workflows, checkpointing, adaptive concurrency, integrity verification, circuit breakers.
- Performance engineering: benchmarking, throughput curves, load testing, Locust, profiling release regressions.
- Formal methods awareness: TLA+, Alloy, invariants, consistency models.

## What tools like Linear, Notion, Slack add

These are not "extra PM fluff." They are part of senior execution.
- Linear/Jira: issue decomposition, prioritization, release trains, triage, ownership, milestones.
- Notion/docs systems: design docs, ADRs, RFCs, decision logs, onboarding docs, architecture communication.
- Slack: incident response, async coordination, stakeholder comms, escalation paths, bot workflows, release coordination.

A senior engineer is expected to turn architecture into an operating system for the team:
- clear tickets,
- clear docs,
- clear ownership,
- clear rollout plan,
- clear incident path.

## Agentic engineering to add

This should absolutely be on your list. Learn how to:
- use OpenCode, Claude Code, Codex, AmpCode as parallel workers, not chat toys;
- scope tasks tightly;
- require verification;
- review outputs like code review;
- keep one useful agent running in the background when it genuinely saves time;
- convert repeated agent work into scripts, wrappers, and repo conventions.

That matches the repo guidance in 08_ai_agents.md.

## MCPs

Yes, MCP fluency is now a real skill. You should know:
- how MCP servers expose tools safely;
- auth and permission boundaries;
- context scoping;
- failure handling and retries;
- how to compose multiple MCP tools into a workflow;
- how to avoid over-tooling and agent chaos.

This is basically the new "internal tooling/platform integration" layer for AI-assisted development.

## AGENTS.md / SKILLS.md

This is an important new documentation skill. You should be able to write repo guidance files that encode:
- architecture boundaries,
- allowed tools,
- testing commands,
- verification standards,
- coding conventions,
- review expectations,
- safety rules.

In this repo, AGENTS.md is already doing that job. If you use SKILLS.md, it would be a complementary file for standardized workflows or agent capabilities.


## Add These From Software Engineering at Google

New items I'd add beyond the earlier list:
- Think in time, scale, and trade-offs as first principles, not just "write code."
- Internalize software engineering = programming integrated over time.
- Learn Hyrum's Law: every observable behavior will eventually have a user.
- Build for changeability, not just "works today."
- Notice boiled frog problems: slow, compounding process decay.
- Prefer policies that scale linearly or better with human effort.
- Use shift left thinking for bugs, security, testing, rollout risk, and compatibility.
- Understand the Beyonce Rule: if a behavior matters, put a CI test on it.
- Optimize for the reader, not the author.
- Treat consistency as an engineering force multiplier.
- Understand bus factor and eliminate single points of human knowledge.
- Build psychological safety so people ask questions early.
- Learn humility, respect, trust as operational engineering skills, not soft fluff.
- Use knowledge sharing systems: docs, talks, office hours, mailing lists, code review.
- Understand readability as standardized mentorship through code review.
- Learn engineering productivity measurement via Goals / Signals / Metrics.
- Learn the QUANTS lens for productivity: quality, attention, intellectual complexity, tempo, satisfaction.
- Treat documentation like code: ownership, review, versioning, freshness.
- Learn test size vs test scope.
- Prefer testing behavior, public APIs, and state, not implementation details.
- Learn when to use fakes, when to use real implementations, and when not to mock.
- Understand larger tests, canaries, probers, and chaos engineering.
- Learn deprecation as a first-class engineering process.
- Understand trunk-based development, source of truth, and the one-version rule.
- Learn why code search, build systems, and static analysis are strategic advantages.
- Understand large-scale changes as an org capability.
- Learn CI and CD as product-quality systems, not just pipelines.
- Learn compute as a service, containers, managed compute, and architecting for failure.
- Add equity/inclusion as a design requirement, not an HR side topic.
- Learn leadership patterns like Always Be Deciding, Always Be Leaving, Always Be Scaling.

## Fancy Distributed Systems Theory

For senior system design, add these hard concepts:
- CAP and PACELC.
- FLP impossibility.
- Linearizability, sequential consistency, causal consistency, eventual consistency.
- Read-your-writes, monotonic reads, session guarantees.
- Replication models: leader-follower, multi-leader, leaderless.
- Quorum math: N, R, W.
- Consensus: Raft, Paxos, leader election, log replication, terms, quorums.
- Split brain and how to prevent it.
- Sharding strategies: range, hash, consistent hashing.
- Hot keys, hotspots, and rebalancing.
- Distributed transactions: 2PC, 3PC, sagas.
- Outbox / inbox patterns and idempotent consumers.
- The exactly-once myth and what "effectively-once" really means.
- Lamport clocks, vector clocks, hybrid logical clocks, clock skew.
- MVCC, snapshots, isolation levels, serializability, snapshot isolation.
- Storage internals: B-trees, LSM trees, WAL, compaction, bloom filters.
- Messaging systems: partitions, ordering, consumer groups, backpressure.
- Service discovery, leases, heartbeats, failure detectors.
- ZooKeeper, etcd, Consul class systems.
- Multi-region design: active-active, active-passive, failover, quorum across regions.
- RPO and RTO.
- Load shedding, circuit breakers, bulkheads, hedged requests.
- Tail latency and why p99 matters more than averages.
- Cache design: local, distributed, write-through, write-back, invalidation, stampede.
- Idempotency keys, retries, exponential backoff, jitter.
- Event sourcing and CQRS basics.
- Batch vs stream, push vs pull, sync vs async, stateful vs stateless.

## Performance Engineering

Add these performance topics explicitly:
- Build a performance cost model: CPU, memory, disk, network, serialization.
- Know latency vs throughput.
- Think in p50/p95/p99, not just averages.
- Learn queueing theory basics and Little's Law.
- Learn profiling before optimizing.
- Use flame graphs, tracing, sampling profilers, heap profilers.
- Understand cache locality, branch prediction, vectorization, SIMD.
- Understand allocation, copying, GC, object overhead, paging, fragmentation.
- Learn sequential vs random I/O.
- Learn when batching helps and when it hurts.
- Understand threads, processes, async I/O, and backpressure.
- Learn NUMA, memory bandwidth, and lock contention at least conceptually.
- Know how to design and read load tests, stress tests, soak tests, and capacity tests.
- Learn coordinated omission in latency measurement.
- Learn perf, eBPF, and system-level observability basics.
- If AI/ML is in scope, learn GPU/TPU bottlenecks, data transfer costs, and batching.

## Senior SWE System Design Skills

These are often the difference between mid-level and senior:
- Write strong design docs with alternatives, risks, rollout, metrics, and failure modes.
- State invariants explicitly.
- Do trade-off analysis instead of solution pitching.
- Ask better design questions: scale, SLOs, failure modes, cost, data model, migration path.
- Design for operability: dashboards, alerts, runbooks, ownership.
- Design for migration, rollback, and deprecation from day one.
- Treat security, privacy, and compliance as design inputs.
- Learn to reason about blast radius.
- Get good at incident response and blameless postmortems.
- Understand developer productivity as a systems problem too.
- Know build/release/deploy systems, not just app architecture.
- Learn to simplify aggressively; senior engineers often win by removing complexity.
- Build product sense: know when a technically elegant system is the wrong business choice.
- Learn to influence across teams without authority.
- Be able to say "no" to bad abstractions, premature scale, and unfunded churn.

## Mental Models To Practice

If you want to think like a senior, practice these constantly:
- What changes over time?
- What fails first?
- What is the bottleneck now?
- What is the hidden contract?
- What is the rollback story?
- What is the migration story?
- What is the oncall burden?
- What happens at 10x scale?
- What is the human process around this system?
- Can this be made smaller, simpler, more local, more testable?


## Non-Negotiables

These are the non-negotiables to be productive in this repo at all.
- Python well enough to read and write real code without guessing.
- Static typing with mypy --strict.
- Object-oriented design with interfaces/ABCs.
- Dependency injection.
- HTTP basics: methods, status codes, headers, JSON, multipart forms.
- REST API design.
- FastAPI and Pydantic.
- pytest fundamentals.
- Mocking external systems correctly.
- Git and GitHub PR workflow.
- Terminal literacy.
- Environment variables and secret handling.
- Reading docs and existing code before changing anything.

Concretely, you should be comfortable with:
- pathlib
- file I/O
- exceptions and exception translation
- context managers
- class design
- TYPE_CHECKING
- BinaryIO
- JSON
- request/response flow
- curl
- uv
- ruff
- mypy
- pytest

## Tier 1 Concepts

These are the engineering ideas behind the codebase.
- Contract vs implementation.
- Adapter pattern.
- Service layer vs client library.
- Generated client vs handwritten code.
- Domain exceptions vs transport exceptions.
- End-to-end flow tracing.
- Backward compatibility.
- Small, reversible changes.
- Testing observable behavior, not internals.
- Keeping dependency direction clean.

## Tier 1 Repo-Specific Knowledge

These are the repo rules you need in your head.
- cloud_storage_api is external and should not be vendored.
- aws_client_impl is the concrete storage implementation.
- aws_client_service exposes storage over FastAPI.
- aws_client_adapter re-implements the same storage contract over HTTP.
- Each implementation exposes get_client_impl().
- Generated OpenAPI client code is not edited by hand.
- Strict linting and strict typing are part of the normal dev loop, not cleanup at the end.
- Tests are split into unit, integration, and e2e.

## Tier 2: Needed For HW3

These are the skills required to actually deliver the homework well.
- AI API integration.
- Prompt/message format design.
- Tool calling / function calling.
- JSON schema for tool definitions.
- Provider abstraction.
- Rate limits, timeouts, retries, and error handling.
- Cost, privacy, and credential handling.
- Cross-service integration.
- OpenAPI basics and generated clients.
- Deployment basics.
- Observability basics.
- Collaboration across teams on shared API contracts.

For AI specifically, learn:
- Chat completion request/response shape.
- System/user/tool messages.
- Tool call loop.
- Structured outputs.
- Model selection tradeoffs.
- Provider quirks behind "OpenAI-compatible" APIs.
- Deterministic testing around nondeterministic models.
- When to mock provider calls and when to hit a fake HTTP server.
- How to separate "AI client" from "AI-powered application logic."

For cross-vertical work, learn:
- How to read another team's API before touching your code.
- How to ask for missing details precisely.
- How to negotiate a minimal stable contract.
- How to design around version drift and flaky assumptions.
- How to write integration tests that prove two systems actually work together.

## Tier 3: To Become Fast And Strong

This is what makes you noticeably better than someone who can merely "make it work."
- Reliability engineering.
- Idempotency.
- Timeout and retry design.
- Failure-mode thinking.
- Observability with logs, metrics, and traces.
- Performance profiling.
- API versioning.
- Release discipline.
- IaC fundamentals.
- Writing strong docs.
- Writing good PRs and review comments.
- Debugging CI failures quickly.
- Knowing when not to add abstractions.

For AI systems specifically:
- Evals.
- Prompt iteration discipline.
- Guardrails and unsafe-tool prevention.
- Model fallback strategy.
- Token budgeting.
- Latency/cost tradeoffs.
- Tool reliability and schema quality.
- Hallucination containment by designing deterministic tool boundaries.

## Cloud And Platform Skills

You are on a cloud storage team, so these matter even if your AI work is only one part.
- S3/object storage concepts.
- Buckets/containers, keys, metadata, content types.
- Upload vs download semantics.
- Multipart upload.
- Auth and IAM basics.
- API keys and OAuth basics.
- Health checks.
- Deployment configs.
- Container basics.
- CI/CD pipelines.
- Secrets management.
- Production environment variables.
- "Works locally" vs "works in CI" vs "works deployed."

## Developer Tools To Know

These are the actual tools this repo either uses directly or clearly expects you to understand.
- uv
- pytest
- ruff
- mypy
- FastAPI
- Pydantic
- boto3
- requests
- httpx
- curl
- gh
- Git
- CircleCI
- Fly.io
- Docker
- Sphinx
- OpenAPI / generated clients

Helpful extras:
- rg / ripgrep
- jq
- tmux
- lazygit
- Nix

## Product And Project Skills

These matter more than students usually realize.
- Turning vague assignment text into clear acceptance criteria.
- Scoping work so it fits the deadline.
- De-risking early.
- Identifying blockers before they become emergencies.
- Writing interface memos.
- Negotiating API contracts with other teams.
- Prioritizing "must pass the rubric" before "cool extra features."
- Defining success metrics.
- Writing clean PR descriptions.
- Planning demos.
- Making tradeoffs explicit.
- Knowing when to ask instead of guessing.

You should be able to answer:
- What is the smallest acceptable version of this feature?
- What proves it works?
- What could break in CI or prod?
- What dependency do we have on another team?
- What do we do if their API changes or is incomplete?
- What are the required screenshots, tests, docs, and demo artifacts?

## Communication Skills

These separate fast engineers from chaotic ones.
- Writing concise technical summaries.
- Asking sharp questions.
- Stating assumptions explicitly.
- Explaining tradeoffs.
- Documenting how to run and verify code.
- Reporting findings with file references.
- Leaving behind work others can continue.

## Engineering Habits

These are the habits that make you faster.
- Read before editing.
- Search before assuming.
- Make the smallest correct change.
- Run the smallest relevant test first.
- Keep one mental model of the request path end to end.
- Treat types as design tools.
- Write tests for behavior changes.
- Avoid mixing unrelated work.
- Preserve public contracts unless change is intentional.
- Leave the tree cleaner, not more surprising.

## What To Read In This Repo

Read these in this order.
1. AGENTS.md
2. README.md
3. DESIGN.md
4. CONTRIBUTING.md
5. pyproject.toml
6. src/aws_client_impl/aws_client_impl/s3_client.py
7. src/aws_client_service/aws_client_service/main.py
8. src/aws_client_adapter/aws_client_adapter/service_adapter.py
9. tests/integration/test_client_integration.py
10. tests/integration/test_service_integration.py
11. tests/integration/test_adapter_integration.py
12. tests/integration/test_oauth_integration.py
13. tests/e2e/test_service_e2e.py
14. tests/e2e/test_main_application.py
15. .circleci/config.yml
16. Homeworks/[DRAFT] hw3-assignment.pdf

Also note the repo already has internal guides for shell, git, and reproducible environments under docs/source/guide/.
