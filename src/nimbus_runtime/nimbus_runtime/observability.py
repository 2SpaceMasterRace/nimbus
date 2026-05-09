"""Shared observability bootstrap for Nimbus services."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import logfire
import sentry_sdk
import structlog
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import DEPLOYMENT_ENVIRONMENT, SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from sentry_sdk.integrations.fastapi import FastApiIntegration

if TYPE_CHECKING:
    from fastapi import FastAPI

log: Any = structlog.get_logger()

_DEFAULT_NEW_RELIC_OTLP_ENDPOINT = "https://otlp.nr-data.net:4318"
_DEFAULT_EXPORT_INTERVAL_MS = 30_000
_configured_structlog = False
_configured_sentry = False
_configured_otel = False
_configured_logfire = False
_instrumented_httpx = False
_instrumented_fastapi_apps: set[int] = set()


def configure_observability(service_name: str, *, app: FastAPI | None = None) -> None:
    """Configure logging, tracing, metrics, and error reporting for one service.

    The naked deployment is still one process with stdout logs. When the
    relevant env vars are present, this function adds Sentry exception capture,
    OTLP traces/metrics for New Relic or any OTLP backend, Logfire support, and
    FastAPI/HTTPX instrumentation. It is intentionally idempotent because tests
    import multiple Nimbus apps in one Python process.
    """
    environment = os.environ.get("ENVIRONMENT", "development").strip() or "development"
    _configure_structlog()
    _configure_sentry(service_name=service_name, environment=environment)
    _configure_otel(service_name=service_name, environment=environment)
    _configure_logfire(service_name=service_name, environment=environment)
    if app is not None:
        _instrument_fastapi(app)
    _instrument_httpx()


def _add_trace_context(
    _logger: object,
    _method: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Inject the current OTel span identity into every log record.

    New Relic correlates logs with traces by ``trace.id`` and ``span.id``. The
    fields are added only when a span is recording so quiet code paths do not
    pollute logs with empty IDs.
    """
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if span_context.is_valid:
        event_dict.setdefault("trace_id", f"{span_context.trace_id:032x}")
        event_dict.setdefault("span_id", f"{span_context.span_id:016x}")
    return event_dict


def _configure_structlog() -> None:
    """Configure JSON structured logs once per process."""
    global _configured_structlog  # noqa: PLW0603
    if _configured_structlog:
        return
    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_trace_context,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(_log_level()),
        cache_logger_on_first_use=True,
    )
    _configured_structlog = True


def _configure_sentry(*, service_name: str, environment: str) -> None:
    """Configure Sentry if a DSN is present."""
    global _configured_sentry  # noqa: PLW0603
    if _configured_sentry:
        return
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        log.debug("sentry_skipped_no_dsn", service_name=service_name)
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("RELEASE"),
        traces_sample_rate=_float_env("SENTRY_TRACES_SAMPLE_RATE", default=0.1),
        profiles_sample_rate=_float_env("SENTRY_PROFILES_SAMPLE_RATE", default=0.0),
        integrations=[FastApiIntegration()],
        send_default_pii=False,
    )
    sentry_sdk.set_tag("service.name", service_name)
    _configured_sentry = True
    log.info("sentry_configured", service_name=service_name)


def _configure_otel(*, service_name: str, environment: str) -> None:
    """Configure OTLP traces and metrics when exporter credentials exist."""
    global _configured_otel  # noqa: PLW0603
    if _configured_otel:
        return
    headers = _otel_headers()
    if not headers:
        log.debug("otel_skipped_no_exporter_headers", service_name=service_name)
        return
    endpoint = _otel_endpoint()
    resource = Resource.create(
        {
            SERVICE_NAME: service_name,
            DEPLOYMENT_ENVIRONMENT: environment,
        }
    )
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{endpoint}/v1/traces",
                headers=headers,
            )
        )
    )
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=f"{endpoint}/v1/metrics",
                    headers=headers,
                ),
                export_interval_millis=_int_env(
                    "OTEL_METRIC_EXPORT_INTERVAL_MS",
                    default=_DEFAULT_EXPORT_INTERVAL_MS,
                ),
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)
    _configured_otel = True
    log.info("otel_configured", service_name=service_name, endpoint=endpoint)


def _configure_logfire(*, service_name: str, environment: str) -> None:
    """Configure Pydantic Logfire when a token or explicit setting is present."""
    global _configured_logfire  # noqa: PLW0603
    if _configured_logfire:
        return
    token = os.environ.get("LOGFIRE_TOKEN", "").strip()
    send_to_logfire = os.environ.get("LOGFIRE_SEND_TO_LOGFIRE", "").strip()
    if not token and not send_to_logfire:
        log.debug("logfire_skipped_no_token", service_name=service_name)
        return
    try:
        logfire.configure(
            token=token or None,
            service_name=service_name,
            environment=environment,
            send_to_logfire="if-token-present" if not send_to_logfire else None,
            console=False,
            inspect_arguments=False,
        )
        logfire.instrument_pydantic()
    except RuntimeError as exc:
        log.warning(
            "logfire_configuration_failed",
            service_name=service_name,
            error=str(exc),
        )
        return
    _configured_logfire = True
    log.info("logfire_configured", service_name=service_name)


def _instrument_fastapi(app: FastAPI) -> None:
    """Instrument one FastAPI app once."""
    app_id = id(app)
    if app_id in _instrumented_fastapi_apps:
        return
    if _configured_logfire:
        logfire.instrument_fastapi(app)
    else:
        FastAPIInstrumentor.instrument_app(app)
    _instrumented_fastapi_apps.add(app_id)


def _instrument_httpx() -> None:
    """Instrument process-wide HTTPX clients once."""
    global _instrumented_httpx  # noqa: PLW0603
    if _instrumented_httpx:
        return
    if _configured_logfire:
        logfire.instrument_httpx(capture_all=True)
    else:
        HTTPXClientInstrumentor().instrument()
    _instrumented_httpx = True


def _otel_endpoint() -> str:
    """Return a normalized OTLP/HTTP endpoint root without signal suffix."""
    raw = (
        os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        or _DEFAULT_NEW_RELIC_OTLP_ENDPOINT
    )
    endpoint = raw.removesuffix("/v1/traces").removesuffix("/v1/metrics")
    return endpoint.rstrip("/")


def _otel_headers() -> dict[str, str]:
    """Return OTLP exporter headers from generic env or New Relic env."""
    headers: dict[str, str] = {}
    raw_headers = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "").strip()
    if raw_headers:
        for item in raw_headers.split(","):
            key, separator, value = item.partition("=")
            if separator and key.strip() and value.strip():
                headers[key.strip()] = value.strip()
    license_key = os.environ.get("NEW_RELIC_LICENSE_KEY", "").strip()
    if license_key:
        headers["api-key"] = license_key
    return headers


def _log_level() -> int:
    """Return the configured stdlib-compatible log level."""
    raw_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw_level, logging.INFO)


def _float_env(name: str, *, default: float) -> float:
    """Parse a bounded float env var."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("invalid_float_env", name=name, value=raw, default=default)
        return default
    return min(max(value, 0.0), 1.0)


def _int_env(name: str, *, default: int) -> int:
    """Parse a positive integer env var."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        log.warning("invalid_int_env", name=name, value=raw, default=default)
        return default
    return value if value > 0 else default
