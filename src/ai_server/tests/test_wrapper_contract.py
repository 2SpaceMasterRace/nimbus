"""Contract tests for the wrapper-facing ``POST /ai/chat/turn`` endpoint."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import ai_server.auth as auth_mod
import ai_server.router as router_mod
import nimbus_runtime.runtime as runtime_mod
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from ai_client_api import AIResponse, TokenUsage

if TYPE_CHECKING:
    from conftest import FakeAIClient, FakeStorageClient

pytestmark = pytest.mark.unit

TEST_SIGNING_SECRET = "test-signing-secret-xyz"
_MAX_ATTACHMENTS_PER_TURN = 10
_MAX_ATTACHMENT_SIZE_BYTES = 20 * 1024 * 1024


def _sign_headers(
    *,
    body: bytes,
    secret: str,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    ts = int(time.time()) if timestamp is None else timestamp
    used_nonce = nonce or f"nonce-{uuid.uuid4().hex}"
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = f"POST\n/ai/chat/turn\n{ts}\n{used_nonce}\n{body_digest}"
    signature = hmac.new(
        secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return {
        "X-Nimbus-Timestamp": str(ts),
        "X-Nimbus-Nonce": used_nonce,
        "X-Nimbus-Signature": signature,
    }


def _turn_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "platform": "slack",
        "workspace_id": "T123TEAM",
        "channel_id": "C123CHAN",
        "thread_id": "1713840000.123456",
        "message_id": "1713840000.123457",
        "user_id": "U123USER",
        "text": "What files are under reports/?",
        "idempotency_key": "slack:T123TEAM:event:evt-123",
        "request_id": "req-wrapper-123",
    }
    body.update(overrides)
    return body


def _attachment(**overrides: object) -> dict[str, object]:
    attachment: dict[str, object] = {
        "platform_file_id": "F123FILE",
        "filename": "report.csv",
        "content_type": "text/csv",
        "size_bytes": 183210,
    }
    attachment.update(overrides)
    return attachment


def _inline_attachment_bytes(text: str, **overrides: object) -> dict[str, object]:
    payload = text.encode("utf-8")
    attachment = _attachment(
        content_base64=base64.b64encode(payload).decode("ascii"),
        sha256_hex=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )
    attachment.update(overrides)
    return attachment


def _encoded_json_body(body: dict[str, object]) -> bytes:
    return json.dumps(body).encode("utf-8")


def _fake_response(text: str) -> AIResponse:
    return AIResponse(
        text=text,
        model="test-model:free",
        tokens=TokenUsage(input_tokens=10, output_tokens=20),
        tool_calls=(),
        latency_ms=50,
        stop_reason="end_turn",
        steps=1,
        fallback_used=False,
    )


class _ToolCallingAIClient:
    """Fake AI client that actively exercises one passed-in wrapper tool."""

    def __init__(self, *, tool_name: str, tool_kwargs: dict[str, object]) -> None:
        self._tool_name = tool_name
        self._tool_kwargs = tool_kwargs

    def send_message(
        self, conv: object, *, tools: object = None, **_kwargs: object
    ) -> AIResponse:
        del conv
        assert isinstance(tools, list)
        tool = next(tool for tool in tools if tool.name == self._tool_name)
        result = tool.handler(**self._tool_kwargs)
        return _fake_response(json.dumps(result, sort_keys=True))

    def on_event(self, _listener: object) -> None:
        pass


def _post_signed_turn(
    client: TestClient,
    *,
    body: dict[str, object],
    secret: str,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> Response:
    encoded = _encoded_json_body(body)
    headers = _sign_headers(
        body=encoded,
        secret=secret,
        timestamp=timestamp,
        nonce=nonce,
    )
    headers["Content-Type"] = "application/json"
    return client.post("/ai/chat/turn", content=encoded, headers=headers)


@pytest.fixture(autouse=True)
def _clear_wrapper_caches() -> None:
    auth_mod._seen_nonces.clear()  # noqa: SLF001
    router_mod._idempotent_turns.clear()  # noqa: SLF001
    router_mod._rate_buckets.clear()  # noqa: SLF001
    runtime_mod._session_locks.clear()  # noqa: SLF001


class TestChatTurnContract:
    def test_returns_200_and_normalized_contract_fields(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body()

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["request_id"] == "req-wrapper-123"
        assert payload["conversation_id"] == "slack:T123TEAM:C123CHAN:1713840000.123456"
        assert payload["text"] == "Hello from Nimbus!"
        assert payload["outcome"] == "reply"
        assert payload["confirmation_required"] is False
        assert payload["confirmation"] is None
        assert payload["suggested_next_actions"] == []
        assert payload["model"] == "test-model:free"
        assert payload["steps"] == 1
        assert payload["fallback_used"] is False
        assert fake_client.calls[-1]["conv"].session_id == payload["conversation_id"]

    def test_passes_read_only_storage_tools_to_the_ai_client(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        resp = _post_signed_turn(client, body=_turn_body(), secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        tools = fake_client.calls[-1]["tools"]
        assert tools is not None
        assert {tool.name for tool in tools} == {"list_files", "get_file_info"}

    def test_uses_message_id_when_thread_id_is_missing(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(thread_id=None, request_id="req-no-thread")

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert (
            resp.json()["conversation_id"]
            == "slack:T123TEAM:C123CHAN:1713840000.123457"
        )

    def test_generates_request_id_when_omitted(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id=None)

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert resp.json()["request_id"].startswith("req-")

    def test_exposes_attachment_metadata_to_the_ai_context(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(attachments=[_attachment()])

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        user_message = fake_client.calls[-1]["conv"].messages()[-1].content
        assert "Wrapper-provided attachments for this turn" in user_message
        assert "F123FILE" in user_message
        assert "report.csv" in user_message
        assert "content_base64" not in user_message

    def test_accepts_worst_case_identifier_lengths_without_session_overflow(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        request_id = "r" * 128
        body = _turn_body(
            platform="abcdefghijklmnop",
            workspace_id="w" * 64,
            channel_id="c" * 64,
            thread_id="t" * 64,
            message_id="m" * 64,
            user_id="u" * 64,
            idempotency_key="i" * 256,
            request_id=request_id,
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["request_id"] == request_id
        assert len(payload["conversation_id"]) > 128
        session_files = list((tmp_path / "sessions").glob("*.json"))
        assert len(session_files) == 1
        assert session_files[0].stem.startswith("sha256-")
        stored = json.loads(session_files[0].read_text(encoding="utf-8"))
        assert stored["session_id"] == payload["conversation_id"]


class TestSlackMappingSemantics:
    def test_top_level_message_anchors_conversation_to_event_ts(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        top_level_ts = "1713840000.555555"
        body = _turn_body(
            thread_id=top_level_ts,
            message_id=top_level_ts,
            request_id="req-slack-top-level",
            idempotency_key="slack:T123TEAM:event:evt-top-level",
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert (
            resp.json()["conversation_id"] == f"slack:T123TEAM:C123CHAN:{top_level_ts}"
        )

    def test_thread_reply_keeps_the_root_thread_as_conversation_anchor(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        thread_ts = "1713840000.111111"
        reply_ts = "1713840000.222222"
        body = _turn_body(
            thread_id=thread_ts,
            message_id=reply_ts,
            request_id="req-slack-thread-reply",
            idempotency_key="slack:T123TEAM:event:evt-thread-reply",
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert resp.json()["conversation_id"] == f"slack:T123TEAM:C123CHAN:{thread_ts}"

    def test_direct_message_uses_the_same_thread_anchor_rule(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        dm_ts = "1713840000.333333"
        body = _turn_body(
            channel_id="D123DM",
            thread_id=dm_ts,
            message_id=dm_ts,
            request_id="req-slack-dm",
            idempotency_key="slack:T123TEAM:event:evt-dm",
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert resp.json()["conversation_id"] == f"slack:T123TEAM:D123DM:{dm_ts}"

    def test_slash_command_without_thread_uses_synthetic_message_id_as_anchor(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        trigger_id = "1337-trigger"
        body = _turn_body(
            thread_id=None,
            message_id=f"cmd:{trigger_id}",
            text="recent",
            request_id=f"req-slack-cmd-{trigger_id}",
            idempotency_key=f"slack:T123TEAM:command:{trigger_id}",
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert (
            resp.json()["conversation_id"]
            == f"slack:T123TEAM:C123CHAN:cmd:{trigger_id}"
        )

    def test_slash_command_can_be_attached_to_an_existing_thread(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        trigger_id = "attached-cmd"
        thread_ts = "1713840000.444444"
        body = _turn_body(
            thread_id=thread_ts,
            message_id=f"cmd:{trigger_id}",
            text="recent",
            request_id=f"req-slack-cmd-{trigger_id}",
            idempotency_key=f"slack:T123TEAM:command:{trigger_id}",
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert resp.json()["conversation_id"] == f"slack:T123TEAM:C123CHAN:{thread_ts}"

    def test_duplicate_slash_command_trigger_reuses_cached_response(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        trigger_id = "retryable-trigger"
        body = _turn_body(
            thread_id=None,
            message_id=f"cmd:{trigger_id}",
            text="recent",
            request_id=f"req-slack-cmd-{trigger_id}",
            idempotency_key=f"slack:T123TEAM:command:{trigger_id}",
        )

        first = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)
        second = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert len(fake_client.calls) == 1


class TestChatTurnAuth:
    def test_rejects_missing_signed_headers(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        resp = client.post("/ai/chat/turn", json=_turn_body())

        assert resp.status_code == 401
        assert "signed" in resp.json()["detail"].lower()

    def test_rejects_invalid_signature(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body()
        encoded = _encoded_json_body(body)
        headers = {
            "X-Nimbus-Timestamp": str(int(time.time())),
            "X-Nimbus-Nonce": f"nonce-{uuid.uuid4().hex}",
            "X-Nimbus-Signature": "not-the-right-signature",
            "Content-Type": "application/json",
        }

        resp = client.post("/ai/chat/turn", content=encoded, headers=headers)

        assert resp.status_code == 401
        assert "invalid" in resp.json()["detail"].lower()

    def test_rejects_replayed_nonce(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-replay")
        nonce = f"nonce-{uuid.uuid4().hex}"

        first = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            nonce=nonce,
        )
        second = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            nonce=nonce,
        )

        assert first.status_code == 200
        assert second.status_code == 401
        assert "nonce" in second.json()["detail"].lower()

    def test_rejects_replayed_nonce_after_in_memory_cache_reset(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-persistent-replay")
        nonce = f"nonce-{uuid.uuid4().hex}"

        first = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            nonce=nonce,
        )
        auth_mod._seen_nonces.clear()  # noqa: SLF001
        second = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            nonce=nonce,
        )

        assert first.status_code == 200
        assert second.status_code == 401
        assert "nonce" in second.json()["detail"].lower()

    def test_rejects_stale_timestamp(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-stale")

        resp = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            timestamp=int(time.time()) - 1000,
        )

        assert resp.status_code == 401
        assert "timestamp" in resp.json()["detail"].lower()

    def test_rejects_future_timestamp(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A timestamp more than 300 s in the future is rejected as stale.

        Prevents pre-signed request harvesting where an attacker captures a
        valid signed request before it is sent and replays it later.
        """
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-future")

        resp = _post_signed_turn(
            client,
            body=body,
            secret=TEST_SIGNING_SECRET,
            timestamp=int(time.time()) + 1000,
        )

        assert resp.status_code == 401
        assert "timestamp" in resp.json()["detail"].lower()

    def test_rejects_non_integer_timestamp(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A non-numeric X-Nimbus-Timestamp must be rejected with 401, not 500.

        The auth layer must not leak a ValueError or crash the request handler
        when the header value cannot be parsed as an integer.
        """
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-bad-ts")
        encoded = _encoded_json_body(body)
        headers = {
            "X-Nimbus-Timestamp": "not-a-number",
            "X-Nimbus-Nonce": f"nonce-{uuid.uuid4().hex}",
            "X-Nimbus-Signature": "irrelevant",
            "Content-Type": "application/json",
        }

        resp = client.post("/ai/chat/turn", content=encoded, headers=headers)

        assert resp.status_code == 401
        assert "timestamp" in resp.json()["detail"].lower()

    def test_unconfigured_signing_secret_returns_503(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When AI_SERVER_SIGNING_SECRET is not set, the server returns 503.

        The service should signal misconfiguration, not silently accept any
        request or crash with an unhandled error.
        """
        monkeypatch.delenv("AI_SERVER_SIGNING_SECRET", raising=False)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient as _TestClient

        bare_app = FastAPI()
        bare_app.include_router(router_mod.router, prefix="/ai")
        bare_client = _TestClient(bare_app)

        body = _turn_body(request_id="req-no-secret")
        encoded = _encoded_json_body(body)
        headers = {
            "X-Nimbus-Timestamp": str(int(time.time())),
            "X-Nimbus-Nonce": f"nonce-{uuid.uuid4().hex}",
            "X-Nimbus-Signature": "any",
            "Content-Type": "application/json",
        }

        resp = bare_client.post("/ai/chat/turn", content=encoded, headers=headers)

        assert resp.status_code == 503


class TestChatTurnIdempotency:
    def test_duplicate_idempotency_key_reuses_cached_response(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-idempotent")

        first = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)
        second = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert len(fake_client.calls) == 1

    def test_duplicate_idempotency_key_reuses_persisted_response_after_memory_reset(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(request_id="req-persistent-idempotent")

        first = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)
        router_mod._idempotent_turns.clear()  # noqa: SLF001
        second = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json() == second.json()
        assert len(fake_client.calls) == 1

    def test_same_idempotency_key_with_different_body_is_rejected(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        first_body = _turn_body(
            request_id="req-idempotent-first",
            idempotency_key="slack:T123TEAM:event:evt-shared-key",
            text="What files are under reports?",
        )
        second_body = _turn_body(
            request_id="req-idempotent-second",
            idempotency_key="slack:T123TEAM:event:evt-shared-key",
            text="delete reports/2024/old.csv",
        )

        first = _post_signed_turn(client, body=first_body, secret=TEST_SIGNING_SECRET)
        second = _post_signed_turn(client, body=second_body, secret=TEST_SIGNING_SECRET)

        assert first.status_code == 200
        assert second.status_code == 409
        assert "different request parameters" in second.json()["detail"]
        assert len(fake_client.calls) == 1

    def test_same_idempotency_key_from_different_actor_is_not_replayed(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        key = "slack:T123TEAM:event:evt-actor-scoped"
        first_body = _turn_body(
            request_id="req-actor-one",
            idempotency_key=key,
            user_id="U123USER",
        )
        second_body = _turn_body(
            request_id="req-actor-two",
            idempotency_key=key,
            user_id="U999OTHER",
        )

        first = _post_signed_turn(client, body=first_body, secret=TEST_SIGNING_SECRET)
        second = _post_signed_turn(client, body=second_body, secret=TEST_SIGNING_SECRET)

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["request_id"] == "req-actor-one"
        assert second.json()["request_id"] == "req-actor-two"
        assert len(fake_client.calls) == 2

    def test_expired_cached_response_causes_fresh_ai_call(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        """After both caches expire, the same idempotency key triggers a fresh AI call."""  # noqa: E501
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(
            request_id="req-ttl",
            idempotency_key="slack:T123TEAM:event:evt-ttl-expiry",
        )

        first = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)
        assert first.status_code == 200
        assert len(fake_client.calls) == 1

        # Expire every in-memory entry by back-dating expires_at past the epoch.
        for entry in router_mod._idempotent_turns.values():  # noqa: SLF001
            entry.expires_at = 0.0

        # Also evict the persistent (file-backed) entry so both layers miss.
        req = router_mod.ChatTurnRequest(**body)  # type: ignore[arg-type]
        cache_key = router_mod._idempotency_cache_key(  # noqa: SLF001
            req,
            conversation_id=router_mod._compose_conversation_id(req),  # noqa: SLF001
        )
        router_mod.delete_state(
            router_mod._IDEMPOTENT_TURN_STATE_NAMESPACE,  # noqa: SLF001
            cache_key,
        )

        second = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)
        assert second.status_code == 200
        assert len(fake_client.calls) == 2


class TestChatTurnToolWiring:
    def test_route_can_exercise_list_files_through_the_bound_tool_surface(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fake_storage_client: FakeStorageClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-key-not-used")
        monkeypatch.setenv("AWS_BUCKET_NAME", "test-wrapper-bucket")
        fake_storage_client.list_return = [
            type(fake_storage_client.info_return)(
                object_name="reports/january.csv",
                size_bytes=123,
            )
        ]

        app = FastAPI()
        app.include_router(router_mod.router, prefix="/ai")
        app.dependency_overrides[router_mod.get_ai_client] = lambda: (
            _ToolCallingAIClient(
                tool_name="list_files",
                tool_kwargs={"prefix": "reports/"},
            )
        )
        app.dependency_overrides[router_mod.get_storage_client] = lambda: (
            fake_storage_client
        )
        client = TestClient(app)

        resp = _post_signed_turn(client, body=_turn_body(), secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert fake_storage_client.lists == [
            {"container": "test-wrapper-bucket", "prefix": "reports/"}
        ]
        assert "reports/january.csv" in resp.json()["text"]

    def test_route_can_exercise_get_file_info_through_the_bound_tool_surface(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        fake_storage_client: FakeStorageClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
        monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-key-not-used")
        monkeypatch.setenv("AWS_BUCKET_NAME", "test-wrapper-bucket")
        fake_storage_client.info_return = type(fake_storage_client.info_return)(
            object_name="reports/april.csv",
            size_bytes=183000,
            version_id="v9",
            updated_at="2026-04-21T10:15:00Z",
        )

        app = FastAPI()
        app.include_router(router_mod.router, prefix="/ai")
        app.dependency_overrides[router_mod.get_ai_client] = lambda: (
            _ToolCallingAIClient(
                tool_name="get_file_info",
                tool_kwargs={"remote_path": "reports/april.csv"},
            )
        )
        app.dependency_overrides[router_mod.get_storage_client] = lambda: (
            fake_storage_client
        )
        client = TestClient(app)

        resp = _post_signed_turn(client, body=_turn_body(), secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert fake_storage_client.infos == [
            {"container": "test-wrapper-bucket", "object_name": "reports/april.csv"}
        ]
        assert "reports/april.csv" in resp.json()["text"]


class TestChatTurnConfirmationFlows:
    def test_delete_request_returns_confirmation_required(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        resp = _post_signed_turn(
            client,
            body=_turn_body(
                text="delete reports/2024/old.csv",
                idempotency_key="slack:T123TEAM:event:evt-delete-1",
            ),
            secret=TEST_SIGNING_SECRET,
        )

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["outcome"] == "confirmation_required"
        assert payload["confirmation_required"] is True
        assert payload["model"] == "nimbus-runtime"
        assert payload["steps"] == 0
        assert payload["confirmation"]["kind"] == "delete_file"
        assert (
            payload["confirmation"]["expected_reply"]
            == "yes, delete reports/2024/old.csv"
        )
        assert "destructive" in payload["text"].lower()
        assert fake_client.calls == []

    def test_same_actor_can_confirm_pending_delete(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_storage_client: FakeStorageClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        first = _post_signed_turn(
            client,
            body=_turn_body(
                text="delete reports/2024/old.csv",
                idempotency_key="slack:T123TEAM:event:evt-delete-2",
            ),
            secret=TEST_SIGNING_SECRET,
        )
        second = _post_signed_turn(
            client,
            body=_turn_body(
                text="yes, delete reports/2024/old.csv",
                idempotency_key="slack:T123TEAM:event:evt-delete-3",
                request_id="req-wrapper-confirm",
            ),
            secret=TEST_SIGNING_SECRET,
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert second.json()["outcome"] == "reply"
        assert second.json()["text"] == "Deleted `reports/2024/old.csv`."
        assert fake_storage_client.deletes == [
            {"container": "test-wrapper-bucket", "object_name": "reports/2024/old.csv"}
        ]

    def test_different_actor_cannot_confirm_pending_delete(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_storage_client: FakeStorageClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        _post_signed_turn(
            client,
            body=_turn_body(
                text="delete reports/2024/old.csv",
                idempotency_key="slack:T123TEAM:event:evt-delete-4",
            ),
            secret=TEST_SIGNING_SECRET,
        )
        resp = _post_signed_turn(
            client,
            body=_turn_body(
                text="yes, delete reports/2024/old.csv",
                user_id="U999OTHER",
                idempotency_key="slack:T123TEAM:event:evt-delete-5",
                request_id="req-wrapper-wrong-user",
            ),
            secret=TEST_SIGNING_SECRET,
        )

        assert resp.status_code == 200
        assert resp.json()["outcome"] == "error"
        assert "original requester" in resp.json()["text"].lower()
        assert fake_storage_client.deletes == []

    def test_confirmation_for_wrong_path_returns_error(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_storage_client: FakeStorageClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)

        _post_signed_turn(
            client,
            body=_turn_body(
                text="delete reports/2024/old.csv",
                idempotency_key="slack:T123TEAM:event:evt-delete-6",
            ),
            secret=TEST_SIGNING_SECRET,
        )
        resp = _post_signed_turn(
            client,
            body=_turn_body(
                text="yes, delete reports/2024/new.csv",
                idempotency_key="slack:T123TEAM:event:evt-delete-7",
                request_id="req-wrapper-wrong-path",
            ),
            secret=TEST_SIGNING_SECRET,
        )

        assert resp.status_code == 200
        assert resp.json()["outcome"] == "error"
        assert "pending delete" in resp.json()["text"].lower()
        assert fake_storage_client.deletes == []


class TestAttachmentByteIngestion:
    def test_uploads_inline_attachment_bytes_end_to_end(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_client: FakeAIClient,
        fake_storage_client: FakeStorageClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(
            text="upload these files to finance/april",
            idempotency_key="slack:T123TEAM:event:evt-upload-1",
            attachments=[
                _inline_attachment_bytes("quarterly report", filename="report.txt")
            ],
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert resp.json()["outcome"] == "reply"
        assert resp.json()["model"] == "nimbus-runtime"
        assert fake_storage_client.uploads[0]["container"] == "test-wrapper-bucket"
        assert (
            fake_storage_client.uploads[0]["remote_path"] == "finance/april/report.txt"
        )
        assert fake_client.calls == []

    def test_partial_success_when_some_attachments_fail_ingestion(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_storage_client: FakeStorageClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        fake_storage_client.upload_error_by_remote_path["finance/april/fail.txt"] = (
            RuntimeError("backend boom")
        )
        body = _turn_body(
            text="upload these files to finance/april",
            idempotency_key="slack:T123TEAM:event:evt-upload-2",
            attachments=[
                _inline_attachment_bytes("ok", filename="ok.txt"),
                _inline_attachment_bytes("fail", filename="fail.txt"),
            ],
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert resp.json()["outcome"] == "partial_success"
        assert len(fake_storage_client.uploads) == 2
        assert "skipped 1" in resp.json()["text"].lower()

    def test_missing_attachment_bytes_returns_error_outcome(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        fake_storage_client: FakeStorageClient,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(
            text="upload these files to finance/april",
            idempotency_key="slack:T123TEAM:event:evt-upload-3",
            attachments=[_attachment(filename="report.txt")],
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 200
        assert resp.json()["outcome"] == "error"
        assert "not provided" in resp.json()["text"].lower()
        assert fake_storage_client.uploads == []


class TestChatTurnRateLimiting:
    def test_same_user_id_in_different_workspaces_does_not_collide(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        exhausted_key = "slack:T123TEAM:U123USER"
        router_mod._rate_buckets[exhausted_key] = router_mod._TokenBucket(  # noqa: SLF001
            tokens=0.0,
            last_refill=time.monotonic(),
        )

        first = _post_signed_turn(
            client,
            body=_turn_body(request_id="req-workspace-one"),
            secret=TEST_SIGNING_SECRET,
        )
        second = _post_signed_turn(
            client,
            body=_turn_body(
                workspace_id="T456TEAM",
                idempotency_key="slack:T456TEAM:event:evt-456",
                request_id="req-workspace-two",
            ),
            secret=TEST_SIGNING_SECRET,
        )

        assert first.status_code == 429
        assert second.status_code == 200


class TestChatTurnValidation:
    def test_requires_user_id(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body()
        body.pop("user_id")

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 422

    def test_rejects_more_than_ten_attachments(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(
            attachments=[_attachment(platform_file_id=f"F{i}") for i in range(11)]
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 422

    def test_rejects_attachment_above_size_cap(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(
            attachments=[
                _attachment(size_bytes=_MAX_ATTACHMENT_SIZE_BYTES + 1),
            ]
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 422

    def test_rejects_attachment_with_invalid_content_type(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        body = _turn_body(attachments=[_attachment(content_type="not-a-mime-type")])

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 422

    def test_rejects_inline_attachment_bytes_over_the_turn_budget(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        monkeypatch.setattr(router_mod, "_MAX_INLINE_ATTACHMENT_BYTES_PER_TURN", 10)
        body = _turn_body(
            attachments=[
                _inline_attachment_bytes("abcdef", filename="one.txt"),
                _inline_attachment_bytes(
                    "ghijkl",
                    filename="two.txt",
                    platform_file_id="F2",
                ),
            ]
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 422

    def test_rejects_inline_attachment_budget_when_declared_sizes_are_dishonest(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)
        monkeypatch.setattr(router_mod, "_MAX_INLINE_ATTACHMENT_BYTES_PER_TURN", 10)
        body = _turn_body(
            attachments=[
                _inline_attachment_bytes(
                    "abcdef",
                    filename="one.txt",
                    size_bytes=1,
                ),
                _inline_attachment_bytes(
                    "ghijkl",
                    filename="two.txt",
                    platform_file_id="F2",
                    size_bytes=1,
                ),
            ]
        )

        resp = _post_signed_turn(client, body=body, secret=TEST_SIGNING_SECRET)

        assert resp.status_code == 422
