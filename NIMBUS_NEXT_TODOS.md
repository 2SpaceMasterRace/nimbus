# Nimbus Next TODOs

Purpose: a future session should be able to open this file, read `AGENTS.md`,
`NIMBUS_STATUS.md`, and this file, and continue work without re-discovering the
 main risks in the AI integration.

## HW3 Framing

This backlog is specifically for HW3.

HW3 is not about building chat completion from scratch. It is about shipping a
cleanly integrated AI-powered system that:

- integrates an external AI provider,
- integrates at least one other vertical through a shared API contract,
- is deployable and observable,
- and is robust enough that a separate chat-wrapper team can build against it.

For Team 2, that means the Nimbus AI service must be a stable, well-documented,
wrapper-facing contract for chat frontends such as Slack.

## Read First

1. `AGENTS.md`
2. `NIMBUS_STATUS.md`
3. `NIMBUS_HW3_SYSTEM_DESIGN.md`
4. `docs/source/nimbus-ai-service.md`

## How To Use This File

- Start with the read-first list above.
- Assume `AGENTS.md` is the behavioral contract for how to work in this repo.
- Assume `NIMBUS_STATUS.md` is the current checkpoint.
- Use this file as the reviewed implementation backlog, not as a brainstorming
  scratchpad.
- Work from Priority 0 upward unless the user explicitly reprioritizes.

## Merged Markdown Sources

This file now merges open work from these markdown sources:

- `AGENTS.md`
- `NIMBUS_STATUS.md`
- `NIMBUS_HW3_SYSTEM_DESIGN.md`
- `docs/source/nimbus-ai-service.md`
- `plans.md`

Other markdown files in the repo were scanned as well. If they contained no
explicit outstanding work items, they were not copied here.

## Current Review Summary

**Last updated: 2026-04-28**

The blocking correctness gaps in Priority 0 are closed. Test infrastructure
(Priority 7.1 and the fuzz harness work) is now complete. The remaining work is
product-contract completion and production-readiness.

Main open gaps:

- the runtime is still embedded in `ai_server` instead of extracted cleanly
- telemetry is still wired manually instead of fed to a structured dashboard
- advanced testing beyond the now-hardened deterministic integration/e2e/BDD
  layer (mutation testing, fault injection, Schemathesis) is still open

## Priority 0: Blocking Correctness Gaps

### 0.0 Finish the contract completely and the full Slack/Nimbus functionality

This is the top Priority 0 directive.

Interpretation:

- complete the wrapper-facing Nimbus contract, not just the text-turn subset,
- fill in the missing product behaviors needed for the Slack wrapper team,
- carry the work through implementation, tests, and docs,
- and finish each completed slice cleanly before moving on.

Execution rule for future sessions:

- when one meaningful slice is finished, update the docs,
- run the relevant local CI gates,
- and create a focused git commit before moving to the next slice.

Minimum acceptance criteria:

- wrapper-facing route supports the real Nimbus behaviors the product promises,
- attachment/file workflows are part of the public contract,
- confirmation/destructive flows are represented explicitly,
- runtime, transport, and reliability behavior are tested and documented,
- local verification stays green after each slice.

### 0.1 Wire the wrapper route to real chat-safe storage tools

Status update:

- `/ai/chat/turn` now passes real read-only storage tools to the AI path.
- `list_files` and `get_file_info` are covered by wrapper-route tests.
- `delete_file` is still intentionally withheld from the wrapper route until
  machine-readable confirmation outcomes and stateful confirmation handling are
  implemented.

Problem:

- the route still needs the explicit confirmation contract before destructive
  storage actions can be safely exposed end to end.
- tool policy is still embedded in `ai_server` instead of an extracted runtime.

Files:

- `src/ai_server/ai_server/router.py`
- `src/ai_server/ai_server/slack_tools.py`
- likely future home: extracted runtime module

Acceptance criteria:

- wrapper-facing turns can list files and get file metadata through the AI path
- destructive actions remain disabled or explicitly confirmation-gated
- tests prove the wrapper route can actually exercise the tool surface

