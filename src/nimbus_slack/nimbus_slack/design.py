"""Nimbus Slack design system.

Reusable Block Kit primitives — the Slack-side counterpart to
``nimbus_cli.ui``. Every Slack card in Nimbus pulls from this module so
headers, buttons, dividers, and footers stay consistent.

Slack does not let us style our own colors directly inside Block Kit, so this
module's job is twofold:

1. Provide a small, opinionated set of card patterns that look the same
   across all Nimbus surfaces (setup, save, dedupe, approval).
2. Keep labels, buttons, and placeholders conversational instead of making
   Slack feel like a terminal dashboard.
"""

from __future__ import annotations

from typing import Any, Literal

# ── Legacy status tokens ─────────────────────────────────────────────────────

# Kept for older callers/tests that import these names. New Slack copy should
# prefer plain language over visible emoji status markers.
EMOJI_OK = "✅"
EMOJI_FAIL = "❌"
EMOJI_WARN = "⚠️"
EMOJI_INFO = "ℹ️"  # noqa: RUF001
EMOJI_PENDING = "⏳"
EMOJI_RUNNING = "⚙️"
EMOJI_SCAN = "🔍"
EMOJI_SAVE = "💾"
EMOJI_FILES = "📂"
EMOJI_ROCKET = "🚀"
EMOJI_LOCK = "🔐"
EMOJI_LINK = "🔗"

ButtonStyle = Literal["primary", "danger", "default"]


# ── Primitive builders ──────────────────────────────────────────────────────


def header(text: str) -> dict[str, Any]:
    """Return a branded card header block.

    Pure text, with status expressed in nearby copy instead of icon prefixes.
    """
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": text, "emoji": False},
    }


def section(text: str) -> dict[str, Any]:
    """Return a standard mrkdwn section block."""
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def context(*lines: str) -> dict[str, Any]:
    """Return a muted context block — good for metadata and footers."""
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": line} for line in lines],
    }


def divider() -> dict[str, Any]:
    """Return a standard divider block."""
    return {"type": "divider"}


def fields(*field_lines: str) -> dict[str, Any]:
    """Two-column field section, used for compact metadata blocks."""
    return {
        "type": "section",
        "fields": [{"type": "mrkdwn", "text": f} for f in field_lines],
    }


def button(
    label: str,
    *,
    action_id: str,
    value: str | None = None,
    url: str | None = None,
    style: ButtonStyle = "default",
) -> dict[str, Any]:
    """Build a Block Kit button.

    Either ``value`` (interactive — Slack will POST a block_actions payload to
    the configured Interactivity URL) or ``url`` (link button — opens the URL
    in the user's browser, no callback) must be set.

    Use ``style="primary"`` for the recommended action, ``"danger"`` for
    destructive actions, ``"default"`` for everything else.
    """
    if (value is None) == (url is None):
        msg = "button requires exactly one of `value` or `url`"
        raise ValueError(msg)
    btn: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": label, "emoji": False},
        "action_id": action_id,
    }
    if value is not None:
        btn["value"] = value
    if url is not None:
        btn["url"] = url
    if style != "default":
        btn["style"] = style
    return btn


def action_bar(*buttons: dict[str, Any]) -> dict[str, Any]:
    """Wrap one or more buttons in an actions block."""
    return {"type": "actions", "elements": list(buttons)}


def link_button(
    label: str, url: str, *, action_id: str = "open_link"
) -> dict[str, Any]:
    """Return a link-style button (convenience wrapper around `button`)."""
    return button(label, action_id=action_id, url=url)


# ── Higher-level card patterns ──────────────────────────────────────────────


def branded_header(text: str, *, status: str | None = None) -> dict[str, Any]:
    """Header helper that ignores legacy status hints for plain Slack copy."""
    _ = status
    return header(text)


def footer(text: str = "Nimbus - proof-carrying storage") -> dict[str, Any]:
    """Return a branded footer context block.

    Use sparingly — Slack already shows the bot name, so most cards don't need this.
    """
    return context(text)


def setup_card(
    *, install_url: str, docs_url: str | None = None
) -> list[dict[str, Any]]:
    """Render the flagship setup card with a tappable "Open setup" link button."""
    blocks: list[dict[str, Any]] = [
        branded_header("Nimbus needs workspace setup", status="info"),
        section(
            "An admin needs to connect *OpenRouter* and *S3* credentials. "
            "Credentials are entered in your browser — never in Slack."
        ),
        context("Setup takes about *2 minutes*. Secrets are encrypted at rest."),
    ]
    buttons: list[dict[str, Any]] = [
        button(
            "Open Setup",
            action_id="open_setup",
            url=install_url,
            style="primary",
        )
    ]
    if docs_url:
        buttons.append(link_button("Docs", docs_url, action_id="open_docs"))
    blocks.append(action_bar(*buttons))
    return blocks


