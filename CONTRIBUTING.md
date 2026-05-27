# Contributing to Nimbus

Thank you for contributing. Nimbus is a substantial system: storage, HTTP
transport, AI provider abstraction, a shared runtime, signed wrapper auth,
session persistence, tests, deployment, and docs all meet in one repository.

This guide adapts two useful ideas from Mitchell Hashimoto's writing to this
codebase:

- From [My Approach to Building Large Technical Projects](https://mitchellh.com/writing/building-large-technical-projects): break work into chunks that produce visible progress, use tests as early results, and sprint toward runnable milestones instead of trying to perfect every subsystem first.
- From [Contributing to Complex Projects](https://mitchellh.com/writing/contributing-to-complex-projects): become a user, build the project, trace a hot path from the outside in, learn the inner pieces, then make a bite-sized change.

The short version: run Nimbus, trace the path you want to change, make the
smallest production-credible patch, and verify it with the right tests.

## What This Project Does

Nimbus combines two axes:

| Axis | Contract | Implementations and transports |
|---|---|---|
| Storage | `cloud_storage_api.CloudStorageClient` from the external shared package | `aws_client_impl`, `aws_client_service`, generated OpenAPI client, `aws_client_adapter` |
| AI/runtime | `ai_client_api.AIClient` + `nimbus_protocol` DTOs | `openrouter_ai_client_impl`, `nimbus_runtime`, `ai_server`, `nimbus_cli`, `nimbus_slack` |

Key rules:

- Program to `CloudStorageClient`, not directly to `S3Client`.
- Program to `AIClient`, not directly to OpenRouter or pydantic-ai.
- Keep provider SDKs in implementation packages.
- Keep generated OpenAPI client code generated; put hand-written adaptation in `aws_client_adapter`.
- Keep wrapper/channel logic thin; shared chat behavior belongs in `nimbus_runtime`.

## Step 1: Become a User

Before editing internals, run enough of the system to feel the product.

```shell
uv sync --all-packages
uv run pytest src/ -q
uv run sphinx-build docs/source docs/build/html
```

Start the local app with development secrets:

```shell
export SESSION_SECRET_KEY="dev-session-secret"
export API_KEY="dev-storage-api-key"
export AI_SERVER_API_KEY="dev-ai-api-key"
export AI_SERVER_SIGNING_SECRET="dev-wrapper-signing-secret"
export AI_SESSION_DIR="$(pwd)/.nimbus-dev/sessions"

uv run uvicorn aws_client_service.main:app --reload
```

Check:

```shell
curl http://localhost:8000/health
curl http://localhost:8000/ai/health
open http://localhost:8000/guide/
```

If you are working on AI behavior, also try:

```shell
export OPENROUTER_API_KEY="..."
uv run nimbus setup local --openrouter-key "$OPENROUTER_API_KEY"
uv run nimbus chat "Summarize this repo in one sentence." --profile local --no-tools
```

The goal is not to become an expert user. The goal is to build empathy for the
workflow you are about to change.

## Step 2: Build and Test Before Reading Everything

Do not start by reading every file. First learn how to get fast feedback.

```shell
# Fast local confidence
uv run pytest src/ -q

# Package-focused checks
uv run --package ai-server pytest src/ai_server/tests/ -q
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/ -q
uv run --package nimbus-cli pytest src/nimbus_cli/tests/ -q
uv run --package nimbus-slack pytest src/nimbus_slack/tests/ -q

# Full normal suite
uv run pytest

# Lint, format, type check
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict .
```

Build docs when editing docs:

```shell
uv run sphinx-build docs/source docs/build/html
```

Most tests do not require AWS or OpenRouter. Live-provider tests must be marked
and opt-in.

## Step 3: Trace Down, Learn Up

Pick one user-visible behavior and trace it from the public boundary inward.
Write notes as you go: files, functions, state, failure handling, and tests.
Do not try to understand every implementation detail on the first pass.

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

## Step 4: Aim for a Reviewable Patch

Large technical work should still produce visible progress quickly. In Nimbus,
a good first patch usually has one of these shapes:

- a route returns a clearer error and a regression test proves it
- a runtime action becomes safer and a test shows the full conversation result
- a storage adapter maps one transport failure correctly
- a docs page turns a confusing workflow into a copy-pasteable command sequence
- a telemetry counter is recorded and asserted in a focused test

Avoid speculative platform work. If a local JSON file is enough for the current
single-machine topology, do not introduce Valkey. If a direct function is enough,
do not add a framework. Document the trigger for heavier infrastructure instead.

## Step 5: Make the Smallest Correct Change

Before non-trivial code changes, write down:

- the goal
- the public contract
- invariants
- state ownership
- failure modes
- dependencies
- verification plan

Then implement the smallest patch that satisfies the contract.

Ask before:

- changing public interfaces
- adding third-party dependencies
- changing `get_client_impl()` factory contracts
- modifying CI/CD, release automation, or root tool configuration
- making broad cross-package refactors

## Project Structure

```text
nimbus/
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
│   ├── e2e/
│   ├── integration/
│   └── test_support/
├── fuzz/
├── docs/
│   └── source/
├── scripts/
├── .circleci/
├── Dockerfile
├── render.yaml
├── main.py
├── plans.md
├── pyproject.toml
└── uv.lock
```

## Tests and Markers

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

## Code Style

The root `pyproject.toml` is canonical for ruff, mypy, pytest, and coverage.

- Python 3.12+
- `ruff` with `ALL` rules selected
- `mypy --strict`
- 80% coverage threshold with branch coverage
- package boundaries preserved

After editing Python:

```shell
uv run ruff check --fix <touched paths>
uv run ruff format <touched paths>
uv run mypy --strict .
uv run pytest <relevant tests>
```

## Documentation Changes

Docs are product surface. Prefer task-focused pages, public contracts, and
copy-pasteable commands. Use Sphinx/MyST cross-references in `docs/source`.

Verify:

```shell
uv run sphinx-build docs/source docs/build/html
```

When behavior changes, update docs and examples in the same PR.

## Branches, Commits, and PRs

Branch from `main` unless the team explicitly tells you otherwise.

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

PRs should explain:

- what problem changed
- why this shape fits the architecture
- what tests/docs were updated
- any remaining risk

Keep the first PR small. A three-line fix that teaches you the review process is
a successful contribution.

## Secrets

Never commit credentials. Use environment variables or `credentials.env`, which
is gitignored.

Minimum local development variables:

```shell
SESSION_SECRET_KEY=...
API_KEY=...
AI_SERVER_API_KEY=...
AI_SERVER_SIGNING_SECRET=...
AI_SESSION_DIR=...          # local fallback
NIMBUS_STATE_BACKEND=postgres
DATABASE_URL=...            # Render Postgres
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
fuzz-smoke, integration tests, and coverage gates. The `hw-3` branch is the
production deploy line and owns the deploy and post-deploy verification path.

If you change public behavior, assume CI is only the last safety net. Run the
focused checks locally first.
