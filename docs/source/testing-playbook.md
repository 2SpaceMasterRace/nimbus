# Testing Playbook

This playbook is the “what do I do in this repo?” companion to the beginner
testing pages.

Read the foundation pages first if any term here feels unfamiliar:
{doc}`testing-pytest-basics`, {doc}`testing-running-debugging`,
{doc}`testing-fixtures`, {doc}`testing-parametrize`,
{doc}`testing-monkeypatch-mocking`, {doc}`testing-hypothesis`, and
{doc}`testing-http-integration-e2e`.

## The first question

Before writing a test, ask:

> What promise does this code make to the next layer up?

That one question tells you almost everything:

- where the test belongs,
- which layer to use,
- what to assert,
- which failures to cover,
- and which dependencies should be real, fake, or mocked.

## The five-minute workflow

When you add or change behavior:

1. Name the component.
2. Name the public boundary.
3. Write the contract in one sentence.
4. Pick the cheapest test layer that proves it.
5. Add the test near similar tests.
6. Run the narrowest command first.
7. Run the broader command before finishing.

Example:

```text
Component: CloudStorageServiceAdapter
Boundary: generated HTTP client -> cloud_storage_api domain contract
Contract: service 401 responses become AuthenticationError
Layer: unit test
File: src/aws_client_adapter/tests/test_service_adapter.py
Command: uv run pytest src/aws_client_adapter/tests/test_service_adapter.py -q --no-cov
```

## Where tests live

This repo has two test zones:

```text
tests/
├── integration/
└── e2e/

src/
├── ai_client_api/tests/
├── ai_server/tests/
├── aws_client_adapter/tests/
├── aws_client_impl/tests/
├── aws_client_service/tests/
├── aws_client_service/aws_client_service/tests/
├── nimbus_runtime/tests/
└── openrouter_ai_client_impl/tests/
```

Use package-local tests for package-local behavior.

Use repository-level `tests/integration/` when multiple packages must agree.

Use repository-level `tests/e2e/` when the caller should treat the system as a
black box.

Use repository-level `tests/bdd/` when the behavior is a wrapper-facing
acceptance contract that should also read as product documentation.

## Which marker should I use?

Every new test file should have one module-level marker:

```python
import pytest

pytestmark = pytest.mark.unit
```

Use this table:

| Marker | Use it when | Run command |
| --- | --- | --- |
| `unit` | The test isolates one module or route with mocks/fakes. | `uv run pytest -m unit --no-cov` |
| `integration` | Real internal components talk through real boundaries. | `uv run pytest tests/integration --no-cov` |
| `property` | Hypothesis generates inputs for an invariant. | `uv run pytest -m property --no-cov` |
| `regression` | A previous bug or high-risk failure gets locked down. | `uv run pytest -m regression --no-cov` |
| `bdd` | Gherkin acceptance scenarios run through `pytest-bdd`. | `uv run pytest tests/bdd --no-cov` |
| `e2e` | The test uses a public workflow or subprocess. | `uv run pytest tests/e2e --no-cov` |
| `circleci` | Safe for CI without local credentials. | Used by CI marker filters. |
| `local_credentials` | Requires local secrets or cloud credentials. | Run manually only. |

If one file contains mostly unit tests but one test is also a regression, keep
the file marker as `unit` and add `@pytest.mark.regression` to that one test.

## How to write a unit test

Use a unit test when the behavior is local.

Good unit-test targets:

- validation,
- error translation,
- local state transitions,
- route response mapping,
- object serialization,
- helper functions,
- and SDK boundary handling with mocks.

### Unit example: adapter error mapping

Contract:

> If the service returns `404` for a missing object, callers should see
> `ObjectNotFoundError`, not a raw HTTP response.

Test:

