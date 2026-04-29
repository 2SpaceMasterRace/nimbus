"""Tests for Slack OAuth helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from nimbus_slack.oauth import (
    SlackOAuthConfig,
    SlackOAuthError,
    create_oauth_state,
    exchange_code_for_installation,
    verify_oauth_state,
)

from nimbus_slack import oauth

pytestmark = pytest.mark.unit


class _FakeResponse:
    """Minimal httpx response double."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        """Pretend the response was HTTP-successful."""

    def json(self) -> dict[str, object]:
        """Return the configured JSON payload."""
        return self._payload


def _config() -> SlackOAuthConfig:
    """Build deterministic OAuth config."""
    return SlackOAuthConfig(
        client_id="client-id",
        client_secret="client-secret",  # noqa: S106
        public_base_url="https://nimbus.example",
        state_secret="state-secret",  # noqa: S106
    )


def test_oauth_state_rejects_tampering_and_expiration() -> None:
    """OAuth state should be signed and time-bounded."""
    now = datetime(2026, 5, 9, tzinfo=UTC)
    state = create_oauth_state("state-secret", now=now)

    assert verify_oauth_state(state, "state-secret", now=now)
    assert not verify_oauth_state(
        f"{state}tampered",
        "state-secret",
        now=now,
    )
    assert not verify_oauth_state(
        state,
        "state-secret",
        now=now + timedelta(minutes=11),
    )


def test_exchange_code_for_installation_parses_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack OAuth success responses should become installation records."""
    captured: dict[str, Any] = {}

    def _post(*args: object, **kwargs: object) -> _FakeResponse:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeResponse(
            {
                "ok": True,
                "access_token": "xoxb-team-token",
                "scope": "chat:write,files:read",
                "bot_user_id": "Ubot",
                "team": {"id": "T123", "name": "Nimbus Lab"},
                "enterprise": {"id": "E123"},
                "authed_user": {"id": "Uadmin"},
            }
        )

    monkeypatch.setattr(oauth.httpx, "post", _post)

    installation = exchange_code_for_installation(config=_config(), code="code-123")

    assert installation.team_id == "T123"
    assert installation.bot_token == "xoxb-team-token"
    assert installation.scopes == ("chat:write", "files:read")
    assert captured["args"] == (oauth.SLACK_ACCESS_URL,)
    assert captured["kwargs"]["data"]["code"] == "code-123"


def test_exchange_code_maps_transport_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slack transport errors should become domain OAuth errors."""

    def _post(*_: object, **__: object) -> _FakeResponse:
        msg = "timeout"
        raise httpx.ConnectTimeout(msg)

    monkeypatch.setattr(oauth.httpx, "post", _post)

    with pytest.raises(SlackOAuthError):
        exchange_code_for_installation(config=_config(), code="code-123")
