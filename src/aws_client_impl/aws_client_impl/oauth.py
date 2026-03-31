"""All the logic for OAuth2.0."""

import os
import secrets

import requests


def build_github_auth_url() -> tuple[str, str]:
    """Build the GitHub authorization URL and return it with the state token.

    Returns:
        A tuple of (authorization_url, state) where state must be stored
        and verified in the callback to prevent CSRF attacks.

    """
    auth_uri = os.environ["GITHUB_AUTH_URI"]
    client_id = os.environ["GITHUB_CLIENT_ID"]
    redirect_uri = os.environ["GITHUB_LOCAL_REDIRECT_URI"]
    state = secrets.token_urlsafe(32)

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "",
        "state": state,
    }

    req = requests.Request("GET", auth_uri, params=params)
    prepared = req.prepare()
    if prepared.url is None:
        msg = "Failed to build authorization URL"
        raise ValueError(msg)
    return prepared.url, state


def validate_state(received_state: str, expected_state: str) -> bool:
    """Compare state tokens in constant time to prevent CSRF attacks."""
    if not received_state or not expected_state:
        return False
    return secrets.compare_digest(received_state, expected_state)


def exchange_code_for_token(code: str) -> str:
    """Exchange the authorization code for a GitHub access token.

    Args:
        code: The authorization code received from GitHub in the callback.

    Returns:
        The access token string.

    Raises:
        ValueError: If the token exchange fails or token is missing.

    """
    token_uri = os.environ["GITHUB_TOKEN_URI"]
    client_id = os.environ["GITHUB_CLIENT_ID"]
    client_secret = os.environ["GITHUB_CLIENT_SECRET"]
    redirect_uri = os.environ["GITHUB_LOCAL_REDIRECT_URI"]

    response = requests.post(
        token_uri,
        headers={"Accept": "application/json"},
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )

    data = response.json()

    if "access_token" not in data:
        msg = f"Token exchange failed: {data.get('error_description', data)}"
        raise ValueError(msg)

    return str(data["access_token"])
