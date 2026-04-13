"""Provider-agnostic data types shared across the AI client layer.

These types are the vocabulary the ``AIClient`` ABC exposes. Implementations
consume and produce them; domain code (tool bindings, REPL, Slack adapters)
depends only on these shapes, not on any specific provider SDK.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal


class Role(StrEnum):
    """Chat message role, matching the OpenAI/OpenRouter wire format."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class Message:
    """A single message in a conversation.

    ``tool_calls`` and ``tool_call_id`` are only populated for assistant
    messages that requested tool calls and for tool-result messages,
    respectively. Keeping them on the same shape avoids a union type at
    every call site.
    """

    role: Role
    content: str
    tool_calls: tuple[ToolCallRequest, ...] = ()
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """A request by the model to invoke a named tool with parsed arguments."""

    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCallRecord:
    """Audit record for a single tool invocation within a ``send_message`` call.

    Callers inspect these to understand what the agent did (observability, tests,
    dry-run previews). ``result_summary`` is a short human-readable preview,
    never the full result — full results may be large or sensitive.
    """

    id: str
    name: str
    arguments: Mapping[str, Any]
    result_summary: str
    success: bool
    latency_ms: int


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool exposed to the model.

    ``parameters_schema`` is a JSON Schema describing the tool's arguments; the
    model receives this verbatim and is responsible for producing conforming
    calls. ``handler`` is the Python callable invoked when the model selects
    this tool. ``handler`` must accept keyword arguments matching the schema
    and must return a value that is JSON-serializable (str, int, float, bool,
    None, list, or dict of same).
    """

    name: str
    description: str
    parameters_schema: Mapping[str, Any]
    handler: Callable[..., Any]


StopReason = Literal["end_turn", "max_steps", "tool_error", "provider_error"]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token accounting for a single ``send_message`` invocation."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        """Total tokens (input + output) consumed across all steps."""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class AIResponse:
    """Structured result of a ``send_message`` call.

    Callers that only care about the text do ``response.text``. Callers that
    need telemetry, auditing, or tests read the tool-call records, token
    counts, and latency.
    """

    text: str
    model: str
    tokens: TokenUsage
    tool_calls: tuple[ToolCallRecord, ...]
    latency_ms: int
    stop_reason: StopReason
    steps: int
    fallback_used: bool = False


@dataclass(frozen=True, slots=True)
class AgentEvent:
    """Event emitted by the agentic loop for observability.

    Subscribers register via ``AIClient.on_event``. The ``kind`` drives
    dispatch; ``payload`` is a free-form mapping whose shape depends on the
    kind (documented in the implementation).
    """

    kind: Literal[
        "request_started",
        "request_completed",
        "tool_call_started",
        "tool_call_completed",
        "model_fallback",
        "error",
    ]
    payload: Mapping[str, Any] = field(default_factory=dict)


EventListener = Callable[[AgentEvent], None]
"""Signature for functions registered via ``AIClient.on_event``."""


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Durable record of a tool invocation, shaped for later persistence.

    This is structurally similar to ``ToolCallRecord`` but pins an absolute
    timestamp and a session id, so a consumer can replay or analyze a full
    session's behavior offline. ``args`` is a shallow copy to prevent the
    record from mutating if the caller later changes the input dict.
    """

    timestamp: str
    session_id: str
    request_id: str
    tool_name: str
    args: Mapping[str, Any]
    result_summary: str
    success: bool
    latency_ms: int


def normalize_tools(tools: Sequence[Tool] | None) -> tuple[Tool, ...]:
    """Return tools as an immutable tuple, or an empty tuple if ``None``.

    Used to keep implementations consistent on how "no tools" is represented.
    """
    if tools is None:
        return ()
    return tuple(tools)
