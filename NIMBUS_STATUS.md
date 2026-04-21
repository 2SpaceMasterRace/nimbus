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
- Latest commit `da0f098`: failure modes FM4–FM10, P2/P3, session mgmt, production READMEs.
- Worktree: **clean** (all changes committed).
- Default models: **`z-ai/glm-4.5-air:free`** (primary, Novita) + **`nousresearch/hermes-3-llama-3.1-405b:free`** (fallback, DeepInfra). Neither is Venice.
- `DEFAULT_MAX_STEPS = 8` in `config.py`. Rationale: cloud-storage tasks are ≤ 4 steps in practice; 8 is the right ceiling — enough for complex chaining, prevents runaway at 10× load (see step-budget note below).
- System prompt has the anti-loop line: *"After list_files returns, summarize immediately. Do NOT call get_file_info on individual entries unless the user explicitly asks about a specific file."*

---

## What was done in the last two sessions

### Session 1 (pre-summary)
- Ran all CircleCI commands locally (ruff, mypy --strict, pytest) and made them pass.
- Updated CircleCI branch filter from `hw-2` to `hw-3`.
- Reviewed AGENTS.md.
- Built `ai_server` from scratch: `router.py`, `sessions.py`, `auth.py`, FastAPI app, Dockerfile, Fly.io config.
- Added `Conversation.pop_last_user()` to `ai_client_api` for optimistic-mutation rollback.
- Fixed `AIClient` ABC docstring to accurately describe `AIToolExecutionError`/`AIUnknownToolError` contract.
- Fixed `_build_model` (P3): attribution headers now threaded via `openai.AsyncOpenAI(default_headers=...)` → `OpenAIProvider(openai_client=...)`.
- Fixed FM4 in `_run_with_fallback`: explicit `status == 429` check for `ModelHTTPError`.

### Session 2 (this session — commit `da0f098`)
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

These were discussed but **not implemented** this session. The next session should start here.

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
2. The next concrete deliverables are: **CI AI e2e job**, **Auth/Slack design doc**, **telemetry wiring**, and **Fly.io volume/deploy verification**.
3. Do not touch `router.py` or `sessions.py` without re-reading them first — they are in a good state and easy to accidentally regress.
4. The step-budget math above should be added as a comment block in `config.py` if it is not already there.
5. Before adding any new package dep, check `pyproject.toml` to confirm it is not already present.

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
