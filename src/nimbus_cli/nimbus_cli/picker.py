"""Interactive selectors used by `nimbus model`, profile pickers, etc.

This module provides a single function — :func:`select_one` — that renders an
arrow-key-navigable picker in the terminal using Rich's ``Live`` display.

The picker yields control to the calling code so callers can do their own
rendering and validation. It uses ``readchar`` for cross-platform single-key
reads (handles ANSI escape sequences for arrow keys on POSIX and the Windows
console key bytes uniformly). When ``readchar`` is unavailable or stdin is not
a TTY (CI, piped input), :func:`select_one` falls back to a numbered prompt
via :class:`rich.prompt.Prompt`.
"""

from __future__ import annotations

import contextlib
import select
import sys
import termios
import tty
from typing import cast

from rich.console import Console
from rich.live import Live
from rich.prompt import Prompt

from nimbus_cli import ui

# ── Key constants — ANSI / readchar values ────────────────────────────────


_KEY_UP = ("\x1b[A", "\x1bOA", "\x00H", "k")
_KEY_DOWN = ("\x1b[B", "\x1bOB", "\x00P", "j")
_KEY_ENTER = ("\r", "\n", "l")
_KEY_QUIT = ("\x1b", "\x03", "q", "h")  # Esc, Ctrl+C, q, Vim-left
_KEY_FIRST = ("g",)
_KEY_LAST = ("G",)
_ESCAPE_SEQUENCE_TIMEOUT_SECONDS = 0.01
_MAX_ESCAPE_SEQUENCE_BYTES = 8


def _read_one_key() -> str:
    """Read one key from stdin in cooked-or-raw mode.

    Uses a tiny POSIX raw-mode reader first so arrow keys work even when the
    optional ``readchar`` package is not installed. Falls back to ``readchar``
    and then line input. The caller is responsible for detecting non-TTY stdin
    and avoiding this path for normal picker use.
    """
    if sys.stdin.isatty():
        with contextlib.suppress(OSError, termios.error):
            return _read_posix_key()
    try:
        import readchar  # noqa: PLC0415
    except ImportError:
        return sys.stdin.readline().strip()
    return cast("str", readchar.readkey())


def _read_posix_key() -> str:
    """Read one key or ANSI escape sequence from a POSIX terminal."""
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        key = sys.stdin.read(1)
        if key != "\x1b":
            return key
        sequence = key
        while len(sequence) < _MAX_ESCAPE_SEQUENCE_BYTES:
            ready, _, _ = select.select(
                [sys.stdin],
                [],
                [],
                _ESCAPE_SEQUENCE_TIMEOUT_SECONDS,
            )
            if not ready:
                break
            sequence += sys.stdin.read(1)
        return sequence
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def select_one(  # noqa: C901 - key handling is clearer as one small loop.
    options: list[ui.SelectOption],
    *,
    title: str,
    console: Console | None = None,
    default_index: int = 0,
) -> str | None:
    """Render an interactive picker and return the chosen value.

    Returns the ``value`` field of the chosen :class:`SelectOption`, or
    ``None`` if the user cancels (Esc, Ctrl+C, q).
    """
    if not options:
        return None
    console = console or Console()

    # Non-TTY: fall back to a numbered Prompt so this works in CI and pipes.
    if not console.is_terminal:
        return _fallback_numbered_prompt(options, title=title, console=console)

    selected = max(0, min(default_index, len(options) - 1))
    with Live(
        ui.render_select_preview(options, selected=selected, title=title),
        console=console,
        refresh_per_second=20,
        transient=True,
    ) as live:
        while True:
            key = _read_one_key()
            if key in _KEY_QUIT:
                return None
            if key in _KEY_ENTER:
                return options[selected].value
            if key in _KEY_UP:
                selected = (selected - 1) % len(options)
            elif key in _KEY_DOWN:
                selected = (selected + 1) % len(options)
            elif key in _KEY_FIRST:
                selected = 0
            elif key in _KEY_LAST:
                selected = len(options) - 1
            # Single-letter direct-select hints (e.g. number 1..9)
            elif key.isdigit():
                idx = int(key) - 1
                if 0 <= idx < len(options):
                    selected = idx
            live.update(
                ui.render_select_preview(options, selected=selected, title=title)
            )


def _fallback_numbered_prompt(
    options: list[ui.SelectOption],
    *,
    title: str,
    console: Console,
) -> str | None:
    """Numbered prompt used when stdin is not a TTY (CI, piped input)."""
    console.print(ui.card(_render_numbered(options), title=title))
    choice = Prompt.ask(
        "Choose",
        choices=[str(i + 1) for i in range(len(options))],
        default="1",
        console=console,
    )
    return options[int(choice) - 1].value


def _render_numbered(options: list[ui.SelectOption]) -> str:
    """Plain numbered list used by the non-TTY fallback."""
    lines = []
    for idx, opt in enumerate(options):
        suffix = f"  ({opt.description})" if opt.description else ""
        lines.append(f"  {idx + 1}. {opt.label}{suffix}")
    return "\n".join(lines)
