# ai-client-api

Provider-agnostic contract for AI chat clients with tool-calling support.

This package defines the `AIClient` abstract base class and the shared value-type
vocabulary (`Tool`, `AIResponse`, `Conversation`, `Message`, exception hierarchy,
event types). It has **no provider-specific code**. Callers program against this
package only; concrete implementations (e.g. `openrouter-ai-client-impl`) depend
on it, never the other way around.

---

## Installation

This package ships as part of the Nimbus workspace. Standalone usage:

```bash
pip install ai-client-api
```

---

## Public surface

```python
from ai_client_api import (
    AIClient,           # abstract base class
    Conversation,       # bounded multi-turn history
    Tool,               # tool binding for the LLM
    AIResponse,         # terminal response from send_message
    TokenUsage,         # input/output token counters
    ToolCallRecord,     # per-call audit record
    AgentEvent,         # lifecycle event emitted during a loop
    EventListener,      # type alias for the event callback
    StopReason,         # "end_turn" | "tool_calls" | "max_tokens"
    Role,               # USER | ASSISTANT | SYSTEM | TOOL
    Message,            # single conversation turn
    # Exceptions
    AIClientError,
    AIClientConfigError,
    AIAuthenticationError,
    AIRateLimitError,
    AIProviderError,
    AITimeoutError,
    AIStepBudgetExceededError,
    AIToolArgsInvalidError,
    AIToolExecutionError,
    AIUnknownToolError,
    # Utilities
    normalize_tools,
)
```

---

## Core concepts

### `AIClient` — the contract

Every provider implementation subclasses `AIClient` and implements two abstract
methods:

| Method | Purpose |
|---|---|
| `send_message(prompt, *, tools, max_steps, dry_run, stream)` | Run the agentic loop and return a final `AIResponse` |
| `ping()` | Probe the provider for reachability; never raises |
| `on_event(listener)` | Register an `EventListener` for lifecycle events |

**`send_message` contract:**
- `prompt` is either a plain `str` (one-shot) or a `Conversation` (multi-turn).
  When a `Conversation` is passed, it is **mutated in place** with the new exchange.
- `tools` is a sequence of `Tool` objects the model may call. Pass `None` or `[]`
  for a plain-text response.
- `max_steps` limits the number of LLM calls in the tool-calling loop. Exceeding
  it raises `AIStepBudgetExceededError`.
- `dry_run=True` records tool invocations but does not execute handlers.
- Returns an `AIResponse` with `.text`, `.tokens`, `.tool_calls`, `.model`,
  `.steps`, `.latency_ms`, `.fallback_used`.

**Exception contract:**
| Exception | When |
|---|---|
| `AIAuthenticationError` | Provider rejected credentials (401) |
| `AIRateLimitError` | Provider rate-limited the caller (429) |
| `AIProviderError` | Other non-auth, non-rate-limit provider error |
| `AITimeoutError` | Request exceeded the configured timeout |
| `AIStepBudgetExceededError` | Loop exceeded `max_steps` |
| `AIToolArgsInvalidError` | Model produced args that failed schema validation |
| `AIToolExecutionError` | Tool handler raised (implementations MAY raise or feed back) |
| `AIUnknownToolError` | Model called a tool not in the `tools` list |

All exceptions derive from `AIClientError` for a single broad catch.

---

### `Conversation` — bounded history

```python
from ai_client_api import Conversation

conv = Conversation(
    system="You are a helpful assistant.",
    session_id="my-channel",
    max_messages=20,       # drop oldest non-system messages above this count
    max_total_tokens=8000, # rough token cap (4 chars ≈ 1 token)
)

conv.add_user("Hello!")
conv.add_assistant("Hi there!", tool_calls=())
conv.add_tool_result(tool_call_id="tc-1", content='{"ok": true}', name="do_thing")

# Pop the last user message (rollback on failed request):
conv.pop_last_user()

# Persist / restore:
data = conv.to_json()           # returns a JSON-serializable dict
conv2 = Conversation.from_json(data)
```

**Trimming:** when either cap is exceeded, the oldest non-system messages are
dropped in pairs that preserve the tool-call/tool-result structure. The system
message is always retained.

**Thread safety:** `Conversation` is not thread-safe. Use one instance per
in-flight `send_message` call.

---

### `Tool` — tool binding

```python
from ai_client_api import Tool

def my_handler(*, city: str) -> dict:
    return {"weather": "sunny", "city": city}

tool = Tool(
    name="get_weather",
    description="Fetch current weather for a city.",
    parameters_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    handler=my_handler,
)
```

The `handler` is called with keyword arguments matching the JSON schema. It must
return a JSON-serializable value or raise `AIToolArgsInvalidError` /
`AIToolExecutionError`.

---

### Events — `on_event` / `emit`

```python
from ai_client_api import AgentEvent

def my_listener(event: AgentEvent) -> None:
    print(event.kind, event.payload)

client.on_event(my_listener)
```

Event kinds emitted by the reference implementation:

| Kind | When |
|---|---|
| `request_started` | `send_message` called |
| `tool_call_started` | Model invoked a tool |
| `tool_call_completed` | Tool handler returned or raised |
| `model_fallback` | Primary model failed; retrying with fallback |
| `request_completed` | Final response produced |

Listener exceptions are caught and logged; they never interrupt the loop.

---

## Failure-mode guidance

| Failure | Caller action |
|---|---|
| `AIRateLimitError` | Back off and retry; implementations should try the fallback model first |
| `AIAuthenticationError` | Fix credentials; do not retry |
| `AITimeoutError` | Retry with exponential back-off |
| `AIStepBudgetExceededError` | Simplify the request or raise `max_steps` |
| `AIProviderError` | Log and surface to the user; implementation may auto-fallback |

---

## Design notes

- The package has **no external dependencies** beyond the standard library. Heavy
  SDKs (openai, pydantic-ai) live in implementation packages only.
- `normalize_tools` coerces `None`, `[]`, or `Sequence[Tool]` to
  `tuple[Tool, ...]` for consistent downstream handling.
- The `pop_last_user()` method supports optimistic-mutation patterns: append the
  user message before calling `send_message`, roll it back on failure.
