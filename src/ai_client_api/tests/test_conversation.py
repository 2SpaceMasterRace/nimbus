"""Tests for ``Conversation`` state and trimming invariants."""

from __future__ import annotations

import pytest

from ai_client_api import Conversation, Message, Role, ToolCallRequest

pytestmark = pytest.mark.unit

MAX_MESSAGES_TRIM = 3
SYSTEM_PLUS_TRIMMED = 4
MAX_TOKENS_TINY = 20
TOKEN_SLACK = 10
TEN_MESSAGES = 10
MAX_MESSAGES_ROUNDTRIP = 7
MAX_TOKENS_ROUNDTRIP = 999


def test_conversation_pins_system_message() -> None:
    """System message must always sit at the head regardless of history."""
    conv = Conversation(system="you are nimbus", max_messages=2)
    conv.add_user("hi")
    conv.add_assistant("hello")
    conv.add_user("are you there?")

    messages = conv.messages()
    assert messages[0].role is Role.SYSTEM
    assert messages[0].content == "you are nimbus"


def test_conversation_respects_max_messages() -> None:
    """Excess messages trim from the oldest non-system message onward."""
    conv = Conversation(system="sys", max_messages=MAX_MESSAGES_TRIM)
    conv.add_user("u1")
    conv.add_assistant("a1")
    conv.add_user("u2")
    conv.add_assistant("a2")
    conv.add_user("u3")

    # System + at most MAX_MESSAGES_TRIM non-system messages.
    assert len(conv) == SYSTEM_PLUS_TRIMMED
    non_system = [m for m in conv.messages() if m.role is not Role.SYSTEM]
    assert non_system[-1].content == "u3"


def test_conversation_respects_max_total_tokens() -> None:
    """Trimming triggers when the token estimate exceeds the cap."""
    conv = Conversation(system="s", max_messages=1000, max_total_tokens=MAX_TOKENS_TINY)
    for _ in range(TEN_MESSAGES):
        conv.add_user("x" * 40)  # ~10 tokens each

    # At most a handful of messages should survive.
    non_system = [m for m in conv.messages() if m.role is not Role.SYSTEM]
    assert len(non_system) < TEN_MESSAGES
    assert conv.approximate_tokens() <= MAX_TOKENS_TINY + TOKEN_SLACK


def test_conversation_rejects_invalid_caps() -> None:
    """Zero or negative caps are a misconfiguration, not a runtime concern."""
    with pytest.raises(ValueError, match="max_messages"):
        Conversation(system="s", max_messages=0)
    with pytest.raises(ValueError, match="max_total_tokens"):
        Conversation(system="s", max_total_tokens=0)


def test_clear_drops_history_but_keeps_system() -> None:
    """``clear`` resets the conversation to just the system prompt."""
    conv = Conversation(system="sys")
    conv.add_user("u")
    conv.add_assistant("a")
    conv.clear()

    assert len(conv) == 1
    assert conv.messages()[0].role is Role.SYSTEM


def test_roundtrip_to_json_and_back() -> None:
    """Conversations survive JSON serialization without losing tool calls."""
    conv = Conversation(
        system="you are nimbus",
        session_id="abc",
        max_messages=MAX_MESSAGES_ROUNDTRIP,
        max_total_tokens=MAX_TOKENS_ROUNDTRIP,
    )
    conv.add_user("upload hello")
    conv.add_assistant(
        "",
        tool_calls=(
            ToolCallRequest(
                id="call_1",
                name="upload_file",
                arguments={"remote_path": "greetings/hello.txt"},
            ),
        ),
    )
    conv.add_tool_result("call_1", '{"ok": true}', name="upload_file")
    conv.add_assistant("done")

    restored = Conversation.from_json(conv.to_json())

    assert restored.system == "you are nimbus"
    assert restored.session_id == "abc"
    assert restored.max_messages == MAX_MESSAGES_ROUNDTRIP
    assert restored.max_total_tokens == MAX_TOKENS_ROUNDTRIP
    assert len(restored) == len(conv)


def test_from_json_rejects_unknown_schema_version() -> None:
    """Reading a future or unknown schema_version is a hard error."""
    with pytest.raises(ValueError, match="schema_version"):
        Conversation.from_json({"schema_version": 99, "system": "s"})


def test_tool_result_orphans_dropped_during_trim() -> None:
    """A dangling tool-result message without its assistant parent is dropped."""
    conv = Conversation(system="s", max_messages=2)
    conv.add_user("u1")
    conv.add_assistant(
        "",
        tool_calls=(ToolCallRequest(id="c1", name="t", arguments={}),),
    )
    conv.add_tool_result("c1", "r1")
    conv.add_user("u2")
    conv.add_assistant("a2")

    # After trimming, no orphan tool results should remain at the head.
    first_non_system = next(m for m in conv.messages() if m.role is not Role.SYSTEM)
    assert first_non_system.role is not Role.TOOL


def test_message_is_immutable() -> None:
    """Messages must be frozen so listeners and audit records can't mutate them."""
    m = Message(role=Role.USER, content="hi")
    with pytest.raises((AttributeError, TypeError)):
        m.content = "changed"  # type: ignore[misc]
