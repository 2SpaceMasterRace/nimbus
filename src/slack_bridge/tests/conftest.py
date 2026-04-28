"""Shared fixtures for slack_bridge tests."""

from __future__ import annotations

import pytest

@pytest.fixture
def sample_event() -> dict[str, object]:
    """A minimal Slack message event dict."""
    return {
        "type": "message",
        "user": "U676767",
        "text": "Hi!!!",
        "ts": "1234567890.123456",
        "channel": "XYZ123",
        "event_ts": "1234567890.123456",
    }


@pytest.fixture
def mention_event() -> dict[str, object]:
    """A Slack app-mention event dict."""
    return {
        "type": "app_mention",
        "user": "U676767",
        "text": "<@BOT> list my files",
        "ts": "1234567890.123456",
        "channel": "XYZ123",
        "event_ts": "1234567890.123456",
    }