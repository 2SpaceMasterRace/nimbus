# Slack Bridge

`slack_bridge` is the standalone Slack-facing service that translates Slack
Events API webhooks and slash-command form posts into signed Nimbus turns and
posts the AI reply back to Slack. The bridge is its own deployable Fly app,
distinct from the AI server, and its only job is to sit on Slack's contract on
one side and the {doc}`bridge-contract` on the other.

## Where it lives

| Location | Contents |
|---|---|
| `src/slack_bridge/slack_bridge/` | Application package (`main.py`, `flow.py`, `body.py`, `client.py`, `verify.py`, `dedupe.py`, `render.py`, `telemetry.py`, `deps.py`, `models.py`) |
| `src/slack_bridge/Dockerfile` | Standalone container; build context is the repository root because the bridge depends on the `nimbus_runtime` workspace package |
| `src/slack_bridge/fly.toml` | Fly.io app definition (`ospsd-team-2-bridge`) |
| `src/slack_bridge/tests/` | pytest suite — body, flow, route, dedupe, render, verify, integration |

## HTTP surface

| Method and path | Purpose |
|---|---|
| `POST /slack/events` | Slack Events API webhook (URL verification challenge, `event_callback` for messages and app mentions) |
| `POST /slack/commands` | Slack slash command (`/nimbus`) form post |
| `GET /health` | Liveness probe used by Fly health checks and deploy verification |

The bridge always acks Slack within the 3-second window. Heavy work is moved
into FastAPI `BackgroundTasks` so the HTTP response is returned immediately
regardless of downstream Nimbus latency.

## Inbound: Slack signature verification

Every `POST /slack/*` request is verified before any payload parsing.

```text
v0=HMAC_SHA256(SLACK_SIGNING_SECRET, "v0:" + X-Slack-Request-Timestamp + ":" + raw_body)
```

The bridge fails closed:

| Condition | Response |
|---|---|
| `SLACK_SIGNING_SECRET` not configured | `401 invalid_slack_signature` |
| `X-Slack-Request-Timestamp` missing or non-integer | `401 invalid_slack_signature` |
| Timestamp older than 5 minutes from server time | `401 invalid_slack_signature` |
| Signature does not match (constant-time compare) | `401 invalid_slack_signature` |

A failure on the verification path records
`nimbus_wrapper_auth_total{mechanism="signed_request",result="failure"}` with a
specific `reason` so missing config and stale timestamps are distinguishable in
metrics.

## Events flow

`POST /slack/events`:

1. Verify the Slack signature.
2. Parse JSON. Reject non-object bodies with `400`.
3. For `url_verification`, echo `payload.challenge`.
4. For `event_callback`:
   - Validate `team_id`, `event_id`, and a dict-typed inner `event`.
   - Drop the event when not user-authored chat: only `type ∈ {message, app_mention}` with no `subtype`, no `bot_id`, and string `user`/`channel`/`ts` are dispatched. Edits, joins, and bot-authored messages are filtered at the boundary so the AI server never sees them and the bridge cannot loop on its own posts.
   - Dedupe by `team_id:event_id` against a process-local LRU cache.
   - Schedule the dispatch as a background task.
5. For any other type, ack `{"ok": true}`.

Filtered and duplicate events still ack `200`. Returning a non-2xx would itself
trigger a Slack retry, which is the situation the dedupe cache is designed to
absorb.

## Slash command flow

`POST /slack/commands` follows the same pattern with the differences expected
of a one-shot invocation:

1. Verify the Slack signature.
2. Decode `application/x-www-form-urlencoded` and require
   `team_id`, `trigger_id`, `channel_id`, `user_id`, `command`. `text` is
   allowed to be empty (the user typed `/nimbus` with no args).
3. Dedupe by `team_id:trigger_id` against a separate LRU cache.
4. Schedule the dispatch as a background task and ack with an empty `200` body
   so Slack does not render a placeholder. The AI reply lands as a normal
   channel message once the background task completes.

## Outbound: signed Nimbus turn

