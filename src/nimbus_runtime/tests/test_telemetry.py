"""Unit tests for the in-process runtime telemetry recorder."""

from __future__ import annotations

import pytest
from nimbus_runtime.telemetry import RuntimeTelemetry

pytestmark = pytest.mark.unit


def test_reset_clears_all_metrics() -> None:
    """Reset should wipe all recorded counters and histograms."""
    t = RuntimeTelemetry()
    t.increment("nimbus_test_events_total")
    t.observe("nimbus_test_latency_ms", 42.0)
    t.reset()
    snap = t.snapshot()
    assert snap["counters"] == {}
    assert snap["histograms"] == {}


def test_increment_defaults_to_one() -> None:
    """Increment should set a counter to 1 on first call."""
    t = RuntimeTelemetry()
    t.increment("nimbus_test_events_total")
    snap = t.snapshot()
    assert snap["counters"]["nimbus_test_events_total"] == 1


def test_add_count_ignores_non_positive_values() -> None:
    """add_count should be a no-op for zero or negative values."""
    t = RuntimeTelemetry()
    t.add_count("nimbus_test_tokens_total", 0)
    t.add_count("nimbus_test_tokens_total", -5)
    assert t.snapshot()["counters"] == {}


def test_add_count_accumulates_positive_values() -> None:
    """add_count should sum positive contributions."""
    t = RuntimeTelemetry()
    t.add_count("nimbus_test_tokens_total", 10)
    t.add_count("nimbus_test_tokens_total", 3)
    assert t.snapshot()["counters"]["nimbus_test_tokens_total"] == 13


def test_observe_tracks_min_max_sum_count() -> None:
    """Observe should record summary stats for histogram samples."""
    t = RuntimeTelemetry()
    t.observe("nimbus_test_latency_ms", 10.0)
    t.observe("nimbus_test_latency_ms", 20.0)
    t.observe("nimbus_test_latency_ms", 5.0)
    h = t.snapshot()["histograms"]["nimbus_test_latency_ms"]
    assert h["count"] == 3
    assert h["sum"] == 35.0
    assert h["min"] == 5.0
    assert h["max"] == 20.0


def test_metric_key_without_labels() -> None:
    """_metric_key should return the bare name when there are no labels."""
    from nimbus_runtime.telemetry import _metric_key

    assert _metric_key("nimbus_test") == "nimbus_test"


def test_metric_key_with_labels() -> None:
    """_metric_key should append sorted label pairs separated by pipe."""
    from nimbus_runtime.telemetry import _metric_key

    result = _metric_key("nimbus_test", b="2", a="1")
    assert result == "nimbus_test|a=1,b=2"


def test_record_wrapper_turn() -> None:
    """record_wrapper_turn should increment the wrapper turn counter."""
    t = RuntimeTelemetry()
    t.record_wrapper_turn(platform="slack", outcome="reply", latency_ms=150)
    snap = t.snapshot()
    assert (
        snap["counters"]["nimbus_wrapper_turns_total|outcome=reply,platform=slack"] == 1
    )


def test_record_idempotent_replay() -> None:
    """record_idempotent_replay should increment the replay counter."""
    t = RuntimeTelemetry()
    t.record_idempotent_replay(backend="local_json")
    snap = t.snapshot()
    assert (
        snap["counters"]["nimbus_wrapper_idempotent_replays_total|backend=local_json"]
        == 1
    )


def test_record_auth_result() -> None:
    """record_auth_result should increment the auth counter."""
    t = RuntimeTelemetry()
    t.record_auth_result(
        mechanism="signing_secret", result="denied", reason="bad_signature"
    )
    snap = t.snapshot()
    key = "nimbus_wrapper_auth_total|mechanism=signing_secret,reason=bad_signature,result=denied"
    assert snap["counters"][key] == 1


def test_record_ai_response() -> None:
    """record_ai_response should increment AI request counters."""
    t = RuntimeTelemetry()
    t.record_ai_response(
        model="test-model",
        latency_ms=200,
        fallback_used=False,
        stop_reason="end_turn",
    )
    snap = t.snapshot()
    key = "nimbus_ai_requests_total|fallback_used=false,model=test-model,result=success,stop_reason=end_turn"
    assert snap["counters"][key] == 1


