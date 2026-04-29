# Deployment and Operations

This page documents the current HW3 deployment shape and the operational
signals that exist in the codebase.

## Runtime topology

The deployed app is the combined FastAPI app:

```shell
uv run uvicorn aws_client_service.main:app --host 0.0.0.0 --port 8000
```

It serves:

| Path | Purpose |
|---|---|
| `/health` | Lightweight liveness probe |
| `/ready` | Render readiness gate for dependencies and schema |
| `/ai/health` | AI server liveness probe |
| `/ai/ready` | AI router readiness details |
| `/files`, `/download`, `/auth/*` | Storage API |
| `/ai/chat/turn`, `/ai/sessions/*` | AI wrapper API |
| `/guide/` | Built Sphinx docs when `docs/build/html` exists |

## Render environments

The canonical deployment target is Render. `render.yaml` defines a `nimbus`
Blueprint project with separate staging and production environments. Each
environment has a backend service and a Slack adapter service:

| Git branch | Render service | Deploy mode | Purpose |
|---|---|---|---|
| `hw3-stage` | `nimbus-staging` | Render auto-deploy | Backend API and `/ai` staging |
| `hw3-stage` | `nimbus-slack-staging` | Render auto-deploy | Slack Events API staging |
| `hw-3` | `nimbus-production` | CircleCI deploy hook | Backend API and `/ai` production |
| `hw-3` | `nimbus-slack-production` | CircleCI deploy hook | Slack Events API production |

All services run the same Docker image. Backend services use `/ready` and
`scripts/render/start.sh`, which applies the idempotent Postgres migration when
`NIMBUS_STATE_BACKEND=postgres`, then `exec`s Uvicorn. Slack adapter services use
`/health` and `scripts/render/start-slack.sh`; they do not own runtime state or
run migrations.

The Blueprint also keeps staging and production databases separate. Database
`ipAllowList: []` means the databases are not opened for arbitrary public
network access; application services receive their own `DATABASE_URL` through
Render-managed environment wiring. Environment private-network isolation is
enabled so staging and production resources cannot accidentally communicate
across their environment boundary over Render's private network.

Relevant platform docs:

- Render docs: <https://render.com/docs>
- Blueprint spec: <https://render.com/docs/blueprint-spec>
- Deploy hooks: <https://render.com/docs/deploy-hooks>
- Health checks: <https://render.com/docs/health-checks>
- Postgres backups: <https://render.com/docs/postgresql-backups>
- Free limits: <https://render.com/docs/free>

## Runtime state

Render deployments use Postgres as the authoritative runtime state store:

- conversations and sessions;
- signed-request nonce state;
- idempotent wrapper responses;
- in-flight turn claims;
- runtime events, actions, and artifacts.

Set `NIMBUS_STATE_BACKEND=postgres` and `DATABASE_URL` on Render. Local
development and existing tests can omit those variables and keep the file/SQLite
fallback under `AI_SESSION_DIR`.

The Render startup path runs migrations automatically. Run migrations/checks
manually only for local verification or one-off maintenance:

```shell
uv run python scripts/db/migrate.py
uv run python scripts/db/check.py
```

Production should use a paid Render Postgres plan if we need honest backups or
point-in-time recovery. Free Postgres is acceptable only for throwaway demos.

## Environment reference