The background task in either flow ends in the same code path: build a
`NimbusTurnRequest`, sign it, POST `/ai/chat/turn`, render the response, and
post it back via the chat client.

The signing payload, headers, and replay/idempotency rules are documented in
{doc}`bridge-contract`. Bridge-specific behavior:

| Concern | Behavior |
|---|---|
| Secret | Reads `AI_SERVER_SIGNING_SECRET` per request; fails fast if unset |
| Body | Compact UTF-8 JSON, byte-stable across retries (the same bytes are signed every attempt) |
| Idempotency key | `slack:{team_id}:event:{event_id}` for events; `slack:{team_id}:command:{trigger_id}` for slash commands |
| Conversation anchor | Events use `thread_id = thread_ts or message_ts`; slash commands use `thread_id = None`, `message_id = "cmd:<trigger_id>"` |
| Retries | Up to 3 attempts on `httpx.TransportError` and `5xx` responses, with `0.5s`, `1.0s` backoff. `4xx` responses, parse failures, and unknown outcomes are not retried. |
| Timeout | `30s` per attempt |

A repeated outbound POST with the same idempotency key returns the cached
`ChatTurnResponse` from the AI server, so retries are safe and free of
duplicate work.

## Slack file attachments

Slack file uploads arrive on the inner event under `files`. The bridge
normalizes each entry into a `TurnAttachment` (metadata only — the bridge does
not fetch bytes) and forwards them as part of the signed turn:

| Limit | Value |
|---|---|
| Attachments per turn | 10 |
| Single attachment size | 20 MiB |
| Default content type when missing | `application/octet-stream` |

Entries that are missing required fields (`id`, `name`), have non-positive or
oversized declared size, or are otherwise malformed are silently dropped at the
boundary. The `files` field as a whole is optional; turns without attachments
pass through unchanged.

For the wrapper-side attachment shape, upload semantics, and total per-turn
byte limits enforced by Nimbus, see {doc}`attachments`.

## Reply rendering

`render_for_chat` is intentionally thin. The Nimbus runtime owns the canonical
user-facing text for every outcome:

| Outcome | Posted to Slack |
|---|---|
| `reply` | `result.text` unchanged |
| `partial_success` | `result.text` unchanged |
| `error` | `result.text` unchanged |
| `confirmation_required` | `result.text`, plus `\n\nReply \`<expected_reply>\` to confirm.` when the expected reply is not already embedded in `result.text` |

This file is the seam where Block Kit, threading, or richer Slack rendering
would land later without leaking transport details into `nimbus_runtime`.

## Failure model

When `call_nimbus` raises after exhausting retries, the bridge:

1. Logs `slack_bridge_nimbus_call_failed` with full context.
2. Posts a short, user-visible fallback message to the originating channel:
   `Sorry, I couldn't reach the AI service right now. Please try again in a moment.`
3. Re-raises so the background dispatcher records the structured failure on
   `slack_bridge_dispatch_total{outcome="failure", source=...}` and the
   latency histogram.

A best-effort `send_message` failure during step 2 is logged
(`slack_bridge_failure_notification_failed`) and absorbed so the original
exception is preserved for ops.

## Dedupe and scaling constraint

Two in-memory caches keep Slack retries idempotent:

| Cache | Key | Default size | Protected against |
|---|---|---|---|
| `_dedupe_cache` | `team_id:event_id` | 4096 (LRU) | Slack Events API retries during slow Nimbus calls |
| `_slash_dedupe_cache` | `team_id:trigger_id` | 4096 (LRU) | Double-submitted slash commands |

Both are process-local. The Fly app is therefore pinned to a single machine
(`min_machines_running = 1`, `auto_stop_machines = "off"` in
`src/slack_bridge/fly.toml`, single uvicorn worker). Scaling to N>1 silently
breaks dedupe because retries can land on a different machine and post the
reply twice. **Move dedupe to a shared store (Redis, Postgres) before
scaling out.**

## Telemetry

