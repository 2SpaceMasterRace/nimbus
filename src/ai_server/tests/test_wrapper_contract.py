"""Contract tests for the wrapper-facing ``POST /ai/chat/turn`` endpoint."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import TYPE_CHECKING

import ai_server.auth as auth_mod
import ai_server.router as router_mod
import pytest
from fastapi.testclient import TestClient
from httpx import Response

if TYPE_CHECKING:
    from tests.conftest import FakeAIClient

TEST_SIGNING_SECRET = "test-signing-secret-xyz"


def _sign_headers(
    *,
    body: bytes,
    secret: str,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    ts = int(time.time()) if timestamp is None else timestamp
    used_nonce = nonce or f"nonce-{uuid.uuid4().hex}"
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n/ai/chat/turn\n{ts}\n{used_nonce}\n{body_digest}"
    signature = hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "X-Nimbus-Timestamp": str(ts),
        "X-Nimbus-Nonce": used_nonce,
        "X-Nimbus-Signature": signature,
    }


def _turn_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "platform": "slack",
        "workspace_id": "T123TEAM",
        "channel_id": "C123CHAN",
        "thread_id": "1713840000.123456",
        "message_id": "1713840000.123457",
        "user_id": "U123USER",
        "text": "What files are under reports/?",
        "idempotency_key": "slack:T123TEAM:event:evt-123",
        "request_id": "req-wrapper-123",
    }
    body.update(overrides)
    return body


def _encoded_json_body(body: dict[str, object]) -> bytes:
    return json.dumps(body).encode("utf-8")


def _post_signed_turn(
    client: TestClient,
    *,
    body: dict[str, object],
    secret: str,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> Response:
    encoded = _encoded_json_body(body)
    headers = _sign_headers(
        body=encoded,
        secret=secret,
        timestamp=timestamp,
        nonce=nonce,
    )
    headers["Content-Type"] = "application/json"
    return client.post("/ai/chat/turn", content=encoded, headers=headers)


@pytest.fixture(autouse=True)
def _clear_wrapper_caches() -> None:
    auth_mod._seen_nonces.clear()  # noqa: SLF001
    router_mod._idempotent_turns.clear()  # noqa: SLF001


class TestChatTurnContract:
    def test_returns_200_and_normalized_contract_fields(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body()

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["request_id"] == "req-wrapper-123"
        assert payload["conversation_id"] == "slack:T123TEAM:C123CHAN:1713840000.123456"
        assert payload["text"] == "Hello from Nimbus!"
        assert payload["outcome"] == "reply"
        assert payload["confirmation_required"] is False
        assert payload["suggested_next_actions"] == []
        assert payload["model"] == "test-model:free"
        assert payload["steps"] == 1
        assert payload["fallback_used"] is False
        assert fake_client.calls[-1]["conv"].session_id == payload["conversation_id"]

    def test_uses_message_id_when_thread_id_is_missing(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(thread_id=None, request_id="req-no-thread")

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert (
            resp.json()["conversation_id"]
            == "slack:T123TEAM:C123CHAN:1713840000.123457"
        )

    def test_generates_request_id_when_omitted(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id=None)

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert resp.json()["request_id"].startswith("req-")


class TestChatTurnAuth:
    def test_rejects_missing_signed_headers(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        resp = client.post("/ai/chat/turn", json=_turn_body())

        assert resp.status_code == 401
        assert "signed" in resp.json()["detail"].lower()

    def test_rejects_invalid_signature(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body()
        encoded = _encoded_json_body(body)
        headers = {
            "X-Nimbus-Timestamp": str(int(time.time())),
            "X-Nimbus-Nonce": f"nonce-{uuid.uuid4().hex}",
            "X-Nimbus-Signature": "not-the-right-signature",
            "Content-Type": "application/json",
        }

        resp = client.post("/ai/chat/turn", content=encoded, headers=headers)

        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    def test_rejects_replayed_nonce(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-replay")
        nonce = f"nonce-{uuid.uuid4().hex}"

        first = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            nonce=nonce,
        )
        second = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            nonce=nonce,
        )

        assert first.status_code == 200
        assert second.status_code == 401
        assert "nonce" in second.json()["detail"].lower()

    def test_rejects_stale_timestamp(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-stale")

        resp = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            timestamp=int(time.time()) - 1000,
        )

        assert resp.status_code == 401
        assert "timestamp" in resp.json()["detail"].lower()


class TestChatTurnIdempotency:
    def test_duplicate_idempotency_key_reuses_cached_response(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-idempotent")

        first = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)
        second = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert len(fake_client.calls) == 1


class TestChatTurnValidation:
    def test_requires_user_id(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body()
        body.pop("user_id")

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 422
