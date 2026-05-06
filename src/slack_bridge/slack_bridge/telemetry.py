"""Bridge-level telemetry recorders.

Records counters and a latency histogram into the shared
:data:`nimbus_runtime.runtime_telemetry` so the bridge appears in the
same in-memory snapshots and OTEL pipeline as the AI server. The
recorders are intentionally narrow: each call site has one obvious
function to call with a small, fixed label set so the metric cardinality
stays bounded.

Five signals are exposed:

``slack_bridge_inbound_total``
    Every inbound ``POST /slack/events`` request, labeled by the
    Slack-declared ``payload_type`` and the bridge's HTTP-level result
    (``accepted`` / ``rejected_signature`` / ``rejected_payload``). This
    is the primary success-rate signal for the events front door.
``slack_bridge_event_callback_total``
    Every ``event_callback`` payload that passed signature verification,
    labeled by what the bridge did with it
    (``dispatched`` / ``filtered`` / ``duplicate``). This is how Slack
    retry storms and bot-loop filtering become visible.
``slack_bridge_slash_inbound_total``
    Every inbound ``POST /slack/commands`` request, labeled by the
    bridge's HTTP-level result. Distinct from the events counter so
    error rates per endpoint are independently observable.
``slack_bridge_slash_command_total``
    Every slash-command payload that passed signature verification,
    labeled by what the bridge did with it
    (``dispatched`` / ``duplicate``). Slash commands have no
    ``filtered`` outcome because every invocation is a real user action.
``slack_bridge_dispatch_total`` and ``slack_bridge_dispatch_latency_ms``
    Background-task outcome (``success`` / ``failure``) and the
    wallclock latency from background-task start to completion, labeled
    by ``source`` (``event`` or ``slash_command``) so the two paths can
    be alerted on independently. This is the primary signal for
    end-to-end Slack-to-Nimbus health.
"""

from __future__ import annotations

from typing import Final

from nimbus_runtime import runtime_telemetry

_INBOUND_COUNTER: Final[str] = "slack_bridge_inbound_total"
_EVENT_CALLBACK_COUNTER: Final[str] = "slack_bridge_event_callback_total"
_SLASH_INBOUND_COUNTER: Final[str] = "slack_bridge_slash_inbound_total"
_SLASH_COMMAND_COUNTER: Final[str] = "slack_bridge_slash_command_total"
_DISPATCH_COUNTER: Final[str] = "slack_bridge_dispatch_total"
_DISPATCH_LATENCY_HISTOGRAM: Final[str] = "slack_bridge_dispatch_latency_ms"


def record_inbound(*, payload_type: str, result: str) -> None:
    """Record one inbound ``POST /slack/events`` request."""
    runtime_telemetry.increment(
        _INBOUND_COUNTER,
        payload_type=payload_type,
        result=result,
    )


def record_event_callback(*, outcome: str) -> None:
    """Record what the bridge did with one ``event_callback`` payload."""
    runtime_telemetry.increment(
        _EVENT_CALLBACK_COUNTER,
        outcome=outcome,
    )


def record_slash_inbound(*, result: str) -> None:
    """Record one inbound ``POST /slack/commands`` request."""
    runtime_telemetry.increment(
        _SLASH_INBOUND_COUNTER,
        result=result,
    )


def record_slash_command(*, outcome: str) -> None:
    """Record what the bridge did with one slash-command payload."""
    runtime_telemetry.increment(
        _SLASH_COMMAND_COUNTER,
        outcome=outcome,
    )


def record_dispatch(*, outcome: str, latency_ms: float, source: str = "event") -> None:
    """Record one background-task dispatch outcome and its latency.

    The ``source`` label distinguishes events from slash commands while
    keeping the existing ``slack_bridge_dispatch_*`` series usable for
    end-to-end health signals across both inputs.
    """
    runtime_telemetry.increment(_DISPATCH_COUNTER, outcome=outcome, source=source)
    runtime_telemetry.observe(
        _DISPATCH_LATENCY_HISTOGRAM,
        latency_ms,
        outcome=outcome,
        source=source,
    )