### 0.2 Finalize the file/attachment ingestion contract

Status update:

- `POST /ai/chat/turn` now accepts a bounded `attachments[]` metadata contract
- attachment schema, size/content-type bounds, wrapper/Nimbus ownership split,
  and exact Slack file -> Nimbus mapping are documented in
  `docs/source/nimbus-ai-service.md`
- validated attachment metadata is exposed to the AI turn as context so the
  field is immediately useful to the wrapper team

Problem:

- byte-carrying upload ingestion is still a later execution slice; the current
  contract is metadata-only on `POST /ai/chat/turn`.

Files:

- `docs/source/nimbus-ai-service.md`
- future wrapper-facing request model in `src/ai_server/ai_server/router.py`

Acceptance criteria:

- define an attachment array or equivalent wrapper-owned reference model
- define size/content-type bounds
- define who fetches file bytes and where validation occurs
- document exact Slack file -> Nimbus request mapping

### 0.3 Fix conversation ID overflow risk

Status update:

- wrapper-facing `conversation_id` values now remain logical/public IDs while
  session persistence uses a deterministic hashed filename stem when needed
- worst-case identifier lengths are covered in wrapper-contract tests
- direct session save/load/list behavior for long IDs is covered in session tests

Problem:

- the response contract still exposes the full logical conversation identity,
  so any future components must continue treating it as a logical key rather
  than an on-disk filename.

Files:

- `src/ai_server/ai_server/router.py`
- `src/ai_server/ai_server/sessions.py`

Acceptance criteria:

- either shorten the derived persisted key deterministically (e.g. stable hash)
- or change the session persistence scheme so long conversation identities are
  supported safely
- add tests with worst-case field lengths

### 0.4 Fix multi-workspace rate-limit identity

Status update:

- wrapper rate limiting now uses the real principal key
  `platform:workspace_id:user_id`
- the same `user_id` in two workspaces no longer shares one token bucket
- coverage lives in `src/ai_server/tests/test_wrapper_contract.py`

Problem:

- the remaining gap is that idempotency and replay protection are still
  process-local even though wrapper principal identity is now correct.

Files:

- `src/ai_server/ai_server/router.py`

Acceptance criteria:

- rate limiting uses a real principal key such as `platform:workspace_id:user_id`
- tests cover same `user_id` in two workspaces without cross-talk

### 0.5 Replace process-local idempotency and replay protection for real deployment

Status update:

- signed-request nonce state and wrapper idempotency state now persist under
  `AI_SESSION_DIR/_request_state`
- replay and idempotent retry behavior now survive service restarts on the
  mounted Fly.io volume
- the deployment contract is explicitly one machine / one process for now,
  matching `fly.toml`
- coverage lives in `src/ai_server/tests/test_request_state.py` and
  `src/ai_server/tests/test_wrapper_contract.py`

Problem:

- a future multi-machine deployment still needs a real shared backend instead of
  one machine's mounted volume; keep that follow-up under Priority 2.3.

Files:

- `src/ai_server/ai_server/auth.py`
- `src/ai_server/ai_server/router.py`

Acceptance criteria:

- either constrain deployment explicitly to one replica for now and document it
- or move nonce/idempotency state into a shared store
- add expiry semantics and observability around replay/idempotency state

## Priority 1: Product-Contract Gaps

### 1.1 Implement richer outcome types for the wrapper route

Problem:

- `ChatTurnResponse.outcome` only allows `"reply"`.
- `confirmation_required` is always `False`.
- docs and design describe confirmation flows that the runtime cannot yet
  express.

Files:

- `src/ai_server/ai_server/router.py`
- future runtime extraction

Acceptance criteria:

- define outcome variants such as `reply`, `confirmation_required`, `error`,
  maybe `partial_success`
- model confirmation state explicitly
- add tests for destructive confirmation flow

### 1.2 Extract the shared runtime out of `ai_server`

Problem:

- the wrapper contract is stable enough to build against, but the orchestration
  logic is still embedded in `ai_server`.
- that increases coupling and makes reuse across CLI and future chat adapters
  harder.

Files:

