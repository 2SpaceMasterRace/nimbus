"""Slack OAuth helpers for multi-workspace Nimbus installations."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from urllib.parse import urlencode

import httpx

SLACK_CLIENT_ID = "SLACK_CLIENT_ID"
SLACK_CLIENT_SECRET = "SLACK_CLIENT_SECRET"  # noqa: S105
NIMBUS_SLACK_PUBLIC_BASE_URL = "NIMBUS_SLACK_PUBLIC_BASE_URL"
NIMBUS_SLACK_STATE_SECRET = "NIMBUS_SLACK_STATE_SECRET"  # noqa: S105
SLACK_AUTHORIZE_URL: Final = "https://slack.com/oauth/v2/authorize"
SLACK_ACCESS_URL: Final = "https://slack.com/api/oauth.v2.access"
OAUTH_STATE_MAX_AGE_SECONDS: Final = 10 * 60
OAUTH_TIMEOUT_SECONDS: Final = 10.0
DEFAULT_BOT_SCOPES: Final = (
    "app_mentions:read",
    "channels:history",
    "channels:read",
    "chat:write",
    "files:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "mpim:read",
    "users:read",
)


class SlackOAuthError(RuntimeError):
    """Raised when Slack OAuth cannot complete safely."""


@dataclass(frozen=True, slots=True)
class SlackOAuthConfig:
    """Environment-backed OAuth configuration for a Slack app."""

    client_id: str
    client_secret: str
    public_base_url: str
    state_secret: str
    scopes: tuple[str, ...] = DEFAULT_BOT_SCOPES

    @classmethod
    def from_env(cls) -> SlackOAuthConfig:
        """Load OAuth configuration from environment variables."""
        return cls(
            client_id=_require_env(SLACK_CLIENT_ID),
            client_secret=_require_env(SLACK_CLIENT_SECRET),
            public_base_url=_require_env(NIMBUS_SLACK_PUBLIC_BASE_URL).rstrip("/"),
            state_secret=_require_env(NIMBUS_SLACK_STATE_SECRET),
        )

    @property
    def redirect_uri(self) -> str:
        """Return the public OAuth callback URL registered with Slack."""
        return f"{self.public_base_url}/slack/oauth/callback"


@dataclass(frozen=True, slots=True)
class SlackOAuthInstallation:
    """Validated installation data returned by Slack OAuth."""

    team_id: str
    enterprise_id: str | None
    team_name: str | None
    bot_user_id: str | None
    bot_token: str
    scopes: tuple[str, ...]
    installed_by: str | None


def build_authorize_url(config: SlackOAuthConfig, *, state: str) -> str:
    """Build the Slack OAuth authorization URL."""
    query = urlencode(
        {
            "client_id": config.client_id,
            "scope": ",".join(config.scopes),
            "redirect_uri": config.redirect_uri,
            "state": state,
        }
    )
    return f"{SLACK_AUTHORIZE_URL}?{query}"


def create_oauth_state(
    secret: str,
    *,
    now: datetime | None = None,
) -> str:
    """Create a signed stateless OAuth CSRF token."""
    issued_at = int((now or _utc_now()).timestamp())
    nonce = secrets.token_urlsafe(18)
    payload = f"{issued_at}.{nonce}"
    signature = _sign_state_payload(secret=secret, payload=payload)
    return f"{payload}.{signature}"


def verify_oauth_state(
    state: str,
    secret: str,
    *,
    now: datetime | None = None,
    max_age_seconds: int = OAUTH_STATE_MAX_AGE_SECONDS,
) -> bool:
    """Return whether a Slack OAuth state token is authentic and fresh."""
    if max_age_seconds <= 0:
        msg = "OAuth state max age must be positive."
        raise SlackOAuthError(msg)
    parts = state.split(".")
    if len(parts) != 3:  # noqa: PLR2004  # OAuth state has exactly 3 parts.
        return False
    issued_at_text, nonce, signature = parts
    if not issued_at_text or not nonce or not signature:
        return False
    payload = f"{issued_at_text}.{nonce}"
    expected = _sign_state_payload(secret=secret, payload=payload)
    if not hmac.compare_digest(expected, signature):
        return False
    try:
        issued_at = int(issued_at_text)
    except ValueError:
        return False
    checked_at = int((now or _utc_now()).timestamp())
    age_seconds = checked_at - issued_at
    return 0 <= age_seconds <= max_age_seconds


def exchange_code_for_installation(
    *,
    config: SlackOAuthConfig,
    code: str,
) -> SlackOAuthInstallation:
    """Exchange a Slack OAuth code for installation data."""
    try:
        response = httpx.post(
            SLACK_ACCESS_URL,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "code": code,
                "redirect_uri": config.redirect_uri,
            },
            timeout=OAUTH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        msg = "Slack OAuth token exchange failed at the transport layer."
        raise SlackOAuthError(msg) from exc
    payload = response.json()
    if not isinstance(payload, dict):
        msg = "Slack OAuth response must be a JSON object."
        raise SlackOAuthError(msg)
    return _parse_installation_payload(payload)


def _parse_installation_payload(
    payload: dict[object, object],
) -> SlackOAuthInstallation:
    """Validate Slack OAuth response shape."""
    if payload.get("ok") is not True:
        error = payload.get("error")
        detail = error if isinstance(error, str) else "unknown_error"
        msg = f"Slack OAuth failed: {detail}"
        raise SlackOAuthError(msg)

    team = payload.get("team")
    if not isinstance(team, dict):
        msg = "Slack OAuth response is missing team metadata."
        raise SlackOAuthError(msg)
    team_id = _require_mapping_str(team, "id")
    team_name = _optional_mapping_str(team, "name")

    enterprise = payload.get("enterprise")
    enterprise_id = None
    if isinstance(enterprise, dict):
        enterprise_id = _optional_mapping_str(enterprise, "id")

    authed_user = payload.get("authed_user")
    installed_by = None
    if isinstance(authed_user, dict):
        installed_by = _optional_mapping_str(authed_user, "id")

    scopes = _parse_scopes(_require_payload_str(payload, "scope"))
    return SlackOAuthInstallation(
        team_id=team_id,
        enterprise_id=enterprise_id,
        team_name=team_name,
        bot_user_id=_optional_payload_str(payload, "bot_user_id"),
        bot_token=_require_payload_str(payload, "access_token"),
        scopes=scopes,
        installed_by=installed_by,
    )


def _parse_scopes(value: str) -> tuple[str, ...]:
    """Parse Slack's comma-delimited scope string."""
    scopes = tuple(scope.strip() for scope in value.split(",") if scope.strip())
    if not scopes:
        msg = "Slack OAuth response did not include any bot scopes."
        raise SlackOAuthError(msg)
    return scopes


