# Nimbus session state

Living checkpoint so a new Claude Code chat can pick up where the previous one left off. Not a mentor prompt (see `CLAUDE.md` for that) and not a teaching template (see `MENTOR.md`). Update when direction changes, not on every edit.

---

## Project at a glance

Nimbus is an LLM-powered cloud-storage assistant.

| Package | Role |
|---|---|
| `src/ai_client_api` | Provider-agnostic contract: `AIClient`, `Conversation`, `Tool`, `AIResponse`, exception hierarchy |
| `src/openrouter_ai_client_impl` | OpenRouter-backed `AIClient` + pydantic-ai agentic loop + `nimbus` CLI/REPL + cloud-storage tool bindings |
| `src/ai_server` | FastAPI HTTP wrapper around the AI client; session management; per-user rate limiting; Slack/channel-adapter target |
| `src/aws_client_impl` / `src/aws_client_adapter` | S3 implementations of `CloudStorageClient` |
| `src/aws_client_service` | FastAPI service wrapping the S3 client |
| `src/aws_s3_cloud_storage_service_client` | Auto-generated OpenAPI client for `aws_client_service` |

Two independent axes: *Cloud-Storage Vertical* (teams 2, 6, 10) exposes `CloudStorageClient`; *AI vertical* wraps it so an LLM can upload / list / download through the same contract.

---

## HW3 scope (branch: `hw-3`)

1. Migrated `OpenRouterClient` from hand-rolled loop to **pydantic-ai `Agent.run_sync()`**. External contract unchanged. Commit `fa0a732`.
2. Tool bindings in `cloud_storage_tools.py`: `upload_file`, `download_file`, `list_files`, `get_file_info`, `delete_file`. Pydantic-validated args; container pinned at bind time; paths constrained to `safe_root`; session-wide upload quota (FM8).
3. REPL (`cli.py`): Rich banner, tool-call events, slash commands, `credentials.env` auto-load, atomic session saves (FM5), P2 conversation rollback on error.
4. `ai_server`: FastAPI HTTP wrapper with `POST /chat/turn`, `GET /sessions/{id}/history`, `DELETE /sessions/{id}`, per-session `asyncio.Lock`, per-user token-bucket rate limiting (FM10), atomic session file writes.
5. All failure modes FM4–FM10 fixed or mitigated (see table below).

---

## HW3 assignment grounding

- AI chat completions are solved; the hard part is wiring AI into the architecture cleanly.
- Every team must integrate an external AI client + at least one other team's vertical through the shared API contract.
- Deployed and managed via IaC. Telemetry is mandatory: request latency, success rate, failure rate.
- Second submission: AI + cross-vertical integration + integration tests. Final: full demo + pipeline walkthrough + telemetry view.

---

## Current state

- Branch: `hw-3`.
- Marker-filtered local gates pass:
  - `uv run pytest -m "unit or regression" --no-cov` → **323 passed, 81 deselected**
  - `uv run pytest -m property --no-cov` → **38 passed, 366 deselected**
  - `uv run pytest tests/integration tests/e2e src/aws_client_adapter/tests/test_service_adapter.py --no-cov` → **40 passed**
  - `PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_*.py` → all three harnesses clean (209 / 513 / 511 inputs)
  - `uv run ruff check .` → clean
  - `uv run mypy --strict .` → clean
  - `uv run sphinx-build docs/source docs/build/html` → clean (exit 0)
- ⚠️ **Aggregate coverage gate is failing.** `uv run pytest src/` reports 404 tests collected
  and **28% total coverage** vs. the 80% threshold in `[tool.coverage.report]`. The
  earlier "84% coverage" figure pre-dated `nimbus_runtime`; that package landed
  largely untested and dragged the aggregate down. Details under the Sprint 5 plan
  below and in `## Remaining backlog → 0. Testing maturity`.
- Wrapper-facing AI-service contract implemented:
  - `POST /ai/chat/turn`
  - signed-request auth via `X-Nimbus-Timestamp`, `X-Nimbus-Nonce`, `X-Nimbus-Signature`
  - normalized conversation IDs derived from `platform/workspace/channel/thread-or-message`
  - best-effort idempotent replay keyed by `platform + workspace_id + idempotency_key`
  - wrapper docs live at `docs/source/nimbus-ai-service.md`
- `/ai/chat/turn` now binds real read-only storage tools when
  `NIMBUS_CONTAINER` or `AWS_BUCKET_NAME` is configured:
  - `list_files(prefix="")`
  - `get_file_info(remote_path)`
  - verified by `src/ai_server/tests/test_wrapper_contract.py`
  - `delete_file` remains intentionally disabled on the wrapper path until the
    public confirmation contract is explicit
- `/ai/chat/turn` accepts a stable wrapper-owned attachment metadata contract:
  - request model includes optional `attachments[]`
  - each attachment carries `platform_file_id`, `filename`, `content_type`, and
    `size_bytes`
  - the route validates count/size/content-type bounds and exposes attachment
    metadata to the AI turn as safe context
  - exact Slack file → Nimbus mapping lives in `docs/source/nimbus-ai-service.md`
- Wrapper conversation IDs no longer fail persistence when normalized chat IDs
  exceed 128 characters:
  - `sessions.py` maps long logical session IDs to deterministic hashed filename
    stems while preserving the full logical `session_id` in JSON
  - worst-case wrapper ID lengths are covered in
    `src/ai_server/tests/test_wrapper_contract.py`
  - session round-trip coverage lives in `src/ai_server/tests/test_sessions.py`
- Wrapper rate limiting keys on the real Model A principal (`platform:workspace_id:user_id`).
  - `_check_rate_limit` accepts a `_now` clock-injection parameter for testability
  - coverage lives in `src/ai_server/tests/test_wrapper_contract.py` and
    `src/ai_server/tests/test_router_properties.py`
- Wrapper replay/idempotency state persists under `AI_SESSION_DIR/_request_state`:
  - signed-request nonce state and idempotent turn responses survive service restarts
  - the current guarantee assumes one machine / one process, matching `fly.toml`
  - coverage lives in `src/ai_server/tests/test_request_state.py` and
    `src/ai_server/tests/test_wrapper_contract.py`

### Test infrastructure (Sprint 4)

- **38 Hypothesis property-based tests** across 4 files (`pytest -m property`):
  - `test_conversation_properties.py` — `_as_int` coercion, token estimate bounds, round-trip serialisation, stateful `ConversationMachine` (bounded-history + orphan-TOOL + round-trip invariants)
  - `test_session_properties.py` — validation oracle, file-stem determinism, short/long ID round-trip persistence
  - `test_auth_properties.py` — sign→verify self-consistency, body/nonce/timestamp sensitivity, secret isolation, payload determinism
  - `test_router_properties.py` — `ChatTurnRequest` validation, `_decoded_base64_size` formula, token-bucket arithmetic
- **3 Atheris fuzz harnesses** under `fuzz/` (smoke mode: `PYTHONFUZZ_NO_ATHERIS=1`):
  - `fuzz_conversation.py` — `Conversation.from_json` + `_message_from_dict` (209 inputs)
  - `fuzz_session_id.py` — `_validate_session_id` + `_session_file_stem`, path-separator escape check (513 inputs)
  - `fuzz_request_state.py` — `_read_live_value` via temp file writes (511 inputs)
- **All test files** carry `pytestmark` — no unmarked tests remain
- **CircleCI** has `property-tests` and `fuzz-smoke` jobs; `unit-tests` filtered to `-m "unit or regression"`
- **`docs/source/testing.md`** — comprehensive testing guide in Sphinx
- **Deterministic integration/e2e workflows** now cover the storage vertical without live AWS:
  - shared `tests/support/storage_fakes.py` file-backed `CloudStorageClient`
  - adapter ↔ service workflow invariants (upload/list/info/download/delete/repeated delete)
  - real auth-path coverage for service + adapter using API-key auth rather than dependency bypass
  - `main.py` subprocess e2e now uses a fake `aws_client_impl` on `PYTHONPATH`, so success/failure paths are reproducible in CI
- Default models: **`z-ai/glm-4.5-air:free`** (primary, Novita) + **`nousresearch/hermes-3-llama-3.1-405b:free`** (fallback, DeepInfra). Neither is Venice.
- `DEFAULT_MAX_STEPS = 8` in `config.py`.
- CLI entry point is `openrouter_ai_client_impl.cli:app` (Typer `app` object). `nimbus` command works.
- pydantic-ai fully adopted as the agent core (`Agent.run_sync()`). Test suite uses `FunctionModel` — no real HTTP in unit tests.

---

## What was done in the last sessions

### Session 8 — BDD acceptance layer (2026-04-29)

**User intent:** Introduce BDD thoroughly enough that wrapper-facing product
contracts are executable acceptance scenarios, not just prose in docs.

**What landed:**

- Added `pytest-bdd` to the dev dependency group.
- Added `tests/bdd/test_wrapper_acceptance.py` with deterministic FastAPI
  `TestClient` coverage against the real `ai_server` router, signed auth layer,
  `nimbus_runtime`, session/state persistence, and fake AI/storage clients.