- `src/ai_server/ai_server/router.py`
- future package: `src/nimbus_runtime/`

Acceptance criteria:

- `ai_server` becomes a thin HTTP adapter
- session orchestration, prompt assembly, and tool policy move into a shared
  runtime layer
- tests separate transport behavior from runtime behavior

### 1.3 Update docs/comments that still describe the old auth picture

Problem:

- some comments and module docs still say every non-health route uses `X-API-Key`
- that is no longer true because `/ai/chat/turn` uses signed auth

Files:

- `src/ai_server/ai_server/router.py`
- related docs if stale wording is found

Acceptance criteria:

- docs match reality everywhere

## Priority 2: Production-Readiness Foundations

### 2.1 Add metrics at the wrapper boundary and AI boundary

Useful `plans.md` items:

- local Prometheus or Jaeger
- request latency, success rate, failure rate
- circuit breaker state as an observable metric

Files:

- `src/ai_server/ai_server/router.py`
- possibly new telemetry module in a future runtime package

Acceptance criteria:

- counters and histograms for wrapper-facing route calls
- visibility into idempotent replay, auth failures, provider failures, and tool calls

### 2.2 Add circuit breaking for the AI provider

Useful `plans.md` item:

- `Circuit breaker for OpenRouter: open after N consecutive failures, half-open probe, close on success`

Files:

- `src/openrouter_ai_client_impl/openrouter_ai_client_impl/openrouter_client.py`

Acceptance criteria:

- repeated provider failures stop hammering the upstream
- breaker state is observable

### 2.3 Decide the shared-state backend for scale-up

Useful `plans.md` item:

- `Pluggable conversation store (JSON on disk today, Redis later)`

Needs:

- shared nonce replay state
- shared idempotency state
- shared conversation/session state if we run multiple replicas

Acceptance criteria:

- document the single-replica assumption if we keep it
- or introduce a shared backend intentionally

### 2.4 Upgrade service-to-service auth when infra allows it

Current state:

- HMAC-signed requests improve over the legacy API key
- still based on a shared symmetric secret

Useful `plans.md` items:

- OIDC federation
- Vault / runtime secret distribution

Acceptance criteria:

- evaluate workload identity or short-lived signed tokens for wrapper -> Nimbus
- keep HMAC as fallback if infra is not ready

### 2.5 Add AI e2e to CI

Carried from `NIMBUS_STATUS.md`.

Status update:

- `src/ai_server/tests/test_e2e.py` now treats the signed wrapper-facing route
  as the only supported chat entrypoint
- live wrapper-path e2e now verify:
  - missing signed headers -> `401`
  - signed `/ai/chat/turn` success response shape
  - slash-command-shaped request anchoring
  - idempotent retry reuse on the wrapper route
- CircleCI now has a dedicated `ai-e2e-tests` job for the live `ai_server` suite,
  wired as an opt-in/context-gated step before deployment

Acceptance criteria:

- add an `ai-e2e-tests` CI job
- make it opt-in by environment/context, not accidental
- run the live deployed `ai_server` e2e suite in CI when configured

### 2.6 Add deployment verification for the AI service

Carried from `NIMBUS_STATUS.md` and `plans.md`.

Status update:

- CircleCI now runs a post-deploy `verify-fly-deploy` job after `deploy-fly`
- the job checks the mounted `/data/sessions` path over `flyctl ssh console`
- the job smoke-tests `/health`, `/ai/health`, `/guide/`, unsigned
  `/ai/chat/turn` rejection, and a signed wrapper request against the deployed
  service

Acceptance criteria:

- verify persistent session storage/volume behavior for deployed Nimbus
- verify protected AI-service reachability model matches the wrapper design
- smoke-test the deployed health endpoint and wrapper-facing path

### 2.7 Add rolling conversation summary (FM6)

Carried from `NIMBUS_STATUS.md`.

Acceptance criteria:

- summarize old turns into a bounded conversation summary message
- preserve tool/result semantics and avoid corrupting the turn history
- keep the summarization trigger bounded and idempotent

### 2.8 Document and enforce step-budget and concurrency math

