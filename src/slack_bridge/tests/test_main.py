"""Tests for the slack_bridge FastAPI application."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from slack_bridge.dedupe import EventDedupeCache
from slack_bridge.main import app

from nimbus_runtime import runtime_telemetry
from slack_bridge import main as bridge_main

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import Response

pytestmark = pytest.mark.unit

_TEST_SIGNING_SECRET = "test-signing-secret"


@pytest.fixture
def client() -> TestClient:
    """Return a FastAPI TestClient bound to the bridge app."""
    return TestClient(app)


@pytest.fixture
def signing_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    """Configure the Slack signing secret for tests that exercise valid auth."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _TEST_SIGNING_SECRET)
    return _TEST_SIGNING_SECRET


@pytest.fixture(autouse=True)
def fresh_dedupe_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[EventDedupeCache]:
    """Replace the module-level dedupe cache with a fresh instance per test."""
    cache = EventDedupeCache()
    monkeypatch.setattr(bridge_main, "_dedupe_cache", cache)
    yield cache


@pytest.fixture(autouse=True)
def fresh_slash_dedupe_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[EventDedupeCache]:
    """Replace the slash-command dedupe cache with a fresh instance per test."""
    cache = EventDedupeCache()
    monkeypatch.setattr(bridge_main, "_slash_dedupe_cache", cache)
    yield cache


@pytest.fixture
def captured_slash_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, str]]:
    """Replace handle_slack_command so dispatched forms can be inspected."""
    captured: list[dict[str, str]] = []

    def _record(form: dict[str, str]) -> None:
        captured.append(dict(form))

    monkeypatch.setattr(bridge_main, "handle_slack_command", _record)
    return captured


@pytest.fixture
def captured_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    """Replace handle_slack_event so dispatched events can be inspected.

    Background tasks scheduled by the route call ``handle_slack_event`` from
    ``slack_bridge.main``'s namespace. Patching it there captures the
    dispatched arguments without exercising the real Nimbus or Slack stack.
    """
    captured: list[dict[str, object]] = []

    def _record(
        *,
        team_id: str,
        event_id: str,
        event: dict[str, object],
    ) -> None:
        captured.append({"team_id": team_id, "event_id": event_id, "event": event})

    monkeypatch.setattr(bridge_main, "handle_slack_event", _record)
    return captured


