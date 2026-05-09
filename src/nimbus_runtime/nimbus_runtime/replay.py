"""Deterministic replay trace helpers for Nimbus runtime evidence."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast, get_args

from nimbus_runtime.domain import (
    ActionStatus,
    ApprovalStatus,
    GenerationStatus,
    StorageChangeStatus,
)
from nimbus_runtime.proof import artifact_payload_digest, digest_value, to_jsonable

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from nimbus_runtime.domain import Artifact, SessionEvent

TRACE_SCHEMA_VERSION = 1
STATUS_SPEC_SCHEMA_VERSION = 1

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type JsonObject = dict[str, JsonValue]
type TraceDiffKind = Literal["changed", "missing", "extra"]

__all__ = [
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "ReplayComparison",
    "TraceDiff",
    "TraceDiffKind",
    "TraceFormatError",
    "compare_traces",
    "export_trace",
    "normalize_trace_envelope",
    "replay_trace",
    "runtime_status_spec",
]


class TraceFormatError(ValueError):
    """Raised when replay input cannot be parsed as canonical trace JSON."""

    @classmethod
    def unsupported_schema(cls, value: object) -> TraceFormatError:
        """Build an error for an unsupported trace schema version."""
        msg = f"unsupported replay trace schema version: {value!r}"
        return cls(msg)

    @classmethod
    def missing_field(cls, field: str) -> TraceFormatError:
        """Build an error for a missing required trace field."""
        msg = f"missing replay trace field: {field}"
        return cls(msg)

    @classmethod
    def expected_field_type(
        cls,
        *,
        field: str,
        expected: str,
    ) -> TraceFormatError:
        """Build an error for a field whose JSON type is not accepted."""
        msg = f"expected {expected} for replay trace field {field}"
        return cls(msg)

    @classmethod
    def non_string_key(cls, path: str) -> TraceFormatError:
        """Build an error for an object key that cannot be represented in JSON."""
        msg = f"expected string object key at {path}"
        return cls(msg)

    @classmethod
    def unsupported_value(cls, *, path: str, type_name: str) -> TraceFormatError:
        """Build an error for a value outside the JSON data model."""
        msg = f"unsupported replay trace value at {path}: {type_name}"
        return cls(msg)

    @classmethod
    def non_finite_number(cls, path: str) -> TraceFormatError:
        """Build an error for a non-finite JSON number."""
        msg = f"non-finite replay trace number at {path}"
        return cls(msg)

    @classmethod
    def naive_datetime(cls, field: str) -> TraceFormatError:
        """Build an error for a timestamp without timezone authority."""
        msg = f"expected timezone-aware datetime for {field}"
        return cls(msg)

    @classmethod
    def invalid_status_literal(cls, name: str) -> TraceFormatError:
        """Build an error for a status source that is no longer a string literal."""
        msg = f"expected string Literal values for runtime status spec {name}"
        return cls(msg)


@dataclass(frozen=True, slots=True)
class TraceDiff:
    """One strict JSON value difference between replay traces."""

    path: str
    kind: TraceDiffKind
    expected: JsonValue | None
    actual: JsonValue | None


@dataclass(frozen=True, slots=True)
class ReplayComparison:
    """Result of replaying current evidence against a recorded trace."""

    actual_trace: JsonObject
    diffs: tuple[TraceDiff, ...]

    @property
    def matches(self) -> bool:
        """Return whether the replayed evidence exactly matched the trace."""
        return not self.diffs


def export_trace(
    *,
    events: Sequence[SessionEvent],
    artifacts: Sequence[Artifact] = (),
    trace_id: str | None = None,
    exported_at: datetime | None = None,
) -> JsonObject:
    """Export runtime events and artifacts as a deterministic JSON envelope."""
    event_records: list[JsonValue] = [
        _event_record(event) for event in _sort_events(events)
    ]
    artifact_records: list[JsonValue] = [
        _artifact_record(artifact) for artifact in _sort_artifacts(artifacts)
    ]
    status_spec = runtime_status_spec()
    content: JsonObject = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "events": event_records,
        "artifacts": artifact_records,
        "runtime_status_spec": status_spec,
    }
    content_digest = digest_value(content)
    return {
        "schema_version": TRACE_SCHEMA_VERSION,
        "trace_id": trace_id if trace_id is not None else _trace_id_for(content),
        "exported_at": _datetime_to_json(exported_at, field="exported_at")
        if exported_at is not None
        else None,
        "content_digest": content_digest,
        "events": event_records,
        "artifacts": artifact_records,
        "runtime_status_spec": status_spec,
    }


def replay_trace(
    expected: Mapping[str, object],
    *,
    events: Sequence[SessionEvent],
    artifacts: Sequence[Artifact] = (),
    trace_id: str | None = None,
    exported_at: datetime | None = None,
) -> ReplayComparison:
    """Replay current runtime evidence and diff it against an expected trace."""
    actual = export_trace(
        events=events,
        artifacts=artifacts,
        trace_id=trace_id,
        exported_at=exported_at,
    )
    return ReplayComparison(
        actual_trace=actual,
        diffs=compare_traces(expected, actual),
    )


def compare_traces(
    expected: Mapping[str, object],
    actual: Mapping[str, object],
) -> tuple[TraceDiff, ...]:
    """Return strict JSON diffs between two replay trace envelopes."""
    expected_trace = normalize_trace_envelope(expected)
    actual_trace = normalize_trace_envelope(actual)
    return tuple(_iter_diffs(expected_trace, actual_trace, "$"))


def normalize_trace_envelope(raw: Mapping[str, object]) -> JsonObject:
    """Parse raw JSON-like input into a canonical replay trace envelope."""
    envelope = _copy_json_object(raw, path="$")
    schema_version = envelope.get("schema_version")
    if schema_version != TRACE_SCHEMA_VERSION:
        raise TraceFormatError.unsupported_schema(schema_version)
    _require_string(envelope, "trace_id")
    _require_optional_string(envelope, "exported_at")
    _require_string(envelope, "content_digest")
    _require_object_array(envelope, "events")
    _require_object_array(envelope, "artifacts")
    _require_object(envelope, "runtime_status_spec")
    return envelope


def runtime_status_spec() -> JsonObject:
    """Return the formal runtime status values included in replay traces."""
    statuses: JsonObject = {
        "action": [status.value for status in ActionStatus],
        "approval": [status.value for status in ApprovalStatus],
        "generation": _literal_status_values(
            GenerationStatus,
            name="generation",
        ),
        "stack": _literal_status_values(StorageChangeStatus, name="stack"),
    }
    return {
        "schema_version": STATUS_SPEC_SCHEMA_VERSION,
        "statuses": statuses,
    }


def _sort_events(events: Sequence[SessionEvent]) -> list[SessionEvent]:
    return sorted(
        events,
        key=lambda event: (
            event.tenant.tenant_id,
            event.session_id,
            event.sequence,
            event.event_id,
        ),
    )


def _sort_artifacts(artifacts: Sequence[Artifact]) -> list[Artifact]:
    return sorted(
        artifacts,
        key=lambda artifact: (
            artifact.tenant.tenant_id,
            artifact.session_id,
            _datetime_to_json(artifact.created_at, field="artifact.created_at"),
            artifact.artifact_id,
        ),
    )


def _event_record(event: SessionEvent) -> JsonObject:
    return {
        "tenant_id": event.tenant.tenant_id,
        "tenant": _json_object(event.tenant, field="event.tenant"),
        "session_id": event.session_id,
        "sequence": event.sequence,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "actor": _json_object(event.actor, field="event.actor")
        if event.actor is not None
        else None,
        "payload": _json_object(event.payload, field="event.payload"),
        "created_at": _datetime_to_json(event.created_at, field="event.created_at"),
    }


def _artifact_record(artifact: Artifact) -> JsonObject:
    return {
        "tenant_id": artifact.tenant.tenant_id,
        "tenant": _json_object(artifact.tenant, field="artifact.tenant"),
        "session_id": artifact.session_id,
        "artifact_id": artifact.artifact_id,
        "action_id": artifact.action_id,
        "kind": artifact.kind,
        "uri": artifact.uri,
        "payload": _json_object(artifact.payload, field="artifact.payload"),
        "payload_digest": artifact.payload_digest
        if artifact.payload_digest is not None
        else artifact_payload_digest(artifact.payload),
        "created_at": _datetime_to_json(
            artifact.created_at,
            field="artifact.created_at",
        ),
    }


def _json_object(value: object, *, field: str) -> JsonObject:
    jsonable = to_jsonable(value)
    if not isinstance(jsonable, dict):
        raise TraceFormatError.expected_field_type(field=field, expected="object")
    return jsonable


def _datetime_to_json(value: datetime, *, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TraceFormatError.naive_datetime(field)
    return value.astimezone(UTC).isoformat()


def _trace_id_for(content: JsonObject) -> str:
    digest = digest_value(content).removeprefix("sha256:")
    return f"trace-{digest[:32]}"


def _literal_status_values(value: object, *, name: str) -> list[JsonValue]:
    values = get_args(value)
    if not values or not all(isinstance(item, str) for item in values):
        raise TraceFormatError.invalid_status_literal(name)
    return list(cast("tuple[str, ...]", values))


def _copy_json_object(raw: Mapping[str, object], *, path: str) -> JsonObject:
    value = _copy_json_value(raw, path=path)
    if not isinstance(value, dict):
        raise TraceFormatError.expected_field_type(field=path, expected="object")
    return value


def _copy_json_value(value: object, *, path: str) -> JsonValue:
    if isinstance(value, Mapping):
        copied: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TraceFormatError.non_string_key(path)
            copied[key] = _copy_json_value(item, path=_join_key(path, key))
        return copied
    if isinstance(value, list):
        return [
            _copy_json_value(item, path=_join_index(path, index))
            for index, item in enumerate(value)
        ]
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TraceFormatError.non_finite_number(path)
        return value
    if isinstance(value, str) or value is None:
        return value
    raise TraceFormatError.unsupported_value(
        path=path,
        type_name=type(value).__name__,
    )


def _require_string(envelope: JsonObject, field: str) -> str:
    if field not in envelope:
        raise TraceFormatError.missing_field(field)
    value = envelope[field]
    if not isinstance(value, str):
        raise TraceFormatError.expected_field_type(field=field, expected="string")
    return value


def _require_optional_string(envelope: JsonObject, field: str) -> str | None:
    if field not in envelope:
        raise TraceFormatError.missing_field(field)
    value = envelope[field]
    if value is None or isinstance(value, str):
        return value
    raise TraceFormatError.expected_field_type(
        field=field,
        expected="string or null",
    )


def _require_object(envelope: JsonObject, field: str) -> JsonObject:
    if field not in envelope:
        raise TraceFormatError.missing_field(field)
    value = envelope[field]
    if not isinstance(value, dict):
        raise TraceFormatError.expected_field_type(field=field, expected="object")
    return value


def _require_object_array(envelope: JsonObject, field: str) -> list[JsonObject]:
    if field not in envelope:
        raise TraceFormatError.missing_field(field)
    value = envelope[field]
    if not isinstance(value, list):
        raise TraceFormatError.expected_field_type(field=field, expected="array")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            item_field = _join_index(field, index)
            raise TraceFormatError.expected_field_type(
                field=item_field,
                expected="object",
            )
    return cast("list[JsonObject]", value)


def _iter_diffs(
    expected: JsonValue,
    actual: JsonValue,
    path: str,
) -> Iterator[TraceDiff]:
    if isinstance(expected, dict) and isinstance(actual, dict):
        expected_keys = expected.keys()
        actual_keys = actual.keys()
        for key in sorted(expected_keys - actual_keys):
            yield TraceDiff(
                path=_join_key(path, key),
                kind="missing",
                expected=expected[key],
                actual=None,
            )
        for key in sorted(actual_keys - expected_keys):
            yield TraceDiff(
                path=_join_key(path, key),
                kind="extra",
                expected=None,
                actual=actual[key],
            )
        for key in sorted(expected_keys & actual_keys):
            yield from _iter_diffs(
                expected[key],
                actual[key],
                _join_key(path, key),
            )
        return
    if isinstance(expected, list) and isinstance(actual, list):
        common_length = min(len(expected), len(actual))
        for index in range(common_length):
            yield from _iter_diffs(
                expected[index],
                actual[index],
                _join_index(path, index),
            )
        for index in range(common_length, len(expected)):
            yield TraceDiff(
                path=_join_index(path, index),
                kind="missing",
                expected=expected[index],
                actual=None,
            )
        for index in range(common_length, len(actual)):
            yield TraceDiff(
                path=_join_index(path, index),
                kind="extra",
                expected=None,
                actual=actual[index],
            )
        return
    if expected != actual:
        yield TraceDiff(path=path, kind="changed", expected=expected, actual=actual)


def _join_key(path: str, key: str) -> str:
    if key.isidentifier():
        return f"{path}.{key}"
    encoded = json.dumps(key, ensure_ascii=True)
    return f"{path}[{encoded}]"


def _join_index(path: str, index: int) -> str:
    return f"{path}[{index}]"
