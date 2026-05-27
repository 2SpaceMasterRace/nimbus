"""Live provider health probes and advisory evidence payloads.

Provider status pages are advisory context only. The functions here record what
Nimbus actually observed against a tenant's configured storage scope, then write
that observation as a normal immutable artifact.
"""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from cloud_storage_api.exceptions import (
    AuthenticationError,
    ContainerNotFoundError,
    ObjectNotFoundError,
    StorageBackendError,
)

from nimbus_runtime.domain import (
    Artifact,
    ProviderHealthReport,
    ProviderName,
    ProviderOutcome,
    ProviderProbeResult,
    TenantIdentity,
)
from nimbus_runtime.proof import canonical_json_bytes, to_jsonable
from nimbus_runtime.provider_capabilities import ProviderPagination

if TYPE_CHECKING:
    from cloud_storage_api import CloudStorageClient, ObjectInfo

    from nimbus_runtime.domain import VerifiedActor
    from nimbus_runtime.stores import ArtifactStore

_DEFAULT_TTL_SECONDS = 300
_DEFAULT_MAX_LIST_KEYS = 1
_ERROR_MESSAGE_LIMIT = 240
_AWS_SERVICE_HEALTH_URL = "https://health.aws.amazon.com/health/status"
_AWS_PERSONAL_HEALTH_DASHBOARD_URL = "https://health.aws.amazon.com/health/home"


def classify_provider_exception(exc: BaseException) -> ProviderOutcome:  # noqa: PLR0911
    """Map provider/transport failures into the shared Nimbus taxonomy."""
    if isinstance(exc, AuthenticationError):
        return ProviderOutcome.AUTH_FAILURE
    if isinstance(exc, ContainerNotFoundError | ObjectNotFoundError):
        return ProviderOutcome.NOT_FOUND
    if isinstance(exc, PermissionError):
        return ProviderOutcome.PERMISSION_DENIED
    if isinstance(exc, TimeoutError):
        return ProviderOutcome.TIMEOUT
    message = str(exc).lower()
    if isinstance(exc, StorageBackendError):
        if any(token in message for token in ("access denied", "forbidden", "403")):
            return ProviderOutcome.PERMISSION_DENIED
        if any(token in message for token in ("throttl", "slowdown", "429")):
            return ProviderOutcome.THROTTLED
        if any(token in message for token in ("timeout", "timed out")):
            return ProviderOutcome.TIMEOUT
        if any(
            token in message
            for token in (
                "unavailable",
                "connection",
                "connect",
                "reset",
                "503",
                "502",
            )
        ):
            return ProviderOutcome.PROVIDER_UNAVAILABLE
    return ProviderOutcome.UNKNOWN


def run_provider_health_probes(  # noqa: PLR0913
    *,
    storage: CloudStorageClient,
    tenant: TenantIdentity,
    provider: ProviderName,
    container: str,
    prefix: str = "",
    region: str | None = None,
    head_key: str | None = None,
    now: datetime | None = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    max_list_keys: int = _DEFAULT_MAX_LIST_KEYS,
) -> ProviderHealthReport:
    """Run bounded live probes and return a provider health report.

    The LIST probe uses ``ProviderPagination`` when available so the check never
    expands into an unbounded bucket scan. If the storage client cannot provide
    that bounded primitive, Nimbus records a degraded probe instead of silently
    falling back to an expensive full listing.
    """
    generated_at = now or datetime.now(UTC)
    list_probe, inferred_head_key = _probe_list(
        storage=storage,
        provider=provider,
        container=container,
        prefix=prefix,
        region=region,
        observed_at=generated_at,
        max_list_keys=max(1, max_list_keys),
    )
    probes = [list_probe]
    resolved_head_key = head_key or inferred_head_key
    if resolved_head_key:
        probes.append(
            _probe_head(
                storage=storage,
                provider=provider,
                container=container,
                prefix=prefix,
                object_name=resolved_head_key,
                region=region,
                observed_at=generated_at,
            )
        )

    probe_tuple = tuple(probes)
    status = provider_health_status(probe_tuple)
    return ProviderHealthReport(
        report_id=provider_health_report_id(
            tenant=tenant,
            provider=provider,
            container=container,
            prefix=prefix,
            region=region,
            generated_at=generated_at,
            probes=probe_tuple,
        ),
        tenant=tenant,
        provider=provider,
        container=container,
        prefix=prefix,
        region=region,
        status=status,
        health_score=provider_health_score(probe_tuple),
        confidence=_confidence_for(probe_tuple),
        evidence_source="live_probe",
        generated_at=generated_at,
        expires_at=generated_at + timedelta(seconds=max(1, ttl_seconds)),
        probes=probe_tuple,
        advisory_context=provider_advisory_context(provider=provider, region=region),
        next_operator_step=_next_operator_step(status=status, probes=probe_tuple),
    )


