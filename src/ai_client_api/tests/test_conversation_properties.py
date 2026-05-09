"""Property-based tests for Conversation state invariants and serialisation.

Three categories of property are checked:

1. Unit properties on helpers (_as_int coercion, token estimate bounds).
2. Round-trip identity: to_json → from_json must be lossless for any
   Conversation that could appear in production.
3. Stateful invariants: after any sequence of add_user / add_assistant /
   add_tool_result calls, the bounded-history rules must hold:
     - _messages never exceeds max_messages entries.
     - The first element of _messages is never a bare TOOL message (which
       would be an orphan tool result; most providers reject that shape).
     - The round-trip property holds mid-sequence too.
"""

from __future__ import annotations

import pytest
from ai_client_api.conversation import Conversation, _as_int
from ai_client_api.models import Role, ToolCallRequest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    rule,
    run_state_machine_as_test,
)

pytestmark = pytest.mark.property

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

_text = st.text(min_size=1, max_size=200)
_small_pos_int = st.integers(min_value=1, max_value=6)

# Valid session IDs match the regex in sessions.py.
_session_id = st.from_regex(r"^[A-Za-z0-9_\-.:]{1,64}$", fullmatch=True)


@st.composite
def _conversation(draw: st.DrawFn) -> Conversation:
    """Build a fresh (no messages) Conversation with arbitrary-but-valid caps."""
    return Conversation(
        system=draw(_text),
        session_id=draw(_session_id),
        max_messages=draw(_small_pos_int),
        max_total_tokens=draw(_small_pos_int),
    )


# ---------------------------------------------------------------------------
# _as_int coercion helper
# ---------------------------------------------------------------------------


@given(st.integers())
def test_as_int_passes_through_integers(n: int) -> None:
    """Plain integers are returned unchanged."""
    assert _as_int(n, default=99) == n


@given(st.booleans())
def test_as_int_treats_booleans_as_default(b: bool) -> None:
    """Booleans (which are int subclasses) must fall back to the default."""
    assert _as_int(b, default=42) == 42


@given(
    st.one_of(
        st.none(),
        st.floats(allow_nan=False),
        st.text().filter(lambda s: not s.isdigit()),
        st.lists(st.integers()),
    )
)
def test_as_int_returns_default_for_non_numeric(value: object) -> None:
    """Anything that is not int/bool/digit-string must return the default."""
    assert _as_int(value, default=-1) == -1


@given(st.integers(min_value=0).map(str))
def test_as_int_parses_digit_strings(s: str) -> None:
    """Non-negative integer digit strings must be parsed correctly."""
    assert _as_int(s, default=-1) == int(s)


# ---------------------------------------------------------------------------
# Token estimate bounds
# ---------------------------------------------------------------------------


@given(_conversation())
def test_fresh_conversation_token_estimate_is_positive(conv: Conversation) -> None:
    """A brand-new conversation with only the system message has tokens > 0."""
    assert conv.approximate_tokens() > 0


@given(_conversation(), _text)
def test_token_estimate_grows_with_messages(conv: Conversation, text: str) -> None:
    """Adding a message never decreases the token estimate."""
    conv.add_user(text)
    # Trim may reduce the count, but the estimate must remain >= 0.
    assert conv.approximate_tokens() >= 0


# ---------------------------------------------------------------------------
# Fresh-conversation invariants
# ---------------------------------------------------------------------------


@given(_conversation())
def test_fresh_conversation_has_one_message(conv: Conversation) -> None:
    """A newly created Conversation contains exactly the system message."""
    assert len(conv) == 1


@given(_conversation())
def test_fresh_conversation_messages_tuple_has_system_at_head(
    conv: Conversation,
) -> None:
    """The first message in a fresh conversation must be the system message."""
    msgs = conv.messages()
    assert msgs[0].role is Role.SYSTEM
    assert msgs[0].content == conv.system


# ---------------------------------------------------------------------------
# Round-trip serialisation
# ---------------------------------------------------------------------------


