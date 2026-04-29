# openrouter-ai-client-impl

OpenRouter-backed implementation of the provider-agnostic `ai-client-api`
contract. This package contains the concrete model transport, fallback behavior,
cloud-storage tool bindings, and the `nimbus` CLI/REPL.

## What Belongs Here

- `OpenRouterClient`: implements `AIClient`.
- `OpenRouterConfig`: loads provider settings from environment variables.
- Cloud-storage tools: binds a `CloudStorageClient` to LLM-callable operations.
- Nimbus CLI: local REPL for chat, tool use, sessions, and debugging.
- Provider failure translation: maps OpenRouter/pydantic-ai failures into
  `ai_client_api` domain exceptions.

Provider-specific code should stay in this package. Callers should type against
`ai_client_api.AIClient` unless they are configuring OpenRouter directly.

## Role

This is the concrete AI provider implementation. It turns `AIClient` calls into
OpenRouter-compatible model requests, translates provider failures into domain
errors, exposes the `nimbus` CLI, and builds storage tools for model tool
calling.

## Public API

| Entry point | Purpose |
| --- | --- |
| `OpenRouterClient` | Concrete `AIClient` implementation |
| `OpenRouterConfig.from_env()` | Load model, timeout, base URL, and API key settings |
| `get_client_impl()` | Factory used by runtime/server wiring |
| `build_cloud_storage_tools(...)` | Bind a `CloudStorageClient` as model-callable tools |
| `nimbus` | Typer CLI/REPL script |

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `ai-client-api` | Provider-neutral contract implemented by this package |
| `openai` | OpenRouter-compatible HTTP client surface |
| `pydantic-ai` | Agent/tool-call loop integration |
| `pydantic` | Tool argument and config validation |
| `python-dotenv` | Local `credentials.env` / `.env` loading |
| `typer[all]` | CLI/REPL command surface |
| `structlog` | Structured provider/runtime logging |

## Quick Start

```bash
uv sync --all-packages

export OPENROUTER_API_KEY="sk-or-v1-..."
export OPENROUTER_MODEL="z-ai/glm-4.5-air:free"
export OPENROUTER_FALLBACK_MODEL="nousresearch/hermes-3-llama-3.1-405b:free"

uv run nimbus
```

Run without storage tools:

```bash
uv run nimbus --no-tools
```

Run with storage tools pinned to a bucket and local safe root:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
export NIMBUS_CONTAINER="my-dev-bucket"
export NIMBUS_SAFE_ROOT="$PWD"

uv run nimbus
```

The CLI automatically loads `credentials.env` or `.env` from the current
directory or a parent directory. Exported shell variables take precedence.

## Programmatic Usage

```python
from ai_client_api import Conversation
from openrouter_ai_client_impl import OpenRouterClient, OpenRouterConfig

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

from aws_client_impl import get_client_impl
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
| `OPENROUTER_MODEL` | No | package default | Primary model |
| `OPENROUTER_FALLBACK_MODEL` | No | package default | Fallback model for retryable failures |
| `OPENROUTER_BASE_URL` | No | `https://openrouter.ai/api/v1` | Override for tests |
| `OPENROUTER_TIMEOUT` | No | `60.0` | Provider request timeout in seconds |
| `OPENROUTER_MAX_STEPS` | No | `8` | Default agent loop budget |
| `OPENROUTER_APP_REFERER` | No | None | Optional OpenRouter attribution header |
| `OPENROUTER_APP_TITLE` | No | None | Optional OpenRouter attribution header |
| `NIMBUS_CONTAINER` | Storage tools | `$AWS_BUCKET_NAME` | Bucket pinned to CLI tools |
| `NIMBUS_SAFE_ROOT` | No | `$PWD` | Local filesystem sandbox |
| `NIMBUS_SESSION_DIR` | No | `~/.nimbus/sessions` | CLI conversation persistence |

## CLI Commands

| Command | Description |
| --- | --- |
| `/help` | Show commands |
| `/model [name]` | Show or change primary model |
| `/fallback [name|none]` | Show or change fallback model |
| `/models` | List curated free-tier models |
| `/steps [n]` | Show or change step budget |
| `/clear` | Clear conversation history |
| `/history` | Print current conversation JSON |
| `/session <id>` | Switch persisted session |
| `/cost` | Show token totals |
| `/ping` | Probe provider reachability |
| `/status` | Show session/model/tool status |
| `/dry-run on|off` | Log tool calls without executing handlers |
| `/debug [on|off]` | Toggle provider debug output |
| `/quit` | Exit |

## Event Stream

`OpenRouterClient` emits lifecycle events through the `AIClient.on_event()`
contract:

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
| Timeout | Raises `AITimeoutError` |
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
- `docs/source/ai-client-tutorial.md`
- `docs/source/ai-client-guardrails.md`
- `src/ai_client_api/README.md`
