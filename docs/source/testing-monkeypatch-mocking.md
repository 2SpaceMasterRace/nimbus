# Monkeypatching And Mocking

Real code talks to the outside world.

It reads environment variables, opens files, calls APIs, checks the clock, and
uses third-party SDKs. Tests should usually avoid real outside systems because
they make tests slow, flaky, expensive, or dangerous.

Monkeypatching and mocking let a test replace one dependency with a controlled
stand-in.

## The idea

Suppose production code does this:

```python
import os


def service_base_url() -> str:
    return os.environ.get("CLOUD_STORAGE_SERVICE_BASE_URL", "http://127.0.0.1:8000")
```

A test can control the environment:

```python
def test_service_base_url_uses_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLOUD_STORAGE_SERVICE_BASE_URL", "https://service.test")

    assert service_base_url() == "https://service.test"
```

Output:

```text
.                                                                        [100%]
1 passed in 0.01s
```

`monkeypatch` is a pytest fixture. It changes something for one test and then
automatically restores it afterward.

```text
test        -> monkeypatch: setenv("CLOUD_STORAGE_SERVICE_BASE_URL", "https://service.test")
monkeypatch -> environment: temporarily change value
test        -> environment: code reads value
monkeypatch -> environment: restore original value after test
```

## Monkeypatching environment variables

Set an environment variable:

```python
def test_api_key_enables_authenticated_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("API_KEY", "test-api-key")

    adapter = get_client_impl()

    assert isinstance(adapter._service_client, AuthenticatedClient)
```

Delete an environment variable:

```python
def test_missing_api_key_uses_plain_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("API_KEY", raising=False)

    adapter = get_client_impl()

    assert isinstance(adapter._service_client, Client)
```

`raising=False` means “do not fail if this variable was already missing.”

## Monkeypatching functions

Suppose production code calls GitHub:

```python
def callback(code: str) -> str:
    token = exchange_code_for_token(code)
    return f"stored {token}"
```

In a unit test, we do not want to call real GitHub. We replace the function:

```python
def test_callback_stores_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_exchange_code_for_token(code: str) -> str:
        assert code == "abc123"
        return "fake-token"

    monkeypatch.setattr(
        "aws_client_service.routes.auth.exchange_code_for_token",
        fake_exchange_code_for_token,
    )

    result = callback("abc123")

    assert result == "stored fake-token"
```

Important rule:

> Patch the name where the code under test looks it up.

If `aws_client_service.routes.auth` imported `exchange_code_for_token`, patch
`aws_client_service.routes.auth.exchange_code_for_token`, not some other module
where the original function happened to be defined.

## What is a mock?

A mock is an object that records how it was called.

Pytest itself does not ship mocks, but this repo uses `pytest-mock`, which gives
us the `mocker` fixture.

```python
def test_delete_calls_generated_client(mocker: MockerFixture) -> None:
    generated_delete = mocker.patch(
        "aws_client_adapter.service_adapter.delete_object_api.sync_detailed",
        return_value=_response(HTTPStatus.OK, parsed=DeleteResultResponse(deleted=True)),
    )
    adapter = _make_adapter()

    result = adapter.delete_file("docs-bucket", "docs/a.txt")

    assert result["deleted"] is True
    generated_delete.assert_called_once_with(
        container="docs-bucket",
        object_name="docs/a.txt",
        client=adapter._service_client,
    )
```

Output:

```text
.                                                                        [100%]
1 passed in 0.01s
```

The mock both replaces the real function and remembers the call.

## MagicMock

`MagicMock` comes from Python's `unittest.mock`. It is useful when a dependency
has methods that the code under test will call.

Example:

```python
from unittest.mock import MagicMock


def test_download_route_calls_storage_client(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    mock_storage_client.download_file.return_value = ObjectInfo(
        object_name="docs/a.txt"
    )

    response = client.get(
        "/download",
        params={"container": "docs-bucket", "object_name": "docs/a.txt"},
    )

    assert response.status_code == 200
    mock_storage_client.download_file.assert_called_once()
```

Mocks can answer two questions:

- What should the dependency return?
- Was the dependency called the way we expected?

