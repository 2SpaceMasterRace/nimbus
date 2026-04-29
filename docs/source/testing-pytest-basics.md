# Pytest Basics

This page assumes you have never written a test before.

## What is pytest?

`pytest` is a Python program that finds and runs tests.

It looks for:

- files named `test_*.py` or `*_test.py`,
- functions named `test_*`,
- and `assert` statements inside those functions.

Here is a tiny project:

```text
demo/
├── calculator.py
└── test_calculator.py
```

`calculator.py`:

```python
def multiply(left: int, right: int) -> int:
    return left * right
```

`test_calculator.py`:

```python
from calculator import multiply


def test_multiply_returns_product() -> None:
    assert multiply(3, 4) == 12
```

Run:

```shell
uv run pytest test_calculator.py -q
```

Output:

```text
.                                                                        [100%]
1 passed in 0.01s
```

The dot is pytest's compact way of saying “one test passed.”

## The shape of a test

Most tests follow this shape:

```python
def test_name_describes_expected_behavior() -> None:
    # Arrange: create inputs.
    value = "hello"

    # Act: call the thing being tested.
    result = value.upper()

    # Assert: check the result.
    assert result == "HELLO"
```

The test name matters. When CI fails, the name is the first explanation a human
sees.

Prefer:

```python
def test_uppercase_converts_letters_to_uppercase() -> None:
    assert "hello".upper() == "HELLO"
```

Avoid:

```python
def test_1() -> None:
    assert "hello".upper() == "HELLO"
```

The second test may pass, but it explains nothing.

## How assert works

`assert` means “this must be true.”

```python
assert 2 + 2 == 4
```

If it is true, the test continues.

If it is false, pytest stops the test and prints a failure.

```python
def test_bad_math() -> None:
    assert 2 + 2 == 5
```

Output:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
________________________________ test_bad_math _________________________________

    def test_bad_math() -> None:
>       assert 2 + 2 == 5
E       assert (2 + 2) == 5

test_math.py:2: AssertionError
=========================== short test summary info ============================
FAILED test_math.py::test_bad_math - assert (2 + 2) == 5
1 failed in 0.01s
```

Pytest rewrites assertions so it can show useful details. That is why plain
`assert` is usually better than writing custom failure messages everywhere.

## Testing exceptions

Sometimes correct behavior is “this input should be rejected.”

Example function:

```python
def divide(left: int, right: int) -> float:
    if right == 0:
        raise ValueError("right must not be zero")
    return left / right
```

Test:

```python
import pytest

from calculator import divide


def test_divide_rejects_zero_divisor() -> None:
    with pytest.raises(ValueError, match="must not be zero"):
        divide(10, 0)
```

What this means:

- `pytest.raises(ValueError)` says the code inside the block must raise
  `ValueError`.
- `match="must not be zero"` checks the error message.
- If no exception is raised, the test fails.
- If the wrong exception is raised, the test fails.

Passing output:

```text
.                                                                        [100%]
1 passed in 0.01s
```

Failure output if the function does not raise:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
________________________ test_divide_rejects_zero_divisor ________________________

    def test_divide_rejects_zero_divisor() -> None:
>       with pytest.raises(ValueError, match="must not be zero"):
E       Failed: DID NOT RAISE <class 'ValueError'>

test_calculator.py:7: Failed
```

## Testing objects

Most real code is not as small as `add()`. Here is a tiny class:

```python
class ShoppingCart:
    def __init__(self) -> None:
        self._items: list[str] = []

    def add(self, item: str) -> None:
        if not item:
            raise ValueError("item must not be empty")
        self._items.append(item)

    def items(self) -> list[str]:
        return list(self._items)
```

Tests:

```python
import pytest

from shop import ShoppingCart


def test_cart_starts_empty() -> None:
    cart = ShoppingCart()

    assert cart.items() == []


def test_cart_remembers_added_items() -> None:
    cart = ShoppingCart()

    cart.add("notebook")
    cart.add("pencil")

    assert cart.items() == ["notebook", "pencil"]


def test_cart_rejects_empty_item() -> None:
    cart = ShoppingCart()

    with pytest.raises(ValueError, match="must not be empty"):
        cart.add("")
```

Output:

```text
...                                                                      [100%]
3 passed in 0.01s
```

Three dots: three passing tests.

## Why tests should be small

Small tests fail with precise explanations.

This is too much in one test:

```python
def test_cart() -> None:
    cart = ShoppingCart()
    assert cart.items() == []
    cart.add("notebook")
    assert cart.items() == ["notebook"]
    with pytest.raises(ValueError):
        cart.add("")
```

If this fails, you must read the whole test to know what behavior broke.

Prefer separate tests:

```python
def test_cart_starts_empty() -> None:
    ...


def test_cart_remembers_added_items() -> None:
    ...


def test_cart_rejects_empty_item() -> None:
    ...
```

One behavior per test is a good beginner rule.

## What a good failure teaches you

Suppose this test fails:

```python
def test_adapter_maps_unauthorized_response_to_authentication_error() -> None:
    with pytest.raises(AuthenticationError):
        adapter.delete_file("docs-bucket", "docs/a.txt")
```

That name tells you the promise:

> If the service says unauthorized, the adapter should raise
> `AuthenticationError`.

The test is not just checking code. It is documenting the contract between two
layers.

That is the style this repository aims for.
