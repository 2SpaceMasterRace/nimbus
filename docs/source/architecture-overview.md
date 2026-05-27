# Architecture Overview

Nimbus is a Python 3.12+ workspace made of small installable packages. The
design rule is simple: application code depends on contracts, while provider
SDKs and transports live behind adapters.

## System shape

| Area | Public boundary | Implementation packages | Transport packages |
|---|---|---|---|
| Storage | `cloud_storage_api` from the external shared package | `aws_client_impl` | `aws_client_service`, `aws_client_adapter`, generated OpenAPI client |
| AI | `ai_client_api` + `nimbus_protocol` | `openrouter_ai_client_impl`, `nimbus_runtime` | `ai_server`, `nimbus_cli`, `nimbus_slack` |

The storage and AI axes are connected by tools. Nimbus can expose storage
operations to an LLM without making the AI abstraction depend on S3 or boto3.

## Package map

| Package | Owns | Must not own |
|---|---|---|
| `cloud_storage_api` | `CloudStorageClient`, `ObjectInfo`, `DeleteResult`, domain exceptions | Provider implementation details |
| `aws_client_impl` | boto3-backed S3 behavior, multipart upload helpers, OAuth token helpers | HTTP service routing |
| `aws_client_service` | FastAPI storage endpoints, auth dependency, docs mount, `/ai` router mount | Direct S3 construction outside `get_client_impl()` |
| `aws_s3_cloud_storage_service_client` | Generated OpenAPI client code | Hand edits |
| `aws_client_adapter` | `CloudStorageClient` over HTTP using the generated client | Service internals |
| `ai_client_api` | `AIClient`, `Conversation`, `Tool`, `AIResponse`, `AIStreamEvent`, AI exceptions | Provider SDK imports |
| `openrouter_ai_client_impl` | OpenRouter + pydantic-ai implementation and storage tool bindings | The provider-agnostic contract or terminal UX |
| `nimbus_protocol` | Shared turn, event, error, approval, and permission DTOs | Provider SDK behavior |
| `nimbus_runtime` | Session orchestration, streaming/replay, confirmation flows, attachment ingestion, telemetry | FastAPI, Slack, or CLI request parsing |
| `nimbus_cli` | Python terminal onboarding, local in-process runtime, remote profiles | Runtime policy or provider SDK behavior |
| `nimbus_slack` | Slack signature verification, OAuth installation, encrypted BYOK setup, Slack file diff/save commands, retry dedupe, Events API normalization, threaded replies | Runtime policy or provider SDK behavior |
| `ai_server` | HTTP wrapper for chat frontends, signed auth, idempotency, rate limiting | Channel-specific Slack business logic |

## Dependency direction

```text
User code
  |
  +--> cloud_storage_api.CloudStorageClient
  |       |
  |       +--> aws_client_impl.S3Client
  |       |
  |       +--> aws_client_adapter.CloudStorageServiceAdapter
  |               |
  |               +--> generated OpenAPI client
  |                       |
  |                       +--> aws_client_service FastAPI app
  |
  +--> nimbus_cli local profile
  |       |
  |       +--> nimbus_protocol DTOs
  |               |
  |               +--> nimbus_runtime.NimbusRuntime
  |                       |
  |                       +--> ai_client_api.AIClient
  |                               |
  |                               +--> openrouter_ai_client_impl.OpenRouterClient
  |
  +--> nimbus_cli remote profile / nimbus_slack / future channel adapter
          |
          +--> nimbus_protocol DTOs
                  |
                  +--> ai_server router mounted at /ai
                          |
                          +--> nimbus_runtime.NimbusRuntime
                                  |
                                  +--> ai_client_api.AIClient
                                          |
                                          +--> openrouter_ai_client_impl.OpenRouterClient
```

Rules enforced by review:

- Only `aws_client_impl` imports `boto3`.
- Provider-specific AI code stays out of `ai_client_api`.
- `aws_client_service` obtains storage through `get_client_impl()`.
- `aws_client_adapter` talks through the generated client, not service internals.
- `ai_server` remains an HTTP adapter around `nimbus_runtime`.
- `nimbus_cli` and `nimbus_slack` remain channel adapters around
  `nimbus_runtime` or the signed `/ai/chat/turn` contract.

## Storage request flows

### In-process storage

| Step | Actor | Action |
|---|---|---|
| 1 | Caller | Imports `get_client_impl()` from `aws_client_impl`. |
| 2 | Factory | Returns an `S3Client`, typed by the `CloudStorageClient` contract. |
| 3 | Caller | Calls `upload_file`, `list_files`, `download_file`, `delete_file`, or `get_file_info`. |
| 4 | `S3Client` | Validates container and object names, then calls boto3. |
| 5 | `S3Client` | Translates AWS failures into domain exceptions and returns `ObjectInfo` or `DeleteResult`. |

