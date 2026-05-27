# aws-client-service

Deployable FastAPI application for Nimbus storage and AI HTTP APIs.

The service owns the public HTTP boundary for the storage vertical. It injects a
`CloudStorageClient` implementation from `aws_client_impl`, exposes file
operations as JSON/multipart endpoints, publishes an OpenAPI schema for the
generated client, mounts the AI chat router at `/ai`, and serves the built Sphinx
guide at `/guide` when `docs/build/html` exists.

## Role

This is the deployable service boundary. It owns HTTP routing, storage route
authentication, GitHub OAuth, OpenAPI generation, and composition of the AI
router under `/ai`.

## Public API

The public API is HTTP:

- storage routes under `/files`, `/download`, `/health`, and `/auth/...`
- generated OpenAPI schema at `/openapi.json`
- AI router mounted under `/ai`
- built Sphinx guide mounted at `/guide/` when available

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `fastapi`, `starlette`, `uvicorn` | ASGI service framework and local serving |
| `python-multipart` | Multipart upload parsing |
| `pydantic` | Request/response models |
| `itsdangerous` | Session signing support |
| `python-dotenv` | Local env loading |
| `structlog` | Structured service logging |
| `aws-client-impl` | Injected storage implementation |
| `cloud-storage-api` | Storage contract and exceptions |
| `ai-server` | Mounted AI chat router |

## Architecture

```text
cloud_storage_api
    ^
    | implemented by
aws_client_impl
    ^
    | injected into
aws_client_service  ->  OpenAPI schema  ->  aws_s3_cloud_storage_service_client
    |                                           ^
    | mounted at /ai                            | used by
    v                                           |
ai_server + nimbus_runtime                 aws_client_adapter
```

Important boundaries:

- `aws_client_service` depends on the abstract `CloudStorageClient` contract, not
  concrete S3 classes.
- `aws_client_service` gets the storage backend through `get_client_impl()` and
  FastAPI dependency injection.
- `ai_server` is included as a router under `/ai`; it remains a separate package
  so chat orchestration does not leak into storage routes.
- `src/aws_s3_cloud_storage_service_client/` is generated from this app's
  OpenAPI document. Regenerate it when endpoint shape changes.

## Run Locally

Start from the repository root:

```bash
uv sync --all-packages

export SESSION_SECRET_KEY="dev-session-secret-change-me"
export API_KEY="dev-storage-api-key"
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="..."

uv run uvicorn aws_client_service.main:app --reload
```

Local URLs:

| URL | Purpose |
| --- | --- |
| `http://localhost:8000/health` | Storage service liveness check |
| `http://localhost:8000/openapi.json` | Raw OpenAPI schema |
| `http://localhost:8000/docs` | FastAPI Swagger UI |
| `http://localhost:8000/guide/` | Built Sphinx docs, if present |
| `http://localhost:8000/ai/health` | AI router liveness check |

Build the Sphinx guide before using `/guide/`:

```bash
uv run sphinx-build docs/source docs/build/html
```

## Local Smoke Tests

Use the repository smoke harness when you want a fast HTTP check without real AWS
credentials:

```bash
uv run python scripts/run_curl_test_service.py
```

The harness starts the app against deterministic local storage on
`http://127.0.0.1:8001` and exercises the public storage endpoints.

For manual requests, pass the API key explicitly:

```bash
curl -sS \
  -H "X-API-Key: $API_KEY" \
  "http://localhost:8000/files?container=$AWS_BUCKET_NAME&prefix="
```

## Routes

### Storage

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/health` | None | Liveness probe |
| `GET` | `/` | None | Service metadata/root response |
| `POST` | `/files/{container}/{object_name:path}` | API key or OAuth session | Upload a multipart file |
| `GET` | `/download` | API key or OAuth session | Download an object to the response body |
| `GET` | `/files` | API key or OAuth session | List objects in a container |
| `GET` | `/files/{container}/{object_name:path}/info` | API key or OAuth session | Fetch object metadata |
| `DELETE` | `/files/{container}/{object_name:path}` | API key or OAuth session | Delete an object |

`object_name` is a FastAPI `path` parameter, so nested object keys such as
`reports/2026/april.csv` are valid.

### Authentication

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/auth/login` | Start the GitHub OAuth browser flow |
| `GET` | `/auth/callback` | Complete the GitHub OAuth browser flow |

### AI Router

