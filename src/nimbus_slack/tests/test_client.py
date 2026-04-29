"""Tests for nimbus_slack.client."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from nimbus_runtime.models import ChatTurnResult, ConfirmationDetails
from nimbus_slack.client import (
    _parse_result,
    call_nimbus,
    encode_body,
    sign_request,
)
from nimbus_slack.models import NimbusTurnRequest

pytestmark = pytest.mark.unit

_SIGNED_PATH = "/ai/chat/turn"
_SIGNING_SECRET = "test-signing-secret"


@dataclass(frozen=True, slots=True)
class _FakeUUID:
    """Minimal UUID-like value with a deterministic hex string."""

    hex: str


def _sample_turn() -> NimbusTurnRequest:
    """Build a deterministic Nimbus turn request."""
    return NimbusTurnRequest(
        platform="slack",
        workspace_id="T123",
        channel_id="C999",
        message_id="1710000000.123456",
        user_id="U999",
        text="hello",
        idempotency_key="slack:T123:event:Ev1",
        thread_id="1710000000.123456",
        request_id="slack-Ev1",
    )


def _response_payload(**overrides: object) -> dict[str, object]:
    """Return a valid wrapper response payload with optional overrides."""
    payload: dict[str, object] = {
        "request_id": "slack-Ev1",
        "conversation_id": "slack:T123:C999:1710000000.123456",
        "text": "Hi from Nimbus",
        "outcome": "reply",
        "confirmation_required": False,
        "suggested_next_actions": [],
        "model": "nimbus-runtime",
        "steps": 0,
        "fallback_used": False,
        "confirmation": None,
    }
    payload.update(overrides)
    return payload


def test_encode_body_uses_compact_json() -> None:
    """Request serialization should produce the exact bytes that get signed."""
    encoded = encode_body(_sample_turn())

    assert b" " not in encoded
    assert json.loads(encoded) == {
        "platform": "slack",
        "workspace_id": "T123",
        "channel_id": "C999",
        "message_id": "1710000000.123456",
        "user_id": "U999",
        "text": "hello",
        "idempotency_key": "slack:T123:event:Ev1",
        "thread_id": "1710000000.123456",
        "request_id": "slack-Ev1",
        "attachments": [],
    }


def test_sign_request_builds_expected_hmac_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Signing should cover method, path, timestamp, nonce, and body digest."""
    body = b'{"hello":"world"}'
    monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", _SIGNING_SECRET)
    monkeypatch.setattr("nimbus_slack.client.time.time", lambda: 1234.9)
    monkeypatch.setattr(
        uuid,
        "uuid4",
        lambda: _FakeUUID("nonceabc"),
    )

    headers = sign_request(body)

    body_digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n{_SIGNED_PATH}\n1234\nnonceabc\n{body_digest}"
    expected = hmac.new(
        _SIGNING_SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert headers == {
        "Content-Type": "application/json",
        "X-Nimbus-Timestamp": "1234",
        "X-Nimbus-Nonce": "nonceabc",
        "X-Nimbus-Signature": expected,
    }


def test_sign_request_requires_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing signing secret should fail before any HTTP request is made."""
    monkeypatch.delenv("AI_SERVER_SIGNING_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="AI_SERVER_SIGNING_SECRET"):
        sign_request(b"{}")


def test_parse_result_accepts_confirmation_response() -> None:
    """Machine-readable confirmation payloads should round-trip cleanly."""
    payload = _response_payload(
        outcome="confirmation_required",
        confirmation_required=True,
        confirmation={
            "action_id": "act-123",
            "kind": "delete_file",
            "prompt": "Delete reports/old.csv?",
            "expected_reply": "yes, delete reports/old.csv",
            "expires_at": "2026-05-08T20:00:00+00:00",
        },
        suggested_next_actions=["yes, delete reports/old.csv"],
    )

    result = _parse_result(payload)

    assert result == ChatTurnResult(
        request_id="slack-Ev1",
        conversation_id="slack:T123:C999:1710000000.123456",
        text="Hi from Nimbus",
        outcome="confirmation_required",
        confirmation_required=True,
        suggested_next_actions=("yes, delete reports/old.csv",),
        model="nimbus-runtime",
        steps=0,
        fallback_used=False,
        confirmation=ConfirmationDetails(
            action_id="act-123",
            kind="delete_file",
            prompt="Delete reports/old.csv?",
            expected_reply="yes, delete reports/old.csv",
            expires_at="2026-05-08T20:00:00+00:00",
        ),
    )


def test_parse_result_preserves_actions_and_artifacts() -> None:
    """Slack rendering can inspect durable action and artifact summaries."""
    result = _parse_result(
        _response_payload(
            actions=[
                {
                    "action_id": "act-1",
                    "kind": "delete_file",
                    "status": "succeeded",
                    "target": {"object_name": "old.csv"},
                }
            ],
            artifacts=[
                {
                    "artifact_id": "art-1",
                    "kind": "delete_report",
                    "action_id": "act-1",
                    "payload": {"deleted": True},
                }
            ],
        )
    )

    assert result.actions[0].action_id == "act-1"
    assert result.actions[0].target == {"object_name": "old.csv"}
    assert result.artifacts[0].payload == {"deleted": True}


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ([], "JSON object"),
        (_response_payload(outcome="mystery"), "Unknown Nimbus outcome"),
        (_response_payload(confirmation=[]), "confirmation"),
        (
            _response_payload(suggested_next_actions=["ok", 1]),
            "suggested_next_actions",
        ),
        (_response_payload(steps=True), "steps"),
    ],
)
def test_parse_result_rejects_malformed_payloads(
    payload: object,
    match: str,
) -> None:
    """Malformed Nimbus responses should fail explicitly at the bridge boundary."""
    with pytest.raises((RuntimeError, TypeError), match=match):
        _parse_result(payload)


def test_call_nimbus_signs_and_posts_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """call_nimbus should send the signed body and parse the response contract."""
    calls: list[dict[str, Any]] = []
    monkeypatch.setenv("AI_SERVER_BASE_URL", "https://nimbus.example.test/")
    monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", _SIGNING_SECRET)

    def _fake_post(
        url: str,
        *,
        content: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        calls.append(
            {
                "url": url,
                "content": content,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return httpx.Response(
            status_code=200,
            json=_response_payload(),
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", _fake_post)

    result = call_nimbus(_sample_turn())

    assert result.text == "Hi from Nimbus"
    assert calls == [
        {
            "url": "https://nimbus.example.test/ai/chat/turn",
            "content": encode_body(_sample_turn()),
            "headers": calls[0]["headers"],
            "timeout": 30.0,
        }
    ]
    assert calls[0]["headers"]["Content-Type"] == "application/json"
    assert calls[0]["headers"]["X-Nimbus-Signature"]


def test_call_nimbus_requires_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bridge should fail clearly when the Nimbus base URL is absent."""
    monkeypatch.delenv("AI_SERVER_BASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="AI_SERVER_BASE_URL"):
        call_nimbus(_sample_turn())
