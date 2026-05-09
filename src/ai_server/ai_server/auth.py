"""Authentication dependencies for AI server routes.

Two auth modes currently exist:

- ``require_api_key``: shared-secret auth used by the session-management
  endpoints.
- ``require_signed_service_request``: stronger wrapper-to-service auth for the
  canonical chat-wrapper boundary. The wrapper signs the HTTP request with an
  HMAC over method, path, timestamp, nonce, and body digest.

The signed-request path exists so the future Slack/Discord wrapper can call the
Nimbus AI service without exposing a simple long-lived bearer secret on the
wire for every request.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from typing import Annotated

import structlog
from fastapi import Header, HTTPException, Request, status

from ai_server.request_state import put_state_if_absent
from nimbus_runtime import runtime_telemetry

_SIGNED_REQUEST_MAX_AGE_SECONDS = 300
_SIGNED_REQUEST_MAX_BODY_BYTES = int(
    os.environ.get("AI_SERVER_SIGNED_REQUEST_MAX_BODY_BYTES", str(512 * 1024))
)
_MAX_TIMESTAMP_HEADER_BYTES = 16
_MAX_NONCE_HEADER_BYTES = 128
_SIGNATURE_HEX_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_NONCE_STATE_NAMESPACE = "signed_request_nonces"
_seen_nonces: dict[str, float] = {}
_seen_nonces_lock = threading.Lock()
log = structlog.get_logger()


def _hmac_secret() -> str:
    return os.environ.get("AI_SERVER_SIGNING_SECRET", "").strip()


def _cleanup_seen_nonces(now: float) -> None:
    expired = [nonce for nonce, expiry in _seen_nonces.items() if expiry <= now]
    for nonce in expired:
        del _seen_nonces[nonce]


def _build_signature_payload(
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> bytes:
    body_digest = hashlib.sha256(body).hexdigest()
    canonical = f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_digest}"
    return canonical.encode("utf-8")


def _expected_signature(*, secret: str, payload: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """Require a valid ``AI_SERVER_API_KEY`` in the ``X-API-Key`` header.

    Returns the validated key on success.

    Raises:
        HTTPException 503: ``AI_SERVER_API_KEY`` is not configured in the
            process environment.
        HTTPException 401: The supplied key is missing or does not match.

    """
    expected = os.environ.get("AI_SERVER_API_KEY", "").strip()
    if not expected:
        log.error("api_key_auth_unconfigured")
        runtime_telemetry.record_auth_result(
            mechanism="api_key",
            result="failure",
            reason="unconfigured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI_SERVER_API_KEY is not configured on this server.",
        )
    if x_api_key is None or not hmac.compare_digest(x_api_key, expected):
        log.warning("api_key_auth_failed")
        runtime_telemetry.record_auth_result(
            mechanism="api_key",
            result="failure",
            reason="invalid_or_missing",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "API-Key"},
        )
    runtime_telemetry.record_auth_result(
        mechanism="api_key",
        result="success",
        reason="matched",
    )
    return x_api_key


async def require_signed_service_request(  # noqa: C901, PLR0912, PLR0915 - fail-closed auth checks are explicit
    request: Request,
    x_nimbus_timestamp: Annotated[
        str | None, Header(alias="X-Nimbus-Timestamp")
    ] = None,
    x_nimbus_nonce: Annotated[str | None, Header(alias="X-Nimbus-Nonce")] = None,
    x_nimbus_signature: Annotated[
        str | None, Header(alias="X-Nimbus-Signature")
    ] = None,
) -> str:
    r"""Require a valid signed wrapper-to-service request.

    Headers:
        X-Nimbus-Timestamp: Unix timestamp in whole seconds.
        X-Nimbus-Nonce: Single-use caller-generated nonce.
        X-Nimbus-Signature: Hex HMAC-SHA256 signature of the canonical payload.

    The canonical payload is:

    ``METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + SHA256(BODY)``

    Returns:
        The validated signature string.

    Raises:
        HTTPException 503: signing secret not configured.
        HTTPException 401: missing/invalid headers, stale timestamp, replayed
            nonce, or invalid signature.

    """
    secret = _hmac_secret()
    if not secret:
        log.error("signed_request_auth_unconfigured")
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="unconfigured",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI_SERVER_SIGNING_SECRET is not configured on this server.",
        )
    if not x_nimbus_timestamp or not x_nimbus_nonce or not x_nimbus_signature:
        log.warning("signed_request_missing_headers", path=request.url.path)
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="missing_headers",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing signed-request headers.",
        )
    if len(x_nimbus_timestamp) > _MAX_TIMESTAMP_HEADER_BYTES:
        log.warning("signed_request_invalid_timestamp", path=request.url.path)
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="invalid_timestamp",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Nimbus-Timestamp header.",
        )
    if not _NONCE_RE.fullmatch(x_nimbus_nonce):
        log.warning("signed_request_invalid_nonce", path=request.url.path)
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="invalid_nonce",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Nimbus-Nonce header.",
        )
    try:
        timestamp_int = int(x_nimbus_timestamp)
    except ValueError as exc:
        log.warning("signed_request_invalid_timestamp", path=request.url.path)
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="invalid_timestamp",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Nimbus-Timestamp header.",
        ) from exc
    if not _SIGNATURE_HEX_RE.fullmatch(x_nimbus_signature):
        log.warning("signed_request_invalid_signature_header", path=request.url.path)
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="invalid_signature_header",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Nimbus-Signature header.",
        )

    now = int(time.time())
    if abs(now - timestamp_int) > _SIGNED_REQUEST_MAX_AGE_SECONDS:
        log.warning("signed_request_stale_timestamp", path=request.url.path)
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="stale_timestamp",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Signed request timestamp is too old or too far in the future.",
        )

    with _seen_nonces_lock:
        _cleanup_seen_nonces(float(now))
        if x_nimbus_nonce in _seen_nonces:
            log.warning("signed_request_replayed_nonce", path=request.url.path)
            runtime_telemetry.record_auth_result(
                mechanism="signed_request",
                result="failure",
                reason="replayed_nonce_memory",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Signed request nonce has already been used.",
            )

    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            body_size = int(content_length)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid Content-Length header.",
            ) from exc
        if body_size > _SIGNED_REQUEST_MAX_BODY_BYTES:
            log.warning("signed_request_body_too_large", path=request.url.path)
            runtime_telemetry.record_auth_result(
                mechanism="signed_request",
                result="failure",
                reason="body_too_large",
            )
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Signed request body is too large.",
            )
    body = await request.body()
    if len(body) > _SIGNED_REQUEST_MAX_BODY_BYTES:
        log.warning("signed_request_body_too_large", path=request.url.path)
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="body_too_large",
        )
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Signed request body is too large.",
        )
    payload = _build_signature_payload(
        method=request.method,
        path=request.url.path,
        timestamp=x_nimbus_timestamp,
        nonce=x_nimbus_nonce,
        body=body,
    )
    expected = _expected_signature(secret=secret, payload=payload)
    if not hmac.compare_digest(x_nimbus_signature, expected):
        log.warning("signed_request_invalid_signature", path=request.url.path)
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="invalid_signature",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid signed request.",
        )

    with _seen_nonces_lock:
        _cleanup_seen_nonces(float(now))
        if x_nimbus_nonce in _seen_nonces:
            log.warning(
                "signed_request_replayed_nonce",
                path=request.url.path,
                state_backend="memory",
            )
            runtime_telemetry.record_auth_result(
                mechanism="signed_request",
                result="failure",
                reason="replayed_nonce_memory",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Signed request nonce has already been used.",
            )
        write_result = put_state_if_absent(
            _NONCE_STATE_NAMESPACE,
            x_nimbus_nonce,
            value={
                "path": request.url.path,
                "timestamp": x_nimbus_timestamp,
            },
            expires_at=float(now + _SIGNED_REQUEST_MAX_AGE_SECONDS),
        )
        if write_result.cleaned_entries:
            log.info(
                "signed_request_nonce_state_cleaned",
                path=request.url.path,
                cleaned_entries=write_result.cleaned_entries,
            )
        if not write_result.stored:
            log.warning(
                "signed_request_replayed_nonce",
                path=request.url.path,
                state_backend="persistent",
            )
            runtime_telemetry.record_auth_result(
                mechanism="signed_request",
                result="failure",
                reason="replayed_nonce_persistent",
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Signed request nonce has already been used.",
            )
        _seen_nonces[x_nimbus_nonce] = float(now + _SIGNED_REQUEST_MAX_AGE_SECONDS)
    log.info("signed_request_authenticated", path=request.url.path)
    runtime_telemetry.record_auth_result(
        mechanism="signed_request",
        result="success",
        reason="validated",
    )
    return x_nimbus_signature