def _signed_headers(
    secret: str,
    body: bytes,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Build the Slack-style signed request headers for ``body``."""
    ts = timestamp if timestamp is not None else str(int(time.time()))
    canonical = f"v0:{ts}:{body.decode('utf-8')}"
    digest = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
    }


def _post_signed(client: TestClient, secret: str, payload: object) -> Response:
    """POST a signed JSON request to ``/slack/events``."""
    body = json.dumps(payload).encode("utf-8")
    return client.post(
        "/slack/events",
        content=body,
        headers=_signed_headers(secret, body),
    )


def _user_message_event_callback(
    *,
    team_id: str = "T123",
    event_id: str = "Ev1",
    event_overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a minimal Slack event_callback payload for a user message."""
    event: dict[str, object] = {
        "type": "message",
        "user": "U1",
        "text": "hi",
        "ts": "1.2",
        "channel": "C1",
    }
    if event_overrides:
        event.update(event_overrides)
    return {
        "type": "event_callback",
        "team_id": team_id,
        "event_id": event_id,
        "event": event,
    }


def test_health_returns_ok(client: TestClient) -> None:
    """GET /health returns a 200 with a stable status payload."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_slack_events_url_verification_returns_challenge(
    client: TestClient,
    signing_secret: str,
) -> None:
    """Slack URL verification echoes back the challenge string verbatim."""
    response = _post_signed(
        client,
        signing_secret,
        {"type": "url_verification", "challenge": "abc123"},
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "abc123"}


def test_slack_events_invalid_json_returns_400(
    client: TestClient,
    signing_secret: str,
) -> None:
    """Non-JSON request bodies are rejected with a clear 400 once signed."""
    body = b"not json"
    response = client.post(
        "/slack/events",
        content=body,
        headers=_signed_headers(signing_secret, body),
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "invalid_json"


def test_slack_events_non_object_payload_returns_400(
    client: TestClient,
    signing_secret: str,
) -> None:
    """JSON arrays/scalars at the top level are rejected with 400."""
    response = _post_signed(client, signing_secret, ["not", "an", "object"])
    assert response.status_code == 400
    assert response.json()["detail"] == "payload_must_be_object"


def test_slack_events_url_verification_missing_challenge_returns_400(
    client: TestClient,
    signing_secret: str,
) -> None:
    """URL verification without a challenge string is rejected."""
    response = _post_signed(client, signing_secret, {"type": "url_verification"})
    assert response.status_code == 400
    assert response.json()["detail"] == "missing_challenge"


def test_slack_events_event_callback_dispatches_to_handle_slack_event(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],
) -> None:
    """A well-formed event_callback acks 200 and schedules dispatch."""
    payload = _user_message_event_callback()
    response = _post_signed(client, signing_secret, payload)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured_dispatches == [
        {
            "team_id": "T123",
            "event_id": "Ev1",
            "event": payload["event"],
        },
    ]


def test_slack_events_unknown_type_acks(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],
) -> None:
    """Unknown payload types are acknowledged and not dispatched."""
    response = _post_signed(client, signing_secret, {"type": "some_future_type"})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured_dispatches == []


def test_slack_events_missing_signature_returns_401(
    client: TestClient,
    signing_secret: str,  # noqa: ARG001 - secret is configured but headers are intentionally omitted
) -> None:
    """Requests without Slack signature headers are rejected with 401."""
    response = client.post(
        "/slack/events",
        json={"type": "url_verification", "challenge": "abc"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_slack_signature"


def test_slack_events_wrong_signature_returns_401(
    client: TestClient,
    signing_secret: str,  # noqa: ARG001 - configured by fixture; we tamper with the signature
) -> None:
    """Requests with a wrong HMAC are rejected with 401."""
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode("utf-8")
    response = client.post(
        "/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=" + ("0" * 64),
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_slack_signature"


def test_slack_events_stale_timestamp_returns_401(
    client: TestClient,
    signing_secret: str,
) -> None:
    """Requests with timestamps outside the freshness window are rejected."""
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode("utf-8")
    stale_ts = str(int(time.time()) - 600)
    response = client.post(
        "/slack/events",
        content=body,
        headers=_signed_headers(signing_secret, body, timestamp=stale_ts),
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_slack_signature"


def test_slack_events_unconfigured_secret_returns_401(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When SLACK_SIGNING_SECRET is unset every request fails closed."""
    monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode("utf-8")
    response = client.post(
        "/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=" + ("0" * 64),
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_slack_signature"


@pytest.mark.parametrize(
    ("missing_field", "expected_detail"),
    [
        ("team_id", "missing_team_id"),
        ("event_id", "missing_event_id"),
        ("event", "missing_event"),
    ],
)
def test_slack_events_event_callback_missing_field_returns_400(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],
    missing_field: str,
    expected_detail: str,
) -> None:
    """event_callback payloads missing a required top-level field are 400."""
    payload = _user_message_event_callback()
    payload.pop(missing_field)
    response = _post_signed(client, signing_secret, payload)
    assert response.status_code == 400
    assert response.json()["detail"] == expected_detail
    assert captured_dispatches == []


def test_slack_events_filters_bot_messages(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],
) -> None:
    """Messages with bot_id are filtered to prevent self-reply loops."""
    payload = _user_message_event_callback(
        event_overrides={"bot_id": "B123"},
    )
    response = _post_signed(client, signing_secret, payload)
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured_dispatches == []


def test_slack_events_filters_subtyped_messages(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],
) -> None:
    """Edit/delete and similar subtyped messages are not dispatched."""
    payload = _user_message_event_callback(
        event_overrides={"subtype": "message_changed"},
    )
    response = _post_signed(client, signing_secret, payload)
    assert response.status_code == 200
    assert captured_dispatches == []


def test_slack_events_filters_unsupported_event_type(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],
) -> None:
    """Events outside the dispatched type set are dropped at the boundary."""
    payload = _user_message_event_callback(
        event_overrides={"type": "reaction_added"},
    )
    response = _post_signed(client, signing_secret, payload)
    assert response.status_code == 200
    assert captured_dispatches == []


