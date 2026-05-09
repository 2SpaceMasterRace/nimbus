"""Unit tests for the shared Nimbus runtime layer."""

from __future__ import annotations

import asyncio
import base64
import gc
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from nimbus_runtime.models import ChatTurnInput, ChatTurnResult, TurnAttachment

from ai_client_api import AIResponse, AIStreamEvent, TokenUsage
from nimbus_protocol import NimbusEvent, StreamEventType
from nimbus_runtime import (
    ActionKind,
    ActionStatus,
    ApprovalStatus,
    DeleteFileResult,
    FileActionStore,
    FileApprovalStore,
    FilePlanStore,
    FileSessionEventStore,
    NimbusRuntime,
    PlanStatus,
    PolicyDecision,
    TenantIdentity,
    UploadAttachmentResult,
    runtime_telemetry,
)

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

    async def stream_message(
        self,
        conv: object,
        *,
        tools: object | None = None,
        **_kwargs: object,
    ) -> AsyncIterator[AIStreamEvent]:
        self.calls.append({"conv": conv, "tools": tools, "stream": True})
        yield AIStreamEvent(
            kind="request_started",
            sequence=1,
            payload={
                "request_id": "provider-req-123",
                "session_id": getattr(conv, "session_id", None),
            },
        )
        yield AIStreamEvent(
            kind="text_delta",
            sequence=2,
            payload={"delta": "Hello "},
        )
        yield AIStreamEvent(
            kind="text_delta",
            sequence=3,
            payload={"delta": "from Nimbus!"},
        )
        yield AIStreamEvent(
            kind="text_completed",
            sequence=4,
            payload={"text": self._response.text},
        )
        yield AIStreamEvent(
            kind="request_completed",
            sequence=5,
            payload={"response": self._response, "model": self._response.model},
        )

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
    infos: list[dict[str, str]] = field(default_factory=list)
    deletes: list[dict[str, str]] = field(default_factory=list)
    info_return: _ObjectInfo = field(
        default_factory=lambda: _ObjectInfo(object_name="reports/q1.csv", size_bytes=10)
    )
    delete_result: _DeleteResult | dict[str, object] = field(
        default_factory=_DeleteResult
    )

    def list_files(self, *, container: str, prefix: str = "") -> list[_ObjectInfo]:
        del container, prefix
        return []

    def get_file_info(self, *, container: str, object_name: str) -> _ObjectInfo:
        self.infos.append({"container": container, "object_name": object_name})
        return _ObjectInfo(
            object_name=object_name,
            size_bytes=self.info_return.size_bytes,
            version_id=self.info_return.version_id,
            updated_at=self.info_return.updated_at,
        )

    def delete_file(
        self, *, container: str, object_name: str
    ) -> _DeleteResult | dict[str, object]:
        self.deletes.append({"container": container, "object_name": object_name})
        return self.delete_result

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


async def _collect_stream(
    runtime: NimbusRuntime,
    turn: ChatTurnInput,
) -> list[NimbusEvent]:
    return [event async for event in runtime.stream_chat_turn(turn)]


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
    assert {tool.name for tool in tools} == {
        "list_files",
        "get_file_info",
        "read_file",
        "delete_file",
        "copy_file",
        "move_file",
        "write_file",
    }


def test_runtime_uses_same_model_tools_for_cli_and_slack(tmp_path: Path) -> None:
    """CLI and Slack model-backed turns should receive the same tool surface."""
    ai_client = FakeAIClient()
    runtime = NimbusRuntime(
        ai_client=ai_client,
        storage=FakeStorageClient(),
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )
    slack_turn = _turn(text="What files are under reports/?", request_id="req-slack")
    cli_turn = ChatTurnInput(
        request_id="req-cli",
        conversation_id="cli:local:session-1",
        platform="cli",
        workspace_id="local",
        channel_id="terminal",
        thread_id="session-1",
        message_id="msg-cli-1",
        user_id="local-user",
        text="What files are under reports/?",
    )

    _run_turn(runtime, slack_turn)
    _run_turn(runtime, cli_turn)

    observed = []
    for call in ai_client.calls:
        tools = call["tools"]
        assert isinstance(tools, list)
        observed.append({tool.name for tool in tools})
    full_tool_set = {
        "list_files",
        "get_file_info",
        "read_file",
        "delete_file",
        "copy_file",
        "move_file",
        "write_file",
    }
    assert observed == [full_tool_set, full_tool_set]


