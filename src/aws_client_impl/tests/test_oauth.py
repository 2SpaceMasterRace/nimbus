"""Unit Tests for OAuth.py."""

from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import pytest
import requests
from aws_client_impl.oauth import (
    OAuthProviderError,
    OAuthTransportError,
    build_github_auth_url,
    exchange_code_for_token,
    validate_state,
)

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.unit


@pytest.fixture
def github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required GitHub OAuth environment variables."""
    monkeypatch.setenv("GITHUB_AUTH_URI", "https://github.com/login/oauth/authorize")
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GITHUB_LOCAL_REDIRECT_URI", "http://localhost:8000/callback")


def test_build_github_auth_url_returns_url_and_state(github_env: None) -> None:  # noqa: ARG001
    """build_github_auth_url returns a non-empty URL and a non-empty state token."""
    url, state = build_github_auth_url()

    assert url
    assert state


def test_build_github_auth_url_state_is_unique(github_env: None) -> None:  # noqa: ARG001
    """Each call to build_github_auth_url produces a different state token."""
    _, state1 = build_github_auth_url()
    _, state2 = build_github_auth_url()

    assert state1 != state2


def test_build_github_auth_url_contains_required_params(github_env: None) -> None:  # noqa: ARG001
    """The authorization URL contains client_id, redirect_uri, and state params."""
    url, state = build_github_auth_url()
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["client_id"] == ["test_client_id"]
    assert params["redirect_uri"] == ["http://localhost:8000/callback"]
    assert params["state"] == [state]


def test_build_github_auth_url_uses_auth_uri_as_base(github_env: None) -> None:  # noqa: ARG001
    """The authorization URL starts with GITHUB_AUTH_URI."""
    url, _ = build_github_auth_url()

    assert url.startswith("https://github.com/login/oauth/authorize")


def test_build_github_auth_url_missing_env_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_github_auth_url raises KeyError when a required env var is missing."""
    monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
    monkeypatch.setenv("GITHUB_AUTH_URI", "https://github.com/login/oauth/authorize")
    monkeypatch.setenv("GITHUB_LOCAL_REDIRECT_URI", "http://localhost:8000/callback")

    with pytest.raises(KeyError):
        build_github_auth_url()


def test_validate_state_returns_true_for_matching_states() -> None:
    """validate_state returns True when received and expected states match."""
    assert validate_state("abc123", "abc123") is True


def test_validate_state_returns_false_for_mismatched_states() -> None:
    """validate_state returns False when received and expected states differ."""
    assert validate_state("abc123", "xyz789") is False


def test_validate_state_returns_false_for_empty_vs_nonempty() -> None:
    """validate_state returns False when one state is empty."""
    assert validate_state("", "abc123") is False


def test_validate_state_returns_false_for_both_empty() -> None:
    """validate_state returns False when both states are empty strings."""
    assert validate_state("", "") is False


def test_validate_state_is_case_sensitive() -> None:
    """validate_state treats uppercase and lowercase as different."""
    assert validate_state("ABC123", "abc123") is False


def test_validate_state_uses_real_generated_state(github_env: None) -> None:  # noqa: ARG001
    """validate_state correctly validates a state produced by build_github_auth_url."""
    _, state = build_github_auth_url()
    assert validate_state(state, state) is True


