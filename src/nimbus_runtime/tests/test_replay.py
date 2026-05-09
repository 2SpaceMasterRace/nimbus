"""Tests for deterministic Nimbus replay trace exports."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import get_args

import pytest
from nimbus_runtime.domain import (
    ActionStatus,
    ApprovalStatus,
    Artifact,
    GenerationStatus,
    SessionEvent,
    StorageChangeStatus,
    TenantIdentity,
    UploadReport,
    VerifiedActor,
)
from nimbus_runtime.proof import artifact_payload_digest
from nimbus_runtime.replay import (
    TraceFormatError,
    compare_traces,
    export_trace,
    normalize_trace_envelope,
    replay_trace,
    runtime_status_spec,
)

pytestmark = pytest.mark.unit

_FIXED_NOW = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
_SESSION_ID = "slack:TREPLAY:CCHANNEL:thread"


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="slack", workspace_id="TREPLAY")


def _actor(tenant: TenantIdentity) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id="UREPLAY",
        auth_source="slack_signed_event",
        bridge_id="slack",
        verified_at=_FIXED_NOW,
    )


def _event(
    *,
    sequence: int = 1,
    event_id: str = "evt-replay-1",
    status: ActionStatus = ActionStatus.AUTHORIZED,
) -> SessionEvent:
    tenant = _tenant()
    return SessionEvent(
        tenant=tenant,
        session_id=_SESSION_ID,
        sequence=sequence,
        event_id=event_id,
        event_type="action_transitioned",
        actor=_actor(tenant),
        payload={
            "action_id": "act-replay",
            "status": status,
            "target": "reports/demo.txt",
        },
        created_at=_FIXED_NOW,
    )


def _artifact() -> Artifact:
    tenant = _tenant()
    payload = UploadReport(
        remote_path="reports/demo.txt",
        filename="demo.txt",
        size_bytes=12,
        sha256_hex="ab" * 32,
    )
    return Artifact(
        artifact_id="art-replay",
        tenant=tenant,
        session_id=_SESSION_ID,
        action_id="act-replay",
        kind="upload_report",
        uri=None,
        payload=payload,
        created_at=_FIXED_NOW,
        payload_digest=artifact_payload_digest(payload),
    )


def test_export_trace_is_stable_json_with_injected_id_and_clock() -> None:
    """Trace export should be deterministic and directly JSON serializable."""
    event = _event()
    artifact = _artifact()

    first = export_trace(
        events=(event,),
        artifacts=(artifact,),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )
    second = export_trace(
        events=(event,),
        artifacts=(artifact,),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )

    assert first == second
    assert first["trace_id"] == "trace-fixed"
    assert first["exported_at"] == "2026-05-21T12:00:00+00:00"
    assert isinstance(first["content_digest"], str)
    assert first["content_digest"].startswith("sha256:")
    json.dumps(first, sort_keys=True)


def test_export_trace_rejects_naive_export_clock() -> None:
    """Export metadata should not silently accept timezone-ambiguous clocks."""
    naive_now = _FIXED_NOW.replace(tzinfo=None)

    with pytest.raises(TraceFormatError, match="timezone-aware"):
        export_trace(
            events=(_event(),),
            artifacts=(_artifact(),),
            exported_at=naive_now,
        )


def test_normalize_trace_envelope_rejects_malformed_input() -> None:
    """Replay comparison should parse and reject malformed boundary input."""
    trace = export_trace(
        events=(_event(),),
        artifacts=(_artifact(),),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )
    wrong_schema = dict(trace)
    wrong_schema["schema_version"] = 999
    missing_events = dict(trace)
    del missing_events["events"]

    with pytest.raises(TraceFormatError, match="unsupported"):
        normalize_trace_envelope(wrong_schema)
    with pytest.raises(TraceFormatError, match="events"):
        normalize_trace_envelope(missing_events)


def test_replay_trace_reports_strict_event_payload_drift() -> None:
    """Replay should identify the exact event payload path that drifted."""
    expected = export_trace(
        events=(_event(status=ActionStatus.AUTHORIZED),),
        artifacts=(_artifact(),),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )
    drifted_event = _event(status=ActionStatus.EXECUTING)

    comparison = replay_trace(
        expected,
        events=(drifted_event,),
        artifacts=(_artifact(),),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )

    assert not comparison.matches
    assert any(
        diff.path == "$.events[0].payload.status"
        and diff.kind == "changed"
        and diff.expected == "authorized"
        and diff.actual == "executing"
        for diff in comparison.diffs
    )


def test_compare_traces_reports_missing_events_by_index() -> None:
    """Strict diffs should report missing event rows instead of ignoring length."""
    expected = export_trace(
        events=(_event(sequence=1), _event(sequence=2, event_id="evt-replay-2")),
        artifacts=(_artifact(),),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )
    actual = export_trace(
        events=(_event(sequence=1),),
        artifacts=(_artifact(),),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )

    diffs = compare_traces(expected, actual)

    assert any(diff.path == "$.events[1]" and diff.kind == "missing" for diff in diffs)


def test_runtime_status_spec_matches_fixture() -> None:
    """The committed formal spec should move intentionally with status changes."""
    fixture_path = Path(__file__).with_name("fixtures") / "replay_status_spec.json"
    expected = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert runtime_status_spec() == expected


def test_runtime_status_spec_is_derived_from_domain_status_sources() -> None:
    """Approval, action, generation, and stack status sources should stay wired."""
    spec = runtime_status_spec()
    statuses = spec["statuses"]
    assert isinstance(statuses, dict)

    assert statuses["action"] == [status.value for status in ActionStatus]
    assert statuses["approval"] == [status.value for status in ApprovalStatus]
    assert statuses["generation"] == list(get_args(GenerationStatus))
    assert statuses["stack"] == list(get_args(StorageChangeStatus))


def test_replay_trace_detects_artifact_payload_drift() -> None:
    """Artifact payload drift should be visible through strict trace comparison."""
    expected_artifact = _artifact()
    expected = export_trace(
        events=(_event(),),
        artifacts=(expected_artifact,),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )
    drifted_payload = replace(expected_artifact.payload, size_bytes=13)
    drifted_artifact = replace(
        expected_artifact,
        payload=drifted_payload,
        payload_digest=artifact_payload_digest(drifted_payload),
    )

    comparison = replay_trace(
        expected,
        events=(_event(),),
        artifacts=(drifted_artifact,),
        trace_id="trace-fixed",
        exported_at=_FIXED_NOW,
    )

    assert not comparison.matches
    assert any(
        diff.path == "$.artifacts[0].payload.size_bytes"
        and diff.expected == 12
        and diff.actual == 13
        for diff in comparison.diffs
    )
