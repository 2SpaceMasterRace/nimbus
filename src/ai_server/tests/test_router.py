"""Integration tests for POST /ai/chat and GET /ai/health.

These tests run against a standalone FastAPI app with a fake AI client.
No real OpenRouter calls are made; no AWS credentials are needed.
"""

from __future__ import annotations

import json as _json
import os
from pathlib import Path
from typing import Any

import pytest
from ai_server.router import get_ai_client, router
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_client_api import (
    AIClientConfigError,
    AIProviderError,
    AIRateLimitError,
    AIResponse,
    AIStepBudgetExceededError,
    AITimeoutError,
    TokenUsage,
)
from tests.conftest import TEST_API_KEY, FakeAIClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_response(text: str = "Hello!") -> AIResponse:
    return AIResponse(
        text=text,
        model="test-model:free",
        tokens=TokenUsage(input_tokens=5, output_tokens=10),
        tool_calls=(),
        latency_ms=30,
        stop_reason="end_turn",
        steps=1,
        fallback_used=False,
    )


class _RaisingClient:
    """Fake client that always raises a configured exception."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def send_message(self, *_a: Any, **_kw: Any) -> AIResponse:  # noqa: ANN401
        raise self._exc

    def on_event(self, *_a: Any) -> None:  # noqa: ANN401
        pass


def _app_raising(exc: Exception) -> TestClient:
    """Return a TestClient whose AI client always raises ``exc``."""
    os.environ["AI_SERVER_API_KEY"] = TEST_API_KEY
    test_app = FastAPI()
    test_app.include_router(router, prefix="/ai")
    test_app.dependency_overrides[get_ai_client] = lambda: _RaisingClient(exc)
    return TestClient(test_app)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------


class TestHealth:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/ai/health").status_code == 200

    def test_body_has_status_ok(self, client: TestClient) -> None:
        body = client.get("/ai/health").json()
        assert body["status"] == "ok"
        assert body["service"] == "ai-server"

    def test_no_auth_required(self, client: TestClient) -> None:
        assert client.get("/ai/health").status_code == 200


# ---------------------------------------------------------------------------
# Happy-path chat
# ---------------------------------------------------------------------------


class TestChatHappyPath:
    def test_returns_200(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "Hello!", "session_id": "chan-001"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_response_contains_reply_text(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "Hello!", "session_id": "chan-001"},
            headers=auth_headers,
        )
        assert resp.json()["response"] == "Hello from Nimbus!"

    def test_response_echoes_session_id(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "my-channel"},
            headers=auth_headers,
        )
        assert resp.json()["session_id"] == "my-channel"

    def test_response_includes_model_steps_fallback(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "chan-002"},
            headers=auth_headers,
        ).json()
        assert body["model"] == "test-model:free"
        assert body["steps"] == 1
        assert body["fallback_used"] is False

    def test_user_id_is_optional(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "chan-003", "user_id": "U01ABC"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_unicode_message_accepted(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "你好 🙂 こんにちは", "session_id": "unicode-chan"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_max_length_message_accepted(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "x" * 4096, "session_id": "long-msg-chan"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_session_id_with_all_valid_special_chars(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "chan_A-B.C:D"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_session_id_at_max_length(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "a" * 128},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_tools_not_passed_to_client_in_mvp(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        fake_client: FakeAIClient,
    ) -> None:
        """MVP passes tools=None — no storage client wired yet."""
        client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "s1"},
            headers=auth_headers,
        )
        assert fake_client.calls[-1]["tools"] is None


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------


class TestRequestValidation:
    def test_rejects_empty_message(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat", json={"message": "", "session_id": "s1"}, headers=auth_headers
        )
        assert resp.status_code == 422

    def test_rejects_missing_message(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post("/ai/chat", json={"session_id": "s1"}, headers=auth_headers)
        assert resp.status_code == 422

    def test_rejects_message_over_4096_chars(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "x" * 4097, "session_id": "s1"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_rejects_missing_session_id(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post("/ai/chat", json={"message": "hi"}, headers=auth_headers)
        assert resp.status_code == 422

    def test_rejects_unsafe_session_id(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "../../etc/passwd"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_rejects_session_id_over_128_chars(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "a" * 129},
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    def test_session_file_created_after_chat(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
        client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "persist-test"},
            headers=auth_headers,
        )
        assert (tmp_path / "sessions" / "persist-test.json").is_file()

    def test_conversation_grows_across_turns(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fake_client: FakeAIClient,
        auth_headers: dict[str, str],
    ) -> None:
        monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-key-not-used")
        test_app = FastAPI()
        test_app.include_router(router, prefix="/ai")
        test_app.dependency_overrides[get_ai_client] = lambda: fake_client
        tc = TestClient(test_app)

        tc.post(
            "/ai/chat",
            json={"message": "turn one", "session_id": "multi"},
            headers=auth_headers,
        )
        tc.post(
            "/ai/chat",
            json={"message": "turn two", "session_id": "multi"},
            headers=auth_headers,
        )

        data = _json.loads((tmp_path / "sessions" / "multi.json").read_text())
        contents = [m["content"] for m in data["messages"]]
        assert "turn one" in contents
        assert "turn two" in contents

    def test_different_session_ids_are_independent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fake_client: FakeAIClient,
        auth_headers: dict[str, str],
    ) -> None:
        monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-key")
        test_app = FastAPI()
        test_app.include_router(router, prefix="/ai")
        test_app.dependency_overrides[get_ai_client] = lambda: fake_client
        tc = TestClient(test_app)

        tc.post(
            "/ai/chat",
            json={"message": "alice here", "session_id": "chan-alice"},
            headers=auth_headers,
        )
        tc.post(
            "/ai/chat",
            json={"message": "bob here", "session_id": "chan-bob"},
            headers=auth_headers,
        )

        alice_data = _json.loads(
            (tmp_path / "sessions" / "chan-alice.json").read_text()
        )
        bob_data = _json.loads((tmp_path / "sessions" / "chan-bob.json").read_text())
        alice_msgs = [m["content"] for m in alice_data["messages"]]
        bob_msgs = [m["content"] for m in bob_data["messages"]]

        assert "alice here" in alice_msgs
        assert "bob here" not in alice_msgs
        assert "bob here" in bob_msgs
        assert "alice here" not in bob_msgs


# ---------------------------------------------------------------------------
# Save failure resilience
# ---------------------------------------------------------------------------


class TestSessionSaveFailure:
    def test_response_returned_even_when_save_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
        auth_headers: dict[str, str],
    ) -> None:
        monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
        monkeypatch.setenv("AI_SESSION_DIR", "/nonexistent/readonly/path")
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-key")
        test_app = FastAPI()
        test_app.include_router(router, prefix="/ai")
        test_app.dependency_overrides[get_ai_client] = lambda: fake_client
        tc = TestClient(test_app)

        resp = tc.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "save-fail"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["response"] == "Hello from Nimbus!"


# ---------------------------------------------------------------------------
# Default session directory fallback
# ---------------------------------------------------------------------------


class TestDefaultSessionDir:
    def test_uses_default_dir_when_env_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fake_client: FakeAIClient,
        auth_headers: dict[str, str],
    ) -> None:
        monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
        monkeypatch.delenv("AI_SESSION_DIR", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-key")

        import ai_server.router as router_mod

        monkeypatch.setattr(router_mod, "_DEFAULT_SESSION_DIR", tmp_path / "default")
        test_app = FastAPI()
        test_app.include_router(router, prefix="/ai")
        test_app.dependency_overrides[get_ai_client] = lambda: fake_client
        tc = TestClient(test_app)

        resp = tc.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "default-dir-test"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert (tmp_path / "default" / "default-dir-test.json").is_file()


# ---------------------------------------------------------------------------
# Upstream error mapping
# ---------------------------------------------------------------------------


class TestUpstreamErrors:
    def test_rate_limit_returns_429(self) -> None:
        tc = _app_raising(AIRateLimitError("too many requests"))
        resp = tc.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "s1"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert resp.status_code == 429

    def test_timeout_returns_504(self) -> None:
        tc = _app_raising(AITimeoutError("timed out"))
        resp = tc.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "s1"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert resp.status_code == 504

    def test_provider_error_returns_502(self) -> None:
        tc = _app_raising(AIProviderError("bad gateway"))
        resp = tc.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "s1"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert resp.status_code == 502

    def test_step_budget_exceeded_returns_422(self) -> None:
        tc = _app_raising(AIStepBudgetExceededError("too many steps"))
        resp = tc.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "s1"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert resp.status_code == 422

    def test_config_error_returns_503(self) -> None:
        tc = _app_raising(AIClientConfigError("no api key"))
        resp = tc.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "s1"},
            headers={"X-API-Key": TEST_API_KEY},
        )
        assert resp.status_code == 503

    def test_error_responses_have_detail_field(self) -> None:
        for exc in [AIRateLimitError("x"), AITimeoutError("x"), AIProviderError("x")]:
            tc = _app_raising(exc)
            body = tc.post(
                "/ai/chat",
                json={"message": "hi", "session_id": "s1"},
                headers={"X-API-Key": TEST_API_KEY},
            ).json()
            assert "detail" in body, f"missing detail for {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Per-user rate limiting (FM10)
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """Token-bucket rate limiting keyed by user_id."""

    def test_first_request_always_allowed(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        resp = client.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "rl-1", "user_id": "user-rl-fresh"},
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_requests_without_user_id_always_allowed(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """Callers that omit user_id bypass the per-user bucket."""
        for _ in range(5):
            resp = client.post(
                "/ai/chat",
                json={"message": "hi", "session_id": "rl-anon"},
                headers=auth_headers,
            )
            assert resp.status_code == 200

    def test_exhausted_bucket_returns_429(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        auth_headers: dict[str, str],
        fake_client: FakeAIClient,
    ) -> None:
        """Once the token bucket is empty, the server returns 429."""
        import time

        import ai_server.router as router_mod
        from ai_server.router import _TokenBucket  # type: ignore[attr-defined]

        monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "rl-sessions"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake")

        uid = "user-bucket-empty-test-unique"
        # Inject a bucket with zero tokens so the next request is refused.
        router_mod._rate_buckets[uid] = _TokenBucket(  # noqa: SLF001
            tokens=0.0, last_refill=time.monotonic()
        )

        test_app = FastAPI()
        test_app.include_router(router, prefix="/ai")
        test_app.dependency_overrides[get_ai_client] = lambda: fake_client
        tc = TestClient(test_app)

        resp = tc.post(
            "/ai/chat",
            json={"message": "hi", "session_id": "rl-empty", "user_id": uid},
            headers=auth_headers,
        )
        assert resp.status_code == 429
        assert "rate limit" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Session history endpoint
# ---------------------------------------------------------------------------


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

    def test_history_returns_messages_after_chat(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fake_client: FakeAIClient,
        auth_headers: dict[str, str],
    ) -> None:
        monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
        app = FastAPI()
        app.include_router(router, prefix="/ai")
        app.dependency_overrides[get_ai_client] = lambda: fake_client
        tc = TestClient(app)

        tc.post(
            "/ai/chat",
            json={"message": "history test", "session_id": "hist-chan"},
            headers=auth_headers,
        )
        resp = tc.get("/ai/sessions/hist-chan/history", headers=auth_headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == "hist-chan"
        assert body["message_count"] >= 1
        contents = [m["content"] for m in body["messages"]]
        assert "history test" in contents

    def test_history_rejects_unsafe_session_id(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # FastAPI path routing treats slashes as path separators, so a
        # traversal attempt simply won't match the route — the status code
        # varies by version but is never 200.
        resp = client.get("/ai/sessions/../../etc/passwd/history", headers=auth_headers)
        assert resp.status_code != 200


# ---------------------------------------------------------------------------
# Session delete endpoint
# ---------------------------------------------------------------------------


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
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fake_client: FakeAIClient,
        auth_headers: dict[str, str],
    ) -> None:
        monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
        app = FastAPI()
        app.include_router(router, prefix="/ai")
        app.dependency_overrides[get_ai_client] = lambda: fake_client
        tc = TestClient(app)

        tc.post(
            "/ai/chat",
            json={"message": "to be deleted", "session_id": "del-chan"},
            headers=auth_headers,
        )

        resp = tc.delete("/ai/sessions/del-chan", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True
        assert not (tmp_path / "sessions" / "del-chan.json").exists()

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
