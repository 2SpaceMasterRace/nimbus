"""Tests for server-side OAuth token storage."""

from __future__ import annotations

from pathlib import Path

import pytest
from aws_client_service.token_store import (
    create_oauth_session,
    delete_oauth_session,
    get_oauth_session,
)

pytestmark = pytest.mark.unit


def test_create_and_get_oauth_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stored access tokens are retrievable by opaque session ID."""
    monkeypatch.setenv("OAUTH_SESSION_STORE_DIR", str(tmp_path))

    session_id = create_oauth_session("secret-token")
    stored = get_oauth_session(session_id)

    assert stored is not None
    assert stored.access_token == "secret-token"


def test_delete_oauth_session_removes_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Deleting a stored session makes future lookups return None."""
    monkeypatch.setenv("OAUTH_SESSION_STORE_DIR", str(tmp_path))

    session_id = create_oauth_session("secret-token")
    delete_oauth_session(session_id)

    assert get_oauth_session(session_id) is None
