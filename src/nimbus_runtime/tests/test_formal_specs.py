"""Regression checks for the repository formal-methods artifacts."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from nimbus_runtime.replay import runtime_status_spec

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TLA_SPEC = _REPO_ROOT / "formal" / "tla" / "NimbusActionLedger.tla"
_LEAN_SPEC = _REPO_ROOT / "formal" / "lean" / "Nimbus" / "ActionLedger.lean"


def test_tla_status_domains_match_runtime_status_spec() -> None:
    """TLA+ status sets must stay in lock-step with replay trace metadata."""
    text = _TLA_SPEC.read_text(encoding="utf-8")
    statuses = runtime_status_spec()["statuses"]
    assert _tla_set(text, "ActionStatuses") == set(statuses["action"])
    assert _tla_set(text, "ApprovalStatuses") == set(statuses["approval"])
    assert _tla_set(text, "GenerationStatuses") == set(statuses["generation"])
    assert _tla_set(text, "StackStatuses") == set(statuses["stack"])


def test_lean_status_domains_match_runtime_status_spec() -> None:
    """Lean inductives must name the same states as the Python runtime."""
    text = _LEAN_SPEC.read_text(encoding="utf-8")
    statuses = runtime_status_spec()["statuses"]
    assert _lean_cases(text, "ActionStatus") == set(statuses["action"])
    assert _lean_cases(text, "ApprovalStatus") == set(statuses["approval"])
    assert _lean_cases(text, "GenerationStatus") == set(statuses["generation"])
    assert _lean_cases(text, "StackStatus") == set(statuses["stack"])


def test_formal_specs_name_terminal_transition_invariants() -> None:
    """The MVP formal files should expose the terminal-state safety contract."""
    tla = _TLA_SPEC.read_text(encoding="utf-8")
    lean = _LEAN_SPEC.read_text(encoding="utf-8")
    assert "NoTerminalActionStep" in tla
    assert "ApprovalDecisionIsTerminal" in tla
    assert "succeeded_terminal" in lean
    assert "approval_rejected_terminal" in lean


def _tla_set(text: str, name: str) -> set[str]:
    match = re.search(rf"{name}\s*==\s*\{{(?P<body>.*?)\}}", text, re.DOTALL)
    assert match is not None, f"{name} not found"
    return set(re.findall(r'"([^"]+)"', match.group("body")))


def _lean_cases(text: str, name: str) -> set[str]:
    match = re.search(
        rf"inductive {name} where(?P<body>.*?)deriving",
        text,
        re.DOTALL,
    )
    assert match is not None, f"{name} not found"
    cases = re.findall(
        r"^\s*\|\s+(?:«([^»]+)»|([A-Za-z0-9_]+))",
        match.group("body"),
        re.MULTILINE,
    )
    return {escaped or plain for escaped, plain in cases}