- Added four Gherkin feature files under `tests/bdd/features/`:
  - `chat_turn.feature` — signed Slack-style requests return `reply` and expose
    read-only storage tools.
  - `wrapper_signed_auth.feature` — missing headers, tampered body, and replayed
    nonce fail closed with `401`.
  - `confirmation_flow.feature` — destructive delete intent returns
    `confirmation_required`; same actor can confirm; different actor cannot.
  - `attachment_ingestion.feature` — inline attachment byte upload can return
    `reply`, `partial_success`, or `error`.
- Added the `bdd` pytest marker and a `just bdd` shortcut.
- Added a CircleCI `bdd-tests` job and made `coverage-gate` require it.
- Added `docs/source/testing-bdd.md` and linked it from the testing docs.
- Updated `docs/source/testing.md`, `docs/source/testing-playbook.md`,
  `tests/README.md`, `plans.md`, and `NIMBUS_NEXT_TODOS.md` so BDD is no longer
  listed as open work.

**Verification:**

- `uv run pytest tests/bdd -q --no-cov` → **11 passed**
- CircleCI YAML parsed locally; `bdd-tests` is wired into the workflow and
  required by `coverage-gate`.
- `uv run pytest tests/bdd src/ai_server/tests/test_wrapper_contract.py src/nimbus_runtime/tests/test_runtime.py -q --no-cov` → **57 passed**
- `uv run pytest -m "unit or regression" --no-cov -q` → **371 passed, 88 deselected**
- `uv run pytest --no-cov -q` → **443 passed, 16 skipped**
- `uv run pytest src/ -q` → **424 passed, 16 skipped, 86% coverage**
- `uv run ruff check .` → clean
- `uv run mypy --strict .` → clean
- `uv run sphinx-build docs/source docs/build/html` → clean

### Session 7 — Pytest suite polish and contributor guide (2026-04-28)

**User intent:** Raise the overall pytest quality bar across the repo so the
tests read like a mature OSS codebase: better fixture hygiene, fewer duplicate
test surfaces, stronger auth-path coverage, and clear contributor guidance for
adding new tests.

**What landed:**

- Added `tests/README.md` as the contributor-facing testing guide with:
  - test philosophy and pytest conventions
  - guidance on choosing unit vs integration vs property vs e2e
  - fixture strategy and a test-writing checklist
  - a file-by-file inventory of the suite so contributors know where new tests belong
- Updated `docs/source/testing.md` to point readers at the new repo-local
  testing guide.
- Consolidated repeated FastAPI service endpoint test setup into
  `src/aws_client_service/aws_client_service/tests/conftest.py`.
- Removed the weaker duplicate `src/aws_client_service/aws_client_service/tests/test_auth.py`
  so `src/aws_client_service/tests/test_auth.py` is now the single stronger auth
  test source of truth.
- Strengthened `src/aws_client_service/tests/test_auth.py` with:
  - callback state-clearing checks
  - explicit `ValueError` → `400` mapping coverage
  - parametrized API-key / bearer acceptance and rejection cases
- Added stable repo-local test-support import plumbing in `conftest.py` so
  shared helper modules remain importable under full-suite pytest collection.

### Session 6 — Antithesis-style integration/e2e hardening (2026-04-28)

**User intent:** Apply Antithesis testing principles to the repo, fix bugs the
stronger tests uncover, and beef up the integration/e2e suite enough that the
storage vertical has deterministic workflow coverage rather than import/file
smoke tests.

**What landed:**

- Added shared deterministic storage fake support in
  `tests/support/storage_fakes.py` so integration/e2e tests can run full
  storage workflows without live AWS.
- Reworked `tests/integration/test_adapter_integration.py` around invariants:
  upload/list/info/download/delete/repeated-delete now execute through the real
  FastAPI app and generated client, with real API-key auth exercised end to end.
- Reworked `tests/e2e/test_main_application.py` so `main.py` runs as a real
  subprocess against a fake `aws_client_impl` injected via `PYTHONPATH`.
  Success, missing-env, and backend-failure paths are now deterministic.
- Reworked `tests/e2e/test_service_e2e.py` so the real FastAPI app is exercised
  over its HTTP contract with API-key auth, real request/response payloads, and
  workflow invariants instead of just OpenAPI/file-structure smoke checks.

**Bugs uncovered and fixed by the stronger tests:**

- `CloudStorageServiceAdapter.upload_file()` was catching downstream auth /
  transport failures and misreporting them as `LocalFileAccessError` because
  the `try` block wrapped the remote upload as well as the local open.
- `CloudStorageServiceAdapter` treated service `401` responses as generic
  backend failures instead of `AuthenticationError`.
- `CloudStorageServiceAdapter` treated all service `404` responses as object
  misses instead of preserving container-missing classification when the service
  detail says the container/bucket is missing.

**Why this matters in Antithesis terms:**

- The tests assert properties and invariants, not just single examples.
- The workloads are deterministic and reproducible.
- Failure modes are intentionally exercised across transport boundaries.
- The black-box `main.py` e2e no longer depends on ambient AWS credentials.

### Session 5 — Testing audit + tooling roadmap (Sprint 5 planning, 2026-04-28)

**User intent:** Get an SDE-T-grade audit of the testing suite, agree on a tooling
roadmap (BDD, nox/poe, locust, freezegun, faker, polyfactory, responses, mutation
testing, Schemathesis), and beef up structlog logging across the codebase. Stay in
mentor mode — discussion + planning, no implementation this session.

**No code was written in this session.** This entry exists so a future session can
resume from the agreed plan.

**Audit findings (must-fix before next implementation slice):**

| Surface | Coverage | What's missing |
|---|---|---|
| `nimbus_runtime/runtime.py` | 22% (312 lines) | Confirmation state machine, session-lock contention, telemetry event ordering, attachment ingestion paths |
| `openrouter_client.py` | 12% (273 lines) | Fallback chain, `_EmptyChoicesError`, step-budget exhaustion, event-listener resilience, `_sandbox_result` edges |
| `cli.py` | 20% (356 lines) | REPL `run()` loop (`Prompt.ask`/EOFError), `/debug` ring-buffer print, error rollback inside `_send_user_turn` |
| `aws_client_impl/s3_client.py` | 14% (248 lines) | All non-trivial multipart paths, error translation from botocore |
| `aws_client_adapter/service_adapter.py` | 17% (183 lines) | Every operation; this is the HTTP adapter the wrapper team relies on |
| `nimbus_runtime/state_store.py` | 26% (90 lines) | Confirmation/idempotency persistence, expiry semantics |
| `ai_server/sessions.py` | 23% (65 lines) | Long-ID hash path, `list_sessions` |
| `ai_server/wrapper_client.py` | 21% (56 lines) | Reference signer/normalizer used by wrapper team |

**Structural testing gaps identified:**

1. No concurrency tests for `asyncio.Lock` per-session serialization.
2. No crash-recovery tests (`.tmp` written, process dies before `os.replace()`).
3. No transport-failure → domain-error mapping tests at the boto3 / OpenRouter
   adapter boundary. SDK-internal exceptions can leak past abstraction today and
   no test asserts they don't.
4. No structured-log shape tests (no `request_id`/`operation` field assertions,
   no credential-shape leak guard).
5. No mutation testing — 38 property tests pass but mutmut hasn't been run, so
   we don't know how many surviving mutants there are.
6. No `Schemathesis` against `/ai/chat/turn` — OpenAPI response drift unguarded.
7. No multi-Python-version matrix — single 3.12 in CI; no tox/nox config.
8. No `.pre-commit-config.yaml`.
9. No canonical task runner (justfile / nox / poe). `uv run …` works but isn't
   discoverable for new contributors.

**Agreed tooling stack for Sprint 5 (decisions captured for the next session):**

- **Task runner:** add `nox` (Python-native, lets us script complex sessions
  like "build, run schemathesis against TestClient, tear down") and a thin
  `justfile` for the shell-level shortcuts. Reasoning: nox is more flexible than
  tox once you go past simple version matrices and the codebase already has
  Python-heavy CI logic; poethepoet is fine but nox has the larger ecosystem.
- **BDD:** `pytest-bdd` (not `behave`) for the wrapper-facing chat turn flows.
  Reason: stays inside pytest discovery/markers/fixtures, no separate runner.
  Initial scope = three feature files: `chat_turn.feature`,
  `confirmation_flow.feature`, `wrapper_signed_auth.feature`. ~12–15 scenarios
  total. Treat them as the authoritative product-level acceptance tests; keep
  unit + property tests for invariants.
- **Mutation testing:** `mutmut` (not `cosmic-ray`). Run on `auth.py`,
  `sessions.py`, `conversation.py`, `state_store.py` first. Goal is to catch
  surviving mutants that property tests should have killed.
- **HTTP contract drift:** `Schemathesis` against `TestClient(app)`. One CI
  job, runs on every push.
- **Load testing:** `locust` for `/ai/chat/turn` under concurrent signed
  requests. Local-only initially; nightly later.
