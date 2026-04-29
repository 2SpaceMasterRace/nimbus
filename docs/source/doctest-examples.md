# Executable Examples

This page keeps a small set of examples that can run under Sphinx doctest. The
examples intentionally avoid AWS, OpenRouter, and live HTTP calls.

## Conversation basics

```{doctest}
>>> from ai_client_api import Conversation
>>> conv = Conversation(system="You are concise.", session_id="docs")
>>> conv.add_user("hello")
>>> [message.role.value for message in conv.messages()]
['system', 'user']
>>> conv.to_json()["session_id"]
'docs'
```

## Tool normalization

```{doctest}
>>> from ai_client_api import normalize_tools
>>> normalize_tools(None)
()
>>> normalize_tools([])
()
```

## Runtime telemetry snapshot

```{doctest}
>>> from nimbus_runtime.telemetry import RuntimeTelemetry
>>> telemetry = RuntimeTelemetry()
>>> telemetry.record_wrapper_turn(platform="slack", outcome="reply", latency_ms=12)
>>> snapshot = telemetry.snapshot()
>>> snapshot["counters"]["nimbus_wrapper_turns_total|outcome=reply,platform=slack"]
1
>>> snapshot["histograms"]["nimbus_wrapper_turn_latency_ms|platform=slack"]["count"]
1
```

## What belongs here

Add examples here when they can run deterministically without credentials,
network access, wall-clock sleeps, or filesystem state shared across examples.
Examples that need S3, OpenRouter, or FastAPI should live in normal pytest tests
or task guides instead.
