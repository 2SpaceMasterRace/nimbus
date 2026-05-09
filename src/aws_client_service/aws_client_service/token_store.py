"""Server-side storage for OAuth access tokens.

The browser session cookie must not contain raw GitHub access tokens. Instead,
the cookie stores only an opaque session handle while the token itself lives in
this server-side store.
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_TOKEN_STORE_DIR = Path.home() / ".ospsd-team-2" / "oauth_sessions"
_TOKEN_STORE_ENV = "OAUTH_SESSION_STORE_DIR"  # noqa: S105 - env var name, not a secret
_TOKEN_SESSION_ID_BYTES = 32
_TOKEN_STORE_DIR_MODE = 0o700
_TOKEN_FILE_MODE = 0o600


@dataclass(frozen=True)
class OAuthSession:
    """Server-side OAuth session payload."""

    access_token: str


def _store_dir() -> Path:
    raw = os.environ.get(_TOKEN_STORE_ENV, "").strip()
    return Path(raw) if raw else _DEFAULT_TOKEN_STORE_DIR


def _session_path(session_id: str) -> Path:
    return _store_dir() / f"{session_id}.json"


def _new_session_id() -> str:
    return secrets.token_urlsafe(_TOKEN_SESSION_ID_BYTES)


def create_oauth_session(access_token: str) -> str:
    """Persist *access_token* server-side and return an opaque session ID."""
    session_id = _new_session_id()
    store = _store_dir()
    store.mkdir(parents=True, exist_ok=True)
    store.chmod(_TOKEN_STORE_DIR_MODE)
    path = _session_path(session_id)
    tmp = path.with_suffix(".tmp")
    payload = {"access_token": access_token}
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.chmod(_TOKEN_FILE_MODE)
    tmp.replace(path)
    path.chmod(_TOKEN_FILE_MODE)
    return session_id


def get_oauth_session(session_id: str) -> OAuthSession | None:
    """Return the stored OAuth session for *session_id*, if present."""
    path = _session_path(session_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None
    return OAuthSession(access_token=access_token)


def delete_oauth_session(session_id: str) -> None:
    """Delete the stored OAuth session for *session_id*, if it exists."""
    _session_path(session_id).unlink(missing_ok=True)