| Variable | Used by | Purpose |
|---|---|---|
| `SESSION_SECRET_KEY` | `aws_client_service` | Starlette session cookie signing |
| `API_KEY` | `aws_client_service`, `aws_client_adapter` | Storage endpoint shared secret |
| `DATABASE_URL` | `nimbus_runtime` | Render Postgres connection string |
| `NIMBUS_STATE_BACKEND` | `nimbus_runtime`, `ai_server` | Use `postgres` on Render |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION` | `aws_client_impl` | Live S3 access |
| `AWS_BUCKET_NAME` | demos and Nimbus tool container fallback | Default bucket/container |
| `CLOUD_STORAGE_SERVICE_BASE_URL` | `aws_client_adapter` | Generated-client base URL |
| `OPENROUTER_API_KEY` | `openrouter_ai_client_impl`, `ai_server` | Live model access |
| `OPENROUTER_MODEL`, `OPENROUTER_FALLBACK_MODEL` | `openrouter_ai_client_impl` | Model selection |
| `AI_SERVER_API_KEY` | `ai_server` | Session history/delete auth |
| `AI_SERVER_SIGNING_SECRET` | `ai_server` | HMAC auth for `/ai/chat/turn` |
| `AI_SESSION_DIR` | local fallback | Session/request-state root when Postgres is disabled |
| `AI_RATE_LIMIT_CAPACITY`, `AI_RATE_LIMIT_RPM` | `ai_server` | Per-user token bucket |
| `AI_IDEMPOTENCY_TTL_SECONDS` | `ai_server` | Cached turn-response TTL |
| `NIMBUS_PENDING_DELETE_TTL_SECONDS` | `nimbus_runtime` | Delete confirmation TTL |
| `NIMBUS_CONTAINER` | Nimbus CLI/runtime tools | Explicit tool bucket/container |
| `NIMBUS_HOME` | `nimbus_cli` | CLI profiles, sessions, and fallback secret storage |
| `SLACK_SIGNING_SECRET`, `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` | `nimbus_slack` | Slack callback verification and OAuth installation |
| `NIMBUS_SLACK_PUBLIC_BASE_URL`, `NIMBUS_SLACK_STATE_SECRET`, `NIMBUS_SLACK_SECRET_KEY`, `NIMBUS_SLACK_STATE_DIR` | `nimbus_slack` | Public callback URL, OAuth state signing, encrypted secret storage, and SQLite state location |
| `NIMBUS_SLACK_MODEL_MODE`, `NIMBUS_SLACK_SESSION_DIR` | `nimbus_slack` | Remote-vs-tenant-local model mode and tenant runtime session state |
| `NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE`, `NIMBUS_SLACK_FILE_SCAN_MAX_PAGES`, `NIMBUS_SLACK_MAX_FILE_BYTES` | `nimbus_slack` | Slack file scan and download bounds |
| `SLACK_BOT_TOKEN` | `nimbus_slack` | Optional local single-workspace reply fallback |
| `NEW_RELIC_LICENSE_KEY` | telemetry | Primary observability export |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | telemetry | New Relic OTLP endpoint override |
| `SENTRY_DSN` | telemetry | Exception reporting |
| `LAUNCHDARKLY_SDK_KEY` | `ai_server` production | Kill switches and rollout gates |

Do not commit secret values. Put shared values in Render environment variables
and the CircleCI `render-production` context. Use Doppler for team sharing if it
is available to the project.

## Health and readiness

```shell
curl "$NIMBUS_BASE_URL/health"
curl "$NIMBUS_BASE_URL/ready"
curl "$NIMBUS_BASE_URL/ai/health"
curl "$NIMBUS_BASE_URL/guide/"
```

`/health` is intentionally light and should stay up without cloud dependencies.
`/ready` fails closed when required secrets are missing, Postgres is unreachable,
or the runtime schema is stale.

## Telemetry and logs

New Relic is the primary telemetry destination. Sentry remains the exception
sink. The runtime still exposes in-process counters and histogram summaries so
tests can assert on observable behavior without depending on a vendor backend.

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

## Production deploy and rollback

Staging auto-deploys from `hw3-stage`.

Production deploys from `hw-3` only after CircleCI gates pass. CircleCI calls the
Render production deploy hook, waits for `/ready`, and then runs
`scripts/ci/verify_deployed_nimbus.py` against the live service.

Rollback uses Render's dashboard or API rollback to a previous successful
deploy. After rollback, verify:

```shell
curl "$NIMBUS_BASE_URL/health"
curl "$NIMBUS_BASE_URL/ready"
uv run python scripts/ci/verify_deployed_nimbus.py \
  --base-url "$NIMBUS_BASE_URL" \
  --signing-secret "$AI_SERVER_SIGNING_SECRET"
```

## Operational failure modes

| Failure | Expected behavior |
|---|---|
| Missing `AI_SERVER_SIGNING_SECRET` | `/ready` fails and `/ai/chat/turn` returns `503`. |
| Replayed wrapper nonce | `/ai/chat/turn` returns `401`. |
| Reused idempotency key with different payload | `/ai/chat/turn` returns `409`. |
| Duplicate logical turn while first is running | `/ai/chat/turn` returns `409` until the first result is durable. |
| OpenRouter timeout | Runtime maps to `504`. |
| OpenRouter rate limit | Runtime maps to `429` or uses fallback when eligible. |
| Postgres unavailable | `/ready` fails; stateful request paths fail closed. |
| Corrupt local fallback session JSON | Runtime starts a fresh conversation rather than crashing. |
| S3 backend error | Storage service returns `502`, adapter maps to domain error. |