def test_slack_events_filters_event_missing_user(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],
) -> None:
    """Events missing required user/channel/ts fields are not dispatched."""
    payload = _user_message_event_callback()
    inner_event = payload["event"]
    assert isinstance(inner_event, dict)
    inner_event.pop("user")
    response = _post_signed(client, signing_secret, payload)
    assert response.status_code == 200
    assert captured_dispatches == []


def test_slack_events_dedupes_repeated_event_ids(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],
) -> None:
    """Repeated deliveries of the same event_id only dispatch once."""
    payload = _user_message_event_callback()
    first = _post_signed(client, signing_secret, payload)
    second = _post_signed(client, signing_secret, payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(captured_dispatches) == 1


def test_slack_events_does_not_dispatch_when_signature_invalid(
    client: TestClient,
    signing_secret: str,  # noqa: ARG001 - configured by fixture; signature is intentionally wrong
    captured_dispatches: list[dict[str, object]],
) -> None:
    """Unauthenticated requests must never reach the background dispatcher."""
    payload = _user_message_event_callback()
    body = json.dumps(payload).encode("utf-8")
    response = client.post(
        "/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=" + ("0" * 64),
        },
    )
    assert response.status_code == 401
    assert captured_dispatches == []


def test_dispatch_with_logging_swallows_handle_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Background dispatcher logs and absorbs unexpected failures."""

    def _boom(**_: object) -> None:
        msg = "downstream exploded"
        raise RuntimeError(msg)

    monkeypatch.setattr(bridge_main, "handle_slack_event", _boom)
    bridge_main._dispatch_with_logging(  # noqa: SLF001 - exercising private helper directly is the contract here
        team_id="T",
        event_id="E",
        event={"type": "message"},
    )


def _counter_keys(prefix: str) -> dict[str, int]:
    """Return runtime_telemetry counters whose key starts with ``prefix``."""
    snapshot = runtime_telemetry.snapshot()
    counters = snapshot["counters"]
    assert isinstance(counters, dict)
    return {key: count for key, count in counters.items() if key.startswith(prefix)}


def _histogram_keys(prefix: str) -> dict[str, dict[str, object]]:
    """Return runtime_telemetry histograms whose key starts with ``prefix``."""
    snapshot = runtime_telemetry.snapshot()
    histograms = snapshot["histograms"]
    assert isinstance(histograms, dict)
    return {key: state for key, state in histograms.items() if key.startswith(prefix)}


def test_telemetry_records_accepted_event_callback(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],  # noqa: ARG001 - fixture suppresses real dispatch
) -> None:
    """A dispatched event_callback updates inbound + event_callback counters."""
    payload = _user_message_event_callback()
    response = _post_signed(client, signing_secret, payload)
    assert response.status_code == 200
    inbound = _counter_keys("slack_bridge_inbound_total")
    event_callbacks = _counter_keys("slack_bridge_event_callback_total")
    assert inbound == {
        "slack_bridge_inbound_total|payload_type=event_callback,result=accepted": 1,
    }
    assert event_callbacks == {
        "slack_bridge_event_callback_total|outcome=dispatched": 1,
    }


def test_telemetry_records_rejected_signature(
    client: TestClient,
    signing_secret: str,  # noqa: ARG001 - configured but signature is intentionally wrong
) -> None:
    """A bad signature updates inbound counter with rejected_signature label."""
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode("utf-8")
    response = client.post(
        "/slack/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=" + ("0" * 64),
        },
    )
    assert response.status_code == 401
    inbound = _counter_keys("slack_bridge_inbound_total")
    assert inbound == {
        "slack_bridge_inbound_total|payload_type=unknown,result=rejected_signature": 1,
    }


def test_telemetry_records_filtered_and_duplicate(
    client: TestClient,
    signing_secret: str,
    captured_dispatches: list[dict[str, object]],  # noqa: ARG001 - fixture suppresses real dispatch
) -> None:
    """Filtered and duplicate paths update event_callback counter accordingly."""
    bot_payload = _user_message_event_callback(
        event_id="EvBot",
        event_overrides={"bot_id": "B1"},
    )
    user_payload = _user_message_event_callback(event_id="EvUser")
    assert _post_signed(client, signing_secret, bot_payload).status_code == 200
    assert _post_signed(client, signing_secret, user_payload).status_code == 200
    assert _post_signed(client, signing_secret, user_payload).status_code == 200
    counters = _counter_keys("slack_bridge_event_callback_total")
    assert counters == {
        "slack_bridge_event_callback_total|outcome=filtered": 1,
        "slack_bridge_event_callback_total|outcome=dispatched": 1,
        "slack_bridge_event_callback_total|outcome=duplicate": 1,
    }


def test_telemetry_records_dispatch_success_and_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful background dispatch records success counter + latency histogram."""

    def _ok(**_: object) -> None:
        return None

    monkeypatch.setattr(bridge_main, "handle_slack_event", _ok)
    bridge_main._dispatch_with_logging(  # noqa: SLF001 - direct private-helper test
        team_id="T",
        event_id="E",
        event={"type": "message"},
    )
    assert _counter_keys("slack_bridge_dispatch_total") == {
        "slack_bridge_dispatch_total|outcome=success,source=event": 1,
    }
    histograms = _histogram_keys("slack_bridge_dispatch_latency_ms")
    assert list(histograms) == [
        "slack_bridge_dispatch_latency_ms|outcome=success,source=event",
    ]
    state = histograms["slack_bridge_dispatch_latency_ms|outcome=success,source=event"]
    assert state["count"] == 1


