# Fixtures

Fixtures are one of pytest's most useful ideas.

A fixture is a small helper that prepares something a test needs.

You use fixtures when several tests need the same setup:

- a temporary directory,
- a fake storage client,
- a FastAPI test client,
- a configured adapter,
- a sample request body,
- or a monkeypatched environment.

## The problem fixtures solve

Without fixtures, tests often repeat setup:

```python
def test_cart_starts_empty() -> None:
    cart = ShoppingCart()

    assert cart.items() == []


def test_cart_remembers_added_items() -> None:
    cart = ShoppingCart()

    cart.add("notebook")

    assert cart.items() == ["notebook"]
```

That is fine for tiny examples. In real code, setup can be longer:

```python
client = TestClient(app)
app.dependency_overrides[get_storage_client] = lambda: fake_storage
fake_storage.create_container("docs-bucket")
```

Repeating that in every test is noisy and easy to get wrong.

## A first fixture

```python
import pytest


@pytest.fixture
def cart() -> ShoppingCart:
    return ShoppingCart()
```

Now any test can ask for `cart` by naming it as an argument:

```python
def test_cart_starts_empty(cart: ShoppingCart) -> None:
    assert cart.items() == []


def test_cart_remembers_added_items(cart: ShoppingCart) -> None:
    cart.add("notebook")

    assert cart.items() == ["notebook"]
```

Pytest sees the `cart` argument, finds the fixture named `cart`, calls it, and
passes the result into the test.

```text
pytest runs test
       |
       v
test has argument: cart
       |
       v
pytest calls cart() fixture
       |
       v
pytest passes ShoppingCart into test
```

Output:

```text
..                                                                       [100%]
2 passed in 0.01s
```

## Fixtures are fresh by default

By default, pytest calls the fixture once per test.

```python
@pytest.fixture
def cart() -> ShoppingCart:
    print("creating cart")
    return ShoppingCart()


def test_one(cart: ShoppingCart) -> None:
    cart.add("one")
    assert cart.items() == ["one"]


def test_two(cart: ShoppingCart) -> None:
    assert cart.items() == []
```

Run:

```shell
uv run pytest test_cart.py -q -s
```

Output:

```text
creating cart
.creating cart
.                                                                       [100%]
2 passed in 0.01s
```

The second test gets a new cart. That isolation is important. Tests should not
secretly depend on the order they run in.

## Built-in fixture: tmp_path

`tmp_path` is a fixture pytest gives you automatically. It creates a temporary
directory for one test.

```python
from pathlib import Path


def test_writes_file(tmp_path: Path) -> None:
    destination = tmp_path / "hello.txt"

    destination.write_text("hello", encoding="utf-8")

    assert destination.read_text(encoding="utf-8") == "hello"
```

Output:

```text
.                                                                        [100%]
1 passed in 0.01s
```

Why this is better than writing to a real project file:

- it does not dirty your repo,
- it does not collide with another test,
- pytest cleans it up later,
- and every test gets its own directory.

## Fixtures can use other fixtures

Fixtures can depend on fixtures.

```python
@pytest.fixture
def sample_file(tmp_path: Path) -> Path:
    path = tmp_path / "sample.txt"
    path.write_text("hello", encoding="utf-8")
    return path


def test_sample_file_contains_text(sample_file: Path) -> None:
    assert sample_file.read_text(encoding="utf-8") == "hello"
```

Pytest builds the dependency graph:

```text
tmp_path fixture -> sample_file fixture -> test_sample_file_contains_text()
```

## Fixture cleanup with yield

Sometimes setup needs cleanup.

Use `yield`:

```python
@pytest.fixture
def configured_app() -> Iterator[TestClient]:
    app.dependency_overrides[get_storage_client] = lambda: FakeStorageClient()
    client = TestClient(app)

    yield client

    app.dependency_overrides.clear()
```

The code before `yield` runs before the test.
The code after `yield` runs after the test, even if the test fails.

