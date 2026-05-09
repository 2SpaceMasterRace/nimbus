"""Nimbus CLI design system.

A single source of truth for colors, icons, spacing, and component renderers
used by the Nimbus CLI. Inspired by Linear, Stripe, and Figma — quiet, dense,
and consistent. Every CLI surface should pull from this module.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from collections.abc import Iterator


# ── Color tokens ──────────────────────────────────────────────────────────
# Semantic names, not literal colors. Refactor the values; never the names.

PRIMARY = "cyan"
SUCCESS = "green"
WARNING = "yellow"
DANGER = "red"
INFO = "blue"
MUTED = "dim"
ACCENT = "magenta"

HEADING = f"bold {PRIMARY}"
LABEL = f"bold {MUTED}"
HINT = f"italic {MUTED}"
ERROR = f"bold {DANGER}"


# ── Icon set ──────────────────────────────────────────────────────────────
# A small, deliberate set. Unicode glyphs that render in every modern terminal.

ICON_OK = "✓"
ICON_FAIL = "✗"
ICON_WARN = "⚠"
ICON_INFO = "ⓘ"
ICON_ARROW = "→"
ICON_BULLET = "•"
ICON_TREE_BRANCH = "├─"
ICON_TREE_LAST = "└─"
ICON_TREE_PIPE = "│"
ICON_PENDING = "○"
ICON_ACTIVE = "●"
ICON_SAVE = "⬆"
ICON_DELETE = "⌫"
ICON_DOWNLOAD = "⬇"


# ── Status badges ─────────────────────────────────────────────────────────


def badge_ok(text: str = "OK") -> Text:
    """Green success badge."""
    return Text(f" {ICON_OK} {text} ", style=f"bold reverse {SUCCESS}")


def badge_fail(text: str = "FAIL") -> Text:
    """Red failure badge."""
    return Text(f" {ICON_FAIL} {text} ", style=f"bold reverse {DANGER}")


def badge_warn(text: str = "WARN") -> Text:
    """Yellow warning badge."""
    return Text(f" {ICON_WARN} {text} ", style=f"bold reverse {WARNING}")


def badge_info(text: str = "INFO") -> Text:
    """Blue informational badge."""
    return Text(f" {ICON_INFO} {text} ", style=f"bold reverse {INFO}")


_STATUS_STYLE = {
    "created": MUTED,
    "planning": INFO,
    "scanning": INFO,
    "diffing": INFO,
    "awaiting_approval": WARNING,
    "applying": INFO,
    "verifying": INFO,
    "done": SUCCESS,
    "failed": DANGER,
    "canceled": MUTED,
    "expired": MUTED,
    "rejected": DANGER,
}


def status_badge(status: str) -> Text:
    """Color-coded task status badge."""
    style = _STATUS_STYLE.get(status, MUTED)
    return Text(f" {status} ", style=f"bold reverse {style}")


# ── Layout helpers ────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class KV:
    """A label/value row for key-value tables. Use with `kv_table`."""

    label: str
    value: str | Text
    style: str = ""


def kv_table(rows: list[KV], *, padding: tuple[int, int] = (0, 1)) -> Table:
    """Two-column key-value table with right-aligned muted labels.

    Use this everywhere a banner, summary, or details panel is needed —
    it gives the Nimbus CLI its consistent look.
    """
    table = Table.grid(padding=padding)
    table.add_column(style=LABEL, justify="right", no_wrap=True)
    table.add_column()
    for row in rows:
        value = (
            row.value
            if isinstance(row.value, Text)
            else Text(row.value, style=row.style)
        )
        table.add_row(row.label, value)
    return table


def card(
    body: RenderableType,
    *,
    title: str | None = None,
    title_style: str = HEADING,
    border_style: str = PRIMARY,
    subtitle: str | None = None,
) -> Panel:
    """Render a standard Nimbus card with title and body.

    Cards are the primary unit of display. Every multi-line response should
    be inside a card so the CLI feels deliberate, not raw.
    """
    title_text = Text(title, style=title_style) if title else None
    subtitle_text = Text(subtitle, style=HINT) if subtitle else None
    return Panel(
        body,
        title=title_text,
        subtitle=subtitle_text,
        border_style=border_style,
        padding=(1, 2),
    )


def section_header(text: str) -> Text:
    """Render a section header for breaks within a card body."""
    return Text(f"  {text}  ", style=f"bold {MUTED} reverse")


def empty_state(message: str, *, hint: str | None = None) -> Panel:
    """Empty-state placeholder, e.g. when no tasks/artifacts exist yet."""
    body: RenderableType
    if hint:
        body = Group(
            Text(message, style=MUTED),
            Text(""),
            Text(hint, style=HINT),
        )
    else:
        body = Text(message, style=MUTED)
    return Panel(body, border_style=MUTED, padding=(1, 2))


# ── Progress / waiting ────────────────────────────────────────────────────


@contextmanager
def thinking(console: Console, text: str = "thinking…") -> Iterator[None]:
    """Live spinner that disappears when the block exits.

    Use this around any synchronous-looking call (`asyncio.run`, blocking HTTP)
    so the terminal never appears frozen.
    """
    spinner = Spinner("dots", text=Text(text, style=MUTED), style=PRIMARY)
    with Live(spinner, console=console, transient=True, refresh_per_second=10):
        yield


def make_progress_bar() -> Progress:
    """Render a standard progress bar with count and filename."""
    return Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=20, complete_style=PRIMARY, finished_style=SUCCESS),
        TextColumn("{task.completed}/{task.total}"),
        TextColumn("{task.fields[detail]}", style=MUTED),
    )


# ── Messages ──────────────────────────────────────────────────────────────


def success(console: Console, message: str) -> None:
    """Print a success line."""
    console.print(Text(f"{ICON_OK} ", style=SUCCESS) + Text(message))


def error(console: Console, message: str, *, hint: str | None = None) -> None:
    """Print an error line, optionally followed by a recovery hint."""
    console.print(Text(f"{ICON_FAIL} ", style=DANGER) + Text(message, style=ERROR))
    if hint:
        console.print(Text(f"  {hint}", style=HINT))


def warn(console: Console, message: str) -> None:
    """Print a warning line."""
    console.print(Text(f"{ICON_WARN} ", style=WARNING) + Text(message, style=WARNING))


def info(console: Console, message: str) -> None:
    """Print an informational line."""
    console.print(Text(f"{ICON_INFO} ", style=INFO) + Text(message))


def hint(console: Console, message: str) -> None:
    """Print a muted hint line."""
    console.print(Text(f"  {message}", style=HINT))


# ── Prompts ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SelectOption:
    """One row in a `select_one` picker."""

    label: str
    value: str
    description: str | None = None
    group: str | None = None


def render_select_preview(
    options: list[SelectOption],
    *,
    selected: int,
    title: str,
    footer: str = "↑↓/j k navigate  •  Enter/l select  •  Esc/h cancel",
) -> Panel:
    """Render a select-one picker as a panel.

    The actual key handling lives in `picker.py` — this is the visual.
    """
    rendered: list[Text] = []
    current_group: str | None = None
    for idx, opt in enumerate(options):
        if opt.group and opt.group != current_group:
            if rendered:
                rendered.append(Text(""))
            rendered.append(Text(f"  ── {opt.group}", style=LABEL))
            current_group = opt.group
        marker = ICON_ACTIVE if idx == selected else ICON_PENDING
        marker_style = PRIMARY if idx == selected else MUTED
        label_style = "bold" if idx == selected else ""
        line = Text(f"  {marker} ", style=marker_style)
        line.append(opt.label, style=label_style)
        if opt.description:
            line.append(f"   {opt.description}", style=HINT)
        rendered.append(line)
    body = Group(*rendered, Text(""), Text(footer, style=HINT))
    return card(body, title=title)


# ── Confirmation prompts ──────────────────────────────────────────────────


def render_confirmation(
    *,
    action: str,
    target: str,
    expected_reply: str,
    expires_at: str | None = None,
) -> Panel:
    """Render the destructive-action confirmation block.

    Used by the runtime confirmation flow — the user must type the exact
    `expected_reply` to proceed.
    """
    rows: list[Text] = [
        Text(f"{ICON_WARN} ", style=WARNING)
        + Text("Destructive action requires confirmation", style=f"bold {WARNING}"),
        Text(""),
    ]
    rows.append(Text(f"  {action}  ", style="bold") + Text(target, style=DANGER))
    rows.append(Text(""))
    rows.append(Text("  Type exactly to confirm:", style=MUTED))
    rows.append(Text(f"    {expected_reply}", style=f"bold {PRIMARY}"))
    if expires_at:
        rows.append(Text(""))
        rows.append(Text(f"  Expires {expires_at}", style=HINT))
    return Panel(
        Group(*rows),
        border_style=WARNING,
        padding=(1, 2),
    )


_TYPE_ICON = {
    "delete_file": ICON_DELETE,
    "upload_attachment": ICON_SAVE,
    "list_files": ICON_BULLET,
}


def action_line(*, kind: str, target: str, status: str) -> Text:
    """One-line action summary, used in result tails."""
    icon = _TYPE_ICON.get(kind, ICON_BULLET)
    status_style = (
        SUCCESS if status == "completed" else (DANGER if status == "failed" else MUTED)
    )
    line = Text(f"  {icon} ", style=PRIMARY)
    line.append(kind, style=LABEL)
    line.append(f"  {target}", style="")
    line.append(f"  ({status})", style=status_style)
    return line


def artifact_line(*, kind: str, artifact_id: str) -> Text:
    """One-line artifact summary."""
    line = Text(f"  {ICON_INFO} ", style=INFO)
    line.append("artifact ", style=LABEL)
    line.append(f"{kind} ", style="")
    line.append(f"→ {artifact_id}", style=HINT)
    return line


# ── Result rendering ──────────────────────────────────────────────────────


def render_result(  # noqa: PLR0913 - Result rendering mirrors runtime fields.
    console: Console,
    *,
    text: str,
    outcome: str,
    confirmation: object | None = None,
    actions: tuple[object, ...] = (),
    artifacts: tuple[object, ...] = (),
) -> None:
    """Render a complete `ChatTurnResult`.

    Replaces the previous "print result.text only" path. Confirmation prompts,
    actions, and artifacts are always shown so users never wonder what just
    happened.
    """
    from rich.markdown import Markdown  # noqa: PLC0415 — local for snappier import

    if text:
        console.print(Markdown(text))

    # Render confirmation block if present.
    if confirmation is not None:
        action_kind = getattr(confirmation, "kind", "action").replace("_", " ")
        expected = getattr(confirmation, "expected_reply", "")
        prompt = getattr(confirmation, "prompt", "")
        expires = getattr(confirmation, "expires_at", None)
        # The prompt usually already contains target/action context; extract
        # action+target if available, otherwise show the prompt verbatim.
        console.print(
            render_confirmation(
                action=action_kind,
                target=prompt,
                expected_reply=expected,
                expires_at=expires,
            )
        )

    # Render actions.
    if actions:
        console.print()
        console.print(section_header("Actions"))
        for action in actions:
            console.print(
                action_line(
                    kind=getattr(action, "kind", "?"),
                    target=_target_text(getattr(action, "target", None)),
                    status=getattr(action, "status", "?"),
                )
            )

    # Render artifacts.
    if artifacts:
        console.print()
        console.print(section_header("Artifacts"))
        for artifact in artifacts:
            console.print(
                artifact_line(
                    kind=getattr(artifact, "kind", "?"),
                    artifact_id=getattr(artifact, "artifact_id", "?"),
                )
            )

    # Outcome footer if something interesting happened.
    if outcome not in {"reply", "error"}:
        console.print()
        console.print(Text(f"  outcome: {outcome}", style=HINT))


def _target_text(target: object) -> str:
    """Format an ObjectRef-like dict or string into a single line."""
    if target is None:
        return ""
    if isinstance(target, str):
        return target
    if isinstance(target, dict):
        name = target.get("object_name") or target.get("name") or "?"
        container = target.get("container")
        if container:
            return f"{container}/{name}"
        return str(name)
    return repr(target)


# ── Type-supporting display values ────────────────────────────────────────


Tone = Literal["primary", "success", "warning", "danger", "info", "muted"]


_TONE_STYLES: dict[Tone, str] = {
    "primary": PRIMARY,
    "success": SUCCESS,
    "warning": WARNING,
    "danger": DANGER,
    "info": INFO,
    "muted": MUTED,
}


def tone_style(tone: Tone) -> str:
    """Look up a Rich style string for a semantic tone name."""
    return _TONE_STYLES[tone]


# ── Live-watch panel ───────────────────────────────────────────────────────

ICON_CLOCK = "⊙"
ICON_PLAN = "◈"

_SECONDS_PER_MINUTE = 60
_INTENT_PREVIEW_CHARS = 70


def elapsed_text(seconds: float) -> Text:
    """Format elapsed seconds as a compact human-readable duration."""
    if seconds < _SECONDS_PER_MINUTE:
        return Text(f"{seconds:.0f}s", style=MUTED)
    minutes = int(seconds / _SECONDS_PER_MINUTE)
    secs = int(seconds % _SECONDS_PER_MINUTE)
    return Text(f"{minutes}m {secs:02d}s", style=MUTED)


_EVENT_TYPE_ICONS: dict[str, str] = {
    "task_created": ICON_BULLET,
    "task_started": ICON_ACTIVE,
    "task_planning": ICON_PLAN,
    "task_scanning": ICON_ARROW,
    "task_diffing": "≠",
    "task_awaiting_approval": ICON_WARN,
    "task_applying": ICON_SAVE,
    "task_verifying": ICON_INFO,
    "task_done": ICON_OK,
    "task_failed": ICON_FAIL,
    "task_canceled": ICON_DELETE,
    "approval_requested": ICON_WARN,
    "approval_granted": ICON_OK,
    "approval_rejected": ICON_FAIL,
    "artifact_created": ICON_INFO,
    "action_completed": ICON_OK,
    "action_failed": ICON_FAIL,
}


def event_type_icon(event_type: str) -> str:
    """Return a UI icon for a named task event type."""
    return _EVENT_TYPE_ICONS.get(event_type, ICON_BULLET)


def live_task_panel(  # noqa: PLR0913 — watch panel needs all context fields.
    *,
    task_id: str,
    status: str,
    intent: str,
    elapsed: float | None = None,
    status_history: list[tuple[str, str]] | None = None,
    poll_interval: float = 2.0,
) -> Panel:
    """Build a compact task-watch panel for use inside a Rich Live display."""
    kv_rows: list[KV] = [
        KV("task", task_id),
        KV("status", status_badge(status)),
        KV(
            "intent",
            (intent[:_INTENT_PREVIEW_CHARS] + "…")
            if len(intent) > _INTENT_PREVIEW_CHARS
            else intent,
        ),
    ]
    if elapsed is not None:
        kv_rows.append(KV("elapsed", elapsed_text(elapsed)))

    rows: list[RenderableType] = [kv_table(kv_rows)]

    if status_history:
        rows.append(Text(""))
        for ts, s in status_history[-4:]:
            style = _STATUS_STYLE.get(s, MUTED)
            line = Text(f"  {ts}  ", style=HINT)
            line.append(f" {s} ", style=f"bold reverse {style}")
            rows.append(line)

    rows.extend(
        [
            Text(""),
            Text(
                f"  {ICON_INFO} Ctrl+C to stop  •  polls every {poll_interval:.0f}s",
                style=HINT,
            ),
        ]
    )

    return card(Group(*rows), title="Nimbus  •  watch")