Carried from `NIMBUS_STATUS.md`.

Acceptance criteria:

- explain why the current step budget exists
- tie budget choice to upstream rate-limit realities
- enforce lower limits or different defaults if upstream/provider conditions change

## Priority 3: Wrapper-Contract Completion

### 3.1 Define Slack file and attachment ingestion end to end

Carried from `docs/source/nimbus-ai-service.md` and `NIMBUS_HW3_SYSTEM_DESIGN.md`.

Acceptance criteria:

- define the wrapper-facing attachment schema
- define who fetches Slack file bytes and under what validation rules
- define size, type, and count limits
- define failure behavior for partial attachment ingestion

### 3.2 Complete destructive confirmation flows

Carried from `NIMBUS_HW3_SYSTEM_DESIGN.md`.

Acceptance criteria:

- wrapper-facing responses can represent confirmation-required outcomes
- runtime stores and validates pending actions explicitly
- same actor and same conversation must confirm destructive work

### 3.3 Finalize Slack command and message semantics

Carried from `docs/source/nimbus-ai-service.md` and `NIMBUS_HW3_SYSTEM_DESIGN.md`.

Status update:

- wrapper docs now spell out canonical Slack mapping rules for:
  - top-level mentions/messages
  - thread replies
  - direct messages
  - slash commands, including one-shot vs thread-attached command behavior
- `/nimbus recent` now has a documented MVP backing-store strategy:
  persisted conversation history first, wrapper-local cache second, Redis later
- wrapper docs now include a Python-first reference flow for:
  - Slack event normalization
  - slash-command normalization
  - Nimbus request signing
  - wrapper-side response handling by explicit `outcome`
- wrapper contract tests now cover the documented Slack shapes directly:
  - top-level message anchoring
  - thread reply anchoring
  - DM anchoring
  - slash command synthetic IDs
  - slash-command retry/idempotency behavior

Concrete implementation sequence for this repo:

1. Freeze Slack mapping semantics in `docs/source/nimbus-ai-service.md`.
2. Keep the wrapper path Python-first: document one reference signer and one
   normalization flow instead of inventing a browser/TypeScript SDK.
3. Encode those documented shapes in `src/ai_server/tests/test_wrapper_contract.py`.
4. Keep `ai_server` transport-thin and `nimbus_runtime` semantics-heavy.
5. Ship a Python reference helper and HTTP smoke path for the wrapper-facing
   route.
6. Keep live signed wrapper e2e in CI so deployed behavior does not drift.

Acceptance criteria:

- top-level mentions, thread replies, DMs, and slash commands all have explicit mapping rules
- `/nimbus recent` has a defined backing store strategy
- wrapper docs include exact request mapping and retry guidance

## Priority 4: CLI And Runtime UX Backlog

### 4.1 CLI UX polish

Carried from `NIMBUS_STATUS.md`.

Acceptance criteria:

- evaluate `prompt_toolkit` for keybindings and editable history
- support up-arrow history and better line editing if the dependency is justified
- consider `Ctrl-C` as cancel-in-flight rather than immediate exit
- consider `Ctrl-L` clear-screen behavior

### 4.2 Add `/ping` and `/status` diagnostics

Carried from `NIMBUS_STATUS.md`.

Acceptance criteria:

- show provider reachability
- show session size / token-budget hints
- show current model/fallback/runtime posture clearly

### 4.3 Decide how CLI should share the future runtime

Carried from `NIMBUS_HW3_SYSTEM_DESIGN.md`.

Acceptance criteria:

- CLI reuses runtime behavior where it reduces duplication
- CLI-specific UX stays local to CLI code
- no regression to the current local REPL workflow

### 4.4 `/nimbus recent` strategy

Carried from `NIMBUS_HW3_SYSTEM_DESIGN.md`.

Status update:

- the documented MVP stance is now wrapper-local first
- wrapper docs explicitly recommend:
  - wrapper-local recent-command state first
  - Nimbus conversation history only if that is simpler for the wrapper
  - no new Nimbus API surface for `/nimbus recent` in the MVP

Acceptance criteria:

