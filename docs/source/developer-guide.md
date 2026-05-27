# Developer Guide

This is the exhaustive onboarding path for developers who want to build on top
of Nimbus, not just run it once. It covers the local loop, docs loop, package
boundaries, extension points, and the checks that keep the system safe.

## The mental model

Nimbus is a workspace, not one monolithic package. Each package owns one layer
of the system:

| Layer | Package | What you build here |
|---|---|---|
| Storage contract | external `cloud_storage_api` | Shared API types; not edited in this repo |
| S3 implementation | `aws_client_impl` | boto3-backed storage behavior |
| Storage HTTP service | `aws_client_service` | FastAPI routes, auth, OpenAPI schema, docs mount |
| Generated storage client | `aws_s3_cloud_storage_service_client` | Generated code only |
| Storage adapter | `aws_client_adapter` | HTTP-to-`CloudStorageClient` translation |
| AI contract | `ai_client_api` | Provider-neutral AI types and conversation state |
| OpenRouter implementation | `openrouter_ai_client_impl` | pydantic-ai/OpenRouter model loop and storage tools |
| Nimbus protocol | `nimbus_protocol` | Shared turn, event, error, approval, and permission DTOs |
| Runtime | `nimbus_runtime` | Transport-neutral chat/session/tool orchestration, streaming, replay |
| CLI adapter | `nimbus_cli` | Python terminal onboarding, local runtime profile, remote HTTP profiles |
| Slack adapter | `nimbus_slack` | Slack Events API verification, OAuth install, encrypted BYOK setup, Slack file diff/save commands, dedupe, and threaded replies |
| AI HTTP adapter | `ai_server` | Signed wrapper auth, rate limit, idempotency, `/ai` routes |

The primary design question for any change is: **which layer owns this behavior?**

## One-command setup

```shell
uv sync --all-packages
```

This creates `.venv`, installs every workspace package, installs dev tools, and
uses `uv.lock` for reproducible dependency resolution.

Confirm imports:

```shell
uv run python -c "import aws_client_impl, aws_client_service, aws_client_adapter, ai_client_api, openrouter_ai_client_impl, nimbus_protocol, nimbus_runtime, nimbus_cli, nimbus_slack, ai_server; print('workspace ok')"
```

## How to run the docs

Build once:

```shell
uv run sphinx-build docs/source docs/build/html
```

Build from scratch when changing navigation, autodoc config, or page names:

```shell
cd docs
make fresh-html
```

Run executable docs examples:

```shell
uv run sphinx-build -b doctest docs/source docs/build/doctest
```

Serve and rebuild automatically:

```shell
cd docs
make serve
```

Or directly:

```shell
uv run sphinx-autobuild docs/source docs/build/html
```

Open the rendered docs from disk:

```shell
open docs/build/html/index.html
```

Serve them through the app:

```shell
uv run sphinx-build docs/source docs/build/html
SESSION_SECRET_KEY=dev-session-secret \
API_KEY=dev-storage-api-key \
AI_SERVER_API_KEY=dev-ai-api-key \
AI_SERVER_SIGNING_SECRET=dev-wrapper-signing-secret \
uv run uvicorn aws_client_service.main:app --reload
```

Then open:

```shell
open http://localhost:8000/guide/
```

### Docs troubleshooting

| Symptom | Fix |
|---|---|
| `SESSION_SECRET_KEY` missing during docs build | `docs/source/conf.py` sets docs-safe defaults. If this still appears, check imports added to autodoc. |
| Page is missing from sidebar | Add it to a `toctree` in `index.md` or a section index page. |
| Renamed or deleted pages still appear | Run `cd docs && make fresh-html`; `sphinx-build -E` refreshes Sphinx state but does not remove stale HTML files. |
| Duplicate toctree warnings | A page appears in two visible toctrees. Keep child pages owned by their section index. |
| Doctest reports zero tests | Use MyST doctest fences, not plain `pycon` fences. |
| Autodoc imports generated client internals | Document generated client behavior manually; do not autodoc the generated package. |

## Run the system locally

### Fast no-cloud path

This validates imports, docs, and tests without AWS or OpenRouter:

```shell
uv run pytest src/ -q
uv run sphinx-build docs/source docs/build/html
```

### Combined API app

`aws_client_service.main:app` is the deployable app. It includes storage routes
and mounts `ai_server.router` under `/ai`.

```shell
export SESSION_SECRET_KEY="dev-session-secret"
export API_KEY="dev-storage-api-key"
export AI_SERVER_API_KEY="dev-ai-api-key"
export AI_SERVER_SIGNING_SECRET="dev-wrapper-signing-secret"
export AI_SESSION_DIR="$(pwd)/.nimbus-dev/sessions"

uv run uvicorn aws_client_service.main:app --reload
```

Health checks:

```shell
curl http://localhost:8000/health
curl http://localhost:8000/ai/health
```

OpenAPI schema:

```shell
open http://localhost:8000/openapi.json
```

### Live storage path

```shell
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="your-bucket"
```

Smoke:

```shell
printf 'hello\n' > /tmp/nimbus.txt

curl -X POST "http://localhost:8000/files/$AWS_BUCKET_NAME/dev/nimbus.txt" \
  -H "X-API-Key: $API_KEY" \
  -F "file=@/tmp/nimbus.txt"

curl "http://localhost:8000/files?container=$AWS_BUCKET_NAME&prefix=dev/" \
  -H "X-API-Key: $API_KEY"
```

### Live AI path

```shell
export OPENROUTER_API_KEY="sk-or-v1-..."
uv run nimbus auth local --openrouter-key "$OPENROUTER_API_KEY" --no-aws
uv run nimbus chat "Summarize this repo in one sentence." --profile local --no-tools
```