@pytest.fixture
def token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required GitHub token exchange environment variables."""
    monkeypatch.setenv(
        "GITHUB_TOKEN_URI", "https://github.com/login/oauth/access_token"
    )
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setenv("GITHUB_LOCAL_REDIRECT_URI", "http://localhost:8000/callback")


def test_exchange_code_for_token_returns_access_token(
    mocker: "MockerFixture",
    token_env: None,  # noqa: ARG001
) -> None:
    """exchange_code_for_token returns the access token string on success."""
    mock_response = mocker.Mock()
    mock_response.json.return_value = {"access_token": "gho_abc123"}
    mocker.patch("aws_client_impl.oauth.requests.post", return_value=mock_response)

    token = exchange_code_for_token("auth_code_xyz")

    assert token == "gho_abc123"


def test_exchange_code_for_token_posts_to_token_uri(
    mocker: "MockerFixture",
    token_env: None,  # noqa: ARG001
) -> None:
    """exchange_code_for_token POSTs to GITHUB_TOKEN_URI with correct payload."""
    mock_response = mocker.Mock()
    mock_response.json.return_value = {"access_token": "gho_abc123"}
    mock_post = mocker.patch(
        "aws_client_impl.oauth.requests.post", return_value=mock_response
    )

    exchange_code_for_token("auth_code_xyz")

    mock_post.assert_called_once_with(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "code": "auth_code_xyz",
            "redirect_uri": "http://localhost:8000/callback",
        },
        timeout=10,
    )


def test_exchange_code_for_token_raises_value_error_when_token_missing(
    mocker: "MockerFixture",
    token_env: None,  # noqa: ARG001
) -> None:
    """exchange_code_for_token raises ValueError with desc when token missing."""
    mock_response = mocker.Mock()
    mock_response.json.return_value = {
        "error": "bad_verification_code",
        "error_description": "The code passed is incorrect or expired.",
    }
    mocker.patch("aws_client_impl.oauth.requests.post", return_value=mock_response)

    with pytest.raises(ValueError, match=r"The code passed is incorrect or expired\."):
        exchange_code_for_token("bad_code")


def test_exchange_code_for_token_missing_env_raises(
    mocker: "MockerFixture",  # noqa: ARG001
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """exchange_code_for_token raises KeyError when a required env var is missing."""
    monkeypatch.delenv("GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.setenv(
        "GITHUB_TOKEN_URI", "https://github.com/login/oauth/access_token"
    )
    monkeypatch.setenv("GITHUB_CLIENT_ID", "test_client_id")
    monkeypatch.setenv("GITHUB_LOCAL_REDIRECT_URI", "http://localhost:8000/callback")

    with pytest.raises(KeyError):
        exchange_code_for_token("some_code")


def test_exchange_code_for_token_raises_transport_error_on_timeout(
    mocker: "MockerFixture",
    token_env: None,  # noqa: ARG001
) -> None:
    """Timeouts are surfaced as OAuthTransportError."""
    mocker.patch(
        "aws_client_impl.oauth.requests.post",
        side_effect=requests.Timeout("timed out"),
    )

    with pytest.raises(OAuthTransportError, match="timed out"):
        exchange_code_for_token("auth_code_xyz")


def test_exchange_code_for_token_raises_transport_error_on_connection_failure(
    mocker: "MockerFixture",
    token_env: None,  # noqa: ARG001
) -> None:
    """Connection failures are surfaced as OAuthTransportError."""
    mocker.patch(
        "aws_client_impl.oauth.requests.post",
        side_effect=requests.ConnectionError("boom"),
    )

    with pytest.raises(OAuthTransportError, match="could not reach"):
        exchange_code_for_token("auth_code_xyz")


def test_exchange_code_for_token_raises_provider_error_on_http_failure(
    mocker: "MockerFixture",
    token_env: None,  # noqa: ARG001
) -> None:
    """HTTP failures after the request reaches GitHub are provider errors."""
    mock_response = mocker.Mock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("bad gateway")
    mocker.patch("aws_client_impl.oauth.requests.post", return_value=mock_response)

    with pytest.raises(OAuthProviderError, match="transport layer"):
        exchange_code_for_token("auth_code_xyz")


def test_exchange_code_for_token_raises_provider_error_on_invalid_json(
    mocker: "MockerFixture",
    token_env: None,  # noqa: ARG001
) -> None:
    """Invalid JSON from GitHub is surfaced as OAuthProviderError."""
    mock_response = mocker.Mock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.side_effect = ValueError("not json")
    mocker.patch("aws_client_impl.oauth.requests.post", return_value=mock_response)

    with pytest.raises(OAuthProviderError, match="invalid JSON"):
        exchange_code_for_token("auth_code_xyz")
