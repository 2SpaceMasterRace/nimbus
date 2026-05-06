"""Render Nimbus chat-turn results into the text posted back to chat.

The Nimbus runtime owns the canonical user-facing text for each outcome, so
this module is intentionally a thin transformation rather than a duplicate
formatter. It exists as the dedicated seam where Slack-specific rendering
concerns (Block Kit, threading, mentions, attachments) can be added later
without leaking transport details into the runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nimbus_runtime.models import ChatTurnResult


def render_for_chat(result: ChatTurnResult) -> str:
    """Return the chat-facing text for one Nimbus chat-turn result.

    The transform is conservative:

    * ``reply``, ``partial_success``, and ``error`` outcomes pass through
      ``result.text`` unchanged.
    * ``confirmation_required`` outcomes append a short, action-oriented
      footer derived from ``confirmation.expected_reply``, but only when
      the expected reply is not already present in ``result.text`` to avoid
      duplicating the runtime-supplied prompt.
    """
    text = result.text
    confirmation = result.confirmation
    if (
        result.outcome == "confirmation_required"
        and confirmation is not None
        and confirmation.expected_reply
        and confirmation.expected_reply not in text
    ):
        text = f"{text}\n\nReply `{confirmation.expected_reply}` to confirm."
    return text