def test_record_ai_tokens_skips_zero_count() -> None:
    """record_ai_tokens should skip directions with zero tokens."""
    t = RuntimeTelemetry()
    t.record_ai_tokens(model="test-model", input_tokens=0, output_tokens=0)
    assert t.snapshot()["counters"] == {}


def test_record_ai_tokens_counts_nonzero_directions() -> None:
    """record_ai_tokens should record both input and output directions."""
    t = RuntimeTelemetry()
    t.record_ai_tokens(model="test-model", input_tokens=10, output_tokens=25)
    counters = t.snapshot()["counters"]
    assert counters["nimbus_ai_tokens_total|direction=input,model=test-model"] == 10
    assert counters["nimbus_ai_tokens_total|direction=output,model=test-model"] == 25


def test_record_ai_cost() -> None:
    """record_ai_cost should record a histogram sample."""
    t = RuntimeTelemetry()
    t.record_ai_cost(model="test-model", cost_usd=0.0015)
    h = t.snapshot()["histograms"]["nimbus_ai_cost_usd|model=test-model"]
    assert h["count"] == 1
    assert h["sum"] == pytest.approx(0.0015)


def test_record_ai_failure() -> None:
    """record_ai_failure should record a failed AI request."""
    t = RuntimeTelemetry()
    t.record_ai_failure(error_kind="provider_timeout")
    snap = t.snapshot()
    key = "nimbus_ai_requests_total|error_kind=provider_timeout,result=failure"
    assert snap["counters"][key] == 1


def test_record_tool_call() -> None:
    """record_tool_call should count tool invocations."""
    t = RuntimeTelemetry()
    t.record_tool_call(tool_name="list_files", success=True, latency_ms=50)
    snap = t.snapshot()
    key = "nimbus_ai_tool_calls_total|success=true,tool_name=list_files"
    assert snap["counters"][key] == 1


def test_record_slack_turn() -> None:
    """record_slack_turn should count Slack events."""
    t = RuntimeTelemetry()
    t.record_slack_turn(kind="event_callback", outcome="accepted")
    snap = t.snapshot()
    key = "nimbus_slack_turns_total|kind=event_callback,outcome=accepted"
    assert snap["counters"][key] == 1


def test_record_slack_reply() -> None:
    """record_slack_reply should count Slack reply attempts."""
    t = RuntimeTelemetry()
    t.record_slack_reply(result="sent", reason="ok")
    snap = t.snapshot()
    key = "nimbus_slack_replies_total|reason=ok,result=sent"
    assert snap["counters"][key] == 1


def test_record_destructive_tool_call() -> None:
    """record_destructive_tool_call should count destructive invocations."""
    t = RuntimeTelemetry()
    t.record_destructive_tool_call(tool_name="delete_file", success=True)
    snap = t.snapshot()
    key = "nimbus_ai_destructive_tool_calls_total|success=true,tool_name=delete_file"
    assert snap["counters"][key] == 1


def test_record_storage_op() -> None:
    """record_storage_op should log ops with optional bytes and latency."""
    t = RuntimeTelemetry()
    t.record_storage_op(
        op="upload", outcome="ok", bytes_transferred=4096, latency_ms=120
    )
    snap = t.snapshot()
    assert snap["counters"]["nimbus_storage_ops_total|op=upload,outcome=ok"] == 1
    assert snap["histograms"]["nimbus_storage_bytes|op=upload"]["count"] == 1
    assert snap["histograms"]["nimbus_storage_latency_ms|op=upload"]["count"] == 1


def test_record_storage_op_without_optional_metrics() -> None:
    """record_storage_op should work without bytes/latency."""
    t = RuntimeTelemetry()
    t.record_storage_op(op="list", outcome="ok")
    snap = t.snapshot()
    assert snap["counters"]["nimbus_storage_ops_total|op=list,outcome=ok"] == 1
    assert "nimbus_storage_bytes" not in str(snap["histograms"])


