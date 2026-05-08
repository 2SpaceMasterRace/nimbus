"""Feature-flag adapter for production kill switches."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

import ldclient
import structlog
from ldclient.config import Config
from ldclient.context import Context
from nimbus_runtime.postgres import postgres_enabled

log = structlog.get_logger()

MODEL_TURNS_ENABLED = "nimbus.model_turns.enabled"
STORAGE_TOOLS_ENABLED = "nimbus.storage_tools.enabled"
DELETE_ACTIONS_ENABLED = "nimbus.delete_actions.enabled"
ATTACHMENT_UPLOADS_ENABLED = "nimbus.attachment_uploads.enabled"
POSTGRES_STATE_ENABLED = "nimbus.postgres_state.enabled"


class FeatureFlagProvider(Protocol):
    """Small boolean feature-flag contract used by the HTTP adapter."""

    def is_enabled(self, flag_key: str, *, default: bool) -> bool:
        """Return a boolean flag value or the supplied safe default."""


@dataclass(frozen=True, slots=True)
class RuntimeFeatureFlags:
    """Runtime kill-switch values passed into ``NimbusRuntime``."""

    model_turns_enabled: bool = True
    storage_tools_enabled: bool = True
    delete_actions_enabled: bool = True
    attachment_uploads_enabled: bool = True
    postgres_state_enabled: bool = False


class StaticFeatureFlagProvider:
    """Environment/default-backed feature flags for local and staging usage."""

    def is_enabled(self, flag_key: str, *, default: bool) -> bool:
        """Return a boolean value from ``NIMBUS_FLAG_*`` or the default."""
        env_key = (
            "NIMBUS_FLAG_" + flag_key.replace("nimbus.", "").replace(".", "_").upper()
        )
        raw = os.environ.get(env_key, "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return default


class LaunchDarklyFeatureFlagProvider:
    """LaunchDarkly-backed feature flag provider with safe fallbacks."""

    def __init__(self, sdk_key: str) -> None:
        """Initialize the LaunchDarkly SDK client."""
        ldclient.set_config(Config(sdk_key))
        self._client = ldclient.get()
        self._context = Context.builder("nimbus-production").kind("service").build()

    def is_enabled(self, flag_key: str, *, default: bool) -> bool:
        """Return a LaunchDarkly boolean variation or a safe default."""
        try:
            value = self._client.variation(flag_key, self._context, default)
        except Exception:  # noqa: BLE001 - feature flags must fail open to defaults
            log.warning("launchdarkly_flag_read_failed", flag_key=flag_key)
            return default
        return bool(value)


@lru_cache(maxsize=1)
def provider_from_env() -> FeatureFlagProvider:
    """Return LaunchDarkly provider when configured, otherwise static defaults."""
    sdk_key = os.environ.get("LAUNCHDARKLY_SDK_KEY", "").strip()
    if not sdk_key:
        return StaticFeatureFlagProvider()
    try:
        return LaunchDarklyFeatureFlagProvider(sdk_key)
    except Exception:  # noqa: BLE001 - missing/failed SDK should not break startup
        log.warning("launchdarkly_provider_unavailable")
        return StaticFeatureFlagProvider()


def runtime_flags(provider: FeatureFlagProvider | None = None) -> RuntimeFeatureFlags:
    """Resolve all runtime feature flags for one request/runtime instance."""
    resolved_provider = provider or provider_from_env()
    return RuntimeFeatureFlags(
        model_turns_enabled=resolved_provider.is_enabled(
            MODEL_TURNS_ENABLED,
            default=True,
        ),
        storage_tools_enabled=resolved_provider.is_enabled(
            STORAGE_TOOLS_ENABLED,
            default=True,
        ),
        delete_actions_enabled=resolved_provider.is_enabled(
            DELETE_ACTIONS_ENABLED,
            default=True,
        ),
        attachment_uploads_enabled=resolved_provider.is_enabled(
            ATTACHMENT_UPLOADS_ENABLED,
            default=True,
        ),
        postgres_state_enabled=resolved_provider.is_enabled(
            POSTGRES_STATE_ENABLED,
            default=postgres_enabled(),
        ),
    )
