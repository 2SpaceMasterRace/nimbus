"""Integration tests for the supported AI router surfaces.

These tests run against a standalone FastAPI app with a fake AI client.
No real OpenRouter calls are made; no AWS credentials are needed.
"""

from __future__ import annotations

import pytest
from ai_server.wrapper_client import (
    build_message_event_turn,
    encode_turn_body,
    sign_nimbus_request,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

TEST_SIGNING_SECRET = "test-signing-secret-xyz"


def _post_signed_turn(
    client: TestClient,
    *,
    event_id: str,
    text: str,
    thread_ts: str | None = None,
    message_ts: str = "1713840000.123456",
) -> object:
    event: dict[str, object] = {
        "channel": "C123CHAN",
        "ts": message_ts,
        "user": "U123USER",
        "text": text,
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    body = build_message_event_turn(
        workspace_id="T123TEAM",
        event_id=event_id,
        event=event,
    )
    encoded = encode_turn_body(body)
    headers = sign_nimbus_request(body=encoded, secret=TEST_SIGNING_SECRET)
    return client.post("/ai/chat/turn", content=encoded, headers=headers)


class TestHealth:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/ai/health").status_code == 200

    def test_body_has_status_ok(self, client: TestClient) -> None:
        body = client.get("/ai/health").json()
        assert body["status"] == "ok"
        assert body["service"] == "ai-server"

    def test_no_auth_required(self, client: TestClient) -> None:
        assert client.get("/ai/health").status_code == 200


class TestRemovedLegacyChatRoute:
    def test_legacy_chat_route_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "Hello!", "session_id": "chan-001"},
            headers={"X-API-Key": "ignored-for-404"},
        )
        assert resp.status_code == 404


class TestSessionHistory:
    def test_history_returns_404_for_missing_session(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.get(
            "/ai/sessions/nonexistent-session/history", headers=auth_headers
        )
        assert resp.status_code == 404

    def test_history_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/ai/sessions/some-session/history")
        assert resp.status_code in (401, 403)

    def test_history_returns_messages_after_wrapper_turn(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        auth_headers: dict[str, str],
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        turn = _post_signed_turn(
            client,
            event_id="evt-history",
            text="history test",
        )
        assert turn.status_code == 200
        conversation_id = turn.json()["conversation_id"]

        resp = client.get(
            f"/ai/sessions/{conversation_id}/history",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == conversation_id
        assert body["message_count"] >= 1
        contents = [message["content"] for message in body["messages"]]
        assert "history test" in contents


class TestSessionDelete:
    def test_delete_nonexistent_returns_deleted_false(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.delete("/ai/sessions/never-existed", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is False
        assert resp.json()["session_id"] == "never-existed"

    def test_delete_existing_session_returns_deleted_true(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        auth_headers: dict[str, str],
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        turn = _post_signed_turn(
            client,
            event_id="evt-delete",
            text="to be deleted",
        )
        assert turn.status_code == 200
        conversation_id = turn.json()["conversation_id"]

        resp = client.delete(f"/ai/sessions/{conversation_id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_is_idempotent(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp1 = client.delete("/ai/sessions/idempotent-del", headers=auth_headers)
        resp2 = client.delete("/ai/sessions/idempotent-del", headers=auth_headers)
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp2.json()["deleted"] is False

    def test_delete_requires_auth(self, client: TestClient) -> None:
        resp = client.delete("/ai/sessions/any-session")
        assert resp.status_code in (401, 403)
