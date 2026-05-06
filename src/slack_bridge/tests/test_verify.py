"""Tests for slack_bridge.verify."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest


def _make_signature(secret: str, timestamp: str, body: bytes) -> str:
    canonical = f"v0:{timestamp}:{body.decode('utf-8')}"
    digest = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v0={digest}"


class TestVerifySlackSecret:
    def test_valid_signature_returns_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from slack_bridge.verify import verify_slack_secret

        secret = "test-signing-secret"
        monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
        body = b"token=test&team_id=T1234"
        timestamp = str(int(time.time()))
        sig = _make_signature(secret, timestamp, body)

        assert (
            verify_slack_secret(body=body, timestamp=timestamp, slack_signature=sig)
            is True
        )

    def test_wrong_signature_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from slack_bridge.verify import verify_slack_secret

        monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing-secret")
        body = b"token=test&team_id=T1234"
        timestamp = str(int(time.time()))

        assert (
            verify_slack_secret(
                body=body, timestamp=timestamp, slack_signature="v0=wrongsignature"
            )
            is False
        )

    def test_stale_timestamp_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from slack_bridge.verify import verify_slack_secret

        secret = "test-signing-secret"
        monkeypatch.setenv("SLACK_SIGNING_SECRET", secret)
        body = b"token=test&team_id=T1234"
        timestamp = str(int(time.time()) - 400)
        sig = _make_signature(secret, timestamp, body)

        assert (
            verify_slack_secret(body=body, timestamp=timestamp, slack_signature=sig)
            is False
        )

    def test_missing_secret_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from slack_bridge.verify import verify_slack_secret

        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        assert (
            verify_slack_secret(
                body=b"body", timestamp="12345", slack_signature="v0=abc"
            )
            is False
        )

    def test_invalid_timestamp_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from slack_bridge.verify import verify_slack_secret

        monkeypatch.setenv("SLACK_SIGNING_SECRET", "test-signing-secret")
        assert (
            verify_slack_secret(
                body=b"body", timestamp="notanumber", slack_signature="v0=abc"
            )
            is False
        )