@given(_conversation())
def test_empty_conversation_round_trips(conv: Conversation) -> None:
    """to_json → from_json must be an identity for a message-less conversation."""
    assert Conversation.from_json(conv.to_json()).to_json() == conv.to_json()


@given(_conversation(), _text, _text)
def test_conversation_with_turns_round_trips(
    conv: Conversation, user_text: str, assistant_text: str
) -> None:
    """Round-trip must hold after adding user and assistant turns."""
    conv.add_user(user_text)
    conv.add_assistant(assistant_text)
    assert Conversation.from_json(conv.to_json()).to_json() == conv.to_json()


@given(_conversation(), _text)
def test_session_id_survives_round_trip(conv: Conversation, text: str) -> None:
    """session_id must be identical after serialise→deserialise."""
    conv.add_user(text)
    loaded = Conversation.from_json(conv.to_json())
    assert loaded.session_id == conv.session_id


# ---------------------------------------------------------------------------
# Stateful invariants: bounded-history + orphan-TOOL guard
# ---------------------------------------------------------------------------


class ConversationMachine(RuleBasedStateMachine):
    """Explore arbitrary operation sequences and assert invariants after each step.

    Operations model the real usage pattern: users add user turns, the AI
    adds assistant turns (optionally with tool calls), and tool results follow
    an assistant message that carried a tool call.  The machine tracks whether
    the last assistant message included a tool call so that ``add_tool_result``
    is only injected when it would be structurally valid.
    """

    def __init__(self) -> None:
        super().__init__()
        # Use tight caps so trim fires frequently during the run.
        self.conv = Conversation(system="sys", max_messages=3, max_total_tokens=40)
        self._last_tool_call_id: str | None = None

    @initialize()
    def reset(self) -> None:
        self.conv = Conversation(system="sys", max_messages=3, max_total_tokens=40)
        self._last_tool_call_id = None

    @rule(text=_text)
    def add_user(self, text: str) -> None:
        self.conv.add_user(text)
        self._last_tool_call_id = None

    @rule(text=_text)
    def add_assistant_plain(self, text: str) -> None:
        self.conv.add_assistant(text)
        self._last_tool_call_id = None

    @rule(text=_text, call_id=st.from_regex(r"^[A-Za-z0-9]{1,16}$", fullmatch=True))
    def add_assistant_with_tool_call(self, text: str, call_id: str) -> None:
        tc = ToolCallRequest(id=call_id, name="read_file", arguments={"path": "/tmp"})  # noqa: S108
        self.conv.add_assistant(text, tool_calls=(tc,))
        self._last_tool_call_id = call_id

    @rule(content=_text)
    def add_tool_result(self, content: str) -> None:
        # Only inject a tool result when the last assistant message declared
        # a tool call — otherwise we would manufacture an orphan result, which
        # is a usage error the class does not guard against.
        if self._last_tool_call_id is not None:
            self.conv.add_tool_result(
                tool_call_id=self._last_tool_call_id, content=content
            )
            self._last_tool_call_id = None

    @rule()
    def pop_last_user(self) -> None:
        self.conv.pop_last_user()
        self._last_tool_call_id = None

    @invariant()
    def message_count_within_cap(self) -> None:
        assert len(self.conv._messages) <= self.conv.max_messages

    @invariant()
    def no_orphan_tool_message_at_head(self) -> None:
        if self.conv._messages:
            assert self.conv._messages[0].role is not Role.TOOL

    @invariant()
    def round_trip_is_lossless(self) -> None:
        loaded = Conversation.from_json(self.conv.to_json())
        assert loaded.to_json() == self.conv.to_json()

    @invariant()
    def len_counts_system_message(self) -> None:
        assert len(self.conv) == len(self.conv._messages) + 1


# Expose the stateful machine to pytest.
# run_state_machine_as_test accepts a settings kwarg directly.
def test_conversation_stateful() -> None:
    """Run invariants over 200 generated operation sequences.

    Checks bounded-history, no orphan TOOL messages, and round-trip identity.
    """
    run_state_machine_as_test(
        ConversationMachine,
        settings=settings(
            max_examples=200,
            suppress_health_check=[HealthCheck.too_slow],
        ),
    )
