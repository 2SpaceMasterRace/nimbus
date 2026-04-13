"""Unit tests for the shared Nimbus runtime layer."""

from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nimbus_runtime.models import ChatTurnInput, ChatTurnResult, TurnAttachment

from ai_client_api import AIResponse, TokenUsage
from nimbus_runtime import NimbusRuntime, runtime_telemetry

pytestmark = pytest.mark.unit


def _fake_response(text: str = "Hello from Nimbus!") -> AIResponse:
    return AIResponse(
        text=text,
        model="test-model:free",
        tokens=TokenUsage(input_tokens=8, output_tokens=13),
        tool_calls=(),
        latency_ms=42,
        stop_reason="end_turn",
        steps=1,
        fallback_used=False,
    )


class FakeAIClient:
    """Minimal AI client stand-in for runtime tests."""

    def __init__(self, response: AIResponse | None = None) -> None:
        self._response = response or _fake_response()
        self.calls: list[dict[str, object]] = []

    def send_message(
        self,
        conv: object,
        *,
        tools: object | None = None,
        **_kwargs: object,
    ) -> AIResponse:
        self.calls.append({"conv": conv, "tools": tools})
        return self._response

    def on_event(self, _listener: object) -> None:
        pass

    def ping(self) -> bool:
        return True


@dataclass
class _ObjectInfo:
    object_name: str
    size_bytes: int | None = None
    version_id: str | None = None
    updated_at: str | None = None


@dataclass
class _DeleteResult:
    deleted: bool = True
    version_id: str | None = None


@dataclass
class FakeStorageClient:
    uploads: list[dict[str, str]] = field(default_factory=list)
    deletes: list[dict[str, str]] = field(default_factory=list)

    def list_files(self, *, container: str, prefix: str = "") -> list[_ObjectInfo]:
        del container, prefix
        return []

    def get_file_info(self, *, container: str, object_name: str) -> _ObjectInfo:
        del container
        return _ObjectInfo(object_name=object_name, size_bytes=10)

    def delete_file(self, *, container: str, object_name: str) -> _DeleteResult:
        self.deletes.append({"container": container, "object_name": object_name})
        return _DeleteResult(deleted=True)

    def upload_file(
        self,
        *,
        container: str,
        local_path: str,
        remote_path: str,
    ) -> _ObjectInfo:
        self.uploads.append(
            {
                "container": container,
                "local_path": local_path,
                "remote_path": remote_path,
            }
        )
        return _ObjectInfo(object_name=remote_path)


def _turn(
    *,
    text: str,
    attachments: tuple[TurnAttachment, ...] = (),
    user_id: str = "U123USER",
    request_id: str = "req-123",
) -> ChatTurnInput:
    return ChatTurnInput(
        request_id=request_id,
        conversation_id="slack:T123TEAM:C123CHAN:1713840000.123456",
        platform="slack",
        workspace_id="T123TEAM",
        channel_id="C123CHAN",
        thread_id="1713840000.123456",
        message_id="1713840000.123457",
        user_id=user_id,
        text=text,
        attachments=attachments,
    )


def _inline_attachment(filename: str, text: str) -> TurnAttachment:
    payload = text.encode("utf-8")
    return TurnAttachment(
        platform_file_id=f"F-{filename}",
        filename=filename,
        content_type="text/plain",
        size_bytes=len(payload),
        content_base64=base64.b64encode(payload).decode("ascii"),
        sha256_hex=hashlib.sha256(payload).hexdigest(),
    )


@pytest.fixture(autouse=True)
def _reset_runtime_metrics() -> None:
    runtime_telemetry.reset()


def _run_turn(runtime: NimbusRuntime, turn: ChatTurnInput) -> ChatTurnResult:
    return asyncio.run(runtime.run_chat_turn(turn))


