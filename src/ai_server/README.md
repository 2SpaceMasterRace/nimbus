# ai-server

FastAPI router for the Nimbus chat API.

`ai_server` is the HTTP adapter around `nimbus_runtime`. It normalizes incoming
wrapper/chat requests, verifies HMAC signatures, applies rate limiting and
idempotency, calls the shared runtime, and returns a transport-neutral response
shape that `nimbus_slack`, remote `nimbus-cli` profiles, or future frontends can
render.

In the deployable application, `aws_client_service.main:app` mounts this router
under `/ai`, so the production chat endpoint is `POST /ai/chat/turn`.

## Role

This is the AI HTTP boundary. It should stay thin: validate and authenticate
requests, apply request-level controls, call `nimbus_runtime`, and serialize the
result.

## Public API

The public API is the mounted HTTP router:

- `GET /ai/health`
- `POST /ai/chat/turn`
- `GET /ai/sessions/{session_id}/history`
- `DELETE /ai/sessions/{session_id}`

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `fastapi` | Router, dependency injection, request validation |
| `structlog` | Structured auth/runtime logging |
| `nimbus-runtime` | Shared chat orchestration |
| `ai-client-api` | AI contract and domain errors |
| `openrouter-ai-client-impl` | Default provider wiring |
| `aws-client-impl` | Default storage tool backend |
| `cloud-storage-api` | Storage contract type |

## Architecture

```text
nimbus_slack / remote CLI / future frontend
        |
        | signed HTTP request
        v
ai_server.router
        |
        | ChatTurnInput
        v
nimbus_runtime
        |
        | AIClient + CloudStorageClient
        v
openrouter_ai_client_impl + aws_client_impl
```

Package responsibilities:

- `ai_server` owns HTTP, auth, request validation, rate limiting, replay
  protection, idempotent response caching, and response serialization.
- `nimbus_runtime` owns sessions, confirmations, tool policy, and the AI turn.
- `openrouter_ai_client_impl` owns provider-specific model calls.
- `aws_client_impl` owns concrete S3 access for storage-backed tools.

## Routes

Routes are defined without a prefix in this package and mounted at `/ai` by the
combined app.

| Method | Mounted path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/ai/health` | None | Liveness probe |
| `POST` | `/ai/chat/turn` | HMAC signed request | Submit one chat turn |
| `GET` | `/ai/sessions/{session_id}/history` | `X-API-Key` | Retrieve persisted conversation history |
| `DELETE` | `/ai/sessions/{session_id}` | `X-API-Key` | Delete a persisted session |

## Run Locally

Start the combined FastAPI app from the repository root:

```bash
uv sync --all-packages

export SESSION_SECRET_KEY="dev-session-secret-change-me"
export API_KEY="dev-storage-api-key"
export AI_SERVER_API_KEY="dev-ai-management-key"
export AI_SERVER_SIGNING_SECRET="dev-ai-signing-secret"
export OPENROUTER_API_KEY="sk-or-v1-..."
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="my-dev-bucket"

uv run uvicorn aws_client_service.main:app --reload
```

Health check:

```bash
curl -sS http://localhost:8000/ai/health
```

Smoke-test the signed chat route with the repository helper:

```bash
uv run python scripts/ai_server_wrapper_smoke.py \
  --base-url http://localhost:8000 \
  --signing-secret "$AI_SERVER_SIGNING_SECRET" \
  message-event \
  --workspace-id T123TEAM \
  --event-id smoke-001 \
  --channel-id C123CHAN \
  --message-ts 1713840000.123456 \
  --user-id U123USER \
  --text "Hello, Nimbus."
