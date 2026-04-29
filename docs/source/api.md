# HTTP API Reference

The deployed app is `aws_client_service.main:app`. It exposes storage routes at
the root and includes the AI router under `/ai`.

**Local base URL:** `http://localhost:8000`

This page is the global route index. For Stripe-style storage resource docs with
copy-pasteable examples and error semantics, see {doc}`cloud-storage/http-api`.

## Authentication summary

| Route family | Auth |
|---|---|
| `GET /health`, `GET /`, `GET /ai/health` | No auth |
| `/auth/*` | GitHub OAuth browser flow |
| Storage file routes | GitHub OAuth session, `X-API-Key`, or `Authorization: Bearer <api-key>` |
| `POST /ai/chat/turn` | HMAC signed request headers |
| `/ai/sessions/*` | `X-API-Key: $AI_SERVER_API_KEY` |

## Storage endpoints

The full storage API guide lives in {doc}`cloud-storage/http-api`. The summary
below is kept here so developers can see the whole deployed app in one place.

### `GET /health`

Storage service liveness probe.

```json
{"status": "ok"}
```

### `GET /`

Simple root endpoint.

```json
{"message": "Hello World"}
```

### `GET /auth/login`

Starts the GitHub OAuth flow and redirects the browser to GitHub.

| Status | Meaning |
|---|---|
| `302` | Redirect to GitHub authorization URL |

### `GET /auth/callback`

Completes the GitHub OAuth flow.

| Status | Meaning |
|---|---|
| `200` | OAuth token stored server-side for the browser session |
| `400` | Invalid state or rejected code |
| `504` | GitHub transport timeout |
| `502` | GitHub provider failure |

### `POST /files/{container}/{object_name:path}`

Upload a file to a container.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `container` | path | string | S3 bucket/container name, length 3-63 |
| `object_name` | path | string | Object key, length 1-1024 |
| `file` | multipart form | binary | File payload |

| Status | Meaning |
|---|---|
| `200` | `ObjectInfoResponse` |
| `400` | Invalid container, object name, or file object |
| `401` | Invalid or missing auth |
| `404` | Container not found |
| `422` | FastAPI validation error |
| `502` | Storage backend error |

```shell
curl -X POST "http://localhost:8000/files/$AWS_BUCKET_NAME/reports/data.csv" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@/path/to/local/data.csv"
```

### `GET /files`

List files in a container.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `container` | query | string | S3 bucket/container name |
| `prefix` | query | string | Optional object-key prefix |

| Status | Meaning |
|---|---|
| `200` | `list[ObjectInfoResponse]` |
| `400` | Invalid container |
| `401` | Invalid or missing auth |
| `404` | Container not found |
| `422` | FastAPI validation error |
| `502` | Storage backend error |

```shell
curl "http://localhost:8000/files?container=$AWS_BUCKET_NAME&prefix=reports/" \
  -H "X-API-Key: $API_KEY"
```

### `GET /download`

Download an object and stream it back as a file. The temporary server-side file
is removed after the response finishes.

| Parameter | Location | Type | Description |
|---|---|---|---|
| `container` | query | string | S3 bucket/container name |
| `object_name` | query | string | Object key |

| Status | Meaning |
|---|---|
| `200` | File response |
| `400` | Invalid container or object name |
| `401` | Invalid or missing auth |
| `404` | Object or container not found |
| `422` | FastAPI validation error |
| `502` | Storage backend error |

```shell
curl "http://localhost:8000/download?container=$AWS_BUCKET_NAME&object_name=reports/data.csv" \
  -H "X-API-Key: $API_KEY" \
  --output data.csv
```

### `DELETE /files/{container}/{object_name:path}`

Delete an object.

| Status | Meaning |
|---|---|
| `200` | `DeleteResultResponse` |
| `400` | Invalid container or object name |
| `401` | Invalid or missing auth |
| `404` | Object or container not found |
| `422` | FastAPI validation error |
| `502` | Storage backend error |

```shell
curl -X DELETE \
  "http://localhost:8000/files/$AWS_BUCKET_NAME/reports/data.csv" \
  -H "X-API-Key: $API_KEY"
```

### `GET /files/{container}/{object_name:path}/info`

Return object metadata without downloading bytes.

