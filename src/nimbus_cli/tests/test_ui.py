"""Tests for the Nimbus CLI design system module."""

from __future__ import annotations

import io
from typing import ClassVar

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from nimbus_cli import ui

pytestmark = pytest.mark.unit


def _render(renderable: object) -> str:
    """Render any Rich renderable to a plain string."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80, no_color=True)
    console.print(renderable)
    return buf.getvalue()


# ── Color tokens ──────────────────────────────────────────────────────────


def test_color_tokens_are_strings() -> None:
    """All tokens should be strings so they can be interpolated into styles."""
    for name in ("PRIMARY", "SUCCESS", "WARNING", "DANGER", "INFO", "MUTED", "ACCENT"):
        assert isinstance(getattr(ui, name), str)


def test_tone_style_round_trip() -> None:
    """`tone_style` should map every semantic tone to a non-empty style."""
    for tone in ("primary", "success", "warning", "danger", "info", "muted"):
        assert ui.tone_style(tone) != ""  # type: ignore[arg-type]


# ── Badges ────────────────────────────────────────────────────────────────


def test_badge_ok_contains_check_icon() -> None:
    """Success badges should include the check icon and provided label."""
    out = _render(ui.badge_ok("ready"))
    assert ui.ICON_OK in out
    assert "ready" in out


def test_status_badge_uses_known_status() -> None:
    """Known statuses should render their label text."""
    assert "done" in _render(ui.status_badge("done"))
    assert "applying" in _render(ui.status_badge("applying"))


def test_status_badge_falls_back_to_muted_for_unknown_status() -> None:
    """Unknown statuses should still render without crashing."""
    out = _render(ui.status_badge("nonsense"))
    assert "nonsense" in out


# ── KV table ──────────────────────────────────────────────────────────────


def test_kv_table_renders_label_and_value() -> None:
    """Key-value tables should render both labels and values."""
    table = ui.kv_table(
        [
            ui.KV("profile", "local"),
            ui.KV("model", "gpt-4o-mini"),
        ]
    )
    assert isinstance(table, Table)
    out = _render(table)
    assert "profile" in out
    assert "local" in out
    assert "model" in out
    assert "gpt-4o-mini" in out


def test_kv_table_accepts_text_value() -> None:
    """Values can be either plain strings or pre-styled Text instances."""
    table = ui.kv_table([ui.KV("status", Text("active", style="green"))])
    out = _render(table)
    assert "active" in out


# ── Cards ─────────────────────────────────────────────────────────────────


def test_card_returns_panel_with_title() -> None:
    """Cards should be Rich panels that render their title and body."""
    panel = ui.card(Text("hi"), title="Welcome")
    assert isinstance(panel, Panel)
    out = _render(panel)
    assert "Welcome" in out
    assert "hi" in out


def test_empty_state_renders_message_and_hint() -> None:
    """Empty states should render both the message and optional hint."""
    panel = ui.empty_state("Nothing here yet.", hint="Run `nimbus chat`.")
    out = _render(panel)
    assert "Nothing here yet." in out
    assert "Run `nimbus chat`." in out


# ── Inline messages ───────────────────────────────────────────────────────


def test_success_prints_check_and_message() -> None:
    """Success helper should print the check icon and message."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80, no_color=True)
    ui.success(console, "saved")
    out = buf.getvalue()
    assert ui.ICON_OK in out
    assert "saved" in out


def test_error_prints_x_and_hint() -> None:
    """Error helper should print the failure icon, message, and hint."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80, no_color=True)
    ui.error(console, "broken", hint="run `nimbus doctor`")
    out = buf.getvalue()
    assert ui.ICON_FAIL in out
    assert "broken" in out
    assert "run `nimbus doctor`" in out


def test_warn_and_info_render() -> None:
    """Warning and info helpers should print their messages."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80, no_color=True)
    ui.warn(console, "watch out")
    ui.info(console, "fyi")
    out = buf.getvalue()
    assert "watch out" in out
    assert "fyi" in out


# ── Select picker ─────────────────────────────────────────────────────────


def test_render_select_preview_highlights_selected() -> None:
    """Select preview should include title, options, and descriptions."""
    options = [
        ui.SelectOption(label="One", value="one"),
        ui.SelectOption(label="Two", value="two", description="the second"),
    ]
    panel = ui.render_select_preview(options, selected=1, title="Pick")
    out = _render(panel)
    assert "Pick" in out
    assert "One" in out
    assert "Two" in out
    assert "the second" in out


def test_render_select_preview_groups_options() -> None:
    """Select preview should render group labels."""
    options = [
        ui.SelectOption(label="Free model", value="m1", group="Free"),
        ui.SelectOption(label="Paid model", value="m2", group="Paid"),
    ]
    panel = ui.render_select_preview(options, selected=0, title="Models")
    out = _render(panel)
    assert "Free" in out
    assert "Paid" in out


# ── Confirmation ──────────────────────────────────────────────────────────


def test_render_confirmation_shows_expected_reply() -> None:
    """Confirmation panels should include the exact expected reply."""
    panel = ui.render_confirmation(
        action="delete",
        target="reports/q1.csv",
        expected_reply="yes, delete reports/q1.csv",
        expires_at="2026-05-17T14:33Z",
    )
    out = _render(panel)
    assert "delete" in out
    assert "reports/q1.csv" in out
    assert "yes, delete reports/q1.csv" in out
    assert "2026-05-17T14:33Z" in out