def test_runtime_uses_ai_path_for_normal_turns(tmp_path: Path) -> None:
    """Normal wrapper turns should still go through the AI path."""
    ai_client = FakeAIClient()
    runtime = NimbusRuntime(
        ai_client=ai_client,
        storage=FakeStorageClient(),
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    result = _run_turn(runtime, _turn(text="What files are under reports/?"))

    assert result.outcome == "reply"
    assert result.model == "test-model:free"
    tools = ai_client.calls[-1]["tools"]
    assert isinstance(tools, list)
    assert {tool.name for tool in tools} == {"list_files", "get_file_info"}


def test_runtime_returns_confirmation_required_for_delete_then_confirms(
    tmp_path: Path,
) -> None:
    """Delete requests should become explicit confirmation flows."""
    storage = FakeStorageClient()
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=storage,
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    first = _run_turn(runtime, _turn(text="delete reports/2024/old.csv"))
    second = _run_turn(
        runtime,
        _turn(text="yes, delete reports/2024/old.csv", request_id="req-456"),
    )

    assert first.outcome == "confirmation_required"
    assert first.confirmation is not None
    assert second.outcome == "reply"
    assert storage.deletes == [
        {"container": "bucket", "object_name": "reports/2024/old.csv"}
    ]


def test_runtime_rejects_confirmation_from_different_actor(tmp_path: Path) -> None:
    """Only the original actor should be able to confirm a delete."""
    storage = FakeStorageClient()
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=storage,
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    _run_turn(runtime, _turn(text="delete reports/2024/old.csv"))
    result = _run_turn(
        runtime,
        _turn(
            text="yes, delete reports/2024/old.csv",
            user_id="U999OTHER",
            request_id="req-789",
        ),
    )

    assert result.outcome == "error"
    assert storage.deletes == []


def test_runtime_ingests_attachment_bytes_for_upload_turns(tmp_path: Path) -> None:
    """Upload-style turns should ingest inline bytes through storage."""
    storage = FakeStorageClient()
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=storage,
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    result = _run_turn(
        runtime,
        _turn(
            text="upload these files to finance/april",
            attachments=(_inline_attachment("report.txt", "quarterly report"),),
        ),
    )

    assert result.outcome == "reply"
    assert storage.uploads[0]["remote_path"] == "finance/april/report.txt"


def test_runtime_records_wrapper_and_ai_metrics(tmp_path: Path) -> None:
    """Successful AI-backed turns should emit wrapper and AI metrics."""
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=FakeStorageClient(),
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    _run_turn(runtime, _turn(text="What files are under reports/?"))
    snapshot = runtime_telemetry.snapshot()
    ai_metric_key = (
        "nimbus_ai_requests_total|fallback_used=false,model=test-model:free,"
        "result=success,stop_reason=end_turn"
    )

    assert (
        snapshot["counters"]["nimbus_wrapper_turns_total|outcome=reply,platform=slack"]
        == 1
    )
    assert snapshot["counters"][ai_metric_key] == 1


@pytest.mark.regression
def test_attachment_upload_does_not_leak_temp_file_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bug #3 regression for temp-file cleanup on attachment write failure.

    The previous implementation kept the cleanup ``try/finally`` *outside* the
    ``with tempfile.NamedTemporaryFile(..., delete=False)`` block.  If
    ``handle.write(payload)`` itself failed (e.g. ENOSPC mid-write), the
    ``with`` block exited — closing but not deleting the file because
    ``delete=False`` — and the cleanup branch was never entered, leaving a
    leftover ``nimbus-attachment-*`` file under ``_attachment_ingestion/``.

    The fix wraps both the write and the upload in one ``try/finally``.
    """
    import tempfile as stdlib_tempfile

    real_factory = stdlib_tempfile.NamedTemporaryFile

    class _WriteFailingHandle:
        """Wrapper that simulates a mid-write OSError without touching disk twice."""

        def __init__(self, real_handle: object) -> None:
            self._real = real_handle

        @property
        def name(self) -> str:
            return str(self._real.name)  # type: ignore[attr-defined]

        def write(self, _data: bytes) -> int:
            msg = "simulated mid-write disk failure"
            raise OSError(msg)

        def close(self) -> None:
            self._real.close()  # type: ignore[attr-defined]

    def _patched_factory(**kwargs: object) -> _WriteFailingHandle:
        return _WriteFailingHandle(real_factory(**kwargs))  # type: ignore[arg-type]

    import nimbus_runtime.runtime as runtime_mod

    monkeypatch.setattr(runtime_mod.tempfile, "NamedTemporaryFile", _patched_factory)

    storage = FakeStorageClient()
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=storage,
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    result = _run_turn(
        runtime,
        _turn(
            text="upload these files to finance/april",
            attachments=(_inline_attachment("report.txt", "quarterly report"),),
        ),
    )

    assert result.outcome == "error"
    # Storage was never called because the write failed before upload.
    assert storage.uploads == []
    # The scratch directory exists (we created it) but holds no leftover files.
    scratch = tmp_path / "sessions" / "_attachment_ingestion"
    assert scratch.is_dir()
    leftover = sorted(p.name for p in scratch.iterdir() if p.is_file())
    assert leftover == [], f"temp files leaked on write failure: {leftover}"


def test_session_locks_do_not_accumulate_after_last_reference_is_dropped() -> None:
    """Per-session lock bookkeeping should not grow without bound over time."""
    import nimbus_runtime.runtime as runtime_mod

    lock = runtime_mod.get_session_lock("slack:T123TEAM:C123CHAN:session")

    assert "slack:T123TEAM:C123CHAN:session" in runtime_mod._session_locks  # noqa: SLF001

    del lock
    gc.collect()

    assert "slack:T123TEAM:C123CHAN:session" not in runtime_mod._session_locks  # noqa: SLF001
