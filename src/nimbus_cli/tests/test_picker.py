"""Tests for the interactive picker and curated model catalogue."""

from __future__ import annotations

import io

import pytest
from nimbus_cli.models_catalog import CUSTOM_VALUE, MODEL_CATALOG
from rich.console import Console

from nimbus_cli import picker, ui

pytestmark = pytest.mark.unit


def test_model_catalog_has_free_and_paid_tiers() -> None:
    """The catalogue should expose at least one free and one paid model."""
    groups = {m.group for m in MODEL_CATALOG}
    assert "Free" in groups
    assert "Paid" in groups


def test_model_catalog_first_free_entry_is_default() -> None:
    """The first Free entry should be marked as the default."""
    first_free = next(m for m in MODEL_CATALOG if m.group == "Free")
    assert "default" in first_free.description.lower()


def test_custom_value_is_a_sentinel_string() -> None:
    """The custom picker sentinel should be a stable non-empty string."""
    assert isinstance(CUSTOM_VALUE, str)
    assert CUSTOM_VALUE


def test_select_one_returns_none_on_empty_options() -> None:
    """Empty picker option sets should return None instead of prompting."""
    assert picker.select_one([], title="empty") is None


def test_select_one_non_tty_uses_numbered_prompt() -> None:
    """Non-TTY consoles should use a numbered prompt.

    The fallback goes through Rich's Prompt.ask and returns the chosen option's
    value.
    """
    buf = io.StringIO()
    console = Console(file=buf, force_terminal=False, width=80)
    options = [
        ui.SelectOption(label="Alpha", value="alpha"),
        ui.SelectOption(label="Beta", value="beta"),
    ]

    # Feed "2" to choose Beta.
    console.input = lambda *_args, **_kwargs: "2"  # type: ignore[method-assign]
    result = picker.select_one(options, title="Pick", console=console)

    assert result == "beta"


def test_render_numbered_includes_descriptions() -> None:
    """The fallback numbered list should include labels and descriptions."""
    options = [
        ui.SelectOption(label="One", value="o", description="first"),
        ui.SelectOption(label="Two", value="t"),
    ]
    rendered = picker._render_numbered(options)
    assert "One" in rendered
    assert "first" in rendered
    assert "Two" in rendered
