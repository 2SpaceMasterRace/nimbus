r"""End-to-end tests against the live deployed AI server.

These tests are **skipped** unless all of these are set:

    export RUN_AI_SERVER_E2E=1
    export AI_SERVER_BASE_URL=https://ospsd-team-2.fly.dev
    export AI_SERVER_API_KEY=<the secret from fly secrets>
    export AI_SERVER_SIGNING_SECRET=<the wrapper signing secret>

Run them with:

    RUN_AI_SERVER_E2E=1 \
    AI_SERVER_BASE_URL=https://ospsd-team-2.fly.dev \
    AI_SERVER_API_KEY=<key> \
    AI_SERVER_SIGNING_SECRET=<wrapper-secret> \
    uv run pytest src/ai_server/tests/test_e2e.py -v -m e2e

----------------------------------------------------------------------------
Slack middleman integration contract
----------------------------------------------------------------------------

The Slack bot receives a Slack webhook, ACKs immediately (< 3 s), then calls
either the legacy path or the canonical wrapper-facing path.

Legacy path:

    POST  {AI_SERVER_BASE_URL}/ai/chat
    X-API-Key: {AI_SERVER_API_KEY}
    Content-Type: application/json

    {
        "message":    "<user text from Slack>",
        "session_id": "<slack channel ID or thread_ts>",
        "user_id":    "<slack user ID>"          // optional but recommended
    }

Canonical wrapper-facing path:

    POST  {AI_SERVER_BASE_URL}/ai/chat/turn
    X-Nimbus-Timestamp: <unix seconds>
    X-Nimbus-Nonce: <single-use nonce>
    X-Nimbus-Signature: <hex hmac sha256>

    {
        "platform": "slack",
        "workspace_id": "T123TEAM",
        "channel_id": "C123CHAN",
        "thread_id": "1713840000.123456",
        "message_id": "1713840000.123456",
        "user_id": "U123USER",
        "text": "What files are under reports/?",
        "idempotency_key": "slack:T123TEAM:event:evt-123",
        "request_id": "req-slack-evt-123"
    }

Errors the Slack bot must handle:

    401  Invalid/missing auth or signature — server configuration problem.
    422  Validation error — malformed request shape or unsafe identifiers.
    429  Upstream/provider or local rate limit — tell user to retry shortly.
    502  Upstream AI error — tell user the AI is unavailable.
    503  Server-side auth/config issue — alert on-call.
    504  Upstream timeout — tell user the AI took too long.
"""

from __future__ import annotations

import time

import httpx
import pytest
from ai_server.wrapper_client import (
    build_message_event_turn,
    build_slash_command_turn,
    encode_turn_body,
    sign_nimbus_request,
)

pytestmark = pytest.mark.e2e


def _post_signed_turn(
    *,
    e2e_base_url: str,
    signing_secret: str,
    body: dict[str, object],
    timeout: float = 60.0,
) -> httpx.Response:
    body_bytes = encode_turn_body(body)
    headers = sign_nimbus_request(body=body_bytes, secret=signing_secret)
    return httpx.post(
        f"{e2e_base_url}/ai/chat/turn",
        content=body_bytes,
        headers=headers,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealthE2E:
    def test_health_returns_200(self, e2e_base_url: str) -> None:
        resp = httpx.get(f"{e2e_base_url}/ai/health", timeout=10)
        assert resp.status_code == 200

    def test_health_body(self, e2e_base_url: str) -> None:
        body = httpx.get(f"{e2e_base_url}/ai/health", timeout=10).json()
        assert body["status"] == "ok"
        assert body["service"] == "ai-server"

    def test_health_requires_no_auth(self, e2e_base_url: str) -> None:
        # Absolutely no headers — must still return 200
        resp = httpx.get(f"{e2e_base_url}/ai/health", timeout=10)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------


class TestAuthGuardE2E:
    def test_missing_api_key_returns_401(self, e2e_base_url: str) -> None:
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            json={"message": "hello", "session_id": "e2e-auth-test"},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_wrong_api_key_returns_401(self, e2e_base_url: str) -> None:
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": "definitely-wrong-key"},
            json={"message": "hello", "session_id": "e2e-auth-test"},
            timeout=10,
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidationE2E:
    def test_empty_message_returns_422(
        self, e2e_base_url: str, e2e_api_key: str
    ) -> None:
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={"message": "", "session_id": "e2e-validation"},
            timeout=10,
        )
        assert resp.status_code == 422

    def test_unsafe_session_id_returns_422(
        self, e2e_base_url: str, e2e_api_key: str
    ) -> None:
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={"message": "hi", "session_id": "../../etc/passwd"},
            timeout=10,
        )
        assert resp.status_code == 422

    def test_missing_message_returns_422(
        self, e2e_base_url: str, e2e_api_key: str
    ) -> None:
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={"session_id": "e2e-validation"},
            timeout=10,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Real chat
# ---------------------------------------------------------------------------


class TestChatE2E:
    def test_chat_returns_200(self, e2e_base_url: str, e2e_api_key: str) -> None:
        session_id = f"e2e-chat-{int(time.time())}"
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={
                "message": "Reply with exactly one word: hello",
                "session_id": session_id,
            },
            timeout=60,
        )
        assert resp.status_code == 200


