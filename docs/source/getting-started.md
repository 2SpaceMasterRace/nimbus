# Getting Started

This guide gets a new contributor from a clean checkout to a runnable HW3
system. The fastest path uses mocked tests first, then starts the local service
with development secrets, then opts into real AWS/OpenRouter credentials only
when you need live behavior.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Optional: an AWS account for live S3 operations
- Optional: an OpenRouter API key for live Nimbus model calls

## Install

```shell
git clone git@github.com:ospsd-team-2/ospsd-team-2.git
cd ospsd-team-2
uv sync --all-packages
```

Confirm the workspace packages import:

```shell
uv run python -c "import aws_client_impl, ai_client_api, nimbus_runtime, nimbus_cli, nimbus_slack, ai_server; print('ok')"
```

## Run the safe checks first

Most tests use fakes and do not require cloud credentials.

```shell
uv run pytest src/ -q
uv run pytest -m "unit or regression" -q
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict .
```

Build the docs:

```shell
uv run sphinx-build docs/source docs/build/html
```

## Start the local service

The HW3 FastAPI app lives in `aws_client_service.main:app`. It exposes:

- storage endpoints at `/health`, `/files`, `/download`, and `/auth/*`
- AI endpoints under `/ai`
- built Sphinx docs under `/guide` when `docs/build/html` exists

For local development, use throwaway secrets:

```shell
export SESSION_SECRET_KEY="dev-session-secret"
export API_KEY="dev-storage-api-key"
export AI_SERVER_API_KEY="dev-ai-api-key"
export AI_SERVER_SIGNING_SECRET="dev-wrapper-signing-secret"
export AI_SESSION_DIR="$(pwd)/.nimbus-dev/sessions"

uv run uvicorn aws_client_service.main:app --reload
```

Check both health endpoints:

```shell
curl http://localhost:8000/health
curl http://localhost:8000/ai/health
```

Expected responses:

```json
{"status":"ok"}
```

```json
{"status":"ok","service":"ai-server"}
```

## Try storage operations

Real storage operations need AWS credentials and a bucket.

```shell
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="your-bucket"
```

The service accepts either a GitHub OAuth session or a shared API key. For local
curl smoke tests, use `X-API-Key`.

```shell
printf 'hello from Nimbus\n' > /tmp/nimbus-hello.txt

curl -X POST "http://localhost:8000/files/$AWS_BUCKET_NAME/demo/nimbus-hello.txt" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@/tmp/nimbus-hello.txt"

curl "http://localhost:8000/files?container=$AWS_BUCKET_NAME&prefix=demo/" \
  -H "X-API-Key: $API_KEY"

curl "http://localhost:8000/download?container=$AWS_BUCKET_NAME&object_name=demo/nimbus-hello.txt" \
  -H "X-API-Key: $API_KEY" \
  --output /tmp/nimbus-downloaded.txt
```

Delete the object when you are done:

```shell
curl -X DELETE \
  "http://localhost:8000/files/$AWS_BUCKET_NAME/demo/nimbus-hello.txt" \
  -H "X-API-Key: $API_KEY"
```

## Try Nimbus from the CLI

The Python `nimbus-cli` package supports local profiles that run
`NimbusRuntime` in-process and remote profiles that call a self-hosted Nimbus
server. Chat starts a fresh session by default; use `resume` when you want the
last session.

```shell
export OPENROUTER_API_KEY="sk-or-v1-..."
uv run nimbus setup local --openrouter-key "$OPENROUTER_API_KEY"
uv run nimbus chat "hello Nimbus" --profile local --no-tools
uv run nimbus resume "continue" --profile local --no-tools
```

For storage tools:

```shell
export NIMBUS_CONTAINER="$AWS_BUCKET_NAME"
uv run nimbus setup local --openrouter-key "$OPENROUTER_API_KEY" --container "$NIMBUS_CONTAINER"
uv run nimbus chat "list files under demo/" --profile local
```

For a remote/self-hosted server:

```shell
uv run nimbus setup remote \
  --profile dev-server \
  --base-url http://localhost:8000 \
  --auth hmac \
  --signing-secret "$AI_SERVER_SIGNING_SECRET"
uv run nimbus chat "hello through HTTP" --profile dev-server
```

## Try the AI HTTP wrapper

`POST /ai/chat/turn` is for chat-platform wrappers. It uses HMAC-signed request
authentication, not `X-API-Key`. See {doc}`nimbus/bridge-contract` for the full
canonical payload.

Session history and deletion use `X-API-Key`:

```shell
curl "http://localhost:8000/ai/sessions/slack:T1:C1:thread1/history" \
  -H "X-API-Key: $AI_SERVER_API_KEY"
```

## Common failures

| Symptom | What to check |
|---|---|
| `SESSION_SECRET_KEY` error at import/startup | Export `SESSION_SECRET_KEY` before starting `aws_client_service.main:app`. |
| Storage endpoint returns `401` | Send `X-API-Key: $API_KEY` or complete GitHub OAuth. |
| AI endpoint returns `503` | Set `OPENROUTER_API_KEY` for model-backed turns, or `AI_SERVER_SIGNING_SECRET` for signed requests. |
| Signed wrapper request returns `401` | Recompute the HMAC over method, path, timestamp, nonce, and SHA-256 body digest. Nonces are single-use. |
| Tests ask for credentials | Use marker filters such as `-m "not local_credentials"` unless you are intentionally running live-provider tests. |

## Next steps

- Read {doc}`architecture-overview` to understand package boundaries.
- Read {doc}`CONTRIBUTING` before changing code.
- Read {doc}`testing` before adding or modifying tests.
