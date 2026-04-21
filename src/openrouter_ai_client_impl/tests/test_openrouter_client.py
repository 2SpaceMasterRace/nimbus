"""Tests for the pydantic-ai-backed :class:`OpenRouterClient`.

All model interactions are scripted via ``FunctionModel`` — no real HTTP calls
are made. ``FunctionModel`` lets us return canned ``ModelResponse`` objects
(plain text or tool calls) on a per-step basis, giving exact control over the
agentic loop without any mocking of internals.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import openai
import pytest
from openrouter_ai_client_impl.config import OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import (
    OpenRouterClient,
    _sandbox_result,
)
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ai_client_api import (
    AgentEvent,
    AIAuthenticationError,
    AIRateLimitError,
    AIStepBudgetExceededError,
    Conversation,
    Tool,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# FunctionModel factories
# ---------------------------------------------------------------------------


def _text_model(text: str = "done") -> FunctionModel:
    """Model that always returns a plain-text response."""

    def fn(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(fn)


def _scripted_model(*responses: ModelResponse) -> FunctionModel:
    """Model that pops from a scripted queue on each call."""
    it = iter(responses)

    def fn(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        return next(it)

    return FunctionModel(fn)


def _error_model(error: Exception) -> FunctionModel:
    """Model that always raises *error* — used to test fallback / error paths."""

    def fn(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        raise error

    return FunctionModel(fn)


def _tool_call_response(
    *, call_id: str, name: str, args: dict[str, Any]
) -> ModelResponse:
    return ModelResponse(
        parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=call_id)]
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**overrides: Any) -> OpenRouterConfig:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "api_key": "sk-test",
        "model": "primary/model:free",
        "fallback_model": "fallback/model:free",
        "base_url": "https://example.test/api/v1",
        "timeout_seconds": 1.0,
    }
    defaults.update(overrides)
    return OpenRouterConfig(**defaults)


def _client(
    pai_model: FunctionModel,
    *,
    pai_fallback_model: FunctionModel | None = None,
    **cfg_overrides: Any,  # noqa: ANN401
) -> OpenRouterClient:
    return OpenRouterClient(
        _config(**cfg_overrides),
        pai_model=pai_model,
        pai_fallback_model=pai_fallback_model,
    )


def _noop_tool(name: str = "do_thing") -> tuple[Tool, list[dict[str, Any]]]:
    """Return a (Tool, calls_list) pair; calls_list is mutated by each invocation."""
    calls: list[dict[str, Any]] = []

    def handler(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        calls.append(kwargs)
        return {"ok": True}

    tool = Tool(
        name=name,
        description="Does a thing",
        parameters_schema={"type": "object", "properties": {"x": {"type": "integer"}}},
        handler=handler,
    )
    return tool, calls


# ---------------------------------------------------------------------------
# Basic send_message tests
# ---------------------------------------------------------------------------


def test_send_message_plain_text_ends_after_one_step() -> None:
    """No tool calls → loop exits on step 1 with the assistant text."""
    client = _client(_text_model("hi there"))
    response = client.send_message("say hi")

    assert response.text == "hi there"
    assert response.stop_reason == "end_turn"
    assert response.steps == 1
    assert response.fallback_used is False


def test_send_message_runs_tools_then_returns_text() -> None:
    """Tool call → handler runs → result fed back → model produces text."""
    tool, calls = _noop_tool()
    model = _scripted_model(
        _tool_call_response(call_id="c1", name="do_thing", args={"x": 1}),
        ModelResponse(parts=[TextPart("done")]),
    )
    client = _client(model)
    response = client.send_message("run it", tools=[tool])

    assert response.text == "done"
    assert response.steps == 2
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "do_thing"
    assert response.tool_calls[0].success is True
    assert calls == [{"x": 1}]


def test_send_message_exceeds_max_steps_raises() -> None:
    """If the model never stops calling tools, we raise after max_steps."""
    tool, _ = _noop_tool("loop")

    call_n = [0]

    def fn(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        call_n[0] += 1
        return ModelResponse(
            parts=[
                ToolCallPart(tool_name="loop", args={}, tool_call_id=f"c{call_n[0]}")
            ]
        )

    model = FunctionModel(fn)
    client = _client(model)

    with pytest.raises(AIStepBudgetExceededError):
        client.send_message("go", tools=[tool], max_steps=3)


def test_send_message_rejects_zero_max_steps() -> None:
    """Zero / negative max_steps is a misconfiguration, not a runtime case."""
    client = _client(_text_model())
    with pytest.raises(ValueError, match="max_steps"):
        client.send_message("hi", max_steps=0)


def test_handler_exception_is_recorded_and_fed_back() -> None:
    """A tool handler that raises becomes a structured failure record."""

    def bad_handler(**_: Any) -> dict[str, Any]:  # noqa: ANN401
        msg = "boom"
        raise RuntimeError(msg)

    tool = Tool(
        name="broken",
        description="",
        parameters_schema={"type": "object"},
        handler=bad_handler,
    )
    model = _scripted_model(
        _tool_call_response(call_id="b1", name="broken", args={}),
        ModelResponse(parts=[TextPart("noted")]),
    )
    client = _client(model)
    response = client.send_message("go", tools=[tool])

    assert response.text == "noted"
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].success is False
    assert "boom" in response.tool_calls[0].result_summary


# ---------------------------------------------------------------------------
# Auth / rate-limit / fallback tests
# ---------------------------------------------------------------------------


def test_auth_error_translated() -> None:
    """401s from the provider surface as :class:`AIAuthenticationError`."""
    err = openai.AuthenticationError(
        message="bad key", response=MagicMock(status_code=401), body=None
    )
    client = _client(_error_model(err))

    with pytest.raises(AIAuthenticationError):
        client.send_message("hi")


def test_rate_limit_falls_back_to_secondary_model() -> None:
    """A 429 triggers a single retry with the fallback model."""
    rate_limit = openai.RateLimitError(
        message="slow down", response=MagicMock(status_code=429), body=None
    )
    primary = _error_model(rate_limit)
    fallback = _text_model("fallback says hi")

    events: list[AgentEvent] = []
    client = _client(primary, pai_fallback_model=fallback)
    client.on_event(events.append)

    response = client.send_message("hi")

    assert response.text == "fallback says hi"
    assert response.fallback_used is True
    assert response.model == "fallback/model:free"
    assert any(e.kind == "model_fallback" for e in events)


def test_rate_limit_without_fallback_raises() -> None:
    """With no fallback configured, 429 propagates as :class:`AIRateLimitError`."""
    err = openai.RateLimitError(
        message="slow down", response=MagicMock(status_code=429), body=None
    )
    client = _client(_error_model(err), fallback_model=None)

    with pytest.raises(AIRateLimitError):
        client.send_message("hi")


def test_model_http_error_5xx_triggers_fallback() -> None:
    """A 503 ModelHTTPError triggers fallback (server-side error)."""
    err = ModelHTTPError(status_code=503, model_name="primary/model:free", body="oops")
    primary = _error_model(err)
    fallback = _text_model("recovered")

    client = _client(primary, pai_fallback_model=fallback)
    events: list[AgentEvent] = []
    client.on_event(events.append)

    response = client.send_message("hi")
    assert response.text == "recovered"
    assert response.fallback_used is True
    fallback_events = [e for e in events if e.kind == "model_fallback"]
    assert fallback_events[0].payload["reason"] == "server_error"


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_skips_handler_execution() -> None:
    """In dry-run the handler is not called; we record what *would* run."""
    called = False

    def handler(**_: Any) -> dict[str, Any]:  # noqa: ANN401
        nonlocal called
        called = True
        return {}

    tool = Tool(
        name="risky",
        description="",
        parameters_schema={"type": "object"},
        handler=handler,
    )
    model = _scripted_model(
        _tool_call_response(call_id="d1", name="risky", args={}),
        ModelResponse(parts=[TextPart("ok")]),
    )
    client = _client(model)
    response = client.send_message("go", tools=[tool], dry_run=True)

    assert called is False
    assert response.tool_calls[0].success is True
    assert "dry_run" in response.tool_calls[0].result_summary


# ---------------------------------------------------------------------------
# Event ordering
# ---------------------------------------------------------------------------


def test_events_are_emitted_in_order() -> None:
    """Each lifecycle hook produces an event in the expected order."""
    tool, _ = _noop_tool("t")
    model = _scripted_model(
        _tool_call_response(call_id="e1", name="t", args={}),
        ModelResponse(parts=[TextPart("done")]),
    )
    events: list[AgentEvent] = []
    client = _client(model)
    client.on_event(events.append)

    client.send_message("go", tools=[tool])

    kinds = [e.kind for e in events]
    assert kinds == [
        "request_started",
        "tool_call_started",
        "tool_call_completed",
        "request_completed",
    ]


# ---------------------------------------------------------------------------
# Conversation mutation
# ---------------------------------------------------------------------------


def test_conversation_is_mutated_in_place() -> None:
    """Passing a ``Conversation`` accumulates the new exchange on it."""
    conv = Conversation(system="sys")
    conv.add_user("previous turn")
    client = _client(_text_model("answer"))

    client.send_message(conv)

    texts = [m.content for m in conv.messages()]
    assert "previous turn" in texts
    assert "answer" in texts


def test_multi_turn_conversation_history_preserved() -> None:
    """Second turn sees first-turn messages in pydantic-ai history."""
    seen_message_counts: list[int] = []

    def fn(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        seen_message_counts.append(len(messages))
        return ModelResponse(parts=[TextPart("ok")])

    model = FunctionModel(fn)
    conv = Conversation(system="sys")

    client = _client(model)
    conv.add_user("first")
    client.send_message(conv)

    conv.add_user("second")
    client.send_message(conv)

    # Second call should receive more messages (history from first turn).
    assert seen_message_counts[1] > seen_message_counts[0]


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


def test_ping_returns_true_on_success() -> None:
    """A successful one-token completion means the provider is reachable."""
    client = _client(_text_model("ok"))
    assert client.ping() is True


def test_ping_returns_false_on_any_exception() -> None:
    """``ping`` must never raise; it returns False when the provider is down."""
    err = openai.RateLimitError(
        message="down", response=MagicMock(status_code=429), body=None
    )
    client = _client(_error_model(err))
    assert client.ping() is False


# ---------------------------------------------------------------------------
# Sandbox helper
# ---------------------------------------------------------------------------


def test_sandbox_wraps_and_truncates() -> None:
    """Tool results are wrapped and oversize content is truncated."""
    wrapped = _sandbox_result("short")
    assert '<tool_result source="untrusted">' in wrapped
    assert "short" in wrapped

    huge = _sandbox_result("x" * 10000)
    assert "truncated" in huge


def test_sandbox_strips_control_characters() -> None:
    """FM7: ASCII control characters are stripped before the model sees the result."""
    # Build a string with a mix of safe whitespace and unsafe control chars.
    text = "safe\x00text\x01with\x07bells\x1bESC\x7fDEL\nnewline\ttab"
    result = _sandbox_result(text)
    # Safe whitespace (newline, tab) is preserved.
    assert "\n" in result
    assert "\t" in result
    # Unsafe control chars are gone.
    assert "\x00" not in result
    assert "\x01" not in result
    assert "\x07" not in result
    assert "\x1b" not in result
    assert "\x7f" not in result


# ---------------------------------------------------------------------------
# FM4: ModelHTTPError 429 is treated as rate-limit, not provider error
# ---------------------------------------------------------------------------


def test_model_http_error_429_raises_rate_limit_error() -> None:
    """FM4: ModelHTTPError(429) must surface as AIRateLimitError."""
    err = ModelHTTPError(status_code=429, model_name="primary/model:free", body="limit")
    client = _client(_error_model(err), fallback_model=None)

    with pytest.raises(AIRateLimitError):
        client.send_message("hi")


def test_model_http_error_429_triggers_fallback() -> None:
    """FM4: a 429 ModelHTTPError on the primary triggers the fallback model."""
    err = ModelHTTPError(status_code=429, model_name="primary/model:free", body="limit")
    primary = _error_model(err)
    fallback = _text_model("recovered from 429")

    events: list[AgentEvent] = []
    client = _client(primary, pai_fallback_model=fallback)
    client.on_event(events.append)

    response = client.send_message("hi")

    assert response.text == "recovered from 429"
    assert response.fallback_used is True
    fallback_events = [e for e in events if e.kind == "model_fallback"]
    assert fallback_events[0].payload["reason"] == "rate_limit"


def test_fallback_model_http_error_429_raises_rate_limit_error() -> None:
    """FM4: fallback ModelHTTPError(429) also raises AIRateLimitError."""
    primary_err = ModelHTTPError(
        status_code=429, model_name="primary/model:free", body="limit"
    )
    fallback_err = ModelHTTPError(
        status_code=429, model_name="fallback/model:free", body="also limited"
    )
    client = _client(
        _error_model(primary_err),
        pai_fallback_model=_error_model(fallback_err),
    )

    with pytest.raises(AIRateLimitError):
        client.send_message("hi")


# ---------------------------------------------------------------------------
# Listener resilience
# ---------------------------------------------------------------------------


def test_listener_exceptions_do_not_break_loop() -> None:
    """A raising listener is logged but does not interrupt the loop."""

    def bad_listener(_event: AgentEvent) -> None:
        msg = "listener exploded"
        raise RuntimeError(msg)

    client = _client(_text_model("still works"))
    client.on_event(bad_listener)

    response = client.send_message("hi")
    assert response.text == "still works"


# ---------------------------------------------------------------------------
# last_raw_completions (debug ring buffer)
# ---------------------------------------------------------------------------


def test_last_raw_completions_captures_finish_reason() -> None:
    """After send_message the debug buffer has an entry with finish_reason."""
    client = _client(_text_model("hi"))
    client.send_message("go")

    raw = client.last_raw_completions()
    assert len(raw) >= 1
    assert "finish_reason" in raw[-1]
    assert raw[-1]["finish_reason"] == "stop"
