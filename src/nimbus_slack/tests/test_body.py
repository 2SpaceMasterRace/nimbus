"""Tests for nimbus_slack.body."""

from __future__ import annotations

import pytest
from nimbus_slack.body import _strip_mention, build_event_body

pytestmark = pytest.mark.unit


class TestStripMention:
    def test_strips_leading_mention(self) -> None:
        assert _strip_mention("<@BOT> list my files") == "list my files"

    def test_leaves_plain_text_unchanged(self) -> None:
        assert _strip_mention("hello world") == "hello world"

    def test_leaves_empty_string_unchanged(self) -> None:
        assert _strip_mention("") == ""

    def test_strips_only_leading_mention(self) -> None:
        assert _strip_mention("<@BOT> hello <@U12345>") == "hello <@U12345>"

    def test_only_mention_becomes_empty_text(self) -> None:
        assert _strip_mention("<@BOT>") == ""


class TestBuildEventBody:
    def test_returns_nimbus_turn_request(self, sample_event: dict[str, object]) -> None:
        result = build_event_body(
            team_id="T123456",
            event_id="E123456",
            event=sample_event,
        )
        assert result.platform == "slack"
        assert result.workspace_id == "T123456"
        assert result.channel_id == "XYZ123"
        assert result.user_id == "U676767"
        assert result.text == "Hi!!!"

    def test_idempotency_key_is_stable(self, sample_event: dict[str, object]) -> None:
        result = build_event_body(
            team_id="T123456",
            event_id="E123456",
            event=sample_event,
        )
        assert result.idempotency_key == "slack:T123456:event:E123456"

    def test_thread_id_defaults_to_message_ts(
        self, sample_event: dict[str, object]
    ) -> None:
        result = build_event_body(
            team_id="T123456",
            event_id="E123456",
            event=sample_event,
        )
        assert result.thread_id == result.message_id

    def test_strips_mention_from_text(self, mention_event: dict[str, object]) -> None:
        result = build_event_body(
            team_id="T123456",
            event_id="E123456",
            event=mention_event,
        )
        assert result.text == "list my files"

    def test_rejects_empty_text_after_mention_strip(self) -> None:
        event = {
            "type": "app_mention",
            "user": "U676767",
            "text": "<@BOT>",
            "ts": "1234567890.123456",
            "channel": "XYZ123",
        }

        with pytest.raises(ValueError, match="text"):
            build_event_body(team_id="T123456", event_id="E123456", event=event)
