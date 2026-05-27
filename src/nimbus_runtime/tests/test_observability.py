"""Tests for shared Nimbus observability bootstrap."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from nimbus_runtime.observability import (
    _add_trace_context,
    _float_env,
    _int_env,
    _log_level,
    _otel_endpoint,
    _otel_headers,
    configure_observability,
)

pytestmark = pytest.mark.unit


def _reset_globals() -> None:
    """Reset the module-level sentinel flags between tests."""
    import nimbus_runtime.observability as m

    m._configured_structlog = False
    m._configured_sentry = False
    m._configured_otel = False
    m._configured_logfire = False
    m._instrumented_httpx = False
    m._instrumented_fastapi_apps.clear()


# -- helper: _log_level -------------------------------------------------------


class TestAddTraceContext:
    def test_with_valid_span_context(self) -> None:
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-span") as _span:
            event_dict: dict[str, str] = {}
            result = _add_trace_context(None, None, event_dict)
            assert "trace_id" in result
            assert "span_id" in result

    def test_without_valid_span_context(self) -> None:
        event_dict: dict[str, str] = {}
        result = _add_trace_context(None, None, event_dict)
        assert result == {}


class TestLogLevel:
    @pytest.mark.parametrize(
        ("env_val", "expected"),
        [
            ("DEBUG", 10),
            ("INFO", 20),
            ("WARNING", 30),
            ("ERROR", 40),
            ("", 20),
            ("INVALID", 20),
        ],
    )
    def test_log_level(
        self, monkeypatch: pytest.MonkeyPatch, env_val: str, expected: int
    ) -> None:
        monkeypatch.setenv("LOG_LEVEL", env_val)
        assert _log_level() == expected


# -- helper: _otel_endpoint ---------------------------------------------------


class TestOtelEndpoint:
    def test_default_endpoint_when_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        result = _otel_endpoint()
        assert result == "https://otlp.nr-data.net:4318"

    def test_custom_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "https://my-otlp.example.com:4318"
        )
        assert _otel_endpoint() == "https://my-otlp.example.com:4318"

    def test_strips_traces_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.com/v1/traces"
        )
        assert _otel_endpoint() == "https://example.com"

    def test_strips_metrics_suffix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.com/v1/metrics"
        )
        assert _otel_endpoint() == "https://example.com"

    def test_strips_trailing_slash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "https://example.com/")
        assert _otel_endpoint() == "https://example.com"


# -- helper: _otel_headers ----------------------------------------------------


class TestOtelHeaders:
    def test_no_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
        monkeypatch.delenv("NEW_RELIC_LICENSE_KEY", raising=False)
        assert _otel_headers() == {}

    def test_parses_csv_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "key1=val1,key2=val2")
        monkeypatch.delenv("NEW_RELIC_LICENSE_KEY", raising=False)
        assert _otel_headers() == {"key1": "val1", "key2": "val2"}

    def test_skips_malformed_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_HEADERS", "good=val,bad,=emptykey,onlykey="
        )
        monkeypatch.delenv("NEW_RELIC_LICENSE_KEY", raising=False)
        assert _otel_headers() == {"good": "val"}

    def test_adds_new_relic_license_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_HEADERS", raising=False)
        monkeypatch.setenv("NEW_RELIC_LICENSE_KEY", "nr-license-123")
        assert _otel_headers() == {"api-key": "nr-license-123"}

    def test_merges_with_license_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "existing=header")
        monkeypatch.setenv("NEW_RELIC_LICENSE_KEY", "nr-license-456")
        assert _otel_headers() == {"existing": "header", "api-key": "nr-license-456"}


# -- helper: _float_env -------------------------------------------------------


class TestFloatEnv:
    def test_not_set_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_FLOAT", raising=False)
        assert _float_env("TEST_FLOAT", default=0.5) == 0.5

    def test_valid_float(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_FLOAT", "0.75")
        assert _float_env("TEST_FLOAT", default=0.5) == 0.75

    def test_invalid_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_FLOAT", "not-a-number")
        assert _float_env("TEST_FLOAT", default=0.5) == 0.5

    def test_clamps_to_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_FLOAT", "-1.0")
        assert _float_env("TEST_FLOAT", default=0.5) == 0.0

    def test_clamps_to_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_FLOAT", "2.5")
        assert _float_env("TEST_FLOAT", default=0.5) == 1.0


# -- helper: _int_env ---------------------------------------------------------


class TestIntEnv:
    def test_not_set_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_INT", raising=False)
        assert _int_env("TEST_INT", default=42) == 42

    def test_valid_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT", "100")
        assert _int_env("TEST_INT", default=42) == 100

    def test_invalid_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_INT", "not-an-int")
        assert _int_env("TEST_INT", default=42) == 42

    def test_non_positive_returns_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TEST_INT", "0")
        assert _int_env("TEST_INT", default=42) == 42


# -- configure_observability with Sentry DSN ----------------------------------


class TestConfigureObservability:
    def test_basic_without_vendor_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        configure_observability("nimbus-test")

    def test_with_sentry_dsn(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example.com/1")
        for name in (
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with patch("nimbus_runtime.observability.sentry_sdk.init") as mock_init:
            configure_observability("nimbus-test")
            mock_init.assert_called_once()

    def test_with_sentry_dsn_and_release(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example.com/1")
        monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")
        for name in (
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with patch("nimbus_runtime.observability.sentry_sdk.init") as mock_init:
            configure_observability("nimbus-test")
            mock_init.assert_called_once()

    def test_idempotent_sentry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example.com/1")
        for name in (
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with patch("nimbus_runtime.observability.sentry_sdk.init") as mock_init:
            configure_observability("nimbus-test")
            configure_observability("nimbus-test")
            mock_init.assert_called_once()

    def test_with_otel_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "api-key=test-key")
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with (
            patch("nimbus_runtime.observability.TracerProvider"),
            patch("nimbus_runtime.observability.BatchSpanProcessor"),
            patch("nimbus_runtime.observability.OTLPSpanExporter"),
            patch("nimbus_runtime.observability.MeterProvider"),
            patch("nimbus_runtime.observability.PeriodicExportingMetricReader"),
            patch("nimbus_runtime.observability.OTLPMetricExporter"),
            patch("nimbus_runtime.observability.trace.set_tracer_provider"),
            patch("nimbus_runtime.observability.metrics.set_meter_provider"),
        ):
            configure_observability("nimbus-test")

    def test_with_logfire_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        monkeypatch.setenv("LOGFIRE_TOKEN", "lgt_abc123")
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with patch("nimbus_runtime.observability.logfire.configure") as mock_cfg:
            configure_observability("nimbus-test")
            mock_cfg.assert_called_once()

    def test_with_logfire_send_to_logfire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _reset_globals()
        monkeypatch.setenv("LOGFIRE_SEND_TO_LOGFIRE", "true")
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_TOKEN",
        ):
            monkeypatch.delenv(name, raising=False)
        with patch("nimbus_runtime.observability.logfire.configure") as mock_cfg:
            configure_observability("nimbus-test")
            mock_cfg.assert_called_once()

    def test_logfire_runtime_error_handled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _reset_globals()
        monkeypatch.setenv("LOGFIRE_TOKEN", "lgt_abc123")
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with patch(
            "nimbus_runtime.observability.logfire.configure",
            side_effect=RuntimeError("already configured"),
        ):
            configure_observability("nimbus-test")

    def test_with_app_and_logfire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        monkeypatch.setenv("LOGFIRE_TOKEN", "lgt_abc123")
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        app = MagicMock()
        with (
            patch("nimbus_runtime.observability.logfire.configure"),
            patch("nimbus_runtime.observability.logfire.instrument_pydantic"),
            patch(
                "nimbus_runtime.observability.logfire.instrument_fastapi"
            ) as mock_inst,
        ):
            configure_observability("nimbus-test", app=app)
            mock_inst.assert_called_once_with(app)

    def test_with_app_no_logfire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        app = MagicMock()
        with (
            patch(
                "nimbus_runtime.observability.FastAPIInstrumentor.instrument_app"
            ) as mock_inst,
        ):
            configure_observability("nimbus-test", app=app)
            mock_inst.assert_called_once_with(app)

    def test_instrument_httpx_with_logfire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _reset_globals()
        monkeypatch.setenv("LOGFIRE_TOKEN", "lgt_abc123")
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with (
            patch("nimbus_runtime.observability.logfire.configure"),
            patch("nimbus_runtime.observability.logfire.instrument_pydantic"),
            patch(
                "nimbus_runtime.observability.logfire.instrument_httpx"
            ) as mock_httpx,
        ):
            configure_observability("nimbus-test")
            mock_httpx.assert_called_once_with(capture_all=True)

    def test_instrument_httpx_without_logfire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _reset_globals()
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with (
            patch("nimbus_runtime.observability.HTTPXClientInstrumentor") as mock_inst,
        ):
            configure_observability("nimbus-test")
            mock_inst.return_value.instrument.assert_called_once()

    def test_otel_idempotent_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "api-key=test")
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with (
            patch("nimbus_runtime.observability.TracerProvider"),
            patch("nimbus_runtime.observability.BatchSpanProcessor"),
            patch("nimbus_runtime.observability.OTLPSpanExporter"),
            patch("nimbus_runtime.observability.MeterProvider"),
            patch("nimbus_runtime.observability.PeriodicExportingMetricReader"),
            patch("nimbus_runtime.observability.OTLPMetricExporter"),
            patch("nimbus_runtime.observability.trace.set_tracer_provider"),
            patch("nimbus_runtime.observability.metrics.set_meter_provider"),
        ):
            configure_observability("nimbus-test")
            configure_observability("nimbus-test")

    def test_logfire_idempotent_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        monkeypatch.setenv("LOGFIRE_TOKEN", "lgt_abc")
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        with (
            patch("nimbus_runtime.observability.logfire.configure"),
            patch("nimbus_runtime.observability.logfire.instrument_pydantic"),
        ):
            configure_observability("nimbus-test")
            configure_observability("nimbus-test")

    def test_fastapi_idempotent_guard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _reset_globals()
        for name in (
            "SENTRY_DSN",
            "NEW_RELIC_LICENSE_KEY",
            "OTEL_EXPORTER_OTLP_HEADERS",
            "LOGFIRE_TOKEN",
            "LOGFIRE_SEND_TO_LOGFIRE",
        ):
            monkeypatch.delenv(name, raising=False)
        app = MagicMock()
        with (
            patch("nimbus_runtime.observability.FastAPIInstrumentor.instrument_app"),
        ):
            configure_observability("nimbus-test", app=app)
            configure_observability("nimbus-test", app=app)
