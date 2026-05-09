"""Tests for provider health probes and evidence artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from cloud_storage_api import ObjectInfo
from cloud_storage_api.exceptions import AuthenticationError
from nimbus_runtime.domain import ProviderHealthReport, ProviderOutcome, TenantIdentity
from nimbus_runtime.provider_health import (
    classify_provider_exception,
    create_provider_health_artifact,
    run_provider_health_probes,
)
from nimbus_runtime.stores import FileArtifactStore

pytestmark = pytest.mark.unit


def test_provider_health_probe_records_successful_list_and_head() -> None:
    """Live LIST/HEAD probes produce high-confidence healthy evidence."""
    storage = _FakePagedStorage(
        objects=[
            ObjectInfo(
                object_name="team/a.txt",
                size_bytes=5,
                integrity="sha256:a",
            )
        ]
    )

    report = run_provider_health_probes(
        storage=storage,
        tenant=_tenant(),
        provider="s3",
        container="bucket",
        prefix="team/",
        now=_now(),
    )

    assert report.status == "healthy"
    assert report.health_score == 100
    assert report.confidence == "high"
    assert [probe.operation for probe in report.probes] == ["LIST", "HEAD"]
    assert all(probe.outcome is ProviderOutcome.SUCCESS for probe in report.probes)
    assert any(
        "AWS Service Health Dashboard" in item for item in report.advisory_context
    )
    assert storage.list_calls == [("bucket", "team/", 1, "")]
    assert storage.head_calls == [("bucket", "team/a.txt")]


def test_provider_health_probe_degrades_without_bounded_pagination() -> None:
    """Nimbus must not replace a bounded probe with an unbounded full listing."""
    report = run_provider_health_probes(
        storage=_FakeUnboundedOnlyStorage(),
        tenant=_tenant(),
        provider="s3",
        container="bucket",
        prefix="team/",
        now=_now(),
    )

    assert report.status == "degraded"
    assert report.confidence == "low"
    assert report.probes[0].outcome is ProviderOutcome.PROVIDER_HEALTH_DEGRADED
    assert "ProviderPagination" in (report.probes[0].error_message or "")


def test_provider_health_probe_classifies_auth_failure() -> None:
    """Provider auth failures become explicit health outcomes."""
    report = run_provider_health_probes(
        storage=_FakePagedStorage(error=AuthenticationError("bad credentials")),
        tenant=_tenant(),
        provider="s3",
        container="bucket",
        prefix="team/",
        now=_now(),
    )

    assert report.status == "blocked"
    assert report.probes[0].outcome is ProviderOutcome.AUTH_FAILURE
    assert "credentials" in (report.probes[0].error_message or "")


def test_provider_health_artifact_round_trips(tmp_path: Path) -> None:
    """Provider health is persisted as a typed immutable evidence artifact."""
    store = FileArtifactStore(tmp_path)
    report = run_provider_health_probes(
        storage=_FakePagedStorage(objects=[]),
        tenant=_tenant(),
        provider="s3",
        container="bucket",
        prefix="team/",
        now=_now(),
    )

    artifact = create_provider_health_artifact(
        report=report,
        artifact_store=store,
        actor=None,
        session_id="sess-health",
    )
    found = store.get(tenant=_tenant(), artifact_id=artifact.artifact_id)

    assert found is not None
    assert found.kind == "provider_health"
    assert found.payload_digest is not None
    assert isinstance(found.payload, ProviderHealthReport)
    assert found.payload.report_id == report.report_id
    assert found.payload.probes[0].outcome is ProviderOutcome.SUCCESS


def test_classify_provider_exception_maps_timeouts() -> None:
    """Transport timeouts are represented distinctly from unknown failures."""
    assert (
        classify_provider_exception(TimeoutError("timed out"))
        is ProviderOutcome.TIMEOUT
    )


class _FakePagedStorage:
    def __init__(
        self,
        *,
        objects: list[ObjectInfo] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._objects = objects or []
        self._error = error
        self.list_calls: list[tuple[str, str, int, str]] = []
        self.head_calls: list[tuple[str, str]] = []

    def list_files_page(
        self,
        container: str,
        prefix: str,
        max_keys: int,
        continuation_token: str = "",
    ) -> tuple[list[ObjectInfo], str]:
        self.list_calls.append((container, prefix, max_keys, continuation_token))
        if self._error is not None:
            raise self._error
        matches = [obj for obj in self._objects if obj.object_name.startswith(prefix)]
        return matches[:max_keys], ""

    def get_file_info(self, container: str, object_name: str) -> ObjectInfo:
        self.head_calls.append((container, object_name))
        for obj in self._objects:
            if obj.object_name == object_name:
                return obj
        msg = f"unexpected head for {container}/{object_name}"
        raise AssertionError(msg)


class _FakeUnboundedOnlyStorage:
    def list_files(self, _container: str, _prefix: str) -> list[ObjectInfo]:
        msg = "unbounded list_files must not be called"
        raise AssertionError(msg)


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="cli", workspace_id="local")


def _now() -> datetime:
    return datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
