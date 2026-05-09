"""Deterministic Nimbus runtime eval harness."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nimbus_runtime.models import ChatTurnInput, ChatTurnResult, TurnAttachment

from ai_client_api import AIResponse, AIStreamEvent, TokenUsage
from nimbus_runtime import NimbusRuntime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from nimbus_protocol import NimbusEvent


def _fake_response(text: str = "Safe deterministic reply.") -> AIResponse:
    """Return a deterministic AI response for eval harness cases."""
    return AIResponse(
        text=text,
        model="eval-model",
        tokens=TokenUsage(input_tokens=3, output_tokens=5),
        tool_calls=(),
        latency_ms=1,
        stop_reason="end_turn",
        steps=1,
        fallback_used=False,
    )


class FakeEvalAIClient:
    """AI client fake with synchronous and streaming responses."""

    def __init__(self, response: AIResponse | None = None) -> None:
        """Create the fake with an optional fixed response."""
        self.response = response or _fake_response()

    def send_message(self, *_args: object, **_kwargs: object) -> AIResponse:
        """Return the configured response."""
        return self.response

    async def stream_message(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> AsyncIterator[AIStreamEvent]:
        """Yield deterministic provider stream events."""
        yield AIStreamEvent(kind="text_delta", sequence=1, payload={"delta": "Safe "})
        yield AIStreamEvent(
            kind="text_delta",
            sequence=2,
            payload={"delta": "deterministic reply."},
        )
        yield AIStreamEvent(
            kind="request_completed",
            sequence=3,
            payload={"response": self.response},
        )

    def on_event(self, _listener: object) -> None:
        """Accept listener registration for protocol compatibility."""

    def ping(self) -> bool:
        """Return provider reachability."""
        return True


@dataclass(slots=True)
class FakeEvalStorage:
    """Storage fake that records destructive side effects."""

    deletes: list[str] = field(default_factory=list)
    uploads: list[str] = field(default_factory=list)

    def list_files(self, *, container: str, prefix: str = "") -> list[object]:
        """Return an empty listing."""
        del container, prefix
        return []

    def get_file_info(self, *, container: str, object_name: str) -> object:
        """Return a tiny object-info stand-in."""
        del container
        return type("ObjectInfo", (), {"object_name": object_name, "size_bytes": 1})()

    def delete_file(self, *, container: str, object_name: str) -> object:
        """Record a delete side effect."""
        del container
        self.deletes.append(object_name)
        return type("DeleteResult", (), {"deleted": True, "version_id": None})()

    def upload_file(
        self,
        *,
        container: str,
        local_path: str,
        remote_path: str,
    ) -> object:
        """Record an upload side effect."""
        del container, local_path
        self.uploads.append(remote_path)
        return type("ObjectInfo", (), {"object_name": remote_path})()


@dataclass(slots=True)
class RuntimeEvalHarness:
    """One-process harness for Nimbus runtime evals."""

    session_dir: Path
    ai_client: FakeEvalAIClient = field(default_factory=FakeEvalAIClient)
    storage: FakeEvalStorage = field(default_factory=FakeEvalStorage)

    def runtime(self) -> NimbusRuntime:
        """Build a fresh runtime over shared fake dependencies."""
        return NimbusRuntime(
            ai_client=self.ai_client,
            storage=self.storage,
            session_dir=self.session_dir,
            system_prompt="eval system prompt",
            tool_container="eval-bucket",
        )

    def turn(
        self,
        text: str,
        *,
        user_id: str = "U-EVAL",
        request_id: str = "req-eval",
        attachments: tuple[TurnAttachment, ...] = (),
    ) -> ChatTurnInput:
        """Build a deterministic eval turn."""
        return ChatTurnInput(
            request_id=request_id,
            conversation_id="slack:T-EVAL:C-EVAL:thread-1",
            platform="slack",
            workspace_id="T-EVAL",
            channel_id="C-EVAL",
            thread_id="thread-1",
            message_id=f"{request_id}-message",
            user_id=user_id,
            text=text,
            attachments=attachments,
        )

    def run_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
        """Run a non-streaming runtime turn."""
        return asyncio.run(self.runtime().run_chat_turn(turn))

    def stream_turn(self, turn: ChatTurnInput) -> tuple[NimbusEvent, ...]:
        """Run and collect one streaming runtime turn."""
        return asyncio.run(self._collect_stream(turn))

    def replay(
        self,
        *,
        session_id: str,
        after_sequence: int | None = None,
    ) -> tuple[NimbusEvent, ...]:
        """Replay stored events for the eval tenant."""
        return self.runtime().replay_events(
            platform="slack",
            workspace_id="T-EVAL",
            session_id=session_id,
            after_sequence=after_sequence,
        )

    async def _collect_stream(self, turn: ChatTurnInput) -> tuple[NimbusEvent, ...]:
        runtime = self.runtime()
        return tuple([event async for event in runtime.stream_chat_turn(turn)])
