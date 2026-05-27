"""Unit tests for the persistent expiring request-state store."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from ai_server.request_state import get_state, put_state, put_state_if_absent

pytestmark = pytest.mark.unit


class TestRequestState:
    def test_put_state_round_trips_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path))

        cleaned_entries = put_state(
            "idempotent_turns",
            "cache-key-1",
            value={"text": "hello"},
            expires_at=time.time() + 60,
        )
        result = get_state("idempotent_turns", "cache-key-1")

        assert cleaned_entries == 0
        assert result.value == {"text": "hello"}
        assert result.cleaned_entries == 0

    def test_get_state_removes_expired_entry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path))

        put_state(
            "signed_request_nonces",
            "nonce-1",
            value={"path": "/ai/chat/turn"},
            expires_at=time.time() - 1,
        )
        first = get_state("signed_request_nonces", "nonce-1")
        second = get_state("signed_request_nonces", "nonce-1")

        assert first.value is None
        assert first.cleaned_entries == 1
        assert second.value is None
        assert second.cleaned_entries == 0

    def test_put_state_if_absent_overwrites_expired_entry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path))

        put_state(
            "signed_request_nonces",
            "nonce-2",
            value={"path": "/ai/chat/turn"},
            expires_at=time.time() - 1,
        )

        write_result = put_state_if_absent(
            "signed_request_nonces",
            "nonce-2",
            value={"path": "/ai/chat/turn", "timestamp": "123"},
            expires_at=time.time() + 60,
        )
        read_result = get_state("signed_request_nonces", "nonce-2")

        assert write_result.stored is True
        assert write_result.cleaned_entries == 1
        assert read_result.value == {"path": "/ai/chat/turn", "timestamp": "123"}

    def test_put_state_if_absent_rejects_live_duplicate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path))

        first = put_state_if_absent(
            "idempotent_turn_claims",
            "turn-cache-key",
            value={"request_fingerprint": "first", "status": "in_flight"},
            expires_at=time.time() + 60,
        )
        second = put_state_if_absent(
            "idempotent_turn_claims",
            "turn-cache-key",
            value={"request_fingerprint": "second", "status": "in_flight"},
            expires_at=time.time() + 60,
        )
        read_result = get_state("idempotent_turn_claims", "turn-cache-key")

        assert first.stored is True
        assert second.stored is False
        assert read_result.value == {
            "request_fingerprint": "first",
            "status": "in_flight",
        }
