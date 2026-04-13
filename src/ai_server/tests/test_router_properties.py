"""Property-based tests for ChatTurnRequest validation and rate-limit arithmetic.

Two subsystems are covered:

1. ``ChatTurnRequest`` field validation — Pydantic model with pattern-constrained
   fields.  Properties verify that inputs generated from the field regex are
   always accepted, and that the ``_decoded_base64_size`` formula matches the
   standard-library decoder for any well-formed base64 string.

2. Token-bucket rate limiter — ``_check_rate_limit`` must satisfy three
   arithmetic invariants:
     - Any principal is allowed on the first request.
     - After ``_RATE_LIMIT_CAPACITY`` consecutive requests with zero elapsed
       time, the next must be denied.
     - The bucket token count never goes below 0 or above the capacity.
"""

from __future__ import annotations

import base64

import pytest
from ai_server.router import (
    _RATE_LIMIT_CAPACITY,
    ChatTurnRequest,
    _check_rate_limit,
    _decoded_base64_size,
    _rate_buckets,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

pytestmark = pytest.mark.property

# ---------------------------------------------------------------------------
# Strategies derived from the model's own field patterns
# ---------------------------------------------------------------------------

_platform = st.from_regex(r"^[a-z][a-z0-9_-]{0,15}$", fullmatch=True)
_chat_id = st.from_regex(r"^[A-Za-z0-9_.:-]{1,64}$", fullmatch=True)
_idempotency_key = st.from_regex(r"^[A-Za-z0-9_.:-]{1,256}$", fullmatch=True)
_message_text = st.text(min_size=1, max_size=512)

# ---------------------------------------------------------------------------
# ChatTurnRequest field validation
# ---------------------------------------------------------------------------


@given(
    _platform, _chat_id, _chat_id, _chat_id, _chat_id, _message_text, _idempotency_key
)
def test_valid_fields_are_accepted_by_the_model(  # noqa: PLR0913
    platform: str,
    workspace_id: str,
    channel_id: str,
    message_id: str,
    user_id: str,
    text: str,
    idempotency_key: str,
) -> None:
    """Any input generated from the model's own field patterns must be accepted."""
    req = ChatTurnRequest(
        platform=platform,
        workspace_id=workspace_id,
        channel_id=channel_id,
        message_id=message_id,
        user_id=user_id,
        text=text,
        idempotency_key=idempotency_key,
    )
    assert req.platform == platform
    assert req.workspace_id == workspace_id


@given(st.text(alphabet="/\\ \x00!@#$%^&*()", min_size=1))
def test_platform_with_unsafe_chars_is_rejected(bad_platform: str) -> None:
    """A platform value with characters outside the pattern must be rejected."""
    with pytest.raises(ValidationError):
        ChatTurnRequest(
            platform=bad_platform,
            workspace_id="T12345",
            channel_id="C12345",
            message_id="M12345",
            user_id="U12345",
            text="hello",
            idempotency_key="key-1",
        )


@given(
    # Efficient construction: fixed 4096-char base + a small suffix so Hypothesis
    # only generates the suffix.  The resulting string always exceeds the 4096
    # character limit without triggering data_too_large health checks.
    st.text(max_size=64).map(lambda suffix: "a" * 4096 + suffix + "x")
)
def test_text_exceeding_max_length_is_rejected(long_text: str) -> None:
    """Message text over 4096 characters must be rejected."""
    with pytest.raises(ValidationError):
        ChatTurnRequest(
            platform="slack",
            workspace_id="T12345",
            channel_id="C12345",
            message_id="M12345",
            user_id="U12345",
            text=long_text,
            idempotency_key="key-1",
        )


# ---------------------------------------------------------------------------
# _decoded_base64_size formula
# ---------------------------------------------------------------------------


@given(st.binary(max_size=512).map(base64.b64encode).map(bytes.decode))
def test_decoded_size_matches_stdlib_for_standard_base64(content_base64: str) -> None:
    """_decoded_base64_size must agree with base64.b64decode for any standard payload.

    Covers multiples of 4 bytes with correct padding.
    """
    expected = len(base64.b64decode(content_base64))
    assert _decoded_base64_size(content_base64) == expected


@given(st.binary(max_size=200).map(base64.b64encode).map(bytes.decode))
def test_decoded_size_never_exceeds_encoded_length(content_base64: str) -> None:
    """The decoded byte count must always be ≤ the base64 string length."""
    assert _decoded_base64_size(content_base64) <= len(content_base64)


@given(st.binary(max_size=200).map(base64.b64encode).map(bytes.decode))
def test_decoded_size_is_non_negative(content_base64: str) -> None:
    """The decoded byte count must always be ≥ 0."""
    assert _decoded_base64_size(content_base64) >= 0


# ---------------------------------------------------------------------------
# Token-bucket rate limiter
# ---------------------------------------------------------------------------


@given(
    st.from_regex(r"^[A-Za-z0-9_.-]{4,32}$", fullmatch=True),
    st.floats(min_value=1_000.0, max_value=1_000_000.0, allow_nan=False),
)
@settings(max_examples=100)
def test_first_request_always_allowed(principal: str, now: float) -> None:
    """The very first request from any unseen principal must be permitted."""
    # Ensure a clean slate for this principal.
    _rate_buckets.pop(principal, None)
    result = _check_rate_limit(principal, _now=now)
    assert result is True
    _rate_buckets.pop(principal, None)  # clean up


@given(
    st.from_regex(r"^[A-Za-z0-9_.-]{4,32}$", fullmatch=True),
    st.floats(min_value=1_000.0, max_value=1_000_000.0, allow_nan=False),
)
@settings(max_examples=100)
def test_bucket_exhausted_after_capacity_consecutive_requests(
    principal: str, start_time: float
) -> None:
    """Exhaust the rate bucket; the next request at the same instant must be denied.

    No time has elapsed between requests so no tokens have refilled.
    """
    _rate_buckets.pop(principal, None)
    # Fire capacity requests at the same frozen clock value.
    for _ in range(int(_RATE_LIMIT_CAPACITY)):
        _check_rate_limit(principal, _now=start_time)
    # One more at the same instant must be denied.
    assert _check_rate_limit(principal, _now=start_time) is False
    _rate_buckets.pop(principal, None)


@given(st.floats(min_value=1_000.0, max_value=1_000_000.0, allow_nan=False))
@settings(max_examples=100)
def test_none_principal_is_always_allowed(now: float) -> None:
    """A ``None`` principal must always be allowed regardless of clock state."""
    assert _check_rate_limit(None, _now=now) is True


@given(
    st.from_regex(r"^[A-Za-z0-9_.-]{4,32}$", fullmatch=True),
    st.floats(min_value=1_000.0, max_value=1_000_000.0, allow_nan=False),
    st.floats(min_value=1.0, max_value=10.0, allow_nan=False),
)
@settings(max_examples=100)
def test_token_count_stays_within_bounds_after_refill(
    principal: str, start_time: float, elapsed: float
) -> None:
    """After any sequence of requests and elapsed time, the token count stays bounded.

    The bucket must remain in [0, _RATE_LIMIT_CAPACITY] at all times.
    """
    _rate_buckets.pop(principal, None)
    # Drain the bucket at time=start.
    for _ in range(int(_RATE_LIMIT_CAPACITY) + 2):
        _check_rate_limit(principal, _now=start_time)
    # Check at a later time (simulates refill).
    _check_rate_limit(principal, _now=start_time + elapsed)
    bucket = _rate_buckets.get(principal)
    if bucket is not None:
        assert 0.0 <= bucket.tokens <= float(_RATE_LIMIT_CAPACITY)
    _rate_buckets.pop(principal, None)