| Status | Meaning |
|---|---|
| `200` | `ObjectInfoResponse` |
| `400` | Invalid container or object name |
| `401` | Invalid or missing auth |
| `404` | Object or container not found |
| `422` | FastAPI validation error |
| `502` | Storage backend error |

```shell
curl "http://localhost:8000/files/$AWS_BUCKET_NAME/reports/data.csv/info" \
  -H "X-API-Key: $API_KEY"
```

## AI endpoints

The AI routes are wrapper-facing resources. They use stronger signed request
auth because they are meant to be called by another service, not directly by
browser JavaScript.

### `GET /ai/health`

AI server liveness probe.

```json
{"status": "ok", "service": "ai-server"}
```

### `POST /ai/chat/turn`

Canonical wrapper-facing chat turn. This route is intended for Slack or other
chat-platform bridge services.

Required signed headers:

| Header | Description |
|---|---|
| `X-Nimbus-Timestamp` | Unix timestamp in whole seconds, within five minutes of server time |
| `X-Nimbus-Nonce` | Single-use nonce |
| `X-Nimbus-Signature` | Hex HMAC-SHA256 signature |

The canonical signature payload is:

```text
METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + SHA256(BODY)
```

Request body:

| Field | Type | Description |
|---|---|---|
| `platform` | string | Lowercase platform name such as `slack` |
| `workspace_id` | string | Platform workspace/team ID |
| `channel_id` | string | Channel or DM ID |
| `thread_id` | string or null | Thread anchor; if omitted, `message_id` anchors the conversation |
| `message_id` | string | Unique source message ID |
| `user_id` | string | Actor ID |
| `text` | string | Plain-text user message |
| `idempotency_key` | string | Caller-generated retry key |
| `request_id` | string or null | Optional correlation ID |
| `attachments` | list | Attachment metadata and optional inline bytes |

Response body:

| Field | Type | Description |
|---|---|---|
| `request_id` | string | Correlation ID |
| `conversation_id` | string | Normalized runtime conversation ID |
| `text` | string | Reply for the wrapper to post |
| `outcome` | string | `reply`, `confirmation_required`, `partial_success`, or `error` |
| `confirmation_required` | boolean | Whether the wrapper should wait for explicit confirmation |
| `confirmation` | object or null | Delete confirmation details |
| `suggested_next_actions` | list[string] | Safe follow-up suggestions |
| `model` | string | Model name or `nimbus-runtime` |
| `steps` | integer | Model-call rounds; zero for runtime-direct behavior |
| `fallback_used` | boolean | Whether the fallback model was used |

Status codes:

| Status | Meaning |
|---|---|
| `200` | Chat turn handled |
| `401` | Bad/missing/stale/replayed signed auth |
| `422` | Invalid body or AI step budget exceeded |
| `429` | Per-user or provider rate limit |
| `502` | Upstream AI provider error |
| `503` | AI server missing required config |
| `504` | AI provider timeout |

See {doc}`nimbus/bridge-contract` for a signing example.

### `GET /ai/sessions/{session_id}/history`

Read persisted conversation history. Requires `X-API-Key: $AI_SERVER_API_KEY`.

| Status | Meaning |
|---|---|
| `200` | `SessionHistoryResponse` |
| `401` | Invalid or missing API key |
| `404` | No session exists |
| `422` | Unsafe session ID |
| `503` | API key not configured |

### `DELETE /ai/sessions/{session_id}`

Delete/reset a persisted conversation. Requires `X-API-Key:
$AI_SERVER_API_KEY`. The operation is idempotent: deleting a missing session
returns `deleted: false`.

| Status | Meaning |
|---|---|
| `200` | `SessionDeleteResponse` |
| `401` | Invalid or missing API key |
| `422` | Unsafe session ID |
| `503` | API key not configured |

## Response models

### `ObjectInfoResponse`

```json
{
  "object_name": "reports/data.csv",
  "version_id": null,
  "data_type": null,
  "integrity": null,
  "encryption": null,
  "storage_tier": null,
  "size_bytes": 123,
  "updated_at": "2026-04-29T12:00:00Z",
  "metadata": null
}
```

### `DeleteResultResponse`

```json
{
  "deleted": true,
  "version_id": null,
  "request_charged": null
}
```

## Python reference

See {doc}`reference/python-api` for autodoc output from the hand-authored
packages. The generated OpenAPI client is intentionally not expanded there;
callers should use `aws_client_adapter` unless they specifically need raw HTTP
client primitives.
