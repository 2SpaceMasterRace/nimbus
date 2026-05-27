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


@pytest.mark.parametrize(
    "text",
    [
        "what files are in this channel?",
        "list the files in this channel",
        "show me the files here",
        "which files are in this channel",
    ],
)
def test_parse_list_channel_files_command(text: str) -> None:
    """Channel-listing prompts should hit the adapter, not the model."""
    assert parse_slack_command(text).kind is SlackCommandKind.LIST_CHANNEL_FILES


def test_parse_upload_alias_routes_to_save_command() -> None:
    """Alias `upload` should route to the existing save flow."""
    command = parse_slack_command("upload all files in this channel to my bucket")

    assert command.kind is SlackCommandKind.SAVE_CHANNEL_FILES


def test_parse_save_channel_mentions_routes_to_save_command() -> None:
    """Mentioned Slack channels are enough destination context for save."""
    command = parse_slack_command("save files from <#C1|legal> and <#C2|design>")

    assert command.kind is SlackCommandKind.SAVE_CHANNEL_FILES


@pytest.mark.parametrize(
    "text",
    [
        "can you store all the files in this channel to my aws bucket?",
        "archive every file in this channel",
        "back up the files here please",
        "snapshot the channel files to s3",
        "ingest the files in this channel",
    ],
)
def test_parse_save_synonyms_route_to_save_command(text: str) -> None:
    """Common natural phrasings for save should hit the adapter."""
    assert parse_slack_command(text).kind is SlackCommandKind.SAVE_CHANNEL_FILES


@pytest.mark.parametrize(
    "text",
    [
        "which files changed since the last sync?",
        "what files changed since last sync",
        "files changed since last save",
    ],
)
def test_parse_changed_since_sync_command(text: str) -> None:
    """Change-since-sync prompts should hit the adapter."""
    assert parse_slack_command(text).kind is SlackCommandKind.CHANGED_SINCE_SYNC


@pytest.mark.parametrize(
    "text",
    [
        "find duplicate files",
        "detect duplicates",
        "are there any stale files in s3?",
    ],
)
def test_parse_dedupe_report_command(text: str) -> None:
    """Dedupe / stale prompts should hit the adapter."""
    assert parse_slack_command(text).kind is SlackCommandKind.DEDUPE_REPORT


def test_diff_still_wins_over_list_when_phrased_with_missing() -> None:
    """A `missing from S3` question should map to diff, not list."""
    command = parse_slack_command("which channel files are missing from s3?")

    assert command.kind is SlackCommandKind.DIFF_CHANNEL_FILES


# ── P8: STATUS command ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "status",
        "STATUS",
        "Status",
        "health",
        "ping",
        "diagnostics",
    ],
)
def test_parse_status_keywords_return_status_command(text: str) -> None:
    """Exact status keywords should route to the adapter STATUS handler."""
    assert parse_slack_command(text).kind is SlackCommandKind.STATUS


def test_status_is_exact_match_only() -> None:
    """Phrases that merely contain a status keyword fall through to the model."""
    # "health check" is not in _STATUS_KEYWORDS so it's a model turn
    command = parse_slack_command("run a health check on my bucket")
    assert command.kind is SlackCommandKind.MODEL_TURN


def test_status_is_case_insensitive() -> None:
    """parse_slack_command normalises to lower-case before matching."""
    assert parse_slack_command("PING").kind is SlackCommandKind.STATUS
    assert parse_slack_command("  Health  ").kind is SlackCommandKind.STATUS


# ── TOOLS command ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "tools",
        "capabilities",
        "what can you do",
        "what can nimbus do",
        "help",
        "commands",
    ],
)
def test_parse_tools_command(text: str) -> None:
    """Capability-discovery prompts should stay adapter-owned."""
    assert parse_slack_command(text).kind is SlackCommandKind.TOOLS


# ── SEARCH command ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "search for the quarterly report document",
        "find the indexed file named budget.xlsx",
        "look for documents in s3",
        "query the indexed documents for security policy",
    ],
)
def test_parse_search_command(text: str) -> None:
    """Explicit search phrases should route to the SEARCH adapter handler."""
    assert parse_slack_command(text).kind is SlackCommandKind.SEARCH


def test_ordinary_find_without_document_cue_falls_through_to_model() -> None:
    """A vague `find` that doesn't mention files/docs stays as a model turn."""
    command = parse_slack_command("find a way to improve our CI pipeline")
    assert command.kind is SlackCommandKind.MODEL_TURN