def test_runtime_streams_and_replays_provider_events(tmp_path: Path) -> None:
    """Streaming turns should be durable and replayable from a sequence cursor."""
    ai_client = FakeAIClient()
    runtime = NimbusRuntime(
        ai_client=ai_client,
        storage=FakeStorageClient(),
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    turn = _turn(text="Stream a greeting")
    events = asyncio.run(_collect_stream(runtime, turn))
    replayed_tail = runtime.replay_events(
        platform="slack",
        workspace_id="T123TEAM",
        session_id=turn.conversation_id,
        after_sequence=2,
    )

    assert [event.event_type for event in events] == [
        StreamEventType.TURN_STARTED.value,
        StreamEventType.PROVIDER_REQUEST_STARTED.value,
        StreamEventType.TEXT_DELTA.value,
        StreamEventType.TEXT_DELTA.value,
        StreamEventType.TEXT_COMPLETED.value,
        StreamEventType.TURN_COMPLETED.value,
    ]
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5, 6]
    assert events[-1].payload["response"] == {
        "text": "Hello from Nimbus!",
        "model": "test-model:free",
        "tokens": {"input_tokens": 8, "output_tokens": 13, "total": 21},
        "tool_calls": [],
        "latency_ms": 42,
        "stop_reason": "end_turn",
        "steps": 1,
        "fallback_used": False,
    }
    assert [event.event_id for event in replayed_tail] == [
        event.event_id for event in events[2:]
    ]
    assert replayed_tail[0].turn_id == "req-123"


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
    assert first.actions[0].status == ActionStatus.AWAITING_CONFIRMATION.value
    assert second.outcome == "reply"
    assert second.actions[0].status == ActionStatus.SUCCEEDED.value
    assert storage.deletes == [
        {"container": "bucket", "object_name": "reports/2024/old.csv"}
    ]


def test_runtime_preserves_dict_backed_delete_result_fields(tmp_path: Path) -> None:
    """Delete reports should reflect the dict-backed cloud-storage contract."""
    storage = FakeStorageClient(
        delete_result={"deleted": False, "version_id": "v-delete-marker"}
    )
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=storage,
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    _run_turn(runtime, _turn(text="delete reports/2024/missing.csv"))
    result = _run_turn(
        runtime,
        _turn(text="yes, delete reports/2024/missing.csv", request_id="req-456"),
    )

    assert result.text == "No file was deleted for `reports/2024/missing.csv`."
    payload = result.artifacts[0].payload
    assert payload is not None
    assert payload["remote_path"] == "reports/2024/missing.csv"
    assert payload["deleted"] is False
    assert payload["version_id"] == "v-delete-marker"
    restore_plan = payload["restore_plan"]
    assert isinstance(restore_plan, dict)
    assert restore_plan["strategy"] == "not_required"
    assert restore_plan["restorable"] is True
    assert restore_plan["version_id"] == "v-delete-marker"


