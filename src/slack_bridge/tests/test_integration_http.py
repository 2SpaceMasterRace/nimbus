"""Integration test for the slack_bridge HTTP -> background -> chat path.

Other tests in this package patch ``slack_bridge.main.handle_slack_event``
to capture dispatch arguments without exercising the rest of the stack.
This file goes one layer further: it patches only the **leaf** boundaries
(``call_nimbus`` and the chat-client resolver) and lets the real bridge
route, the real ``BackgroundTasks`` plumbing, the real
``handle_slack_event``, and the real ``render_for_chat`` run.

The goal is to catch wiring bugs that captured-dispatch tests cannot:
namespacing of monkeypatches, body translation under the bridge's own
event filter, idempotency-key shape, telemetry counter coverage, and
that the chat-client receives the rendered text after the response has
already been returned.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from nimbus_runtime.models import ChatTurnResult
from slack_bridge.dedupe import EventDedupeCache
from slack_bridge.main import app

from nimbus_runtime import runtime_telemetry
from slack_bridge import flow as bridge_flow
from slack_bridge import main as bridge_main

if TYPE_CHECKING:
    from collections.abc import Iterator

    from slack_bridge.models import NimbusTurnRequest

pytestmark = pytest.mark.integration

_TEST_SIGNING_SECRET = "integration-secret"


@dataclass
class _RecordingChatClient:
    """In-memory ChatClient used to assert what the bridge actually posts."""

    sent: list[tuple[str, str]] = field(default_factory=list)

    def send_message(self, channel_id: str, text: str) -> object:
        """Record a send call and return a minimal message-like object."""
        self.sent.append((channel_id, text))
        return None


@pytest.fixture
def client() -> TestClient:
    """Return a FastAPI TestClient bound to the real bridge app."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the Slack signing secret used to sign integration requests."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", _TEST_SIGNING_SECRET)


@pytest.fixture(autouse=True)
def _fresh_dedupe_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Replace the bridge's dedupe cache so test ordering does not matter."""
    monkeypatch.setattr(bridge_main, "_dedupe_cache", EventDedupeCache())
    yield


@pytest.fixture
def chat_client_stub(monkeypatch: pytest.MonkeyPatch) -> _RecordingChatClient:
    """Replace the chat-client resolver in the flow module with a fake."""
    fake = _RecordingChatClient()
    monkeypatch.setattr(bridge_flow, "get_chat_client", lambda: fake)
    return fake


def _signed_headers(body: bytes) -> dict[str, str]:
    """Build Slack-style signed request headers for ``body``."""
    ts = str(int(time.time()))
    canonical = f"v0:{ts}:{body.decode('utf-8')}"
    digest = hmac.new(
        _TEST_SIGNING_SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
    }


def _post_signed(client: TestClient, payload: dict[str, object]) -> int:
    """Send a signed event_callback to /slack/events; return status code."""
    body = json.dumps(payload).encode("utf-8")
    response = client.post("/slack/events", content=body, headers=_signed_headers(body))
    return response.status_code


def _user_message_payload() -> dict[str, object]:
    """Build a Slack user-authored message event_callback payload."""
    return {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev-int-1",
        "event": {
            "type": "message",
            "user": "U1",
            "text": "<@BOT> hello there",
            "ts": "1730000000.000100",
            "channel": "C-int-1",
        },
    }


def _make_nimbus_stub(
    *,
    text: str = "Hi from Nimbus integration",
    outcome: str = "reply",
) -> tuple[list[NimbusTurnRequest], object]:
    """Return a list-capture and a stub call_nimbus that records turns."""
    captured: list[NimbusTurnRequest] = []

    def _stub(turn: NimbusTurnRequest) -> ChatTurnResult:
        captured.append(turn)
        return ChatTurnResult(
            request_id=turn.request_id or "req-int",
            conversation_id="conv-int",
            text=text,
            outcome=outcome,  # type: ignore[arg-type]
            confirmation_required=False,
            model="nimbus-runtime",
            steps=1,
            fallback_used=False,
        )

    return captured, _stub


