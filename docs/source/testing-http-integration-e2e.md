# HTTP, Integration, E2E, And Fuzz Tests

Unit tests are the first layer. This repo also tests real boundaries:

- FastAPI routes with `TestClient`,
- generated HTTP clients,
- handwritten adapters,
- deterministic fake storage,
- subprocess command-line entry points,
- live deployed service checks,
- and fuzz harnesses for parser-like code.

This page explains those layers from a beginner's point of view.

## The testing pyramid for this repo

```text
          Live e2e tests
       Deployed service checks
    -----------------------------
        Local e2e tests
    subprocesses, real app entrypoints
    -----------------------------
       Integration tests
 real app + generated client + adapter + fake storage
    -----------------------------
          Unit tests
 functions, methods, route mapping, error translation
```

The bottom is wider because unit tests are cheaper and faster. The top is
narrower because live tests are slower and depend on deployed infrastructure.

## FastAPI TestClient

`TestClient` lets a test call a FastAPI app without starting a real server.

Example:

```python
from fastapi.testclient import TestClient

from aws_client_service.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Output:

```text
.                                                                        [100%]
1 passed in 0.03s
```

What happened:

```text
test code -> TestClient -> FastAPI route -> response object -> assertions
```

No network port was opened. The request stayed in process.

## Route tests with dependency overrides

FastAPI apps often get dependencies through dependency injection.

In production:

```text
route -> get_storage_client() -> real S3 client
```

In a test:

```text
route -> get_storage_client() -> fake or mock storage client
```

That lets the test exercise the real route while avoiding real AWS.

Example:

```python
@pytest.fixture
def client(mock_storage_client: MagicMock) -> Iterator[TestClient]:
    app.dependency_overrides[get_storage_client] = lambda: mock_storage_client

    yield TestClient(app)

    app.dependency_overrides.clear()
```

Test:

```python
def test_download_returns_file_bytes(
    client: TestClient,
    mock_storage_client: MagicMock,
    tmp_path: Path,
) -> None:
    def fake_download(_bucket: str, _key: str, destination: str) -> ObjectInfo:
        Path(destination).write_bytes(b"hello")
        return ObjectInfo(object_name="docs/a.txt")

    mock_storage_client.download_file.side_effect = fake_download

    response = client.get(
        "/download",
        params={"container": "docs-bucket", "object_name": "docs/a.txt"},
    )

    assert response.status_code == 200
    assert response.content == b"hello"
```

Why this is useful:

- the route function is real,
- request parsing is real,
- response creation is real,
- storage is controlled by the test.

## What makes an integration test different?

A unit test usually isolates one module.

An integration test checks that several real pieces agree.

In this repo, the main storage integration path is:

```text
CloudStorageServiceAdapter
        |
        v
generated aws_s3_cloud_storage_service_client
        |
        v
FastAPI app from aws_client_service
        |
        v
deterministic file-backed storage fake
```

That test proves the adapter, generated client, service routes, auth headers,
and storage contract all line up.

## Integration workflow example

The integration test does a real workflow:

```python
source = tmp_path / "report-a.txt"
source.write_text("quarterly report", encoding="utf-8")

uploaded = adapter.upload_file(
    "demo-bucket",
    str(source),
    "nested/report-a.txt",
)
listed = adapter.list_files("demo-bucket", "nested/")
downloaded = tmp_path / "downloaded.txt"
info = adapter.download_file("demo-bucket", "nested/report-a.txt", str(downloaded))
deleted = adapter.delete_file("demo-bucket", "nested/report-a.txt")
repeated_delete = adapter.delete_file("demo-bucket", "nested/report-a.txt")

assert uploaded.object_name == "nested/report-a.txt"
assert [item.object_name for item in listed] == ["nested/report-a.txt"]
assert downloaded.read_text(encoding="utf-8") == "quarterly report"
assert info.object_name == "nested/report-a.txt"
assert deleted["deleted"] is True
assert repeated_delete["deleted"] is False
```

This is stronger than testing one method because it checks workflow invariants:

- upload creates an object,
- list can see it,
- download returns the same bytes,
- delete removes it,
- repeated delete is stable.

Run it:

```shell
uv run pytest tests/integration/test_adapter_integration.py -q --no-cov
```

Expected output:

```text
...                                                                      [100%]
3 passed in 0.31s
```

## Why deterministic fakes matter

A fake is a small implementation used by tests.

The file-backed storage fake under `test_support/storage_fakes.py` behaves like
a tiny storage backend on local disk. It is not a mock that only records calls.
It actually stores bytes in a temporary directory.

That gives us:

- reproducible tests,
- no AWS credentials,
- real state transitions,
- and realistic workflow assertions.

Do not use live AWS for ordinary integration tests. Live cloud tests should be
opt-in because they depend on credentials, network, account state, and service
availability.

## E2E tests

E2E means “end to end.” In this repo, an e2e test treats the public entry point
as a black box.

Example: `main.py` subprocess test.

```python
result = subprocess.run(
    [sys.executable, str(main_script)],
    check=False,
    capture_output=True,
    text=True,
    timeout=10,
    env=env,
)

assert result.returncode == 0
assert "Files in" in result.stdout
```

The test does not import and call `main.main()` directly. It runs Python the way
a user would.

```text
pytest -> subprocess -> python main.py -> stdout/stderr/exit code -> assertions
```

Run local e2e tests:

```shell
uv run pytest tests/e2e -q --no-cov
```

Possible output:

```text
........                                                                 [100%]
8 passed in 1.12s
```

## Live e2e tests

Some tests are designed for deployed services. They are gated by environment
variables so they do not run accidentally.

Example command shape:

```shell
RUN_AI_SERVER_E2E=1 \
AI_SERVER_BASE_URL="https://example.onrender.com" \
AI_SERVER_SIGNING_SECRET="..." \
uv run pytest src/ai_server/tests/test_e2e.py -q --no-cov
```

Use live e2e tests to verify:

- deployed health endpoints,
- signed wrapper auth,
- real network routing,
- idempotent retry behavior,
- and response shape from the deployed service.

Do not use live e2e tests for ordinary local iteration.

## Fuzz smoke tests

Fuzzing feeds malformed or unexpected bytes into parser-like code.

This repo has fuzz harnesses under `fuzz/`:

```shell
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_conversation.py
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_session_id.py
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_request_state.py
```

Smoke mode runs a fixed set of inputs without needing Atheris installed. It is a
portable safety check for CI and local machines.

Fuzzing is useful when the main question is:

> Can malformed bytes crash this parser or state reader?

It is different from Hypothesis:

| Tool | Main input style | Best for |
| --- | --- | --- |
| Hypothesis | Structured Python values | Invariants and state machines |
| Fuzzing | Raw bytes or byte-like data | Parsers, deserializers, corrupt files |

## How to choose the layer

Start low and move up only when the contract crosses a boundary.

| You need to prove... | Use... |
| --- | --- |
| A function rejects invalid input | Unit test |
| A route maps a storage exception to HTTP `404` | Route unit test with `TestClient` and mock storage |
| Adapter and service agree on response shape | Integration test |
| `main.py` works as a caller sees it | Local e2e subprocess test |
| Deployed service accepts signed requests | Live e2e test |
| Session ID parser handles weird bytes | Fuzz harness |

Good tests are not “unit tests everywhere” or “e2e tests everywhere.” Good
tests choose the cheapest layer that proves the promise.