def _sign_state_payload(*, secret: str, payload: str) -> str:
    """Sign the stable OAuth state payload."""
    if not secret:
        msg = "OAuth state secret must not be empty."
        raise SlackOAuthError(msg)
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _require_env(name: str) -> str:
    """Return a required non-empty environment variable."""
    value = os.environ.get(name, "").strip()
    if not value:
        msg = f"{name} is not set."
        raise SlackOAuthError(msg)
    return value


def _require_payload_str(payload: dict[object, object], key: str) -> str:
    """Return a required string from a Slack OAuth payload."""
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    msg = f"Slack OAuth response field {key!r} must be a non-empty string."
    raise SlackOAuthError(msg)


def _optional_payload_str(payload: dict[object, object], key: str) -> str | None:
    """Return an optional string from a Slack OAuth payload."""
    value = payload.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"Slack OAuth response field {key!r} must be a string or null."
    raise SlackOAuthError(msg)


def _require_mapping_str(payload: dict[object, object], key: str) -> str:
    """Return a required string from a nested Slack OAuth mapping."""
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    msg = f"Slack OAuth nested field {key!r} must be a non-empty string."
    raise SlackOAuthError(msg)


def _optional_mapping_str(payload: dict[object, object], key: str) -> str | None:
    """Return an optional string from a nested Slack OAuth mapping."""
    value = payload.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"Slack OAuth nested field {key!r} must be a string or null."
    raise SlackOAuthError(msg)


def _utc_now() -> datetime:
    """Return an aware UTC timestamp."""
    return datetime.now(UTC)
