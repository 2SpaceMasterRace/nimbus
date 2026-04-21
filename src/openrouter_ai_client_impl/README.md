# openrouter-ai-client-impl

OpenRouter-backed implementation of the `ai-client-api` contract, powered by
**pydantic-ai**. Includes the `nimbus` CLI/REPL and cloud-storage tool bindings.

---

## Features

- **`OpenRouterClient`** — implements `AIClient` via pydantic-ai's `Agent.run_sync`.
- **Primary → fallback model switching** — transparent retry on 429 or 5xx, with
  an observable `model_fallback` event so the CLI can display a `↻` indicator.
- **Cloud-storage tools** — five LLM-callable tools (`upload_file`,
  `download_file`, `list_files`, `delete_file`, `get_file_info`) with Pydantic
  argument validation, a pinned container, and a `safe_root` path sandbox.
- **Nimbus REPL** — Rich-powered interactive CLI with slash commands, tool-call
  rendering, session persistence, and debug tooling.
- **Structured events** — `request_started`, `tool_call_started/completed`,
  `model_fallback`, `request_completed` emitted to registered listeners.

---

## Quick start

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
# Optional — defaults are set in config.py
export OPENROUTER_MODEL="z-ai/glm-4.5-air:free"
export OPENROUTER_FALLBACK_MODEL="nousresearch/hermes-3-llama-3.1-405b:free"

# Start the REPL (auto-loads credentials.env if present):
uv run nimbus

# Or without cloud-storage tools:
uv run nimbus --no-tools
```

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | **Yes** | — | OpenRouter API key |
| `OPENROUTER_MODEL` | No | `z-ai/glm-4.5-air:free` | Primary model ID |
| `OPENROUTER_FALLBACK_MODEL` | No | `nousresearch/hermes-3-llama-3.1-405b:free` | Fallback on 429/5xx |
| `OPENROUTER_BASE_URL` | No | `https://openrouter.ai/api/v1` | Override for testing |
| `OPENROUTER_TIMEOUT` | No | `120.0` | Request timeout (seconds) |
| `OPENROUTER_MAX_STEPS` | No | `8` | Default agentic loop step budget |
| `OPENROUTER_APP_REFERER` | No | — | `HTTP-Referer` header for OpenRouter attribution |
| `OPENROUTER_APP_TITLE` | No | — | `X-Title` header for OpenRouter attribution |
| `NIMBUS_CONTAINER` | No | `$AWS_BUCKET_NAME` | S3 bucket the LLM tools are pinned to |
| `NIMBUS_SAFE_ROOT` | No | `$PWD` | Local directory the LLM may read/write |
| `NIMBUS_SESSION_DIR` | No | `~/.nimbus/sessions` | Conversation persistence directory |

> **Note:** if `credentials.env` or `.env` exists in the current directory or
> any parent, it is loaded automatically at startup (via `python-dotenv`).
> Shell-exported variables always win.

---

## Programmatic usage

```python
from openrouter_ai_client_impl import OpenRouterClient, OpenRouterConfig
from ai_client_api import Conversation, Tool

config = OpenRouterConfig.from_env()
client = OpenRouterClient(config)

# One-shot prompt
response = client.send_message("Summarise the Nimbus project in one sentence.")
print(response.text)

# Multi-turn conversation
conv = Conversation(system="You are a helpful assistant.")
conv.add_user("List three AWS S3 best practices.")
response = client.send_message(conv)
print(response.text, "—", response.model, f"({response.tokens.total} tokens)")
```

### Attaching tools

```python
from openrouter_ai_client_impl.cloud_storage_tools import build_cloud_storage_tools
from aws_client_impl import get_client_impl

storage = get_client_impl()
tools = build_cloud_storage_tools(
    storage=storage,
    container="my-bucket",
    safe_root=Path("/home/user/workspace"),
    max_upload_bytes=10 * 1024 * 1024,    # 10 MB per-file cap
    session_max_upload_bytes=50 * 1024 * 1024,  # 50 MB session cap (FM8)
)
response = client.send_message("List the files in the bucket.", tools=tools)
```

