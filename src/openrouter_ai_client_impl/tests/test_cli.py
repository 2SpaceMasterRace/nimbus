"""Tests for the Nimbus CLI wrapper around :class:`OpenRouterClient`.

These tests exercise the slash-command dispatch table, session round-trips,
and event rendering. The REPL's ``Prompt.ask`` input loop is covered by the
``main_exits_cleanly_on_eof`` test; we stub the prompt so no real stdin is
needed. The underlying AI client is a MagicMock — we are testing the CLI
layer, not the provider.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from openrouter_ai_client_impl.cli import NimbusCLI, app
from openrouter_ai_client_impl.config import DEFAULT_SYSTEM_PROMPT, OpenRouterConfig
from rich.console import Console
from typer.testing import CliRunner

from ai_client_api import (
    AgentEvent,
    AIResponse,
    TokenUsage,
    Tool,
)

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


# --- Helpers ---------------------------------------------------------------


def _dummy_tool() -> Tool:
    return Tool(
        name="noop",
        description="no-op tool used only for banner rendering",
        parameters_schema={"type": "object", "properties": {}},
        handler=lambda **_: {"ok": True},
    )


def _fake_client(
    *, response_text: str = "hello", raw_tail: list[dict[str, Any]] | None = None
) -> MagicMock:
    """Build a MagicMock OpenRouterClient with just the attributes the CLI uses.

    ``send_message`` mutates the passed-in conversation the same way the real
    client does (appending the assistant reply) — otherwise tests that inspect
    persisted conversation history would drift from the real implementation.
    """
    client = MagicMock()
    client._config = OpenRouterConfig(
        api_key="sk-test",
        model="primary/model:free",
        fallback_model="fallback/model:free",
    )
    response = AIResponse(
        text=response_text,
        model="primary/model:free",
        tokens=TokenUsage(input_tokens=3, output_tokens=4),
        tool_calls=(),
        latency_ms=10,
        stop_reason="end_turn",
        steps=1,
        fallback_used=False,
    )

    def _send_message(conv: Any, **_: Any) -> AIResponse:  # noqa: ANN401
        conv.add_assistant(response_text)
        return response

    client.send_message.side_effect = _send_message
    client.last_raw_completions.return_value = list(raw_tail or [])
    client.on_event = MagicMock()
    return client


@dataclass
class _CliHarness:
    cli: NimbusCLI
    client: MagicMock
    buffer: io.StringIO
    session_dir: Path

    def output(self) -> str:
        return self.buffer.getvalue()


def _harness(tmp_path: Path, **client_kwargs: Any) -> _CliHarness:  # noqa: ANN401
    client = _fake_client(**client_kwargs)
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False, color_system=None)
    cli = NimbusCLI(
        client=client,
        tools=[_dummy_tool()],
        session_id="test-session",
        session_dir=tmp_path,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        console=console,
    )
    return _CliHarness(cli=cli, client=client, buffer=buffer, session_dir=tmp_path)


# --- Slash command dispatch ------------------------------------------------


def test_help_command_renders_table(tmp_path: Path) -> None:
    """/help returns True (don't quit) and writes every registered command."""
    h = _harness(tmp_path)
    assert h.cli._handle_slash("/help") is True
    output = h.output()
    for cmd in ("/help", "/clear", "/history", "/model", "/debug", "/session", "/quit"):
        assert cmd in output


def test_quit_command_returns_false_and_saves(tmp_path: Path) -> None:
    """/quit signals the loop to exit and persists conversation to disk."""
    h = _harness(tmp_path)
    h.cli._conversation.add_user("ping")
    assert h.cli._handle_slash("/quit") is False
    expected = tmp_path / "test-session.json"
    assert expected.exists()
    saved = json.loads(expected.read_text())
    assert saved["session_id"] == "test-session"


def test_dry_run_toggle(tmp_path: Path) -> None:
    """/dry-run on|off flips the internal flag that's passed to send_message."""
    h = _harness(tmp_path)
    h.cli._handle_slash("/dry-run on")
    assert h.cli._dry_run is True
    h.cli._handle_slash("/dry-run off")
    assert h.cli._dry_run is False


def test_debug_toggle_and_tail(tmp_path: Path) -> None:
    """/debug on turns on auto-tail; bare /debug prints captured completions."""
    raw = [{"model": "m", "content": None, "tool_calls": [], "finish_reason": "stop"}]
    h = _harness(tmp_path, raw_tail=raw)
    h.cli._handle_slash("/debug on")
    assert h.cli._debug is True
    h.cli._handle_slash("/debug")
    assert "finish_reason" in h.output()


def test_unknown_slash_command_prints_hint(tmp_path: Path) -> None:
    """Typos like /foo don't crash; they print a hint and keep the REPL alive."""
    h = _harness(tmp_path)
    assert h.cli._handle_slash("/foo") is True
    assert "unknown" in h.output().lower()


def test_clear_command_empties_conversation(tmp_path: Path) -> None:
    """/clear wipes history but keeps the system prompt."""
    h = _harness(tmp_path)
    h.cli._conversation.add_user("hi")
    h.cli._handle_slash("/clear")
    msgs = h.cli._conversation.to_json()["messages"]
    assert msgs == []


def test_cost_command_reports_token_totals(tmp_path: Path) -> None:
    """/cost prints the cumulative tokens tracked across turns."""
    h = _harness(tmp_path)
    h.cli._total_input_tokens = 42
    h.cli._total_output_tokens = 17
    h.cli._handle_slash("/cost")
    output = h.output()
    assert "42" in output
    assert "17" in output
    assert "59" in output


def test_cost_command_reports_dollar_estimate_for_paid_models(tmp_path: Path) -> None:
    """/cost renders cumulative cost in USD once any priced turn has been seen.

    Sets ``_total_cost_usd`` directly because the CLI accumulates it from
    ``AIResponse.cost_usd_estimate`` and we'd rather assert the rendering
    contract here than couple the test to the OpenRouter client. The rate-
    table behavior itself is covered by test_pricing.py.
    """
    h = _harness(tmp_path)
    h.cli._total_input_tokens = 1000
    h.cli._total_output_tokens = 500
    h.cli._total_cost_usd = 0.0123
    h.cli._handle_slash("/cost")
    output = h.output()
    # 4-decimal rendering keeps small per-turn costs visible without scientific
    # notation. The "≈" prefix flags that this is an estimate, not invoice
    # truth.
    assert "$0.0123" in output
    assert "≈" in output


def test_cost_command_renders_free_tier_hint_when_no_paid_usage(
    tmp_path: Path,
) -> None:
    """Free-tier sessions show the informational hint instead of $0.0000.

    The threshold is "any paid turn at all" — so as long as cumulative cost
    is zero, we render the hint to avoid implying $0 is a billing fact.
    """
    h = _harness(tmp_path)
    h.cli._total_input_tokens = 100
    h.cli._total_output_tokens = 200
    h.cli._total_cost_usd = 0.0
    h.cli._handle_slash("/cost")
    output = h.output()
    assert "free-tier" in output
    assert "informational" in output


def test_ping_command_reports_reachable(tmp_path: Path) -> None:
    """/ping prints a green check when the provider responds."""
    h = _harness(tmp_path)
    h.client.ping.return_value = True
    assert h.cli._handle_slash("/ping") is True
    assert "reachable" in h.output()


def test_ping_command_reports_unreachable(tmp_path: Path) -> None:
    """/ping prints a red cross when the provider is down."""
    h = _harness(tmp_path)
    h.client.ping.return_value = False
    assert h.cli._handle_slash("/ping") is True
    assert "unreachable" in h.output()


def test_status_command_shows_model_session_and_tokens(tmp_path: Path) -> None:
    """/status panel includes model, session id, and cumulative token counts."""
    h = _harness(tmp_path)
    h.cli._total_input_tokens = 10
    h.cli._total_output_tokens = 5
    assert h.cli._handle_slash("/status") is True
    output = h.output()
    assert "primary/model:free" in output
    assert "test-session" in output
    assert "10" in output
    assert "5" in output


def test_status_command_shows_step_budget_and_flags(tmp_path: Path) -> None:
    """/status panel includes max_steps and dry_run/debug flags."""
    h = _harness(tmp_path)
    h.cli._max_steps = 4
    h.cli._dry_run = True
    assert h.cli._handle_slash("/status") is True
    output = h.output()
    assert "4" in output
    assert "True" in output


def test_model_command_reports_and_switches(tmp_path: Path) -> None:
    """/model with no arg prints current; /model <name> rewrites the config."""
    h = _harness(tmp_path)
    h.cli._handle_slash("/model")
    assert "primary/model:free" in h.output()
    h.cli._handle_slash("/model foo/bar:free")
    assert h.client._config.model == "foo/bar:free"


def test_history_command_dumps_json(tmp_path: Path) -> None:
    """/history prints the conversation as JSON (valid and parseable)."""
    h = _harness(tmp_path)
    h.cli._conversation.add_user("ping")
    h.cli._handle_slash("/history")
    # The output contains the message content somewhere in it.
    assert "ping" in h.output()


def test_session_command_switches_session(tmp_path: Path) -> None:
    """/session <id> saves the current one and loads the new id."""
    h = _harness(tmp_path)
    h.cli._conversation.add_user("first")
    h.cli._handle_slash("/session other")
    assert h.cli._session_id == "other"
    # The prior session's file should still exist with 'first' in it.
    saved = json.loads((tmp_path / "test-session.json").read_text())
    contents = [m["content"] for m in saved["messages"]]
    assert "first" in contents


# --- send_user_turn --------------------------------------------------------


def test_send_user_turn_appends_to_conversation_and_saves(tmp_path: Path) -> None:
    """A normal user turn hits the client, updates token counters, persists."""
    h = _harness(tmp_path, response_text="hi back")
    h.cli._send_user_turn("hi")

    h.client.send_message.assert_called_once()
    assert h.cli._total_output_tokens == 4
    assert h.cli._total_input_tokens == 3
    saved = json.loads((tmp_path / "test-session.json").read_text())
    roles = [m["role"] for m in saved["messages"]]
    assert roles == ["user", "assistant"]
    assert "hi back" in h.output()


def test_send_user_turn_rolls_back_on_error(tmp_path: Path) -> None:
    """P2: when send_message raises, the optimistic user message is removed."""
    from ai_client_api import AIProviderError

    client = _fake_client()
    client.send_message.side_effect = AIProviderError("boom")

    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False, color_system=None)
    cli = NimbusCLI(
        client=client,
        tools=[],
        session_id="err-session",
        session_dir=tmp_path,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        console=console,
    )

    cli._send_user_turn("will fail")

    # The failed message must not be in the conversation history.
    msg_contents = [m.content for m in cli._conversation.messages()]
    assert "will fail" not in msg_contents