def test_runtime_records_delete_actions_and_events(tmp_path: Path) -> None:
    """Delete confirmations should be backed by the action/event stores."""
    storage = FakeStorageClient()
    session_dir = tmp_path / "sessions"
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=storage,
        session_dir=session_dir,
        system_prompt="sys",
        tool_container="bucket",
    )

    _run_turn(runtime, _turn(text="delete reports/2024/old.csv"))
    result = _run_turn(
        runtime,
        _turn(text="yes, delete reports/2024/old.csv", request_id="req-456"),
    )

    tenant = TenantIdentity(platform="slack", workspace_id="T123TEAM")
    action_store = FileActionStore(session_dir)
    [action] = action_store.list_for_session(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:1713840000.123456",
    )
    assert action.kind is ActionKind.DELETE_FILE
    assert action.status is ActionStatus.SUCCEEDED
    assert action.result is not None
    assert isinstance(action.result, DeleteFileResult)
    assert action.result.remote_path == "reports/2024/old.csv"
    assert action.result.deleted is True
    assert action.result.version_id is None
    assert isinstance(action.result.artifact_id, str)
    assert action.policy_decision is not None
    assert action.policy_decision.decision is PolicyDecision.REQUIRES_APPROVAL
    [plan] = FilePlanStore(session_dir).list_for_session(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:1713840000.123456",
    )
    event_store = FileSessionEventStore(session_dir)
    events = event_store.list_events(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:1713840000.123456",
    )
    approval_id = events[2].payload["approval_id"]
    assert isinstance(approval_id, str)
    approval = FileApprovalStore(session_dir).get(
        tenant=tenant,
        approval_id=approval_id,
    )
    assert plan.status is PlanStatus.APPLIED
    assert plan.action_id == action.action_id
    assert approval is not None
    assert approval.action_id == action.action_id
    assert approval.plan_id == plan.plan_id
    assert approval.exact_target == "reports/2024/old.csv"
    report = result.artifacts[0].payload
    assert report is not None
    restore_plan = report["restore_plan"]
    assert isinstance(restore_plan, dict)
    assert restore_plan["strategy"] == "unavailable"
    assert restore_plan["restorable"] is False
    assert "copy-to-trash" in " ".join(restore_plan["limitations"])

    assert [event.event_type for event in events] == [
        "action_created",
        "plan_created",
        "approval_requested",
        "approval_decided",
        "plan_approved",
        "action_authorized",
        "action_started",
        "verification_started",
        "artifact_created",
        "artifact_created",
        "action_completed",
        "plan_applied",
    ]


def test_runtime_delete_report_uses_pre_delete_version_for_restore(
    tmp_path: Path,
) -> None:
    """Versioned object metadata should become a restorable delete receipt."""
    storage = FakeStorageClient(
        info_return=_ObjectInfo(
            object_name="reports/2024/old.csv",
            size_bytes=123,
            version_id="v-source",
        )
    )
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
        _turn(text="yes, delete reports/2024/old.csv", request_id="req-456"),
    )

    payload = result.artifacts[0].payload
    assert payload is not None
    restore_plan = payload["restore_plan"]
    assert isinstance(restore_plan, dict)
    assert restore_plan["strategy"] == "s3_version"
    assert restore_plan["restorable"] is True
    assert restore_plan["version_id"] == "v-source"
    assert restore_plan["size_bytes"] == 123
    assert "v-source" in str(restore_plan["restore_command"])
    assert storage.infos == [
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
    tenant = TenantIdentity(platform="slack", workspace_id="T123TEAM")
    session_id = "slack:T123TEAM:C123CHAN:1713840000.123456"
    [action] = FileActionStore(tmp_path / "sessions").list_for_session(
        tenant=tenant,
        session_id=session_id,
    )
    approval = FileApprovalStore(tmp_path / "sessions").find_pending_for_action(
        tenant=tenant,
        action_id=action.action_id,
    )
    assert approval is not None
    assert approval.status is ApprovalStatus.PENDING
    events = FileSessionEventStore(tmp_path / "sessions").list_events(
        tenant=tenant,
        session_id=session_id,
    )
    assert events[-1].event_type == "approval_decision_failed"
    assert events[-1].payload["reason"] == "wrong_actor"


def test_runtime_rejects_confirmation_for_different_target(tmp_path: Path) -> None:
    """The confirmed delete target must match the planned exact target."""
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
            text="yes, delete reports/2024/other.csv",
            request_id="req-789",
        ),
    )

    assert result.outcome == "error"
    assert storage.deletes == []
    tenant = TenantIdentity(platform="slack", workspace_id="T123TEAM")
    session_id = "slack:T123TEAM:C123CHAN:1713840000.123456"
    events = FileSessionEventStore(tmp_path / "sessions").list_events(
        tenant=tenant,
        session_id=session_id,
    )
    assert events[-1].event_type == "approval_decision_failed"
    assert events[-1].payload["reason"] == "target_mismatch"


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
    assert result.actions[0].kind == ActionKind.UPLOAD_ATTACHMENT.value
    assert result.actions[0].status == ActionStatus.SUCCEEDED.value
    assert result.artifacts[0].kind == "upload_report"
    assert storage.uploads[0]["remote_path"] == "finance/april/report.txt"


