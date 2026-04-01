"""Tests for GitHub OAuth routes and session dependency."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from aws_client_service.deps import require_oauth_session
from aws_client_service.routes.auth import router as auth_router
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

if TYPE_CHECKING:
    import pytest

HTTP_OK = 200
HTTP_FOUND = 302
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401


def create_test_app() -> FastAPI:
    """Create a test FastAPI app with session middleware and auth router."""
    app = FastAPI()
    app.add_middleware(
        SessionMiddleware,
        secret_key="test-secret-key",  # noqa: S106 - test-only secret
    )
    app.include_router(auth_router)

    @app.get("/protected")
    def protected(
        _: Annotated[str, Depends(require_oauth_session)],
    ) -> dict[str, bool]:
        return {"ok": True}

    return app


def test_auth_login_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that /auth/login redirects to GitHub and stores state."""

    def mock_build_github_auth_url() -> tuple[str, str]:
        return "https://github.com/login/oauth/authorize?state=test-state", "test-state"

    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        mock_build_github_auth_url,
    )

    client = TestClient(create_test_app())
    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == HTTP_FOUND
    assert response.headers["location"] == (
        "https://github.com/login/oauth/authorize?state=test-state"
    )


def test_auth_callback_rejects_invalid_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that /auth/callback rejects mismatched state."""

    def mock_build_github_auth_url() -> tuple[str, str]:
        return "https://github.com/login/oauth/authorize?state=test-state", "test-state"

    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        mock_build_github_auth_url,
    )

    client = TestClient(create_test_app())
    client.get("/auth/login", follow_redirects=False)

    response = client.get(
        "/auth/callback",
        params={"code": "abc123", "state": "wrong-state"},
    )

    assert response.status_code == HTTP_BAD_REQUEST
    assert response.json()["detail"] == "Invalid OAuth state"


def test_auth_callback_stores_token_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that /auth/callback succeeds when state matches."""

    def mock_build_github_auth_url() -> tuple[str, str]:
        return "https://github.com/login/oauth/authorize?state=test-state", "test-state"

    def mock_exchange_code_for_token(code: str) -> str:
        assert code == "abc123"
        return "fake-token"

    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        mock_build_github_auth_url,
    )
    monkeypatch.setattr(
        "aws_client_service.routes.auth.exchange_code_for_token",
        mock_exchange_code_for_token,
    )

    client = TestClient(create_test_app())
    client.get("/auth/login", follow_redirects=False)

    response = client.get(
        "/auth/callback",
        params={"code": "abc123", "state": "test-state"},
    )

    assert response.status_code == HTTP_OK
    assert response.json()["message"] == "OAuth login successful"

    protected_response = client.get("/protected")
    assert protected_response.status_code == HTTP_OK
    assert protected_response.json() == {"ok": True}


def test_require_oauth_session_accepts_x_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test protected route accepts a configured X-API-Key header."""
    monkeypatch.setenv("API_KEY", "test-api-key")

    client = TestClient(create_test_app())
    response = client.get("/protected", headers={"X-API-Key": "test-api-key"})

    assert response.status_code == HTTP_OK
    assert response.json() == {"ok": True}


def test_require_oauth_session_accepts_bearer_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test protected route accepts a configured bearer API key."""
    monkeypatch.setenv("API_KEY", "test-api-key")

    client = TestClient(create_test_app())
    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer test-api-key"},
    )

    assert response.status_code == HTTP_OK
    assert response.json() == {"ok": True}


def test_require_oauth_session_rejects_missing_token() -> None:
    """Test protected route rejects requests without OAuth session."""
    client = TestClient(create_test_app())
    response = client.get("/protected")

    assert response.status_code == HTTP_UNAUTHORIZED
    assert response.json()["detail"] == "Authentication required"
