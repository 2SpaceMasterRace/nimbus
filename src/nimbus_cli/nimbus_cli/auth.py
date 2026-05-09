"""Remote Nimbus HTTP authentication helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from typing import TYPE_CHECKING

from nimbus_cli.config import DEFAULT_REMOTE_PATH, NimbusProfile

if TYPE_CHECKING:
    from collections.abc import Mapping

    from nimbus_cli.secrets import NimbusSecrets

UNSUPPORTED_BEARER_REMOTE_AUTH = (
    "bearer remote profiles are not accepted by Nimbus' signed /ai/chat/turn "
    "endpoint; use --auth hmac with AI_SERVER_SIGNING_SECRET"
)


def encode_json_body(body: Mapping[str, object]) -> bytes:
    """Encode a request body using Nimbus' stable JSON signing shape."""
    return json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def remote_auth_headers(
    *,
    profile: NimbusProfile,
    secrets: NimbusSecrets,
    body: bytes,
    method: str = "POST",
    path: str = DEFAULT_REMOTE_PATH,
) -> dict[str, str]:
    """Return HTTP headers for a remote Nimbus profile."""
    if profile.remote_auth == "bearer":
        raise ValueError(UNSUPPORTED_BEARER_REMOTE_AUTH)
    if profile.remote_auth == "hmac":
        secret = secrets.get(profile=profile.name, kind="remote_signing_secret")
        if not secret:
            msg = f"profile {profile.name!r} is missing an HMAC signing secret"
            raise ValueError(msg)
        return sign_request(body=body, secret=secret, method=method, path=path)
    msg = f"profile {profile.name!r} does not declare remote auth"
    raise ValueError(msg)


def sign_request(  # noqa: PLR0913 - mirrors the server signing contract.
    *,
    body: bytes,
    secret: str,
    method: str = "POST",
    path: str = DEFAULT_REMOTE_PATH,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """Return HMAC headers accepted by ``ai_server`` signed routes."""
    ts = int(time.time()) if timestamp is None else timestamp
    used_nonce = nonce or f"nonce-{uuid.uuid4().hex}"
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = f"{method.upper()}\n{path}\n{ts}\n{used_nonce}\n{body_digest}"
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Nimbus-Timestamp": str(ts),
        "X-Nimbus-Nonce": used_nonce,
        "X-Nimbus-Signature": signature,
    }
