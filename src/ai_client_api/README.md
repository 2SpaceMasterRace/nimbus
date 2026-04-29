# ai-client-api

Provider-agnostic AI client contract for Nimbus.

This package defines the stable interface and shared value types for chat,
multi-turn conversations, tool calling, usage accounting, lifecycle events,
provider token streams, and domain errors. It has no provider SDK dependencies
and contains no OpenRouter-specific logic.

Use this package when you are writing code that should work with any AI
provider. Use `openrouter-ai-client-impl` only when you need the concrete
OpenRouter implementation.

## Role

This is the AI abstraction package. It owns the public contract, shared models,
and exception hierarchy used by `nimbus_runtime` and concrete provider packages.
It must not import OpenRouter, OpenAI SDKs, FastAPI, or storage implementations.

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `pydantic` | Validation/model helpers for shared AI-facing value types |

## Design Contract

```text
application/runtime code
        |
        | depends on
        v
ai_client_api
        ^
        | implemented by
openrouter_ai_client_impl or future providers
```

The dependency direction is intentional: abstractions stay inward, provider
transports stay outward.

## Public Surface

```python
from ai_client_api import (
    AIClient,
    AIResponse,
    AIStreamEvent,
    Conversation,
    Message,
    Role,
    Tool,
    TokenUsage,
    ToolCallRecord,
    AgentEvent,
    EventListener,
    StopReason,
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
    normalize_tools,
)
```

## Implementing a Provider

A provider package subclasses `AIClient`:

```python
from collections.abc import AsyncIterator

from ai_client_api import AIClient, AIResponse, Conversation, Tool

class MyProviderClient(AIClient):
    def send_message(
        self,
        prompt: str | Conversation,
        *,
        tools: list[Tool] | None = None,
        max_steps: int | None = None,
        dry_run: bool = False,
        stream: bool = False,
    ) -> AIResponse:
        ...

    def stream_message(
        self,
        prompt: str | Conversation,
        *,
        tools: list[Tool] | None = None,
        max_steps: int = 5,
        dry_run: bool = False,
    ) -> AsyncIterator[AIStreamEvent]:
        ...

    def ping(self) -> bool:
        ...
```

Provider implementations are responsible for:

- Translating SDK/provider errors into `AIClientError` subclasses.
- Enforcing `max_steps` for tool-calling loops.
- Respecting `dry_run=True` by recording tool calls without executing handlers.
- Mutating `Conversation` only according to the conversation contract.
- Emitting useful lifecycle events through `on_event()`/`emit()`.
- Yielding provider-backed `AIStreamEvent` records from `stream_message()`,
  ending with `request_completed` and an `AIResponse` in the payload.

## Calling a Client

```python
from ai_client_api import Conversation

conversation = Conversation(
    system="You are a helpful assistant.",
    session_id="slack:T123:C456:U789",
    max_messages=40,
    max_total_tokens=8000,
)

conversation.add_user("Explain the storage API in one paragraph.")
response = client.send_message(conversation)

print(response.text)
print(response.model)
print(response.tokens.total)
```

`send_message()` accepts either a plain string or a `Conversation`. When a
`Conversation` is passed, implementations may mutate it in place with assistant
and tool messages.

## Tool Calling

```python
from ai_client_api import Tool

def get_weather(*, city: str) -> dict[str, str]:
    return {"city": city, "forecast": "sunny"}

tool = Tool(
    name="get_weather",
    description="Fetch the weather for a city.",
    parameters_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
    handler=get_weather,
)

response = client.send_message(
    "What is the weather in Amsterdam?",
    tools=[tool],
    max_steps=4,
)
```

Handlers receive validated keyword arguments and return JSON-serializable data.
Invalid model arguments should become `AIToolArgsInvalidError`; handler failures
should become `AIToolExecutionError` or be fed back through the provider loop in
a documented way.

## Conversation Model

`Conversation` is a bounded, serializable history object:

```python
from ai_client_api import Conversation

conversation = Conversation(system="You are concise.", max_messages=20)
conversation.add_user("hello")
conversation.add_assistant("hi", tool_calls=())

payload = conversation.to_json()
restored = Conversation.from_json(payload)
```

Rules:

- The system message is retained when history is trimmed.
- Old non-system messages are trimmed when message or token caps are exceeded.
- Tool-call/tool-result structure is preserved while trimming.
- `Conversation` is not thread-safe; use one in-flight request per instance.
- `pop_last_user()` exists for rollback after optimistic append failures.

## Events

```python
from ai_client_api import AgentEvent

def listener(event: AgentEvent) -> None:
    print(event.kind, event.payload)

client.on_event(listener)
```

Common event kinds are `request_started`, `tool_call_started`,
`tool_call_completed`, `model_fallback`, `request_completed`, and `error`.
Listener exceptions should be logged by implementations and must not interrupt
the model loop.

## Streaming

`stream_message()` returns an async iterator of provider-neutral
`AIStreamEvent` objects. The sequence is monotonic within one provider call;
runtime adapters allocate their own durable session sequence when events are
persisted.

```python
async for event in client.stream_message(conversation):
    if event.kind == "text_delta":
        print(event.payload["delta"], end="")
```

If the provider fails after partial output, implementations yield `error` when
possible and then raise the same `AIClientError` family used by
`send_message()`.

## Error Hierarchy

All domain exceptions derive from `AIClientError`.

| Exception | Meaning |
| --- | --- |
| `AIClientConfigError` | Local configuration is missing or invalid |
| `AIAuthenticationError` | Provider rejected credentials |
| `AIRateLimitError` | Provider rate limit was reached |
| `AIProviderError` | Provider returned a non-auth, non-rate-limit failure |
| `AITimeoutError` | Provider call exceeded the configured timeout |
| `AIStepBudgetExceededError` | Tool loop exceeded `max_steps` |
| `AIToolArgsInvalidError` | Model-produced tool args failed validation |
| `AIToolExecutionError` | Tool handler failed |
| `AIUnknownToolError` | Model called a tool not provided by the caller |

Callers can catch specific errors for UX and retry behavior, or catch
`AIClientError` for a single safe boundary.

## Tests

```bash
uv run --package ai-client-api pytest src/ai_client_api/tests/ -q
```

The tests cover the abstract contract, model defaults, serialization, trimming,
exception hierarchy, and property-style conversation invariants.

## Full Documentation

- `docs/source/ai-client-api.md`
- `docs/source/ai-client-overview.md`
- `docs/source/reference/python-api.md`
- `src/openrouter_ai_client_impl/README.md`
