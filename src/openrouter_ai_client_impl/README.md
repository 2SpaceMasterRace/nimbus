# openrouter-ai-client-impl

OpenRouter-backed implementation of the provider-agnostic `ai-client-api`
contract. This package contains the concrete model transport, fallback behavior,
and cloud-storage tool bindings. The `nimbus` command now lives in the separate
`nimbus-cli` package.

## What Belongs Here

- `OpenRouterClient`: implements `AIClient`.
- `OpenRouterConfig`: loads provider settings from environment variables.
- Cloud-storage tools: binds a `CloudStorageClient` to LLM-callable operations.
- Provider token streaming: exposes `AIClient.stream_message()` events for the
  runtime and CLI.
- Provider failure translation: maps OpenRouter/pydantic-ai failures into
  `ai_client_api` domain exceptions.

Provider-specific code should stay in this package. Callers should type against
`ai_client_api.AIClient` unless they are configuring OpenRouter directly.

## Role

This is the concrete AI provider implementation. It turns `AIClient` calls into
OpenRouter-compatible model requests, translates provider failures into domain
errors, streams provider events, and builds storage tools for model tool
calling.

## Public API

| Entry point | Purpose |
| --- | --- |
| `OpenRouterClient` | Concrete `AIClient` implementation |
| `OpenRouterConfig.from_env()` | Load model, timeout, base URL, and API key settings |
| `get_client_impl()` | Factory used by runtime/server wiring |
| `build_cloud_storage_tools(...)` | Bind a `CloudStorageClient` as model-callable tools |

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `ai-client-api` | Provider-neutral contract implemented by this package |
| `openai` | OpenRouter-compatible HTTP client surface |
| `pydantic-ai` | Agent/tool-call loop integration |
| `pydantic` | Tool argument and config validation |
| `python-dotenv` | Local `credentials.env` / `.env` loading |
| `typer` | Legacy local REPL module support during the CLI package split |
| `structlog` | Structured provider/runtime logging |

## Quick Start

```bash
uv sync --all-packages

export OPENROUTER_API_KEY="sk-or-v1-..."
export OPENROUTER_MODEL="openai/gpt-oss-120b:free"
export OPENROUTER_FALLBACK_MODEL="nousresearch/hermes-3-llama-3.1-405b:free"
```

Use `uv run nimbus ...` from the `nimbus-cli` package for terminal chat.

## Programmatic Usage

```python
from ai_client_api import Conversation
from openrouter_ai_client_impl.config import OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient

client = OpenRouterClient(OpenRouterConfig.from_env())

response = client.send_message("Summarize this codebase in one sentence.")
print(response.text)

conversation = Conversation(system="You are a concise engineering assistant.")
conversation.add_user("List three S3 reliability concerns.")
response = client.send_message(conversation)
print(response.model, response.tokens.total)
```

### Storage Tools

```python
from pathlib import Path

from aws_client_impl.s3_client import get_client_impl
from openrouter_ai_client_impl.cloud_storage_tools import build_cloud_storage_tools

storage = get_client_impl()
tools = build_cloud_storage_tools(
    storage=storage,
    container="my-dev-bucket",
    safe_root=Path.cwd(),
    max_upload_bytes=10 * 1024 * 1024,
    session_max_upload_bytes=50 * 1024 * 1024,
)

response = client.send_message("List files under reports/.", tools=tools)
```

Tool safety properties:

- The container is pinned by the caller, not chosen by the model.
- Local paths are constrained by `safe_root`.
- Upload size is bounded per file and per session.
- Tool results are sanitized before being fed back to the model.

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `OPENROUTER_API_KEY` | Yes | None | OpenRouter API key |
| `OPENROUTER_MODEL` | No | `openai/gpt-oss-120b:free` | Primary model |
| `OPENROUTER_FALLBACK_MODEL` | No | package default | Fallback model for retryable failures |
| `OPENROUTER_BASE_URL` | No | `https://openrouter.ai/api/v1` | Override for tests |
| `OPENROUTER_TIMEOUT` | No | `60.0` | Provider request timeout in seconds |
| `OPENROUTER_MAX_STEPS` | No | `8` | Default agent loop budget |
| `OPENROUTER_MAX_RETRIES` | No | `3` | Total attempts on transient transport errors before falling back |
| `OPENROUTER_APP_REFERER` | No | None | Optional OpenRouter attribution header |
| `OPENROUTER_APP_TITLE` | No | None | Optional OpenRouter attribution header |
| `NIMBUS_CONTAINER` | Storage tools | `$AWS_BUCKET_NAME` | Bucket pinned to storage tools |

