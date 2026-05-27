"""BDD acceptance scenarios for the wrapper-facing Nimbus contract."""
# Step functions are documented by the matching Gherkin sentence; adding
# duplicate docstrings to every step makes the glue harder to scan.

from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import ai_server.auth as auth_mod
import ai_server.router as router_mod
import nimbus_runtime.runtime as runtime_mod
import pytest
from ai_server.router import get_ai_client, get_storage_client, router
from ai_server.wrapper_client import (
    build_message_event_turn,
    encode_turn_body,
    sign_nimbus_request,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest_bdd import given, parsers, scenarios, then, when

from ai_client_api import AIResponse, TokenUsage
from nimbus_runtime import runtime_telemetry

if TYPE_CHECKING:
    from httpx import Response

pytestmark = [pytest.mark.unit, pytest.mark.bdd]

scenarios("features")

TEST_API_KEY = "test-key-abc123"
TEST_SIGNING_SECRET = "test-signing-secret-xyz"
WORKSPACE_ID = "T123TEAM"
CHANNEL_ID = "C123CHAN"
THREAD_TS = "1713840000.000001"
DEFAULT_USER_ID = "U123USER"


@dataclass
class _FakeObjectInfo:
    object_name: str
    size_bytes: int | None = None
    version_id: str | None = None
    updated_at: str | None = None


@dataclass
class _FakeDeleteResult:
    deleted: bool = True
    version_id: str | None = None


@dataclass
class _FakeStorageClient:
    lists: list[dict[str, Any]] = field(default_factory=list)
    infos: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[dict[str, Any]] = field(default_factory=list)
    uploads: list[dict[str, Any]] = field(default_factory=list)
    upload_error_by_remote_path: dict[str, Exception] = field(default_factory=dict)

    def list_files(self, *, container: str, prefix: str = "") -> list[_FakeObjectInfo]:
        self.lists.append({"container": container, "prefix": prefix})
        return [_FakeObjectInfo(object_name="reports/april.csv", size_bytes=17)]

    def get_file_info(self, *, container: str, object_name: str) -> _FakeObjectInfo:
        self.infos.append({"container": container, "object_name": object_name})
        return _FakeObjectInfo(object_name=object_name, size_bytes=17)

    def delete_file(self, *, container: str, object_name: str) -> _FakeDeleteResult:
        self.deletes.append({"container": container, "object_name": object_name})
        return _FakeDeleteResult(deleted=True)

    def upload_file(
        self, *, container: str, local_path: str, remote_path: str
    ) -> _FakeObjectInfo:
        self.uploads.append(
            {
                "container": container,
                "local_path": local_path,
                "remote_path": remote_path,
            }
        )
        if remote_path in self.upload_error_by_remote_path:
            raise self.upload_error_by_remote_path[remote_path]
        return _FakeObjectInfo(object_name=remote_path)


def _fake_response(text: str = "Hello from Nimbus!") -> AIResponse:
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


class _FakeAIClient:
    """AI client test double that records calls and optionally invokes a tool."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tool_call: tuple[str, dict[str, object]] | None = None

    def send_message(
        self, conv: object, *, tools: list[object] | None = None, **_kwargs: object
    ) -> AIResponse:
        self.calls.append({"conv": conv, "tools": tools})
        if self.tool_call is None:
            return _fake_response()
        tool_name, tool_kwargs = self.tool_call
        assert tools is not None
        typed_tools = [cast("_ToolLike", tool) for tool in tools]
        tool = next(tool for tool in typed_tools if tool.name == tool_name)
        result = tool.handler(**tool_kwargs)
        return _fake_response(str(result))

    def on_event(self, _listener: object) -> None:
        pass


@dataclass
class _BDDContext:
    body: dict[str, object] | None = None
    attachments: list[dict[str, object]] = field(default_factory=list)
    response: Response | None = None
    first_response: Response | None = None
    second_response: Response | None = None


class _ToolLike(Protocol):
    name: str
    handler: Callable[..., object]


@pytest.fixture(autouse=True)
def _reset_global_state() -> None:
    auth_mod._seen_nonces.clear()
    router_mod._idempotent_turns.clear()
    router_mod._rate_buckets.clear()
    runtime_mod._session_locks.clear()
    runtime_telemetry.reset()


@pytest.fixture
def fake_ai_client() -> _FakeAIClient:
    return _FakeAIClient()


@pytest.fixture
def fake_storage_client() -> _FakeStorageClient:
    return _FakeStorageClient()


@pytest.fixture
def app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_ai_client: _FakeAIClient,
    fake_storage_client: _FakeStorageClient,
) -> FastAPI:
    monkeypatch.setenv("AI_SERVER_API_KEY", TEST_API_KEY)
    monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake-or-key-not-used")
    monkeypatch.setenv("AWS_BUCKET_NAME", "test-wrapper-bucket")

    test_app = FastAPI()
    test_app.include_router(router, prefix="/ai")
    test_app.dependency_overrides[get_ai_client] = lambda: fake_ai_client
    test_app.dependency_overrides[get_storage_client] = lambda: fake_storage_client
    return test_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def bdd_context() -> _BDDContext:
    return _BDDContext()


def _payload(response: Response) -> dict[str, Any]:
    payload = response.json()
    assert isinstance(payload, dict)
    return payload


def _current_response(bdd_context: _BDDContext) -> Response:
    assert bdd_context.response is not None
    return bdd_context.response


def _body_with_attachments(bdd_context: _BDDContext) -> dict[str, object]:
    assert bdd_context.body is not None
    if bdd_context.attachments:
        bdd_context.body["attachments"] = list(bdd_context.attachments)
    return bdd_context.body


def _post_signed(
    *,
    client: TestClient,
    bdd_context: _BDDContext,
    nonce: str | None = None,
) -> Response:
    body = _body_with_attachments(bdd_context)
    encoded = encode_turn_body(body)
    headers = sign_nimbus_request(
        body=encoded,
        secret=TEST_SIGNING_SECRET,
        nonce=nonce,
    )
    return client.post("/ai/chat/turn", content=encoded, headers=headers)


def _make_attachment(filename: str, text: str) -> dict[str, object]:
    payload = text.encode("utf-8")
    return {
        "platform_file_id": f"F-{filename}",
        "filename": filename,
        "content_type": "text/plain",
        "size_bytes": len(payload),
        "content_base64": base64.b64encode(payload).decode("ascii"),
        "sha256_hex": hashlib.sha256(payload).hexdigest(),
    }


@given("the wrapper signing secret is configured")
def configure_signing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", TEST_SIGNING_SECRET)


@given(
    parsers.parse(
        'the wrapper sends a Slack message "{text}" with event id "{event_id}"'
    )
)
def wrapper_sends_message(
    bdd_context: _BDDContext, *, text: str, event_id: str
) -> None:
    event = {
        "channel": CHANNEL_ID,
        "thread_ts": THREAD_TS,
        "ts": f"{THREAD_TS[:-1]}2",
        "user": DEFAULT_USER_ID,
        "text": text,
    }
    bdd_context.body = build_message_event_turn(
        workspace_id=WORKSPACE_ID,
        event_id=event_id,
        event=event,
    )


@given(
    parsers.parse(
        'user "{user_id}" sends a Slack message "{text}" with event id "{event_id}"'
    )
)
def wrapper_sends_message_from_user(
    bdd_context: _BDDContext, *, text: str, user_id: str, event_id: str
) -> None:
    event = {
        "channel": CHANNEL_ID,
        "thread_ts": THREAD_TS,
        "ts": f"{THREAD_TS[:-1]}3",
        "user": user_id,
        "text": text,
    }
    bdd_context.body = build_message_event_turn(
        workspace_id=WORKSPACE_ID,
        event_id=event_id,
        event=event,
    )


@given(
    parsers.parse('the AI client will call the "{tool_name}" tool for "{remote_path}"')
)
def ai_client_will_call_tool(
    fake_ai_client: _FakeAIClient, *, tool_name: str, remote_path: str
) -> None:
    fake_ai_client.tool_call = (tool_name, {"remote_path": remote_path})


@given(parsers.parse('a pending delete exists for "{remote_path}"'))
def pending_delete_exists(
    client: TestClient,
    bdd_context: _BDDContext,
    *,
    remote_path: str,
) -> None:
    bdd_context.body = build_message_event_turn(
        workspace_id=WORKSPACE_ID,
        event_id=f"setup-delete-{remote_path.replace('/', '-')}",
        event={
            "channel": CHANNEL_ID,
            "thread_ts": THREAD_TS,
            "ts": THREAD_TS,
            "user": DEFAULT_USER_ID,
            "text": f"delete {remote_path}",
        },
    )
    response = _post_signed(client=client, bdd_context=bdd_context)
    assert response.status_code == 200
    assert response.json()["outcome"] == "confirmation_required"


@given(parsers.parse('the wrapper attaches file "{filename}" containing "{text}"'))
def wrapper_attaches_file(
    bdd_context: _BDDContext, *, filename: str, text: str
) -> None:
    bdd_context.attachments.append(_make_attachment(filename, text))


@given(parsers.parse('the wrapper attaches metadata-only file "{filename}"'))
def wrapper_attaches_metadata_only_file(
    bdd_context: _BDDContext, *, filename: str
) -> None:
    bdd_context.attachments.append(
        {
            "platform_file_id": f"F-{filename}",
            "filename": filename,
            "content_type": "text/plain",
            "size_bytes": 10,
        }
    )


@given(parsers.parse('uploading "{remote_path}" will fail'))
def uploading_remote_path_will_fail(
    fake_storage_client: _FakeStorageClient, *, remote_path: str
) -> None:
    fake_storage_client.upload_error_by_remote_path[remote_path] = RuntimeError(
        "backend boom"
    )


@when("the wrapper posts the signed chat turn")
def wrapper_posts_signed_turn(client: TestClient, bdd_context: _BDDContext) -> None:
    bdd_context.response = _post_signed(client=client, bdd_context=bdd_context)


@when("the wrapper posts the chat turn without signed headers")
def wrapper_posts_without_signed_headers(
    client: TestClient, bdd_context: _BDDContext
) -> None:
    encoded = encode_turn_body(_body_with_attachments(bdd_context))
    bdd_context.response = client.post(
        "/ai/chat/turn",
        content=encoded,
        headers={"Content-Type": "application/json"},
    )


@when("the wrapper posts a tampered chat turn with the original signature")
def wrapper_posts_tampered_body(client: TestClient, bdd_context: _BDDContext) -> None:
    original = _body_with_attachments(bdd_context)
    encoded_original = encode_turn_body(original)
    headers = sign_nimbus_request(
        body=encoded_original,
        secret=TEST_SIGNING_SECRET,
    )
    tampered = dict(original)
    tampered["text"] = f"{tampered['text']} after signing"
    bdd_context.response = client.post(
        "/ai/chat/turn",
        content=encode_turn_body(tampered),
        headers=headers,
    )


@when(
    parsers.parse(
        'the wrapper posts the same signed chat turn twice with nonce "{nonce}"'
    )
)
def wrapper_posts_same_signed_turn_twice(
    client: TestClient, bdd_context: _BDDContext, *, nonce: str
) -> None:
    body = _body_with_attachments(bdd_context)
    encoded = encode_turn_body(body)
    headers = sign_nimbus_request(
        body=encoded,
        secret=TEST_SIGNING_SECRET,
        nonce=nonce,
    )
    bdd_context.first_response = client.post(
        "/ai/chat/turn",
        content=encoded,
        headers=headers,
    )
    bdd_context.second_response = client.post(
        "/ai/chat/turn",
        content=encoded,
        headers=headers,
    )
    bdd_context.response = bdd_context.second_response


@then(parsers.parse("the response status is {status_code:d}"))
def response_status_is(bdd_context: _BDDContext, *, status_code: int) -> None:
    assert _current_response(bdd_context).status_code == status_code


@then(parsers.parse("the first response status is {status_code:d}"))
def first_response_status_is(bdd_context: _BDDContext, *, status_code: int) -> None:
    assert bdd_context.first_response is not None
    assert bdd_context.first_response.status_code == status_code


@then(parsers.parse("the second response status is {status_code:d}"))
def second_response_status_is(bdd_context: _BDDContext, *, status_code: int) -> None:
    assert bdd_context.second_response is not None
    assert bdd_context.second_response.status_code == status_code


@then(parsers.parse('the response outcome is "{outcome}"'))
def response_outcome_is(bdd_context: _BDDContext, *, outcome: str) -> None:
    assert _payload(_current_response(bdd_context))["outcome"] == outcome


@then(parsers.parse('the response text is "{text}"'))
def response_text_is(bdd_context: _BDDContext, *, text: str) -> None:
    assert _payload(_current_response(bdd_context))["text"] == text


@then(parsers.parse('the response text contains "{fragment}"'))
def response_text_contains(bdd_context: _BDDContext, *, fragment: str) -> None:
    assert fragment in _payload(_current_response(bdd_context))["text"]


@then(parsers.parse('the response detail contains "{fragment}"'))
def response_detail_contains(bdd_context: _BDDContext, *, fragment: str) -> None:
    assert fragment in str(_payload(_current_response(bdd_context))["detail"])


@then(parsers.parse('the second response detail contains "{fragment}"'))
def second_response_detail_contains(bdd_context: _BDDContext, *, fragment: str) -> None:
    assert bdd_context.second_response is not None
    assert fragment in str(_payload(bdd_context.second_response)["detail"])


@then("the response confirmation flag is false")
def response_confirmation_flag_is_false(bdd_context: _BDDContext) -> None:
    payload = _payload(_current_response(bdd_context))
    assert payload["confirmation_required"] is False
    assert payload["confirmation"] is None


@then("the response confirmation flag is true")
def response_confirmation_flag_is_true(bdd_context: _BDDContext) -> None:
    payload = _payload(_current_response(bdd_context))
    assert payload["confirmation_required"] is True
    assert payload["confirmation"] is not None


@then(parsers.parse('the response conversation id is "{conversation_id}"'))
def response_conversation_id_is(
    bdd_context: _BDDContext, *, conversation_id: str
) -> None:
    assert (
        _payload(_current_response(bdd_context))["conversation_id"] == conversation_id
    )


@then(parsers.parse('the response model is "{model}"'))
def response_model_is(bdd_context: _BDDContext, *, model: str) -> None:
    assert _payload(_current_response(bdd_context))["model"] == model


@then(parsers.parse('the response confirmation kind is "{kind}"'))
def response_confirmation_kind_is(bdd_context: _BDDContext, *, kind: str) -> None:
    confirmation = _payload(_current_response(bdd_context))["confirmation"]
    assert confirmation["kind"] == kind


@then(parsers.parse('the response expected confirmation reply is "{expected_reply}"'))
def response_expected_confirmation_reply_is(
    bdd_context: _BDDContext, *, expected_reply: str
) -> None:
    confirmation = _payload(_current_response(bdd_context))["confirmation"]
    assert confirmation["expected_reply"] == expected_reply


@then("the AI client received the turn with the full storage tool surface")
def ai_client_received_turn_with_full_tools(
    fake_ai_client: _FakeAIClient,
) -> None:
    # Wrapper exposes destructive tools too (delete/move). Approval enforcement
    # happens downstream in the runtime, not by hiding tools.
    assert fake_ai_client.calls
    tools = fake_ai_client.calls[-1]["tools"]
    assert isinstance(tools, list)
    assert {tool.name for tool in tools} == {
        "list_files",
        "get_file_info",
        "read_file",
        "delete_file",
        "copy_file",
        "move_file",
        "write_file",
    }


@then(parsers.parse('the storage client recorded an info lookup for "{remote_path}"'))
def storage_recorded_info_lookup(
    fake_storage_client: _FakeStorageClient, *, remote_path: str
) -> None:
    assert fake_storage_client.infos == [
        {"container": "test-wrapper-bucket", "object_name": remote_path}
    ]


@then("the storage client has not deleted any files")
def storage_has_not_deleted_files(fake_storage_client: _FakeStorageClient) -> None:
    assert fake_storage_client.deletes == []


@then(parsers.parse('the storage client deleted "{remote_path}"'))
def storage_deleted_remote_path(
    fake_storage_client: _FakeStorageClient, *, remote_path: str
) -> None:
    assert fake_storage_client.deletes == [
        {"container": "test-wrapper-bucket", "object_name": remote_path}
    ]


@then(parsers.parse('the storage client uploaded "{remote_path}"'))
def storage_uploaded_remote_path(
    fake_storage_client: _FakeStorageClient, *, remote_path: str
) -> None:
    assert any(
        upload["remote_path"] == remote_path for upload in fake_storage_client.uploads
    )


@then("the storage client has not uploaded any files")
def storage_has_not_uploaded_files(fake_storage_client: _FakeStorageClient) -> None:
    assert fake_storage_client.uploads == []
