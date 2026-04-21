# Nimbus session state

Living checkpoint so a new Claude Code chat can pick up where the previous one left off. Not a mentor prompt (see `CLAUDE.md` for that) and not a teaching template (see `MENTOR.md`). Update when direction changes, not on every edit.

## Project at a glance

Nimbus is an LLM-powered cloud-storage assistant.

- **`src/ai_client_api`** — provider-agnostic contract (`AIClient`, `Conversation`, `Tool`, `AIResponse`).
- **`src/openrouter_ai_client_impl`** — OpenRouter-backed implementation + Typer REPL (`nimbus` CLI).
- **`src/aws_client_impl`** / **`src/aws_client_adapter`** — S3 implementations of `CloudStorageClient`.
- **`src/aws_client_service`** — FastAPI service wrapping the S3 client.
- **`src/aws_s3_cloud_storage_service_client`** — HTTP client for the service.

Two independent axes: *Cloud-Storage Vertical* (teams 2, 6, 10) exposes `CloudStorageClient`; *AI client* wraps it so an LLM can upload / list / download.

## HW3 scope (this branch: `hw-3`)

1. Migrated `OpenRouterClient` from a hand-rolled agentic loop to **pydantic-ai `Agent.run_sync()`**. External contract unchanged. Commit `fa0a732`.
2. Tool bindings in `openrouter_ai_client_impl/cloud_storage_tools.py`: `upload_file`, `download_file`, `list_files`, `get_file_info`, `delete_file`. Arguments validated by Pydantic; container pinned at bind time; paths constrained to `safe_root`.
3. REPL (`openrouter_ai_client_impl/cli.py`): Rich banner, tool-call events, slash commands, `credentials.env` auto-load.
4. Benchmark script (`scripts/benchmark_models.py`): scores free-tier models across 5 tasks with retry-on-429 and per-model failure diagnostics.

## HW3 assignment grounding

The official HW3 prompt now in chat makes these points explicit:

- AI chat completions are a solved problem; the real challenge is wiring AI into the architecture cleanly.
- Every team must integrate an external AI client plus at least one other team's vertical through the shared API contract.
- The system must be deployed and managed via IaC.
- Telemetry is mandatory: request latency, success rate, and failure rate.
- First submission was shared vertical API alignment; second submission is AI + cross-vertical integration + integration tests; final adds full demo, pipeline walkthrough, and telemetry view.

## Current state

- Branch: `hw-3`, 2 commits ahead of `origin/hw-3`.
- Latest commit `42392af`: CI deploy branch filter updated from `hw-2` to `hw-3`.
- Default models: **`z-ai/glm-4.5-air:free`** (primary, Novita) + **`nousresearch/hermes-3-llama-3.1-405b:free`** (fallback, DeepInfra). Neither is on Venice.
- `DEFAULT_MAX_STEPS = 8` in `config.py`. Rationale in the comment block there.
- System prompt has the anti-loop line: *"After list_files returns, summarize immediately. Do NOT call get_file_info on individual entries unless the user explicitly asks about a specific file."*
- Worktree is currently dirty:
  - modified: `AGENTS.md`
  - modified: `src/ai_server/ai_server/router.py`
  - modified: `src/ai_server/ai_server/sessions.py`
  - untracked: `src/openrouter_ai_client_impl/scripts/benchmark_results.json`

## Free-tier reality check

OpenRouter's `free-models-per-day` cap is **global across all `:free` models** on a single account, not per-model. Two benchmark runs can exhaust it for the day. $10 credits unlocks 1000 req/day.

Venice upstream (the free backend for `meta-llama/llama-3.3-*` and `qwen/qwen3-next-*`) has an **8 RPM shared cap** — collapses under multi-user load. All default models now route to Novita / DeepInfra / Google.

## Failure-mode backlog (priority order)

Also tracked in `plans.md` under "Nimbus REPL Backlog". Fm 2 and 3 are fixed.

