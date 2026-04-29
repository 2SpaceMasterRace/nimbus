"""Tests for nimbus_cli.runtime."""

from __future__ import annotations

from pathlib import Path

import pytest
from nimbus_cli.config import NimbusProfile
from nimbus_cli.secrets import NimbusSecrets

pytestmark = pytest.mark.unit


def test_build_local_runtime_raises_when_api_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing OpenRouter API key should produce a clear error message."""
    from ai_client_api import AIClientConfigError
    from nimbus_cli.runtime import build_local_runtime

    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    profile = NimbusProfile(name="local", mode="local")
    secrets = NimbusSecrets(tmp_path)

    with pytest.raises(AIClientConfigError, match="API key"):
        build_local_runtime(profile=profile, secrets=secrets)


def test_build_local_runtime_uses_env_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENROUTER_API_KEY env var is accepted when no keyring secret is present."""
    import nimbus_cli.runtime as rt

    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-key")
    monkeypatch.setattr(rt, "OpenRouterClient", lambda cfg: object())
    monkeypatch.setattr(rt, "NimbusRuntime", lambda **kw: object())

    profile = NimbusProfile(name="local", mode="local", session_dir=str(tmp_path))
    secrets = NimbusSecrets(tmp_path)
    result = rt.build_local_runtime(profile=profile, secrets=secrets)
    assert result is not None
