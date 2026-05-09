# Spec: AI System Redesign — Evals, Guardrails, and Onboarding

**Status:** Draft  
**Author:** (you)  
**Created:** 2026-05-08  
**Target branch:** main  

---

## Overview

Nimbus has a working AI integration: OpenRouter-backed `AIClient`, tool calling over
cloud storage, HMAC-signed wrapper protocol, session persistence, and basic guardrails.
HW3 is shipped. The system works.

What the system does not yet have is *production AI quality discipline*: no systematic
evaluation of model behavior, no typed policy enforcement, no onboarding path for new
users. These are not stretch goals — they are the difference between a demo that passes
a code review and a system a senior engineer would trust in production.

This spec covers three areas in one coordinated redesign:

1. **Evals** — a harness to measure and gate AI behavior quality before code ships.
2. **Guardrails** — replace ad-hoc checks with a typed `PolicyDecision` enforcement
   layer that every tool call, confirmation, and attachment must pass through.
3. **User Onboarding** — a first-run experience that gets a new user from zero to a
   working AI session in under two minutes without reading the docs.

These three areas are deeply coupled. Guardrails need evals to know they work. Onboarding
exercises guardrails on a known-safe subset of capabilities. Evals need realistic
onboarding flows as fixtures. Build them together or you will build them twice.

---

## Current State

### What Exists

| Component | Current shape |
|---|---|
| `ai_client_api` | Provider-neutral `AIClient`, `Conversation`, `Tool`, `AIResponse` |
| `openrouter_ai_client_impl` | OpenRouter via OpenAI-compatible SDK, pydantic-ai loop |
| `nimbus_runtime` | Session orchestration, action state machine, SQLite stores, telemetry |
| `ai_server` | FastAPI HTTP wrapper, HMAC auth, idempotency, rate limiting |
| Guardrails | Container pinning, `safe_root` sandbox, 100MB upload cap, delete `confirm=True` flag, 4000-char tool result truncation, 5-step loop budget, primary→fallback model switch |
| Tests | 38 Hypothesis property tests, 3 fuzz harnesses, BDD features for chat turn and confirmation flow, unit/integration tiers |
| Observability | structlog, Sentry, OTel setup, in-memory runtime telemetry |

### What Does Not Exist

- **Evals**: No golden-set test suite. No scoring of model output quality, tool call
  accuracy, guardrail bypass rate, or confirmation flow correctness. No CI gate that
  fails when model behavior regresses.
- **Typed policy layer**: `PolicyDecision` is documented as a target in
  `complete-system-design.md` but not implemented. Guards are scattered inline checks
  (container pinning in tool binding code, delete confirm in tool schema, safe_root in
  path resolver) with no unified deny/allow/confirm/admin-review outcome type.
- **RuntimeSpec**: Documented as a target. Not yet recorded with turns or actions.
  There is no way to answer "which model policy, prompt version, and feature flags were
  active when this action was authorized?"
- **Onboarding**: No first-run experience. No guided setup. New user clones the repo,
  reads scattered docs, sets nine env vars, and hopes.
- **Prompt injection defense beyond truncation**: Tool results are wrapped in
  `<tool_result source="untrusted">` and truncated. No structural validation that object
  names, prefixes, or metadata fed back to the model cannot carry instruction-injection
  payloads.
- **Token / cost budget per request**: `plans.md` lists this as a known gap.
- **Eval-as-CI**: There is no job that runs a model interaction and asserts output shape,
  tool call selection, or policy compliance.

---

## Design Goals

| Goal | Not-goal |
|---|---|
| Evals run offline, deterministically, in CI without live OpenRouter calls | Replacing the live provider; evals supplement it |
| PolicyDecision is the single enforcement point for every side-effecting tool | Adding a new runtime framework; adapt what exists in `nimbus_runtime` |
| Onboarding is a first-run CLI + env-setup path, not a web wizard | Building a GUI; the Nimbus REPL is the primary surface |
| Guardrail coverage is measurable (bypass rate, false-positive rate, P95 latency overhead) | Eliminating all model creativity; only side-effecting tools are gated |
| Spec is self-contained enough to implement in one sprint without re-reading design docs | Replacing the existing design; this extends it |