The AI server package is mounted under `/ai` by this application:

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/ai/health` | None | AI router liveness probe |
| `POST` | `/ai/chat/turn` | HMAC signed request | Submit one chat turn |
| `GET` | `/ai/sessions/{session_id}/history` | `X-API-Key` | Inspect persisted conversation history |
| `DELETE` | `/ai/sessions/{session_id}` | `X-API-Key` | Delete a persisted session |

See `src/ai_server/README.md` for the signed request contract.

## Authentication Model

Storage routes accept either:

- `X-API-Key: <API_KEY>` or `Authorization: Bearer <API_KEY>`.
- A GitHub OAuth session cookie created by `/auth/login`.

Use API-key auth for scripts, generated clients, tests, and service-to-service
traffic. Use OAuth only for browser-based manual exploration.

The AI chat route uses a different secret: `AI_SERVER_SIGNING_SECRET`.
Management endpoints under `/ai/sessions/...` use `AI_SERVER_API_KEY`.

## Environment

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `SESSION_SECRET_KEY` | Yes | None | Starlette session signing key |
| `API_KEY` | Yes | None | Storage API shared secret |
| `AWS_ACCESS_KEY_ID` | Real S3 | None | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | Real S3 | None | AWS secret key |
| `AWS_REGION` | No | `us-east-1` | AWS region |
| `AWS_BUCKET_NAME` | Usually | None | Default bucket used by demos and tools |
| `GITHUB_CLIENT_ID` | OAuth only | None | GitHub OAuth client ID |
| `GITHUB_CLIENT_SECRET` | OAuth only | None | GitHub OAuth client secret |
| `GITHUB_AUTH_URI` | No | GitHub default | OAuth authorization URL |
| `GITHUB_TOKEN_URI` | No | GitHub default | OAuth token URL |
| `GITHUB_LOCAL_REDIRECT_URI` | OAuth only | None | Registered callback URL |
| `OAUTH_SESSION_STORE_DIR` | No | `~/.ospsd-team-2/oauth_sessions` | Server-side OAuth token store |
| `AI_SERVER_API_KEY` | AI management | None | API key for `/ai/sessions/...` |
| `AI_SERVER_SIGNING_SECRET` | AI chat | None | HMAC signing secret for `/ai/chat/turn` |
| `AI_SESSION_DIR` | No | `~/.nimbus/sessions/ai_server` | AI session and replay state directory |
| `OPENROUTER_API_KEY` | AI chat | None | OpenRouter key for live AI calls |

`credentials.env` is gitignored and may be used for local development, but
production deployments should set real environment variables or platform
secrets.

## Response Shapes

`ObjectInfoResponse` is returned by upload, list, download metadata, and
get-info paths:

| Field | Type | Meaning |
| --- | --- | --- |
| `object_name` | `str` | Object key |
| `version_id` | `str \| None` | Provider version identifier |
| `data_type` | `str` | MIME type |
| `integrity` | `str` | ETag/checksum-style integrity value |
| `encryption` | `str` | Server-side encryption mode |
| `storage_tier` | `str` | Provider storage class |
| `size_bytes` | `int` | Object size |
| `updated_at` | `str` | ISO-8601 last-modified time |
| `metadata` | `dict` | User metadata |

`DeleteResultResponse` contains `deleted`, `version_id`, and `request_charged`.

## Regenerate the Client

When storage endpoints or models change, regenerate the checked-in OpenAPI client
instead of editing generated files by hand:

```bash
./scripts/update_openapi_schema.sh
./scripts/generate_client.sh
```

Then run adapter and integration tests to confirm the generated client and
`aws_client_adapter` still satisfy the `CloudStorageClient` contract.

## Tests

```bash
uv run --package aws-client-service pytest src/aws_client_service/tests/ -q
uv run pytest tests/integration/ -q
uv run pytest tests/e2e/ -m "not local_credentials" -v
```

## Troubleshooting

| Symptom | Likely fix |
| --- | --- |
| `/ready` reports missing `SESSION_SECRET_KEY` | Export `SESSION_SECRET_KEY` before treating the service as deployable |
| `401 Unauthorized` on storage routes | Pass `X-API-Key` or complete OAuth login |
| `422 Unprocessable Entity` on upload | Send multipart form field named `file` |
| `/guide/` returns 404 | Build docs with `uv run sphinx-build docs/source docs/build/html` |
| AI route returns signed-request errors | Use `scripts/ai_server_wrapper_smoke.py` and the exact `AI_SERVER_SIGNING_SECRET` |

## Full Documentation

- `docs/source/cloud-storage/http-api.md`
- `docs/source/api.md`
- `docs/source/developer-guide.md`
- `src/aws_s3_cloud_storage_service_client/README.md`
