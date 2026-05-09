"""Tests for the Nimbus Slack design system primitives."""

from __future__ import annotations

import pytest

from nimbus_slack import design

pytestmark = pytest.mark.unit

# ── Primitive builders ──────────────────────────────────────────────────────


def test_header_produces_plain_text_block() -> None:
    block = design.header("Welcome")
    assert block["type"] == "header"
    assert block["text"]["type"] == "plain_text"
    assert block["text"]["text"] == "Welcome"
    assert block["text"]["emoji"] is False


def test_section_produces_mrkdwn_block() -> None:
    block = design.section("**hi**")
    assert block["type"] == "section"
    assert block["text"]["type"] == "mrkdwn"
    assert block["text"]["text"] == "**hi**"


def test_context_collects_lines() -> None:
    block = design.context("one", "two")
    assert block["type"] == "context"
    assert [e["text"] for e in block["elements"]] == ["one", "two"]


def test_divider_returns_block() -> None:
    assert design.divider() == {"type": "divider"}


def test_fields_block() -> None:
    block = design.fields("*Saved*\n5", "*Failed*\n0")
    assert block["type"] == "section"
    assert len(block["fields"]) == 2
    assert block["fields"][0]["text"] == "*Saved*\n5"


# ── Buttons ──────────────────────────────────────────────────────────────────


def test_button_value_only() -> None:
    btn = design.button("Click", action_id="click", value="x")
    assert btn["type"] == "button"
    assert btn["text"]["text"] == "Click"
    assert btn["action_id"] == "click"
    assert btn["value"] == "x"
    assert "url" not in btn


def test_button_url_only() -> None:
    btn = design.button("Open", action_id="open", url="https://example.com")
    assert btn["url"] == "https://example.com"
    assert "value" not in btn


def test_button_style_primary() -> None:
    btn = design.button("Go", action_id="go", value="go", style="primary")
    assert btn["style"] == "primary"


def test_button_default_style_is_omitted() -> None:
    """Slack treats the absence of `style` as default — don't emit it."""
    btn = design.button("Plain", action_id="x", value="y", style="default")
    assert "style" not in btn


def test_button_requires_one_of_value_or_url() -> None:
    with pytest.raises(ValueError, match="exactly one of"):
        design.button("X", action_id="a")
    with pytest.raises(ValueError, match="exactly one of"):
        design.button("X", action_id="a", value="v", url="https://x.com")


def test_link_button_helper() -> None:
    btn = design.link_button("Docs", "https://docs.nimbus.test")
    assert btn["url"] == "https://docs.nimbus.test"
    assert btn["action_id"] == "open_link"


def test_action_bar_wraps_buttons() -> None:
    bar = design.action_bar(
        design.button("A", action_id="a", value="a"),
        design.button("B", action_id="b", value="b"),
    )
    assert bar["type"] == "actions"
    assert len(bar["elements"]) == 2


# ── Branded header ──────────────────────────────────────────────────────────


def test_branded_header_with_ok_status_stays_plain() -> None:
    block = design.branded_header("Done", status="ok")
    assert block["text"]["text"] == "Done"


def test_branded_header_with_unknown_status_omits_prefix() -> None:
    block = design.branded_header("Plain", status="weird")
    assert block["text"]["text"] == "Plain"


def test_branded_header_without_status() -> None:
    block = design.branded_header("Just a header")
    assert block["text"]["text"] == "Just a header"


# ── Setup card ──────────────────────────────────────────────────────────────


def test_setup_card_includes_install_button() -> None:
    blocks = design.setup_card(install_url="https://nimbus.test/slack/install")
    # Find the action bar
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(action_blocks) == 1
    buttons = action_blocks[0]["elements"]
    assert any(b.get("url") == "https://nimbus.test/slack/install" for b in buttons)


def test_setup_card_includes_docs_button_when_provided() -> None:
    blocks = design.setup_card(
        install_url="https://nimbus.test/install",
        docs_url="https://docs.nimbus.test",
    )
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    urls = [b.get("url") for b in action_blocks[0]["elements"]]
    assert "https://docs.nimbus.test" in urls


def test_setup_card_no_docs_button_by_default() -> None:
    blocks = design.setup_card(install_url="https://nimbus.test/install")
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(action_blocks[0]["elements"]) == 1


def test_setup_card_install_button_is_primary() -> None:
    blocks = design.setup_card(install_url="https://nimbus.test/install")
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    install_btn = action_blocks[0]["elements"][0]
    assert install_btn.get("style") == "primary"


# ── Thinking / error cards ──────────────────────────────────────────────────


def test_thinking_card_uses_plain_placeholder_text() -> None:
    blocks = design.thinking_card()
    assert blocks[0]["type"] == "section"
    assert "Scanning files" in blocks[0]["text"]["text"]
    assert design.EMOJI_PENDING not in blocks[0]["text"]["text"]


def test_thinking_card_uses_custom_text() -> None:
    blocks = design.thinking_card("doing things")
    assert "doing things" in blocks[0]["text"]["text"]