class TestWrapperTurnE2E:
    def test_wrapper_turn_missing_signed_headers_returns_401(
        self, e2e_base_url: str
    ) -> None:
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat/turn",
            json={
                "platform": "slack",
                "workspace_id": "T123TEAM",
                "channel_id": "C123CHAN",
                "thread_id": "1713840000.123456",
                "message_id": "1713840000.123456",
                "user_id": "U123USER",
                "text": "hi",
                "idempotency_key": "slack:T123TEAM:event:e2e-missing-headers",
                "request_id": "req-slack-e2e-missing-headers",
            },
            timeout=10,
        )
        assert resp.status_code == 401

    def test_signed_wrapper_turn_returns_structured_reply(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        event_id = f"e2e-event-{int(time.time())}"
        body = build_message_event_turn(
            workspace_id="T123TEAM",
            event_id=event_id,
            event={
                "channel": "C123CHAN",
                "ts": str(time.time()),
                "user": "U123USER",
                "text": "Reply with exactly one short sentence saying hello.",
            },
        )

        resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=body,
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["request_id"] == body["request_id"]
        assert isinstance(payload["conversation_id"], str)
        assert payload["outcome"] in {
            "reply",
            "confirmation_required",
            "partial_success",
            "error",
        }
        assert isinstance(payload["text"], str)

    def test_signed_slash_command_shape_anchors_to_synthetic_message_id(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        trigger_id = f"trigger-{int(time.time())}"
        body = build_slash_command_turn(
            workspace_id="T123TEAM",
            channel_id="C123CHAN",
            trigger_id=trigger_id,
            user_id="U123USER",
            text="recent",
        )

        resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=body,
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["conversation_id"] == f"slack:T123TEAM:C123CHAN:cmd:{trigger_id}"

    def test_signed_wrapper_turn_idempotent_retry_reuses_cached_response(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        event_id = f"e2e-idempotent-{int(time.time())}"
        body = build_message_event_turn(
            workspace_id="T123TEAM",
            event_id=event_id,
            event={
                "channel": "C123CHAN",
                "ts": str(time.time()),
                "user": "U123USER",
                "text": "Say hi.",
            },
        )

        first = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=body,
        )
        second = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=body,
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()

    def test_chat_response_shape(self, e2e_base_url: str, e2e_api_key: str) -> None:
        session_id = f"e2e-shape-{int(time.time())}"
        body = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={"message": "Say hi", "session_id": session_id},
            timeout=60,
        ).json()
        assert "response" in body
        assert "session_id" in body
        assert "model" in body
        assert "steps" in body
        assert "fallback_used" in body

    def test_chat_echoes_session_id(self, e2e_base_url: str, e2e_api_key: str) -> None:
        session_id = f"e2e-echo-{int(time.time())}"
        body = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={"message": "hi", "session_id": session_id},
            timeout=60,
        ).json()
        assert body["session_id"] == session_id

    def test_chat_response_is_non_empty_string(
        self, e2e_base_url: str, e2e_api_key: str
    ) -> None:
        session_id = f"e2e-nonempty-{int(time.time())}"
        body = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={"message": "What is 2 + 2?", "session_id": session_id},
            timeout=60,
        ).json()
        assert isinstance(body["response"], str)
        assert len(body["response"]) > 0

    def test_chat_with_user_id(self, e2e_base_url: str, e2e_api_key: str) -> None:
        session_id = f"e2e-uid-{int(time.time())}"
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={
                "message": "hi",
                "session_id": session_id,
                "user_id": "U01TESTUSER",
            },
            timeout=60,
        )
        assert resp.status_code == 200

    def test_chat_with_unicode_message(
        self, e2e_base_url: str, e2e_api_key: str
    ) -> None:
        session_id = f"e2e-unicode-{int(time.time())}"
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers={"X-API-Key": e2e_api_key},
            json={
                "message": "你好! What language did I just use?",
                "session_id": session_id,
            },
            timeout=60,
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Session continuity
# ---------------------------------------------------------------------------


class TestSessionContinuityE2E:
    def test_second_turn_sees_first_turn(
        self, e2e_base_url: str, e2e_api_key: str
    ) -> None:
        """The AI must remember the conversation across turns in the same session."""
        session_id = f"e2e-continuity-{int(time.time())}"
        headers = {"X-API-Key": e2e_api_key}

        # Turn 1: plant a memorable fact
        httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers=headers,
            json={
                "message": "Remember this number: 7429. Just say 'Got it'.",
                "session_id": session_id,
            },
            timeout=60,
        )

        # Turn 2: ask for it back
        body = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers=headers,
            json={
                "message": "What number did I ask you to remember?",
                "session_id": session_id,
            },
            timeout=60,
        ).json()

        assert "7429" in body["response"], (
            f"AI did not recall the planted number. Response was: {body['response']!r}"
        )

    def test_independent_sessions_do_not_bleed(
        self, e2e_base_url: str, e2e_api_key: str
    ) -> None:
        """A fact planted in session A must not appear in session B."""
        ts = int(time.time())
        session_a = f"e2e-bleed-a-{ts}"
        session_b = f"e2e-bleed-b-{ts}"
        headers = {"X-API-Key": e2e_api_key}

        httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers=headers,
            json={
                "message": "My secret code is XYZZY. Just say acknowledged.",
                "session_id": session_a,
            },
            timeout=60,
        )

        body = httpx.post(
            f"{e2e_base_url}/ai/chat",
            headers=headers,
            json={
                "message": "Do you know any secret codes?",
                "session_id": session_b,
            },
            timeout=60,
        ).json()

        assert "XYZZY" not in body["response"], (
            f"Session B leaked data from session A. Response: {body['response']!r}"
        )