```python
def test_delete_file_maps_not_found_to_domain_exception(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "aws_client_adapter.service_adapter.delete_object_api.sync_detailed",
        return_value=_response(
            HTTPStatus.NOT_FOUND,
            content=b'{"detail":"Object was missing"}',
        ),
    )
    adapter = _make_adapter()

    with pytest.raises(ObjectNotFoundError, match="Object was missing"):
        adapter.delete_file("docs-bucket", "docs/a.txt")
```

Run:

```shell
uv run pytest src/aws_client_adapter/tests/test_service_adapter.py \
  -k "not_found" -q --no-cov
```

Passing output:

```text
.                                                                        [100%]
1 passed, 34 deselected in 0.06s
```

Why this is good:

- it calls the real adapter method,
- it mocks only the generated-client boundary,
- it asserts the domain exception,
- and it documents the adapter contract.

## How to write a route test

Route tests usually use FastAPI `TestClient`.

Use a route test when the behavior is HTTP-facing but you do not need a real
server.

Contract:

> Missing wrapper signing headers return `401`.

Test:

```python
def test_missing_signed_headers_returns_401(client: TestClient) -> None:
    response = client.post(
        "/ai/chat/turn",
        json={
            "platform": "slack",
            "workspace_id": "T123TEAM",
            "channel_id": "C123CHAN",
            "thread_id": "1713840000.123456",
            "message_id": "1713840000.123456",
            "user_id": "U123USER",
            "text": "hi",
            "idempotency_key": "slack:T123TEAM:event:missing-headers",
            "request_id": "req-missing-headers",
        },
    )

    assert response.status_code == 401
```

Run:

```shell
uv run pytest src/ai_server/tests/test_wrapper_contract.py \
  -k "missing_signed_headers" -q --no-cov
```

Output:

```text
.                                                                        [100%]
1 passed, 52 deselected in 0.18s
```

Route tests should assert caller-visible behavior:

- status code,
- JSON body,
- headers when important,
- persisted state when relevant,
- and cleanup side effects.

## How to write an integration test

Use an integration test when several real pieces must agree.

In this repo, integration tests often look like this:

```text
test
 |
 v
CloudStorageServiceAdapter
 |
 v
generated service client
 |
 v
FastAPI app
 |
 v
deterministic storage fake
```

Contract:

> The adapter and service agree on upload/list/info/download/delete semantics.

Test shape:

```python
def test_adapter_service_workflow_round_trips_object_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    source = tmp_path / "report.txt"
    downloaded = tmp_path / "downloaded.txt"
    source.write_text("quarterly report", encoding="utf-8")

    monkeypatch.setenv("API_KEY", API_KEY)
    monkeypatch.setenv("CLOUD_STORAGE_SERVICE_BASE_URL", "http://testserver")

    fake_storage = FileBackedStorageClient(storage_root)

    with override_storage(fake_storage):
        with TestClient(app) as test_client:
            adapter = _adapter_for_test_server(test_client)

            uploaded = adapter.upload_file(
                "demo-bucket",
                str(source),
                "nested/report.txt",
            )
            listed = adapter.list_files("demo-bucket", "nested/")
            info = adapter.download_file(
                "demo-bucket",
                "nested/report.txt",
                str(downloaded),
            )

    assert uploaded.object_name == "nested/report.txt"
    assert [item.object_name for item in listed] == ["nested/report.txt"]
    assert info.object_name == "nested/report.txt"
    assert downloaded.read_text(encoding="utf-8") == "quarterly report"
```

Run all integration tests:

```shell
uv run pytest tests/integration --no-cov
```

Run the storage adapter integration file:

```shell
uv run pytest tests/integration/test_adapter_integration.py -q --no-cov
```

Expected output:

```text
...                                                                      [100%]
3 passed in 0.31s
```

Use integration tests to check:

- generated client and service path names match,
- auth headers work through the real route,
- response payloads map back into domain objects,
- object workflow invariants hold,
- and failure classifications survive the transport boundary.

## How to write an e2e test

Use e2e when the system should look like a black box.

Contract:

