"""Unit tests for aws_client_service OAuth token store."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aws_client_service.token_store import (
    OAuthSession,
    create_oauth_session,
    delete_oauth_session,
    get_oauth_session,
)

pytestmark = pytest.mark.unit


def _store(monkeypatch: pytest.MonkeyPatch) -> Path:
    p = Path("/tmp/test-token-store")  # noqa: S108
    monkeypatch.setenv("OAUTH_SESSION_STORE_DIR", str(p))
    return p


class TestCreateAndGet:
    def test_create_and_retrieve(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(monkeypatch)
        store.mkdir(parents=True, exist_ok=True)
        try:
            session_id = create_oauth_session("gho_test-token")
            assert isinstance(session_id, str) and len(session_id) > 0

            session = get_oauth_session(session_id)
            assert session is not None
            assert isinstance(session, OAuthSession)
            assert session.access_token == "gho_test-token"
        finally:
            for p in store.glob("*"):
                p.unlink()
            store.rmdir()

    def test_nonexistent_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(monkeypatch)
        store.mkdir(parents=True, exist_ok=True)
        try:
            assert get_oauth_session("no-such-id") is None
        finally:
            store.rmdir()


class TestGetErrors:
    def test_invalid_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(monkeypatch)
        store.mkdir(parents=True, exist_ok=True)
        (store / "bad.json").write_text("not valid json", encoding="utf-8")
        try:
            assert get_oauth_session("bad") is None
        finally:
            for p in store.glob("*"):
                p.unlink()
            store.rmdir()

    def test_oserror_on_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(monkeypatch)
        store.mkdir(parents=True, exist_ok=True)
        f = store / "oserror.json"
        f.write_text("{}", encoding="utf-8")
        f.chmod(0o000)
        try:
            result = get_oauth_session("oserror")
            assert result is None
        finally:
            f.chmod(0o644)
            f.unlink()
            store.rmdir()

    def test_missing_access_token_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(monkeypatch)
        store.mkdir(parents=True, exist_ok=True)
        (store / "notoken.json").write_text(
            json.dumps({"other": "value"}), encoding="utf-8"
        )
        try:
            assert get_oauth_session("notoken") is None
        finally:
            for p in store.glob("*"):
                p.unlink()
            store.rmdir()

    def test_empty_access_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(monkeypatch)
        store.mkdir(parents=True, exist_ok=True)
        (store / "emptytoken.json").write_text(
            json.dumps({"access_token": ""}), encoding="utf-8"
        )
        try:
            assert get_oauth_session("emptytoken") is None
        finally:
            for p in store.glob("*"):
                p.unlink()
            store.rmdir()


class TestDelete:
    def test_deletes_existing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(monkeypatch)
        store.mkdir(parents=True, exist_ok=True)
        sid = "delete-me"
        (store / f"{sid}.json").write_text(
            json.dumps({"access_token": "tok"}), encoding="utf-8"
        )
        try:
            delete_oauth_session(sid)
            assert not (store / f"{sid}.json").exists()
        finally:
            for p in store.glob("*"):
                p.unlink()
            store.rmdir()

    def test_delete_nonexistent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        store = _store(monkeypatch)
        store.mkdir(parents=True, exist_ok=True)
        try:
            delete_oauth_session("no-such-file")
        finally:
            store.rmdir()