---

## Requirements

### REQ-EVAL-01 — Deterministic eval harness

The system must have an offline eval harness that:

- Accepts a golden dataset of `(prompt, conversation_context, expected_outcome)` tuples.
- Runs each case against a `FakeAIClient` (deterministic, no network calls) or optionally
  against a live provider.
- Scores each case on a rubric: `tool_selected`, `tool_args_correct`, `response_contains`,
  `policy_outcome`, `no_side_effects_without_confirm`.
- Produces a machine-readable report (JSON) and a human-readable summary.
- Can be run with `uv run pytest -m eval` in CI without live credentials.

**Acceptance criteria:**
- `pytest -m eval` passes in CI using `FakeAIClient`.
- At least 20 golden cases covering: list files, upload, delete (with and without confirm),
  guardrail bypass attempt, prompt injection via filename, rate-limit exhaustion, and
  wrong-actor confirmation.
- A new model policy change that breaks three or more golden cases fails CI.

---

### REQ-EVAL-02 — Guardrail effectiveness tests

For each existing guardrail (container pinning, safe_root, size cap, delete confirmation,
step budget, result truncation) there must be at least one eval case that:

- Sends a prompt designed to bypass that guardrail.
- Asserts the bypass does not succeed (action is not executed or `PolicyDecision.DENY`
  is returned).
- Asserts the response to the user is informative, not a silent failure or a 500.

**Acceptance criteria:**
- 10 adversarial eval cases, one per guardrail, checked in as `tests/evals/adversarial/`.
- Zero bypasses pass on the main branch.

---

### REQ-EVAL-03 — Eval fixtures are realistic

Eval fixtures must use the same `ChatTurnInput` / `ChatTurnResult` schema used in
production. They must not bypass `nimbus_runtime` by calling tool code directly.

**Acceptance criteria:**
- All eval cases go through `NimbusRuntime.handle_turn()` or the full HTTP stack via
  `TestClient`.
- Fixture factories use `polyfactory` or `pytest.fixture` with typed Pydantic models,
  not raw `dict[str, Any]`.

---

### REQ-GUARD-01 — Typed `PolicyDecision`

Replace inline guardrail checks with a typed `PolicyDecision` domain object:

```python
class PolicyDecision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"        # proceed after actor confirmation
    ADMIN_REVIEW = "admin_review"  # escalate; block until reviewed

@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    reason: str
    required_confirmation: ConfirmationSpec | None = None
    audit_fields: dict[str, str] = field(default_factory=dict)
```

Every tool call must pass through a `PolicyEngine.evaluate(actor, action_kind, params,
context) -> PolicyResult` before the action is created or executed.

**Acceptance criteria:**
- `PolicyEngine` lives in `nimbus_runtime.policy` (already partially exists).
- All existing guardrails are re-expressed as `PolicyEngine` rules, not inline checks.
- `PolicyResult` is logged with every action creation event.
- A `DENY` from `PolicyEngine` never reaches the executor.
- A unit test asserts that removing one `PolicyEngine` rule makes a guardrail bypass
  eval case pass — i.e., the test proves the rule does work.

---

### REQ-GUARD-02 — Prompt injection structural defense

Object names, prefixes, metadata values, and tool results re-entering the model context
must be validated against an allowlist of safe characters before inclusion.

**Specification:**
- Allowed characters in object names re-entered into model context: `[A-Za-z0-9._/-]`.
- Names containing characters outside that set are either rejected at upload time
  (strict mode) or replaced with a sanitized representation (permissive mode) before
  appearing in the model's message history.
