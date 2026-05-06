"""Tests for slack_bridge.render."""

from __future__ import annotations

import pytest
from nimbus_runtime.models import ChatTurnResult, ConfirmationDetails
from slack_bridge.render import render_for_chat

pytestmark = pytest.mark.unit


def _result(
    *,
    text: str,
    outcome: str,
    confirmation: ConfirmationDetails | None = None,
) -> ChatTurnResult:
    """Build a minimal ChatTurnResult for renderer tests."""
    return ChatTurnResult(
        request_id="req-1",
        conversation_id="conv-1",
        text=text,
        outcome=outcome,  # type: ignore[arg-type]
        confirmation_required=outcome == "confirmation_required",
        confirmation=confirmation,
    )


@pytest.mark.parametrize("outcome", ["reply", "partial_success", "error"])
def test_render_for_chat_passthrough_outcomes(outcome: str) -> None:
    """Reply, partial_success, and error outcomes pass text through verbatim."""
    rendered = render_for_chat(_result(text="hello world", outcome=outcome))
    assert rendered == "hello world"


def test_render_for_chat_confirmation_appends_expected_reply_when_missing() -> None:
    """Confirmation outcomes append the expected-reply hint when not in text."""
    confirmation = ConfirmationDetails(
        action_id="act-1",
        kind="delete_file",
        prompt="Delete file foo.txt?",
        expected_reply="YES",
        expires_at="2030-01-01T00:00:00Z",
    )
    result = _result(
        text="Delete file foo.txt?",
        outcome="confirmation_required",
        confirmation=confirmation,
    )
    rendered = render_for_chat(result)
    assert rendered.startswith("Delete file foo.txt?")
    assert "Reply `YES` to confirm." in rendered


def test_render_for_chat_confirmation_skips_hint_when_already_present() -> None:
    """If the runtime already mentions the expected reply, don't duplicate it."""
    confirmation = ConfirmationDetails(
        action_id="act-1",
        kind="delete_file",
        prompt="Reply YES to confirm deletion of foo.txt.",
        expected_reply="YES",
        expires_at="2030-01-01T00:00:00Z",
    )
    result = _result(
        text="Reply YES to confirm deletion of foo.txt.",
        outcome="confirmation_required",
        confirmation=confirmation,
    )
    rendered = render_for_chat(result)
    assert rendered == "Reply YES to confirm deletion of foo.txt."


def test_render_for_chat_confirmation_without_details_passes_through() -> None:
    """confirmation_required without ConfirmationDetails still returns text."""
    result = _result(
        text="Are you sure?",
        outcome="confirmation_required",
        confirmation=None,
    )
    assert render_for_chat(result) == "Are you sure?"
