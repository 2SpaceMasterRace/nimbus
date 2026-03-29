
"""Tests for GitHub OAuth routes and session dependency."""

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from aws_client_service.deps import require_oauth_session
from aws_client_service.routes.auth import router as auth_router
from starlette.middleware.sessions import SessionMiddleware


def create_test_app() -> FastAPI:
    """Create a test FastAPI app with session middleware and auth router."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret-key")
    app.include_router(auth_router)

    @app.get("/protected")
    def protected(_: str = Depends(require_oauth_session)) -> dict[str, bool]:
        return {"ok": True}

    return app


def test_auth_login_redirects(monkeypatch) -> None:
    """Test that /auth/login redirects to GitHub and stores state."""

    def mock_build_github_auth_url() -> tuple[str, str]:
        return "https://github.com/login/oauth/authorize?state=test-state", "test-state"

    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        mock_build_github_auth_url,
    )

    client = TestClient(create_test_app())
    response = client.get("/auth/login", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://github.com/login/oauth/authorize?state=test-state"
    )


def test_auth_callback_rejects_invalid_state(monkeypatch) -> None:
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

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid OAuth state"


def test_auth_callback_stores_token_on_success(monkeypatch) -> None:
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

    assert response.status_code == 200
    assert response.json()["message"] == "OAuth login successful"

    protected_response = client.get("/protected")
    assert protected_response.status_code == 200
    assert protected_response.json() == {"ok": True}


def test_require_oauth_session_rejects_missing_token() -> None:
    """Test protected route rejects requests without OAuth session."""
    client = TestClient(create_test_app())
    response = client.get("/protected")

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