- The current `<tool_result source="untrusted">` wrapping is preserved.
- The sanitization function is tested with Hypothesis over arbitrary Unicode strings.

**Acceptance criteria:**
- `tests/evals/adversarial/test_prompt_injection.py` includes at least three cases:
  filename containing instruction text, metadata value containing `SYSTEM:` prefix,
  list result with embedded newline + instruction fragment.
- All three cases return `PolicyDecision.DENY` or the injected content is provably
  inert (wrapped and truncated before the model sees it).

---

### REQ-GUARD-03 — Per-request cost budget

The runtime must accept a `cost_budget_usd: float | None` field on `ChatTurnInput`.
When a budget is set, `NimbusRuntime` tracks estimated token cost during the turn and
raises `TurnBudgetExceededError` (a subclass of `AIClientError`) before initiating
a model call that would exceed it.

**Acceptance criteria:**
- `ChatTurnInput.cost_budget_usd` is optional; `None` means no limit (current behavior).
- Exceeding the budget returns a structured error response, not a 500.
- One Hypothesis test asserts that a budget of `$0.0` always returns a budget error
  before any tool call is made.

---

### REQ-GUARD-04 — RuntimeSpec recorded with every turn

Every call to `NimbusRuntime.handle_turn()` must record a `RuntimeSpec` snapshot:

```python
@dataclass(frozen=True)
class RuntimeSpec:
    spec_version: str          # semver, bump on any behavioral change
    model_id: str
    fallback_model_id: str | None
    prompt_version: str        # git SHA or explicit version tag
    max_tool_steps: int
    tool_names: tuple[str, ...]
    policy_version: str        # version of the PolicyEngine rule set
    feature_flags: dict[str, bool]
    cost_budget_usd: float | None
```

The `RuntimeSpec` is stored as a field on the action/event record and is included in
audit export.

**Acceptance criteria:**
- Every `ActionRecord` in SQLite has a `runtime_spec_json` column.
- `GET /ai/sessions/{id}/history` includes `runtime_spec.spec_version` in each turn.
- A migration adds the column without breaking existing records (NULL for historical rows).

---

### REQ-ONBOARD-01 — `nimbus setup` wizard

A new `nimbus setup` CLI command (new Typer subcommand in `openrouter_ai_client_impl`)
guides a new user through first-run setup:

1. Detects which required env vars are missing.
2. For each missing var, prints a one-sentence explanation of what it does and where to
   get it (no URL-fishing — the instructions are hardcoded in the CLI, not fetched).
3. Prompts the user to enter the value, masks secret values on input.
4. Writes a `credentials.env` file (which is already gitignored).
5. Runs `nimbus ping` to verify the configuration is working before exiting.

**Acceptance criteria:**
- `nimbus setup --dry-run` prints what would be written without touching the filesystem.
- `nimbus setup` with all vars pre-set reports "All required variables are set" and exits 0.
- Unit test uses `tmp_path` and a fake environment to assert correct file output.

---

### REQ-ONBOARD-02 — `nimbus quickstart`

A `nimbus quickstart` command runs a guided interactive session that demonstrates the
three core capabilities: list files, upload a sample file, ask a natural-language question
about storage. Each step includes inline explanation of what is happening and why.

**Acceptance criteria:**
- `nimbus quickstart --non-interactive` runs the full sequence with pre-set answers,
  against a `FakeStorageClient` and `FakeAIClient`, and exits 0.
- The sequence uses real `NimbusRuntime` (not bypassed) to prove the onboarding path
  exercises the same code paths as production.

---

### REQ-ONBOARD-03 — Getting-started doc

A `docs/source/getting-started.md` page (already a stub) must be complete enough that
a new contributor can run `nimbus quickstart` without any other documentation.

Minimum content:
- Prerequisites (Python 3.12+, uv, OpenRouter API key, AWS credentials for storage).
- `nimbus setup` → `nimbus quickstart` sequence.
- What each credential is, what it does, how to get it.
- A "next steps" section linking to the architecture overview and testing guide.

