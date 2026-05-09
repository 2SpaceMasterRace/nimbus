"""Tests for nimbus_cli.runtime."""

from __future__ import annotations

from pathlib import Path

import nimbus_cli.runtime as rt
import pytest
from nimbus_cli.config import NimbusProfile
from nimbus_cli.secrets import NimbusSecrets

from ai_client_api import AIClientConfigError

pytestmark = pytest.mark.unit


class _FakeOpenRouterClient:
    """Tiny OpenRouter stand-in that records the supplied config."""

    def __init__(self, config: object) -> None:
        self.config = config


class _FakeRuntime:
    """Tiny runtime stand-in that records construction arguments."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def test_build_local_runtime_raises_when_api_key_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing OpenRouter API key should produce a clear error message."""
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    profile = NimbusProfile(name="local", mode="local")
    secrets = NimbusSecrets(tmp_path)

    with pytest.raises(AIClientConfigError, match="API key"):
        rt.build_local_runtime(profile=profile, secrets=secrets)


def test_build_local_runtime_uses_env_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OPENROUTER_API_KEY env var is accepted when no keyring secret is present."""
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-key")
    monkeypatch.setattr(rt, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(rt, "NimbusRuntime", _FakeRuntime)

    profile = NimbusProfile(name="local", mode="local", session_dir=str(tmp_path))
    secrets = NimbusSecrets(tmp_path)
    result = rt.build_local_runtime(profile=profile, secrets=secrets)
    assert result is not None


def test_build_local_runtime_prefers_env_over_stored_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Process env should override stored profile secrets at runtime."""
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    monkeypatch.setattr(rt, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(rt, "NimbusRuntime", _FakeRuntime)

    profile = NimbusProfile(name="local", mode="local", session_dir=str(tmp_path))
    secrets = NimbusSecrets(tmp_path)
    secrets.set(profile="local", kind="openrouter_api_key", value="sk-stored")

    result = rt.build_local_runtime(profile=profile, secrets=secrets)

    assert isinstance(result, _FakeRuntime)
    ai_client = result.kwargs["ai_client"]
    assert isinstance(ai_client, _FakeOpenRouterClient)
    assert ai_client.config.api_key == "sk-env"


def test_build_local_runtime_uses_stored_aws_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stored AWS credentials should be passed to the local S3 client."""

    class _FakeS3Client:
        """S3 stand-in that captures constructor arguments."""

        def __init__(
            self,
            region_name: str,
            *,
            aws_access_key_id: str | None = None,
            aws_secret_access_key: str | None = None,
            aws_session_token: str | None = None,
        ) -> None:
            self.region_name = region_name
            self.aws_access_key_id = aws_access_key_id
            self.aws_secret_access_key = aws_secret_access_key
            self.aws_session_token = aws_session_token

    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(rt, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(rt, "NimbusRuntime", _FakeRuntime)
    monkeypatch.setattr(rt, "S3Client", _FakeS3Client)

    profile = NimbusProfile(
        name="local",
        mode="local",
        storage_container="bucket",
        aws_region="eu-west-1",
        session_dir=str(tmp_path),
    )
    secrets = NimbusSecrets(tmp_path)
    secrets.set(profile="local", kind="openrouter_api_key", value="sk-test")
    secrets.set(profile="local", kind="aws_access_key_id", value="AKIA_TEST")
    secrets.set(profile="local", kind="aws_secret_access_key", value="secret")
    secrets.set(profile="local", kind="aws_session_token", value="token")

    result = rt.build_local_runtime(profile=profile, secrets=secrets)

    assert isinstance(result, _FakeRuntime)
    storage = result.kwargs["storage"]
    assert isinstance(storage, _FakeS3Client)
    assert storage.region_name == "eu-west-1"
    assert storage.aws_access_key_id == "AKIA_TEST"
    assert storage.aws_secret_access_key == "secret"
    assert storage.aws_session_token == "token"


def test_build_local_runtime_prefers_env_over_stored_aws_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AWS environment variables should override stored local profile secrets."""

    class _FakeS3Client:
        """S3 stand-in that captures constructor arguments."""

        def __init__(
            self,
            region_name: str,
            *,
            aws_access_key_id: str | None = None,
            aws_secret_access_key: str | None = None,
            aws_session_token: str | None = None,
        ) -> None:
            self.region_name = region_name
            self.aws_access_key_id = aws_access_key_id
            self.aws_secret_access_key = aws_secret_access_key
            self.aws_session_token = aws_session_token

    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_ENV")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "env-secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "env-token")
    monkeypatch.setattr(rt, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(rt, "NimbusRuntime", _FakeRuntime)
    monkeypatch.setattr(rt, "S3Client", _FakeS3Client)

    profile = NimbusProfile(
        name="local",
        mode="local",
        storage_container="bucket",
        aws_region="eu-west-1",
        session_dir=str(tmp_path),
    )
    secrets = NimbusSecrets(tmp_path)
    secrets.set(profile="local", kind="aws_access_key_id", value="AKIA_STORED")
    secrets.set(profile="local", kind="aws_secret_access_key", value="stored-secret")
    secrets.set(profile="local", kind="aws_session_token", value="stored-token")

    result = rt.build_local_runtime(profile=profile, secrets=secrets)

    assert isinstance(result, _FakeRuntime)
    storage = result.kwargs["storage"]
    assert isinstance(storage, _FakeS3Client)
    assert storage.aws_access_key_id == "AKIA_ENV"
    assert storage.aws_secret_access_key == "env-secret"
    assert storage.aws_session_token == "env-token"


def test_storage_client_returns_none_when_no_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_storage_client returns None when profile has no storage_container."""
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(rt, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(rt, "NimbusRuntime", _FakeRuntime)

    profile = NimbusProfile(name="local", mode="local", session_dir=str(tmp_path))
    secrets = NimbusSecrets(tmp_path)

    result = rt.build_local_runtime(profile=profile, secrets=secrets)
    assert result.kwargs["storage"] is None


def test_build_local_runtime_no_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_local_runtime with no_tools=True should pass storage=None."""
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(rt, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(rt, "NimbusRuntime", _FakeRuntime)

    profile = NimbusProfile(name="local", mode="local", session_dir=str(tmp_path))
    secrets = NimbusSecrets(tmp_path)

    result = rt.build_local_runtime(profile=profile, secrets=secrets, no_tools=True)
    assert result.kwargs["storage"] is None
    assert result.kwargs["storage_tools_enabled"] is False


def test_storage_client_rejects_incomplete_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Incomplete AWS credentials should raise AIClientConfigError."""
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr(rt, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(rt, "NimbusRuntime", _FakeRuntime)

    profile = NimbusProfile(
        name="local",
        mode="local",
        storage_container="bucket",
        session_dir=str(tmp_path),
    )
    secrets = NimbusSecrets(tmp_path)
    secrets.set(profile="local", kind="aws_access_key_id", value="AKIA_TEST")

    with pytest.raises(AIClientConfigError, match="incomplete AWS credentials"):
        rt.build_local_runtime(profile=profile, secrets=secrets)


def test_storage_client_default_region(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default region should be us-east-1 when no region is specified."""

    class _FakeS3Client:
        def __init__(
            self,
            region_name: str,
            *,
            aws_access_key_id: str | None = None,
            aws_secret_access_key: str | None = None,
            aws_session_token: str | None = None,
        ) -> None:
            self.region_name = region_name

    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(rt, "OpenRouterClient", _FakeOpenRouterClient)
    monkeypatch.setattr(rt, "NimbusRuntime", _FakeRuntime)
    monkeypatch.setattr(rt, "S3Client", _FakeS3Client)

    profile = NimbusProfile(
        name="local",
        mode="local",
        storage_container="bucket",
        session_dir=str(tmp_path),
    )
    secrets = NimbusSecrets(tmp_path)
    secrets.set(profile="local", kind="openrouter_api_key", value="sk-test")

    result = rt.build_local_runtime(profile=profile, secrets=secrets)
    storage = result.kwargs["storage"]
    assert storage.region_name == "us-east-1"