def test_telemetry_records_dispatch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch exception records the failure counter and histogram."""

    def _boom(**_: object) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(bridge_main, "handle_slack_event", _boom)
    bridge_main._dispatch_with_logging(  # noqa: SLF001 - direct private-helper test
        team_id="T",
        event_id="E",
        event={"type": "message"},
    )
    assert _counter_keys("slack_bridge_dispatch_total") == {
        "slack_bridge_dispatch_total|outcome=failure,source=event": 1,
    }
    histograms = _histogram_keys("slack_bridge_dispatch_latency_ms")
    assert list(histograms) == [
        "slack_bridge_dispatch_latency_ms|outcome=failure,source=event",
    ]


def _slash_form(**overrides: str) -> dict[str, str]:
    """Build a baseline slash-command form payload for HTTP tests."""
    base = {
        "team_id": "T123",
        "trigger_id": "trig-1",
        "channel_id": "C9",
        "user_id": "U7",
        "text": "list reports/",
        "command": "/nimbus",
    }
    base.update(overrides)
    return base


def _post_signed_form(
    client: TestClient,
    secret: str,
    form: dict[str, str],
) -> Response:
    """POST a signed form-encoded request to ``/slack/commands``."""
    body = "&".join(f"{key}={value}" for key, value in form.items()).encode("utf-8")
    headers = _signed_headers(secret, body)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    return client.post("/slack/commands", content=body, headers=headers)


def test_slash_command_dispatches_with_form_payload(
    client: TestClient,
    signing_secret: str,
    captured_slash_dispatches: list[dict[str, str]],
) -> None:
    """A signed slash command schedules dispatch and acks 200 with empty body."""
    response = _post_signed_form(client, signing_secret, _slash_form())
    assert response.status_code == 200
    assert response.json() == {}
    assert captured_slash_dispatches == [_slash_form()]


def test_slash_command_missing_signature_returns_401(client: TestClient) -> None:
    """Slash commands without Slack signature headers are rejected with 401."""
    body = b"team_id=T&trigger_id=t&channel_id=C&user_id=U&command=/n&text="
    response = client.post(
        "/slack/commands",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_slack_signature"


def test_slash_command_wrong_signature_returns_401(
    client: TestClient,
    signing_secret: str,  # noqa: ARG001 - configured by fixture; signature tampered
) -> None:
    """Slash commands with a wrong HMAC are rejected with 401."""
    body = b"team_id=T&trigger_id=t&channel_id=C&user_id=U&command=/n&text="
    response = client.post(
        "/slack/commands",
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Request-Timestamp": str(int(time.time())),
            "X-Slack-Signature": "v0=" + ("0" * 64),
        },
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "invalid_slack_signature"


@pytest.mark.parametrize(
    "missing_field",
    ["team_id", "trigger_id", "channel_id", "user_id", "command"],
)
def test_slash_command_missing_required_field_returns_400(
    client: TestClient,
    signing_secret: str,
    captured_slash_dispatches: list[dict[str, str]],
    missing_field: str,
) -> None:
    """Slash payloads missing a required field are rejected before dispatch."""
    form = _slash_form()
    del form[missing_field]
    response = _post_signed_form(client, signing_secret, form)
    assert response.status_code == 400
    assert response.json()["detail"] == f"missing_{missing_field}"
    assert captured_slash_dispatches == []


def test_slash_command_accepts_empty_text(
    client: TestClient,
    signing_secret: str,
    captured_slash_dispatches: list[dict[str, str]],
) -> None:
    """Empty text is accepted; the user typed only ``/nimbus`` with no args."""
    form = _slash_form(text="")
    response = _post_signed_form(client, signing_secret, form)
    assert response.status_code == 200
    assert len(captured_slash_dispatches) == 1


def test_slash_command_dedupes_repeated_trigger_ids(
    client: TestClient,
    signing_secret: str,
    captured_slash_dispatches: list[dict[str, str]],
) -> None:
    """Repeated deliveries of the same trigger_id only dispatch once."""
    form = _slash_form()
    first = _post_signed_form(client, signing_secret, form)
    second = _post_signed_form(client, signing_secret, form)
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(captured_slash_dispatches) == 1


def test_slash_command_telemetry_records_dispatched(
    client: TestClient,
    signing_secret: str,
    captured_slash_dispatches: list[dict[str, str]],  # noqa: ARG001 - fixture suppresses real dispatch
) -> None:
    """A dispatched slash command updates the slash-specific counters."""
    response = _post_signed_form(client, signing_secret, _slash_form())
    assert response.status_code == 200
    inbound = _counter_keys("slack_bridge_slash_inbound_total")
    slash_outcomes = _counter_keys("slack_bridge_slash_command_total")
    assert inbound == {"slack_bridge_slash_inbound_total|result=accepted": 1}
    assert slash_outcomes == {"slack_bridge_slash_command_total|outcome=dispatched": 1}


def test_slash_command_telemetry_records_duplicate_and_rejected(
    client: TestClient,
    signing_secret: str,
    captured_slash_dispatches: list[dict[str, str]],  # noqa: ARG001 - fixture suppresses real dispatch
) -> None:
    """Duplicate trigger_ids and missing-field rejections show in telemetry."""
    form = _slash_form()
    bad_form = _slash_form()
    del bad_form["channel_id"]

    assert _post_signed_form(client, signing_secret, bad_form).status_code == 400
    assert _post_signed_form(client, signing_secret, form).status_code == 200
    assert _post_signed_form(client, signing_secret, form).status_code == 200

    inbound = _counter_keys("slack_bridge_slash_inbound_total")
    slash_outcomes = _counter_keys("slack_bridge_slash_command_total")
    assert inbound == {
        "slack_bridge_slash_inbound_total|result=accepted": 2,
        "slack_bridge_slash_inbound_total|result=rejected_payload": 1,
    }
    assert slash_outcomes == {
        "slack_bridge_slash_command_total|outcome=dispatched": 1,
        "slack_bridge_slash_command_total|outcome=duplicate": 1,
    }


def test_slash_command_dispatch_records_slash_command_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The slash dispatcher records dispatch outcome with source=slash_command."""

    def _ok(form: dict[str, str]) -> None:  # noqa: ARG001
        return None

    monkeypatch.setattr(bridge_main, "handle_slack_command", _ok)
    bridge_main._dispatch_command_with_logging(  # noqa: SLF001 - direct private-helper test
        form={"team_id": "T", "trigger_id": "t", "command": "/n"},
    )
    assert _counter_keys("slack_bridge_dispatch_total") == {
        "slack_bridge_dispatch_total|outcome=success,source=slash_command": 1,
    }


def test_slash_command_dispatch_records_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slash dispatch exception records the failure counter and histogram."""

    def _boom(form: dict[str, str]) -> None:  # noqa: ARG001
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(bridge_main, "handle_slack_command", _boom)
    bridge_main._dispatch_command_with_logging(  # noqa: SLF001 - direct private-helper test
        form={"team_id": "T", "trigger_id": "t", "command": "/n"},
    )
    assert _counter_keys("slack_bridge_dispatch_total") == {
        "slack_bridge_dispatch_total|outcome=failure,source=slash_command": 1,
    }
    histograms = _histogram_keys("slack_bridge_dispatch_latency_ms")
    assert list(histograms) == [
        "slack_bridge_dispatch_latency_ms|outcome=failure,source=slash_command",
    ]
