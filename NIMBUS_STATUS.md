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
4. `ai_server`: FastAPI HTTP wrapper with `POST /chat`, `GET /sessions/{id}/history`, `DELETE /sessions/{id}`, per-session `asyncio.Lock`, per-user token-bucket rate limiting (FM10), atomic session file writes.
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
- Latest pushed commit `e246957`: signed wrapper-facing AI contract for chat adapters.
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
- `/ai/chat/turn` now accepts a stable wrapper-owned attachment metadata contract:
  - request model includes optional `attachments[]`
  - each attachment carries `platform_file_id`, `filename`, `content_type`, and
    `size_bytes`
  - the route validates count/size/content-type bounds and exposes attachment
    metadata to the AI turn as safe context
  - exact Slack file -> Nimbus mapping lives in `docs/source/nimbus-ai-service.md`
- Wrapper conversation IDs no longer fail persistence when normalized chat IDs
  exceed 128 characters:
  - `sessions.py` now maps long logical session IDs to deterministic hashed
    filename stems while preserving the full logical `session_id` in JSON
  - worst-case wrapper ID lengths are covered in
    `src/ai_server/tests/test_wrapper_contract.py`
  - direct session round-trip coverage lives in `src/ai_server/tests/test_sessions.py`
- Wrapper rate limiting now keys on the real Model A principal:
  - `/ai/chat/turn` uses `platform:workspace_id:user_id`
  - same `user_id` in two workspaces no longer collides in the token bucket
  - coverage lives in `src/ai_server/tests/test_wrapper_contract.py`
- Wrapper replay/idempotency state is no longer process-memory-only on the
  deployed single-machine shape:
  - signed-request nonce state and idempotent turn responses now persist under
    `AI_SESSION_DIR/_request_state`
  - wrapper retries and replay checks survive service restarts on the mounted
    Fly.io volume
  - the current guarantee still assumes one machine / one process, matching
    `fly.toml`
  - coverage lives in `src/ai_server/tests/test_request_state.py` and
    `src/ai_server/tests/test_wrapper_contract.py`
- Review/TODO handoff file now exists at `NIMBUS_NEXT_TODOS.md`.
- Worktree currently includes local follow-up docs/todo edits plus `scripts/benchmark_results.json` as untracked data, not code.
- Default models: **`z-ai/glm-4.5-air:free`** (primary, Novita) + **`nousresearch/hermes-3-llama-3.1-405b:free`** (fallback, DeepInfra). Neither is Venice.
- `DEFAULT_MAX_STEPS = 8` in `config.py`. Rationale: cloud-storage tasks are ≤ 4 steps in practice; 8 is the right ceiling — enough for complex chaining, prevents runaway at 10× load (see step-budget note below).
- System prompt has the anti-loop line: *"After list_files returns, summarize immediately. Do NOT call get_file_info on individual entries unless the user explicitly asks about a specific file."*
- CLI entry point is `openrouter_ai_client_impl.cli:app` (Typer `app` object, not `main` function). `nimbus` command works.
- pydantic-ai fully adopted as the agent core (`Agent.run_sync()`). Test suite uses `FunctionModel` — no real HTTP in unit tests.
- Full local checks now pass:
  - `uv run pytest` -> **316 passed, 19 skipped**
  - `uv run ruff check .` -> clean
  - `uv run mypy --strict .` -> clean
  - `uv run sphinx-build docs/source docs/build/html` -> clean

---

## What was done in the last three sessions

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

### 0. Raise test coverage above 80% (BLOCKING — CI fails)

Coverage is at 61% total (threshold is 80%). The biggest gaps:
- `cli.py` REPL `run()` loop: the `Prompt.ask` / `EOFError` path is not exercised in tests. The simplest fix is to extract the inner loop body into a testable `_process_input(text: str)` method and test that directly.
- `cloud_storage_tools.py`: session upload quota exceeded path, permission/path error path.
- `ai_server/router.py`: some fallback branches in chat endpoint.
- Check `uv run pytest src/ -q --cov --cov-report=term-missing` to see exactly which lines are uncovered.

### 1. Auth walkthrough + Slack adapter design
- Design doc for the end-to-end auth flow: Slack → `ai_server` → OpenRouter → S3.
- Define thin `slack_adapter` package (event handler, slash-command dispatch, message formatting).
- `build_slack_tools(storage=...)` scaffold exists in `ai_server/slack_tools.py` — wire it up.
- Session ID = Slack channel ID or thread timestamp.
- Auth: `X-API-Key` header for server-to-server; OAuth for user-facing Slack commands (decide whether to implement or stub).

### 2. Add AI e2e job to CircleCI
- New `ai-e2e-tests` job that:
  - Uses `openrouter` context (injects `OPENROUTER_API_KEY`, `AI_SERVER_BASE_URL`, `AI_SERVER_API_KEY`).
  - Runs `uv run pytest src/ai_server/tests/test_e2e.py -m e2e`.
- Slot it after `e2e-tests` (AWS) and before `deploy-fly` in the workflow.
- `src/ai_server/tests/test_e2e.py` already has the skeleton with `e2e_base_url` / `e2e_api_key` fixtures.

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
- Set secrets: `flyctl secrets set AI_SESSION_DIR=/data/sessions AI_SERVER_API_KEY=<key>`.
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
3. The top Priority 0 directive now lives in `NIMBUS_NEXT_TODOS.md:0.0`: finish the wrapper contract completely and the full Slack/Nimbus functionality, and after each completed slice update docs, run local CI, and commit.
4. Full local checks currently pass; do not regress them.
5. Do not touch `router.py`, `auth.py`, or `sessions.py` without re-reading them first — they now carry the wrapper-facing contract and are easy to accidentally regress.
6. Before adding any new package dep, check `pyproject.toml` to confirm it is not already present.
7. The CLI entry point is `cli:app` (Typer), not `cli:main`. If you see `ImportError: cannot import name 'main'` in tests, check that `test_cli.py` imports `app` not `main`.

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
| `AI_SERVER_API_KEY` | ai_server | **Yes** | — | Shared secret for `X-API-Key` header |
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
- `ai_server/router.py`: `POST /chat` with session locking, `GET /health`. Per-session `asyncio.Lock` via `_session_locks` dict. `asyncio.to_thread` for blocking `send_message`.
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