- **Time control:** `freezegun` for the rate-limiter, idempotency TTL, and
  session-expiry tests. Keeps the existing `_now` clock-injection seam — does
  not replace it; complements it for places that don't have explicit injection.
- **Test data factories:** `polyfactory` (pydantic-aware, modern) for
  `ChatTurnRequest` / `MessageRecord` / attachment-metadata factories.
  `Faker` only as a sub-provider when polyfactory needs deterministic but
  varied strings (filenames, principal IDs).
- **HTTP mocking:** `responses` for `requests`-shaped tests; for the OpenRouter
  layer keep using pydantic-ai's `FunctionModel` — it's the right abstraction
  level. Add `responses` only where we actually use `requests` directly
  (currently nowhere; defer until needed).
- **Coverage:** keep `coverage.py` (pytest-cov is a wrapper); add `--cov-branch`
  is already on; raise property-test contribution by parallelizing into
  `coverage combine`.

**Logging stack for Sprint 5:**

- structlog is wired in `router.py` and `openrouter_client.py` but inconsistently
  used elsewhere. Sprint 5 logging goal: every request path emits at least
  `request_received`, `request_completed` / `request_failed` with `request_id`,
  `conversation_id`, `user_id`, `platform`, `latency_ms`, `outcome_class`. Tool
  calls emit `tool_started` / `tool_completed` with `tool_name`, `latency_ms`.
  No raw exceptions in log strings — use `structlog`'s `exc_info=True` so the
  log processor formats them.
- Add a structlog test helper (`structlog.testing.capture_logs`) and write a
  `test_log_shape.py` per package that asserts every emitted event has the
  required keys and no credential-shaped values (regex over values).

**What Sprint 5 explicitly does NOT include:**

- No Bazel migration, no Stripe-style selective test execution, no Toxiproxy
  yet, no Testcontainers / LocalStack yet, no formal methods. These stay in
  Priority 10 of `NIMBUS_NEXT_TODOS.md`.
- No tool replacements that aren't earning their keep: not migrating off
  pydantic, not adopting attrs across the board, not switching from json to
  protobuf/capnp, not adopting pendulum/arrow.

**External audit feedback received this session (preserved verbatim for traceability):**

Five testing weaknesses called out by the reviewer:

1. **Inconsistent fixture use** — repeated ad-hoc `_client_error()` and
   `_make_client()` helpers where shared fixtures would standardize setup. Block
   A should consolidate these into `src/aws_client_impl/tests/conftest.py`.
2. **Limited negative testing** — happy paths well-covered, S3 error matrix
   under-covered. Map directly to Block C (transport-failure → domain-error
   mapping) plus a parametrized error-class fixture.
3. **Hardcoded test data** — `"test-bucket"`, `"some-key.txt"` repeated everywhere.
   Map directly to Block A's polyfactory adoption: replace string constants with
   typed factories that vary key/bucket per example.
4. **Missing performance/load testing** — no benchmarks for critical paths. Map
   directly to Block H (locust port for `/ai/chat/turn`) + add `pytest-benchmark`
   for inner-loop perf assertions on conversation trim, session serialization,
   and `_decoded_base64_size`.
5. **Limited chaos/failure testing** — no network-fault simulation, partial-
   failure paths absent. Map to Block B (concurrency + crash recovery) plus a
   new **Block J — Chaos** described in `## Remaining backlog → 0. Testing
   maturity → Block J` below. Toxiproxy is the path to take here in V2.

