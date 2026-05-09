# Parametrization

Parametrization lets one test run with many inputs.

Use it when you are about to copy and paste the same test several times.

## The copy-paste problem

This works:

```python
def test_uppercase_hello() -> None:
    assert "hello".upper() == "HELLO"


def test_uppercase_nimbus() -> None:
    assert "nimbus".upper() == "NIMBUS"


def test_uppercase_storage() -> None:
    assert "storage".upper() == "STORAGE"
```

Output:

```text
...                                                                      [100%]
3 passed in 0.01s
```

But the tests are basically the same. Only the input and expected output
change.

## The parametrized version

```python
import pytest


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hello", "HELLO"),
        ("nimbus", "NIMBUS"),
        ("storage", "STORAGE"),
    ],
)
def test_uppercase_returns_uppercase_text(raw: str, expected: str) -> None:
    assert raw.upper() == expected
```

Pytest runs the test three times:

```text
...                                                                      [100%]
3 passed in 0.01s
```

If one case fails, pytest tells you which one:

```text
FAILED test_text.py::test_uppercase_returns_uppercase_text[nimbus-NIMBUS]
```

That bracketed part is the generated case name.

## Reading the decorator

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hello", "HELLO"),
        ("nimbus", "NIMBUS"),
    ],
)
```

This means:

- create test argument `raw`,
- create test argument `expected`,
- first run: `raw="hello"`, `expected="HELLO"`,
- second run: `raw="nimbus"`, `expected="NIMBUS"`.

```text
parametrize case list
       |
       +--> Run 1: raw="hello",  expected="HELLO"
       |          |
       |          v
       |      test_uppercase_returns_uppercase_text
       |
       +--> Run 2: raw="nimbus", expected="NIMBUS"
                  |
                  v
              test_uppercase_returns_uppercase_text
```

## Example: validating object names

Suppose this repository rejects empty keys and keys with a leading slash:

```python
def validate_object_name(object_name: str) -> None:
    if not object_name:
        raise InvalidObjectNameError("Key cannot be empty")
    if object_name.startswith("/"):
        raise InvalidObjectNameError("S3 object key cannot start with a leading slash")
```

Parametrized test:

```python
@pytest.mark.parametrize(
    ("object_name", "message"),
    [
        ("", "Key cannot be empty"),
        ("/docs/a.txt", "leading slash"),
    ],
)
def test_validate_object_name_rejects_invalid_keys(
    object_name: str,
    message: str,
) -> None:
    with pytest.raises(InvalidObjectNameError, match=message):
        validate_object_name(object_name)
```

Output:

```text
..                                                                       [100%]
2 passed in 0.01s
```

## Example: response codes map to exceptions

This is a realistic adapter-style test:

```python
@pytest.mark.parametrize(
    ("status_code", "content", "expected_error"),
    [
        (401, b'{"detail":"bad API key"}', AuthenticationError),
        (404, b'{"detail":"Object was missing"}', ObjectNotFoundError),
        (502, b"upstream unavailable", StorageBackendError),
    ],
)
def test_response_errors_are_translated_to_domain_exceptions(
    status_code: int,
    content: bytes,
    expected_error: type[Exception],
) -> None:
    adapter = _make_adapter()
    response = _response(status_code, content=content)

    with pytest.raises(expected_error):
        adapter._raise_for_response(response)
```

This test documents a contract:

| Service response | Caller sees |
| --- | --- |
| `401` | `AuthenticationError` |
| `404` object missing | `ObjectNotFoundError` |
| `502` | `StorageBackendError` |

That mapping is exactly the kind of behavior adapters exist to protect.

## Use ids when cases need names

Sometimes values do not produce readable case names.

```python
@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        pytest.param({}, 422, id="empty-body"),
        pytest.param({"text": ""}, 422, id="empty-text"),
        pytest.param({"text": "hello"}, 200, id="valid-text"),
    ],
)
def test_chat_request_validation(payload: dict[str, object], expected_status: int) -> None:
    response = client.post("/ai/chat/turn", json=payload)

    assert response.status_code == expected_status
```

Failure output becomes easier to read:

```text
FAILED test_router.py::test_chat_request_validation[empty-text]
```

## When not to parametrize

Do not parametrize when each case needs a long, different story.

This is too compressed:

```python
@pytest.mark.parametrize("scenario", ["login", "callback", "logout"])
def test_auth_everything(scenario: str) -> None:
    ...
```

Those are probably three different behaviors. Give them three test names.

Parametrize when:

- the test body is the same,
- the inputs vary,
- the expected result varies in a simple way,
- and failure output will still be understandable.

## Parametrization versus Hypothesis

Parametrization is for examples you choose.

Hypothesis is for examples generated by a tool.

Use parametrization when you know the important cases:

```python
@pytest.mark.parametrize("status_code", [400, 401, 404, 500])
def test_known_status_codes(status_code: int) -> None:
    ...
```

Use Hypothesis when the input space is too large to list:

```python
@given(st.text(min_size=1, max_size=300))
def test_session_ids_never_escape_session_directory(session_id: str) -> None:
    ...
```

Read {doc}`testing-hypothesis` when you are ready for that jump.
