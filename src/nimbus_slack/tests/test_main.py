"""HTTP tests for the Nimbus Slack FastAPI app."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from nimbus_slack.main import app
from nimbus_slack.oauth import SlackOAuthInstallation, create_oauth_state

pytestmark = pytest.mark.unit


def _body(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _headers(body: bytes, *, secret: str | None = None) -> dict[str, str]:
    used_secret = secret or "slack-secret"
    timestamp = str(int(time.time()))
    canonical = b"v0:" + timestamp.encode("utf-8") + b":" + body
    signature = hmac.new(
        used_secret.encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"v0={signature}",
    }


def test_url_verification_returns_challenge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slack URL verification should never call Nimbus."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    payload = {"type": "url_verification", "challenge": "challenge-123"}
    body = _body(payload)

    response = TestClient(app).post(
        "/slack/events",
        content=body,
        headers=_headers(body),
    )

    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-123"}


def test_install_redirects_to_slack_oauth(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Install endpoint should generate a Slack OAuth redirect."""
    _set_control_plane_env(monkeypatch, tmp_path)

    response = TestClient(app).get("/slack/install", follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("https://slack.com/oauth/v2/authorize?")
    assert "client_id=client-id" in location
    assert "chat%3Awrite" in location


def test_oauth_callback_stores_installation_and_setup_accepts_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """OAuth plus setup should persist encrypted workspace configuration."""
    _set_control_plane_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "nimbus_slack.main.exchange_code_for_installation",
        lambda **_: SlackOAuthInstallation(
            team_id="T123",
            enterprise_id=None,
            team_name="Nimbus Lab",
            bot_user_id="Ubot",
            bot_token="xoxb-oauth-token",  # noqa: S106
            scopes=("chat:write", "files:read"),
            installed_by="Uadmin",
        ),
    )
    state = create_oauth_state("state-secret")
    callback = TestClient(app).get(
        "/slack/oauth/callback",
        params={"code": "code-123", "state": state},
    )

    assert callback.status_code == 200
    setup_path = _extract_setup_path(callback.text)

    form = TestClient(app).get(setup_path)
    submit = TestClient(app).post(
        setup_path,
        json={
            "openrouter_api_key": "sk-or-secret",
            "aws_access_key_id": "AKIA_TEST_SECRET",
            "aws_secret_access_key": "aws-secret",
            "aws_region": "us-east-1",
            "s3_bucket": "nimbus-test-bucket",
            "s3_prefix": "slack/archive",
        },
    )
    repeat = TestClient(app).post(
        setup_path,
        json={
            "openrouter_api_key": "sk-or-secret",
            "aws_access_key_id": "AKIA_TEST_SECRET",
            "aws_secret_access_key": "aws-secret",
            "aws_region": "us-east-1",
            "s3_bucket": "nimbus-test-bucket",
            "s3_prefix": "slack/archive",
        },
    )

    assert form.status_code == 200
    assert submit.status_code == 200
    assert repeat.status_code == 404
    db_bytes = (tmp_path / "nimbus_slack.sqlite3").read_bytes()
    assert b"xoxb-oauth-token" not in db_bytes
    assert b"sk-or-secret" not in db_bytes


def test_event_callback_queues_one_background_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid Slack event should be acknowledged and processed once."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "nimbus_slack.main.handle_slack_event",
        lambda **kwargs: calls.append(kwargs),
    )
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev-main-1",
        "event": {
            "type": "app_mention",
            "channel": "C123",
            "user": "U123",
            "text": "<@BOT> hello",
            "ts": "1710000000.123",
        },
    }
    body = _body(payload)
    client = TestClient(app)

    first = client.post("/slack/events", content=body, headers=_headers(body))
    second = client.post("/slack/events", content=body, headers=_headers(body))

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == {"ok": True, "duplicate": True}
    assert len(calls) == 1
    assert calls[0]["event_id"] == "Ev-main-1"


def test_bot_event_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bot events should be acknowledged without calling Nimbus."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event_id": "Ev-bot-1",
        "event": {
            "type": "message",
            "subtype": "bot_message",
            "bot_id": "B123",
            "channel": "C123",
            "text": "hello",
            "ts": "1710000000.123",
        },
    }
    body = _body(payload)

    response = TestClient(app).post(
        "/slack/events",
        content=body,
        headers=_headers(body),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "ignored": True}


def test_invalid_signature_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Slack signature verification is the inbound trust boundary."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    body = _body({"type": "url_verification", "challenge": "challenge-123"})
    headers = _headers(body)
    headers["X-Slack-Signature"] = "v0=bad"

    response = TestClient(app).post("/slack/events", content=body, headers=headers)

    assert response.status_code == 401


def _set_control_plane_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Configure OAuth and encrypted store environment for app tests."""
    monkeypatch.setenv("SLACK_CLIENT_ID", "client-id")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("NIMBUS_SLACK_PUBLIC_BASE_URL", "https://nimbus.example")
    monkeypatch.setenv("NIMBUS_SLACK_STATE_SECRET", "state-secret")
    monkeypatch.setenv("NIMBUS_SLACK_STATE_DIR", str(tmp_path))
    monkeypatch.setenv(
        "NIMBUS_SLACK_SECRET_KEY",
        Fernet.generate_key().decode("utf-8"),
    )


def _extract_setup_path(html: str) -> str:
    """Extract the setup path from the small callback HTML page."""
    marker = 'href="'
    start = html.index(marker) + len(marker)
    end = html.index('"', start)
    return html[start:end]
