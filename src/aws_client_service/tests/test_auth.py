"""Tests for GitHub OAuth routes and session dependency."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import pytest
from aws_client_impl.oauth import OAuthProviderError, OAuthTransportError
from aws_client_service.deps import require_oauth_session
from aws_client_service.routes.auth import router as auth_router
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

pytestmark = pytest.mark.unit

HTTP_OK = 200
HTTP_FOUND = 302
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_BAD_GATEWAY = 502
HTTP_GATEWAY_TIMEOUT = 504
TEST_OAUTH_STATE = "test-state"
TEST_AUTH_URL = f"https://github.com/login/oauth/authorize?state={TEST_OAUTH_STATE}"
TEST_API_KEY = "test-api-key"


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


@pytest.fixture
def auth_client() -> TestClient:
    """Return a fresh test client for the OAuth routes."""
    return TestClient(create_test_app())


def _mock_build_github_auth_url() -> tuple[str, str]:
    """Return a deterministic GitHub auth redirect target."""
    return TEST_AUTH_URL, TEST_OAUTH_STATE


def test_auth_login_redirects(
    monkeypatch: pytest.MonkeyPatch,
    auth_client: TestClient,
) -> None:
    """Login starts the OAuth flow and redirects to GitHub."""
    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        _mock_build_github_auth_url,
    )
    response = auth_client.get("/auth/login", follow_redirects=False)

    assert response.status_code == HTTP_FOUND
    assert response.headers["location"] == TEST_AUTH_URL


def test_auth_callback_rejects_invalid_state(
    monkeypatch: pytest.MonkeyPatch,
    auth_client: TestClient,
) -> None:
    """Callback rejects mismatched OAuth state."""
    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        _mock_build_github_auth_url,
    )
    auth_client.get("/auth/login", follow_redirects=False)
    response = auth_client.get(
        "/auth/callback",
        params={"code": "abc123", "state": "wrong-state"},
    )

    assert response.status_code == HTTP_BAD_REQUEST
    assert response.json()["detail"] == "Invalid OAuth state"


def test_auth_callback_stores_token_on_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    auth_client: TestClient,
) -> None:
    """Callback stores the token server-side and authenticates later requests."""
    monkeypatch.setenv("OAUTH_SESSION_STORE_DIR", str(tmp_path))

    def mock_exchange_code_for_token(code: str) -> str:
        assert code == "abc123"
        return "fake-token"

    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        _mock_build_github_auth_url,
    )
    monkeypatch.setattr(
        "aws_client_service.routes.auth.exchange_code_for_token",
        mock_exchange_code_for_token,
    )
    auth_client.get("/auth/login", follow_redirects=False)
    response = auth_client.get(
        "/auth/callback",
        params={"code": "abc123", "state": TEST_OAUTH_STATE},
    )

    assert response.status_code == HTTP_OK
    assert response.json()["message"] == "OAuth login successful"

    protected_response = auth_client.get("/protected")
    assert protected_response.status_code == HTTP_OK
    assert protected_response.json() == {"ok": True}
    assert "fake-token" not in (auth_client.cookies.get("session") or "")
    stored_files = list(tmp_path.glob("*.json"))
    assert len(stored_files) == 1
    assert "fake-token" in stored_files[0].read_text(encoding="utf-8")


def test_auth_callback_clears_oauth_state_after_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    auth_client: TestClient,
) -> None:
    """Callback removes the one-time OAuth state after successful login."""
    monkeypatch.setenv("OAUTH_SESSION_STORE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        _mock_build_github_auth_url,
    )
    monkeypatch.setattr(
        "aws_client_service.routes.auth.exchange_code_for_token",
        lambda _code: "fake-token",
    )

    auth_client.get("/auth/login", follow_redirects=False)
    response = auth_client.get(
        "/auth/callback",
        params={"code": "abc123", "state": TEST_OAUTH_STATE},
    )

    assert response.status_code == HTTP_OK
    follow_up = auth_client.get(
        "/auth/callback",
        params={"code": "abc123", "state": TEST_OAUTH_STATE},
    )
    assert follow_up.status_code == HTTP_BAD_REQUEST
    assert follow_up.json()["detail"] == "Invalid OAuth state"


def test_auth_callback_maps_value_error_to_400(
    monkeypatch: pytest.MonkeyPatch,
    auth_client: TestClient,
) -> None:
    """User-facing OAuth callback errors return 400 with the original detail."""
    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        _mock_build_github_auth_url,
    )
    monkeypatch.setattr(
        "aws_client_service.routes.auth.exchange_code_for_token",
        lambda _code: (_ for _ in ()).throw(ValueError("expired code")),
    )
    auth_client.get("/auth/login", follow_redirects=False)

    response = auth_client.get(
        "/auth/callback",
        params={"code": "abc123", "state": TEST_OAUTH_STATE},
    )

    assert response.status_code == HTTP_BAD_REQUEST
    assert response.json() == {"detail": "expired code"}


def test_auth_callback_maps_transport_timeout_to_504(
    monkeypatch: pytest.MonkeyPatch,
    auth_client: TestClient,
) -> None:
    """Timeouts talking to GitHub become 504 responses."""

    def mock_exchange_code_for_token(_code: str) -> str:
        msg = "GitHub OAuth token exchange timed out"
        raise OAuthTransportError(msg)

    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        _mock_build_github_auth_url,
    )
    monkeypatch.setattr(
        "aws_client_service.routes.auth.exchange_code_for_token",
        mock_exchange_code_for_token,
    )
    auth_client.get("/auth/login", follow_redirects=False)
    response = auth_client.get(
        "/auth/callback",
        params={"code": "abc123", "state": TEST_OAUTH_STATE},
    )

    assert response.status_code == HTTP_GATEWAY_TIMEOUT


def test_auth_callback_maps_provider_failure_to_502(
    monkeypatch: pytest.MonkeyPatch,
    auth_client: TestClient,
) -> None:
    """Provider-side OAuth failures become 502 responses."""

    def mock_exchange_code_for_token(_code: str) -> str:
        msg = "GitHub OAuth token exchange returned invalid JSON"
        raise OAuthProviderError(msg)

    monkeypatch.setattr(
        "aws_client_service.routes.auth.build_github_auth_url",
        _mock_build_github_auth_url,
    )
    monkeypatch.setattr(
        "aws_client_service.routes.auth.exchange_code_for_token",
        mock_exchange_code_for_token,
    )
    auth_client.get("/auth/login", follow_redirects=False)
    response = auth_client.get(
        "/auth/callback",
        params={"code": "abc123", "state": TEST_OAUTH_STATE},
    )

    assert response.status_code == HTTP_BAD_GATEWAY


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({"X-API-Key": TEST_API_KEY}, HTTP_OK),
        ({"Authorization": f"Bearer {TEST_API_KEY}"}, HTTP_OK),
        ({"Authorization": "Bearer"}, HTTP_UNAUTHORIZED),
        ({"Authorization": "Basic test-api-key"}, HTTP_UNAUTHORIZED),
        ({"Authorization": "Bearer wrong-key"}, HTTP_UNAUTHORIZED),
        ({"X-API-Key": "wrong-key"}, HTTP_UNAUTHORIZED),
    ],
)
def test_require_oauth_session_authenticates_only_expected_api_keys(
    monkeypatch: pytest.MonkeyPatch,
    auth_client: TestClient,
    headers: dict[str, str],
    expected_status: int,
) -> None:
    """Protected routes accept only the configured API key formats."""
    monkeypatch.setenv("API_KEY", TEST_API_KEY)
    response = auth_client.get("/protected", headers=headers)

    assert response.status_code == expected_status
    if expected_status == HTTP_OK:
        assert response.json() == {"ok": True}
    else:
        assert response.json() == {"detail": "Authentication required"}


def test_require_oauth_session_rejects_missing_token(
    auth_client: TestClient,
) -> None:
    """Protected routes reject requests without an OAuth session or API key."""
    response = auth_client.get("/protected")

    assert response.status_code == HTTP_UNAUTHORIZED
    assert response.json()["detail"] == "Authentication required"