- decide whether recent prompts come from session history or wrapper-local state
- do not require Redis for the MVP
- leave room for Redis/shared cache later if multi-instance wrappers appear

## Priority 5: AI Runtime And Provider Hardening

### 5.1 Migrate from `run_sync` thread offload to native async if it earns its keep

Carried from router follow-up notes and `NIMBUS_STATUS.md`.

Acceptance criteria:

- evaluate whether `agent.run()` materially improves backpressure and cancellation
- keep behavior and tests stable if migrated
- do not do this merely for fashion

### 5.2 Auto-model discovery and model selection policy

Carried from `plans.md` and `NIMBUS_STATUS.md`.

Acceptance criteria:

- discover candidate models from the provider API with useful filters
- keep free-tier caveats documented
- support stable defaults and predictable fallback behavior

### 5.3 Benchmark script cleanup

Carried from `NIMBUS_STATUS.md`.

Acceptance criteria:

- move or demote `scripts/benchmark_models.py` if it causes operator confusion
- document quota risk clearly
- consider `--dry-run` or safer placement under a dev-only path

## Priority 6: Documentation And Contract Quality

### 6.1 Keep wrapper docs authoritative

Carried from `docs/source/nimbus-ai-service.md` and `AGENTS.md`.

Status update:

- `docs/source/nimbus-ai-service.md` now includes stable examples for all four
  wrapper outcomes:
  - `reply`
  - `confirmation_required`
  - `partial_success`
  - `error`
- the docs now point directly at the Python wrapper helper module and the signed
  smoke client
- wrapper-facing auth now centers on the signed request contract

Acceptance criteria:

- wrapper contract docs stay aligned with code and tests
- request and response examples are stable and beginner-readable
- signed wrapper auth remains clear in docs and examples

### 6.2 Improve repo docs foundations

Carried from `plans.md`.

Acceptance criteria:

- proper quickstart and architecture overview
- more API reference generated from docstrings
- doctest integration where practical
- multi-version docs if the project keeps evolving after HW3

### 6.3 Add changelog and governance docs

Carried from `plans.md`.

Acceptance criteria:

- changelog generation flow exists
- governance/security docs exist if the repo is used beyond the class deliverable

## Priority 7: Testing And Correctness Expansion

### 7.1 Property and stateful testing — DONE

Status: **Complete as of Sprint 4 (2026-04-26).**

What was built:

- 38 Hypothesis property-based tests across 4 files (`pytest -m property`)
- `test_conversation_properties.py`: `_as_int` coercion, token estimate bounds, round-trip serialisation, `ConversationMachine` stateful machine (bounded-history + orphan-TOOL + round-trip invariants via `RuleBasedStateMachine`)
- `test_session_properties.py`: validation oracle, file-stem determinism/routing, short/long ID round-trip persistence (uses `tempfile.TemporaryDirectory` per example, not `tmp_path`)
- `test_auth_properties.py`: sign→verify self-consistency, body/nonce/timestamp sensitivity, secret isolation, payload determinism
- `test_router_properties.py`: `ChatTurnRequest` field validation, `_decoded_base64_size` formula, token-bucket arithmetic (first allowed, exhaustion, bounds, None-principal)
- 3 Atheris fuzz harnesses under `fuzz/`: conversation deserialisation, session-ID path-escape, request-state deserialisation — all run in CI as `fuzz-smoke` job
- `docs/source/testing.md`: comprehensive guide for all five test categories

Remaining testing gaps (not yet done):

- property tests for S3 key generation and storage operation commutativity
- fault-injection harness (7.3) and mutation testing — still open
- network fault simulation via Toxiproxy — still open
- OpenAPI contract drift detection via Schemathesis — still open
- structured log shape and sensitive-data tests — still open

### 7.2 Contract testing for integrations

Carried from `plans.md`.

Status update:

- `src/ai_server/tests/test_wrapper_contract.py` encodes the documented
  Slack mapping contract directly, including:
  - top-level message anchoring
  - thread reply anchoring
  - DM anchoring
  - slash-command synthetic IDs
  - slash-command idempotent retry behavior