# ── Action / artifact lines ───────────────────────────────────────────────


def test_action_line_renders_kind_and_status() -> None:
    """Action summaries should include kind, target, and status."""
    line = ui.action_line(
        kind="delete_file",
        target="bucket/file.csv",
        status="completed",
    )
    out = _render(line)
    assert "delete_file" in out
    assert "bucket/file.csv" in out
    assert "completed" in out


def test_artifact_line_renders_id() -> None:
    """Artifact summaries should include kind and artifact ID."""
    line = ui.artifact_line(kind="delete_report", artifact_id="art-abc")
    out = _render(line)
    assert "delete_report" in out
    assert "art-abc" in out


# ── Full result rendering ─────────────────────────────────────────────────


class _StubConfirmation:
    """Stand-in for ConfirmationDetails — pure attribute carrier."""

    kind = "delete_file"
    prompt = "Confirm delete of reports/x.csv"
    expected_reply = "yes, delete reports/x.csv"
    expires_at = "2026-05-17T14:33Z"


class _StubAction:
    """Stand-in for ActionSummary — pure attribute carrier."""

    kind = "delete_file"
    status = "completed"
    target: ClassVar[dict[str, str]] = {
        "container": "my-bucket",
        "object_name": "reports/x.csv",
    }


class _StubArtifact:
    """Stand-in for ArtifactSummary — pure attribute carrier."""

    kind = "delete_report"
    artifact_id = "art-123"


def test_render_result_shows_text_actions_artifacts_and_confirmation() -> None:
    """Result rendering should show text, confirmations, actions, and artifacts."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=120, no_color=True)
    ui.render_result(
        console,
        text="**Deleted** the file.",
        outcome="reply",
        confirmation=_StubConfirmation(),
        actions=(_StubAction(),),
        artifacts=(_StubArtifact(),),
    )
    out = buf.getvalue()
    assert "Deleted" in out
    assert "yes, delete reports/x.csv" in out
    assert "delete_file" in out
    assert "my-bucket/reports/x.csv" in out
    assert "art-123" in out


def test_render_result_omits_empty_sections() -> None:
    """Empty/None fields should produce no output for that section."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80, no_color=True)
    ui.render_result(
        console,
        text="just text",
        outcome="reply",
    )
    out = buf.getvalue()
    assert "just text" in out
    # No confirmation, action, or artifact markers
    assert "Type exactly" not in out
    assert "artifact" not in out


def test_render_result_shows_outcome_for_non_reply_states() -> None:
    """Non-reply outcomes should render a visible status badge."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80, no_color=True)
    ui.render_result(
        console,
        text="",
        outcome="confirmation_required",
        confirmation=_StubConfirmation(),
    )
    out = buf.getvalue()
    assert "confirmation_required" in out


# ── Progress / thinking ───────────────────────────────────────────────────


def test_thinking_yields_and_exits_cleanly() -> None:
    """The thinking context manager should run its body and clean up."""
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80, no_color=True)
    entered = False
    with ui.thinking(console, "loading…"):
        entered = True
    assert entered


def test_make_progress_bar_returns_progress_instance() -> None:
    """Progress factory should return a usable Rich Progress instance."""
    progress = ui.make_progress_bar()
    # Add a task and confirm it works through Rich's API
    task_id = progress.add_task("scanning", total=10, detail="files")
    progress.update(task_id, advance=5)
    assert progress.tasks[0].completed == 5


# ── Live-watch panel ───────────────────────────────────────────────────────


def test_elapsed_text_under_one_minute() -> None:
    """Elapsed seconds under 60 should render as '{n}s'."""
    out = _render(ui.elapsed_text(45.0))
    assert "45s" in out


def test_elapsed_text_over_one_minute() -> None:
    """Elapsed seconds over 60 should render as '{m}m {s}s'."""
    out = _render(ui.elapsed_text(90.0))
    assert "1m" in out
    assert "30s" in out


def test_event_type_icon_known_type() -> None:
    """Known event types should return a non-empty icon character."""
    icon = ui.event_type_icon("task_done")
    assert icon == ui.ICON_OK


def test_event_type_icon_unknown_type_falls_back_to_bullet() -> None:
    """Unknown event types should return the bullet fallback."""
    assert ui.event_type_icon("whatever") == ui.ICON_BULLET


def test_live_task_panel_renders_task_fields() -> None:
    """Live task panel should render task ID, status, and intent."""
    panel = ui.live_task_panel(
        task_id="task-abc",
        status="applying",
        intent="Save channel files to S3",
        elapsed=30.0,
    )
    out = _render(panel)
    assert "task-abc" in out
    assert "applying" in out
    assert "Save channel files" in out
    assert "30s" in out


def test_live_task_panel_truncates_long_intent() -> None:
    """Intents longer than _INTENT_PREVIEW_CHARS should be truncated."""
    long_intent = "a" * 100
    panel = ui.live_task_panel(task_id="t", status="done", intent=long_intent)
    out = _render(panel)
    assert "…" in out


def test_live_task_panel_shows_status_history() -> None:
    """Status history entries should appear in the panel."""
    panel = ui.live_task_panel(
        task_id="t",
        status="done",
        intent="test",
        status_history=[("10:00:00", "scanning"), ("10:01:00", "done")],
    )
    out = _render(panel)
    assert "scanning" in out
    assert "done" in out
