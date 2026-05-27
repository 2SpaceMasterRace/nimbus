# AI Client API Reference

This page documents the public surface of `ai_client_api` and
`openrouter_ai_client_impl`. Anything not listed here is an
implementation detail and may change without notice.

## `ai_client_api`

Provider-agnostic abstract layer. Consumers import from this package
only.

### Classes

- **`AIClient`** — ABC with four abstract methods:
  - `send_message(prompt, *, tools=None, max_steps=5, dry_run=False, stream=False) -> AIResponse`
  - `stream_message(prompt, *, tools=None, max_steps=5, dry_run=False) -> AsyncIterator[AIStreamEvent]`
  - `ping() -> bool`
  - `on_event(listener: Callable[[AgentEvent], None]) -> None`

- **`Conversation`** — mutable dataclass holding the system prompt and
  the ordered list of `Message` objects, plus helpers:
  - `add_user(text)`, `add_assistant(text, *, tool_calls=())`,
    `add_tool_result(tool_call_id, *, result, ok=True)`
  - `to_json()` / `from_json(data)` for persistence
  - `clear()` drops all messages but keeps the system prompt
  - `trim(max_messages)` drops oldest non-system messages
  - `estimate_tokens()` for a rough budget check (4 chars ≈ 1 token)

- **`Message`**, **`ToolCallRequest`**, **`ToolCallRecord`** —
  immutable dataclasses. See `ai_client_api.models`.

- **`Tool`** — dataclass bundling `name`, `description`,
  `parameters_schema` (JSON Schema), and `handler` (a callable that
  returns a JSON-serialisable result).

- **`AIResponse`** — `text`, `steps`, `fallback_used`, `tokens`
  (`TokenUsage(input_tokens, output_tokens)`), `model`,
  `tool_calls: tuple[ToolCallRecord, ...]`.

- **`AIStreamEvent`** — provider-neutral streaming records used by Nimbus
  runtime replay. Events include provider request boundaries, text deltas,
  tool-call events, fallback notices, errors, and final completion metadata.

- **`AgentEvent`** — `kind` (one of the strings above) and a free-form
  `payload: Mapping[str, object]`.

### Factories

- **`register_client_factory(factory)`** — implementation packages call this at
  import time to register their environment-backed provider factory.
- **`get_client()`** — returns an `AIClient` from the registered factory and
  raises `AIClientConfigError` if no implementation has registered yet.

For the built-in provider:

```python
import openrouter_ai_client_impl
from ai_client_api import get_client

client = get_client()
```

The interface package owns the public factory, while implementation packages
own the concrete `get_client_impl()` functions.

### Exceptions

All inherit from `AIClientError`:

- `AIClientConfigError` — missing or malformed configuration.
- `AIAuthenticationError` — provider rejected the API key.
- `AIRateLimitError` — 429 after fallback failed.
- `AIProviderError` — any other provider-side or transport failure.
- `AITimeoutError` — request or read timeout.
- `AIStepBudgetExceededError` — the agentic loop hit `max_steps`.
- `AIToolExecutionError` — a tool handler raised unexpectedly.
- `AIToolArgsInvalidError(tool_name, msg)` — Pydantic / guard rejected
  tool arguments; carries `tool_name` for the structured error record.

## `openrouter_ai_client_impl`

### `OpenRouterConfig`

Frozen slots dataclass. Construct via `from_env()`:

```python
from openrouter_ai_client_impl import OpenRouterConfig

config = OpenRouterConfig.from_env()  # reads OPENROUTER_API_KEY, etc.
```

Environment variables:

| Variable                    | Default                              | Required |
| --------------------------- | ------------------------------------ | -------- |
| `OPENROUTER_API_KEY`        | —                                    | yes      |
| `OPENROUTER_MODEL`          | `openai/gpt-oss-120b:free`           | no       |
| `OPENROUTER_FALLBACK_MODEL` | `meta-llama/llama-3.1-8b-instruct:free` | no       |
| `OPENROUTER_BASE_URL`       | `https://openrouter.ai/api/v1`       | no       |
| `OPENROUTER_TIMEOUT`        | `60.0`                               | no       |

An empty `OPENROUTER_FALLBACK_MODEL` disables the fallback. An
unparseable `OPENROUTER_TIMEOUT` falls back to the default.

### `OpenRouterClient`

Implements `AIClient`. Constructor:

```python
OpenRouterClient(config, *, pai_model=None, pai_fallback_model=None)
```

The optional model arguments are pydantic-ai model slots. Tests inject
`FunctionModel` instances here so no real HTTP is needed; production code
leaves them unset.

### `get_client_impl`

```python
from openrouter_ai_client_impl import get_client_impl

client = get_client_impl()  # reads env, builds an OpenRouterClient
```

Mirrors the `aws_client_impl.get_client_impl()` convention used elsewhere in
the workspace. Importing `openrouter_ai_client_impl` also registers this
factory with `ai_client_api.get_client()`. Streaming is exposed through
`AIClient.stream_message()` and consumed by `nimbus_runtime`.

### `build_cloud_storage_tools`

```python
from openrouter_ai_client_impl.cloud_storage_tools import build_cloud_storage_tools

tools = build_cloud_storage_tools(
    storage=s3_client,
    container="nimbus-tutorial",
    safe_root=Path.cwd(),
    max_upload_bytes=100 * 1024 * 1024,
    require_delete_confirmation=True,
)
```

Returns a list of five `Tool` instances: `upload_file`, `download_file`,
`list_files`, `delete_file`, `get_file_info`.

Each tool's `parameters_schema` is generated via
`BaseModel.model_json_schema()` and is the exact schema the LLM sees —
no second source of truth.

### `nimbus_cli` / `nimbus`

The maintained Python-only console-script entry point lives in the separate
`nimbus_cli` package. It owns onboarding, profile storage, local in-process
runtime execution, remote `/ai/chat/turn` profiles, and explicit resume
behavior. See {doc}`nimbus/cli` for usage examples.

The legacy `openrouter_ai_client_impl.cli` module remains for compatibility
tests during the package split, but new terminal behavior belongs in
`nimbus_cli`.
