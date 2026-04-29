# Testing

Testing is how we teach the codebase to explain itself.

If you are new to Python testing, start here. You do not need to know pytest,
fixtures, monkeypatching, mocks, property-based testing, or this repository's
architecture yet. This section starts from the first idea:

> A test is a small program that runs another piece of code and checks whether
> the result matches the behavior we promised.

Good tests are not just for professors, CI, or coverage numbers. Good tests help
you answer practical engineering questions:

- Did I break behavior that used to work?
- Does this function reject bad input?
- Does this API return the shape callers expect?
- Does this adapter hide transport errors behind the right domain exception?
- Can a future maintainer understand what the code is supposed to do?

## Start here

Read these pages in order if you are learning tests from scratch:

```{toctree}
:maxdepth: 1

testing-pytest-basics
testing-running-debugging
testing-fixtures
testing-parametrize
testing-monkeypatch-mocking
testing-hypothesis
testing-http-integration-e2e
testing-bdd
testing-playbook
```

## The shortest possible test

Imagine we have a tiny function:

```python
def add(left: int, right: int) -> int:
    return left + right
```

A pytest test for it looks like this:

```python
def test_add_returns_sum() -> None:
    result = add(2, 3)

    assert result == 5
```

Run it:

```shell
uv run pytest tests/test_math.py -q
```

Output when it passes:

```text
.                                                                        [100%]
1 passed in 0.01s
```

That dot means one test passed.

Output when it fails:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
____________________________ test_add_returns_sum _____________________________

    def test_add_returns_sum() -> None:
        result = add(2, 3)

>       assert result == 5
E       assert 6 == 5

tests/test_math.py:6: AssertionError
=========================== short test summary info ============================
FAILED tests/test_math.py::test_add_returns_sum - assert 6 == 5
1 failed in 0.02s
```

Pytest shows:

- the test that failed,
- the exact assertion,
- the actual value,
- the expected value,
- and the file and line number.

That is the whole foundation. Everything else in these docs is a more powerful
version of this same idea.

## How to think about a test

A useful test has three parts:

```text
Arrange                         Act                         Assert
Create inputs and dependencies -> call the code under test -> check the result
```

In code:

```python
def test_clear_drops_history_but_keeps_system_prompt() -> None:
    # Arrange
    conversation = Conversation(system="You are Nimbus.")
    conversation.add_user("hello")
    conversation.add_assistant("hi")

    # Act
    conversation.clear()

    # Assert
    assert len(conversation) == 1
    assert conversation.messages()[0].role is Role.SYSTEM
```

The comments are useful while learning. In real tests, you often remove them
when the test name and code are clear enough.

## What this repository tests

This repository has several kinds of code, so it needs several kinds of tests:

| Layer | What it proves | Example |
| --- | --- | --- |
| Unit | One function, class, or module keeps its local promise. | A bad object name raises `InvalidObjectNameError`. |
| Integration | Multiple internal components agree at a real boundary. | The FastAPI service, generated client, and adapter agree on upload/list/download/delete. |
| Property | An invariant holds for many generated inputs. | Any valid session ID can be saved and loaded safely. |
| BDD | A product-level acceptance flow stays true in executable Gherkin. | A signed Slack wrapper turn returns `reply`, `confirmation_required`, `partial_success`, or `error` as promised. |
| E2E | A caller can use the public workflow as a black box. | `main.py` runs in a subprocess with a deterministic fake backend. |
| Fuzz | Parser-like code does not crash on malformed bytes. | Conversation JSON deserialization handles arbitrary byte input. |

The most important habit is not memorizing test categories. The habit is asking:

> What promise does this code make to the next layer up?

Then write the cheapest test that proves that promise.

## How pytest fits into the project

Pytest is the test runner. It discovers files named `test_*.py`, runs functions
named `test_*`, and reports which assertions passed or failed.

This repo uses markers to group tests:

| Marker | Meaning |
| --- | --- |
| `unit` | Fast, isolated tests with mocked external I/O. |
| `integration` | Real wiring tests with deterministic substitutes. |
| `property` | Hypothesis-generated invariant tests. |
| `regression` | Tests that lock in a previously fixed bug or important risk. |
| `e2e` | Black-box workflow tests. |
| `bdd` | Behavior-driven acceptance scenarios written in Gherkin and run by `pytest-bdd`. |
| `circleci` | Safe to run in CI without local credentials. |
| `local_credentials` | Requires local secrets or token files. |

Every new test file should set a marker at module scope:

```python
import pytest

pytestmark = pytest.mark.unit
```

That means every test in the file is a unit test unless a specific test adds
another marker.

## Commands you will use most

```shell
# Run the full suite with coverage.
uv run pytest

# Run fast local tests.
uv run pytest -m "unit or regression" --no-cov

# Run one file.
uv run pytest src/aws_client_adapter/tests/test_service_adapter.py -q --no-cov

# Run one test by name.
uv run pytest src/aws_client_adapter/tests/test_service_adapter.py \
  -k "test_delete_file_maps_not_found_to_domain_exception" \
  -q --no-cov

# Re-run only tests that failed last time.
uv run pytest --lf

# Stop at the first failure.
uv run pytest -x

# Run property tests.
uv run pytest -m property --no-cov

# Run behavior-driven acceptance tests.
uv run pytest tests/bdd -q --no-cov

# Build these docs.
uv run sphinx-build docs/source docs/build/html
```

## What to read next

If you are brand new, read {doc}`testing-pytest-basics`.

If you can write a tiny test but do not know how to run the right slice, read
{doc}`testing-running-debugging`.

If you already know basic pytest but fixtures still feel mysterious, read
{doc}`testing-fixtures`.

If you keep copy-pasting the same test with different inputs, read
{doc}`testing-parametrize`.

If a test needs to replace an environment variable, network call, clock, or
dependency, read {doc}`testing-monkeypatch-mocking`.

If you want to test an invariant across many inputs, read
{doc}`testing-hypothesis`.

If you need to test FastAPI routes, generated clients, subprocesses, or fake
storage workflows, read {doc}`testing-http-integration-e2e`.

If you need executable product acceptance scenarios in Given/When/Then form,
read {doc}`testing-bdd`.

If you want repository-specific examples, read {doc}`testing-playbook`.
