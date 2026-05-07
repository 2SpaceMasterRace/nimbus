# Deployment and Operations

This page documents the current HW3 deployment shape and the operational
signals that exist in the codebase.

## Deployed apps

| Fly app | Code path | Hostname | Role |
|---|---|---|---|
| `ospsd-team-2` | repository root (`Dockerfile`, `fly.toml`) | `https://ospsd-team-2.fly.dev` | Combined storage service + AI server |
| `ospsd-team-2-bridge` | `src/slack_bridge/` (`Dockerfile`, `fly.toml`) | `https://ospsd-team-2-bridge.fly.dev` | Standalone Slack bridge ({doc}`nimbus/slack-bridge`) |

The bridge is its own Fly app. It terminates Slack webhooks, verifies
signatures, normalizes events into the {doc}`nimbus/bridge-contract`, signs
the outbound turn, and posts the AI reply back to Slack via
`chat_client_api`. The AI server itself never sees Slack traffic directly.

## Runtime topology

The combined `ospsd-team-2` app runs the FastAPI service:

```shell
uv run uvicorn aws_client_service.main:app --host 0.0.0.0 --port 8000
```

It serves:

| Path | Purpose |
|---|---|
| `/health` | Storage service health |
| `/ai/health` | AI server health |
| `/files`, `/download`, `/auth/*` | Storage API |
| `/ai/chat/turn`, `/ai/sessions/*` | AI wrapper API |
| `/guide/` | Built Sphinx docs when `docs/build/html` exists |

## Fly.io persistent sessions

Session files and request-state files must survive redeploys. Mount a persistent
volume and point `AI_SESSION_DIR` at it.

```toml
[[mounts]]
  source      = "nimbus_sessions"
  destination = "/data"
```

Create the volume once per region:

```shell
flyctl volumes create nimbus_sessions --region iad --size 1
```

Set secrets:

```shell
flyctl secrets set \
  AI_SESSION_DIR=/data/sessions \
  SESSION_SECRET_KEY=<session-cookie-secret> \
  API_KEY=<storage-api-key> \
  AI_SERVER_API_KEY=<session-management-key> \
  AI_SERVER_SIGNING_SECRET=<wrapper-signing-secret> \
  OPENROUTER_API_KEY=<openrouter-key> \
  AWS_ACCESS_KEY_ID=<aws-key> \
  AWS_SECRET_ACCESS_KEY=<aws-secret> \
  AWS_REGION=us-east-1 \
  AWS_BUCKET_NAME=<bucket>
```

Keep at least one machine running so the volume is attached:

```shell
flyctl scale count 1 --min 1
```

## Slack bridge Fly app

The bridge is deployed independently as `ospsd-team-2-bridge`. It has no
persistent volume because all bridge state is in-process (signature checks,
LRU dedupe caches, telemetry counters).

Build context must be the repository root because the bridge depends on the
`nimbus_runtime` workspace package:

```shell
flyctl deploy \
  --app ospsd-team-2-bridge \
  --config src/slack_bridge/fly.toml \
  --dockerfile src/slack_bridge/Dockerfile
```

Required secrets:

```shell
flyctl secrets set --app ospsd-team-2-bridge \
  SLACK_SIGNING_SECRET=<slack-app-signing-secret> \
  SLACK_BOT_TOKEN=xoxb-<slack-bot-oauth-token> \
  AI_SERVER_BASE_URL=https://ospsd-team-2.fly.dev \
  AI_SERVER_SIGNING_SECRET=<wrapper-signing-secret>
```

`AI_SERVER_SIGNING_SECRET` must match the value set on the AI server app
exactly; the AI server rejects mismatched signatures with `401`.

:::{warning}
The bridge holds two process-local LRU dedupe caches (one for Slack
`event_id`, one for slash-command `trigger_id`). The Fly app must run on
exactly one machine until those caches are moved to a shared store such as
Redis. Scaling out silently breaks dedupe — Slack retries can land on a
different machine and post the AI reply twice.

Pin to one machine:

```shell
flyctl scale count 1 --app ospsd-team-2-bridge
```

The bundled `src/slack_bridge/fly.toml` already declares
`auto_stop_machines = "off"` and `min_machines_running = 1` to enforce this.
:::

Health check:

```shell
curl https://ospsd-team-2-bridge.fly.dev/health
```

Once deployed, point the Slack app's **Event Subscriptions** request URL at
`https://ospsd-team-2-bridge.fly.dev/slack/events` and the `/nimbus` slash
command at `https://ospsd-team-2-bridge.fly.dev/slack/commands`. Subscribe
the bot to `app_mention`, `message.channels`, and `message.im`.

## Environment reference

