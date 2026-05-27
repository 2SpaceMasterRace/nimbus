#!/usr/bin/env python3
"""Verify the deployed Nimbus HTTP surface with structured checks."""

from __future__ import annotations

import argparse
import time

import httpx
from ai_server.wrapper_client import (
    build_message_event_turn,
    encode_turn_body,
    sign_nimbus_request,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Base URL of deployed Nimbus")
    parser.add_argument(
        "--signing-secret",
        required=True,
        help="Shared signing secret for /ai/chat/turn",
    )
    return parser.parse_args()


def _ensure(*, condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _signed_turn_body() -> bytes:
    event_id = f"deploy-verify-{int(time.time())}"
    body = build_message_event_turn(
        workspace_id="TCIRCLECI",
        event_id=event_id,
        event={
            "channel": "CCIRCLECI",
            "ts": "1713840000.123456",
            "user": "UCIRCLECI",
            "text": "Reply with exactly one short sentence saying deploy verification passed.",
        },
    )
    return encode_turn_body(body)


def _signed_turn_response(
    *, client: httpx.Client, base_url: str, signing_secret: str, encoded: bytes
) -> httpx.Response:
    headers = sign_nimbus_request(body=encoded, secret=signing_secret)
    return client.post(
        f"{base_url}/ai/chat/turn", content=encoded, headers=headers, timeout=30.0
    )


def main() -> int:
    args = _parse_args()
    base_url = args.base_url.rstrip("/")

    with httpx.Client(timeout=15.0) as client:
        health = client.get(f"{base_url}/health")
        _ensure(
            condition=health.status_code == 200,
            message=f"/health returned {health.status_code}",
        )
        health_body = health.json()
        _ensure(
            condition=health_body.get("status") == "ok",
            message="/health did not return status=ok",
        )

        ready = client.get(f"{base_url}/ready")
        _ensure(
            condition=ready.status_code == 200,
            message=f"/ready returned {ready.status_code}: {ready.text}",
        )
        ready_body = ready.json()
        _ensure(
            condition=ready_body.get("status") == "ready",
            message="/ready did not return status=ready",
        )

        ai_health = client.get(f"{base_url}/ai/health")
        _ensure(
            condition=ai_health.status_code == 200,
            message=f"/ai/health returned {ai_health.status_code}",
        )
        ai_health_body = ai_health.json()
        _ensure(
            condition=ai_health_body.get("status") == "ok",
            message="/ai/health did not return status=ok",
        )
        _ensure(
            condition=ai_health_body.get("service") == "ai-server",
            message="/ai/health did not identify ai-server",
        )

        guide = client.get(f"{base_url}/guide/")
        _ensure(
            condition=guide.status_code == 200,
            message=f"/guide/ returned {guide.status_code}",
        )
        _ensure(
            condition="<html" in guide.text.lower(),
            message="/guide/ did not look like HTML",
        )

        unsigned = client.post(
            f"{base_url}/ai/chat/turn",
            json={
                "platform": "slack",
                "workspace_id": "TCIRCLECI",
                "channel_id": "CCIRCLECI",
                "thread_id": "1713840000.123456",
                "message_id": "1713840000.123456",
                "user_id": "UCIRCLECI",
                "text": "hello from CircleCI",
                "idempotency_key": "slack:TCIRCLECI:event:unsigned-check",
                "request_id": "req-circleci-unsigned-check",
            },
        )
        _ensure(
            condition=unsigned.status_code == 401,
            message=f"unsigned /ai/chat/turn returned {unsigned.status_code}",
        )

        legacy = client.post(
            f"{base_url}/ai/chat",
            json={"message": "hello", "session_id": "removed-route-check"},
        )
        _ensure(
            condition=legacy.status_code == 404,
            message=f"legacy /ai/chat returned {legacy.status_code}",
        )

        encoded_turn = _signed_turn_body()
        signed = _signed_turn_response(
            client=client,
            base_url=base_url,
            signing_secret=args.signing_secret,
            encoded=encoded_turn,
        )
        _ensure(
            condition=signed.status_code == 200,
            message=f"signed /ai/chat/turn returned {signed.status_code}",
        )
        signed_body = signed.json()
        _ensure(
            condition=isinstance(signed_body.get("conversation_id"), str),
            message="signed response missing conversation_id",
        )
        _ensure(
            condition=isinstance(signed_body.get("text"), str),
            message="signed response missing text",
        )
        _ensure(
            condition=signed_body.get("outcome")
            in {"reply", "confirmation_required", "partial_success", "error"},
            message=f"unexpected wrapper outcome: {signed_body.get('outcome')!r}",
        )

        duplicate = _signed_turn_response(
            client=client,
            base_url=base_url,
            signing_secret=args.signing_secret,
            encoded=encoded_turn,
        )
        _ensure(
            condition=duplicate.status_code == 200,
            message=f"duplicate signed /ai/chat/turn returned {duplicate.status_code}",
        )
        duplicate_body = duplicate.json()
        _ensure(
            condition=duplicate_body.get("request_id") == signed_body.get("request_id"),
            message="duplicate signed request did not replay the cached turn",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