## create_autospec

Loose mocks accept any attribute name. That can hide typos.

```python
storage = MagicMock()
storage.donwload_file.return_value = None  # typo: donwload_file
```

That typo creates a fake mock method instead of failing.

`create_autospec()` builds a stricter mock from a real class or protocol:

```python
from unittest.mock import create_autospec


storage = create_autospec(CloudStorageClient, instance=True)
storage.download_file.return_value = ObjectInfo(object_name="docs/a.txt")
```

If you typo the method name, the test fails immediately:

```text
AttributeError: Mock object has no attribute 'donwload_file'
```

This repo prefers `create_autospec` for Python collaborators when practical,
especially at important boundaries.

## Mocking failures

Use `side_effect` when the dependency should raise:

```python
def test_download_maps_transport_failure_to_backend_error(
    mocker: MockerFixture,
) -> None:
    mocker.patch(
        "aws_client_adapter.service_adapter.download_file_api.sync_detailed",
        side_effect=httpx.ConnectError("connection reset"),
    )
    adapter = _make_adapter()

    with pytest.raises(StorageBackendError, match="Download failed"):
        adapter.download_file("docs-bucket", "docs/a.txt", "a.txt")
```

Why this is valuable:

- the test is fast,
- it does not need a real server,
- it proves `httpx.ConnectError` does not leak to callers,
- and it locks in the domain-level contract.

Failure output if the adapter leaks the raw exception:

```text
E   httpx.ConnectError: connection reset
```

That would tell us the abstraction boundary is broken.

## Fake versus mock

A fake is a small working implementation.

A mock is usually a programmable recorder.

Use a mock when you care about one call:

```python
generated_delete.assert_called_once()
```

Use a fake when you want a more realistic workflow:

```python
storage = FileBackedStorageFake(tmp_path)
adapter.upload_file("bucket", "local.txt", "remote.txt")
adapter.download_file("bucket", "remote.txt", "copy.txt")
```

```text
Mock: best for one boundary call       -> usually a unit test
Fake: best for workflow behavior       -> often an integration test
```

In this repo:

- mocks are common for SDK or generated-client failures,
- deterministic fakes are common for integration tests,
- live services are reserved for opt-in e2e tests.

## side_effect versus return_value

Use `return_value` when the dependency should return normally:

```python
mocker.patch(
    "aws_client_adapter.service_adapter.delete_object_api.sync_detailed",
    return_value=_response(HTTPStatus.OK, parsed=DeleteResultResponse(deleted=True)),
)
```

Use `side_effect` when the dependency should raise or run custom logic:

```python
mocker.patch(
    "aws_client_adapter.service_adapter.download_file_api.sync_detailed",
    side_effect=httpx.ConnectError("connection reset"),
)
```

Custom function side effects are useful when you need to inspect arguments:

```python
def fake_upload(**kwargs: object) -> Response[object]:
    assert kwargs["container"] == "docs-bucket"
    return _response(HTTPStatus.OK, parsed=ObjectInfoResponse(object_name="docs/a.txt"))


mocker.patch(
    "aws_client_adapter.service_adapter.upload_object_api.sync_detailed",
    side_effect=fake_upload,
)
```

## Do not mock what you are trying to test

If you are testing `CloudStorageServiceAdapter`, do not mock
`CloudStorageServiceAdapter.delete_file()` itself. That would only test the
mock.

Mock the dependency below it:

```python
mocker.patch(
    "aws_client_adapter.service_adapter.delete_object_api.sync_detailed",
    return_value=...,
)
```

Then call the real adapter:

```python
adapter.delete_file("docs-bucket", "docs/a.txt")
```

## Beginner checklist

Use `monkeypatch` for:

- environment variables,
- replacing module attributes,
- temporary path changes,
- one-test-only global changes.

Use `mocker.patch()` for:

- replacing a function and asserting how it was called,
- raising a fake exception from a dependency,
- returning a controlled response from a generated client or SDK.

Use a fake object when:

- the dependency has several operations,
- you want state to change realistically,
- or you are testing a workflow rather than one call.

The goal is control. A test should create the world it needs, run the behavior,
and cleanly put the world back.