### HTTP-backed storage

| Step | Actor | Action |
|---|---|---|
| 1 | Caller | Imports `get_client_impl()` from `aws_client_adapter`. |
| 2 | Adapter factory | Builds an authenticated generated client using `CLOUD_STORAGE_SERVICE_BASE_URL` and `API_KEY`. |
| 3 | Adapter | Implements the same `CloudStorageClient` methods over HTTP. |
| 4 | Generated client | Sends requests to `aws_client_service`. |
| 5 | Service | Authenticates the request, calls the injected storage client, and returns JSON or a file response. |
| 6 | Adapter | Maps generated response models back to `ObjectInfo`, `DeleteResult`, or domain exceptions. |

## AI wrapper request flow

| Step | Actor | Action |
|---|---|---|
| 1 | Chat wrapper | Sends `POST /ai/chat/turn` with a signed canonical request body. |
| 2 | `ai_server.auth` | Verifies timestamp freshness, nonce uniqueness, body digest, and HMAC signature. |
| 3 | `ai_server.router` | Checks per-user token bucket rate limit and idempotency cache. |
| 4 | `NimbusRuntime` | Acquires the per-conversation lock and decides whether the turn is direct runtime behavior or model-backed behavior. |
| 5 | Runtime direct path | Handles delete confirmation and attachment upload without asking the model to perform dangerous work. |
| 6 | Runtime AI path | Loads the persisted conversation, appends the user turn, and offloads `OpenRouterClient.send_message()` with `asyncio.to_thread`. |
| 7 | `OpenRouterClient` | Runs the pydantic-ai loop, records tool calls, falls back on eligible provider errors, and returns `AIResponse`. |
| 8 | Runtime/router | Persists the conversation atomically, records telemetry, caches the idempotent response, and returns `ChatTurnResponse`. |

## State ownership

| State | Owner | Storage | Bound |
|---|---|---|---|
| Conversation history | `nimbus_runtime` and `ai_server.sessions` | JSON files under `AI_SESSION_DIR` | Bounded by `Conversation.max_messages` and token estimate |
| Per-session locks | `nimbus_runtime.get_session_lock()` | In-process `WeakValueDictionary` | Tracks active sessions only |
| Signed nonces | `ai_server.auth` and `ai_server.request_state` | In-memory dict plus expiring JSON files | Five-minute freshness window |
| Idempotent turn responses | `ai_server.router` | In-memory dict plus expiring JSON files | `AI_IDEMPOTENCY_TTL_SECONDS`, default 3600 |
| Runtime confirmation actions | `nimbus_runtime.state_store` | Expiring JSON files | `NIMBUS_PENDING_DELETE_TTL_SECONDS`, default 900 |
| Runtime telemetry | `nimbus_runtime.telemetry` | In-memory counters and histograms | Resettable process-local snapshot |

## Failure model

| Boundary | Expected failure | Behavior |
|---|---|---|
| boto3/S3 | auth, missing bucket/object, backend error, local file access | Translate to `cloud_storage_api` domain exceptions. |
| Storage HTTP service | invalid input, missing auth, backend exception | Return 400/401/404/422/502 with a stable response shape. |
| Generated client/adapter | transport error or non-OK response | Translate to domain exceptions; do not leak httpx details to callers. |
| OpenRouter | config, auth, rate limit, timeout, provider error | Translate to `ai_client_api` exceptions; fallback model handles eligible 429/5xx cases. |
| Wrapper auth | missing headers, stale timestamp, replayed nonce, bad HMAC | Return 401 or 503 without entering runtime execution. |
| Session persistence | corrupt or missing fallback JSON, missing Postgres row | Reset to a fresh conversation rather than crashing. |
| Postgres state store | unavailable or stale schema | `/ready` fails closed; stateful request paths do not pretend to be healthy. |
| Destructive delete | missing explicit confirmation | Return `confirmation_required` with an exact expected reply. |

## Why the current primitives are enough

The current production deployment is a Render web service backed by Render
Postgres. Postgres is the smallest production-credible shared state primitive
for conversations, nonce replay defense, idempotent turns, in-flight turn
claims, actions, events, and artifacts. It avoids the accidental single-process
assumptions that would make auto-deploys, retries, or future horizontal scaling
unsafe.

Local files and SQLite remain the development fallback. The trigger for adding
Valkey/Redis, queues, or worker fleets is explicit: measured hot coordination,
long-running actions that exceed HTTP deadlines, or sustained traffic that
cannot be handled by one web service plus Postgres.