> `main.py` can run as a real subprocess and print the expected public output.

Test shape:

```python
def test_main_application_runs_successfully(tmp_path: Path) -> None:
    fake_pkg_root = tmp_path / "fake_packages"
    _write_fake_aws_client_impl(fake_pkg_root)
    env = _subprocess_env(extra_pythonpath=[str(fake_pkg_root)])

    result = subprocess.run(
        [sys.executable, "main.py"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0
    assert "Files in" in result.stdout
```

Run:

```shell
uv run pytest tests/e2e/test_main_application.py -q --no-cov
```

Output:

```text
......                                                                   [100%]
6 passed in 0.72s
```

E2E tests should assert public behavior:

- exit code,
- stdout/stderr,
- HTTP status,
- public response body,
- and no dependence on private implementation details.

## How to write a BDD acceptance test

Use BDD when a behavior should read like acceptance criteria. The feature file
states the product promise; the Python step definitions reuse pytest fixtures to
exercise the real boundary.

Contract:

> A destructive delete request must return `confirmation_required`, and only the
> same actor in the same conversation may confirm it.

Feature shape:

```gherkin
Scenario: Same actor can confirm a pending delete
  Given the wrapper signing secret is configured
  And a pending delete exists for "reports/2024/old.csv"
  And the wrapper sends a Slack message "yes, delete reports/2024/old.csv" with event id "evt-bdd-delete-confirm"
  When the wrapper posts the signed chat turn
  Then the response status is 200
  And the response outcome is "reply"
  And the storage client deleted "reports/2024/old.csv"
```

Run:

```shell
uv run pytest tests/bdd -q --no-cov
```

BDD scenarios should be short and product-shaped. Do not encode every Pydantic
field limit or every malformed input permutation in Gherkin; use unit and
property tests for those.

## How to write a property test

Use Hypothesis when examples are not enough.

Contract:

> Signing and verifying the same payload succeeds for any generated valid
> method, path, timestamp, nonce, body, and secret.

Test shape:

```python
@given(_method, _path, _timestamp, _nonce, _body, _secret)
def test_sign_then_verify_accepts_same_payload(
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
    secret: str,
) -> None:
    headers = sign_request(
        method=method,
        path=path,
        body=body,
        secret=secret,
        timestamp=timestamp,
        nonce=nonce,
    )

    verify_request(
        method=method,
        path=path,
        body=body,
        headers=headers,
        secret=secret,
        now=float(timestamp),
    )
```

Run:

```shell
uv run pytest -m property --no-cov
```

Output:

```text
......................................                                   [100%]
38 passed in 3.82s
```

Property tests should have a sentence you can say out loud:

- “Any valid ID round-trips.”
- “Changing signed bytes invalidates the signature.”
- “Token buckets never exceed capacity.”
- “Conversation trimming never leaves an invalid tool-result shape.”

If you cannot name the invariant, use example tests first.

## How to write a fuzz harness

Most contributors will not need to add fuzz harnesses right away. They matter
for parser-like code.

A fuzz harness usually:

1. Accepts raw bytes.
2. Feeds them into one parser or state reader.
3. Ignores expected validation failures.
4. Crashes only on unexpected exceptions.

Run smoke mode:

```shell
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_conversation.py
```

Output shape:

```text
fuzz_conversation smoke mode completed 209 inputs
```

Add a fuzz harness when a function consumes raw bytes, JSON text, filenames, or
state files from outside the trust boundary.

## How to use monkeypatch in repo tests

Use `monkeypatch` for temporary global changes.

Common patterns:

```python
def test_uses_session_dir_from_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AI_SESSION_DIR", str(tmp_path / "sessions"))

    path = get_session_dir()

    assert path == tmp_path / "sessions"
```

```python
def test_missing_secret_disables_signed_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AI_SERVER_SIGNING_SECRET", raising=False)

    app = create_app()

    assert app is not None
```

