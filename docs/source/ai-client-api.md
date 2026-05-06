# AI Client API Reference

This page documents the public surface of `ai_client_api` and
`openrouter_ai_client_impl`. Anything not listed here is an
implementation detail and may change without notice.

## `ai_client_api`

Provider-agnostic abstract layer. Consumers import from this package
only.

### Classes

- **`AIClient`** — ABC with two abstract methods:
  - `send_message(conversation, *, tools, max_steps=5, dry_run=False) -> AIResponse`
  - `ping() -> bool`

  Plus the concrete observer helper:
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

- **`AgentEvent`** — `kind` (one of the strings above) and a free-form
  `payload: Mapping[str, object]`.

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
from openrouter_ai_client_impl.config import OpenRouterConfig

config = OpenRouterConfig.from_env()  # reads OPENROUTER_API_KEY, etc.
```

Environment variables:

| Variable                    | Default                              | Required |
| --------------------------- | ------------------------------------ | -------- |
| `OPENROUTER_API_KEY`        | —                                    | yes      |
| `OPENROUTER_MODEL`          | `openai/gpt-oss-120b:free`           | no       |
| `OPENROUTER_FALLBACK_MODEL` | `nvidia/nemotron-3-super:free`       | no       |
| `OPENROUTER_BASE_URL`       | `https://openrouter.ai/api/v1`       | no       |
| `OPENROUTER_TIMEOUT`        | `30.0`                               | no       |

An empty `OPENROUTER_FALLBACK_MODEL` disables the fallback. An
unparseable `OPENROUTER_TIMEOUT` falls back to the default.

### `OpenRouterClient`

Implements `AIClient`. Constructor:

```python
OpenRouterClient(config, *, openai_client=None)
```

`openai_client` is optional — tests inject a fake. Production code
lets the client construct the real `openai.OpenAI` instance from
`config`.

### `get_client_impl`

```python
from openrouter_ai_client_impl.openrouter_client import get_client_impl

client = get_client_impl()  # reads env, builds an OpenRouterClient
```

Mirrors the `aws_client_impl.get_client_impl()` convention used
elsewhere in the workspace. Pass `interactive=True` to pick up
interactive-mode tweaks (currently a no-op; reserved for future
streaming UX).

### `build_cloud_storage_tools`

```python
from openrouter_ai_client_impl.cloud_storage_tools import build_cloud_storage_tools

tools = build_cloud_storage_tools(
    storage=s3_client,
    container="ospsd-team-2-tutorial",
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

### `NimbusCLI` / `nimbus`

The console-script entry point. See {doc}`ai-client-tutorial` for usage
examples. Internals live in `openrouter_ai_client_impl.cli` and are
not considered a public API — they can (and should) evolve as the
tutorial grows.
