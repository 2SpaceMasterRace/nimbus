"""Tests for Slack adapter command parsing."""

from __future__ import annotations

import pytest
from nimbus_slack.commands import SlackCommandKind, parse_slack_command

pytestmark = pytest.mark.unit


def test_parse_setup_command() -> None:
    """Setup-like text should stay in the Slack adapter."""
    command = parse_slack_command("setup")

    assert command.kind is SlackCommandKind.SETUP


def test_parse_save_channel_files_command() -> None:
    """Save-channel-file requests should not be sent to the model."""
    command = parse_slack_command("save all the files in this channel")

    assert command.kind is SlackCommandKind.SAVE_CHANNEL_FILES


def test_parse_diff_channel_files_command() -> None:
    """Missing-file questions should map to the diff operation."""
    command = parse_slack_command(
        "what files in this channel are not saved in my s3 bucket?"
    )

    assert command.kind is SlackCommandKind.DIFF_CHANNEL_FILES


def test_parse_ordinary_chat_falls_back_to_model() -> None:
    """Ordinary chat should remain model-owned."""
    command = parse_slack_command("summarize the storage policy")

    assert command.kind is SlackCommandKind.MODEL_TURN