The bridge feeds `nimbus_runtime.runtime_telemetry` so its signals appear in
the same in-memory snapshots and OTEL pipeline as the AI server.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `slack_bridge_inbound_total` | counter | `payload_type`, `result` | Every `POST /slack/events` request. Primary success-rate signal for the events front door. |
| `slack_bridge_event_callback_total` | counter | `outcome` (`dispatched` / `filtered` / `duplicate`) | Per `event_callback` payload that passed signature verification. Surfaces Slack retry storms and bot-loop filtering. |
| `slack_bridge_slash_inbound_total` | counter | `result` | Every `POST /slack/commands` request. Distinct from the events counter so error rates per endpoint are independently observable. |
| `slack_bridge_slash_command_total` | counter | `outcome` (`dispatched` / `duplicate`) | Per slash-command payload that passed signature verification. |
| `slack_bridge_dispatch_total` | counter | `outcome` (`success` / `failure`), `source` (`event` / `slash_command`) | Background-task outcome. Primary end-to-end Slack-to-Nimbus health signal. |
| `slack_bridge_dispatch_latency_ms` | histogram summary | `outcome`, `source` | Wallclock latency from background-task start to completion. |

Auth failures share the existing wrapper auth counter so the AI server and
bridge contribute to one consistent series:

```text
nimbus_wrapper_auth_total{mechanism="signed_request", result="failure", reason=...}
```

## Environment

| Variable | Required | Used by | Purpose |
|---|---|---|---|
| `SLACK_SIGNING_SECRET` | yes | `slack_bridge.verify` | Verifies `X-Slack-Signature` on every inbound request |
| `SLACK_BOT_TOKEN` | yes | `slack_client_impl` (via `chat_client_api`) | Posts replies to Slack from `ChatClient.send_message` |
| `AI_SERVER_BASE_URL` | yes | `slack_bridge.client` | Base URL of the AI server (e.g. `https://ospsd-team-2.fly.dev`); the bridge appends `/ai/chat/turn` |
| `AI_SERVER_SIGNING_SECRET` | yes | `slack_bridge.client` | HMAC secret shared with the AI server; must match the AI server's value exactly |
| `PORT` | no | uvicorn | Defaults to `8080` (set via `fly.toml`) |

A missing required secret produces a fail-closed mode rather than a silent
degradation: signature checks reject every inbound request, and the outbound
sign step raises before the HTTP call.

## Deployment

The bridge is deployed as its own Fly app (`ospsd-team-2-bridge`), separate
from the AI server. Operational details, secret-rotation steps, and the
single-machine constraint are documented in {doc}`../deployment-operations`.

## Local development

Run the bridge locally against a real or stubbed AI server:

```shell
export SLACK_SIGNING_SECRET="dev-slack-signing-secret"
export SLACK_BOT_TOKEN="xoxb-dev-token"
export AI_SERVER_BASE_URL="http://localhost:8000"
export AI_SERVER_SIGNING_SECRET="dev-wrapper-signing-secret"

uv run --package slack-bridge \
  uvicorn slack_bridge.main:app --reload --port 8080
```

Smoke checks:

```shell
curl http://localhost:8080/health
```

The signed-request smoke recipe in {doc}`smoke-tests` adapts to
`POST /slack/events` by signing the body with `SLACK_SIGNING_SECRET` instead
of the wrapper signing secret.

## Tests

```shell
uv run --package slack-bridge pytest src/slack_bridge/tests/ -q
```

| File | Surface |
|---|---|
| `test_main.py` | HTTP routes, signature failures, payload validation, dedupe, telemetry on the events and slash-command paths |
| `test_body.py` | Slack event and slash-command payload normalization, attachment extraction limits |
| `test_flow.py` | `handle_slack_event` / `handle_slack_command` orchestration with mocked Nimbus and chat client |
| `test_client.py` | Signed POST to `/ai/chat/turn`, retry behavior, response parsing |
| `test_verify.py` | Signature freshness window, constant-time compare, fail-closed modes |
| `test_dedupe.py` | LRU eviction, idempotent first-insert semantics |
| `test_render.py` | `confirmation_required` footer, no-op for other outcomes |
| `test_integration_http.py` | End-to-end HTTP → background → chat-client path with minimal mocking |