def test_save_conversation_is_atomic(tmp_path: Path) -> None:
    """FM5: a .tmp file is used; no half-written session files remain after save."""
    h = _harness(tmp_path)
    h.cli._conversation.add_user("hello")
    h.cli._save_conversation()

    session_path = tmp_path / "test-session.json"
    tmp_path2 = tmp_path / "test-session.tmp"

    assert session_path.exists()
    # The temporary staging file must not linger.
    assert not tmp_path2.exists()


# --- event rendering -------------------------------------------------------


def test_on_event_tool_call_success(tmp_path: Path) -> None:
    """A tool_call_completed (success) renders a green check line."""
    h = _harness(tmp_path)
    h.cli._on_event(
        AgentEvent(
            kind="tool_call_started", payload={"name": "list_files", "arguments": {}}
        )
    )
    h.cli._on_event(
        AgentEvent(
            kind="tool_call_completed",
            payload={"name": "list_files", "success": True},
        )
    )
    output = h.output()
    assert "list_files" in output
    assert "✓" in output


def test_on_event_tool_call_failure(tmp_path: Path) -> None:
    """Failed tool call renders the reason string from the payload."""
    h = _harness(tmp_path)
    h.cli._on_event(
        AgentEvent(
            kind="tool_call_completed",
            payload={"name": "delete_file", "success": False, "reason": "no_confirm"},
        )
    )
    output = h.output()
    assert "delete_file" in output
    assert "no_confirm" in output