With storage tools:

```shell
export NIMBUS_CONTAINER="$AWS_BUCKET_NAME"
uv run nimbus auth local \
  --openrouter-key "$OPENROUTER_API_KEY" \
  --aws-access-key-id "$AWS_ACCESS_KEY_ID" \
  --aws-secret-access-key "$AWS_SECRET_ACCESS_KEY" \
  --aws-region "$AWS_REGION" \
  --container "$NIMBUS_CONTAINER"
uv run nimbus chat "List files under demo/" --profile local
```

## Test loop by change type

| Change | Minimum verification |
|---|---|
| Storage implementation | `uv run --package aws-client-impl pytest src/aws_client_impl/tests/ -q` |
| Storage service route | `uv run --package aws-client-service pytest src/aws_client_service/tests/ -q` and `uv run pytest tests/integration/ -q` |
| Storage adapter | `uv run --package aws-client-adapter pytest src/aws_client_adapter/tests/ -q` and integration tests |
| AI contract | `uv run --package ai-client-api pytest src/ai_client_api/tests/ -q` |
| OpenRouter implementation | `uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q` |
| Nimbus protocol | `uv run --package nimbus-protocol pytest src/nimbus_protocol/tests/ -q` |
| Runtime behavior | `uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/ -q` |
| CLI adapter | `uv run --package nimbus-cli pytest src/nimbus_cli/tests/ -q` |
| Slack adapter | `uv run --package nimbus-slack pytest src/nimbus_slack/tests/ -q` |
| AI HTTP wrapper | `uv run --package ai-server pytest src/ai_server/tests/ -q` |
| Runtime evals | `uv run pytest -m eval tests/evals -q --no-cov` |
| Docs | `uv run sphinx-build -E docs/source docs/build/html` and doctest build |
| Public behavior | Add or update a regression/integration/e2e test |

Before finishing Python changes:

```shell
uv run ruff check --fix <touched paths>
uv run ruff format <touched paths>
uv run mypy --strict .
```

## Build on top of Nimbus

### Add a new storage backend

Create a new package that depends on `cloud_storage_api` and implements
`CloudStorageClient`.

Checklist:

1. Implement all contract methods.
2. Translate provider errors into domain exceptions.
3. Provide `get_client_impl(*, interactive: bool = False)`.
4. Keep provider SDK imports inside the implementation package.
5. Add unit tests for success, validation, provider failure, and local file failure.
6. Add an integration test proving the factory returns a `CloudStorageClient`.

Do not change callers. They should only switch the factory import.

### Add a new AI provider

Create a package that depends on `ai_client_api` and implements `AIClient`.

Checklist:

1. Implement `send_message`, `ping`, `on_event`, and `emit`.
2. Translate provider failures into `AIClientError` subclasses.
3. Support `Conversation` mutation semantics.
4. Preserve `Tool` schema and audit records.
5. Add tests with fake provider responses.
6. Avoid adding provider SDK imports to `ai_client_api`.

### Add a new chat frontend

Do not put Slack/Discord/Teams business logic in `ai_server`. Build a wrapper
service that normalizes platform events into `ChatTurnRequest` and signs
`POST /ai/chat/turn`.

Wrapper responsibilities:

- map platform IDs into `platform`, `workspace_id`, `channel_id`, `thread_id`,
  `message_id`, and `user_id`
- generate `idempotency_key`
- fetch attachments from the platform if inline upload is needed
- sign the request
- post the returned `text`
- handle `confirmation_required` using exact `expected_reply`

Nimbus responsibilities:

- auth, replay defense, idempotency, rate limiting
- session storage
- delete confirmation state
- attachment byte validation and storage upload
- model/tool orchestration

### Add a new tool

Prefer adding tool behavior in the runtime when it is wrapper-specific and
safety-sensitive. Prefer `openrouter_ai_client_impl.cloud_storage_tools` when it
is part of the CLI's model-exposed storage tool set.

Tool checklist:

- Define a schema with Pydantic or a typed dataclass-to-schema adapter.
- Pin dangerous authority at bind time, such as container names or safe roots.
- Validate bytes and structured payloads actually received.
- Return summaries, not raw sensitive data.
- Add tests for malformed input, success, provider failure, and guardrails.

## System design principles used here

### Contracts inward, transports outward

The innermost code defines what is possible. The outer code decides how the
operation crosses a network, provider SDK, CLI, or chat-platform boundary.

### Failures are normal

Every boundary has a translation layer:

- boto3 `ClientError` to storage domain exceptions
- HTTP response codes to storage domain exceptions
- OpenRouter/pydantic-ai errors to AI domain exceptions
- malformed wrapper requests to 401/422 before runtime execution

### State has an owner

If a change adds state, document:

- owner package
- persistence location
- cleanup policy
- concurrency policy
- upgrade trigger for shared infrastructure

### Current topology is Render plus Postgres

Render deployments use Postgres for conversations, replay state, idempotency,
in-flight claims, actions, events, and artifacts. File/SQLite state is a local
development fallback. The trigger for Valkey/Redis-like shared state is measured
hot coordination that Postgres cannot handle cleanly.

### Observable behavior is API

Docs, env vars, error messages, response shapes, ordering, defaults, and CLI
output can all become compatibility surface. Change them deliberately.

## What not to do

- Do not edit `aws_s3_cloud_storage_service_client` by hand.
- Do not import `boto3` outside `aws_client_impl`.
- Do not put provider-specific AI code in `ai_client_api`.
- Do not bypass `NimbusRuntime` for wrapper-visible business logic.
- Do not add a dependency when a small local primitive is enough.
- Do not use live AWS/OpenRouter in unit tests.
- Do not add unmarked test files.
- Do not commit credentials.
