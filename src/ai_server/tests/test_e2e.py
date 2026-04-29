r"""End-to-end tests against the live deployed AI server.

These tests are **skipped** unless all of these are set:

    export RUN_AI_SERVER_E2E=1
    export AI_SERVER_BASE_URL=https://ospsd-team-2.fly.dev
    export AI_SERVER_SIGNING_SECRET=<the wrapper signing secret>

Run them with:

    RUN_AI_SERVER_E2E=1 \
    AI_SERVER_BASE_URL=https://ospsd-team-2.fly.dev \
    AI_SERVER_SIGNING_SECRET=<wrapper-secret> \
    uv run pytest src/ai_server/tests/test_e2e.py -v -m e2e

----------------------------------------------------------------------------
Slack middleman integration contract
----------------------------------------------------------------------------

The Slack bot receives a Slack webhook, ACKs immediately (< 3 s), then calls
the canonical wrapper-facing path.

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
    404  Removed route or wrong path — update the bridge to use /ai/chat/turn.
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


def _message_turn_body(
    *,
    event_id: str,
    text: str,
    message_ts: str,
    thread_ts: str | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "channel": "C123CHAN",
        "ts": message_ts,
        "user": "U123USER",
        "text": text,
    }
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return build_message_event_turn(
        workspace_id="T123TEAM",
        event_id=event_id,
        event=event,
    )


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


class TestHealthE2E:
    def test_health_returns_200(self, e2e_base_url: str) -> None:
        resp = httpx.get(f"{e2e_base_url}/ai/health", timeout=10)
        assert resp.status_code == 200

    def test_health_body(self, e2e_base_url: str) -> None:
        body = httpx.get(f"{e2e_base_url}/ai/health", timeout=10).json()
        assert body["status"] == "ok"
        assert body["service"] == "ai-server"

    def test_health_requires_no_auth(self, e2e_base_url: str) -> None:
        resp = httpx.get(f"{e2e_base_url}/ai/health", timeout=10)
        assert resp.status_code == 200


class TestRemovedLegacyRouteE2E:
    def test_legacy_chat_route_returns_404(self, e2e_base_url: str) -> None:
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat",
            json={"message": "hello", "session_id": "removed-route-check"},
            timeout=10,
        )
        assert resp.status_code == 404


class TestAuthGuardE2E:
    def test_missing_signed_headers_returns_401(self, e2e_base_url: str) -> None:
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

    def test_invalid_signature_returns_401(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        body = _message_turn_body(
            event_id="e2e-invalid-signature",
            text="hi",
            message_ts="1713840000.123456",
        )
        encoded = encode_turn_body(body)
        headers = sign_nimbus_request(body=encoded, secret=e2e_signing_secret)
        headers["X-Nimbus-Signature"] = "0" * 64
        resp = httpx.post(
            f"{e2e_base_url}/ai/chat/turn",
            content=encoded,
            headers=headers,
            timeout=10,
        )
        assert resp.status_code == 401


class TestValidationE2E:
    def test_empty_text_returns_422(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        body = _message_turn_body(
            event_id="e2e-empty-text",
            text="hi",
            message_ts="1713840000.123456",
        )
        body["text"] = ""
        resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=body,
            timeout=10,
        )
        assert resp.status_code == 422

    def test_unsafe_workspace_id_returns_422(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        body = _message_turn_body(
            event_id="e2e-unsafe-workspace",
            text="hi",
            message_ts="1713840000.123456",
        )
        body["workspace_id"] = "../../etc/passwd"
        resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=body,
            timeout=10,
        )
        assert resp.status_code == 422

    def test_missing_text_returns_422(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        body = _message_turn_body(
            event_id="e2e-missing-text",
            text="hi",
            message_ts="1713840000.123456",
        )
        del body["text"]
        resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=body,
            timeout=10,
        )
        assert resp.status_code == 422


class TestWrapperTurnE2E:
    def test_signed_wrapper_turn_returns_structured_reply(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        event_id = f"e2e-event-{int(time.time())}"
        body = _message_turn_body(
            event_id=event_id,
            text="Reply with exactly one short sentence saying hello.",
            message_ts=str(time.time()),
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
        body = _message_turn_body(
            event_id=event_id,
            text="Say hi.",
            message_ts=str(time.time()),
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


class TestConversationContinuityE2E:
    def test_same_thread_reuses_conversation_id(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        root_thread_ts = f"1713840000.{int(time.time())}"
        first = _message_turn_body(
            event_id=f"e2e-thread-a-{int(time.time())}",
            text="First turn in the thread.",
            message_ts=f"{time.time():.6f}",
            thread_ts=root_thread_ts,
        )
        second = _message_turn_body(
            event_id=f"e2e-thread-b-{int(time.time()) + 1}",
            text="Second turn in the same thread.",
            message_ts=f"{time.time() + 1:.6f}",
            thread_ts=root_thread_ts,
        )

        first_resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=first,
        )
        second_resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=second,
        )

        assert first_resp.status_code == 200
        assert second_resp.status_code == 200
        assert (
            first_resp.json()["conversation_id"]
            == second_resp.json()["conversation_id"]
        )

    def test_different_threads_are_isolated(
        self, e2e_base_url: str, e2e_signing_secret: str
    ) -> None:
        first = _message_turn_body(
            event_id=f"e2e-isolated-a-{int(time.time())}",
            text="Thread A",
            message_ts=f"{time.time():.6f}",
            thread_ts="1713840000.111111",
        )
        second = _message_turn_body(
            event_id=f"e2e-isolated-b-{int(time.time()) + 1}",
            text="Thread B",
            message_ts=f"{time.time() + 1:.6f}",
            thread_ts="1713840000.222222",
        )

        first_resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=first,
        )
        second_resp = _post_signed_turn(
            e2e_base_url=e2e_base_url,
            signing_secret=e2e_signing_secret,
            body=second,
        )

        assert first_resp.status_code == 200
        assert second_resp.status_code == 200
        assert (
            first_resp.json()["conversation_id"]
            != second_resp.json()["conversation_id"]
        )
