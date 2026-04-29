"""Tests for the provider-agnostic value types."""

from __future__ import annotations

import pytest

from ai_client_api import (
    AgentEvent,
    AIResponse,
    AuditEvent,
    TokenUsage,
    Tool,
    ToolCallRecord,
    normalize_tools,
)

pytestmark = pytest.mark.unit

TOKEN_TOTAL_EXPECTED = 17
AI_RESPONSE_TOTAL_EXPECTED = 12


def test_token_usage_total() -> None:
    """``total`` sums input and output tokens."""
    usage = TokenUsage(input_tokens=12, output_tokens=5)
    assert usage.total == TOKEN_TOTAL_EXPECTED


def test_normalize_tools_returns_empty_tuple_for_none() -> None:
    """``None`` and empty lists are canonicalized to an empty tuple."""
    assert normalize_tools(None) == ()
    assert normalize_tools([]) == ()


def test_normalize_tools_returns_tuple_for_sequence() -> None:
    """Tools come back as an immutable tuple regardless of input container."""

    def _noop(**_: object) -> dict[str, object]:
        return {"ok": True}

    tools = [
        Tool(name="a", description="", parameters_schema={}, handler=_noop),
        Tool(name="b", description="", parameters_schema={}, handler=_noop),
    ]
    assert normalize_tools(tools) == tuple(tools)


def test_ai_response_fields() -> None:
    """Whole-object equality catches any accidental field drift."""
    resp = AIResponse(
        text="hi",
        model="openai/gpt-oss-120b:free",
        tokens=TokenUsage(input_tokens=10, output_tokens=2),
        tool_calls=(),
        latency_ms=123,
        stop_reason="end_turn",
        steps=1,
        fallback_used=False,
    )
    assert resp.text == "hi"
    assert resp.tokens.total == AI_RESPONSE_TOTAL_EXPECTED
    assert resp.stop_reason == "end_turn"


def test_tool_call_record_is_immutable() -> None:
    """Audit records must be frozen so later listeners cannot rewrite history."""
    record = ToolCallRecord(
        id="c1",
        name="upload_file",
        arguments={"k": "v"},
        result_summary="ok",
        success=True,
        latency_ms=11,
    )
    with pytest.raises((AttributeError, TypeError)):
        record.success = False  # type: ignore[misc]


def test_audit_event_shape() -> None:
    """AuditEvent carries the fields needed for later persistence."""
    event = AuditEvent(
        timestamp="2026-04-19T00:00:00Z",
        session_id="sess-1",
        request_id="req-1",
        tool_name="list_files",
        args={"prefix": ""},
        result_summary="0 results",
        success=True,
        latency_ms=10,
    )
    assert event.session_id == "sess-1"
    assert event.tool_name == "list_files"


def test_agent_event_payload_defaults_to_empty() -> None:
    """``payload`` is optional and defaults to an empty mapping."""
    event = AgentEvent(kind="request_started")
    assert event.payload == {}
