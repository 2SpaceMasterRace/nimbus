"""Tests for slack_bridge.deps."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_get_chat_client_requires_slack_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory reads SLACK_BOT_TOKEN when resolving the client."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    from slack_bridge.deps import get_chat_client

    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
        get_chat_client()


def test_get_chat_client_returns_chat_client_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a token, the registered Slack implementation is returned."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")

    from slack_bridge.deps import get_chat_client

    client = get_chat_client()
    assert client.send_message is not None
    assert client.get_channels is not None
