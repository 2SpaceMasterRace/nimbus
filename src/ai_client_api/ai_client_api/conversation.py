"""In-process conversation state with bounded history.

The OpenAI/OpenRouter API is stateless: the caller resends the full message
list every turn. A long-running REPL or chatbot therefore needs a local cap
on history to prevent unbounded token growth.

``Conversation`` enforces two caps:

* ``max_messages``: drop oldest non-system messages when the count exceeds
  the limit.
* ``max_total_tokens``: drop oldest non-system messages when the crude
  token estimate exceeds the limit.

The system message is always pinned at the head. When trimming, we drop
messages in pairs that preserve a valid tool-call / tool-result structure —
dropping a lone ``tool_calls`` message without its matching ``tool`` result
would produce a 400 from most providers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ai_client_api.models import Message, Role, ToolCallRequest

if TYPE_CHECKING:
    from collections.abc import Iterator

# Rough approximation: OpenAI tokens are typically ~4 characters on average.
# We use a crude character count instead of importing ``tiktoken`` because it
# keeps this package free of heavy deps and is accurate enough for a safety
# cap — we only need to know roughly when we are getting large.
_CHARS_PER_TOKEN = 4

# Persistence schema version; bump if the on-disk format ever changes.
_SCHEMA_VERSION = 1


@dataclass
class Conversation:
    """Ordered list of messages with bounded history.

    Not thread-safe: one ``Conversation`` per in-flight ``send_message`` call.
    Callers persisting conversations across processes should use
    :func:`to_json` / :func:`from_json`.
    """

    system: str
    session_id: str = "default"
    max_messages: int = 20
    max_total_tokens: int = 8000
    _messages: list[Message] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate the caps are sane."""
        if self.max_messages < 1:
            msg = "max_messages must be >= 1"
            raise ValueError(msg)
        if self.max_total_tokens < 1:
            msg = "max_total_tokens must be >= 1"
            raise ValueError(msg)

    def add_user(self, text: str) -> None:
        """Append a user message and trim if necessary."""
        self._append(Message(role=Role.USER, content=text))

    def add_assistant(
        self,
        text: str,
        tool_calls: tuple[ToolCallRequest, ...] = (),
    ) -> None:
        """Append an assistant message and trim if necessary."""
        self._append(Message(role=Role.ASSISTANT, content=text, tool_calls=tool_calls))

    def add_tool_result(
        self,
        tool_call_id: str,
        content: str,
        name: str | None = None,
    ) -> None:
        """Append a tool-result message and trim if necessary."""
        self._append(
            Message(
                role=Role.TOOL,
                content=content,
                tool_call_id=tool_call_id,
                name=name,
            )
        )

    def messages(self) -> tuple[Message, ...]:
        """Return all messages including the pinned system prompt."""
        return (Message(role=Role.SYSTEM, content=self.system), *self._messages)

    def pop_last_user(self) -> bool:
        """Remove the most-recently appended user message.

        Used by callers that append a user turn optimistically and need to
        roll back when the downstream call fails — otherwise the failed
        message lingers and gets re-sent on the next successful request.

        Returns:
            ``True`` if a user message was found and removed; ``False`` if
            the message buffer was empty or the last message was not a user
            message.

        """
        for i in range(len(self._messages) - 1, -1, -1):
            if self._messages[i].role is Role.USER:
                self._messages.pop(i)
                return True
        return False

    def clear(self) -> None:
        """Drop all non-system messages."""
        self._messages.clear()

    def approximate_tokens(self) -> int:
        """Return a conservative token estimate for the full message list."""
        return sum(self._estimate(m) for m in self.messages())

    def __len__(self) -> int:
        """Return the message count including the pinned system prompt."""
        return len(self._messages) + 1

    def __iter__(self) -> Iterator[Message]:
        """Iterate over all messages including the system prompt."""
        return iter(self.messages())

    # Persistence

    def to_json(self) -> dict[str, object]:
        """Return a JSON-serializable representation of this conversation."""
        return {
            "schema_version": _SCHEMA_VERSION,
            "system": self.system,
            "session_id": self.session_id,
            "max_messages": self.max_messages,
            "max_total_tokens": self.max_total_tokens,
            "messages": [_message_to_dict(m) for m in self._messages],
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> Conversation:
        """Reconstruct a conversation from a mapping produced by ``to_json``."""
        version = data.get("schema_version")
        if version != _SCHEMA_VERSION:
            msg = f"Unsupported conversation schema_version: {version!r}"
            raise ValueError(msg)
        messages_raw = data.get("messages", [])
        if not isinstance(messages_raw, list):
            msg = "'messages' must be a list"
            raise TypeError(msg)
        conv = cls(
            system=str(data.get("system", "")),
            session_id=str(data.get("session_id", "default")),
            max_messages=_as_int(data.get("max_messages"), default=20),
            max_total_tokens=_as_int(data.get("max_total_tokens"), default=8000),
        )
        conv._messages = [_message_from_dict(m) for m in messages_raw]
        return conv

    # Internals

    def _append(self, message: Message) -> None:
        self._messages.append(message)
        self._trim()

    def _trim(self) -> None:
        # Drop oldest non-system messages until both caps are satisfied. We
        # drop one at a time but skip orphan tool-result messages that would
        # otherwise leave a dangling tool_calls block at the head.
        while self._over_cap() and self._messages:
            self._messages.pop(0)
            # If the new head is a tool result without its preceding assistant
            # message, drop it too — providers reject tool messages without a
            # preceding assistant message containing the matching tool_call.
            while self._messages and self._messages[0].role is Role.TOOL:
                self._messages.pop(0)

    def _over_cap(self) -> bool:
        if len(self._messages) > self.max_messages:
            return True
        return self.approximate_tokens() > self.max_total_tokens

    @staticmethod
    def _estimate(message: Message) -> int:
        base = max(1, len(message.content) // _CHARS_PER_TOKEN)
        # Add a small constant for role and formatting overhead.
        overhead = 4
        for call in message.tool_calls:
            base += max(1, len(call.name) // _CHARS_PER_TOKEN)
            arg_chars = sum(len(str(v)) for v in call.arguments.values())
            base += max(1, arg_chars // _CHARS_PER_TOKEN)
        return base + overhead


def _as_int(value: object, *, default: int) -> int:
    """Coerce a JSON-decoded value to ``int`` or fall back to ``default``."""
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _message_to_dict(m: Message) -> dict[str, object]:
    return {
        "role": m.role.value,
        "content": m.content,
        "tool_calls": [
            {"id": tc.id, "name": tc.name, "arguments": dict(tc.arguments)}
            for tc in m.tool_calls
        ],
        "tool_call_id": m.tool_call_id,
        "name": m.name,
    }


def _message_from_dict(data: object) -> Message:
    if not isinstance(data, dict):
        msg = "message entry must be a dict"
        raise TypeError(msg)
    tool_calls_raw = data.get("tool_calls", []) or []
    if not isinstance(tool_calls_raw, list):
        msg = "'tool_calls' must be a list"
        raise TypeError(msg)
    tool_calls: tuple[ToolCallRequest, ...] = tuple(
        ToolCallRequest(
            id=str(tc["id"]),
            name=str(tc["name"]),
            arguments=dict(tc.get("arguments", {})),
        )
        for tc in tool_calls_raw
    )
    return Message(
        role=Role(str(data["role"])),
        content=str(data.get("content", "")),
        tool_calls=tool_calls,
        tool_call_id=(
            str(data["tool_call_id"]) if data.get("tool_call_id") is not None else None
        ),
        name=(str(data["name"]) if data.get("name") is not None else None),
    )