- `src/ai_server/tests/test_wrapper_client.py` verifies the Python reference
  helper functions for normalization and signing

Acceptance criteria:

- add contract testing around wrapper/service and cross-team integration boundaries
- avoid depending on live remote services for every contract check

### 7.3 Deterministic fault-injection harness

Carried from `plans.md`.

Acceptance criteria:

- deterministic replay for failure scenarios
- explicit simulation of partial failures, retries, and recovery

### 7.4 Mutation/random-order/snapshot support

Carried from `plans.md`.

Acceptance criteria:

- strengthen tests against brittle assumptions and accidental drift

Status update:

- BDD acceptance support is now implemented with `pytest-bdd`.
- Feature files live under `tests/bdd/features/` and cover signed replies,
  signed-auth failures, destructive confirmation, and attachment ingestion
  outcomes.
- CircleCI now has a dedicated `bdd-tests` job required by `coverage-gate`.
- Remaining 7.4 work is mutation testing, random-order testing, and optional
  snapshot support.

## Priority 8: Tooling, CI, And Developer Productivity Foundations

### 8.1 Pre-commit and canonical task entry points

Carried from `plans.md`.

Status update:

- this is still open and is now one of the highest-leverage developer
  productivity follow-ups
- recommended concrete shape for this repo:
  - add `.pre-commit-config.yaml`
  - add one canonical task runner surface such as a `justfile`
  - make the common paths one-command and hard to misuse:
    - `just setup`
    - `just lint`
    - `just test`
    - `just docs`
    - `just run-nimbus`
    - `just smoke-wrapper`
- do not automate `git push`; automate the checks that should happen before a
  commit or push instead

Acceptance criteria:

- pre-commit hooks for formatting, linting, and basic hygiene
- one canonical developer command surface such as a `justfile` or equivalent

### 8.2 Stronger CI and supply-chain posture

Carried from `plans.md`.

Status update:

- CircleCI now has a dedicated `ai-e2e-tests` job for the live `ai_server`
  wrapper-facing path
- CircleCI now caches `uv` artifacts plus `.mypy_cache`, `.ruff_cache`, and
  `.pytest_cache` where they improve repeat-job latency
- `lint`, `docs-build`, `unit-tests`, and `integration-tests` now run in
  parallel for faster feedback on ordinary pushes
- `unit-tests` now uses CircleCI timing data with `parallelism: 4` so the
  largest test job is sharded across four executors instead of one
- CI now has a dedicated `docs-build` job rather than relying on local-only docs
  verification
- live AWS and deployed-Nimbus jobs now run only on `hw-3`, which keeps normal
  branch pipelines focused on the fast local validation path
- coverage HTML/XML generation is centralized in `coverage-gate` rather than
  repeated in every test job
- deploys are now followed by `verify-fly-deploy`, which checks the mounted Fly
  session path, `/health`, `/ai/health`, `/guide/`, unsigned wrapper auth
  rejection, and a signed wrapper smoke turn
- installs remain reproducible with `uv sync --frozen`
- the main remaining supply-chain follow-up is dedicated dependency or secret
  scanning if it proves worth the extra CI noise for this repo size
- explicit non-goals for now:
  - no Bazel migration
  - no sandbox-based developer workflow
  - no Stripe-style selective test execution yet; the repo is not large enough
    for that complexity to pay off today

Acceptance criteria:

- reproducible installs in CI
- pinned tool versions
- secret scanning / dependency scanning / security linting where worthwhile

### 8.3 Type-checking improvements

Carried from `plans.md`.

Acceptance criteria:

- consider `pyrefly` alongside mypy if it finds real narrowing bugs
- keep strict type guarantees without adding noise

### 8.4 Hurl or equivalent HTTP contract smoke layer

Carried from `plans.md`.

Status update:

- instead of Hurl, this repo now ships a Python `httpx` equivalent:
  `scripts/ai_server_wrapper_smoke.py`
- the smoke client exercises the real signed `/ai/chat/turn` contract for:
  - message/app-mention/thread/DM-shaped requests
  - slash-command-shaped requests
