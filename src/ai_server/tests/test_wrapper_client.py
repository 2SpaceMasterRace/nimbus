"""Unit tests for the Python wrapper reference helpers."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from ai_server.wrapper_client import (
    build_message_event_turn,
    build_slash_command_turn,
    encode_turn_body,
    sign_nimbus_request,
)

pytestmark = pytest.mark.unit


def test_message_event_uses_event_ts_as_thread_anchor_when_no_thread_ts() -> None:
    """Top-level Slack messages should anchor to their own timestamp."""
    body = build_message_event_turn(
        workspace_id="T123TEAM",
        event_id="evt-123",
        event={
            "channel": "C123CHAN",
            "ts": "1713840000.123456",
            "user": "U123USER",
            "text": "hello",
        },
    )

    assert body["thread_id"] == "1713840000.123456"
    assert body["message_id"] == "1713840000.123456"
    assert body["idempotency_key"] == "slack:T123TEAM:event:evt-123"


def test_message_event_uses_thread_ts_when_present() -> None:
    """Thread replies should stay anchored to the thread root timestamp."""
    body = build_message_event_turn(
        workspace_id="T123TEAM",
        event_id="evt-123",
        event={
            "channel": "C123CHAN",
            "thread_ts": "1713840000.000001",
            "ts": "1713840000.123456",
            "user": "U123USER",
            "text": "hello",
        },
    )

    assert body["thread_id"] == "1713840000.000001"
    assert body["message_id"] == "1713840000.123456"


def test_message_event_strips_one_leading_app_mention() -> None:
    """App mentions should lose the leading bot mention before reaching Nimbus."""
    body = build_message_event_turn(
        workspace_id="T123TEAM",
        event_id="evt-123",
        event={
            "channel": "C123CHAN",
            "ts": "1713840000.123456",
            "user": "U123USER",
            "text": "<@U0BOT> summarize reports/2026/april.csv",
        },
    )

    assert body["text"] == "summarize reports/2026/april.csv"


def test_slash_command_uses_synthetic_message_id_and_request_id() -> None:
    """Slash commands should use a stable synthetic message anchor."""
    body = build_slash_command_turn(
        workspace_id="T123TEAM",
        channel_id="C123CHAN",
        trigger_id="1337-trigger",
        user_id="U123USER",
        text="recent",
    )

    assert body["thread_id"] is None
    assert body["message_id"] == "cmd:1337-trigger"
    assert body["idempotency_key"] == "slack:T123TEAM:command:1337-trigger"
    assert body["request_id"] == "req-slack-cmd-1337-trigger"


def test_sign_nimbus_request_matches_the_documented_hmac_shape() -> None:
    """The Python helper should match the documented wrapper HMAC contract."""
    body = encode_turn_body(
        {
            "platform": "slack",
            "workspace_id": "T123TEAM",
            "channel_id": "C123CHAN",
            "thread_id": "1713840000.123456",
            "message_id": "1713840000.123456",
            "user_id": "U123USER",
            "text": "hello",
            "idempotency_key": "slack:T123TEAM:event:evt-123",
            "request_id": "req-slack-evt-123",
        }
    )
    headers = sign_nimbus_request(
        body=body,
        secret="test-secret",  # noqa: S106 - fixed test fixture value
        timestamp=1713849999,
        nonce="nonce-123",
    )

    digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n/ai/chat/turn\n1713849999\nnonce-123\n{digest}"
    expected = hmac.new(
        b"test-secret",
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert headers["X-Nimbus-Signature"] == expected


def test_message_event_requires_a_message_timestamp() -> None:
    """Slack message normalization should reject events without a timestamp."""
    with pytest.raises(ValueError, match="ts"):
        build_message_event_turn(
            workspace_id="T123TEAM",
            event_id="evt-123",
            event={
                "channel": "C123CHAN",
                "user": "U123USER",
                "text": "hello",
            },
        )


def test_message_event_rejects_non_string_text() -> None:
    """Malformed wrapper events should not stringify ``None`` into user text."""
    with pytest.raises(TypeError, match="string text"):
        build_message_event_turn(
            workspace_id="T123TEAM",
            event_id="evt-123",
            event={
                "channel": "C123CHAN",
                "ts": "1713840000.123456",
                "user": "U123USER",
                "text": None,
            },
        )


def test_message_event_rejects_bare_app_mention_with_no_remaining_text() -> None:
    """A bare mention should be rejected before it becomes a 422 request body."""
    with pytest.raises(ValueError, match="non-empty text"):
        build_message_event_turn(
            workspace_id="T123TEAM",
            event_id="evt-123",
            event={
                "channel": "C123CHAN",
                "ts": "1713840000.123456",
                "user": "U123USER",
                "text": "<@U0BOT>",
            },
        )