def thinking_card(
    text: str = "Scanning files…",
    *,
    steps: list[str] | None = None,
    current_step: int = 0,
) -> list[dict[str, Any]]:
    """Lightweight placeholder card used while a slow command runs.

    Pass ``steps`` for a multi-step scanning indicator that shows which step
    is active (bold), which are done (strikethrough), and which are pending
    (bullet). Posted immediately after Slack ACKs, edited in-place when done.
    """
    blocks: list[dict[str, Any]] = [section(f"_{text}_")]

    if steps:
        step_parts: list[str] = []
        for i, step in enumerate(steps):
            if i < current_step:
                step_parts.append(f"Done: ~{step}~")
            elif i == current_step:
                step_parts.append(f"Now: *{step}*")
            else:
                step_parts.append(f"Next: {step}")
        blocks.append(context("  |  ".join(step_parts)))

    return blocks


def error_card(
    *,
    title: str,
    detail: str,
    retry_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Render a user-facing error card with a friendly title and optional retry hint."""
    blocks: list[dict[str, Any]] = [
        branded_header(title, status="error"),
        section(detail),
    ]
    if retry_hint:
        blocks.append(context(f"Try: {retry_hint}"))
    return blocks


def warning_card(
    *,
    title: str,
    detail: str,
    retry_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Render a user-facing warning card with an optional retry hint."""
    blocks: list[dict[str, Any]] = [
        branded_header(title, status="warning"),
        section(detail),
    ]
    if retry_hint:
        blocks.append(context(f"Try: {retry_hint}"))
    return blocks


# ── File-card action bars ───────────────────────────────────────────────────


def file_list_actions(*, can_save: bool = True) -> dict[str, Any]:
    """Return an action bar for file-listing cards (save, diff, and dedupe buttons)."""
    buttons: list[dict[str, Any]] = []
    if can_save:
        buttons.append(
            button(
                "Save all to S3",
                action_id="cmd:save_channel_files",
                value="save_channel_files",
                style="primary",
            )
        )
    buttons.append(
        button(
            "Find duplicates",
            action_id="cmd:dedupe_report",
            value="dedupe_report",
        )
    )
    buttons.append(
        button(
            "What's missing?",
            action_id="cmd:diff_channel_files",
            value="diff_channel_files",
        )
    )
    return action_bar(*buttons)


def save_report_actions(*, has_failures: bool) -> dict[str, Any]:
    """Return an action bar for save-report cards."""
    buttons = [
        button(
            "View diff",
            action_id="cmd:diff_channel_files",
            value="diff_channel_files",
        ),
        button(
            "Find duplicates",
            action_id="cmd:dedupe_report",
            value="dedupe_report",
        ),
    ]
    if has_failures:
        buttons.append(
            button(
                "Retry failed",
                action_id="cmd:retry_save",
                value="retry_save",
                style="primary",
            )
        )
    return action_bar(*buttons)


def diff_report_actions() -> dict[str, Any]:
    """Return an action bar for diff cards — primary CTA is save."""
    return action_bar(
        button(
            "Save unsaved files",
            action_id="cmd:save_channel_files",
            value="save_channel_files",
            style="primary",
        ),
    )


def task_action_bar(*, task_id: str, status: str) -> dict[str, Any]:
    """Return an action bar for a task card, adapting buttons to the current status.

    - awaiting_approval: Approve / Reject (bound to this task's action ID).
    - terminal states: View artifacts link.
    - active states: Cancel button.
    """
    if status == "awaiting_approval":
        return action_bar(
            button(
                "Approve",
                action_id=f"task:approve:{task_id}",
                value=task_id,
                style="primary",
            ),
            button(
                "Reject",
                action_id=f"task:reject:{task_id}",
                value=task_id,
                style="danger",
            ),
        )
    if status in {"done", "failed", "canceled", "expired", "rejected"}:
        return action_bar(
            button(
                "View artifacts",
                action_id=f"task:view:{task_id}",
                value=task_id,
            )
        )
    return action_bar(
        button(
            "Cancel task",
            action_id=f"task:cancel:{task_id}",
            value=task_id,
            style="danger",
        )
    )


def manifest_summary_actions() -> dict[str, Any]:
    """Return an action bar for manifest summary cards with post-backup follow-ups."""
    return action_bar(
        button(
            "Find duplicates",
            action_id="cmd:dedupe_report",
            value="dedupe_report",
        ),
        button(
            "View diff",
            action_id="cmd:diff_channel_files",
            value="diff_channel_files",
        ),
    )
