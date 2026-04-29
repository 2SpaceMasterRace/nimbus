"""Local Nimbus runtime construction for CLI profiles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from aws_client_impl.s3_client import get_client_impl
from openrouter_ai_client_impl.config import DEFAULT_SYSTEM_PROMPT, OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient

from ai_client_api import AIClientConfigError
from nimbus_cli.config import NimbusProfile, default_session_dir
from nimbus_runtime import NimbusRuntime, runtime_telemetry

if TYPE_CHECKING:
    from cloud_storage_api import CloudStorageClient

    from nimbus_cli.secrets import NimbusSecrets


def build_local_runtime(
    *,
    profile: NimbusProfile,
    secrets: NimbusSecrets,
    no_tools: bool = False,
) -> NimbusRuntime:
    """Build an in-process runtime for a local CLI profile."""
    api_key = secrets.get(
        profile=profile.name,
        kind="openrouter_api_key",
    ) or os.environ.get(
        "OPENROUTER_API_KEY",
        "",
    )
    if not api_key:
        msg = (
            f"profile {profile.name!r} is missing an OpenRouter API key. "
            "Run `nimbus setup local` first."
        )
        raise AIClientConfigError(msg)
    config = OpenRouterConfig(
        api_key=api_key,
        model=profile.model,
        fallback_model=profile.fallback_model,
        base_url=profile.openrouter_base_url,
        system_prompt=DEFAULT_SYSTEM_PROMPT,
    )
    return NimbusRuntime(
        ai_client=OpenRouterClient(config),
        storage=None if no_tools else _storage_client(profile),
        session_dir=Path(profile.session_dir).expanduser()
        if profile.session_dir
        else default_session_dir(),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        tool_container=profile.storage_container,
        telemetry=runtime_telemetry,
        storage_tools_enabled=not no_tools,
    )


def _storage_client(profile: NimbusProfile) -> CloudStorageClient | None:
    """Return a cloud-storage client only when the profile pins a container."""
    if not profile.storage_container:
        return None
    return get_client_impl()