| # | Failure mode | Fix sketch |
|---|---|---|
| 4 | pydantic-ai wraps 429 as `ModelHTTPError` — fallback handler's `except openai.RateLimitError` misses it | In the `ModelHTTPError` branch, treat `status_code == 429` as a rate-limit |
| 5 | Session file race — no lock on `_save_conversation` | `os.replace` with tempfile for atomic writes |
| 6 | Conversation context unbounded growth | Rolling summary after N turns with a cheap model |
| 7 | Prompt injection via tool results | Tighter sandbox; strip/escape control tokens in tool output |
| 8 | `max_upload_bytes` per-call not per-session | Session-wide byte counter in the tool wrapper |
| 9 | Listener exceptions still log to stderr | Route through `structlog`, scrub secrets |
| 10 | No per-user rate limiting | Token bucket keyed by user_id; prerequisite for Slack/Discord frontend |

## Design stance from this chat

- Failures are not edge cases; they are the default case to design around.
- Retries, timeouts, idempotency, backpressure, and overload handling are part of the system, not polish to add later.
- Use modern tooling and concepts when they earn their keep; do not add fancy machinery just because it exists.
- Optimize for low cognitive load, deep modules, shallow interfaces, and long-term changeability.
- Treat observable behavior as API surface: env vars, session files, CLI output, response schemas, error text, ordering, and defaults all matter.
- HW3 architecture should keep the storage vertical stable while cleanly integrating the AI vertical and at least one external vertical.
- Channel adapters such as Slack should stay thin; shared runtime/tool/integration logic should live behind reusable boundaries.
- MCP is still the likely direction for future capability exposure, but only if host/client/server roles, auth, transport, and failure handling are made explicit.

## Immediate HW3 target

- Today is `2026-04-21`; the second submission is due `2026-04-22`.
- The immediate deliverable is not more local AI plumbing. It is a convincing integrated system:
  - AI provider works
  - at least one cross-vertical integration works through the shared API
  - integration tests prove the pieces work together
  - deployment/IaC/telemetry are at least on a credible path, ideally already working

## Resume here next chat

- Re-read `AGENTS.md` and this file first.
- Re-read `NIMBUS_HW3_SYSTEM_DESIGN.md` for the current Slack-first system design and backlog.
- Preserve any existing Python worktree changes in `router.py` and `sessions.py`; do not overwrite them casually.
- If continuing design work, ground it in the official HW3 text above, not guesses from earlier homework patterns.
- If continuing implementation, prioritize the second-submission path: cross-vertical integration, thin frontend adapter shape, and telemetry/operational readiness.
- Keep the central architecture question in view: where should shared agent/runtime logic live so CLI, Slack, and future adapters do not duplicate business logic?

## Workflow conventions the user has set

- No git worktrees right now — work directly on `hw-3`.
- Any git commands EXCEPT `push`.
- Squash related work into one commit; don't leave a string of micro-commits.
- Don't create new `.md` files unless asked (this file is an exception — they asked for session state).
- Keep responses concise; no trailing summaries.
- Default models should be non-Venice.

## Useful commands

```bash
# Run the REPL (auto-loads credentials.env):
uv run --package openrouter-ai-client-impl nimbus

# Test suite:
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q

# Lint just the production package:
uv run --package openrouter-ai-client-impl ruff check src/openrouter_ai_client_impl/openrouter_ai_client_impl/

# List free OpenRouter models that support tool calls:
curl -s "https://openrouter.ai/api/v1/models?supported_parameters=tools" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  | jq -r '.data[] | select(.id|endswith(":free")) | .id'
```

## Gotchas

- `credentials.env` in the repo root has `OPENROUTER_MODEL` and `OPENROUTER_FALLBACK_MODEL` — if set, they override the new defaults in `config.py`. Check there first if the banner shows the wrong model.
- `benchmark_results.json` is a run artifact, not committed.
- DeepSeek has **no free-tier** on OpenRouter as of this writing.
