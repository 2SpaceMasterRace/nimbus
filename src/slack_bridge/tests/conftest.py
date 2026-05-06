"""Shared fixtures for slack_bridge tests."""

from __future__ import annotations

import pytest

from nimbus_runtime import runtime_telemetry


@pytest.fixture(autouse=True)
def _reset_runtime_telemetry() -> None:
    """Clear the in-memory telemetry registry before every bridge test."""
    runtime_telemetry.reset()


@pytest.fixture
def sample_event() -> dict[str, object]:
    """Return a minimal Slack message event dict."""
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
    """Return a Slack app-mention event dict."""
    return {
        "type": "app_mention",
        "user": "U676767",
        "text": "<@BOT> list my files",
        "ts": "1234567890.123456",
        "channel": "XYZ123",
        "event_ts": "1234567890.123456",
    }
