"""Small in-process telemetry recorder for Nimbus runtime boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

from opentelemetry import metrics


@dataclass(slots=True)
class _HistogramState:
    count: int = 0
    total: float = 0.0
    minimum: float | None = None
    maximum: float | None = None

    def observe(self, value: float) -> None:
        self.count += 1
        self.total += value
        self.minimum = value if self.minimum is None else min(self.minimum, value)
        self.maximum = value if self.maximum is None else max(self.maximum, value)


def _metric_key(name: str, **labels: str) -> str:
    if not labels:
        return name
    rendered = ",".join(f"{key}={labels[key]}" for key in sorted(labels))
    return f"{name}|{rendered}"


class RuntimeTelemetry:
    """In-memory counters and histogram summaries for runtime events."""

    def __init__(self) -> None:
        """Initialize empty metric registries."""
        self._lock = Lock()
        self._counters: dict[str, int] = {}
        self._histograms: dict[str, _HistogramState] = {}
        self.meter = metrics.get_meter("nimbus-runtime")
        self.turn_counter = self.meter.create_counter(
            name="nimbus.wrapper.turns",
            description="Counts every chat turn processed by the runtime",
        )
        self.turn_histogram = self.meter.create_histogram(
            name="nimbus.wrapper.turn_latency_ms",
            description="Measures how long each full chat turn takes end to end",
            unit="ms",
        )
        self.ai_request_counter = self.meter.create_counter(
            name="nimbus.ai.requests",
            description="Counts every call made to the AI provider",
        )
        self.ai_latency_histogram = self.meter.create_histogram(
            name="nimbus.ai.latency_ms",
            description="Measures how long the AI provider takes to respond",
            unit="ms",
        )
        self.tool_call_counter = self.meter.create_counter(
            name="nimbus.ai.tool_calls",
            description="Counts every tool invocation made by the AI",
        )
        self.tool_latency_histogram = self.meter.create_histogram(
            name="nimbus.ai.tool_latency_ms",
            description="Measures how long each tool call takes",
            unit="ms",
        )
        self.auth_counter = self.meter.create_counter(
            name="nimbus.wrapper.auth",
            description="Counts authentication attempts by mechanism and result",
        )
        self.idempotent_replay_counter = self.meter.create_counter(
            name="nimbus.wrapper.idempotent_replays",
            description="Counts duplicate requests served from cache",
        )
        self.slack_turn_counter = self.meter.create_counter(
            name="nimbus.slack.turns",
            description="Counts Slack events accepted by the Slack adapter",
        )
        self.slack_reply_counter = self.meter.create_counter(
            name="nimbus.slack.replies",
            description="Counts Slack reply-post attempts by result",
        )
        self.destructive_tool_counter = self.meter.create_counter(
            name="nimbus.ai.destructive_tool_calls",
            description=(
                "Counts tool invocations whose Tool definition declares "
                "is_destructive=True. Useful for auditing model-driven mutation."
            ),
        )
        self.storage_op_counter = self.meter.create_counter(
            name="nimbus.storage.ops",
            description="Counts storage operations by kind and outcome",
        )
        self.storage_bytes_histogram = self.meter.create_histogram(
            name="nimbus.storage.bytes",
            description="Distribution of storage operation byte sizes",
            unit="By",
        )
        self.storage_latency_histogram = self.meter.create_histogram(
            name="nimbus.storage.latency_ms",
            description="Distribution of storage operation latencies",
            unit="ms",
        )
        self.ai_tokens_counter = self.meter.create_counter(
            name="nimbus.ai.tokens",
            description="Counts AI tokens consumed, labeled by model and direction",
            unit="token",
        )
        self.ai_cost_histogram = self.meter.create_histogram(
            name="nimbus.ai.cost_usd",
            description=(
                "Estimated USD cost per AI response, computed from token usage "
                "and a curated per-model price table. Approximate."
            ),
            unit="USD",
        )
        self.file_sync_duration_histogram = self.meter.create_histogram(
            name="nimbus.file_sync.save_duration_ms",
            description="Wall-clock duration of a save_channel run",
            unit="ms",
        )
        self.file_sync_files_counter = self.meter.create_counter(
            name="nimbus.file_sync.files_total",
            description="Files processed during save_channel runs, labeled by outcome",
            unit="file",
        )
        # --- Feature 16: trust-chain and task lifecycle metrics ----------
        self.task_duration_histogram = self.meter.create_histogram(
            name="nimbus.task.duration_seconds",
            description=(
                "Wall-clock duration of a completed task from created to terminal state"
            ),
            unit="s",
        )
        self.task_failure_counter = self.meter.create_counter(
            name="nimbus.task.failures_total",
            description="Tasks that reached a failed or expired terminal state",
        )
        self.slack_dedupe_counter = self.meter.create_counter(
            name="nimbus.slack.event_dedupes_total",
            description=(
                "Slack events dropped because the same event_id was already processed"
            ),
        )
        self.verifier_failure_counter = self.meter.create_counter(
            name="nimbus.verifier.failures_total",
            description=(
                "Verifier calls that could not confirm a side effect "
                "(error, timeout, mismatch)"
            ),
        )
        self.search_latency_histogram = self.meter.create_histogram(
            name="nimbus.search.latency_ms",
            description="End-to-end latency of ACL-filtered search queries",
            unit="ms",
        )
        self.index_lag_histogram = self.meter.create_histogram(
            name="nimbus.search.index_lag_ms",
            description="Time between object write and the object becoming searchable",
            unit="ms",
        )
        self.approval_fail_closed_counter = self.meter.create_counter(
            name="nimbus.approval.fail_closed_total",
            description=(
                "Approval decisions that failed closed: wrong actor, expired, "
                "or missing binding"
            ),
        )

    def reset(self) -> None:
        """Clear all recorded metrics."""
        with self._lock:
            self._counters.clear()
            self._histograms.clear()

    def snapshot(self) -> dict[str, object]:
        """Return a test-friendly snapshot of counters and histogram summaries."""
        with self._lock:
            histograms = {
                key: {
                    "count": state.count,
                    "sum": state.total,
                    "min": state.minimum,
                    "max": state.maximum,
                }
                for key, state in self._histograms.items()
            }
            return {
                "counters": dict(self._counters),
                "histograms": histograms,
            }

    def increment(self, name: str, **labels: str) -> None:
        """Increment one labeled counter by 1."""
        key = _metric_key(name, **labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1

    def add_count(self, name: str, value: int, **labels: str) -> None:
        """Add ``value`` to one labeled counter.

        Used by counters whose unit is not "events" — e.g. tokens, where a
        single response contributes many counts. Negative or zero values are
        silently ignored so callers don't need to special-case empty usage.
        """
        if value <= 0:
            return
        key = _metric_key(name, **labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe(self, name: str, value: float, **labels: str) -> None:
        """Observe one labeled histogram sample."""
        key = _metric_key(name, **labels)
        with self._lock:
            state = self._histograms.get(key)
            if state is None:
                state = _HistogramState()
                self._histograms[key] = state
            state.observe(value)

    def record_wrapper_turn(
        self,
        *,
        platform: str,
        outcome: str,
        latency_ms: int,
    ) -> None:
        """Record one wrapper turn outcome and latency."""
        self.increment(
            "nimbus_wrapper_turns_total",
            platform=platform,
            outcome=outcome,
        )
        self.observe(
            "nimbus_wrapper_turn_latency_ms",
            float(latency_ms),
            platform=platform,
        )
        self.turn_counter.add(1, attributes={"platform": platform, "outcome": outcome})
        self.turn_histogram.record(float(latency_ms), attributes={"platform": platform})

    def record_idempotent_replay(self, *, backend: str) -> None:
        """Record one idempotent replay served from cache."""
        self.increment("nimbus_wrapper_idempotent_replays_total", backend=backend)
        self.idempotent_replay_counter.add(1, attributes={"backend": backend})

    def record_auth_result(self, *, mechanism: str, result: str, reason: str) -> None:
        """Record one auth success or failure."""
        self.increment(
            "nimbus_wrapper_auth_total",
            mechanism=mechanism,
            result=result,
            reason=reason,
        )
        self.auth_counter.add(
            1, attributes={"mechanism": mechanism, "result": result, "reason": reason}
        )

    def record_ai_response(
        self,
        *,
        model: str,
        latency_ms: int,
        fallback_used: bool,
        stop_reason: str,
    ) -> None:
        """Record one successful AI response."""
        self.increment(
            "nimbus_ai_requests_total",
            result="success",
            model=model,
            fallback_used=str(fallback_used).lower(),
            stop_reason=stop_reason,
        )
        self.observe("nimbus_ai_latency_ms", float(latency_ms), model=model)
        self.ai_request_counter.add(
            1,
            attributes={
                "result": "success",
                "model": model,
                "fallback_used": str(fallback_used).lower(),
                "stop_reason": stop_reason,
            },
        )
        self.ai_latency_histogram.record(float(latency_ms), attributes={"model": model})

    def record_ai_tokens(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record token usage for one AI response, split by direction.

        Emits two label combinations (``direction=input`` and
        ``direction=output``) on the ``nimbus.ai.tokens`` counter so dashboards
        can chart input vs output independently per model. Zero-count
        directions are skipped.
        """
        for direction, count in (("input", input_tokens), ("output", output_tokens)):
            if count <= 0:
                continue
            self.add_count(
                "nimbus_ai_tokens_total",
                count,
                model=model,
                direction=direction,
            )
            self.ai_tokens_counter.add(
                count,
                attributes={"model": model, "direction": direction},
            )

    def record_ai_cost(self, *, model: str, cost_usd: float) -> None:
        """Record one estimated cost observation in USD for an AI response.

        Cost is approximate. The in-memory snapshot uses a histogram so tests
        can assert on count + sum + min/max without depending on the OTel
        exporter.
        """
        self.observe("nimbus_ai_cost_usd", cost_usd, model=model)
        self.ai_cost_histogram.record(cost_usd, attributes={"model": model})

    def record_ai_failure(self, *, error_kind: str) -> None:
        """Record one AI-side failure."""
        self.increment(
            "nimbus_ai_requests_total",
            result="failure",
            error_kind=error_kind,
        )
        self.ai_request_counter.add(
            1, attributes={"result": "failure", "error_kind": error_kind}
        )

    def record_tool_call(
        self,
        *,
        tool_name: str,
        success: bool,
        latency_ms: int,
    ) -> None:
        """Record one AI tool invocation."""
        self.increment(
            "nimbus_ai_tool_calls_total",
            tool_name=tool_name,
            success=str(success).lower(),
        )
        self.observe(
            "nimbus_ai_tool_latency_ms",
            float(latency_ms),
            tool_name=tool_name,
        )
        self.tool_call_counter.add(
            1,
            attributes={"tool_name": tool_name, "success": str(success).lower()},
        )
        self.tool_latency_histogram.record(
            float(latency_ms), attributes={"tool_name": tool_name}
        )

    def record_slack_turn(self, *, kind: str, outcome: str) -> None:
        """Record one Slack adapter turn."""
        self.increment("nimbus_slack_turns_total", kind=kind, outcome=outcome)
        self.slack_turn_counter.add(1, attributes={"kind": kind, "outcome": outcome})

    def record_slack_reply(self, *, result: str, reason: str) -> None:
        """Record one Slack reply-post result."""
        self.increment("nimbus_slack_replies_total", result=result, reason=reason)
        self.slack_reply_counter.add(1, attributes={"result": result, "reason": reason})

    def record_destructive_tool_call(self, *, tool_name: str, success: bool) -> None:
        """Record one destructive tool invocation for audit dashboards."""
        self.increment(
            "nimbus_ai_destructive_tool_calls_total",
            tool_name=tool_name,
            success=str(success).lower(),
        )
        self.destructive_tool_counter.add(
            1,
            attributes={"tool_name": tool_name, "success": str(success).lower()},
        )

    def record_storage_op(
        self,
        *,
        op: str,
        outcome: str,
        bytes_transferred: int | None = None,
        latency_ms: int | None = None,
    ) -> None:
        """Record one storage operation with size and latency."""
        self.increment(
            "nimbus_storage_ops_total",
            op=op,
            outcome=outcome,
        )
        self.storage_op_counter.add(1, attributes={"op": op, "outcome": outcome})
        if bytes_transferred is not None:
            self.observe(
                "nimbus_storage_bytes",
                float(bytes_transferred),
                op=op,
            )
            self.storage_bytes_histogram.record(
                float(bytes_transferred), attributes={"op": op}
            )
        if latency_ms is not None:
            self.observe(
                "nimbus_storage_latency_ms",
                float(latency_ms),
                op=op,
            )
            self.storage_latency_histogram.record(
                float(latency_ms), attributes={"op": op}
            )

    def record_task_outcome(
        self,
        *,
        status: str,
        duration_seconds: float,
        tenant: str,
    ) -> None:
        """Record one task completion with its terminal status and duration."""
        attrs = {"status": status, "tenant": tenant}
        self.observe(
            "nimbus_task_duration_seconds",
            duration_seconds,
            status=status,
            tenant=tenant,
        )
        self.task_duration_histogram.record(duration_seconds, attributes=attrs)
        if status in {"failed", "expired"}:
            self.increment("nimbus_task_failures_total", status=status, tenant=tenant)
            self.task_failure_counter.add(1, attributes=attrs)

    def record_verifier_failure(self, *, verifier: str, error_kind: str) -> None:
        """Record one verifier call that could not confirm a side effect."""
        self.increment(
            "nimbus_verifier_failures_total",
            verifier=verifier,
            error_kind=error_kind,
        )
        self.verifier_failure_counter.add(
            1, attributes={"verifier": verifier, "error_kind": error_kind}
        )

    def record_search_query(
        self,
        *,
        latency_ms: int,
        result_count: int,
        hit_count: int,
    ) -> None:
        """Record one ACL-filtered search query with latency and result counts."""
        self.observe("nimbus_search_latency_ms", float(latency_ms))
        self.search_latency_histogram.record(float(latency_ms))
        self.increment(
            "nimbus_search_queries_total",
            has_results=str(result_count > 0).lower(),
        )
        _ = hit_count  # available for future per-query hit-rate tracking

    def record_index_lag(self, *, lag_ms: int) -> None:
        """Record the time between an object write and it becoming searchable."""
        self.observe("nimbus_search_index_lag_ms", float(lag_ms))
        self.index_lag_histogram.record(float(lag_ms))

    def record_slack_dedupe(self, *, event_type: str) -> None:
        """Record one Slack event dropped due to deduplication."""
        self.increment("nimbus_slack_event_dedupes_total", event_type=event_type)
        self.slack_dedupe_counter.add(1, attributes={"event_type": event_type})

    def record_approval_fail_closed(self, *, reason: str) -> None:
        """Record one approval that failed closed (wrong actor, expired, no binding)."""
        self.increment("nimbus_approval_fail_closed_total", reason=reason)
        self.approval_fail_closed_counter.add(1, attributes={"reason": reason})

    def record_file_sync_save(
        self,
        *,
        team_id: str,
        duration_ms: int,
        saved: int,
        skipped: int,
        failed: int,
    ) -> None:
        """Record the outcome of one save_channel run."""
        attrs = {"team_id": team_id}
        self.observe("nimbus_file_sync_save_duration_ms", float(duration_ms), **attrs)
        self.file_sync_duration_histogram.record(float(duration_ms), attributes=attrs)
        for outcome, count in (
            ("saved", saved),
            ("skipped", skipped),
            ("failed", failed),
        ):
            if count > 0:
                self.add_count(
                    "nimbus_file_sync_files_total",
                    count,
                    team_id=team_id,
                    outcome=outcome,
                )
                self.file_sync_files_counter.add(
                    count, attributes={**attrs, "outcome": outcome}
                )


runtime_telemetry = RuntimeTelemetry()
