"""Small Slack command parser for adapter-owned Nimbus operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SlackCommandKind(StrEnum):
    """Slack commands handled by the adapter before model fallback."""

    SETUP = "setup"
    STATUS = "status"
    SAVE_CHANNEL_FILES = "save_channel_files"
    DIFF_CHANNEL_FILES = "diff_channel_files"
    LIST_CHANNEL_FILES = "list_channel_files"
    CHANGED_SINCE_SYNC = "changed_since_sync"
    DEDUPE_REPORT = "dedupe_report"
    SEARCH = "search"
    TOOLS = "tools"
    MODEL_TURN = "model_turn"


@dataclass(frozen=True, slots=True)
class SlackCommand:
    """Parsed Slack command."""

    kind: SlackCommandKind


_STATUS_KEYWORDS = frozenset({"status", "health", "ping", "diagnostics"})
_TOOLS_KEYWORDS = frozenset(
    {
        "tools",
        "tool list",
        "capabilities",
        "what can you do",
        "what can nimbus do",
        "help",
        "commands",
    }
)


def parse_slack_command(text: str) -> SlackCommand:
    """Parse Slack text into an adapter command or model fallback."""
    normalized = " ".join(text.lower().split())
    if normalized in {"setup", "configure", "onboard", "onboarding"}:
        return SlackCommand(SlackCommandKind.SETUP)
    if normalized in _STATUS_KEYWORDS:
        return SlackCommand(SlackCommandKind.STATUS)
    if normalized in _TOOLS_KEYWORDS:
        return SlackCommand(SlackCommandKind.TOOLS)
    for matches, kind in _MATCHERS:
        if matches(normalized):
            return SlackCommand(kind)
    return SlackCommand(SlackCommandKind.MODEL_TURN)


_SAVE_VERBS = (
    "save",
    "upload",
    "store",
    "back up",
    "backup",
    "archive",
    "sync",
    "push",
    "ingest",
    "snapshot",
)


def _looks_like_save_files(text: str) -> bool:
    """Return whether text asks Nimbus to save channel files to S3."""
    has_save_verb = any(verb in text for verb in _SAVE_VERBS)
    has_destination = (
        "channel" in text
        or "here" in text
        or "bucket" in text
        or "s3" in text
        or "this thread" in text
        or "<#" in text
    )
    return has_save_verb and "file" in text and has_destination


def _looks_like_diff_files(text: str) -> bool:
    """Return whether text asks Nimbus to compare Slack files with S3."""
    return (
        "file" in text
        and ("s3" in text or "bucket" in text or "saved" in text)
        and (
            "not saved" in text
            or "unsaved" in text
            or "missing" in text
            or "not in" in text
        )
    )


def _looks_like_list_files(text: str) -> bool:
    """Return whether text asks Nimbus to list Slack channel files."""
    if "file" not in text:
        return False
    list_phrases = (
        "what files",
        "which files",
        "list files",
        "list the files",
        "show files",
        "show the files",
        "show me the files",
    )
    if any(phrase in text for phrase in list_phrases):
        return "channel" in text or "here" in text or "in this" in text
    return False


def _looks_like_changed_since_sync(text: str) -> bool:
    """Return whether text asks for files changed since the last sync."""
    if "file" not in text and "files" not in text:
        return False
    if "since" not in text and "after" not in text:
        return False
    return "sync" in text or "save" in text or "backup" in text


def _looks_like_dedupe(text: str) -> bool:
    """Return whether text asks for duplicate or stale file detection."""
    if "duplicate" in text or "duplicates" in text:
        return True
    return "stale" in text and ("file" in text or "files" in text)


def _looks_like_search(text: str) -> bool:
    """Return whether text is an explicit search query against indexed docs."""
    search_verbs = ("search", "find", "look for", "lookup", "query")
    return any(verb in text for verb in search_verbs) and (
        "file" in text or "document" in text or "indexed" in text or "in s3" in text
    )


# Matchers run in order; the first hit wins. The order is significant: more
# specific patterns (containing "missing"/"changed since") come before broader
# ones (plain "list files") so phrasing like "which files are missing" routes
# to the diff command instead of the listing command.
_MATCHERS = (
    (_looks_like_dedupe, SlackCommandKind.DEDUPE_REPORT),
    (_looks_like_changed_since_sync, SlackCommandKind.CHANGED_SINCE_SYNC),
    (_looks_like_diff_files, SlackCommandKind.DIFF_CHANNEL_FILES),
    (_looks_like_save_files, SlackCommandKind.SAVE_CHANNEL_FILES),
    (_looks_like_list_files, SlackCommandKind.LIST_CHANNEL_FILES),
    (_looks_like_search, SlackCommandKind.SEARCH),
)
