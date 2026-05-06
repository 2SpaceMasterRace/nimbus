"""HTTP client for the Nimbus wrapper-facing chat endpoint."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import TYPE_CHECKING, Final, cast

import httpx
import structlog
from nimbus_runtime.models import (
    ChatTurnResult,
    ConfirmationDetails,
    TurnOutcome,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from slack_bridge.models import NimbusTurnRequest

log = structlog.get_logger()
_NIMBUS_PATH: Final[str] = "/ai/chat/turn"
_TIMEOUT_SECONDS: Final[float] = 30.0
_ALLOWED_OUTCOMES: Final[set[str]] = {
    "reply",
    "confirmation_required",
    "partial_success",
    "error",
}


def encode_body(turn: NimbusTurnRequest) -> bytes:
    """Serialize one wrapper-facing request to compact UTF-8 JSON bytes."""
    return json.dumps(
        dataclasses.asdict(turn),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sign_request(body_bytes: bytes) -> dict[str, str]:
    """Build the Nimbus signed-request headers."""
    secret = os.environ.get("AI_SERVER_SIGNING_SECRET", "").strip()
    if not secret:
        msg = "AI_SERVER_SIGNING_SECRET is not set"
        raise RuntimeError(msg)
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    body_digest = hashlib.sha256(body_bytes).hexdigest()
    canonical = f"POST\n{_NIMBUS_PATH}\n{timestamp}\n{nonce}\n{body_digest}"
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Nimbus-Timestamp": timestamp,
        "X-Nimbus-Nonce": nonce,
        "X-Nimbus-Signature": signature,
    }


def _require_str(payload: Mapping[str, object], key: str) -> str:
    """Require a string field from a response payload."""
    value = payload.get(key)
    if not isinstance(value, str):
        msg = f"Nimbus response field {key!r} must be a string"
        raise TypeError(msg)
    return value


def _require_bool(payload: Mapping[str, object], key: str) -> bool:
    """Require a boolean field from a response payload."""
    value = payload.get(key)
    if not isinstance(value, bool):
        msg = f"Nimbus response field {key!r} must be a boolean"
        raise TypeError(msg)
    return value


def _require_int(payload: Mapping[str, object], key: str) -> int:
    """Require an integer field from a response payload."""
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"Nimbus response field {key!r} must be an integer"
        raise TypeError(msg)
    return value


def _parse_outcome(payload: Mapping[str, object]) -> TurnOutcome:
    """Parse and validate the outcome field."""
    outcome = _require_str(payload, "outcome")
    if outcome not in _ALLOWED_OUTCOMES:
        msg = f"Unknown Nimbus outcome {outcome!r}"
        raise RuntimeError(msg)
    return cast("TurnOutcome", outcome)


def _parse_confirmation(payload: Mapping[str, object]) -> ConfirmationDetails | None:
    """Parse the confirmation field if present."""
    raw_confirmation = payload.get("confirmation")
    if raw_confirmation is None:
        return None
    if not isinstance(raw_confirmation, dict):
        msg = "Nimbus response field 'confirmation' must be an object or null"
        raise TypeError(msg)
    action_id = _require_str(raw_confirmation, "action_id")
    kind = _require_str(raw_confirmation, "kind")
    prompt = _require_str(raw_confirmation, "prompt")
    expected_reply = _require_str(raw_confirmation, "expected_reply")
    expires_at = _require_str(raw_confirmation, "expires_at")
    if kind != "delete_file":
        msg = f"Unknown confirmation kind {kind!r}"
        raise RuntimeError(msg)
    return ConfirmationDetails(
        action_id=action_id,
        kind="delete_file",
        prompt=prompt,
        expected_reply=expected_reply,
        expires_at=expires_at,
    )


def _parse_suggested_next_actions(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Parse the suggested_next_actions list field."""
    raw_actions = payload.get("suggested_next_actions", [])
    if not isinstance(raw_actions, list) or not all(
        isinstance(action, str) for action in raw_actions
    ):
        msg = "Nimbus response field 'suggested_next_actions' must be a list of strings"
        raise TypeError(msg)
    return tuple(raw_actions)


def _parse_result(payload: object) -> ChatTurnResult:
    """Parse a raw Nimbus response payload into a ChatTurnResult."""
    if not isinstance(payload, dict):
        msg = "Nimbus response body must be a JSON object"
        raise TypeError(msg)
    return ChatTurnResult(
        request_id=_require_str(payload, "request_id"),
        conversation_id=_require_str(payload, "conversation_id"),
        text=_require_str(payload, "text"),
        outcome=_parse_outcome(payload),
        confirmation_required=_require_bool(payload, "confirmation_required"),
        suggested_next_actions=_parse_suggested_next_actions(payload),
        model=_require_str(payload, "model"),
        steps=_require_int(payload, "steps"),
        fallback_used=_require_bool(payload, "fallback_used"),
        confirmation=_parse_confirmation(payload),
    )


def call_nimbus(turn: NimbusTurnRequest) -> ChatTurnResult:
    """Sign and POST one wrapper-facing chat turn to Nimbus."""
    base_url = os.environ.get("AI_SERVER_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        msg = "AI_SERVER_BASE_URL is not set"
        raise RuntimeError(msg)
    body_bytes = encode_body(turn)
    headers = sign_request(body_bytes)
    log.info(
        "nimbus_request_sent",
        request_id=turn.request_id,
        idempotency_key=turn.idempotency_key,
    )
    response = httpx.post(
        f"{base_url}{_NIMBUS_PATH}",
        content=body_bytes,
        headers=headers,
        timeout=_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    result = _parse_result(response.json())
    log.info(
        "nimbus_response_received",
        request_id=result.request_id,
        conversation_id=result.conversation_id,
        outcome=result.outcome,
    )
    return result