**Acceptance criteria:**
- `uv run sphinx-build docs/source docs/build/html` passes after edits.
- A first-time contributor on the team's peer reviewer list confirms the page is
  sufficient to get started without asking questions.

---

## Technical Design

### Package changes

| Package | Change |
|---|---|
| `nimbus_runtime` | Add `policy.py` `PolicyEngine` + `PolicyDecision` + `PolicyResult` (partially exists); add `RuntimeSpec` dataclass; add `runtime_spec_json` column to `ActionRecord`; add `cost_budget_usd` tracking to `NimbusRuntime.handle_turn()` |
| `ai_client_api` | Add `TurnBudgetExceededError` to the exception hierarchy |
| `openrouter_ai_client_impl` | Add `nimbus setup` and `nimbus quickstart` Typer subcommands |
| `ai_server` | Thread `cost_budget_usd` from `ChatTurnInput` into `NimbusRuntime`; include `runtime_spec` in session history response |
| `tests/evals/` | New directory; `conftest.py` with `FakeAIClient`, `FakeStorageClient`, `EvalFixture` factory; `golden/`, `adversarial/` subdirectories |

### Eval harness architecture

```
tests/evals/
  conftest.py           FakeAIClient, FakeStorageClient, NimbusRuntime wired with fakes
  fixtures.py           EvalCase dataclass, score() -> EvalResult
  golden/
    test_list_files.py
    test_upload.py
    test_delete_with_confirm.py
    test_delete_without_confirm.py
    ...
  adversarial/
    test_container_escape.py
    test_safe_root_escape.py
    test_prompt_injection.py
    test_delete_bypass.py
    test_step_budget_overflow.py
    ...
```

`FakeAIClient` is a deterministic stub: given a `(prompt, tools)` input it returns a
pre-scripted `AIResponse` from a lookup table. This makes evals reproducible without
network calls and without Hypothesis — the adversarial cases are fixed inputs, not
generated ones.

### PolicyEngine integration points

```
ChatTurnInput arrives at NimbusRuntime.handle_turn()
  -> PolicyEngine.evaluate(actor, "read_only", params)  ← non-destructive reads
  -> direct model call if ALLOW

  -> model returns tool call
  -> PolicyEngine.evaluate(actor, tool_kind, tool_args)
  -> DENY: return error response to model, increment deny counter
  -> CONFIRM: create pending action, return confirmation_required
  -> ALLOW: create action, execute, verify, record artifact
```

The key constraint: `PolicyEngine.evaluate()` is pure. It takes only typed inputs and
returns a `PolicyResult`. It does not call the database, the model, or storage. This
makes it trivially testable and fast.

### RuntimeSpec versioning

`spec_version` follows semver. The CI `unit-tests` job includes a check that `spec_version`
in `nimbus_runtime/models.py` is bumped when `policy.py`, `runtime.py`, or
`stores.py` changes. This is a grep-based check, not a semantic one — the point is to
force a conscious version bump when behavior changes.

### Onboarding CLI architecture

`nimbus setup` is a new Typer command group. It uses `pydantic-settings` (already planned
in `plans.md`) to define `NimbusSettings` — the single authoritative list of required
env vars with names, descriptions, and whether they are secret. `nimbus setup` iterates
`NimbusSettings.model_fields` to discover what is missing. This means the settings
definition is the single source of truth for both runtime validation and onboarding
prompts.

---

## Implementation Tasks

### Phase 0: Foundations (do first, everything else depends on this)

- [ ] 0.1 — Define `PolicyResult` and `PolicyDecision` in `nimbus_runtime/policy.py`.
  Write unit tests for the type before adding any rules.
- [ ] 0.2 — Define `RuntimeSpec` dataclass in `nimbus_runtime/models.py`.
  No DB changes yet — just the type.