- wrapper docs now include copy-pasteable smoke commands that map directly to the
  documented request examples

Acceptance criteria:

- runnable HTTP contract checks for service endpoints
- docs examples map to actual testable requests

### 8.5 Fast local developer loop

Carried from the wrapper/team productivity review.

Acceptance criteria:

- a fresh clone can be bootstrapped with one documented command path
- common caches are preserved locally so repeated lint/type/test runs stay fast
- developers can keep Nimbus running on a devbox with one stable command such as
  `just run-nimbus` or equivalent
- the local workflow stays Python-native (`uv`, `ruff`, `mypy`, `pytest`) and
  does not require Bazel or sandbox orchestration

## Priority 9: Observability, Load, And Infra Follow-Through

### 9.1 Local observability stack and screenshots

Carried from `plans.md`.

Acceptance criteria:

- local Prometheus or Jaeger path exists
- traces/metrics can be shown in docs or demo material

### 9.2 Load testing for Nimbus and chat flows

Carried from `plans.md`.

Acceptance criteria:

- define realistic load scenarios for wrapper-facing AI routes
- observe saturation, p99 latency, and failure behavior

### 9.3 Secret distribution and service identity hardening

Carried from `plans.md`.

Acceptance criteria:

- reduce long-lived secrets where infrastructure allows
- use runtime secret distribution intentionally if the deployment path matures

### 9.4 Evaluate `uvloop` or similar only after measurement

Carried from `plans.md`.

Acceptance criteria:

- no event-loop swaps without concrete measured benefit

## Priority 10: Longer-Horizon Foundation Work From `plans.md`

These are not immediate wrapper blockers, but they are still open and should not
be forgotten.

- release pipeline formalization
- Dependabot or Renovate
- stronger package metadata / publishing hygiene
- formal methods / consistency modeling stretch work
- performance profiling before optimization
- future integrations such as GCP backend
- TypeScript/Ink TUI ideas if Nimbus evolves into a broader terminal product
- reading/research items that are explicitly tied to later implementation phases

## Reliability Concepts To Apply Intentionally

Do not treat these as buzzwords. Apply them only where they reduce real risk.

- Idempotency: retries of the same logical Slack event should not duplicate AI
  work or storage side effects.
- Replay protection: signed nonces and fresh timestamps should reject replays.
- Backpressure: refuse or queue overload explicitly instead of silently melting.
- Timeouts: wrapper -> Nimbus, Nimbus -> provider, provider tool paths.
- Retries with jitter: only on safe operations and bounded by policy.
- Circuit breaking: stop hammering a failing AI provider.
- Bulkheads: keep one failing subsystem from poisoning everything.
- Observability: request IDs, conversation IDs, outcome classes, tool-call logs,
  metrics.
- Ordering: one conversation/thread should be serialized.
- Durability: if state is persisted, it should survive crashes or fail loudly.

## Tooling Recommendation

### Use managed/public-edge tooling where it is commodity

- private networking when possible
- workload identity / OIDC where possible
- structured logs and OpenTelemetry
- CDN / WAF / ingress protection at the edge if the service is public

### Do not mistake edge tools for core reliability logic

- `ngrok` is useful for local development and webhook testing, not as a
  production reliability primitive
- `Anubis`-style protection may be useful for public web surfaces, but it does
  not replace application-level correctness or service-to-service auth

### Implement domain-specific reliability ourselves

- idempotency semantics
- confirmation state machines
- retry classification
- conversation ordering
- storage-tool safety policy

## Commands

```bash
# Full local gates
uv run pytest
uv run ruff check .
uv run mypy --strict .
uv run sphinx-build docs/source docs/build/html

# AI server tests only
uv run --package ai-server pytest src/ai_server/tests/

# Live ai_server e2e are opt-in
RUN_AI_SERVER_E2E=1 \
AI_SERVER_BASE_URL=https://ospsd-team-2.fly.dev \
AI_SERVER_SIGNING_SECRET=<wrapper-signing-secret> \
uv run pytest src/ai_server/tests/test_e2e.py -m e2e
```
