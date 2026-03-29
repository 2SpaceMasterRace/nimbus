"""Integration tests for OAuth functions.

This module tests oauth.py against a real HTTP server running locally,
verifying the full request/response cycle without hitting GitHub's API.
"""

from typing import TYPE_CHECKING

import pytest
import requests
from aws_client_impl.oauth import build_github_auth_url, exchange_code_for_token
from werkzeug.wrappers import Request as WerkzeugRequest
from werkzeug.wrappers import Response

if TYPE_CHECKING:
    from pytest_httpserver import HTTPServer

pytestmark = pytest.mark.integration

HTTP_OK = 200


@pytest.fixture
def oauth_env(monkeypatch: pytest.MonkeyPatch, httpserver: "HTTPServer") -> None:
    """Wire all GitHub OAuth env vars to the local test HTTP server."""
    base = httpserver.url_for("")
    monkeypatch.setenv("GITHUB_AUTH_URI", f"{base}/login/oauth/authorize")
    monkeypatch.setenv("GITHUB_TOKEN_URI", f"{base}/login/oauth/access_token")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GITHUB_LOCAL_REDIRECT_URI", "http://localhost:8000/callback")


def test_build_github_auth_url_produces_reachable_url(
    oauth_env: None,  # noqa: ARG001
    httpserver: "HTTPServer",
) -> None:
    """The authorization URL built by build_github_auth_url is a real, reachable URL."""
    httpserver.expect_request("/login/oauth/authorize").respond_with_data(
        "ok", status=HTTP_OK
    )

    url, _ = build_github_auth_url()
    response = requests.get(url, timeout=5)

    assert response.status_code == HTTP_OK


def test_exchange_code_for_token_sends_post_to_token_endpoint(
    oauth_env: None,  # noqa: ARG001
    httpserver: "HTTPServer",
) -> None:
    """exchange_code_for_token sends a POST request to the token URI."""
    httpserver.expect_request(
        "/login/oauth/access_token", method="POST"
    ).respond_with_json({"access_token": "gho_integration_test"})

    token = exchange_code_for_token("real_code")

    assert token == "gho_integration_test"


def test_exchange_code_for_token_sends_correct_form_body(
    oauth_env: None,  # noqa: ARG001
    httpserver: "HTTPServer",
) -> None:
    """exchange_code_for_token sends the correct form fields in the POST body."""
    captured: list[WerkzeugRequest] = []

    def handler(request: WerkzeugRequest) -> Response:
        captured.append(request)
        return Response('{"access_token": "gho_abc"}', content_type="application/json")

    httpserver.expect_request(
        "/login/oauth/access_token", method="POST"
    ).respond_with_handler(handler)

    exchange_code_for_token("test_auth_code")

    assert len(captured) == 1
    req = captured[0]
    assert req.form["client_id"] == "test_client_id"
    assert req.form["client_secret"] == "test_client_secret"
    assert req.form["code"] == "test_auth_code"
    assert req.form["redirect_uri"] == "http://localhost:8000/callback"


def test_exchange_code_for_token_sends_accept_json_header(
    oauth_env: None,  # noqa: ARG001
    httpserver: "HTTPServer",
) -> None:
    """exchange_code_for_token sends Accept: application/json header."""
    captured: list[WerkzeugRequest] = []

    def handler(request: WerkzeugRequest) -> Response:
        captured.append(request)
        return Response('{"access_token": "gho_abc"}', content_type="application/json")

    httpserver.expect_request(
        "/login/oauth/access_token", method="POST"
    ).respond_with_handler(handler)

    exchange_code_for_token("test_auth_code")

    assert captured[0].headers.get("Accept") == "application/json"


def test_exchange_code_for_token_raises_on_error_response(
    oauth_env: None,  # noqa: ARG001
    httpserver: "HTTPServer",
) -> None:
    """exchange_code_for_token raises ValueError when the server returns an error."""
    httpserver.expect_request(
        "/login/oauth/access_token", method="POST"
    ).respond_with_json(
        {
            "error": "bad_verification_code",
            "error_description": "The code passed is incorrect or expired.",
        }
    )

    with pytest.raises(ValueError, match=r"The code passed is incorrect or expired\."):
        exchange_code_for_token("expired_code")


def test_exchange_code_for_token_raises_on_empty_error_response(
    oauth_env: None,  # noqa: ARG001
    httpserver: "HTTPServer",
) -> None:
    """exchange_code_for_token raises ValueError when error_description is absent."""
    httpserver.expect_request(
        "/login/oauth/access_token", method="POST"
    ).respond_with_json({"error": "unknown_error"})

    with pytest.raises(ValueError, match="Token exchange failed"):
        exchange_code_for_token("some_code")
