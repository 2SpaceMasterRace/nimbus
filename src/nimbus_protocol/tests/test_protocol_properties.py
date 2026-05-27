"""Property-based tests for Nimbus protocol boundary contracts."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nimbus_protocol import (
    NimbusError,
    NimbusErrorCategory,
    NimbusErrorCode,
    event_from_mapping,
)

pytestmark = pytest.mark.property

_TEXT = st.text(min_size=1, max_size=80)
_OPTIONAL_TEXT = st.one_of(st.none(), st.text(max_size=80))
_JSON_SCALAR = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.text(max_size=80),
)
_JSON_OBJECT = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=_JSON_SCALAR,
    max_size=8,
)


@given(
    session_id=_TEXT,
    sequence=st.integers(min_value=0, max_value=2**31 - 1),
    event_id=_TEXT,
    event_type=_TEXT,
    payload=_JSON_OBJECT,
    turn_id=_OPTIONAL_TEXT,
    created_at=_OPTIONAL_TEXT,
)
def test_event_from_mapping_preserves_valid_json_like_event(
    session_id: str,
    sequence: int,
    event_id: str,
    event_type: str,
    payload: dict[str, object],
    turn_id: str | None,
    created_at: str | None,
) -> None:
    """Valid JSON-like event mappings must decode without losing fields."""
    raw = {
        "session_id": session_id,
        "sequence": sequence,
        "event_id": event_id,
        "event_type": event_type,
        "payload": payload,
        "turn_id": turn_id,
        "created_at": created_at,
    }

    event = event_from_mapping(raw)

    assert event.session_id == session_id
    assert event.sequence == sequence
    assert event.event_id == event_id
    assert event.event_type == event_type
    assert event.payload == payload
    assert event.payload is not payload
    assert event.turn_id == turn_id
    assert event.created_at == created_at


@given(_JSON_OBJECT)
def test_event_from_mapping_copies_payload_mapping(payload: dict[str, object]) -> None:
    """Decoded event payloads must not alias caller-owned mutable dictionaries."""
    raw: dict[str, object] = {
        "session_id": "sess-1",
        "sequence": 1,
        "event_id": "evt-1",
        "event_type": "text.delta",
        "payload": payload,
    }

    event = event_from_mapping(raw)
    payload["mutated_after_decode"] = "caller-change"

    assert "mutated_after_decode" not in event.payload


@given(
    bad_sequence=st.one_of(
        st.booleans(),
        st.none(),
        st.floats(allow_nan=False),
        st.text(),
        st.lists(_JSON_SCALAR, max_size=4),
        _JSON_OBJECT,
    )
)
def test_event_from_mapping_rejects_non_integer_sequence(
    bad_sequence: object,
) -> None:
    """Replay sequence cursors must reject booleans and non-integer values."""
    with pytest.raises(TypeError, match="sequence"):
        event_from_mapping(
            {
                "session_id": "sess-1",
                "sequence": bad_sequence,
                "event_id": "evt-1",
                "event_type": "text.delta",
            }
        )


@given(
    code=st.sampled_from(tuple(NimbusErrorCode)),
    category=st.sampled_from(tuple(NimbusErrorCategory)),
    internal_message=_TEXT,
    display_message=_TEXT,
    retryable=st.booleans(),
    correlation_id=_OPTIONAL_TEXT,
    http_status=st.one_of(st.none(), st.integers(min_value=100, max_value=599)),
    next_action=_OPTIONAL_TEXT,
    details=_JSON_OBJECT,
)
def test_error_protocol_view_is_redacted_and_stable(
    code: NimbusErrorCode,
    category: NimbusErrorCategory,
    internal_message: str,
    display_message: str,
    retryable: bool,
    correlation_id: str | None,
    http_status: int | None,
    next_action: str | None,
    details: dict[str, object],
) -> None:
    """Protocol errors expose only the redacted client-safe fields."""
    error = NimbusError(
        code=code,
        category=category,
        message=internal_message,
        display_message=display_message,
        retryable=retryable,
        correlation_id=correlation_id,
        http_status=http_status,
        next_action=next_action,
        details=details,
    )

    assert error.to_protocol() == {
        "code": code.value,
        "category": category.value,
        "message": display_message,
        "retryable": retryable,
        "correlation_id": correlation_id,
    }
    assert "details" not in error.to_protocol()
    assert "http_status" not in error.to_protocol()
    assert "next_action" not in error.to_protocol()
    assert error.to_internal()["details"] == details
    assert error.to_internal()["details"] is not details


@given(st.sampled_from(tuple(NimbusErrorCategory)))
def test_every_error_category_has_display_title(
    category: NimbusErrorCategory,
) -> None:
    """Every public error category must have a non-empty display title."""
    error = NimbusError(
        code=NimbusErrorCode.INTERNAL,
        category=category,
        message="internal detail",
        display_message="User-safe message.",
        retryable=False,
    )

    assert error.to_display().title
    assert error.to_display().message == "User-safe message."