def test_full_path_dispatches_through_real_background_task(
    client: TestClient,
    chat_client_stub: _RecordingChatClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: HTTP -> verify -> filter -> dedupe -> background -> chat.

    Only ``call_nimbus`` and ``get_chat_client`` are stubbed. Everything
    else (signature verify, JSON parse, event filter, dedupe, background
    scheduling, build_event_body, render_for_chat) runs unmodified.
    """
    captured_turns, stub = _make_nimbus_stub()
    monkeypatch.setattr(bridge_flow, "call_nimbus", stub)

    status_code = _post_signed(client, _user_message_payload())
    assert status_code == 200

    assert len(captured_turns) == 1
    turn = captured_turns[0]
    assert turn.platform == "slack"
    assert turn.workspace_id == "T123"
    assert turn.channel_id == "C-int-1"
    assert turn.user_id == "U1"
    assert turn.text == "hello there"  # <@BOT> mention stripped
    assert turn.idempotency_key == "slack:T123:event:Ev-int-1"

    assert chat_client_stub.sent == [("C-int-1", "Hi from Nimbus integration")]

    snapshot = runtime_telemetry.snapshot()
    counters = snapshot["counters"]
    assert isinstance(counters, dict)
    assert counters.get("slack_bridge_dispatch_total|outcome=success,source=event") == 1


def test_full_path_posts_user_visible_error_when_nimbus_raises(
    client: TestClient,
    chat_client_stub: _RecordingChatClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If call_nimbus raises, the user gets a fallback message via the real flow."""

    def _boom(_: NimbusTurnRequest) -> ChatTurnResult:
        msg = "Nimbus unavailable"
        raise RuntimeError(msg)

    monkeypatch.setattr(bridge_flow, "call_nimbus", _boom)

    status_code = _post_signed(client, _user_message_payload())
    assert status_code == 200

    assert len(chat_client_stub.sent) == 1
    sent_channel, sent_text = chat_client_stub.sent[0]
    assert sent_channel == "C-int-1"
    assert "AI service" in sent_text

    snapshot = runtime_telemetry.snapshot()
    counters = snapshot["counters"]
    assert isinstance(counters, dict)
    assert counters.get("slack_bridge_dispatch_total|outcome=failure,source=event") == 1


def _signed_form_headers(body: bytes) -> dict[str, str]:
    """Build Slack-style signed headers for a form-encoded body."""
    headers = _signed_headers(body)
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    return headers


def _slash_form_payload() -> dict[str, str]:
    """Build a minimal slash-command form for the integration test."""
    return {
        "team_id": "T123",
        "trigger_id": "trig-int-1",
        "channel_id": "C-int-1",
        "user_id": "U1",
        "text": "list reports/",
        "command": "/nimbus",
    }


def test_full_slash_command_path_dispatches_through_real_background_task(
    client: TestClient,
    chat_client_stub: _RecordingChatClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end slash path: HTTP form -> verify -> dedupe -> background -> chat."""
    captured_turns, stub = _make_nimbus_stub(text="Slash reply from Nimbus")
    monkeypatch.setattr(bridge_flow, "call_nimbus", stub)

    form = _slash_form_payload()
    body = "&".join(f"{key}={value}" for key, value in form.items()).encode("utf-8")
    response = client.post(
        "/slack/commands",
        content=body,
        headers=_signed_form_headers(body),
    )
    assert response.status_code == 200
    assert response.json() == {}

    assert len(captured_turns) == 1
    turn = captured_turns[0]
    assert turn.platform == "slack"
    assert turn.thread_id is None
    assert turn.message_id == "cmd:trig-int-1"
    assert turn.idempotency_key == "slack:T123:command:trig-int-1"
    assert turn.text == "list reports/"

    assert chat_client_stub.sent == [("C-int-1", "Slash reply from Nimbus")]

    snapshot = runtime_telemetry.snapshot()
    counters = snapshot["counters"]
    assert isinstance(counters, dict)
    assert (
        counters.get("slack_bridge_dispatch_total|outcome=success,source=slash_command")
        == 1
    )