def test_record_task_outcome_success() -> None:
    """record_task_outcome should record a successful task."""
    t = RuntimeTelemetry()
    t.record_task_outcome(status="completed", duration_seconds=2.5, tenant="T001")
    h = t.snapshot()["histograms"][
        "nimbus_task_duration_seconds|status=completed,tenant=T001"
    ]
    assert h["count"] == 1


def test_record_task_outcome_failure() -> None:
    """record_task_outcome should increment failure counter on failed tasks."""
    t = RuntimeTelemetry()
    t.record_task_outcome(status="failed", duration_seconds=10.0, tenant="T001")
    snap = t.snapshot()
    assert snap["counters"]["nimbus_task_failures_total|status=failed,tenant=T001"] == 1


def test_record_task_outcome_expired() -> None:
    """record_task_outcome should increment failure counter on expired tasks."""
    t = RuntimeTelemetry()
    t.record_task_outcome(status="expired", duration_seconds=30.0, tenant="T001")
    snap = t.snapshot()
    assert (
        snap["counters"]["nimbus_task_failures_total|status=expired,tenant=T001"] == 1
    )


def test_record_verifier_failure() -> None:
    """record_verifier_failure should count verifier errors."""
    t = RuntimeTelemetry()
    t.record_verifier_failure(verifier="drift_check", error_kind="timeout")
    snap = t.snapshot()
    key = "nimbus_verifier_failures_total|error_kind=timeout,verifier=drift_check"
    assert snap["counters"][key] == 1


def test_record_search_query() -> None:
    """record_search_query should record latency and result indicator."""
    t = RuntimeTelemetry()
    t.record_search_query(latency_ms=80, result_count=5, hit_count=10)
    snap = t.snapshot()
    assert "nimbus_search_latency_ms" in snap["histograms"]
    assert "nimbus_search_queries_total|has_results=true" in snap["counters"]


def test_record_search_query_empty_result() -> None:
    """record_search_query should indicate has_results=false when count is 0."""
    t = RuntimeTelemetry()
    t.record_search_query(latency_ms=5, result_count=0, hit_count=0)
    snap = t.snapshot()
    assert "nimbus_search_queries_total|has_results=false" in snap["counters"]


def test_record_index_lag() -> None:
    """record_index_lag should record a search index lag observation."""
    t = RuntimeTelemetry()
    t.record_index_lag(lag_ms=200)
    h = t.snapshot()["histograms"]["nimbus_search_index_lag_ms"]
    assert h["count"] == 1


def test_record_slack_dedupe() -> None:
    """record_slack_dedupe should count deduplicated Slack events."""
    t = RuntimeTelemetry()
    t.record_slack_dedupe(event_type="message")
    snap = t.snapshot()
    assert snap["counters"]["nimbus_slack_event_dedupes_total|event_type=message"] == 1


def test_record_approval_fail_closed() -> None:
    """record_approval_fail_closed should count fail-closed approvals."""
    t = RuntimeTelemetry()
    t.record_approval_fail_closed(reason="expired")
    snap = t.snapshot()
    assert snap["counters"]["nimbus_approval_fail_closed_total|reason=expired"] == 1


def test_record_file_sync_save() -> None:
    """record_file_sync_save should count saved/skipped/failed files."""
    t = RuntimeTelemetry()
    t.record_file_sync_save(
        team_id="T001",
        duration_ms=500,
        saved=3,
        skipped=1,
        failed=0,
    )
    snap = t.snapshot()
    assert (
        snap["histograms"]["nimbus_file_sync_save_duration_ms|team_id=T001"]["count"]
        == 1
    )
    assert (
        snap["counters"]["nimbus_file_sync_files_total|outcome=saved,team_id=T001"] == 3
    )
    assert (
        snap["counters"]["nimbus_file_sync_files_total|outcome=skipped,team_id=T001"]
        == 1
    )
    assert "outcome=failed" not in str(snap["counters"])
