# Contributing to Nimbus

Nimbus is a complex project now: storage, HTTP transport, AI provider
abstraction, a shared runtime, signed wrapper auth, session persistence, tests,
deployment, and docs all meet in one repository.

This guide adapts two ideas from Mitchell Hashimoto's essays:

- [My Approach to Building Large Technical Projects](https://mitchellh.com/writing/building-large-technical-projects):
  break work into chunks that produce visible progress, use tests as early
  results, and keep moving toward demos.
- [Contributing to Complex Projects](https://mitchellh.com/writing/contributing-to-complex-projects):
  become a user, build the project, trace a hot path, learn the inner pieces,
  and start with a bite-sized change.

The Nimbus version is: run the system, trace the path you want to change, make
the smallest production-credible patch, and verify it with the right tests.

## What this project does

| Axis | Contract | Implementations and transports |
|---|---|---|
| Storage | `cloud_storage_api.CloudStorageClient` | `aws_client_impl`, `aws_client_service`, generated OpenAPI client, `aws_client_adapter` |
| AI/runtime | `ai_client_api.AIClient` + `nimbus_protocol` DTOs | `openrouter_ai_client_impl`, `nimbus_runtime`, `ai_server`, `nimbus_cli`, `nimbus_slack` |

Key rules:

- Program to `CloudStorageClient`, not directly to `S3Client`.
- Program to `AIClient`, not directly to OpenRouter or pydantic-ai.
- Keep provider SDKs in implementation packages.
- Keep generated OpenAPI client code generated.
- Keep wrapper/channel logic thin; shared behavior belongs in `nimbus_runtime`.

## 1. Become a user

Run enough of the system to feel the product:

```shell
uv sync --all-packages
uv run pytest src/ -q
uv run sphinx-build docs/source docs/build/html
```

Start the combined local app:

```shell
export SESSION_SECRET_KEY="dev-session-secret"
export API_KEY="dev-storage-api-key"
export AI_SERVER_API_KEY="dev-ai-api-key"
export AI_SERVER_SIGNING_SECRET="dev-wrapper-signing-secret"
export AI_SESSION_DIR="$(pwd)/.nimbus-dev/sessions"

uv run uvicorn aws_client_service.main:app --reload
```

Check the product surfaces:

```shell
curl http://localhost:8000/health
curl http://localhost:8000/ai/health
open http://localhost:8000/guide/
```

If you are working on the AI side:

```shell
export OPENROUTER_API_KEY="..."
uv run nimbus setup local --openrouter-key "$OPENROUTER_API_KEY"
uv run nimbus chat "Summarize this repo in one sentence." --profile local --no-tools
```

## 2. Build before reading everything

Learn the fast feedback loop before you spelunk.

```shell
uv run pytest src/ -q
uv run --package ai-server pytest src/ai_server/tests/ -q
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/ -q
uv run --package nimbus-cli pytest src/nimbus_cli/tests/ -q
uv run --package nimbus-slack pytest src/nimbus_slack/tests/ -q
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict .
```

Docs:

```shell
uv run sphinx-build docs/source docs/build/html
```

## 3. Trace down, learn up

Pick one user-visible behavior and trace it from the public boundary inward.
Write notes about files, functions, state, failure handling, and tests.

### Storage upload hot path

| Layer | File |
|---|---|
| HTTP route | `src/aws_client_service/aws_client_service/main.py` |
| Storage contract | external `cloud_storage_api` package |
| S3 implementation | `src/aws_client_impl/aws_client_impl/s3_client.py` |
| Generated HTTP client | `src/aws_s3_cloud_storage_service_client/` |
| Adapter back to contract | `src/aws_client_adapter/aws_client_adapter/service_adapter.py` |
| Tests | `src/aws_client_service/aws_client_service/tests/`, `src/aws_client_adapter/tests/`, `tests/integration/` |

### AI wrapper chat-turn hot path

| Layer | File |
|---|---|
| HTTP route and response model | `src/ai_server/ai_server/router.py` |
| Signed auth | `src/ai_server/ai_server/auth.py` |
| Replay/idempotency state | `src/ai_server/ai_server/request_state.py` |
| Runtime orchestration | `src/nimbus_runtime/nimbus_runtime/runtime.py` |
| Runtime models | `src/nimbus_runtime/nimbus_runtime/models.py` |
| AI contract | `src/ai_client_api/ai_client_api/` |
| OpenRouter implementation | `src/openrouter_ai_client_impl/openrouter_ai_client_impl/openrouter_client.py` |
| Tests | `src/ai_server/tests/`, `src/nimbus_runtime/tests/`, `src/openrouter_ai_client_impl/tests/` |

After tracing down, learn up from the inner contract:

1. Understand the dataclass or protocol shape.
2. Understand who owns state.
3. Understand which exceptions cross the boundary.
4. Understand the tests that pin the behavior.
5. Only then edit.

## 4. Aim for a demo-sized patch

A good first change has visible progress:

- a route returns a clearer error and a regression test proves it
- a runtime action becomes safer and a test shows the full conversation result
- a storage adapter maps one transport failure correctly
- a docs page turns a confusing workflow into a copy-pasteable command sequence
- a telemetry counter is recorded and asserted in a focused test

Do not introduce heavier infrastructure just because it is fashionable. Document
the trigger for Valkey, queues, or another backend; do not add them before the
topology needs them.

## 5. Make the smallest correct change

Before non-trivial work, write down:

- goal
- public contract
- invariants
- state ownership
- failure modes
- dependencies
- verification plan

Ask before changing public interfaces, adding dependencies, changing
`get_client_impl()` contracts, modifying CI/release automation, or doing broad
cross-package refactors.

## Project structure

```text
ospsd-team-2/
├── src/
│   ├── ai_client_api/
│   ├── ai_server/
│   ├── aws_client_adapter/
│   ├── aws_client_impl/
│   ├── aws_client_service/
│   ├── aws_s3_cloud_storage_service_client/
│   ├── nimbus_runtime/
│   └── openrouter_ai_client_impl/
├── tests/
├── fuzz/
├── docs/
├── scripts/
├── .circleci/
├── Dockerfile
├── render.yaml
├── main.py
├── plans.md
├── pyproject.toml
└── uv.lock
```

## Tests and markers

| Marker | Meaning |
|---|---|
| `unit` | Fast isolated tests |
| `integration` | Package wiring without real cloud dependencies |
| `regression` | Guards for previously fixed bugs |
| `property` | Hypothesis invariant tests |
| `e2e` | End-to-end workflow tests |
| `circleci` | Safe in CI without local credentials |
| `local_credentials` | Requires local credentials or tokens |

All new test files need `pytestmark = pytest.mark.<marker>` at module scope.

Useful commands:

```shell
uv run pytest -m unit
uv run pytest -m "unit or regression"
uv run pytest -m property
uv run pytest tests/integration/
uv run pytest tests/e2e/ -m "not local_credentials"
```

Fuzz smoke mode:

```shell
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_conversation.py
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_session_id.py
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_request_state.py
```

## Code style

The root `pyproject.toml` is canonical for ruff, mypy, pytest, and coverage.

After editing Python:

```shell
uv run ruff check --fix <touched paths>
uv run ruff format <touched paths>
uv run mypy --strict .
uv run pytest <relevant tests>
```

## Documentation changes

Docs are product surface. Prefer task-focused pages, public contracts, and
copy-pasteable commands. Verify with:

```shell
uv run sphinx-build docs/source docs/build/html
```

When behavior changes, update docs and examples in the same PR.

## Branches, commits, and PRs

Branch from `main` unless the team explicitly says otherwise.

```shell
git checkout main
git pull origin main
git checkout -b docs/update-nimbus-runtime-guide
```

Commit subjects should be short, imperative, and specific:

```text
Fix signed request replay handling
Document Nimbus runtime state ownership
Add adapter regression test for 401 responses
```

Before opening a PR:

```shell
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict .
uv run pytest
uv run sphinx-build docs/source docs/build/html
```

PRs should explain what changed, why the shape fits the architecture, what was
verified, and any remaining risk.

## Secrets

Never commit credentials. Use environment variables or `credentials.env`, which
is gitignored.

Minimum local development variables:

```shell
SESSION_SECRET_KEY=...
API_KEY=...
AI_SERVER_API_KEY=...
AI_SERVER_SIGNING_SECRET=...
AI_SESSION_DIR=...
```

Live provider variables:

```shell
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
AWS_BUCKET_NAME=...
OPENROUTER_API_KEY=...
```

## CI/CD

CircleCI runs linting, docs build, unit/regression tests, property tests,
fuzz-smoke, integration tests, and coverage gates. The `hw-3` branch also owns
the deploy and post-deploy verification path.