def test_runtime_records_upload_actions_and_artifacts(tmp_path: Path) -> None:
    """Upload turns should produce durable actions, events, and artifacts."""
    storage = FakeStorageClient()
    session_dir = tmp_path / "sessions"
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=storage,
        session_dir=session_dir,
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

    tenant = TenantIdentity(platform="slack", workspace_id="T123TEAM")
    action_store = FileActionStore(session_dir)
    [action] = action_store.list_for_session(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:1713840000.123456",
    )
    assert action.kind is ActionKind.UPLOAD_ATTACHMENT
    assert action.status is ActionStatus.SUCCEEDED
    assert action.result is not None
    assert isinstance(action.result, UploadAttachmentResult)
    assert action.result.remote_path == "finance/april/report.txt"
    assert action.policy_decision is not None
    assert action.policy_decision.decision is PolicyDecision.ALLOW
    assert result.artifacts[0].payload is not None
    assert result.artifacts[0].payload["remote_path"] == "finance/april/report.txt"

    event_store = FileSessionEventStore(session_dir)
    events = event_store.list_events(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:1713840000.123456",
    )
    assert [event.event_type for event in events] == [
        "action_created",
        "action_started",
        "verification_started",
        "artifact_created",
        "artifact_created",
        "action_completed",
    ]


def test_runtime_records_failed_action_for_invalid_attachment_bytes(
    tmp_path: Path,
) -> None:
    """Malformed uploads should still leave an auditable terminal action."""
    session_dir = tmp_path / "sessions"
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=FakeStorageClient(),
        session_dir=session_dir,
        system_prompt="sys",
        tool_container="bucket",
    )

    result = _run_turn(
        runtime,
        _turn(
            text="upload these files to finance/april",
            attachments=(
                TurnAttachment(
                    platform_file_id="F-bad",
                    filename="bad.txt",
                    content_type="text/plain",
                    size_bytes=5,
                    content_base64="not valid base64",
                    sha256_hex=None,
                ),
            ),
        ),
    )

    tenant = TenantIdentity(platform="slack", workspace_id="T123TEAM")
    action_store = FileActionStore(session_dir)
    [action] = action_store.list_for_session(
        tenant=tenant,
        session_id="slack:T123TEAM:C123CHAN:1713840000.123456",
    )
    assert result.outcome == "error"
    assert action.status is ActionStatus.FAILED_TERMINAL
    assert action.failure is not None
    assert "base64" in action.failure.detail


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


def test_runtime_records_ai_tokens_split_by_direction(tmp_path: Path) -> None:
    """Each AI response increments input + output token counters separately.

    Splitting the metric by ``direction=input|output`` lets dashboards graph
    them on the same axis and compute ratios (e.g. when prompt size dwarfs
    completion size). The default fake response is 8 input / 13 output, so we
    expect the cumulative counters to match exactly after one turn.
    """
    runtime_telemetry.reset()
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=FakeStorageClient(),
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    _run_turn(runtime, _turn(text="hello"))

    counters = runtime_telemetry.snapshot()["counters"]
    input_key = "nimbus_ai_tokens_total|direction=input,model=test-model:free"
    output_key = "nimbus_ai_tokens_total|direction=output,model=test-model:free"
    assert counters[input_key] == 8  # matches _fake_response().tokens.input_tokens
    assert counters[output_key] == 13  # matches _fake_response().tokens.output_tokens


def test_runtime_records_ai_cost_only_when_estimate_is_provided(tmp_path: Path) -> None:
    """Cost histogram should be silent when ``cost_usd_estimate`` is ``None``.

    Distinguishing "no estimate available" from "$0" is the whole point of
    the optional field — the dashboard should not show a phantom $0 bar for
    every unpriced model. The default fake response leaves the field None,
    so the cost histogram must stay empty.
    """
    runtime_telemetry.reset()
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(),
        storage=FakeStorageClient(),
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    _run_turn(runtime, _turn(text="hello"))

    histograms = runtime_telemetry.snapshot()["histograms"]
    cost_keys = [k for k in histograms if k.startswith("nimbus_ai_cost_usd")]
    assert cost_keys == []


