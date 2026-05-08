"""Unit tests for runtime feature flag resolution."""

from __future__ import annotations

import pytest
from ai_server.feature_flags import (
    DELETE_ACTIONS_ENABLED,
    MODEL_TURNS_ENABLED,
    STORAGE_TOOLS_ENABLED,
    FeatureFlagProvider,
    RuntimeFeatureFlags,
    StaticFeatureFlagProvider,
    provider_from_env,
    runtime_flags,
)

pytestmark = pytest.mark.unit


class _FakeProvider:
    """Deterministic provider for adapter-contract tests."""

    def __init__(self, values: dict[str, bool]) -> None:
        self.values = values

    def is_enabled(self, flag_key: str, *, default: bool) -> bool:
        return self.values.get(flag_key, default)


def test_static_provider_uses_safe_default_when_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing environment overrides should preserve safe defaults."""
    monkeypatch.delenv("NIMBUS_FLAG_MODEL_TURNS_ENABLED", raising=False)

    provider = StaticFeatureFlagProvider()

    assert provider.is_enabled(MODEL_TURNS_ENABLED, default=True) is True
    assert provider.is_enabled(MODEL_TURNS_ENABLED, default=False) is False


def test_static_provider_reads_boolean_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static flags should parse explicit boolean environment overrides."""
    monkeypatch.setenv("NIMBUS_FLAG_STORAGE_TOOLS_ENABLED", "off")
    monkeypatch.setenv("NIMBUS_FLAG_DELETE_ACTIONS_ENABLED", "yes")

    provider = StaticFeatureFlagProvider()

    assert provider.is_enabled(STORAGE_TOOLS_ENABLED, default=True) is False
    assert provider.is_enabled(DELETE_ACTIONS_ENABLED, default=False) is True


def test_runtime_flags_uses_internal_provider_contract() -> None:
    """Runtime flag resolution should depend only on the small provider protocol."""
    provider: FeatureFlagProvider = _FakeProvider(
        {
            MODEL_TURNS_ENABLED: False,
            STORAGE_TOOLS_ENABLED: False,
        }
    )

    flags = runtime_flags(provider)

    assert flags == RuntimeFeatureFlags(
        model_turns_enabled=False,
        storage_tools_enabled=False,
        delete_actions_enabled=True,
        attachment_uploads_enabled=True,
        postgres_state_enabled=False,
    )


def test_provider_from_env_falls_back_to_static_without_sdk_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing LaunchDarkly configuration should not break process startup."""
    monkeypatch.delenv("LAUNCHDARKLY_SDK_KEY", raising=False)

    assert isinstance(provider_from_env(), StaticFeatureFlagProvider)