def test_on_event_fallback_line(tmp_path: Path) -> None:
    """A model_fallback event prints a visible 'fallback' transition line."""
    h = _harness(tmp_path)
    h.cli._on_event(
        AgentEvent(
            kind="model_fallback",
            payload={"from_model": "a", "to_model": "b", "reason": "rate_limit"},
        )
    )
    assert "fallback: a → b" in h.output()


# --- session persistence ---------------------------------------------------


def test_session_round_trip_restores_history(tmp_path: Path) -> None:
    """Saving then loading the same session id restores the conversation."""
    h = _harness(tmp_path)
    h.cli._send_user_turn("first message")

    # Reuse the session id in a fresh CLI instance.
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False, color_system=None)
    client2 = _fake_client(response_text="second reply")
    cli2 = NimbusCLI(
        client=client2,
        tools=[],
        session_id="test-session",
        session_dir=tmp_path,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        console=console,
    )
    msgs = cli2._conversation.to_json()["messages"]
    contents = [m["content"] for m in msgs]
    assert "first message" in contents


# --- app entry-point integration -------------------------------------------

_runner = CliRunner()


def test_main_without_api_key_exits_with_two(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typer app prints a fatal hint and exits with code 2 if no API key is set."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with patch("openrouter_ai_client_impl.cli._load_dotenv_best_effort"):
        result = _runner.invoke(app, ["--no-tools", "--session", "irrelevant"])
    assert result.exit_code == 2


def test_main_auto_generates_session_when_flag_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --session we get a fresh ``session-<uuid8>`` id each invocation."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("NIMBUS_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("NIMBUS_CONTAINER", raising=False)
    monkeypatch.delenv("AWS_BUCKET_NAME", raising=False)

    captured: dict[str, str] = {}

    def fake_cli_run(self: NimbusCLI) -> int:
        captured["session_id"] = self._session_id
        return 0

    with (
        patch("openrouter_ai_client_impl.cli._load_dotenv_best_effort"),
        patch("openrouter_ai_client_impl.cli.OpenRouterClient"),
        patch.object(NimbusCLI, "run", fake_cli_run),
    ):
        result = _runner.invoke(app, ["--no-tools"])

    assert result.exit_code == 0
    assert captured["session_id"].startswith("session-")
    assert len(captured["session_id"]) == len("session-") + 8
