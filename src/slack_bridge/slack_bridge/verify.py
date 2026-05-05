"""Slack request signature verification for the Nimbus Slack bridge.

Verifies that inbound HTTP requests genuinely came from Slack using
HMAC-SHA256 and the app's Signing Secret. Rejects stale or replayed
requests older than 5 minutes.
"""

import hashlib
import hmac
import os
import time

import structlog

from nimbus_runtime import runtime_telemetry

_SIGNED_REQUEST_MAX_AGE_SECONDS = 300
log = structlog.get_logger()


def _hmac_secret() -> str:
    return os.environ.get("SLACK_SIGNING_SECRET", "").strip()


def verify_slack_secret(
    *,
    body: bytes,
    timestamp: str,
    slack_signature: str,
) -> bool:
    """Verify that an inbound request genuinely came from Slack.

    Args:
        body: Raw request body bytes.
        timestamp: Value of the X-Slack-Request-Timestamp header.
        slack_signature: Value of the X-Slack-Signature header (starts with v0=).

    Returns:
        True if the signature is valid and the timestamp is fresh. False otherwise.

    """
    secret = _hmac_secret()
    if not secret:
        log.error("signed_request_auth_unconfigured")
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="unconfigured",
        )
        return False
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        log.warning("signed_request_invalid_timestamp")
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="invalid_timestamp",
        )
        return False
    now = int(time.time())
    if abs(now - timestamp_int) > _SIGNED_REQUEST_MAX_AGE_SECONDS:
        log.warning("signed_request_stale_timestamp")
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="stale_timestamp",
        )
        return False
    canonical = f"v0:{timestamp}:{body.decode('utf-8')}"
    expected = (
        "v0="
        + hmac.new(
            secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    )
    if not hmac.compare_digest(slack_signature, expected):
        log.warning("signed_request_invalid_signature")
        runtime_telemetry.record_auth_result(
            mechanism="signed_request",
            result="failure",
            reason="invalid_signature",
        )
        return False
    return True
