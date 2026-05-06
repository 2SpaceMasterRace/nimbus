"""Shared fixtures for ai_server tests.

Unit / integration tests use a standalone FastAPI app with a fake AI client —
no SessionMiddleware, no AWS deps, no real OpenRouter calls.

E2E tests (marked ``@pytest.mark.e2e``) call the live deployed server.
They are skipped automatically unless both environment variables are set:
    AI_SERVER_BASE_URL  e.g. https://ospsd-team-2.fly.dev
    AI_SERVER_SIGNING_SECRET   the shared secret set via ``fly secrets set``
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from ai_server.fakes import FakeStorageClient
from ai_server.router import get_ai_client, get_storage_client, router
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_client_api import AIResponse, TokenUsage
from nimbus_runtime import runtime_telemetry

TEST_API_KEY = "test-key-abc123"


# ---------------------------------------------------------------------------
# Fake AI client used in unit / integration tests
# ---------------------------------------------------------------------------


def _make_fake_response(text: str = "Hello from Nimbus!") -> AIResponse:
    return AIResponse(
        text=text,
        model="test-model:free",
        tokens=TokenUsage(input_tokens=10, output_tokens=20),
        tool_calls=(),
        latency_ms=50,
        stop_reason="end_turn",
        steps=1,
        fallback_used=False,
    )


class FakeAIClient:
    """Stand-in for ``OpenRouterClient`` that returns a canned reply.

    Exposes ``calls`` so tests can assert on what was sent to the model.
    """

    def __init__(self, response: AIResponse | None = None) -> None:
        self._response = response or _make_fake_response()
        self.calls: list[dict[str, Any]] = []

    def send_message(
        self, conv: Any, *, tools: Any = None, **_kwargs: Any
    ) -> AIResponse:
        self.calls.append({"conv": conv, "tools": tools})
        return self._response

    def on_event(self, _listener: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Unit / integration fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_ai_response() -> AIResponse:
    """Canned ``AIResponse`` returned by the fake client."""
    return _make_fake_response()


@pytest.fixture(autouse=True)
def _reset_runtime_telemetry() -> None:
    runtime_telemetry.reset()


@pytest.fixture
def fake_client(fake_ai_response: AIResponse) -> FakeAIClient:
    """``OpenRouterClient`` stand-in used in unit / integration tests."""
    return FakeAIClient(fake_ai_response)


@pytest.fixture
def fake_storage_client() -> FakeStorageClient:
    """Cloud-storage client stand-in used for wrapper-route tool wiring tests."""
    return FakeStorageClient()


@pytest.fixture
def app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    fake_client: FakeAIClient,
    fake_storage_client: FakeStorageClient,
) -> FastAPI:
    """Isolated FastAPI app with the AI router and overridden dependencies."""
    monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-key-not-used")
    monkeypatch.setenv("AWS_BUCKET_NAME", "test-wrapper-bucket")

    test_app = FastAPI()
    test_app.include_router(router, prefix="/ai")
    test_app.dependency_overrides[get_ai_client] = lambda: fake_client
    test_app.dependency_overrides[get_storage_client] = lambda: fake_storage_client
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """``TestClient`` wrapping the isolated test app."""
    return TestClient(app)


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Headers carrying the test API key."""
    return {"X-API-Key": TEST_API_KEY}


# ---------------------------------------------------------------------------
# E2E fixtures — skipped when credentials are absent
# ---------------------------------------------------------------------------


def _e2e_base_url() -> str | None:
    return os.environ.get("AI_SERVER_BASE_URL", "").strip() or None


def _e2e_signing_secret() -> str | None:
    return os.environ.get("AI_SERVER_SIGNING_SECRET", "").strip() or None


def _run_ai_server_e2e() -> bool:
    raw = os.environ.get("RUN_AI_SERVER_E2E", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@pytest.fixture(scope="session")
def e2e_base_url() -> str:
    if not _run_ai_server_e2e():
        pytest.skip("RUN_AI_SERVER_E2E is not enabled — skipping ai_server e2e tests")
    url = _e2e_base_url()
    if not url:
        pytest.skip("AI_SERVER_BASE_URL not set — skipping e2e tests")
    return url.rstrip("/")


@pytest.fixture(scope="session")
def e2e_signing_secret() -> str:
    if not _run_ai_server_e2e():
        pytest.skip("RUN_AI_SERVER_E2E is not enabled — skipping ai_server e2e tests")
    secret = _e2e_signing_secret()
    if not secret:
        pytest.skip(
            "AI_SERVER_SIGNING_SECRET not set — skipping wrapper-route e2e tests"
        )
    return secret
