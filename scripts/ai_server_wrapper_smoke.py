"""Python httpx smoke client for the wrapper-facing Nimbus route.

Run a local or deployed smoke check against ``POST /ai/chat/turn`` using the
same signing rules the wrapper team should use in middleware.
"""

from __future__ import annotations

import argparse
import json

import httpx
from ai_server.wrapper_client import (
    build_message_event_turn,
    build_slash_command_turn,
    encode_turn_body,
    sign_nimbus_request,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Nimbus base URL")
    parser.add_argument(
        "--signing-secret",
        required=True,
        help="Shared signing secret used for the wrapper route",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    message = subparsers.add_parser(
        "message-event",
        help="Smoke-check a message/app-mention/thread-reply/DM shape",
    )
    message.add_argument("--workspace-id", required=True)
    message.add_argument("--event-id", required=True)
    message.add_argument("--channel-id", required=True)
    message.add_argument("--message-ts", required=True)
    message.add_argument("--user-id", required=True)
    message.add_argument("--text", required=True)
    message.add_argument("--thread-ts")

    slash = subparsers.add_parser(
        "slash-command",
        help="Smoke-check a slash-command shaped request",
    )
    slash.add_argument("--workspace-id", required=True)
    slash.add_argument("--channel-id", required=True)
    slash.add_argument("--trigger-id", required=True)
    slash.add_argument("--user-id", required=True)
    slash.add_argument("--text", required=True)
    slash.add_argument("--thread-id")
    return parser


def _body_from_args(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "message-event":
        event = {
            "channel": args.channel_id,
            "ts": args.message_ts,
            "user": args.user_id,
            "text": args.text,
        }
        if args.thread_ts:
            event["thread_ts"] = args.thread_ts
        return build_message_event_turn(
            workspace_id=args.workspace_id,
            event_id=args.event_id,
            event=event,
        )
    return build_slash_command_turn(
        workspace_id=args.workspace_id,
        channel_id=args.channel_id,
        trigger_id=args.trigger_id,
        user_id=args.user_id,
        text=args.text,
        thread_id=args.thread_id,
    )


def main() -> int:
    args = _parser().parse_args()
    body = _body_from_args(args)
    body_bytes = encode_turn_body(body)
    headers = sign_nimbus_request(body=body_bytes, secret=args.signing_secret)
    response = httpx.post(
        f"{args.base_url.rstrip('/')}/ai/chat/turn",
        content=body_bytes,
        headers=headers,
        timeout=30.0,
    )
    print("Request body:")
    print(json.dumps(body, indent=2, sort_keys=True))
    print()
    print(f"HTTP {response.status_code}")
    print(json.dumps(response.json(), indent=2, sort_keys=True))
    return 0 if response.is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