## Cost Estimation

Every `AIResponse` carries an optional `cost_usd_estimate: float | None`. The
client computes it from the response's token usage and a hardcoded per-model
price table in [`pricing.py`](openrouter_ai_client_impl/pricing.py), expressed
as USD per one million tokens for input and output.

- **Free-tier models** appear in the table at `(0.0, 0.0)` — the estimate is a
  legitimate `0.0`, not `None`.
- **Unknown models** return `None` so downstream code can distinguish "free"
  from "we don't have a price for this." The runtime's `nimbus.ai.cost_usd`
  histogram is silent when the estimate is `None`.
- Update the table in `pricing.py` when changing the production model roster
  or when OpenRouter publishes a price change worth tracking. Tests live in
  [`tests/test_pricing.py`](tests/test_pricing.py).

The CLI's `/cost` command renders the cumulative dollar estimate when any
priced turn has been seen, and falls back to a "free-tier; informational"
hint otherwise.

## Resilience: Transient Transport Retries

`OpenRouterClient` wraps every `agent.run_sync` call in a tenacity-backed
retry. The loop is intentionally narrow:

- **Retried**: `openai.APIConnectionError`, `openai.APITimeoutError`. These
  are transport-layer failures — the request never reached the model, or
  never produced a response — so retrying the same call is safe and
  idempotent.
- **Not retried**: `openai.AuthenticationError` (permanent), `RateLimitError`
  (retrying compounds the limit; the fallback model is a stronger response),
  HTTP 5xx (same — fallback model), `UsageLimitExceeded` (caller-controlled
  step budget).
- Budget: `OPENROUTER_MAX_RETRIES` total attempts (default 3) per model.
  After the primary exhausts its budget on a connection error, the existing
  fallback-model path engages.
- Backoff: random exponential with multiplier 0.5s and a 4-second ceiling.
  Total worst-case extra latency from retry alone is ~1.5s before the
  fallback hop.
- Each retry attempt emits a `openrouter_transient_retry` structlog warning
  so dashboards can correlate retry spikes with upstream incidents.

## Event Stream

`OpenRouterClient` exposes two event surfaces:

- `AIClient.on_event()` for lifecycle events such as tool calls and fallback.
- `AIClient.stream_message()` for provider-backed token/tool events. The final
  `request_completed` stream event carries the `AIResponse` in its payload.

```python
from ai_client_api import AgentEvent

def on_event(event: AgentEvent) -> None:
    if event.kind == "model_fallback":
        print(event.payload)

client.on_event(on_event)
```

Common event kinds include `request_started`, `tool_call_started`,
`tool_call_completed`, `model_fallback`, `request_completed`, and `error`.
Listener failures are logged and do not interrupt the request.

## Failure Model

| Failure | Behavior |
| --- | --- |
| Missing API key | Raises `AIClientConfigError` during configuration/client setup |
| Provider 401 | Raises `AIAuthenticationError` |
| Provider 429 | Tries fallback model when configured, otherwise raises `AIRateLimitError` |
| Provider 5xx | Tries fallback model when configured, otherwise raises `AIProviderError` |
| Connection error | Retried with backoff (`OPENROUTER_MAX_RETRIES`), then falls back, then `AIProviderError` |
| Timeout | Retried with backoff, then raises `AITimeoutError` (no fallback for timeouts) |
| Step budget exhausted | Raises `AIStepBudgetExceededError` |
| Tool schema mismatch | Raises or reports `AIToolArgsInvalidError` through the loop |

Fallback is implemented manually so the client can emit `model_fallback` and
preserve precise domain error mapping.

## Useful Commands

```bash
# Unit tests, no live provider required
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q

# Live OpenRouter checks
export OPENROUTER_API_KEY="sk-or-v1-..."
uv run pytest -m e2e src/openrouter_ai_client_impl/tests/ -v

# Tool-call smoke script
uv run --package openrouter-ai-client-impl python src/openrouter_ai_client_impl/scripts/smoke_tool_call.py

# Model benchmark helper
uv run --package openrouter-ai-client-impl python src/openrouter_ai_client_impl/scripts/benchmark_models.py
```

Benchmark output is written next to the benchmark script and is not meant to be
committed.

## Full Documentation

- `docs/source/ai-client-overview.md`
- `docs/source/nimbus/cli.md`
- `docs/source/ai-client-guardrails.md`
- `src/ai_client_api/README.md`