What the audit said the codebase does well (so we don't regress):

- Property-based testing for validation logic (`test_*_properties.py` — 38 tests)
- Strong autospec mocking practice
- Clear unit / integration / property / e2e separation via markers
- Dependency-injection verification (`get_client_impl()` factory pattern)

Audit recommendations cross-referenced into Sprint 5 blocks:

| Audit recommendation | Sprint 5 block |
|---|---|
| Add chaos / resilience testing | Block J (new) + Block B |
| Standardize test setup with fixtures | Block A (conftest consolidation) |
| Add performance benchmarks | Block H (locust) + new `pytest-benchmark` micro-bench in Block A |
| Expand error condition testing in integration layers | Block C |
| Add contract tests between service boundaries | Block G (Schemathesis) extends existing wrapper-contract tests |

**Correctness through contracts and invariants — design lens for Sprint 5:**

Every block must be motivated by a contract or an invariant, not by coverage
percentage. Coverage is a side-effect of testing the right thing.

- **Contracts** to test: `AIClient` ABC, `CloudStorageClient` ABC, the wrapper
  HTTP contract (`/ai/chat/turn` request/response schema), the runtime's
  `ChatTurnInput` → `ChatTurnResult` mapping, the auth signing protocol.
- **Invariants** to property-test: bounded conversation history, atomic session
  writes (no half-written file ever loaded), idempotent retries (same
  `idempotency_key` + same body within TTL → same response), per-session
  serialization (no two turns interleave on the same `session_id`), HMAC
  determinism (same `(secret, body, ts, nonce)` → same signature),
  rate-limiter monotonicity.
- **Liveness** properties: every accepted turn eventually completes or fails
  with a typed domain exception within the configured timeout budget.
- **Safety** properties: destructive tools (`delete_file`) cannot execute
  without an explicit confirmation match; tool output is always sanitized
  before re-entering the model context.

These are the things property tests + BDD scenarios should *encode*. If a test
doesn't trace back to one of those, it's likely either too narrow (mocking what
it should integrate) or too broad (asserting incidental implementation detail).

**Resume here next time:**

1. Read `## Remaining backlog → 0. Testing maturity` below.
2. Start with Sprint 5 Block A (coverage recovery for `nimbus_runtime`) — the
   single highest-value chunk of work.
3. Each block ends with a focused commit; do not bundle blocks.
4. Tooling additions agreed this session (already in `plans.md` Sprint 5
   subsection): nox, pytest-bdd, locust, freezegun, polyfactory, mutmut,
   schemathesis, pytest-benchmark, pytest-asyncio, pytest-randomly. Stamina
   (Tenacity wrapper) replaces ad-hoc retries when Block C lands.

### Session 4 — Test infrastructure (Sprint 4, 2026-04-26)

**User intent:** Complete the full Hypothesis + Atheris testing plan, update CircleCI, update docs, make everything pass locally.

| Area | Change |
|---|---|
| **Hypothesis — `test_conversation_properties.py`** | 12 tests: `_as_int` coercion (4), token estimate (2), fresh-conv invariants (2), round-trip serialisation (3), `ConversationMachine` stateful machine (1). Uses `RuleBasedStateMachine` with `run_state_machine_as_test`. |
| **Hypothesis — `test_session_properties.py`** | 10 tests: validation oracle (3), file-stem determinism + routing (4), round-trip persistence (3). Uses `tempfile.TemporaryDirectory()` inside the test body — not `tmp_path` — to avoid cross-example contamination. |
| **Hypothesis — `test_auth_properties.py`** | 6 tests: sign→verify self-consistency, body/nonce/timestamp sensitivity (3 tests with `assume`), secret isolation (`max_examples=200`), payload determinism. |
| **Hypothesis — `test_router_properties.py`** | 10 tests: `ChatTurnRequest` valid/invalid field acceptance, large-text rejection (large-base-example health-check safe strategy), `_decoded_base64_size` formula (3 tests), rate-limiter arithmetic (4 tests). |
| **`_check_rate_limit` testability** | Added `_now: float \| None = None` clock-injection parameter. Defaults to `time.monotonic()` in production; property tests inject deterministic float values. |
| **Atheris fuzz harnesses** | `fuzz/fuzz_conversation.py` (209 inputs), `fuzz/fuzz_session_id.py` (513 inputs), `fuzz/fuzz_request_state.py` (511 inputs). All run in smoke mode without Atheris. `fuzz/README.md` documents setup, smoke mode, corpus usage, crash reproduction. |
| **`pytestmark` gap** | All 6 test files that lacked `pytestmark` now have `pytest.mark.unit`. Two files (`aws_client_service/tests/test_auth.py` ×2) had `import pytest` inside `if TYPE_CHECKING` — promoted to runtime import before the fix could work. |
| **CircleCI** | Added `property-tests` job (branch-coverage-contributing, wired into `coverage-gate` requires). Added `fuzz-smoke` job (three sequential run steps). `unit-tests` job now filters to `-m "unit or regression"`. |
| **`docs/source/testing.md`** | Comprehensive guide: five test categories table, running commands, branch coverage, every test file explained, every fuzz harness explained, "Writing Your Own Tests" with templates. Added to Sphinx toctree. |
| **CONTRIBUTING.md** | Both root and docs versions: updated Running Tests section (five layers, fuzz smoke commands, marker table, CI job table). |
| **`pyproject.toml`** | Added `"fuzz"` to `ruff.extend-exclude` — fuzz harnesses legitimately use `print`, `random`, and inline exception strings that ruff would otherwise flag. |
| **Idempotency TTL test** | Added `test_expired_cached_response_causes_fresh_ai_call` to `TestChatTurnIdempotency` — expires both the in-memory dict entry and the file-backed state, then verifies a fresh AI call is made. |
| **Ruff fixes** | `PLR0913` noqa on all Hypothesis test functions with > 5 parameters; `D205` docstring formatting; `FBT001` noqa on boolean positional arg; `F841` unused `before` removed; `D103` missing docstring added; `S108` noqa on `/tmp` in fuzz machine rule; `SLF001` noqa on private member access in invariants. |

**Final state:**
- `pytest -m "unit or regression"` → 323 passed
- `pytest -m property` → 38 passed
- All three fuzz harnesses → clean smoke runs
- `ruff check .` → clean
- `sphinx-build` → exit 0

**What was deliberately skipped:**
- No new product features — this sprint was test infrastructure only.

---

### Session 1 (pre-summary)
- Ran all CircleCI commands locally (ruff, mypy --strict, pytest) and made them pass.
- Updated CircleCI branch filter from `hw-2` to `hw-3`.
- Reviewed AGENTS.md.
- Built `ai_server` from scratch: `router.py`, `sessions.py`, `auth.py`, FastAPI app, Dockerfile, Fly.io config.
- Added `Conversation.pop_last_user()` to `ai_client_api` for optimistic-mutation rollback.
- Fixed `AIClient` ABC docstring to accurately describe `AIToolExecutionError`/`AIUnknownToolError` contract.
- Fixed `_build_model` (P3): attribution headers now threaded via `openai.AsyncOpenAI(default_headers=...)` → `OpenAIProvider(openai_client=...)`.
- Fixed FM4 in `_run_with_fallback`: explicit `status == 429` check for `ModelHTTPError`.

### Session 3 — CLI rewrite (Rich REPL + Typer) + pydantic-ai adoption + smoke-test (latest)

| Area | Change |
|---|---|
| **Argparse → Typer** | Replaced `argparse` in `cli.py` with `typer.Typer()` + `@app.command()`. Entry point changed from `cli:main` to `cli:app`. Tests updated to use `typer.testing.CliRunner`. `typer[all]>=0.12.0` in `pyproject.toml`. |
| **Rich REPL** | `NimbusCLI` class built: `run()` loop, `_send_user_turn`, `_handle_slash`, `_on_event`. Rich banner with model name + tool count. Tool-call event lines (✔/✗). `Prompt.ask()` for input. |
| **Slash commands** | Full dispatch table: `/help`, `/clear`, `/history`, `/model [name]`, `/debug [on\|off]`, `/session [id]`, `/quit`, `/dry-run [on\|off]`, `/cost`. |
| **`/debug` ring buffer** | `OpenRouterClient._last_raw_completions` — 5-item `deque` of raw model response summaries (model, finish_reason, tool_calls count). `/debug` prints them; `/debug on` auto-prints after each turn. |
| **Fresh session by default** | When `--session` is omitted, CLI generates `session-<uuid8>` so each invocation starts fresh without polluting the previous conversation. |
| **pydantic-ai adoption** | `openrouter_client.py` fully rewritten to use `pydantic_ai.Agent.run_sync()`, `OpenAIModel`/`OpenAIProvider`. Constructor gains `pai_model` / `pai_fallback_model` injection points for testing. |
| **FunctionModel test harness** | `test_openrouter_client.py` rewritten: `_text_model`, `_scripted_model`, `_error_model`, `_tool_call_response` factories using `FunctionModel`. 29 unit tests, no real HTTP. |
| **Empty-choices crash fix** | OpenRouter sometimes returns HTTP 200 with `choices=None`. Fixed via `_EmptyChoicesError` sentinel that routes through the normal fallback path. Live smoke test on `meta-llama/llama-3.3-70b-instruct:free` verified. |
| **System prompt rewrite** | New action-oriented system prompt: direct imperative tone, specific tool-call sequence instructions, no markdown in output, concise responses. Verified `upload_file` is called on first tool step. |
| **Default model switch** | Primary switched to a model that reliably emits tool calls. Verified via `scripts/smoke_tool_call.py` with `dry_run=True`. |
| **Smoke test script** | `scripts/smoke_tool_call.py`: builds `_NoopStorage`, calls `send_message(..., dry_run=True)`, exits 0 if `upload_file` called, 1 if no tool call, 2 if config error. |
| **Config quote fix** | `config.py` Q003 ruff violation: `"• Text inside <tool_result source=\"untrusted\">"` → `'• Text inside <tool_result source="untrusted">'`. |
| **`cli.py` test coverage** | `test_cli.py` added with 17+ tests covering: all slash commands, session round-trip, event rendering, `send_user_turn` happy path, error rollback, atomic saves, Typer entry-point tests. CLI went from 0% to ~69% coverage. |
| **Tutorial update** | `docs/source/ai-client-tutorial.md` updated: new defaults, `/debug` command entry, fresh-session behavior, tool glyph descriptions. |
| **Ruff/mypy fixes (session 3)** | `Callable` moved from `typing` to `collections.abc` (UP035). `_SlashHandler` type alias for slash dispatch dict. `# noqa: FBT002` on Typer bool flag. `raise typer.Exit(...) from err` (B904). `import sys` removed (unused). |
| **Atomic saves + rollback (external)** | `_save_conversation` upgraded to write `.tmp` then `os.replace()`. `_send_user_turn` calls `conv.pop_last_user()` on error. These were implemented externally alongside the pydantic-ai rewrite. |
| **`RECOMMENDED_FREE_MODELS`** | Exported from `config.py`, imported by `cli.py` for model suggestions in `/model` output. |
| **`NIMBUS_HW3_SYSTEM_DESIGN.md`** | System design document created (untracked, added to commit). |

### Session 2 (commit `da0f098`)
| Area | Change |
|---|---|
| **FM4 `_try_fallback`** | Fallback handler also now checks `status == 429` on `ModelHTTPError` before raising `AIProviderError` |
| **FM5 atomic save** | `cli.py _save_conversation`: write to `.tmp`, `os.replace()` — atomic on POSIX |
| **FM7 prompt injection** | `_sandbox_result` strips C0 control chars (via `_CONTROL_CHARS_RE`) before truncation + wrapping |
| **FM8 session upload quota** | `build_cloud_storage_tools` gains `session_max_upload_bytes`; list-wrapped counter enforced before network I/O |
| **FM10 per-user rate limiting** | `_TokenBucket` dataclass + `_check_rate_limit(user_id)` in `router.py`; configurable via `AI_RATE_LIMIT_CAPACITY`/`AI_RATE_LIMIT_RPM` |
| **P2 conversation rollback** | `_send_user_turn` calls `pop_last_user()` on `AIClientError` — failed messages not re-sent |
| **P3 attribution headers** | `_build_model` passes `default_headers={}` (empty is fine, not conditional unpack) |
| **mypy fix** | `default_headers` passed directly to `AsyncOpenAI()` — no `**dict` conditional unpack |
| **`ai_server` endpoints** | `GET /sessions/{id}/history`, `DELETE /sessions/{id}` added to router |
| **`sessions.py`** | Added `delete_session`, `list_sessions`; `save_session` uses write-tmp-then-rename |
| **Live integration tests** | Moved to `e2e` marker with shape-only assertions; removed `integration`/`local_credentials` markers |
| **Tests added** | FM4 (3 variants), FM7 control-char, P2 rollback, FM5 atomic save, history endpoint, delete endpoint (idempotency), FM10 token bucket |
| **READMEs** | Production-grade `ai_client_api/README.md` and `openrouter_ai_client_impl/README.md` |
| **AGENTS.md** | Added `ai_server` summary, Fly volume/session setup, mypy exclude rationale, env vars for `ai_server` and `nimbus` |
| **CI** | `uv sync --frozen` in `install-dependencies` command |

---

## Failure-mode status

| # | Failure mode | Status | Where |
|---|---|---|---|
| 1 | `Conversation` not cleaned up on provider crash | ✅ Fixed | `Conversation.pop_last_user()` in `ai_client_api`; wired in CLI (P2) |
| 2 | Tool schema mismatch between Pydantic model and JSON schema | ✅ Fixed (earlier) | `UploadFileArgs.model_json_schema()` auto-generated |
| 3 | `list_files` response looping (model calls `get_file_info` in a loop) | ✅ Fixed (earlier) | System prompt anti-loop line |
| 4 | pydantic-ai 429 as `ModelHTTPError` bypasses `AIRateLimitError` | ✅ Fixed | `openrouter_client.py _run_with_fallback` and `_try_fallback` |
| 5 | Session file race / half-written on crash | ✅ Fixed | `cli.py _save_conversation` and `ai_server/sessions.py save_session` |
| 6 | Conversation context unbounded growth | ⚠️ Partial | `max_messages`/`max_total_tokens` trim oldest turns. Full rolling summary is V2. |
| 7 | Prompt injection via tool results | ✅ Fixed | `_sandbox_result` strips C0 control chars |
| 8 | `max_upload_bytes` per-call not per-session | ✅ Fixed | `session_max_upload_bytes` counter in `cloud_storage_tools.py` |
| 9 | Listener exceptions log to stderr | ✅ Fixed | `emit()` catches and routes through `structlog` |
| 10 | No per-user rate limiting | ✅ Fixed | Token bucket in `ai_server/router.py` |

---

## Remaining backlog (not yet done)

These were discussed but **not implemented**. The next session should start at item 0.

### 0. Testing maturity (BLOCKING — coverage 28% vs. 80% threshold)

Outdated note from earlier: aggregate coverage is 28%, not 61%. The 61% number
was before `nimbus_runtime` landed; the new package added ~500 lines mostly
untested. Re-run the gap analysis with:

```bash
uv run pytest src/ -q --cov --cov-report=term-missing
```

Sprint 5 work breaks into seven blocks. Each block ends with a focused commit.

#### Block A — Coverage recovery (highest value, do first)

Target: aggregate ≥ 80%, per-package no lower than 70%.

- `nimbus_runtime/runtime.py` (22% → ≥ 80%): build a `FakeAIClient` +
  `FakeStorageClient` harness similar to `test_openrouter_client.py`'s
  `FunctionModel` factories. New file `src/nimbus_runtime/tests/test_runtime.py`
  is already there but only has 6 tests. Add: confirmation create→match→execute,
  confirmation expiry, session-lock contention (use `pytest-asyncio` and
  `asyncio.gather()`), attachment ingestion, telemetry event-ordering
  invariants, error-class translation.
- `nimbus_runtime/state_store.py` (26% → ≥ 80%): write a property test for
  round-trip + a unit test for atomic-write crash simulation (write `.tmp`,
  delete it before rename, reload — old data must be intact).
- `openrouter_client.py` (12% → ≥ 70%): new tests for `_run_with_fallback`
  exception classification, `_EmptyChoicesError` path, `_sandbox_result`
  truncation edges, listener-exception isolation.
- `cli.py` (20% → ≥ 70%): extract the REPL `run()` inner loop body into
  `_process_input(text: str)`, then test that directly. Don't try to test
  `Prompt.ask` itself.
- `aws_client_impl/s3_client.py` (14% → ≥ 70%): use moto for unit tests
  covering multipart upload happy + error paths; will revisit with LocalStack
  later.
- `aws_client_adapter/service_adapter.py` (17% → ≥ 70%): use `responses`
  (or the FastAPI `TestClient` against a stub generated-client) to test every
  operation through the adapter contract.

#### Block B — Concurrency + crash recovery

- Add `pytest-asyncio` to deps.
- `test_session_concurrency.py` (`ai_server`): three coroutines hit the same
  `session_id` via `asyncio.gather`; assert final state is one complete turn
  not interleaved.
- `test_session_crash_recovery.py`: write `.tmp`, abort before rename, reload —
  old session intact, no half-written file accepted.
- `test_token_bucket_concurrency.py`: burst load asserts exact rejection count
  (uses `_now` injection — already exists).

#### Block C — Transport-failure → domain-error mapping

This is the abstraction-leak guard.

- `test_s3_error_translation.py`: every `botocore.exceptions.ClientError` we
  care about (NoSuchBucket, AccessDenied, InvalidAccessKeyId, RequestTimeout,
  500/503 from S3) maps to the documented domain exception. Use moto to
  trigger; assert on `isinstance(raised, DomainException)` and
  `not isinstance(raised, botocore.exceptions.ClientError)`.
- `test_openrouter_error_translation.py`: `openai.APITimeoutError`,
  `openai.APIConnectionError`, `openai.RateLimitError`, `ModelHTTPError(429)`,
  `ModelHTTPError(5xx)` each map to the documented `AI*Error`. Use
  `pydantic_ai.models.function.FunctionModel` to script the failure.

#### Block D — Structured log shape + secret leak guard

- New `tests/test_log_shape.py` per package using
  `structlog.testing.capture_logs`.
- For each request path: assert every event has `request_id`, `conversation_id`
  (where applicable), `event` name from a known whitelist, `latency_ms`
  on completion events.
- Assert no log value matches `r'(secret|token|key|signature)\s*[=:]\s*\S+'`
  using a generic walker over the captured event dicts.
- This is a regression net for the entire codebase, not a single feature.

#### Block E — BDD scenarios (pytest-bdd)

- `features/chat_turn.feature`: signed wrapper request → reply outcome.
- `features/confirmation_flow.feature`: destructive intent → confirmation_required
  outcome → matching confirmation → completion.
- `features/wrapper_signed_auth.feature`: missing/invalid/replayed/tampered
  signature paths.
- Step definitions in `tests/bdd/`. Reuse existing fixtures.
- Treat scenarios as the authoritative product-acceptance level. Unit + property
  tests stay where they are.

#### Block F — Mutation testing

- Run `mutmut run --paths-to-mutate src/ai_server/ai_server/auth.py` first —
  smallest, security-critical, has both unit + property tests.
- Then `sessions.py`, `conversation.py`, `state_store.py`.
- Goal: zero surviving mutants in those four files. Don't try the whole repo.
- Add `nox -s mutate` session that runs mutmut on the curated file set.

#### Block G — OpenAPI contract drift (Schemathesis)

- One CI job `schemathesis-tests`: spin up `TestClient(app)`, run
  `schemathesis run --endpoint='/ai/.*' --hypothesis-suppress-health-check=...`.
- Catch: response shape drift, schema-rejected requests still 500ing, etc.

#### Block H — Tool automation + pre-commit

- `noxfile.py` with sessions: `lint`, `test`, `property`, `mutate`, `docs`,
  `schemathesis`, `load`, `bdd`.
- `.pre-commit-config.yaml`: ruff-check --fix, ruff-format, trailing-whitespace,
  end-of-file-fixer, check-yaml, check-toml, debug-statements, mypy
  (`pass_filenames: false`).
- Thin `justfile` for shell shortcuts: `just test`, `just lint`, `just docs`,
  `just nimbus`, `just smoke-wrapper`. Both nox and just; they don't compete.

#### Block I — Logging beef-up

- Audit every package for inconsistent logging. Where `print` or bare `logger.x`
  is used, switch to structlog with bound context.
- `nimbus_runtime/runtime.py` should emit the full event taxonomy from
  `NIMBUS_HW3_SYSTEM_DESIGN.md` § "Observability seams".
- `cloud_storage_tools.py` should emit `tool_started` / `tool_completed`.
- `cli.py` REPL events should structlog through the same logger so a developer
  running locally sees consistent output if `LOG_FORMAT=json` is set.
- Bind context once at request entry (`request_id`, `conversation_id`,
  `user_id`, `platform`) using `structlog.contextvars.bind_contextvars` so
  every nested log line inherits it without manual passing.

#### Block J — Chaos / fault injection (closes the audit's "limited chaos" gap)

This is a deterministic harness, not GameDay-style production chaos.

- New `tests/chaos/` directory, marker `@pytest.mark.chaos`, opt-in CI job.
- `Toxiproxy` proxy in front of OpenRouter and AWS calls during integration
  tests. Inject: latency spike, bandwidth limit, connection reset, partial
  read. Assert retry/timeout policy fires correctly — not just that the error
  is caught.
- For S3: parametrized fault matrix using moto's error injection — 500/503
  mid-multipart, partial PUT, clock skew. Assert resumable upload path on
  resume produces correct final object OR clean abort, never silent corruption.
- For OpenRouter: parametrized failure matrix via `FunctionModel` exceptions —
  timeout, partial-content, invalid-json, rate-limit. Each maps to a typed
  domain error and the conversation rolls back via `pop_last_user()`.
- Property test: for any sequence of `(upload_succeed | upload_fail)` events,
  the final stored object set is consistent with the success-event subset.

#### Block K — Test-data factories + parametrization (closes the audit's "hardcoded test data" gap)

- Add `polyfactory` factories under `tests/factories.py` per package:
  - `ChatTurnRequestFactory`, `MessageRecordFactory`, `TurnAttachmentFactory`,
    `ObjectInfoFactory`, `S3ClientErrorFactory`.
- Migrate `"test-bucket"` / `"some-key.txt"` / hard-coded principal IDs to
  factory output. Hypothesis tests already do this via strategies — this is
  for the unit-test surface that doesn't use Hypothesis.
- Faker as a sub-provider for filename/principal-id realism only where
  determinism doesn't matter; never in property tests.

#### Block L — Fixture consolidation (closes the audit's "inconsistent fixtures" gap)

- `src/aws_client_impl/tests/conftest.py` exports: `s3_client_factory`,
  `client_error_factory` (replacing the repeated `_client_error()` /
  `_make_client()` helpers across files).
- `src/ai_server/tests/conftest.py` exports: `signed_request_factory`,
  `wrapper_app`, `auth_headers`.
- `src/nimbus_runtime/tests/conftest.py` exports: `fake_ai_client`,
  `fake_storage`, `runtime_under_test`.
- Rule (Stargirl-style): mock object names match the real collaborator —
  call the fixture `signer`, not `mock_signer`. Lint with a one-line ruff
  custom rule or grep in pre-commit.

### 1. Auth walkthrough + Slack adapter design
- Design doc for the end-to-end auth flow: Slack → `ai_server` → OpenRouter → S3.
- Define thin `slack_adapter` package (event handler, slash-command dispatch, message formatting).
- `build_slack_tools(storage=...)` scaffold exists in `ai_server/slack_tools.py` — wire it up.
- Session ID = Slack channel ID or thread timestamp.
- Auth: signed requests for wrapper -> Nimbus on `/ai/chat/turn`; `X-API-Key` remains only for session-management endpoints; OAuth for user-facing Slack commands (decide whether to implement or stub).

### 2. Add AI e2e job to CircleCI
- New `ai-e2e-tests` job that:
- Uses `openrouter` context (injects `AI_SERVER_SIGNING_SECRET`).
  - Runs `uv run pytest src/ai_server/tests/test_e2e.py -m e2e`.
- Run it only after `verify-fly-deploy` passes, so the live checks hit a deployment that has already proved it exposes `/ai/chat/turn` and no longer exposes the removed `/ai/chat` route.
- Derive the Fly target directly from `fly.toml` and roll back automatically if the post-deploy checks still fail after retries.
- `src/ai_server/tests/test_e2e.py` now uses `e2e_base_url` / `e2e_signing_secret` for the signed wrapper route.

### 3. UX/UI polish
- `cli.py`: consider `prompt_toolkit` for keybindings (Ctrl-C = cancel in-flight request, not exit; Ctrl-L = clear screen; up-arrow = history).
- Better diagnostics: `/ping`, `/status` commands that show model reachability, session size, token budget remaining.
- Potentially a `src/ui/` module if `prompt_toolkit` integration grows beyond cli.py.
- `pyproject.toml`: add `prompt_toolkit` as optional dep if pursued.

### 4. Step budget / concurrency math (document + enforce)
- **Current:** `DEFAULT_MAX_STEPS = 8`. **Math:** at Venice 8 RPM shared cap, 10 users × 8 steps = 80 concurrent calls — will collapse. For non-Venice (Novita/DeepInfra), this is fine. Document in `config.py` comment why 8 was chosen and when to lower it.
- Cloud-storage tasks are almost always ≤ 4 steps (1 tool + 1 summary = 2; list → act → summarize = 3; multi-file = 4). 8 is the ceiling for complex chaining.
- If Venice is ever re-introduced as an upstream, lower to `MAX_STEPS = 4` and document why.

### 5. Remove or demote `scripts/benchmark_models.py`
- The benchmark script is useful for model selection but creates confusion: it exhausts free-tier quota if run twice, and the results JSON is not committed.
- Either move it to `scripts/dev/` with a prominent warning, add a `--dry-run` flag, or just document it as "run manually once per model selection cycle".
- `benchmark_results.json` is already `.gitignore`d (untracked).

### 6. Telemetry (mandatory for HW3 final)
- Prometheus / structlog metrics: request latency, success rate, failure rate per model.
- The `request_started`/`request_completed` events already carry `latency_ms` — pipe to Prometheus counter/histogram.
- Fly.io metrics endpoint or Grafana Cloud for the dashboard view.
- Structlog already wired in `router.py` and `openrouter_client.py`; add `prometheus_client` or equivalent.

### 7. Deployment / IaC
- `fly.toml` has `[[mounts]]` scaffolded; create the volume: `flyctl volumes create nimbus_sessions --region iad --size 1`.
- Set secrets: `flyctl secrets set AI_SESSION_DIR=/data/sessions AI_SERVER_SIGNING_SECRET=<key>` and keep `AI_SERVER_API_KEY` only if you still need session-management endpoints over API key.
- Verify `min_machines_running = 1` so the volume is always mounted.
- Smoke test the deployed endpoint: `curl https://ospsd-team-2.fly.dev/ai/health`.

### 8. FM6: rolling conversation summary
- When `len(conv.messages()) > max_messages`, trigger a cheap model call to summarize the oldest N turns into a single "conversation summary" system message.
- Prerequisite: a cheap/fast model that can summarize reliably (not the same model as the primary task model).
- Complexity: needs to be idempotent, not lose tool-call structure, and not trigger on every request.
- Deferred to V2.

---

## Step-budget rationale

For 10 users, worst case = 10 × N concurrent LLM calls. At Venice's **8 RPM shared cap**, `MAX_STEPS = 5` already causes 10 users to collide. Cloud-storage tasks are almost always ≤ 4 steps:
- 1 tool call + 1 summary = 2 steps
- list → act → summarize = 3 steps
- multi-file operation = 4 steps

`DEFAULT_MAX_STEPS = 8` is the right ceiling: enough for complex chaining, prevents runaway at 10× load, and only matters under Venice which is not the default upstream.

---

## Free-tier reality check

- OpenRouter's `free-models-per-day` cap is **global across all `:free` models** on a single account. Two benchmark runs can exhaust it for the day. $10 credits unlocks 1 000 req/day.
- Venice upstream (free backend for `meta-llama/llama-3.3-*`, `qwen/*`) has an **8 RPM shared cap** — collapses under multi-user load. Default models route to Novita / DeepInfra.
- DeepSeek has **no free tier** on OpenRouter as of this writing.
- `credentials.env` in the repo root has `OPENROUTER_MODEL` and `OPENROUTER_FALLBACK_MODEL` — if set, they override `config.py` defaults. Check there first if the banner shows the wrong model.

---

## Design stance

- Failures are the default case, not edge cases. Design timeouts, retries, idempotency, backpressure, and observability intentionally.
- Retries, idempotency, backpressure, and overload handling are part of the system, not polish.
- Use modern tooling when it earns its keep; do not add fashionable machinery without a concrete need.
- Optimize for low cognitive load, deep modules, shallow interfaces, and long-term changeability.
- Observable behavior is API surface: env vars, session files, CLI output, response schemas, error text, ordering, and defaults all create compatibility obligations.
- Channel adapters (Slack, CLI) stay thin. Shared runtime/tool/integration logic lives behind reusable boundaries — not duplicated in each adapter.
- MCP is the likely direction for future capability exposure, but only after host/client/server roles, auth, transport, and failure handling are made explicit.

---

## Resume here next session

1. Read `AGENTS.md` and this file first.
2. Read `NIMBUS_HW3_SYSTEM_DESIGN.md` for the system design and `NIMBUS_NEXT_TODOS.md` for the reviewed backlog.
3. The top Priority 0 directive in `NIMBUS_NEXT_TODOS.md:0.0`: finish the wrapper contract completely and the full Slack/Nimbus functionality. Test infrastructure (Sprint 4) is complete — do not revisit it unless a new test category is needed.
4. Full local checks pass: run `uv run pytest -m "unit or regression" --no-cov -q` and `uv run pytest -m property --no-cov -q` to verify before touching anything.
5. Do not touch `router.py`, `auth.py`, or `sessions.py` without re-reading them first — they carry the wrapper-facing contract and are easy to accidentally regress.
6. `_check_rate_limit` now accepts `_now: float | None = None`. Do not remove this — property tests depend on it for clock injection.
7. Before adding any new package dep, check `pyproject.toml` to confirm it is not already present.
8. The CLI entry point is `cli:app` (Typer), not `cli:main`. If you see `ImportError: cannot import name 'main'` in tests, check that `test_cli.py` imports `app` not `main`.
9. All test files must have `pytestmark = pytest.mark.{unit|property|e2e|...}` — every new test file needs this from day one.

---

## Workflow conventions

- No git worktrees — work directly on `hw-3`.
- Any git commands EXCEPT `push`.
- Squash related work into one commit; no string of micro-commits.
- No new `.md` files unless asked.
- Keep responses concise; no trailing summaries.
- Default models must be non-Venice.

---

## Useful commands

```bash
# Run the REPL (auto-loads credentials.env):
uv run --package openrouter-ai-client-impl nimbus

# Full test suite:
uv run pytest src/ -q

# AI server tests only:
uv run --package ai-server pytest src/ai_server/tests/ -q

# OpenRouter package tests only:
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q

# E2e tests (need OPENROUTER_API_KEY set):
uv run pytest -m e2e src/openrouter_ai_client_impl/tests/ -v

# Full CI pipeline locally:
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict .
uv run pytest src/ -q

# List free OpenRouter models that support tool calls:
curl -s "https://openrouter.ai/api/v1/models?supported_parameters=tools" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  | jq -r '.data[] | select(.id|endswith(":free")) | .id'

# Fly.io health check:
curl https://ospsd-team-2.fly.dev/ai/health
```

---

## Environment variables (complete reference)

| Variable | Package | Required | Default | Notes |
|---|---|---|---|---|
| `OPENROUTER_API_KEY` | openrouter | **Yes** | — | |
| `OPENROUTER_MODEL` | openrouter | No | `z-ai/glm-4.5-air:free` | Overrides `config.py` default |
| `OPENROUTER_FALLBACK_MODEL` | openrouter | No | `nousresearch/hermes-3-llama-3.1-405b:free` | |
| `OPENROUTER_BASE_URL` | openrouter | No | `https://openrouter.ai/api/v1` | |
| `OPENROUTER_TIMEOUT` | openrouter | No | `120.0` | Seconds |
| `OPENROUTER_MAX_STEPS` | openrouter | No | `8` | Agentic loop step budget |
| `OPENROUTER_APP_REFERER` | openrouter | No | — | `HTTP-Referer` for OpenRouter attribution |
| `OPENROUTER_APP_TITLE` | openrouter | No | — | `X-Title` for OpenRouter attribution |
| `AI_SERVER_API_KEY` | ai_server | No | — | Shared secret for session-history/session-delete `X-API-Key` auth |
| `AI_SERVER_SIGNING_SECRET` | ai_server | **Yes** | — | Shared secret for signed `POST /ai/chat/turn` requests |
| `AI_SESSION_DIR` | ai_server | No | `~/.nimbus/sessions/ai_server` | Set to `/data/sessions` on Fly.io |
| `AI_RATE_LIMIT_CAPACITY` | ai_server | No | `10` | Per-user token bucket max tokens |
| `AI_RATE_LIMIT_RPM` | ai_server | No | `10` | Refill rate in requests/minute |
| `AI_SERVER_BASE_URL` | test/e2e | No | — | Required for live e2e tests |
| `NIMBUS_CONTAINER` | cli | No | `$AWS_BUCKET_NAME` | S3 bucket for LLM tools |
| `NIMBUS_SAFE_ROOT` | cli | No | `$PWD` | Local directory the LLM may read/write |
| `NIMBUS_SESSION_DIR` | cli | No | `~/.nimbus/sessions` | Conversation persistence |
| `AWS_ACCESS_KEY_ID` | aws | **Yes (e2e)** | — | |
| `AWS_SECRET_ACCESS_KEY` | aws | **Yes (e2e)** | — | |
| `AWS_REGION` | aws | **Yes (e2e)** | — | |
| `AWS_BUCKET_NAME` | aws | No | — | Falls back for `NIMBUS_CONTAINER` |

---

## Conversation log

Verbatim intent and decisions from recent sessions, newest first. Good enough that a fresh Claude can understand *why* choices were made, not just *what* was done.

---

### 2026-04-20 — Session 3 (Typer migration + pydantic-ai + REPL)

**User intent (opening):** Continue from the session 2 summary. CLI was half-rebuilt (Rich REPL, `/debug`, fresh sessions) but untested. No ruff/mypy run. No real-API smoke test. Todo list was all "in-progress".

**User instruction mid-session:** "Migrate from Rich to https://typer.tiangolo.com/"

**Clarification:** Typer *adds* to Rich — it doesn't replace it. Rich is kept for all interactive REPL UI (Console, Panel, Markdown, Prompt.ask). Typer replaces argparse for the top-level CLI argument parsing (`--session`, `--no-tools`) and provides a properly structured entry point, `--help` rendering, and a `CliRunner` for testing the CLI in isolation.

**What was implemented:**

*Typer migration (`cli.py`, `pyproject.toml`, `test_cli.py`):*
- Added `app = typer.Typer(name="nimbus", ...)` at module level. Entry point changed from `cli:main` to `cli:app` in `pyproject.toml`.
- `main()` decorated with `@app.command()`. Args typed with `Annotated[..., typer.Option(...)]`.
- `--no-tools/--with-tools` bool flag pair replaces `--no-tools` argparse bool. Noqa FBT002 for Typer idiom.
- `raise typer.Exit(code=2) from err` for config error path (B904 compliance).
- `_build_tools_or_empty` signature simplified: no `console` param; uses `typer.echo(typer.style(...))` for pre-REPL output.
- `from collections.abc import Callable` (UP035 fix). `_SlashHandler = Callable[["NimbusCLI", str], bool]` type alias to fix mypy on the dispatch dict.
- `test_cli.py`: `from typer.testing import CliRunner`, `_runner = CliRunner()`. Two entry-point tests (`test_main_without_api_key_exits_with_two`, `test_main_auto_generates_session_when_flag_omitted`) updated to use `_runner.invoke(app, [...])`.
- `typer[all]>=0.12.0` in `pyproject.toml` (Typer 0.24.1 already bundles Rich 15.0.0; the `[all]` suffix is harmless in 0.24+).

*pydantic-ai adoption (integrated externally during session):*
- `openrouter_client.py` fully rewritten: `Agent.run_sync()` replaces hand-rolled `openai.OpenAI()` loop. `OpenAIModel`/`OpenAIProvider` replace direct SDK usage. Constructor gains `pai_model` / `pai_fallback_model` for test injection.
- `_EmptyChoicesError` sentinel handles OpenRouter HTTP-200-with-no-choices edge case, routing through the standard fallback path.
- `_sandbox_result`: C0 control-char stripping regex `_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")` (preserves `\n`, `\t`).
- `test_openrouter_client.py` rewritten: `FunctionModel`-based factories: `_text_model`, `_scripted_model`, `_error_model`, `_tool_call_response`. 29 unit tests covering: plain text, tool call + handler, step budget exceeded, handler exceptions, auth error, rate-limit fallback, 5xx server error fallback, dry-run, event ordering, conversation mutation, multi-turn history, ping success/failure, sandbox wrapping/truncation/control-char strip, FM4 ModelHTTPError 429 paths, listener resilience, debug ring buffer.

*Smoke test:*
- `scripts/smoke_tool_call.py` created: builds `_NoopStorage`, calls `send_message(prompt, tools=..., max_steps=2, dry_run=True)`. Exit 0 = model called `upload_file`, 1 = no tool call, 2 = config error.
- Live smoke test ran on `openai/gpt-oss-120b:free`: model correctly emitted `upload_file({"local_path": "hello.txt", "remote_path": "hello.txt"})`.

*Bugs fixed:*
- `meta-llama/llama-3.3-70b-instruct:free` returned HTTP 200 with `choices=None` — traced to OpenRouter upstream error. Fixed via `_EmptyChoicesError` + fallback path.
- Mock `_send_message` in `_fake_client` didn't mutate conversation — broke `test_send_user_turn_appends_to_conversation_and_saves`. Fixed with `side_effect` function that calls `conv.add_assistant(response_text)`.
- Config Q003: backslash-escaped quote in f-string replaced with outer single quotes.

**What was NOT done (gaps / omissions):**
- **Test coverage is 61%, below the 80% threshold** — CI fails. The REPL `run()` loop (`Prompt.ask` input path) is not covered by unit tests. `cloud_storage_tools.py` quota error paths are not covered. This is the most important gap for the next session.
- CI AI e2e job (`ai-e2e-tests` CircleCI job) — not added.
- Auth walkthrough + Slack adapter design — not started.
- Telemetry (Prometheus metrics, Grafana dashboard) — not wired.
- Fly.io volume creation + deployment verification — not done.
- FM6 rolling conversation summary — still deferred to V2.
- `prompt_toolkit` keybindings (`/ping`, Ctrl-C cancel) — not added.
- One-shot task mode (non-interactive, `nimbus --once "upload hello.txt"`) — not implemented.
- Async agent mode (native `agent.run()` instead of `asyncio.to_thread(run_sync, ...)`) — not done.
- Per-session token budgets exposed via CLI — not done.
- `scripts/benchmark_models.py` cleanup (move to `scripts/dev/`, add `--dry-run`) — not done.

**Final state:** `ruff check .` clean, `mypy --strict .` 0 errors, 62 tests passing, 61% coverage (BELOW threshold — next session must address).

---

### 2026-04-21 — Session 2 (this session)

**User intent (opening):** Continue from the previous session summary. All CI commands were passing. The remaining work was a large multi-part task: finish failure modes FM4–FM10, fix P2/P3 bugs, make `ai_client_api` and `openrouter_ai_client_impl` production-grade with complete READMEs, move two skipped live tests to the e2e layer, update AGENTS.md, fix CI `uv sync --frozen`, and add an AI e2e CI job.

**User instruction mid-session:** "ignore the auth walkthrough, finish up the implementation completely and all the tasks I gave you - the issues, the failure modes, etc, from the last prompt"

**What was implemented:**

*openrouter_client.py*
- Added `import re` and `_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")` at module level (FM7).
- `_try_fallback`: split `except (openai.APIStatusError, ModelHTTPError, UnexpectedModelBehavior)` into two branches — `(openai.APIStatusError, ModelHTTPError)` checks `_http_status(ferr) == 429` and raises `AIRateLimitError`, then `UnexpectedModelBehavior` raises `AIProviderError`. This is FM4 in the fallback path (the primary path was fixed in session 1).
- `_sandbox_result`: now calls `_CONTROL_CHARS_RE.sub("", text)` before truncation (FM7). Docstring updated to `r"""..."""` with backslash-safe examples.
- `_build_model`: `**{"default_headers": ...}` conditional unpack replaced with `default_headers=default_headers` direct kwarg — mypy rejected the unpack because the dict type was `dict[str, dict[str, str]]`. Empty dict is safe to pass to `AsyncOpenAI`.

*cli.py*
- `_save_conversation`: rewrote to write to `.tmp` then `tmp.replace(path)` — atomic on POSIX (FM5).
- `_send_user_turn`: added `self._conversation.pop_last_user()` in the `except AIClientError` branch before printing the error (P2 rollback).

*cloud_storage_tools.py*
- Added `DEFAULT_SESSION_MAX_UPLOAD_BYTES: int | None = None` module constant.
- `build_cloud_storage_tools` gains `session_max_upload_bytes` kwarg. Inside `_upload`, a `_session_bytes_uploaded: list[int] = [0]` list-wrapped counter (avoids `nonlocal`) is checked before every upload and incremented after (FM8).
- Annotated function with `# noqa: PLR0913, C901` since it now has 6 kwargs and the inner closure adds complexity.

*ai_server/router.py*
- Added `import time`, `from dataclasses import dataclass, field`.
- Added `_RATE_LIMIT_CAPACITY`, `_RATE_LIMIT_RPM`, `_RATE_LIMIT_REFILL_RATE` constants (env-overridable).
- Added `_TokenBucket` dataclass and `_rate_buckets: dict[str, _TokenBucket]` module dict.
- Added `_check_rate_limit(user_id: str | None) -> bool`: CPython dict ops are GIL-atomic so check-then-insert is safe in single-event-loop async. `None` user_id is always allowed for backwards compat. Returns `False` when `tokens < 1.0`.
- In `chat()`: `_check_rate_limit(req.user_id)` called before the session lock; raises `HTTPException(429)` on failure (FM10).
- Added `GET /sessions/{session_id}/history` and `DELETE /sessions/{session_id}` endpoints with `MessageRecord`, `SessionHistoryResponse`, `SessionDeleteResponse` Pydantic models.

*ai_server/sessions.py*
- `delete_session(session_dir, session_id) -> bool`: validates ID, calls `path.unlink()`, returns `True`/`False` via try/except/else (TRY300 compliant).
- `list_sessions(session_dir) -> list[str]`: returns sorted list of `.json` stems in the directory.
- `save_session`: already had atomic write (write-tmp-rename) from session 1.

*test_openrouter_client.py* — new tests added:
- `test_sandbox_strips_control_characters`: verifies `\x00`, `\x01`, `\x07`, `\x1b`, `\x7f` stripped; `\n`, `\t` preserved.
- `test_model_http_error_429_raises_rate_limit_error`: `ModelHTTPError(429)` without fallback raises `AIRateLimitError`.
- `test_model_http_error_429_triggers_fallback`: `ModelHTTPError(429)` with fallback model falls back and sets `reason="rate_limit"`.
- `test_fallback_model_http_error_429_raises_rate_limit_error`: both primary and fallback `ModelHTTPError(429)` → `AIRateLimitError`.

*test_cli.py* — new tests added:
- `test_send_user_turn_rolls_back_on_error`: injects `AIProviderError`, checks "will fail" not in `conv.messages()` after the call.
- `test_save_conversation_is_atomic`: calls `_save_conversation`, asserts `.json` exists and `.tmp` does not linger.

*test_router.py* — new test classes:
- `TestRateLimiting`: first request allowed; no-user_id always allowed; exhausted bucket returns 429.
- `TestSessionHistory`: 404 for missing session; requires auth; returns messages after chat; unsafe ID rejected.
- `TestSessionDelete`: nonexistent → `deleted=False`; existing → `deleted=True`, file gone; idempotent; requires auth.

*test_openrouter_integration.py* — migrated:
- `pytestmark = [pytest.mark.e2e]` (was `integration, local_credentials`).
- Assertions are shape-only: `isinstance(response.text, str)`, `response.tokens.total >= 0`, etc.
- `@pytest.mark.skipif` replaced with a module-level `_SKIP_NO_KEY` decorator.

*ci and docs:*
- `.circleci/config.yml`: `uv sync --all-packages --all-groups` → `uv sync --frozen --all-packages --all-groups`.
- `src/ai_client_api/README.md`: full production-grade doc (~170 lines): public surface table, Conversation API, Tool schema, event kinds, exception contract, failure-mode guidance, design notes.
- `src/openrouter_ai_client_impl/README.md`: full doc (~190 lines): env vars table, programmatic usage examples, event listener example, REPL slash commands, failure-mode status table, architecture notes, test commands, benchmark script guidance, free-tier reality check.
- `AGENTS.md`: added `ai_server` package summary and architecture note; Fly.io volume mount + secrets commands; mypy `exclude` rationale (multiple `tests/` packages collide under flat namespace); env vars for `ai_server` and `nimbus` CLI.
- `NIMBUS_STATUS.md`: full rewrite with session log, complete failure-mode status table, remaining backlog in priority order, step-budget rationale, full env-var reference table.

**Ruff/mypy issues encountered and fixed:**
- `RUF003`: ambiguous minus sign (Unicode `−` vs ASCII `-`) in a comment — replaced.
- `TRY300`: `return True` inside `try` block — moved to `else` branch.
- `D301`: docstring with backslash escape needs `r"""` prefix — added.
- `I001`: import block ordering in test — reorganized local imports in test methods to use top-level imports instead.
- `N817`: `TestClient as TC` — renamed to `RLTestClient` then later eliminated by using top-level import.
- `E501`: several long lines in test docstrings and one in cloud_storage_tools comment — shortened.
- `PLR0913`/`C901`: `build_cloud_storage_tools` now has 6 kwargs and higher complexity — added `# noqa`.
- mypy `arg-type` on `**{"default_headers": ...}` unpack — switched to direct `default_headers=` kwarg.

**Final state:** `ruff check .` clean, `ruff format --check .` clean, `mypy --strict .` 0 errors, `pytest src/ -q` 313 passed / 18 skipped / 84% coverage.

**What was deliberately skipped:**
- Auth walkthrough (user explicitly said "ignore the auth walkthrough").
- CI AI e2e job (`ai-e2e-tests` CircleCI job) — left for next session.
- UX/UI polish (`prompt_toolkit`, `/ping` command).
- FM6 rolling summary — deferred to V2.
- Telemetry wiring (Prometheus metrics).
- `scripts/benchmark_models.py` cleanup.

---

### 2026-04-21 — Session 1 (pre-summary, reconstructed from summary)

**User intent:** "Let's first work on Auth walkthrough + Slack design for this entire thing and complete the session management part of this ai integration. Once we are done with that, we will do another system design + code review before you work on Failure modes 2–10…"

Then mid-session: "ignore the auth walkthrough, finish up the implementation completely and all the tasks I gave you."

**What was implemented in session 1:**
- Ran all CI commands locally (ruff, mypy, pytest) and made them pass.
- Updated CircleCI branch filter `hw-2` → `hw-3`.
- Built `ai_server` from scratch: `main.py`, `router.py`, `sessions.py`, `auth.py`, `Dockerfile`, `fly.toml`.
- `ai_server/router.py`: `POST /chat/turn` with signed auth, `GET /health`, plus session-management endpoints. Per-session `asyncio.Lock` via `_session_locks` dict. `asyncio.to_thread` for blocking `send_message`.
- `ai_server/sessions.py`: `load_session`, `save_session` (atomic write-tmp-rename), `_validate_session_id` (regex safelist).
- `ai_client_api/conversation.py`: added `pop_last_user()` for optimistic rollback.
- `ai_client_api/client.py` ABC docstring: updated to clarify that `AIToolExecutionError`/`AIUnknownToolError` MAY be raised but `OpenRouterClient` feeds errors back as tool results instead.
- `openrouter_client.py _build_model` (P3): attribution headers via `openai.AsyncOpenAI(default_headers=...)` → `OpenAIProvider(openai_client=...)`.
- `openrouter_client.py _run_with_fallback` (FM4 primary path): explicit `status == 429` check for `ModelHTTPError` before `status >= 500` check.
- `pyproject.toml`: added `**/scripts/*.py` per-file-ignores; added `exclude = ["src/.*/tests/", "tests/"]` to `[tool.mypy]`; added `src/ai_server`, `src/ai_client_api`, `src/openrouter_ai_client_impl` to `mypy_path`.
- Fixed mypy duplicate module collision (`tests/` in multiple packages all mapping to top-level `tests` — fixed by exclude without removing `__init__.py`).
- Fixed `type: ignore[return-value]` in `auth.py` that became unused after mypy narrowing.
- Fixed `arg-type` in `slack_tools.py`: `DeleteFileArgs(**raw)` → `DeleteFileArgs.model_validate(dict(raw))`.

**Key decisions recorded:**
- Session lock dict is never cleaned up — each `asyncio.Lock` is tiny, bounded by number of Slack channels.
- `asyncio.to_thread` for `run_sync` — migrate to native `agent.run()` async in a follow-up.
- Mypy `exclude` for test dirs rather than removing `__init__.py` — removing breaks `from tests.conftest import ...` in test files.