### Event listeners

```python
from ai_client_api import AgentEvent

def on_event(event: AgentEvent) -> None:
    if event.kind == "tool_call_started":
        print(f"  → {event.payload['name']}({event.payload['arguments']})")
    elif event.kind == "model_fallback":
        print(f"  ↻ fallback: {event.payload['from_model']} → {event.payload['to_model']}")

client.on_event(on_event)
```

---

## REPL slash commands

| Command | Description |
|---|---|
| `/help` | Show this list |
| `/model [name]` | Show or change the primary model |
| `/fallback [name\|none]` | Show or change the fallback model |
| `/models` | List curated free-tier models (non-Venice upstreams) |
| `/steps [n]` | Show or change the per-request step budget |
| `/clear` | Wipe conversation history (keeps system prompt) |
| `/history` | Dump the current conversation as JSON |
| `/session <id>` | Switch to a different persisted session |
| `/cost` | Cumulative tokens used this session |
| `/dry-run on\|off` | Toggle dry-run (tools logged, not executed) |
| `/debug [on\|off]` | Print the last few raw provider responses |
| `/quit` | Exit the REPL |

---

## Failure modes and mitigations

| # | Failure | Status | Mitigation |
|---|---|---|---|
| FM4 | pydantic-ai wraps 429 as `ModelHTTPError` | **Fixed** | Explicit `status == 429` check in both `_run_with_fallback` and `_try_fallback` |
| FM5 | CLI session race — no lock on `_save_conversation` | **Fixed** | Write to `.tmp` then `os.replace()` (atomic on POSIX) |
| FM7 | Prompt injection via tool results | **Fixed** | Strip C0 control chars in `_sandbox_result` before wrapping |
| FM8 | `max_upload_bytes` per-call not per-session | **Fixed** | `session_max_upload_bytes` counter in `build_cloud_storage_tools` |
| FM6 | Conversation context unbounded growth | Partial | `max_messages` / `max_total_tokens` caps in `Conversation` trim oldest turns. Full rolling summary is V2. |
| FM9 | Listener exceptions log to stderr | **Fixed** | `emit()` catches exceptions and routes through `structlog` |
| FM10 | No per-user rate limiting | **Fixed (ai_server)** | Token bucket keyed by `user_id` in `ai_server/router.py` |

---

## Architecture notes

- `Agent` is constructed fresh on every `send_message` call — tools, system
  prompt, and model are always in sync with the current config. The overhead
  is negligible; the expensive part is the HTTP round-trip.
- Fallback is implemented manually (not via pydantic-ai's `FallbackModel`) so
  we can emit a `model_fallback` event and control which errors trigger it.
- Tool results are sandboxed before being fed back to the model: control
  characters stripped, content truncated to 4 000 chars, wrapped in
  `<tool_result source="untrusted">` tags.
- The `container` and `safe_root` for cloud tools are **pinned at bind time**;
  the LLM cannot redirect them via prompt injection.

---

## Running tests

```bash
# Unit tests (no credentials needed):
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q

# End-to-end tests (real OpenRouter key required):
export OPENROUTER_API_KEY="sk-or-v1-..."
uv run pytest -m e2e src/openrouter_ai_client_impl/tests/ -v
```

---

## Benchmark script

```bash
# Score free-tier models across 5 tasks (retry-on-429, per-model diagnostics):
uv run --package openrouter-ai-client-impl python scripts/benchmark_models.py
```

Results are written to `scripts/benchmark_results.json` (not committed).

---

## Free-tier reality check

- OpenRouter's `free-models-per-day` cap is **global across all `:free` models**
  on a single account. Two benchmark runs can exhaust it for the day. $10 in
  credits unlocks 1 000 requests/day.
- Venice upstream (used by some `meta-llama` and `qwen` free models) has an
  **8 RPM shared cap** — unreliable under load. Default models route to Novita /
  DeepInfra instead.
- Check `credentials.env` first if the banner shows the wrong model — env vars
  there override the defaults in `config.py`.