```text
pytest  -> fixture: run setup before yield
fixture -> pytest:  yield client
pytest  -> test:    run test(client)
pytest  -> fixture: run cleanup after yield
```

## Fixtures in this repository

This repo uses fixtures to keep tests readable.

Examples:

- `conftest.py` has repo-wide test defaults.
- `src/ai_server/tests/conftest.py` owns fake AI and storage clients for router tests.
- `src/aws_client_service/aws_client_service/tests/conftest.py` owns shared FastAPI `TestClient` setup.
- `tests/test_support/storage_fakes.py` provides a deterministic file-backed storage fake.

Here is the style to copy:

```python
@pytest.fixture
def adapter() -> CloudStorageServiceAdapter:
    return CloudStorageServiceAdapter(Client(base_url="http://service.test"))


def test_adapter_rejects_empty_container(adapter: CloudStorageServiceAdapter) -> None:
    with pytest.raises(InvalidContainerError, match="Container cannot be empty"):
        adapter.list_files("", "")
```

The fixture name should describe what the test receives. `adapter` is better
than `thing` or `setup`.

## Where fixtures live: conftest.py

Pytest has a special file name: `conftest.py`.

Fixtures in `conftest.py` are automatically available to tests below that
directory. Tests do not import from `conftest.py`; pytest finds it.

Example layout:

```text
src/ai_server/tests/
├── conftest.py
├── test_router.py
└── test_wrapper_contract.py
```

If `conftest.py` defines this:

```python
@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-API-Key": "test-api-key"}
```

Then `test_router.py` can use it directly:

```python
def test_history_requires_auth(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    response = client.get("/sessions/demo/history", headers=auth_headers)

    assert response.status_code == 200
```

No import is needed.

Use `conftest.py` for fixtures that many tests in a directory need. Keep
one-off fixtures inside the test file.

## Autouse fixtures

An `autouse` fixture runs automatically for every test in its scope.

Example from this repo's style:

```python
@pytest.fixture(autouse=True)
def reset_runtime_metrics() -> None:
    runtime_telemetry.reset()
```

Tests do not request it:

```python
def test_runtime_records_wrapper_and_ai_metrics(tmp_path: Path) -> None:
    ...
```

Pytest still runs `reset_runtime_metrics()` before the test.

Use `autouse=True` sparingly. It is good for invisible cleanup, such as clearing
global telemetry counters. It is bad when it hides important setup that the test
reader needs to understand.

## usefixtures

Sometimes a test needs a fixture's side effect but not the returned value.

```python
@pytest.mark.usefixtures("mock_storage_client")
def test_download_missing_bucket_name(client: TestClient) -> None:
    response = client.get("/download", params={"object_name": "docs/a.txt"})

    assert response.status_code == 422
```

The test never names `mock_storage_client` as an argument, but pytest still runs
the fixture.

Use this when the fixture configures global or app-level state.

## Fixture scope

Most fixtures should use the default function scope, which means one fresh value
per test.

Pytest also supports broader scopes:

```python
@pytest.fixture(scope="session")
def expensive_dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("dataset")
    path = directory / "data.json"
    path.write_text("{}", encoding="utf-8")
    return path
```

Common scopes:

| Scope | Created |
| --- | --- |
| `function` | Once per test function. This is the default. |
| `class` | Once per test class. |
| `module` | Once per test file. |
| `session` | Once per pytest run. |

In this repo, prefer function scope unless setup is genuinely expensive and
immutable. Shared mutable state is how tests start quietly depending on each
other.

## Beginner fixture checklist

Use a fixture when:

- at least two tests need the same setup,
- the setup distracts from the behavior being tested,
- cleanup must happen reliably,
- or the test needs isolated state such as a temporary directory.

Avoid fixtures when:

- the setup is one simple line used once,
- the fixture hides important test behavior,
- or the fixture becomes a giant “do everything” helper.

Good fixtures make tests shorter and clearer. Bad fixtures make tests magical.
