"""ABC contract tests for :class:`ai_client_api.AIClient`.

These tests only verify the shape of the abstract class — they don't spin up
any HTTP server. Concrete implementations have their own mocked-HTTP tests in
their own packages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ai_client_api import (
    AgentEvent,
    AIClient,
    AIResponse,
    Conversation,
    EventListener,
    TokenUsage,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ai_client_api import Tool

pytestmark = pytest.mark.unit


def test_aiclient_cannot_be_instantiated_directly() -> None:
    """Abstract ``AIClient`` must not be instantiable."""
    with pytest.raises(TypeError):
        AIClient()  # type: ignore[abstract]


def test_partial_subclass_cannot_be_instantiated() -> None:
    """A subclass missing any abstract method must not be instantiable."""

    class MissingSendMessage(AIClient):
        def ping(self) -> bool:
            return True

        def on_event(self, listener: EventListener) -> None:
            del listener

    with pytest.raises(TypeError):
        MissingSendMessage()  # type: ignore[abstract]


def test_full_subclass_is_instantiable() -> None:
    """A concrete subclass implementing every abstract method works."""

    class FakeClient(AIClient):
        """Minimal in-memory client used only for contract verification."""

        def __init__(self) -> None:
            self._events: list[AgentEvent] = []

        def send_message(
            self,
            prompt: str | Conversation,
            *,
            tools: Sequence[Tool] | None = None,
            max_steps: int = 5,
            dry_run: bool = False,
            stream: bool = False,
        ) -> AIResponse:
            del prompt, tools, max_steps, dry_run, stream
            return AIResponse(
                text="pong",
                model="fake/model",
                tokens=TokenUsage(input_tokens=1, output_tokens=1),
                tool_calls=(),
                latency_ms=0,
                stop_reason="end_turn",
                steps=1,
                fallback_used=False,
            )

        def ping(self) -> bool:
            return True

        def on_event(self, listener: EventListener) -> None:
            del listener

    client = FakeClient()
    resp = client.send_message("hi")
    assert resp.text == "pong"
    assert client.ping() is True
