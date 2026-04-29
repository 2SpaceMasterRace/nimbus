"""Small Slack command parser for adapter-owned Nimbus operations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SlackCommandKind(StrEnum):
    """Slack commands handled by the adapter before model fallback."""

    SETUP = "setup"
    SAVE_CHANNEL_FILES = "save_channel_files"
    DIFF_CHANNEL_FILES = "diff_channel_files"
    MODEL_TURN = "model_turn"


@dataclass(frozen=True, slots=True)
class SlackCommand:
    """Parsed Slack command."""

    kind: SlackCommandKind


def parse_slack_command(text: str) -> SlackCommand:
    """Parse Slack text into an adapter command or model fallback."""
    normalized = " ".join(text.lower().split())
    if normalized in {"setup", "configure", "onboard", "onboarding"}:
        return SlackCommand(SlackCommandKind.SETUP)
    if _looks_like_diff_files(normalized):
        return SlackCommand(SlackCommandKind.DIFF_CHANNEL_FILES)
    if _looks_like_save_files(normalized):
        return SlackCommand(SlackCommandKind.SAVE_CHANNEL_FILES)
    return SlackCommand(SlackCommandKind.MODEL_TURN)


def _looks_like_save_files(text: str) -> bool:
    """Return whether text asks Nimbus to save channel files."""
    return "save" in text and "file" in text and ("channel" in text or "here" in text)


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