- [ ] 0.3 — Define `NimbusSettings` in a new `nimbus_runtime/settings.py` using
  `pydantic-settings`. List every required env var from `AGENTS.md`. Unit test that
  missing vars raise a clear error.
- [ ] 0.4 — Define `EvalCase`, `EvalResult`, and `FakeAIClient` in
  `tests/evals/conftest.py`. The fake takes a `script: list[AIResponse]` and returns
  them in order. Write one smoke eval test to validate the harness.

### Phase 1: Guardrails → PolicyEngine

- [ ] 1.1 — Move container pinning from tool binding code into a `PolicyEngine` rule.
  Write a unit test that proves the old inline check is gone and the rule fires.
- [ ] 1.2 — Move `safe_root` path resolution into a `PolicyEngine` rule.
- [ ] 1.3 — Move delete `confirm=True` check into a `PolicyEngine` rule.
  The tool schema still has `confirm: bool = False` for backward compat, but the
  runtime gate is now `PolicyEngine`, not Pydantic field default.
- [ ] 1.4 — Move upload size cap into a `PolicyEngine` rule.
- [ ] 1.5 — Move step budget check into a `PolicyEngine` rule (or keep it at
  `AIClient` level — document the decision either way).
- [ ] 1.6 — Log `PolicyResult` with every action creation event. Add `policy_decision`
  and `policy_reason` fields to the action creation event payload.
- [ ] 1.7 — Write the 10 adversarial eval cases in `tests/evals/adversarial/`.
  Each one must fail (return bypass) if its corresponding `PolicyEngine` rule is
  commented out.

### Phase 2: RuntimeSpec recording

- [ ] 2.1 — Add `runtime_spec_json TEXT` column to `ActionRecord` in SQLite stores.
  Write migration `scripts/db/migrations/002_add_runtime_spec.sql`.
- [ ] 2.2 — Populate `RuntimeSpec` from `NimbusRuntime.handle_turn()` config and
  store it with each action.
- [ ] 2.3 — Include `runtime_spec.spec_version` in `GET /ai/sessions/{id}/history`.
- [ ] 2.4 — Add `spec_version` bump check to the `unit-tests` CI job.

### Phase 3: Cost budget

- [ ] 3.1 — Add `TurnBudgetExceededError` to `ai_client_api` exception hierarchy.
- [ ] 3.2 — Add `cost_budget_usd: float | None = None` to `ChatTurnInput`.
- [ ] 3.3 — Wire budget tracking into `NimbusRuntime.handle_turn()` using token count
  estimates from `openrouter_ai_client_impl`. Raise `TurnBudgetExceededError` before
  any model call that would exceed budget.
- [ ] 3.4 — Write Hypothesis test: budget of `$0.0` always raises before first tool call.

### Phase 4: Eval golden set

- [ ] 4.1 — Write 20 golden eval cases covering the list in REQ-EVAL-01.
- [ ] 4.2 — Add `eval` pytest marker to `pyproject.toml`.
- [ ] 4.3 — Add `eval-tests` CircleCI job that runs `pytest -m eval`.
- [ ] 4.4 — Document the eval harness in `docs/source/testing.md` under a new
  "Evals" section.

### Phase 5: Prompt injection structural defense

- [ ] 5.1 — Write `nimbus_runtime.policy.sanitize_untrusted_string(s: str) -> str`
  that replaces non-allowlisted characters with `<U+XXXX>` placeholders.
- [ ] 5.2 — Apply sanitization to object names and metadata values before they enter
  the model context in `openrouter_ai_client_impl/cloud_storage_tools.py`.
- [ ] 5.3 — Test with Hypothesis over arbitrary Unicode strings: sanitized output
  contains only allowlisted characters.
- [ ] 5.4 — Write three adversarial eval cases in `tests/evals/adversarial/test_prompt_injection.py`.

### Phase 6: Onboarding

- [ ] 6.1 — Add `nimbus setup` command to `openrouter_ai_client_impl/cli.py`.
  Implement `--dry-run` flag. Unit test with `tmp_path`.