def test_error_card_with_retry_hint() -> None:
    blocks = design.error_card(
        title="Save failed",
        detail="The bucket was not found.",
        retry_hint="Run `@Nimbus setup`",
    )
    assert blocks[0]["type"] == "header"
    assert "Save failed" in blocks[0]["text"]["text"]
    assert any("Run `@Nimbus setup`" in str(b) for b in blocks)


def test_error_card_without_retry_hint() -> None:
    blocks = design.error_card(title="X", detail="y")
    # Two blocks: header + section. No context block when no hint.
    assert len(blocks) == 2


# ── File card action bars ───────────────────────────────────────────────────


def test_file_list_actions_includes_save_dedupe_diff() -> None:
    bar = design.file_list_actions()
    action_ids = [b["action_id"] for b in bar["elements"]]
    assert "cmd:save_channel_files" in action_ids
    assert "cmd:dedupe_report" in action_ids
    assert "cmd:diff_channel_files" in action_ids


def test_file_list_actions_save_can_be_disabled() -> None:
    bar = design.file_list_actions(can_save=False)
    action_ids = [b["action_id"] for b in bar["elements"]]
    assert "cmd:save_channel_files" not in action_ids


def test_save_report_actions_includes_retry_when_failures() -> None:
    bar = design.save_report_actions(has_failures=True)
    action_ids = [b["action_id"] for b in bar["elements"]]
    assert "cmd:retry_save" in action_ids


def test_save_report_actions_no_retry_when_clean() -> None:
    bar = design.save_report_actions(has_failures=False)
    action_ids = [b["action_id"] for b in bar["elements"]]
    assert "cmd:retry_save" not in action_ids


def test_diff_report_actions_has_save_primary() -> None:
    bar = design.diff_report_actions()
    save_btn = next(
        b for b in bar["elements"] if b["action_id"] == "cmd:save_channel_files"
    )
    assert save_btn["style"] == "primary"


# ── Thinking card steps ─────────────────────────────────────────────────────


def test_thinking_card_with_steps_adds_context_block() -> None:
    blocks = design.thinking_card("Scanning…", steps=["Scan", "Save", "Verify"])
    assert len(blocks) == 2
    assert blocks[1]["type"] == "context"


def test_thinking_card_steps_marks_current_step_running() -> None:
    blocks = design.thinking_card("…", steps=["A", "B", "C"], current_step=1)
    context_text = blocks[1]["elements"][0]["text"]
    assert "Now: *B*" in context_text


def test_thinking_card_steps_marks_past_steps_done() -> None:
    blocks = design.thinking_card("…", steps=["A", "B"], current_step=1)
    context_text = blocks[1]["elements"][0]["text"]
    assert "Done: ~A~" in context_text


def test_thinking_card_no_steps_unchanged() -> None:
    blocks = design.thinking_card("working…")
    assert len(blocks) == 1
    assert blocks[0]["type"] == "section"


# ── Task action bar ──────────────────────────────────────────────────────────


def test_task_action_bar_awaiting_shows_approve_reject() -> None:
    bar = design.task_action_bar(task_id="t-1", status="awaiting_approval")
    action_ids = [b["action_id"] for b in bar["elements"]]
    assert "task:approve:t-1" in action_ids
    assert "task:reject:t-1" in action_ids


def test_task_action_bar_approve_is_primary_reject_is_danger() -> None:
    bar = design.task_action_bar(task_id="t-2", status="awaiting_approval")
    by_id = {b["action_id"]: b for b in bar["elements"]}
    assert by_id["task:approve:t-2"]["style"] == "primary"
    assert by_id["task:reject:t-2"]["style"] == "danger"


def test_task_action_bar_terminal_shows_view_artifacts() -> None:
    for status in ("done", "failed", "canceled", "expired", "rejected"):
        bar = design.task_action_bar(task_id="t-3", status=status)
        action_ids = [b["action_id"] for b in bar["elements"]]
        assert "task:view:t-3" in action_ids


def test_task_action_bar_active_shows_cancel() -> None:
    bar = design.task_action_bar(task_id="t-4", status="applying")
    action_ids = [b["action_id"] for b in bar["elements"]]
    assert "task:cancel:t-4" in action_ids


# ── Manifest summary actions ─────────────────────────────────────────────────


def test_manifest_summary_actions_includes_dedupe_and_diff() -> None:
    bar = design.manifest_summary_actions()
    assert bar["type"] == "actions"
    action_ids = [b["action_id"] for b in bar["elements"]]
    assert "cmd:dedupe_report" in action_ids
    assert "cmd:diff_channel_files" in action_ids


# ── Legacy status tokens ────────────────────────────────────────────────────


def test_legacy_status_tokens_are_strings() -> None:
    """Older imports still resolve while new Slack copy stays plain."""
    for name in (
        "EMOJI_OK",
        "EMOJI_FAIL",
        "EMOJI_WARN",
        "EMOJI_INFO",
        "EMOJI_PENDING",
        "EMOJI_RUNNING",
        "EMOJI_SCAN",
        "EMOJI_SAVE",
        "EMOJI_FILES",
    ):
        assert isinstance(getattr(design, name), str)
        assert getattr(design, name)  # non-empty
