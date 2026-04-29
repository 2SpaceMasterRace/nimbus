# Test Suite Guide

Tests are part of the Nimbus product surface. They document public contracts,
protect package boundaries, and keep reliability behavior from becoming tribal
knowledge.

Use this guide when deciding where to add a test, what marker to use, and what
kind of failure behavior the codebase expects you to cover.

## Quick Commands

Run from the repository root:

```bash
uv sync --all-packages

# Fast package/unit pass
uv run pytest src/

# Full default suite
uv run pytest

# Marker-focused runs
uv run pytest -m unit
uv run pytest -m "unit or regression"
uv run pytest -m "not e2e"
uv run pytest tests/bdd -q --no-cov

# Integration and e2e helpers
uv run pytest tests/integration/
uv run pytest tests/e2e/ -m "not local_credentials"
./scripts/run_integration_tests.sh
bash scripts/run_e2e_tests.sh
```

## Markers

Markers are defined in the root `pyproject.toml`.

| Marker | Meaning |
| --- | --- |
| `unit` | Fast isolated tests with no real external I/O |
| `integration` | Cross-package wiring without live cloud dependencies |
| `regression` | Guard for a previously fixed bug |
| `property` | Hypothesis/property-style invariant checks |
| `bdd` | Gherkin acceptance scenarios run through `pytest-bdd` |
| `e2e` | Whole workflow or deployed/public contract checks |
| `circleci` | Safe for CI without local credentials |
| `local_credentials` | Requires local credentials or token files |

Every new test file should set `pytestmark = pytest.mark.<marker>` at module
scope. The CI unit job filters markers, so unmarked tests are easy to miss.

## Choosing a Test Type

Use unit tests for local behavior, validation, mapping, and SDK error
translation.

Use integration tests when the real FastAPI app, generated client, dependency
injection, or package boundary matters.

Use property tests for invariants over large input spaces, especially
conversation trimming, ID validation, auth signing, state files, and byte/payload
validation.

Use BDD tests for wrapper-facing product contracts that should read like
acceptance criteria: signed request handling, confirmation flows, and attachment
outcomes.

Use e2e tests for black-box behavior through a CLI, HTTP server, deployed app,
or full public workflow.

Use fuzz harnesses for untrusted parsing paths that must survive corrupted or
malicious input without unexpected exception types.

## Test Design Checklist

For each changed behavior, cover:

- Happy path.
- Failure path.
- Boundary conditions.
- Public error shape or exception type.
- State cleanup and rollback behavior when state is involved.
- Retry, idempotency, replay, timeout, or concurrency behavior for networked and
  stateful code.

Prefer assertions on observable behavior over private helpers. When a response
shape is the contract, assert the full payload.

## Shared Fixtures

| Location | Purpose |
| --- | --- |
| `conftest.py` | Repository-wide defaults and shared FastAPI auth override |
| `tests/bdd/test_wrapper_acceptance.py` | BDD fake AI/storage clients and wrapper acceptance step definitions |
| `src/ai_server/tests/conftest.py` | Fake AI/storage clients, isolated AI router app, telemetry reset, e2e gating |
| `src/aws_client_service/aws_client_service/tests/conftest.py` | Storage service `TestClient` and storage dependency override |
| `test_support/storage_fakes.py` | Deterministic file-backed `CloudStorageClient` fake |

Keep fixtures near the narrowest scope that needs them. Name fixtures for the
capability they provide, not the concrete mock they use.

## Test Map

### Repository Integration and E2E

| File | Covers |
| --- | --- |
| `tests/bdd/features/chat_turn.feature` | Signed wrapper reply outcome and read-only tool availability |
| `tests/bdd/features/wrapper_signed_auth.feature` | Missing, tampered, and replayed signed-request failures |
| `tests/bdd/features/confirmation_flow.feature` | Delete confirmation acceptance flow |
| `tests/bdd/features/attachment_ingestion.feature` | Attachment upload, partial success, and error outcomes |
| `tests/integration/test_adapter_integration.py` | FastAPI app, generated client, adapter workflow, auth/service failure classification |
| `tests/integration/test_client_integration.py` | `aws_client_impl.get_client_impl()` wiring |
| `tests/integration/test_oauth_integration.py` | Local HTTP round-trip behavior for OAuth helpers |
| `tests/integration/test_service_integration.py` | Service dependency injection and endpoint wiring |
| `tests/e2e/test_main_application.py` | Black-box `main.py` subprocess behavior |
| `tests/e2e/test_service_e2e.py` | Public storage HTTP contract and auth checks |

