"""BYOK setup validation for Nimbus Slack tenants."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from typing import TYPE_CHECKING

from nimbus_slack.store import TenantConfig

if TYPE_CHECKING:
    from collections.abc import Mapping

_AWS_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d$")
_S3_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


class TenantSetupError(ValueError):
    """Raised when a BYOK setup payload is invalid."""


@dataclass(frozen=True, slots=True)
class TenantSetupInput:
    """Validated setup input submitted through the secure setup page."""

    openrouter_api_key: str
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str
    s3_bucket: str
    s3_prefix: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> TenantSetupInput:
        """Validate raw JSON or form data."""
        prefix = _optional_text(payload, "s3_prefix").strip("/")
        return cls(
            openrouter_api_key=_required_secret(payload, "openrouter_api_key"),
            aws_access_key_id=_required_secret(payload, "aws_access_key_id"),
            aws_secret_access_key=_required_secret(payload, "aws_secret_access_key"),
            aws_region=_required_region(payload, "aws_region"),
            s3_bucket=_required_bucket(payload, "s3_bucket"),
            s3_prefix=prefix,
        )

    def to_tenant_config(self, *, team_id: str) -> TenantConfig:
        """Convert setup input into a persistent tenant configuration."""
        now = datetime.now(UTC)
        return TenantConfig(
            team_id=team_id,
            openrouter_api_key=self.openrouter_api_key,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            aws_region=self.aws_region,
            s3_bucket=self.s3_bucket,
            s3_prefix=self.s3_prefix,
            status="configured",
            updated_at=now,
            validated_at=None,
        )


def render_setup_form(*, team_id: str, token: str) -> str:
    """Render the small HTML setup form for a Slack workspace."""
    escaped_team_id = escape(team_id)
    escaped_token = escape(token, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nimbus Slack setup</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; max-width: 44rem; }}
    label {{ display: block; margin-top: 1rem; font-weight: 600; }}
    input {{ box-sizing: border-box; width: 100%; padding: .7rem; margin-top: .35rem; }}
    button {{ margin-top: 1.25rem; padding: .75rem 1rem; font-weight: 700; }}
  </style>
</head>
<body>
  <h1>Nimbus Slack setup</h1>
  <p>
    Configure BYOK credentials for Slack workspace
    <strong>{escaped_team_id}</strong>.
  </p>
  <form method="post" action="/slack/setup/{escaped_token}">
    <label>OpenRouter API key
      <input name="openrouter_api_key" type="password" autocomplete="off" required>
    </label>
    <label>AWS access key ID
      <input name="aws_access_key_id" type="password" autocomplete="off" required>
    </label>
    <label>AWS secret access key
      <input name="aws_secret_access_key" type="password" autocomplete="off" required>
    </label>
    <label>AWS region
      <input name="aws_region" value="us-east-1" required>
    </label>
    <label>S3 bucket
      <input name="s3_bucket" required>
    </label>
    <label>S3 prefix
      <input name="s3_prefix" placeholder="optional/prefix">
    </label>
    <button type="submit">Save configuration</button>
  </form>
</body>
</html>"""


def render_setup_success(*, team_id: str) -> str:
    """Render a setup completion page."""
    escaped_team_id = escape(team_id)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Nimbus Slack setup complete</title></head>
<body>
  <h1>Nimbus is configured</h1>
  <p>Workspace <strong>{escaped_team_id}</strong> can now use Nimbus Slack actions.</p>
</body>
</html>"""


def _required_secret(payload: Mapping[str, object], key: str) -> str:
    """Return a non-empty secret string without control characters."""
    value = _required_text(payload, key)
    if any(char in value for char in "\r\n\t"):
        msg = f"{key} must not contain control characters."
        raise TenantSetupError(msg)
    return value


def _required_region(payload: Mapping[str, object], key: str) -> str:
    """Return a syntactically valid AWS region."""
    value = _required_text(payload, key)
    if not _AWS_REGION_RE.fullmatch(value):
        msg = "aws_region must look like us-east-1."
        raise TenantSetupError(msg)
    return value


def _required_bucket(payload: Mapping[str, object], key: str) -> str:
    """Return a syntactically valid S3 bucket name."""
    value = _required_text(payload, key)
    if (
        not _S3_BUCKET_RE.fullmatch(value)
        or ".." in value
        or ".-" in value
        or "-." in value
    ):
        msg = "s3_bucket must be a valid S3 bucket name."
        raise TenantSetupError(msg)
    return value


def _required_text(payload: Mapping[str, object], key: str) -> str:
    """Return a required non-empty string."""
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    msg = f"{key} must be a non-empty string."
    raise TenantSetupError(msg)


def _optional_text(payload: Mapping[str, object], key: str) -> str:
    """Return an optional string value."""
    value = payload.get(key, "")
    if isinstance(value, str):
        return value.strip()
    msg = f"{key} must be a string."
    raise TenantSetupError(msg)
