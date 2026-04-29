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

    def test_health_stays_up_when_dependencies_are_missing(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AI_SERVER_SIGNING_SECRET", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        assert client.get("/ai/health").status_code == 200


class TestReadiness:
    def test_ready_fails_closed_for_missing_secrets(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("AI_SERVER_SIGNING_SECRET", raising=False)

        resp = client.get("/ai/ready")

        assert resp.status_code == 503
        failures = resp.json()["detail"]["failures"]
        assert "missing env var: AI_SERVER_SIGNING_SECRET" in failures

    def test_ready_requires_storage_credentials_when_tools_are_configured(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        monkeypatch.setenv("AWS_BUCKET_NAME", "test-wrapper-bucket")
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)

        resp = client.get("/ai/ready")

        assert resp.status_code == 503
        failures = resp.json()["detail"]["failures"]
        assert "missing env var: AWS_ACCESS_KEY_ID" in failures
        assert "missing env var: AWS_SECRET_ACCESS_KEY" in failures
        assert "missing env var: AWS_REGION" in failures

    def test_ready_succeeds_when_required_configuration_is_present(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.delenv("NIMBUS_STATE_BACKEND", raising=False)

        resp = client.get("/ai/ready")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"
        assert resp.json()["state_backend"] == "file"

    def test_ready_fails_closed_when_postgres_schema_is_not_ready(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import ai_server.router as router_mod
        from nimbus_runtime.postgres import PostgresStateError

        def fail_ready() -> None:
            msg = "Postgres runtime state schema is missing or out of date"
            raise PostgresStateError(msg)

        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("NIMBUS_STATE_BACKEND", "postgres")
        monkeypatch.setattr(router_mod, "check_ready", fail_ready)

        resp = client.get("/ai/ready")

        assert resp.status_code == 503
        failures = resp.json()["detail"]["failures"]
        assert "Postgres runtime state schema is missing or out of date" in failures

    def test_ready_fails_when_postgres_is_disabled_by_flag(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        monkeypatch.setenv("NIMBUS_STATE_BACKEND", "postgres")
        monkeypatch.setenv("NIMBUS_FLAG_POSTGRES_STATE_ENABLED", "off")

        resp = client.get("/ai/ready")

        assert resp.status_code == 503
        failures = resp.json()["detail"]["failures"]
        assert "Postgres state is disabled by feature flag" in failures


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