```python
def test_temp_file_cleanup_on_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", failing_temp_file_factory)

    result = runtime.ingest_attachment(...)

    assert result.outcome == "error"
```

Monkeypatch changes are automatically undone after the test.

## How to use mocker in repo tests

Use `mocker.patch()` when replacing a specific dependency call.

Example:

```python
def test_remote_operations_map_transport_failures_to_backend_errors(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "aws_client_adapter.service_adapter.get_file_info_api.sync_detailed",
        side_effect=httpx.ReadTimeout("info failed"),
    )
    adapter = _make_adapter()

    with pytest.raises(StorageBackendError, match="Get file info failed"):
        adapter.get_file_info("docs-bucket", "docs/a.txt")
```

The production promise:

> Callers should never see raw `httpx` exceptions from the storage adapter.

## How to assert cleanup

Cleanup is behavior. Test it directly.

Example:

```python
def test_download_removes_temp_file_after_successful_response(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    captured_dest: dict[str, str] = {}

    def fake_download(_bucket: str, _key: str, dest: str) -> ObjectInfo:
        captured_dest["path"] = dest
        Path(dest).write_bytes(b"cleanup me")
        return _stub_object_info(_key)

    mock_storage_client.download_file.side_effect = fake_download

    response = client.get(
        "/download",
        params={"container": "my-bucket", "object_name": "cleanup.txt"},
    )

    assert response.status_code == 200
    assert response.content == b"cleanup me"
    assert not Path(captured_dest["path"]).exists()
```

This test protects against a real operational bug: leaking temporary files over
time.

## Component guide

Use this section when you know which package you are touching.

### ai_client_api

Best layers:

- unit tests for models and exceptions,
- property tests for `Conversation`.

Ask:

- Does serialization round-trip?
- Does trimming preserve valid message order?
- Does clearing preserve the system prompt?
- Does the exception hierarchy remain stable?

Run:

```shell
uv run pytest src/ai_client_api/tests -q --no-cov
uv run pytest src/ai_client_api/tests/test_conversation_properties.py -q --no-cov
```

### ai_server

Best layers:

- route tests with `TestClient`,
- BDD acceptance scenarios for wrapper-facing product flows,
- property tests for auth and request validation,
- live e2e tests only when explicitly configured.

Ask:

- Does signed auth fail closed?
- Does replay protection work?
- Does idempotency survive restart?
- Does the response status match the failure type?
- Does session persistence use isolated directories?

Run:

```shell
uv run pytest src/ai_server/tests -q --no-cov
uv run pytest src/ai_server/tests/test_wrapper_contract.py -q --no-cov
uv run pytest src/ai_server/tests/test_auth_properties.py -q --no-cov
uv run pytest tests/bdd -q --no-cov
```

Live e2e shape:

```shell
RUN_AI_SERVER_E2E=1 AI_SERVER_BASE_URL="https://..." \
  uv run pytest src/ai_server/tests/test_e2e.py -q --no-cov
```

### nimbus_runtime

Best layers:

- unit tests for orchestration,
- regression tests for cleanup,
- future concurrency and recovery tests.

Ask:

- Who owns state?
- What survives across turns?
- Does destructive work require confirmation?
- Does telemetry preserve useful event ordering?
- Do attachment temp files get cleaned up on failures?

Run:

```shell
uv run pytest src/nimbus_runtime/tests/test_runtime.py -q --no-cov
```

### openrouter_ai_client_impl

Best layers:

- unit tests with fake model behavior,
- integration-style tests without real provider calls,
- streaming tests for provider event translation.

Ask:

- Does fallback selection work?
- Do provider failures map to domain exceptions?
- Does the step budget stop runaway tool loops?
- Does conversation rollback happen on failure?
- Does event-listener failure stay isolated?

Run:

```shell
uv run pytest src/openrouter_ai_client_impl/tests/test_openrouter_client.py -q --no-cov
uv run pytest src/openrouter_ai_client_impl/tests/test_cloud_storage_tools.py -q --no-cov
```

