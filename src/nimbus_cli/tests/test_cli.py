"""Tests for the Nimbus CLI command surface."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nimbus_cli.cli import app
from nimbus_runtime.models import ChatTurnInput
from typer.testing import CliRunner

from nimbus_protocol import NimbusEvent, StreamEventType

pytestmark = pytest.mark.unit

_runner = CliRunner()


@dataclass
class _FakeRuntime:
    turns: list[ChatTurnInput] = field(default_factory=list)

    async def stream_chat_turn(self, turn: ChatTurnInput) -> AsyncIterator[NimbusEvent]:
        self.turns.append(turn)
        yield NimbusEvent(
            session_id=turn.conversation_id,
            sequence=1,
            event_id="evt-1",
            event_type=StreamEventType.TURN_STARTED.value,
            payload={"request_id": turn.request_id},
            turn_id=turn.request_id,
        )
        yield NimbusEvent(
            session_id=turn.conversation_id,
            sequence=2,
            event_id="evt-2",
            event_type=StreamEventType.TEXT_DELTA.value,
            payload={"delta": "hello from local"},
            turn_id=turn.request_id,
        )
        yield NimbusEvent(
            session_id=turn.conversation_id,
            sequence=3,
            event_id="evt-3",
            event_type=StreamEventType.TURN_COMPLETED.value,
            payload={"response": {"text": "hello from local"}},
            turn_id=turn.request_id,
        )


def test_local_chat_defaults_new_session_and_resume_uses_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default chat creates a new session; resume reuses it explicitly."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    fake_runtime = _FakeRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    setup = _runner.invoke(
        app,
        ["setup", "local", "--openrouter-key", "sk-test"],
    )
    first = _runner.invoke(app, ["chat", "hello"])
    second = _runner.invoke(app, ["resume", "again"])

    assert setup.exit_code == 0
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "hello from local" in first.output
    assert len(fake_runtime.turns) == 2
    assert (
        fake_runtime.turns[0].conversation_id == fake_runtime.turns[1].conversation_id
    )


def test_remote_hmac_profile_posts_signed_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote HMAC profiles should sign canonical chat-turn requests."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    captured: dict[str, object] = {}

    class _Response:
        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {"text": "remote ok"}

    def _fake_post(url: str, **kwargs: object) -> _Response:
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr("nimbus_cli.cli.httpx.post", _fake_post)

    setup = _runner.invoke(
        app,
        [
            "setup",
            "remote",
            "--profile",
            "prod",
            "--base-url",
            "https://nimbus.example",
            "--auth",
            "hmac",
            "--signing-secret",
            "secret",
        ],
    )
    result = _runner.invoke(app, ["chat", "hello", "--profile", "prod"])

    assert setup.exit_code == 0
    assert result.exit_code == 0
    assert "remote ok" in result.output
    assert captured["url"] == "https://nimbus.example/ai/chat/turn"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "X-Nimbus-Signature" in headers