### AI Contract

| File | Covers |
| --- | --- |
| `src/ai_client_api/tests/test_client.py` | Abstract client contract expectations |
| `src/ai_client_api/tests/test_conversation.py` | Conversation examples, trimming, serialization |
| `src/ai_client_api/tests/test_conversation_properties.py` | Hypothesis invariants for conversation history |
| `src/ai_client_api/tests/test_exceptions.py` | Exception hierarchy |
| `src/ai_client_api/tests/test_models.py` | Shared model defaults and validation |

### AI Server and Runtime

| File | Covers |
| --- | --- |
| `src/ai_server/tests/test_auth.py` | HMAC signing/auth verification |
| `src/ai_server/tests/test_auth_properties.py` | Signing invariants under generated inputs |
| `src/ai_server/tests/test_request_state.py` | Durable idempotency/replay state |
| `src/ai_server/tests/test_router.py` | Route behavior and AI error mapping |
| `src/ai_server/tests/test_router_properties.py` | Request validation and token-bucket invariants |
| `src/ai_server/tests/test_sessions.py` | Session persistence and recovery |
| `src/ai_server/tests/test_session_properties.py` | Session ID and persistence invariants |
| `src/ai_server/tests/test_wrapper_client.py` | Reference helpers for signed wrapper requests |
| `src/ai_server/tests/test_wrapper_contract.py` | Primary wrapper HTTP contract and idempotent replay |
| `src/ai_server/tests/test_e2e.py` | Optional live deployed AI server checks |
| `src/nimbus_runtime/tests/test_runtime.py` | Runtime orchestration, confirmation flow, tools, telemetry |

### Storage Implementation, Service, and Adapter

| File or directory | Covers |
| --- | --- |
| `src/aws_client_impl/tests/` | S3 operations, multipart upload, downloads, OAuth helpers, token storage |
| `src/aws_client_service/aws_client_service/tests/` | Storage HTTP endpoints and backend failure mapping |
| `src/aws_client_service/tests/` | GitHub OAuth routes and token store lifecycle |
| `src/aws_client_adapter/tests/test_service_adapter.py` | Generated-client adapter mapping and domain exception translation |

### OpenRouter Implementation

| File | Covers |
| --- | --- |
| `src/openrouter_ai_client_impl/tests/test_cli.py` | CLI behavior and sessions |
| `src/openrouter_ai_client_impl/tests/test_cloud_storage_tools.py` | AI storage tool bindings |
| `src/openrouter_ai_client_impl/tests/test_config.py` | Environment/config parsing |
| `src/openrouter_ai_client_impl/tests/test_openrouter_client.py` | Provider transport, fallback, error mapping |
| `src/openrouter_ai_client_impl/tests/test_openrouter_integration.py` | Provider-integration style behavior without live provider dependencies |

## Patterns to Copy

- Property/stateful invariants: `src/ai_client_api/tests/test_conversation_properties.py`
- BDD wrapper acceptance: `tests/bdd/test_wrapper_acceptance.py`
- HTTP contract testing with fakes: `tests/integration/test_adapter_integration.py`
- Route tests with dependency overrides:
  `src/aws_client_service/aws_client_service/tests/test_download_endpoint.py`
- Error translation across boundaries:
  `src/aws_client_adapter/tests/test_service_adapter.py`

## New Test Template

```python
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def subject() -> MyType:
    return MyType(...)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("a", "A"),
        ("b", "B"),
    ],
)
def test_transform_returns_expected_value(
    subject: MyType,
    raw: str,
    expected: str,
) -> None:
    assert subject.transform(raw) == expected


def test_transform_rejects_empty_input(subject: MyType) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        subject.transform("")
```

## Hypothesis and Temporary Files

Use `tempfile.TemporaryDirectory()` inside Hypothesis tests that write to disk.
Do not rely on `tmp_path` for per-example isolation; every example in one test
function shares the same `tmp_path`.

## BDD Acceptance Tests

BDD scenarios live under `tests/bdd/features/` and are executed by
`pytest-bdd` through `tests/bdd/test_wrapper_acceptance.py`.

Run them with:

```shell
uv run pytest tests/bdd -q --no-cov
```

or:

```shell
just bdd
```

Add BDD scenarios only for stable product contracts. Keep validation matrices,
private helper branches, fuzz cases, and low-level status mapping in the normal
pytest layers.

## Full Documentation

- `docs/source/testing.md`
- `docs/source/testing-bdd.md`
- `fuzz/README.md`
- `AGENTS.md`
