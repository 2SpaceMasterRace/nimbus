"""Tests for the API-key authentication dependency."""

from __future__ import annotations

import pytest
from ai_server.router import get_ai_client, router
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestMissingKey:
    def test_returns_401_when_header_absent(self, client: TestClient) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hello", "session_id": "test"},
        )
        assert resp.status_code == 401

    def test_returns_401_when_header_empty(self, client: TestClient) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hello", "session_id": "test"},
            headers={"X-API-Key": ""},
        )
        assert resp.status_code == 401


class TestWrongKey:
    def test_returns_401_for_wrong_key(self, client: TestClient) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hello", "session_id": "test"},
            headers={"X-API-Key": "definitely-wrong"},
        )
        assert resp.status_code == 401

    def test_error_detail_is_informative(self, client: TestClient) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hello", "session_id": "test"},
            headers={"X-API-Key": "bad"},
        )
        assert "key" in resp.json()["detail"].lower()


class TestUnconfiguredServer:
    def test_returns_503_when_env_var_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If ``AI_SERVER_API_KEY`` is not set, the server should say so."""
        monkeypatch.delenv("AI_SERVER_API_KEY", raising=False)
        bare_app = FastAPI()
        bare_app.include_router(router, prefix="/ai")
        bare_app.dependency_overrides[get_ai_client] = lambda: None  # type: ignore[arg-type]
        resp = TestClient(bare_app).post(
            "/ai/chat",
            json={"message": "hello", "session_id": "test"},
            headers={"X-API-Key": "any-key"},
        )
        assert resp.status_code == 503


class TestCorrectKey:
    def test_correct_key_reaches_handler(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hello", "session_id": "test-session"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_health_requires_no_key(self, client: TestClient) -> None:
        resp = client.get("/ai/health")
        assert resp.status_code == 200
