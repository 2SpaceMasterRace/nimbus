"""Tests for nimbus_slack.deps."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_get_chat_client_requires_slack_bot_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory reads SLACK_BOT_TOKEN when resolving the client."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

    from nimbus_slack.deps import get_chat_client

    with pytest.raises(ValueError, match="SLACK_BOT_TOKEN"):
        get_chat_client()


def test_get_chat_client_returns_chat_client_when_token_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a token, the registered Slack implementation is returned."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token")

    from nimbus_slack.deps import get_chat_client

    client = get_chat_client()
    assert client.send_message is not None
    assert client.get_channels is not None


class TestPositiveIntEnv:
    def test_returns_default_when_not_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nimbus_slack.deps import _positive_int_env

        monkeypatch.delenv("TEST_ENV_VAR", raising=False)
        assert _positive_int_env("TEST_ENV_VAR", default=42) == 42

    def test_returns_parsed_value_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nimbus_slack.deps import _positive_int_env

        monkeypatch.setenv("TEST_ENV_VAR", "10")
        assert _positive_int_env("TEST_ENV_VAR", default=42) == 10

    def test_raises_on_non_integer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nimbus_slack.deps import _positive_int_env

        monkeypatch.setenv("TEST_ENV_VAR", "abc")
        with pytest.raises(ValueError, match="must be a positive integer"):
            _positive_int_env("TEST_ENV_VAR", default=42)

    def test_raises_on_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nimbus_slack.deps import _positive_int_env

        monkeypatch.setenv("TEST_ENV_VAR", "0")
        with pytest.raises(ValueError, match="must be a positive integer"):
            _positive_int_env("TEST_ENV_VAR", default=42)

    def test_raises_on_negative(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from nimbus_slack.deps import _positive_int_env

        monkeypatch.setenv("TEST_ENV_VAR", "-5")
        with pytest.raises(ValueError, match="must be a positive integer"):
            _positive_int_env("TEST_ENV_VAR", default=42)


class TestGetSlackPoster:
    def test_raises_when_no_token_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nimbus_slack.deps import get_slack_poster

        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.setenv("NIMBUS_SLACK_SECRET_KEY", "")
        with pytest.raises(ValueError, match="not configured"):
            get_slack_poster()

    def test_returns_poster_from_env_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from nimbus_slack.deps import SlackSdkPoster, get_slack_poster

        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
        monkeypatch.setenv("NIMBUS_SLACK_SECRET_KEY", "")
        poster = get_slack_poster()
        assert isinstance(poster, SlackSdkPoster)