```

The helper appends `/ai/chat/turn`, builds the canonical request body, signs it
with the same reference code used by tests, and prints the JSON response.

## Signed Chat Contract

`POST /ai/chat/turn` uses HMAC-SHA256. The signature covers the HTTP method, the
mounted path, freshness metadata, and the exact request bytes:

```text
METHOD
PATH
TIMESTAMP
NONCE
SHA256(BODY)
```

Required headers:

| Header | Meaning |
| --- | --- |
| `X-Nimbus-Timestamp` | Unix seconds; must be within 300 seconds of server time |
| `X-Nimbus-Nonce` | Single-use random value; persisted replay cache rejects reuse |
| `X-Nimbus-Signature` | Hex HMAC-SHA256 over the canonical payload |

Use `ai_server.wrapper_client.sign_nimbus_request()` when building a wrapper
integration. It is the reference implementation tested by this package.

## Chat Request

`ChatTurnRequest` is the normalized wrapper-facing payload:

| Field | Type | Description |
| --- | --- | --- |
| `platform` | `str` | Source platform, such as `slack` |
| `workspace_id` | `str` | Workspace/team identifier |
| `channel_id` | `str` | Channel/conversation identifier |
| `thread_id` | `str \| None` | Optional thread identifier |
| `message_id` | `str` | Platform message/event identifier |
| `user_id` | `str` | User identifier |
| `text` | `str` | User-visible prompt text |
| `idempotency_key` | `str` | Stable retry key for this logical turn |
| `request_id` | `str \| None` | Optional trace/correlation ID |
| `attachments` | `list[ChatAttachmentReference]` | Optional file references |

`ChatAttachmentReference` supports inline bytes for small files:

| Field | Type | Description |
| --- | --- | --- |
| `platform_file_id` | `str` | Source platform file ID |
| `filename` | `str` | Original filename |
| `content_type` | `str` | MIME type |
| `size_bytes` | `int` | Declared size |
| `content_base64` | `str \| None` | Optional base64 payload |
| `sha256_hex` | `str \| None` | Optional digest verified against decoded bytes |

The server validates boundary data against decoded bytes when content is
present. Malformed wrapper input is rejected instead of coerced into a prompt.

## Chat Response

| Field | Type | Description |
| --- | --- | --- |
| `request_id` | `str` | Request/trace ID |
| `conversation_id` | `str` | Runtime session key |
| `text` | `str` | Text to render back to the user |
| `outcome` | `reply \| confirmation_required \| partial_success \| error` | Turn classification |
| `confirmation_required` | `bool` | Whether the frontend must ask for confirmation |
| `confirmation` | `ConfirmationState \| None` | Pending destructive action details |
| `suggested_next_actions` | `list[str]` | Optional follow-up prompts |
| `model` | `str` | Model that produced the response |
| `steps` | `int` | Agent loop steps used |
| `fallback_used` | `bool` | Whether fallback model was used |
| `actions` | `list[ActionState]` | Durable action summaries from the runtime |
| `artifacts` | `list[ArtifactState]` | Evidence summaries from the runtime |

Destructive delete flows return `confirmation_required=true` first. The caller
must send the exact expected reply before the runtime executes the delete.

## Management Endpoints

Session history and deletion endpoints use `X-API-Key` checked against
`AI_SERVER_API_KEY`:

```bash
curl -sS \
  -H "X-API-Key: $AI_SERVER_API_KEY" \
  "http://localhost:8000/ai/sessions/slack:T123:C456:U789/history"
```

Use these endpoints for debugging, admin tooling, or controlled support flows.
They are intentionally separate from the HMAC chat route.

## Reliability Model

| Concern | Current behavior |
| --- | --- |
| Rate limiting | Per-user token bucket keyed by `platform:workspace_id:user_id` |
| Idempotency | Cached response per actor + conversation + idempotency key, with a request-parameter fingerprint conflict check |
| Replay protection | Nonces persisted under `AI_SESSION_DIR/_request_state` |
| Concurrency | One async lock per conversation via `nimbus_runtime.get_session_lock()` |
| Session durability | Atomic write-temp-then-rename session persistence |
| Action durability | SQLite action/event/artifact ledger under `AI_SESSION_DIR/nimbus_runtime.sqlite3` |
| Horizontal scaling | Requires a shared state backend before running multiple writable instances |

Local state is intentionally simple for the current deployment. SQLite gives the
runtime a real transaction boundary without adding an external service. The
upgrade trigger for Redis/Postgres-style shared state is multi-instance writes
or cross-region active-active serving.

## Environment

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `AI_SERVER_API_KEY` | Yes | None | API key for session management endpoints |
| `AI_SERVER_SIGNING_SECRET` | Yes | None | HMAC secret for chat turns |
| `AI_SESSION_DIR` | No | `~/.nimbus/sessions/ai_server` | Session, idempotency, and replay state |
| `AI_RATE_LIMIT_CAPACITY` | No | `10` | Token bucket burst capacity |
| `AI_RATE_LIMIT_RPM` | No | `10` | Token refill rate per minute |
| `AI_IDEMPOTENCY_TTL_SECONDS` | No | `3600` | Cached response TTL |
| `OPENROUTER_API_KEY` | Live AI | None | Provider key used by the AI client |
| `OPENROUTER_MODEL` | No | package default | Primary model |
| `OPENROUTER_FALLBACK_MODEL` | No | package default | Fallback model |
| `NIMBUS_CONTAINER` | Storage tools | `$AWS_BUCKET_NAME` | Bucket pinned to AI storage tools |

## Tests

```bash
uv run --package ai-server pytest src/ai_server/tests/ -m unit -q

RUN_AI_SERVER_E2E=1 \
AI_SERVER_BASE_URL=https://nimbus-production.onrender.com \
AI_SERVER_SIGNING_SECRET="<secret>" \
uv run pytest src/ai_server/tests/test_e2e.py -m e2e -v
```

Unit tests build an isolated FastAPI app with fake runtime dependencies. They do
not require OpenRouter, AWS, or a deployed server.

## Full Documentation

- `docs/source/nimbus/bridge-contract.md`
- `docs/source/nimbus/verification.md`
- `docs/source/nimbus/bridge-contract.md`
- `docs/source/deployment-operations.md`
- `src/nimbus_slack/README.md`
- `src/nimbus_cli/README.md`
- `src/nimbus_runtime/README.md`
