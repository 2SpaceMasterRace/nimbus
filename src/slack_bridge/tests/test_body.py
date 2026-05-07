"""Tests for slack_bridge.body."""

from __future__ import annotations

import hashlib

import pytest
from nimbus_runtime.models import TurnAttachment
from slack_bridge.body import (
    _strip_mention,
    build_event_body,
    build_slash_command_body,
)

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

    def test_strips_bare_mention_with_no_following_text(self) -> None:
        assert _strip_mention("<@BOT>") == ""

    def test_strips_mention_with_newline_separator(self) -> None:
        assert _strip_mention("<@BOT>\nhello") == "hello"

    def test_strips_mention_with_pipe_fallback(self) -> None:
        assert _strip_mention("<@BOT|nimbus> hello") == "hello"

    def test_returns_unchanged_when_mention_is_unterminated(self) -> None:
        assert _strip_mention("<@BOT no closing bracket") == "<@BOT no closing bracket"


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

    def test_no_files_yields_empty_attachments_tuple(
        self, sample_event: dict[str, object]
    ) -> None:
        """Plain message events forward an empty attachments tuple."""
        result = build_event_body(
            team_id="T123456",
            event_id="E123456",
            event=sample_event,
        )
        assert result.attachments == ()

    def test_extracts_single_file_attachment(
        self, sample_event: dict[str, object]
    ) -> None:
        """A well-formed Slack file becomes one TurnAttachment."""
        sample_event["files"] = [
            {
                "id": "F123",
                "name": "report.csv",
                "mimetype": "text/csv",
                "size": 1024,
            },
        ]
        result = build_event_body(
            team_id="T123456",
            event_id="E123456",
            event=sample_event,
        )
        assert result.attachments == (
            TurnAttachment(
                platform_file_id="F123",
                filename="report.csv",
                content_type="text/csv",
                size_bytes=1024,
            ),
        )

    def test_drops_oversized_file(self, sample_event: dict[str, object]) -> None:
        """Files larger than 20 MiB are dropped at the bridge boundary."""
        sample_event["files"] = [
            {
                "id": "F-huge",
                "name": "huge.bin",
                "mimetype": "application/octet-stream",
                "size": 21 * 1024 * 1024,
            },
        ]
        result = build_event_body(
            team_id="T",
            event_id="E",
            event=sample_event,
        )
        assert result.attachments == ()

    def test_drops_zero_size_file(self, sample_event: dict[str, object]) -> None:
        """Files reported with size <= 0 are dropped."""
        sample_event["files"] = [
            {"id": "F0", "name": "empty.txt", "mimetype": "text/plain", "size": 0},
        ]
        result = build_event_body(team_id="T", event_id="E", event=sample_event)
        assert result.attachments == ()

    def test_caps_attachments_at_ten(self, sample_event: dict[str, object]) -> None:
        """Only the first 10 attachments are forwarded; the rest are dropped."""
        sample_event["files"] = [
            {
                "id": f"F{i}",
                "name": f"file-{i}.txt",
                "mimetype": "text/plain",
                "size": 10,
            }
            for i in range(15)
        ]
        result = build_event_body(team_id="T", event_id="E", event=sample_event)
        assert len(result.attachments) == 10
        assert [a.platform_file_id for a in result.attachments] == [
            f"F{i}" for i in range(10)
        ]

    def test_drops_malformed_file_entries(
        self, sample_event: dict[str, object]
    ) -> None:
        """Non-dict entries and entries missing id/name are silently dropped."""
        sample_event["files"] = [
            "not a dict",
            {"id": "", "name": "blank-id.txt", "mimetype": "text/plain", "size": 5},
            {"id": "F1", "name": "", "mimetype": "text/plain", "size": 5},
            {"id": "F2", "mimetype": "text/plain", "size": 5},
            {
                "id": "F3",
                "name": "ok.txt",
                "mimetype": "text/plain",
                "size": 7,
            },
        ]
        result = build_event_body(team_id="T", event_id="E", event=sample_event)
        assert [a.platform_file_id for a in result.attachments] == ["F3"]

    def test_defaults_missing_mimetype(self, sample_event: dict[str, object]) -> None:
        """Files without a mimetype fall back to application/octet-stream."""
        sample_event["files"] = [{"id": "F", "name": "blob", "size": 9}]
        result = build_event_body(team_id="T", event_id="E", event=sample_event)
        assert result.attachments == (
            TurnAttachment(
                platform_file_id="F",
                filename="blob",
                content_type="application/octet-stream",
                size_bytes=9,
            ),
        )

    def test_accepts_numeric_string_size(self, sample_event: dict[str, object]) -> None:
        """Slack occasionally sends size as a numeric string; accept it."""
        sample_event["files"] = [
            {"id": "F", "name": "stringy.txt", "mimetype": "text/plain", "size": "42"},
        ]
        result = build_event_body(team_id="T", event_id="E", event=sample_event)
        assert result.attachments[0].size_bytes == 42

    def test_ignores_non_list_files_field(
        self, sample_event: dict[str, object]
    ) -> None:
        """A files field of the wrong type is treated as no attachments."""
        sample_event["files"] = "not a list"
        result = build_event_body(team_id="T", event_id="E", event=sample_event)
        assert result.attachments == ()


class TestBuildSlashCommandBody:
    @staticmethod
    def _form(**overrides: str) -> dict[str, str]:
        """Build a baseline slash-command form payload."""
        base = {
            "team_id": "T123",
            "trigger_id": "trig-1",
            "channel_id": "C9",
            "user_id": "U7",
            "text": "list reports/",
            "command": "/nimbus",
        }
        base.update(overrides)
        return base

    def test_returns_nimbus_turn_request_with_command_shape(self) -> None:
        """Slash command body hashes trigger_id into message_id and nulls thread_id."""
        result = build_slash_command_body(self._form())
        expected_hash = hashlib.sha256(b"trig-1").hexdigest()[:48]
        assert result.platform == "slack"
        assert result.workspace_id == "T123"
        assert result.channel_id == "C9"
        assert result.user_id == "U7"
        assert result.text == "list reports/"
        assert result.thread_id is None
        assert result.message_id == f"cmd:{expected_hash}"
        assert result.idempotency_key == "slack:T123:command:trig-1"
        assert result.request_id == "slack-cmd-trig-1"
        assert result.attachments == ()

    def test_long_trigger_id_keeps_message_id_within_ai_server_limit(self) -> None:
        """A realistic ~60-char trigger_id must not overflow the 64-char cap."""
        trigger_id = "11077352019957.8316111329843.3cdef1234567890abcdef1234567890ab"
        result = build_slash_command_body(self._form(trigger_id=trigger_id))
        assert len(result.message_id) <= 64

    def test_strips_whitespace_in_command_text(self) -> None:
        """Surrounding whitespace in the user's command text is trimmed."""
        result = build_slash_command_body(self._form(text="   list   "))
        assert result.text == "list"

    def test_missing_text_field_yields_empty_string(self) -> None:
        """Slash commands sent with no text default to an empty string body."""
        form = self._form()
        del form["text"]
        result = build_slash_command_body(form)
        assert result.text == ""

    def test_explicit_command_text_overrides_form(self) -> None:
        """The optional command_text argument wins over the form text field."""
        result = build_slash_command_body(
            self._form(text="ignored"),
            command_text="explicit override",
        )
        assert result.text == "explicit override"

    def test_missing_required_field_raises_key_error(self) -> None:
        """Missing team_id/trigger_id/channel_id/user_id surface as KeyError."""
        form = self._form()
        del form["trigger_id"]
        with pytest.raises(KeyError):
            build_slash_command_body(form)
