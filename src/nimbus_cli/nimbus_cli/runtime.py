"""Local Nimbus runtime construction for CLI profiles."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from aws_client_impl.s3_client import S3Client
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
    api_key = os.environ.get(
        "OPENROUTER_API_KEY",
        "",
    ) or secrets.get(
        profile=profile.name,
        kind="openrouter_api_key",
    )
    if not api_key:
        msg = (
            f"profile {profile.name!r} is missing an OpenRouter API key. "
            "Run `nimbus auth local` first or add OPENROUTER_API_KEY to "
            "credentials.env."
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
        storage=None if no_tools else _storage_client(profile, secrets),
        session_dir=Path(profile.session_dir).expanduser()
        if profile.session_dir
        else default_session_dir(),
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        tool_container=profile.storage_container,
        telemetry=runtime_telemetry,
        storage_tools_enabled=not no_tools,
    )


def _storage_client(
    profile: NimbusProfile,
    secrets: NimbusSecrets,
) -> CloudStorageClient | None:
    """Return a cloud-storage client only when the profile pins a container."""
    if not profile.storage_container:
        return None
    access_key_id = os.environ.get("AWS_ACCESS_KEY_ID") or secrets.get(
        profile=profile.name,
        kind="aws_access_key_id",
    )
    secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY") or secrets.get(
        profile=profile.name,
        kind="aws_secret_access_key",
    )
    session_token = os.environ.get("AWS_SESSION_TOKEN") or secrets.get(
        profile=profile.name,
        kind="aws_session_token",
    )
    region = (
        profile.aws_region
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    if bool(access_key_id) != bool(secret_access_key):
        msg = (
            f"profile {profile.name!r} has incomplete AWS credentials. "
            "Run `nimbus auth local --aws` or set AWS_ACCESS_KEY_ID and "
            "AWS_SECRET_ACCESS_KEY in credentials.env."
        )
        raise AIClientConfigError(msg)
    if access_key_id and secret_access_key:
        return S3Client(
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            aws_session_token=session_token,
        )
    return S3Client(region_name=region)
