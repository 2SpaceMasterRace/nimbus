"""OpenTelemetry provider setup for the AWS client service."""

from __future__ import annotations

import os
from typing import Any

import structlog
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

log: Any = structlog.get_logger()

_DEFAULT_ENDPOINT = "https://otlp.nr-data.net:4318"
_DEFAULT_EXPORT_INTERVAL_MS = 30_000


def configure_opentelemetry(service_name: str) -> None:
    """Configure global OpenTelemetry tracer and meter providers.

    Reads ``NEW_RELIC_LICENSE_KEY`` and ``OTEL_EXPORTER_OTLP_ENDPOINT`` from
    the environment. If the license key is absent the function returns early
    and leaves the no-op providers in place so the rest of the application is
    unaffected.

    Args:
        service_name: Logical service name attached to every span and metric
            (visible as the ``service.name`` resource attribute in New Relic).

    """
    license_key = os.environ.get("NEW_RELIC_LICENSE_KEY", "").strip()
    if not license_key:
        log.warning("otel_skipped_no_license_key")
        return

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_ENDPOINT)
    headers = {"api-key": license_key}
    resource = Resource.create({SERVICE_NAME: service_name})

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

    export_interval_ms = int(
        os.environ.get(
            "OTEL_METRIC_EXPORT_INTERVAL_MS",
            str(_DEFAULT_EXPORT_INTERVAL_MS),
        )
    )
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(
                    endpoint=f"{endpoint}/v1/metrics",
                    headers=headers,
                ),
                export_interval_millis=export_interval_ms,
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)
