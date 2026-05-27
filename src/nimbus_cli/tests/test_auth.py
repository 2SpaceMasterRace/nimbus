"""Tests for Nimbus CLI auth helpers."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from nimbus_cli.auth import encode_json_body, remote_auth_headers, sign_request
from nimbus_cli.config import NimbusProfile

pytestmark = pytest.mark.unit


class _FakeSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get(self, *, profile: str, kind: str) -> str | None:
        return self._values.get(kind)


def test_sign_request_matches_server_hmac_contract() -> None:
    """CLI HMAC headers should match the ai_server documented canonical shape."""
    body = encode_json_body({"text": "hello"})
    headers = sign_request(
        body=body,
        secret="secret",  # noqa: S106 - deterministic test signing secret.
        timestamp=1700000000,
        nonce="nonce-1",
    )
    digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n/ai/chat/turn\n1700000000\nnonce-1\n{digest}"
    expected = hmac.new(
        b"secret",
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert headers["X-Nimbus-Signature"] == expected
    assert headers["X-Nimbus-Timestamp"] == "1700000000"
    assert headers["X-Nimbus-Nonce"] == "nonce-1"


def test_remote_auth_headers_bearer_profiles_are_rejected() -> None:
    """Bearer profiles fail before sending a request the server will reject."""
    profile = NimbusProfile(
        name="prod",
        mode="remote",
        remote_base_url="https://example.com",
        remote_auth="bearer",
    )
    secrets = _FakeSecrets({"remote_bearer_token": "tok-abc"})
    with pytest.raises(ValueError, match="not accepted"):
        remote_auth_headers(profile=profile, secrets=secrets, body=b"{}")


def test_remote_auth_headers_hmac_raises_when_secret_missing() -> None:
    """HMAC profiles fail closed when the signing secret is missing."""
    profile = NimbusProfile(
        name="prod",
        mode="remote",
        remote_base_url="https://example.com",
        remote_auth="hmac",
    )
    secrets = _FakeSecrets({})
    with pytest.raises(ValueError, match="signing secret"):
        remote_auth_headers(profile=profile, secrets=secrets, body=b"{}")


def test_remote_auth_headers_raises_when_no_auth_configured() -> None:
    """Non-remote profiles cannot produce remote auth headers."""
    profile = NimbusProfile(name="local", mode="local")
    secrets = _FakeSecrets({})
    with pytest.raises(ValueError, match="does not declare remote auth"):
        remote_auth_headers(profile=profile, secrets=secrets, body=b"{}")