| Variable | Used by | Purpose |
|---|---|---|
| `SESSION_SECRET_KEY` | `aws_client_service` | Starlette session cookie signing |
| `API_KEY` | `aws_client_service`, `aws_client_adapter` | Storage endpoint shared secret |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | `aws_client_impl` | Live S3 access |
| `AWS_BUCKET_NAME` | demos and Nimbus tool container fallback | Default bucket/container |
| `CLOUD_STORAGE_SERVICE_BASE_URL` | `aws_client_adapter` | Generated-client base URL |
| `OPENROUTER_API_KEY` | `openrouter_ai_client_impl`, `ai_server` | Live model access |
| `OPENROUTER_MODEL`, `OPENROUTER_FALLBACK_MODEL` | `openrouter_ai_client_impl` | Model selection |
| `AI_SERVER_API_KEY` | `ai_server` | Session history/delete auth |
| `AI_SERVER_SIGNING_SECRET` | `ai_server` | HMAC auth for `/ai/chat/turn` |
| `AI_SESSION_DIR` | `ai_server`, `nimbus_runtime` | Session and request-state root |
| `AI_RATE_LIMIT_CAPACITY`, `AI_RATE_LIMIT_RPM` | `ai_server` | Per-user token bucket |
| `AI_IDEMPOTENCY_TTL_SECONDS` | `ai_server` | Cached turn-response TTL |
| `NIMBUS_PENDING_DELETE_TTL_SECONDS` | `nimbus_runtime` | Delete confirmation TTL |
| `NIMBUS_CONTAINER` | Nimbus CLI/runtime tools | Explicit tool bucket/container |
| `NIMBUS_SAFE_ROOT` | Nimbus CLI tools | Local filesystem sandbox |
| `SLACK_SIGNING_SECRET` | `slack_bridge` | Verifies inbound Slack webhook signatures |
| `SLACK_BOT_TOKEN` | `slack_bridge` (via `slack_client_impl`) | Posts replies to Slack |
| `AI_SERVER_BASE_URL` | `slack_bridge` | Base URL of the AI server; bridge appends `/ai/chat/turn` |

## Health checks

```shell
curl https://ospsd-team-2.fly.dev/health
curl https://ospsd-team-2.fly.dev/ai/health
```

Both should return `200`. Use `/openapi.json` to inspect the live FastAPI schema.

## Telemetry and logs

Current observability is intentionally lightweight:

- `structlog` is used across the service, S3 implementation, OpenRouter client,
  and runtime boundaries.
- `nimbus_runtime.telemetry.runtime_telemetry` records in-memory counters and
  histogram summaries.
- Tests can call `runtime_telemetry.snapshot()` to verify latency/success/failure
  signals without depending on a metrics backend.

Key metric names:

| Metric | Type | Labels |
|---|---|---|
| `nimbus_wrapper_turns_total` | counter | `platform`, `outcome` |
| `nimbus_wrapper_turn_latency_ms` | histogram summary | `platform` |
| `nimbus_wrapper_idempotent_replays_total` | counter | `backend` |
| `nimbus_wrapper_auth_total` | counter | `mechanism`, `result`, `reason` |
| `nimbus_ai_requests_total` | counter | success/failure labels |
| `nimbus_ai_latency_ms` | histogram summary | `model` |
| `nimbus_ai_tool_calls_total` | counter | `tool_name`, `success` |
| `nimbus_ai_tool_latency_ms` | histogram summary | `tool_name` |
| `slack_bridge_inbound_total` | counter | `payload_type`, `result` |
| `slack_bridge_event_callback_total` | counter | `outcome` |
| `slack_bridge_slash_inbound_total` | counter | `result` |
| `slack_bridge_slash_command_total` | counter | `outcome` |
| `slack_bridge_dispatch_total` | counter | `outcome`, `source` |
| `slack_bridge_dispatch_latency_ms` | histogram summary | `outcome`, `source` |

This is not yet a Prometheus or Datadog export. The upgrade path is to add an
adapter that drains the existing telemetry contract into the chosen backend.

## Rollback

The repository contains `scripts/ci/rollback_fly_release.sh` for CI rollback
after deploy or post-deploy verification failure. Manual operators should still
verify both health endpoints and inspect recent logs after rollback:

```shell
fly logs
curl https://ospsd-team-2.fly.dev/health
curl https://ospsd-team-2.fly.dev/ai/health
```

## Operational failure modes

| Failure | Expected behavior |
|---|---|
| Missing `AI_SERVER_SIGNING_SECRET` | `/ai/chat/turn` returns `503`. |
| Replayed wrapper nonce | `/ai/chat/turn` returns `401`. |
| OpenRouter timeout | Runtime maps to `504`. |
| OpenRouter rate limit | Runtime maps to `429` or uses fallback when eligible. |
| Corrupt session JSON | Runtime starts a fresh conversation rather than crashing. |
| Missing persistent volume | Sessions and nonce/idempotency state are lost on redeploy. |
| S3 backend error | Storage service returns `502`, adapter maps to domain error. |
| Bridge has no `SLACK_SIGNING_SECRET` | Every `POST /slack/*` returns `401 invalid_slack_signature`. |
| Bridge has no `AI_SERVER_SIGNING_SECRET` | Outbound `/ai/chat/turn` raises before sending; the bridge posts the user-visible fallback `"Sorry, I couldn't reach the AI service right now."` to the channel. |
| Bridge scaled to >1 machine | Process-local dedupe is bypassed; Slack retries can post duplicate replies. Re-pin to one machine until dedupe moves to a shared store. |
