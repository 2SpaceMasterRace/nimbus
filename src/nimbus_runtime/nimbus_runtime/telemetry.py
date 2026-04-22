"""Small in-process telemetry recorder for Nimbus runtime boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


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
        """Increment one labeled counter."""
        key = _metric_key(name, **labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1

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

    def record_idempotent_replay(self, *, backend: str) -> None:
        """Record one idempotent replay served from cache."""
        self.increment("nimbus_wrapper_idempotent_replays_total", backend=backend)

    def record_auth_result(self, *, mechanism: str, result: str, reason: str) -> None:
        """Record one auth success or failure."""
        self.increment(
            "nimbus_wrapper_auth_total",
            mechanism=mechanism,
            result=result,
            reason=reason,
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

    def record_ai_failure(self, *, error_kind: str) -> None:
        """Record one AI-side failure."""
        self.increment(
            "nimbus_ai_requests_total",
            result="failure",
            error_kind=error_kind,
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


runtime_telemetry = RuntimeTelemetry()