- [ ] 6.2 — Add `nimbus quickstart` command. Wire it to `FakeStorageClient` and real
  `NimbusRuntime` so the path is exercised fully. Add `--non-interactive` flag.
  Integration test using `CliRunner`.
- [ ] 6.3 — Complete `docs/source/getting-started.md`. Run Sphinx, verify it renders.
- [ ] 6.4 — Peer review the getting-started doc against REQ-ONBOARD-03 acceptance criteria.

---

## Invariants This Spec Must Not Break

These are load-bearing commitments from `INVARIANTS.md` and `complete-system-design.md`.
Every task above must be checked against them:

1. `PolicyEngine.evaluate()` is pure and has no I/O side effects.
2. A `DENY` from `PolicyEngine` must never reach the action executor or the storage client.
3. `RuntimeSpec` is immutable once created for a turn; it must not be mutated by
   downstream code.
4. `TurnBudgetExceededError` is a sub-class of `AIClientError` (not a raw Python exception),
   so the existing REPL and `ai_server` error handling catches it correctly.
5. `nimbus setup` must not commit secrets to git. The `credentials.env` target path must
   be validated against `.gitignore` before writing.
6. Eval cases run without live AWS or OpenRouter credentials. Any case that requires
   real credentials must be marked `@pytest.mark.local_credentials` and excluded from CI.
7. `spec_version` bump is a human-conscious act, not an auto-increment. CI enforces it
   was bumped; it does not bump it automatically.

---

## Open Questions

These must be answered before Phase 1 starts. Do not assume answers in code.

| Question | Who answers | Implication |
|---|---|---|
| Should `PolicyEngine.evaluate()` also cover read-only tool calls (`list_files`, `get_file_info`)? Currently only destructive calls are gated. | Team design session | If yes, Phase 1 scope doubles. |
| What is the token cost estimation strategy for `cost_budget_usd`? OpenRouter does not return token counts before the call completes. | API capability check | May need post-call accounting instead of pre-call gating. |
| Should `nimbus setup` write to `credentials.env` or to a system keychain (macOS Keychain, `pass`, `1Password CLI`)? | Security posture decision | Keychain is safer but adds complexity and platform coupling. |
| Does the Slack bridge (`slack_bridge`) need its own `nimbus setup` step? | `slack_bridge` README | If yes, `NimbusSettings` needs a `slack` section. |
| Which teams in the class are we integrating with? The `cloud_storage_api` is consumed by at least Teams 6 and 10. Do any of them need `PolicyDecision` to be part of the shared API? | Cross-team coordination | If yes, `PolicyDecision` moves into the external `cloud_storage_api` package. |

---

## Success Metrics

When this spec is fully implemented, the following must all be true:

| Metric | Target |
|---|---|
| Adversarial eval bypass rate | 0 / 10 on main branch |
| Golden eval pass rate | 20 / 20 on main branch |
| `PolicyEngine.evaluate()` P99 latency | < 1 ms (it is pure computation) |
| `nimbus setup` time to working session | < 2 minutes for a user with valid credentials |
| CI time for `eval-tests` job | < 60 seconds (no live network calls) |
| Coverage of `nimbus_runtime/policy.py` | ≥ 90% |

---

## Reference

- [`docs/source/complete-system-design.md`](../docs/source/complete-system-design.md) — canonical system design
- [`docs/source/DESIGN.md`](../docs/source/DESIGN.md) — component-level design doc
- [`docs/source/ai-client-guardrails.md`](../docs/source/ai-client-guardrails.md) — existing guardrail inventory
- [`src/nimbus_runtime/INVARIANTS.md`](../src/nimbus_runtime/INVARIANTS.md) — runtime kernel invariants
- [`plans.md`](../plans.md) — Sprint 5+ backlog including policy and onboarding items
- `AGENTS.md` — development rules and system mindset
