"""HW3 integration tests for Nimbus AI-to-storage orchestration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, cast

import ai_server.auth as auth_mod
import ai_server.router as router_mod
import nimbus_runtime.runtime as runtime_mod
import pytest
from ai_server.feature_flags import provider_from_env
from ai_server.router import get_ai_client, get_storage_client, router
from ai_server.wrapper_client import (
    build_message_event_turn,
    encode_turn_body,
    sign_nimbus_request,
)
from cloud_storage_api import ObjectInfo
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_client_api import (
    AIClient,
    AIResponse,
    AIStreamEvent,
    Conversation,
    EventListener,
    TokenUsage,
    Tool,
    ToolCallRecord,
)
from nimbus_runtime import runtime_telemetry

pytestmark = pytest.mark.integration

_TEST_API_KEY = "integration-api-key"
_TEST_SIGNING_SECRET = "integration-signing-secret"
_TEST_CONTAINER = "integration-bucket"


class _ToolLike(Protocol):
    name: str
    handler: Callable[..., object]


@dataclass
class _RecordingStorageClient:
    """Storage fake that records the cross-vertical operation."""

    infos: list[dict[str, str]] = field(default_factory=list)

    def list_files(self, *, container: str, prefix: str = "") -> list[ObjectInfo]:
        self.infos.append({"container": container, "object_name": f"{prefix}index"})
        return [ObjectInfo(object_name=f"{prefix}april.csv", size_bytes=42)]

    def get_file_info(self, *, container: str, object_name: str) -> ObjectInfo:
        self.infos.append({"container": container, "object_name": object_name})
        return ObjectInfo(object_name=object_name, size_bytes=42)


class _ToolCallingAIClient(AIClient):
    """AI fake that invokes the runtime-provided storage tool."""

    def send_message(
        self,
        prompt: str | Conversation,
        *,
        tools: Sequence[Tool] | None = None,
        max_steps: int = 5,
        dry_run: bool = False,
        stream: bool = False,
    ) -> AIResponse:
        del prompt, max_steps, dry_run, stream
        assert tools is not None
        typed_tools = [cast("_ToolLike", tool) for tool in tools]
        tool = next(tool for tool in typed_tools if tool.name == "get_file_info")
        arguments = {"remote_path": "reports/april.csv"}
        result = tool.handler(**arguments)
        assert isinstance(result, dict)
        object_name = result.get("object_name")
        assert isinstance(object_name, str)
        return AIResponse(
            text=f"Storage says {object_name}",
            model="integration/fake-ai",
            tokens=TokenUsage(input_tokens=1, output_tokens=1),
            tool_calls=(
                ToolCallRecord(
                    id="tool-1",
                    name="get_file_info",
                    arguments=arguments,
                    result_summary=str(result),
                    success=True,
                    latency_ms=0,
                ),
            ),
            latency_ms=1,
            stop_reason="end_turn",
            steps=1,
        )

    async def stream_message(
        self,
        prompt: str | Conversation,
        *,
        tools: Sequence[Tool] | None = None,
        max_steps: int = 5,
        dry_run: bool = False,
    ) -> AsyncIterator[AIStreamEvent]:
        del prompt, tools, max_steps, dry_run
        yield AIStreamEvent(
            kind="request_completed",
            sequence=1,
            payload={
                "response": AIResponse(
                    text="streaming is not used in this integration test",
                    model="integration/fake-ai",
                    tokens=TokenUsage(input_tokens=0, output_tokens=0),
                    tool_calls=(),
                    latency_ms=0,
                    stop_reason="end_turn",
                    steps=1,
                )
            },
        )

    def ping(self) -> bool:
        return True

    def on_event(self, listener: EventListener) -> None:
        del listener


@pytest.mark.circleci
def test_slack_turn_ai_tool_call_reaches_storage_and_records_telemetry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A signed wrapper turn can drive an AI-selected storage tool call."""
    auth_mod._seen_nonces.clear()
    router_mod._idempotent_turns.clear()
    router_mod._rate_buckets.clear()
    runtime_mod._session_locks.clear()
    provider_from_env.cache_clear()
    runtime_telemetry.reset()

    session_dir = tmp_path / "sessions"
    monkeypatch.setenv("AI_SERVER_API_KEY", _TEST_API_KEY)
    monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", _TEST_SIGNING_SECRET)
    monkeypatch.setenv("AI_SESSION_DIR", str(session_dir))
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-used")
    monkeypatch.setenv("AWS_BUCKET_NAME", _TEST_CONTAINER)
    monkeypatch.setenv("NIMBUS_STATE_BACKEND", "file")
    monkeypatch.setenv("NIMBUS_FLAG_MODEL_TURNS_ENABLED", "true")
    monkeypatch.setenv("NIMBUS_FLAG_STORAGE_TOOLS_ENABLED", "true")

    storage = _RecordingStorageClient()
    app = FastAPI()
    app.include_router(router, prefix="/ai")
    app.dependency_overrides[get_ai_client] = _ToolCallingAIClient
    app.dependency_overrides[get_storage_client] = lambda: storage

    body = build_message_event_turn(
        workspace_id="T123TEAM",
        event_id="evt-hw3-cross-vertical",
        event={
            "channel": "C123CHAN",
            "thread_ts": "1713840000.000001",
            "ts": "1713840000.000002",
            "user": "U123USER",
            "text": "what do we know about reports/april.csv?",
        },
    )
    encoded_body = encode_turn_body(body)
    headers = sign_nimbus_request(
        body=encoded_body,
        secret=_TEST_SIGNING_SECRET,
        nonce="nonce-hw3-cross-vertical",
    )

    response = TestClient(app).post(
        "/ai/chat/turn",
        content=encoded_body,
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "reply"
    assert payload["text"] == "Storage says reports/april.csv"
    assert storage.infos == [
        {"container": _TEST_CONTAINER, "object_name": "reports/april.csv"}
    ]
    assert (
        runtime_telemetry.snapshot()["counters"][
            "nimbus_ai_tool_calls_total|success=true,tool_name=get_file_info"
        ]
        == 1
    )