def provider_advisory_context(
    *, provider: ProviderName, region: str | None
) -> tuple[str, ...]:
    """Return operator links that are useful context but not proof.

    Nimbus treats provider status pages as advisory because they cannot prove
    whether this tenant's bucket, IAM policy, prefix, or object state is usable.
    The live probes remain authoritative for the report status.
    """
    if provider != "s3":
        return ()
    region_hint = f"region={region}" if region else "region=unknown"
    return (
        f"AWS Service Health Dashboard ({region_hint}): {_AWS_SERVICE_HEALTH_URL}",
        "AWS Health Dashboard for account-specific events: "
        f"{_AWS_PERSONAL_HEALTH_DASHBOARD_URL}",
    )


def create_provider_health_artifact(
    *,
    report: ProviderHealthReport,
    artifact_store: ArtifactStore,
    actor: VerifiedActor | None,
    session_id: str,
    action_id: str | None = None,
) -> Artifact:
    """Persist provider health as immutable tenant-scoped evidence."""
    return artifact_store.create(
        artifact=Artifact(
            artifact_id=f"art-provider-health-{report.report_id.removeprefix('phr-')}",
            tenant=report.tenant,
            session_id=session_id,
            action_id=action_id,
            kind="provider_health",
            uri=None,
            payload=report,
            created_at=report.generated_at,
        ),
        actor=actor,
    )


def provider_health_report_id(  # noqa: PLR0913
    *,
    tenant: TenantIdentity,
    provider: ProviderName,
    container: str,
    prefix: str,
    region: str | None,
    generated_at: datetime,
    probes: tuple[ProviderProbeResult, ...],
) -> str:
    """Return a stable ID for one concrete provider-health observation."""
    seed = {
        "tenant_id": tenant.tenant_id,
        "provider": provider,
        "container": container,
        "prefix": prefix,
        "region": region,
        "generated_at": generated_at.isoformat(),
        "probes": [to_jsonable(probe) for probe in probes],
    }
    digest = hashlib.sha256(canonical_json_bytes(seed)).hexdigest()
    return f"phr-{digest[:32]}"


def provider_health_status(probes: tuple[ProviderProbeResult, ...]) -> str:
    """Return the operator-facing health status for a set of probes."""
    if not probes:
        return "unknown"
    outcomes = {probe.outcome for probe in probes}
    if outcomes == {ProviderOutcome.SUCCESS}:
        return "healthy"
    if outcomes & {ProviderOutcome.AUTH_FAILURE, ProviderOutcome.PERMISSION_DENIED}:
        return "blocked"
    if outcomes <= {
        ProviderOutcome.TIMEOUT,
        ProviderOutcome.THROTTLED,
        ProviderOutcome.PROVIDER_UNAVAILABLE,
        ProviderOutcome.UNKNOWN,
    }:
        return "unavailable"
    return "degraded"


def provider_health_score(probes: tuple[ProviderProbeResult, ...]) -> int:
    """Return a bounded 0-100 provider health score."""
    if not probes:
        return 0
    penalties = {
        ProviderOutcome.SUCCESS: 0,
        ProviderOutcome.PROVIDER_HEALTH_DEGRADED: 35,
        ProviderOutcome.NOT_FOUND: 40,
        ProviderOutcome.STALE_MANIFEST: 40,
        ProviderOutcome.CHECKSUM_MISMATCH: 60,
        ProviderOutcome.THROTTLED: 50,
        ProviderOutcome.TIMEOUT: 55,
        ProviderOutcome.PROVIDER_UNAVAILABLE: 60,
        ProviderOutcome.OUTCOME_AMBIGUOUS: 70,
        ProviderOutcome.UNKNOWN: 60,
        ProviderOutcome.PERMISSION_DENIED: 80,
        ProviderOutcome.AUTH_FAILURE: 90,
    }
    total_penalty = sum(penalties[probe.outcome] for probe in probes)
    return max(0, min(100, 100 - round(total_penalty / len(probes))))


def _probe_list(  # noqa: PLR0913
    *,
    storage: CloudStorageClient,
    provider: ProviderName,
    container: str,
    prefix: str,
    region: str | None,
    observed_at: datetime,
    max_list_keys: int,
) -> tuple[ProviderProbeResult, str | None]:
    started = time.perf_counter()
    if not isinstance(storage, ProviderPagination):
        return (
            ProviderProbeResult(
                probe_name="list",
                operation="LIST",
                provider=provider,
                container=container,
                prefix=prefix,
                object_name=None,
                region=region,
                outcome=ProviderOutcome.PROVIDER_HEALTH_DEGRADED,
                latency_ms=_elapsed_ms(started),
                item_count=None,
                request_id=None,
                error_message=(
                    "storage client does not expose ProviderPagination; "
                    "bounded LIST probe was not attempted"
                ),
                observed_at=observed_at,
            ),
            None,
        )
    try:
        items, _next_token = storage.list_files_page(
            container,
            prefix,
            max_list_keys,
            "",
        )
    except Exception as exc:  # noqa: BLE001 - boundary classifier owns mapping.
        return (
            _failed_probe(
                probe_name="list",
                operation="LIST",
                provider=provider,
                container=container,
                prefix=prefix,
                object_name=None,
                region=region,
                observed_at=observed_at,
                started=started,
                exc=exc,
            ),
            None,
        )
    first = _first_object_name(items)
    return (
        ProviderProbeResult(
            probe_name="list",
            operation="LIST",
            provider=provider,
            container=container,
            prefix=prefix,
            object_name=first,
            region=region,
            outcome=ProviderOutcome.SUCCESS,
            latency_ms=_elapsed_ms(started),
            item_count=len(items),
            request_id=None,
            error_message=None,
            observed_at=observed_at,
        ),
        first,
    )