### nimbus_cli and nimbus_slack

Best layers:

- CLI tests for onboarding, local sessions, remote signing, and resume behavior,
- Slack tests for signature verification, retry dedupe, body normalization, and
  threaded replies,
- eval harness tests for runtime safety and replay promises.

Run:

```shell
uv run pytest src/nimbus_cli/tests/ -q --no-cov
uv run pytest src/nimbus_slack/tests/ -q --no-cov
uv run pytest -m eval tests/evals -q --no-cov
```

### aws_client_adapter

Best layers:

- unit tests for generated-client response mapping,
- integration tests for adapter/service compatibility.

Ask:

- Does every service status map to the correct domain exception?
- Are local file failures separate from remote failures?
- Are generated `Unset` values normalized?
- Does the adapter preserve stable ordering where promised?

Run:

```shell
uv run pytest src/aws_client_adapter/tests/test_service_adapter.py -q --no-cov
uv run pytest tests/integration/test_adapter_integration.py -q --no-cov
```

### aws_client_service

Best layers:

- route tests with `TestClient`,
- auth route tests,
- integration tests for dependency wiring.

Ask:

- Does each endpoint preserve its HTTP contract?
- Are missing query params `422`?
- Are storage errors mapped to `404` or `502` correctly?
- Are temp files removed?
- Do auth states and token files clean up?

Run:

```shell
uv run pytest src/aws_client_service/aws_client_service/tests -q --no-cov
uv run pytest src/aws_client_service/tests/test_auth.py -q --no-cov
```

### aws_client_impl

Best layers:

- unit tests with strict mocks for boto3-facing behavior,
- future high-fidelity integration tests for S3-compatible behavior.

Ask:

- Does boto3 receive the right arguments?
- Do botocore failures become domain errors?
- Does multipart upload abort or complete cleanly?
- Are local file-object errors distinct from provider errors?

Run:

```shell
uv run pytest src/aws_client_impl/tests -q --no-cov
```

## What good assertions look like

Prefer specific assertions:

```python
assert response.status_code == 401
assert response.json() == {"detail": "Authentication required"}
```

Prefer whole payloads when shape matters:

```python
assert repeated_delete == {
    "deleted": False,
    "version_id": None,
    "request_charged": None,
}
```

Prefer domain exceptions at abstraction boundaries:

```python
with pytest.raises(AuthenticationError, match="Authentication required"):
    adapter.list_files("docs-bucket", "")
```

Avoid vague assertions:

```python
assert response
assert result is not None
with pytest.raises(Exception):
    ...
```

Those can pass while the contract is broken.

## What to run before finishing

Small Python-only test change:

```shell
uv run pytest path/to/test_file.py -q --no-cov
uv run ruff check path/to/test_file.py
uv run ruff format path/to/test_file.py
```

Adapter or service boundary change:

```shell
uv run pytest src/aws_client_adapter/tests/test_service_adapter.py -q --no-cov
uv run pytest tests/integration/test_adapter_integration.py -q --no-cov
uv run pytest src/ -q --cov --cov-report=term-missing
```

Docs change:

```shell
uv run sphinx-build -E docs/source docs/build/html
```

Broad behavioral change:

```shell
uv run pytest --no-cov
uv run pytest src/ -q --cov --cov-report=term-missing
uv run pytest tests/bdd -q --no-cov
uv run ruff check .
uv run mypy --strict .
```

## A final checklist

Before calling a test “good,” check:

- The name describes the behavior.
- The test has one clear contract.
- The setup is readable.
- External services are mocked, faked, or explicitly live-gated.
- Failure paths are tested, not just happy paths.
- Cleanup is asserted when cleanup matters.
- The test would still be useful after a refactor.
- The command to run it is obvious from nearby docs or file location.

Good tests make future work calmer. They turn “I hope this still works” into “we
know which promise is protected.”
