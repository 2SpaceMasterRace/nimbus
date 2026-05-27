"""Tests for nimbus_slack.profile."""

from __future__ import annotations

import pytest
from nimbus_slack.profile import (
    PROFILE_TIMING_FLAG,
    ProfileTrace,
    extract_profile_timing,
    extract_profile_timing_mode,
    profile_trace_card,
)

pytestmark = pytest.mark.unit


def test_extract_profile_timing_finds_flag_at_end() -> None:
    """Flag at the end of the message is stripped and reported as enabled."""
    cleaned, enabled = extract_profile_timing(
        "list files in this channel --profile-timing"
    )
    assert cleaned == "list files in this channel"
    assert enabled is True


def test_extract_profile_timing_finds_flag_at_start() -> None:
    """Flag at the start of the message is also stripped."""
    cleaned, enabled = extract_profile_timing("--profile-timing status")
    assert cleaned == "status"
    assert enabled is True


def test_extract_profile_timing_finds_flag_in_middle() -> None:
    """Flag in the middle of the message is stripped without losing context."""
    cleaned, enabled = extract_profile_timing("find --profile-timing duplicate files")
    assert cleaned == "find duplicate files"
    assert enabled is True


def test_extract_profile_timing_case_insensitive() -> None:
    """Token match is case-insensitive."""
    cleaned, enabled = extract_profile_timing("status --PROFILE-TIMING")
    assert cleaned == "status"
    assert enabled is True


def test_extract_profile_timing_multiple_occurrences() -> None:
    """All occurrences are removed; flag stays enabled."""
    cleaned, enabled = extract_profile_timing(
        "--profile-timing status --profile-timing"
    )
    assert cleaned == "status"
    assert enabled is True


def test_extract_profile_timing_substring_does_not_match() -> None:
    """Substring of another token does not trigger flag stripping."""
    cleaned, enabled = extract_profile_timing("foo--profile-timingbar status")
    assert cleaned == "foo--profile-timingbar status"
    assert enabled is False


def test_extract_profile_timing_absent_returns_original_text() -> None:
    """Without the flag, text is returned with whitespace normalised."""
    cleaned, enabled = extract_profile_timing("status")
    assert cleaned == "status"
    assert enabled is False


def test_extract_profile_timing_mode_supports_explicit_modes() -> None:
    """Slack users can request full, HUD, or waterfall renderings."""
    cleaned, mode = extract_profile_timing_mode("status --profile-timings=waterfall")
    assert cleaned == "status"
    assert mode == "waterfall"

    cleaned, mode = extract_profile_timing_mode("--profile-timings full status")
    assert cleaned == "status"
    assert mode == "full"


def test_profile_trace_disabled_records_no_spans() -> None:
    """When disabled, the trace's span context manager is a no-op."""
    trace = ProfileTrace(enabled=False)
    with trace.span("foo", kind="bar"):
        pass
    assert trace.spans == []


def test_profile_trace_enabled_records_span_with_detail() -> None:
    """When enabled, the trace captures the span name and detail map."""
    trace = ProfileTrace(enabled=True)
    with trace.span("slack.parse_command", kind="status"):
        pass
    assert len(trace.spans) == 1
    span = trace.spans[0]
    assert span.name == "slack.parse_command"
    assert dict(span.detail) == {"kind": "status"}
    assert span.duration_ms >= 0


def test_profile_trace_records_span_on_exception() -> None:
    """A span is still recorded when the wrapped block raises."""
    trace = ProfileTrace(enabled=True)
    boom = RuntimeError("boom")
    with pytest.raises(RuntimeError, match="boom"), trace.span("slack.parse_command"):
        raise boom
    assert len(trace.spans) == 1
    assert trace.spans[0].name == "slack.parse_command"


def test_profile_trace_card_includes_total_and_each_span() -> None:
    """The rendered card lists every recorded span in order."""
    trace = ProfileTrace(enabled=True)
    with trace.span("slack.parse_command"):
        pass
    with trace.span("slack.adapter_command", kind="status"):
        pass
    blocks = profile_trace_card(trace)
    assert blocks[0]["type"] == "header"
    header_text = blocks[0]["text"]["text"]
    assert "Profile timing" in header_text
    assert "ms total" in header_text
    body_text = blocks[1]["text"]["text"]
    assert "slack.parse_command" in body_text
    assert "slack.adapter_command" in body_text
    assert "kind=status" in body_text
    assert "```" not in body_text
    footer_text = blocks[-1]["elements"][0]["text"]
    assert PROFILE_TIMING_FLAG in footer_text


def test_profile_trace_card_handles_empty_trace() -> None:
    """An empty trace still produces a valid card with the placeholder message."""
    trace = ProfileTrace(enabled=True)
    blocks = profile_trace_card(trace)
    body_text = blocks[1]["text"]["text"]
    assert "No spans" in body_text


def test_profile_trace_card_full_mode_labels_opaque_boundaries() -> None:
    """Full mode distinguishes measured spans from opaque provider work."""
    trace = ProfileTrace(enabled=True, mode="full")
    with trace.span("slack.parse_command"):
        pass
    with trace.span("slack.model_turn"):
        pass
    body_text = profile_trace_card(trace)[1]["text"]["text"]
    assert "measured" in body_text
    assert "opaque" in body_text
    assert "```" in body_text


def test_profile_trace_card_hud_mode_calls_out_bottleneck() -> None:
    """HUD mode renders a compact game-style bottleneck summary."""
    trace = ProfileTrace(enabled=True, mode="hud")
    with trace.span("slack.parse_command"):
        pass
    body_text = profile_trace_card(trace)[1]["text"]["text"]
    assert "bottleneck" in body_text
    assert "[" in body_text