def _probe_head(  # noqa: PLR0913
    *,
    storage: CloudStorageClient,
    provider: ProviderName,
    container: str,
    prefix: str,
    object_name: str,
    region: str | None,
    observed_at: datetime,
) -> ProviderProbeResult:
    started = time.perf_counter()
    try:
        storage.get_file_info(container, object_name)
    except Exception as exc:  # noqa: BLE001 - boundary classifier owns mapping.
        return _failed_probe(
            probe_name="head",
            operation="HEAD",
            provider=provider,
            container=container,
            prefix=prefix,
            object_name=object_name,
            region=region,
            observed_at=observed_at,
            started=started,
            exc=exc,
        )
    return ProviderProbeResult(
        probe_name="head",
        operation="HEAD",
        provider=provider,
        container=container,
        prefix=prefix,
        object_name=object_name,
        region=region,
        outcome=ProviderOutcome.SUCCESS,
        latency_ms=_elapsed_ms(started),
        item_count=1,
        request_id=None,
        error_message=None,
        observed_at=observed_at,
    )


def _failed_probe(  # noqa: PLR0913
    *,
    probe_name: str,
    operation: str,
    provider: ProviderName,
    container: str,
    prefix: str,
    object_name: str | None,
    region: str | None,
    observed_at: datetime,
    started: float,
    exc: BaseException,
) -> ProviderProbeResult:
    return ProviderProbeResult(
        probe_name=probe_name,
        operation=operation,
        provider=provider,
        container=container,
        prefix=prefix,
        object_name=object_name,
        region=region,
        outcome=classify_provider_exception(exc),
        latency_ms=_elapsed_ms(started),
        item_count=None,
        request_id=None,
        error_message=_safe_error_message(exc),
        observed_at=observed_at,
    )


def _first_object_name(items: list[ObjectInfo]) -> str | None:
    if not items:
        return None
    return items[0].object_name


def _confidence_for(probes: tuple[ProviderProbeResult, ...]) -> str:
    if not probes:
        return "low"
    if all(
        probe.outcome is ProviderOutcome.PROVIDER_HEALTH_DEGRADED for probe in probes
    ):
        return "low"
    return "high"


def _next_operator_step(  # noqa: PLR0911
    *,
    status: str,
    probes: tuple[ProviderProbeResult, ...],
) -> str:
    outcomes = {probe.outcome for probe in probes}
    if status == "healthy":
        return "No action required; live Nimbus probes succeeded."
    if outcomes & {ProviderOutcome.AUTH_FAILURE, ProviderOutcome.PERMISSION_DENIED}:
        return (
            "Check the profile credentials, IAM policy, bucket policy, and KMS grants."
        )
    if ProviderOutcome.PROVIDER_HEALTH_DEGRADED in outcomes:
        return (
            "Use a storage client with bounded ProviderPagination, or pass "
            "--head-key to probe one known object without a bucket listing."
        )
    if outcomes & {ProviderOutcome.TIMEOUT, ProviderOutcome.THROTTLED}:
        return (
            "Retry with backoff and inspect provider/request logs if the failure "
            "persists."
        )
    if ProviderOutcome.PROVIDER_UNAVAILABLE in outcomes:
        return (
            "Treat storage as degraded and compare Nimbus probe evidence with "
            "provider status."
        )
    if ProviderOutcome.NOT_FOUND in outcomes:
        return "Verify the bucket, prefix, and optional --head-key still exist."
    return (
        "Inspect the provider-health artifact and retry the probe with a narrower "
        "prefix."
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _safe_error_message(exc: BaseException) -> str:
    collapsed = " ".join(str(exc).split())
    if len(collapsed) <= _ERROR_MESSAGE_LIMIT:
        return collapsed
    return collapsed[: _ERROR_MESSAGE_LIMIT - 3] + "..."


__all__ = [
    "classify_provider_exception",
    "create_provider_health_artifact",
    "provider_health_report_id",
    "provider_health_score",
    "provider_health_status",
    "run_provider_health_probes",
]
