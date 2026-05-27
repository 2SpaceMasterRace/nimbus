"""HTTP tests for the Nimbus Slack FastAPI app."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from nimbus_slack.main import app
from nimbus_slack.oauth import SlackOAuthInstallation, create_oauth_state

pytestmark = pytest.mark.unit

_SLACK_SIGNING_SECRET = "slack-secret"
_BAD_SLACK_SIGNING_SECRET = "wrong-secret"


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


def test_ready_checks_durable_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Readiness should verify the configured Slack store."""
    called = False

    def check_ready() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("nimbus_slack.main.check_slack_store_ready", check_ready)

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "service": "nimbus-slack"}
    assert called is True


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
    calls: list[dict[str, object]] = []
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


def test_setup_page_emits_locked_down_csp_header(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Setup pages should advertise a CSP that pins inline script and style."""
    _set_control_plane_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "nimbus_slack.main.exchange_code_for_installation",
        lambda **_: SlackOAuthInstallation(
            team_id="T123",
            enterprise_id=None,
            team_name="Nimbus Lab",
            bot_user_id="Ubot",
            bot_token="xoxb-csp-token",  # noqa: S106
            scopes=("chat:write",),
            installed_by="Uadmin",
        ),
    )
    state = create_oauth_state("state-secret")
    callback = TestClient(app).get(
        "/slack/oauth/callback",
        params={"code": "code-123", "state": state},
    )
    setup_path = _extract_setup_path(callback.text)
    form = TestClient(app).get(setup_path)

    csp = form.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert "'sha256-" in csp  # both script and style are hash-pinned
    assert form.headers["cache-control"] == "no-store"
    assert form.headers["referrer-policy"] == "no-referrer"
    assert form.headers["x-content-type-options"] == "nosniff"


def test_setup_submit_rejects_oversized_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Setup POST should reject oversized bodies before parsing secrets."""
    _set_control_plane_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NIMBUS_SLACK_SETUP_MAX_BODY_BYTES", "8")
    monkeypatch.setattr(
        "nimbus_slack.main.exchange_code_for_installation",
        lambda **_: SlackOAuthInstallation(
            team_id="T123",
            enterprise_id=None,
            team_name="Nimbus Lab",
            bot_user_id="Ubot",
            bot_token="xoxb-size-token",  # noqa: S106
            scopes=("chat:write",),
            installed_by="Uadmin",
        ),
    )
    state = create_oauth_state("state-secret")
    callback = TestClient(app).get(
        "/slack/oauth/callback",
        params={"code": "code-123", "state": state},
    )
    setup_path = _extract_setup_path(callback.text)

    response = TestClient(app).post(
        setup_path,
        content=b'{"too":"large"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
    assert "Setup payload too large" in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_setup_submit_validation_error_renders_form_html(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Bad setup input should stay in the onboarding UI instead of JSON."""
    _set_control_plane_env(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "nimbus_slack.main.exchange_code_for_installation",
        lambda **_: SlackOAuthInstallation(
            team_id="T123",
            enterprise_id=None,
            team_name="Nimbus Lab",
            bot_user_id="Ubot",
            bot_token="xoxb-validation-token",  # noqa: S106
            scopes=("chat:write",),
            installed_by="Uadmin",
        ),
    )
    state = create_oauth_state("state-secret")
    callback = TestClient(app).get(
        "/slack/oauth/callback",
        params={"code": "code-123", "state": state},
    )
    setup_path = _extract_setup_path(callback.text)

    response = TestClient(app).post(
        setup_path,
        json={
            "openrouter_api_key": "",
            "aws_access_key_id": "AKIA_TEST",
            "aws_secret_access_key": "aws-secret",
            "aws_region": "us-east-1",
            "s3_bucket": "nimbus-test-bucket",
            "s3_prefix": "team",
        },
    )

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "Configuration was not saved" in response.text
    assert "openrouter_api_key must be a non-empty string." in response.text
    assert "aws-secret" not in response.text


def test_setup_endpoints_rate_limit_per_ip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repeated setup-token attempts from one IP should hit a 429."""
    _set_control_plane_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NIMBUS_SLACK_SETUP_RATE_LIMIT_RPM", "1")
    monkeypatch.setenv("NIMBUS_SLACK_SETUP_RATE_LIMIT_BURST", "2")
    # Rebuild limiter with the override values.
    from nimbus_slack import main as main_module

    # Refresh the module-level limiter so the env override takes effect for
    # this test only.
    main_module._setup_rate_limiter = main_module._build_setup_rate_limiter()

    client = TestClient(app)
    statuses = [client.get("/slack/setup/missing").status_code for _ in range(5)]

    assert statuses[:2] == [404, 404]
    assert 429 in statuses[2:]


# ── Feature 7: /slack/interactive endpoint ──────────────────────────────────


def _interactive_form_body(payload: dict[str, object]) -> bytes:
    """Encode a Slack interactive payload as Slack would send it.

    Slack POSTs ``application/x-www-form-urlencoded`` with one key, ``payload``,
    whose value is the JSON-stringified payload.
    """
    from urllib.parse import urlencode

    return urlencode({"payload": json.dumps(payload)}).encode("utf-8")


def _interactive_headers(body: bytes, *, secret: str | None = None) -> dict[str, str]:
    used_secret = secret or _SLACK_SIGNING_SECRET
    timestamp = str(int(time.time()))
    canonical = b"v0:" + timestamp.encode("utf-8") + b":" + body
    signature = hmac.new(
        used_secret.encode("utf-8"),
        canonical,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": f"v0={signature}",
    }


def _block_actions_payload(
    *,
    action_id: str,
    team_id: str = "T123",
    user_id: str = "U999",
    channel_id: str = "C42",
    message_ts: str = "1715000000.000100",
    action_ts: str = "1715000001.000200",
) -> dict[str, object]:
    return {
        "type": "block_actions",
        "team": {"id": team_id},
        "user": {"id": user_id},
        "channel": {"id": channel_id},
        "container": {
            "type": "message",
            "message_ts": message_ts,
        },
        "actions": [
            {
                "action_id": action_id,
                "block_id": "b1",
                "action_ts": action_ts,
                "value": "x",
                "type": "button",
            }
        ],
    }


def test_interactive_rejects_missing_signature_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    body = _interactive_form_body(_block_actions_payload(action_id="cmd:dedupe_report"))
    response = TestClient(app).post(
        "/slack/interactive",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 401


def test_interactive_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    body = _interactive_form_body(_block_actions_payload(action_id="cmd:dedupe_report"))
    bad_headers = _interactive_headers(body, secret=_BAD_SLACK_SIGNING_SECRET)
    response = TestClient(app).post(
        "/slack/interactive",
        content=body,
        headers=bad_headers,
    )
    assert response.status_code == 401


def test_interactive_button_dispatches_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `cmd:dedupe_report` button click should re-route through the same
    command dispatcher used for text mentions.
    """
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")

    dispatched: list[tuple[str, dict[str, object]]] = []

    def fake_handle(*, team_id: str, payload: dict[str, object], **_: object) -> None:
        dispatched.append((team_id, payload))

    monkeypatch.setattr("nimbus_slack.main.handle_slack_interaction", fake_handle)

    body = _interactive_form_body(_block_actions_payload(action_id="cmd:dedupe_report"))
    response = TestClient(app).post(
        "/slack/interactive",
        content=body,
        headers=_interactive_headers(body),
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert dispatched, "interactive dispatcher was not called"
    team_id, payload = dispatched[0]
    assert team_id == "T123"
    assert payload["actions"][0]["action_id"] == "cmd:dedupe_report"  # type: ignore[index]


def test_interactive_dedupes_duplicate_clicks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same action_ts on the same user should ack as duplicate the second time."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    monkeypatch.setattr("nimbus_slack.main.handle_slack_interaction", lambda **_: None)

    body = _interactive_form_body(_block_actions_payload(action_id="cmd:dedupe_report"))
    client = TestClient(app)
    first = client.post(
        "/slack/interactive", content=body, headers=_interactive_headers(body)
    )
    # Re-use the same body / signed headers to simulate Slack retrying the request.
    second = client.post(
        "/slack/interactive", content=body, headers=_interactive_headers(body)
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json().get("duplicate") is True


def test_interactive_link_button_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """`open_setup` and friends shouldn't run any command — just ack 200."""
    from nimbus_slack import flow

    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    payload = _block_actions_payload(action_id="open_setup")
    result = flow.handle_slack_interaction(team_id="T123", payload=payload)
    assert result is None


def test_interactive_approval_logged_no_action(monkeypatch: pytest.MonkeyPatch) -> None:
    """Until full runtime wiring lands, approval clicks log and ack 200."""
    from nimbus_slack import flow

    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    payload = _block_actions_payload(action_id="approve:act-abc")
    result = flow.handle_slack_interaction(team_id="T123", payload=payload)
    assert result is None


def test_interactive_unknown_action_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown action_ids log and return None instead of crashing."""
    from nimbus_slack import flow

    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    payload = _block_actions_payload(action_id="not_a_real_action")
    result = flow.handle_slack_interaction(team_id="T123", payload=payload)
    assert result is None


def test_interactive_ignores_non_block_actions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    body = _interactive_form_body({"type": "view_submission"})
    response = TestClient(app).post(
        "/slack/interactive",
        content=body,
        headers=_interactive_headers(body),
    )
    assert response.status_code == 200
    assert response.json().get("ignored") == "view_submission"


# ── P2: app_home_opened event routing ────────────────────────────────────────


def test_app_home_opened_dispatches_to_home_publisher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid app_home_opened event should trigger handle_app_home_opened."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    home_calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        "nimbus_slack.main.handle_app_home_opened",
        lambda **kwargs: home_calls.append(kwargs),
    )
    payload = {
        "type": "event_callback",
        "team_id": "T_HOME",
        "event_id": "Ev-home-1",
        "event": {
            "type": "app_home_opened",
            "tab": "home",
            "user": "U_HOME_USER",
            "event_ts": "1710000001.000",
        },
    }
    body = _body(payload)
    response = TestClient(app).post(
        "/slack/events", content=body, headers=_headers(body)
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(home_calls) == 1
    assert home_calls[0]["team_id"] == "T_HOME"
    assert home_calls[0]["user_id"] == "U_HOME_USER"


def test_app_home_opened_is_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Duplicate app_home_opened events for the same user are deduplicated."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    home_calls: list[dict] = []
    monkeypatch.setattr(
        "nimbus_slack.main.handle_app_home_opened",
        lambda **kwargs: home_calls.append(kwargs),
    )
    payload = {
        "type": "event_callback",
        "team_id": "T_DUP",
        "event_id": "Ev-home-dup-1",
        "event": {
            "type": "app_home_opened",
            "tab": "home",
            "user": "U_DUP",
            "event_ts": "1710000002.000",
        },
    }
    body = _body(payload)
    client = TestClient(app)
    client.post("/slack/events", content=body, headers=_headers(body))
    client.post("/slack/events", content=body, headers=_headers(body))

    # The dedupe key includes team_id, user_id, and event_id — second is a dup.
    assert len(home_calls) == 1


def test_app_home_opened_messages_tab_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only tab=home events should trigger home publishing; messages tab is ignored."""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "slack-secret")
    home_calls: list[dict] = []
    monkeypatch.setattr(
        "nimbus_slack.main.handle_app_home_opened",
        lambda **kwargs: home_calls.append(kwargs),
    )
    payload = {
        "type": "event_callback",
        "team_id": "T_MSGS",
        "event_id": "Ev-home-msg-1",
        "event": {
            "type": "app_home_opened",
            "tab": "messages",  # not the Home tab
            "user": "U_MSG_USER",
            "event_ts": "1710000003.000",
        },
    }
    body = _body(payload)
    response = TestClient(app).post(
        "/slack/events", content=body, headers=_headers(body)
    )

    assert response.status_code == 200
    assert len(home_calls) == 0