def test_runtime_records_ai_cost_histogram_when_estimate_present(
    tmp_path: Path,
) -> None:
    """When the response carries a cost estimate, the histogram captures it.

    We construct a priced response in the fake to avoid coupling this test to
    the OpenRouter pricing table (which is exercised separately). The
    histogram is checked by its in-memory snapshot — sum/min/max convey
    enough to assert correctness without a real OTel exporter.
    """
    runtime_telemetry.reset()
    priced_response = AIResponse(
        text="ok",
        model="openai/gpt-4o-mini",
        tokens=TokenUsage(input_tokens=100, output_tokens=200),
        tool_calls=(),
        latency_ms=10,
        stop_reason="end_turn",
        steps=1,
        fallback_used=False,
        cost_usd_estimate=0.000135,
    )
    runtime = NimbusRuntime(
        ai_client=FakeAIClient(response=priced_response),
        storage=FakeStorageClient(),
        session_dir=tmp_path / "sessions",
        system_prompt="sys",
        tool_container="bucket",
    )

    _run_turn(runtime, _turn(text="hello"))

    histograms = runtime_telemetry.snapshot()["histograms"]
    key = "nimbus_ai_cost_usd|model=openai/gpt-4o-mini"
    assert key in histograms
    assert histograms[key]["count"] == 1
    assert histograms[key]["sum"] == pytest.approx(0.000135)


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

    session_id = "slack:T123TEAM:C123CHAN:session-gc-test"

    async def _enter_and_check() -> None:
        async with runtime_mod.get_session_lock(session_id):
            assert session_id in runtime_mod._session_locks

    asyncio.run(_enter_and_check())
    gc.collect()

    assert session_id not in runtime_mod._session_locks


# ── Session usage sidecar tests ─────────────────────────────────────────────


def test_load_session_usage_returns_empty_when_no_file(tmp_path: Path) -> None:
    """Returns an empty dict when no usage sidecar file exists yet."""
    from nimbus_runtime.runtime import load_session_usage

    result = load_session_usage(tmp_path, "session-does-not-exist")
    assert result == {}


def test_update_session_usage_creates_file(tmp_path: Path) -> None:
    """First call creates the sidecar file with the given token counts."""
    import nimbus_runtime.runtime as runtime_mod

    runtime_mod._update_session_usage(
        tmp_path,
        "session-abc",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001,
    )

    from nimbus_runtime.runtime import load_session_usage

    usage = load_session_usage(tmp_path, "session-abc")
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert abs(float(usage["cost_usd_estimate"]) - 0.001) < 1e-9


def test_update_session_usage_accumulates_across_calls(tmp_path: Path) -> None:
    """Subsequent calls add to existing token/cost totals."""
    import nimbus_runtime.runtime as runtime_mod

    runtime_mod._update_session_usage(
        tmp_path, "session-acc", input_tokens=100, output_tokens=50, cost_usd=0.001
    )
    runtime_mod._update_session_usage(
        tmp_path, "session-acc", input_tokens=200, output_tokens=100, cost_usd=0.002
    )

    from nimbus_runtime.runtime import load_session_usage

    usage = load_session_usage(tmp_path, "session-acc")
    assert usage["input_tokens"] == 300
    assert usage["output_tokens"] == 150
    assert abs(float(usage["cost_usd_estimate"]) - 0.003) < 1e-9


def test_update_session_usage_no_cost_skips_cost_key(tmp_path: Path) -> None:
    """When cost_usd is None, no cost_usd_estimate key is written."""
    import nimbus_runtime.runtime as runtime_mod

    runtime_mod._update_session_usage(
        tmp_path, "session-nocost", input_tokens=10, output_tokens=5, cost_usd=None
    )

    from nimbus_runtime.runtime import load_session_usage

    usage = load_session_usage(tmp_path, "session-nocost")
    assert "cost_usd_estimate" not in usage
    assert usage["input_tokens"] == 10


def test_load_session_usage_returns_empty_on_corrupt_file(tmp_path: Path) -> None:
    """Corrupt sidecar JSON should be silently swallowed, returning empty dict."""
    import nimbus_runtime.runtime as runtime_mod

    path = runtime_mod._usage_path(tmp_path, "session-corrupt")
    path.write_text("not valid json", encoding="utf-8")

    from nimbus_runtime.runtime import load_session_usage

    result = load_session_usage(tmp_path, "session-corrupt")
    assert result == {}
