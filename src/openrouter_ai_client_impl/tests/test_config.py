"""Tests for :class:`OpenRouterConfig` environment loading."""

from __future__ import annotations

import pytest
from openrouter_ai_client_impl.config import (
    DEFAULT_BASE_URL,
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    OpenRouterConfig,
)

from ai_client_api import AIClientConfigError

pytestmark = pytest.mark.unit


def test_from_env_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``from_env`` must raise when the API key env var is missing."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(AIClientConfigError, match="OPENROUTER_API_KEY"):
        OpenRouterConfig.from_env()


def test_from_env_uses_defaults_when_only_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``OPENROUTER_API_KEY`` should be required; the rest use defaults."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_FALLBACK_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.delenv("OPENROUTER_TIMEOUT", raising=False)

    config = OpenRouterConfig.from_env()

    assert config.api_key == "sk-test"
    assert config.model == DEFAULT_MODEL
    assert config.fallback_model == DEFAULT_FALLBACK_MODEL
    assert config.base_url == DEFAULT_BASE_URL
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS


def test_from_env_reads_optional_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every optional env var should override the matching default."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_MODEL", "custom/model:free")
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL", "other/model:free")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://example.test/api/v1")
    monkeypatch.setenv("OPENROUTER_TIMEOUT", "12.5")

    config = OpenRouterConfig.from_env()

    assert config.model == "custom/model:free"
    assert config.fallback_model == "other/model:free"
    assert config.base_url == "https://example.test/api/v1"
    assert config.timeout_seconds == pytest.approx(12.5)


def test_from_env_treats_blank_fallback_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blank fallback disables the fallback model entirely."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_FALLBACK_MODEL", "  ")

    config = OpenRouterConfig.from_env()
    assert config.fallback_model is None


def test_from_env_ignores_invalid_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-numeric ``OPENROUTER_TIMEOUT`` values fall back to the default."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENROUTER_TIMEOUT", "not-a-number")

    config = OpenRouterConfig.from_env()
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
