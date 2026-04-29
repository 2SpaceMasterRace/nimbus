# Running And Debugging Tests

This page teaches the mechanics of running tests in this repository.

The official pytest documentation calls this “invoking pytest.” Pytest can run
the whole suite, one directory, one file, one class, one test function, one
parametrized case, or tests selected by keyword or marker. This repo wraps that
through `uv run` so the command uses the workspace virtual environment.

Useful official pytest references:

- [How to invoke pytest](https://docs.pytest.org/en/stable/how-to/usage.html)
- [How to mark test functions](https://docs.pytest.org/en/stable/how-to/mark.html)
- [How to use temporary directories and files](https://docs.pytest.org/en/stable/how-to/tmp_path.html)
- [How to handle skips and expected failures](https://docs.pytest.org/en/stable/how-to/skipping.html)

## The mental model

When you run:

```shell
uv run pytest
```

three things happen:

```text
1. uv run
   Use the repo's managed Python environment.

2. pytest
   Discover test files and test functions.

3. configured addopts
   Apply this repo's defaults from pyproject.toml:
   --cov --cov-report=term-missing --import-mode=importlib
```

Pytest discovers tests from:

```toml
[tool.pytest.ini_options]
testpaths = ["tests", "src/*/tests"]
```

That means it looks in the repository-level `tests/` directory and every
package's `src/<package>/tests/` directory.

## Run the full suite

```shell
uv run pytest
```

You normally use this before pushing or when a change touches many packages.

Typical successful output:

```text
........................................................................ [ 20%]
........................................................................ [ 40%]
........................................................................ [ 60%]
........................................................................ [ 80%]
........................................                                 [100%]
================================ tests coverage ================================
Name                                      Stmts   Miss Branch BrPart  Cover
-----------------------------------------------------------------------------
src/...                                    ...     ...    ...    ...    86%
-----------------------------------------------------------------------------
TOTAL                                     2859    345    516     82    86%
Required test coverage of 80.0% reached. Total coverage: 85.69%
424 passed, 16 skipped in 8.89s
```

What the symbols mean:

| Symbol | Meaning |
| --- | --- |
| `.` | One passing test. |
| `F` | One failing test. |
| `s` | One skipped test. |
| `E` | An error happened during setup or execution. |
| `x` | Expected failure. |
| `X` | Unexpected pass. |

## Run without coverage when iterating

Coverage is useful, but it slows down the edit-run loop.

Use `--no-cov` while developing:

```shell
uv run pytest src/aws_client_adapter/tests/test_service_adapter.py -q --no-cov
```

Output:

```text
...................................                                      [100%]
35 passed in 0.09s
```

The `-q` flag means “quiet.” It makes pytest print less.

## Run one file

```shell
uv run pytest src/aws_client_adapter/tests/test_service_adapter.py -q --no-cov
```

Use this immediately after changing one test file.

## Run one test

Use a node id:

```shell
uv run pytest \
  src/aws_client_adapter/tests/test_service_adapter.py::test_delete_file_maps_not_found_to_domain_exception \
  -q --no-cov
```

Output:

```text
.                                                                        [100%]
1 passed in 0.03s
```

Node ids can include:

```text
path/to/test_file.py::test_function_name
path/to/test_file.py::TestClass::test_method_name
path/to/test_file.py::test_parametrized_case[value-name]
```

## Run tests by keyword

Use `-k` when you remember part of the name:

```shell
uv run pytest -k "adapter and not integration" --no-cov
```

That runs tests whose file name, class name, or function name matches the
expression.

Example:

```shell
uv run pytest -k "not_found" -q --no-cov
```

Possible output:

```text
.......                                                                  [100%]
7 passed, 417 deselected in 1.21s
```

“Deselected” means pytest found those tests but did not run them because the
keyword expression filtered them out.

## Run tests by marker

This repo uses markers to group tests by purpose.

Run fast local tests:

```shell
uv run pytest -m "unit or regression" --no-cov
```

Run property tests:

```shell
uv run pytest -m property --no-cov
```

Run integration tests:

```shell
uv run pytest -m integration --no-cov
```

Run e2e tests:

```shell
uv run pytest -m e2e --no-cov
```

Skip live credential tests:

```shell
uv run pytest -m "not local_credentials" --no-cov
```

Markers are registered in `pyproject.toml`. If you add a new marker, register it
there so pytest does not warn about a typo.

## Module-level pytestmark

Every test file in this repo should declare its default marker:

```python
import pytest

pytestmark = pytest.mark.unit
```

That applies `unit` to every test in the file.

If a specific test also needs a marker:

```python
@pytest.mark.regression
def test_attachment_upload_does_not_leak_temp_file_on_write_failure() -> None:
    ...
```

That test has both the module marker and the function marker.

## See what pytest collected

When you are confused about what will run:

```shell
uv run pytest --collect-only -q
```

Example output:

```text
src/aws_client_adapter/tests/test_service_adapter.py::test_upload_file_posts_container_and_multipart_body
src/aws_client_adapter/tests/test_service_adapter.py::test_list_files_returns_parsed_object_infos
src/aws_client_adapter/tests/test_service_adapter.py::test_delete_file_returns_delete_result_on_success
```

This runs no tests. It only prints what pytest discovered.

## Re-run last failures

```shell
uv run pytest --lf --no-cov
```

`--lf` means “last failed.” It is useful after fixing a failure.

Typical output:

```text
run-last-failure: rerun previous 2 failures
..                                                                       [100%]
2 passed in 0.42s
```

## Stop after the first failure

```shell
uv run pytest -x --no-cov
```

Use this when the first failure is probably the root cause and later failures
would only add noise.

## Show print output

Pytest captures `stdout` and `stderr` by default. That means `print()` output is
usually hidden unless a test fails.

Use `-s` to show it:

```shell
uv run pytest path/to/test_file.py -s --no-cov
```

Example test:

```python
def test_prints_debug_line() -> None:
    print("debug: about to assert")

    assert 1 + 1 == 2
```

Without `-s`:

```text
.                                                                        [100%]
1 passed in 0.01s
```

With `-s`:

```text
debug: about to assert
.                                                                        [100%]
1 passed in 0.01s
```

Do not leave random debug prints in tests. Use `-s` while debugging, then remove
the prints.

## Skip and fail deliberately

This repo uses `pytest.skip()` when a test cannot run in the current
environment:

```python
def test_main_script_exists() -> None:
    main_script = Path("main.py")
    if not main_script.exists():
        pytest.skip(f"main.py not found at {main_script}")
```

Output:

```text
s                                                                        [100%]
1 skipped in 0.01s
```

Use `pytest.fail()` when you catch an unexpected condition and want a readable
message:

```python
try:
    subprocess.run(command, check=True, timeout=10)
except subprocess.TimeoutExpired:
    pytest.fail("E2E test timed out - main.py took too long to execute")
```

Failure output:

```text
FAILED tests/e2e/test_main_application.py::test_main_runs_successfully
E   Failed: E2E test timed out - main.py took too long to execute
```

## Inspect available fixtures and markers

List fixtures:

```shell
uv run pytest --fixtures
```

List markers:

```shell
uv run pytest --markers
```

These commands are noisy, but they are useful when you see a test argument like
`tmp_path`, `monkeypatch`, `mocker`, or `client` and do not know where it comes
from.

## Coverage commands

Full coverage gate:

```shell
uv run pytest
```

Targeted coverage report for one file's test run:

```shell
uv run coverage erase
uv run coverage run -m pytest src/aws_client_adapter/tests/test_service_adapter.py -q
uv run coverage report src/aws_client_adapter/aws_client_adapter/service_adapter.py
```

Coverage output:

```text
Name                                                  Stmts   Miss Branch BrPart  Cover
---------------------------------------------------------------------------------------
src/aws_client_adapter/aws_client_adapter/service_adapter.py
                                                        188     12     52      8    92%
```

Coverage answers “which lines and branches ran?” It does not answer “are the
assertions meaningful?” Use coverage as a map, not as the definition of quality.

## Docs and fuzz commands

Build docs:

```shell
uv run sphinx-build docs/source docs/build/html
```

Serve docs with live reload if `just` is installed:

```shell
just docs
```

Run fuzz smoke harnesses:

```shell
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_conversation.py
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_session_id.py
PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_request_state.py
```

Fuzz smoke mode is not a replacement for pytest. It is a separate check that
parser-like code handles malformed byte-level input without crashing.

## A practical beginner workflow

When you edit code:

1. Run the smallest relevant test file with `--no-cov`.
2. If it fails, read the first failure carefully.
3. Re-run the failing test by node id.
4. Run a related slice, such as integration tests for an adapter change.
5. Run ruff on touched Python files.
6. Run the full suite or full `src/` suite when the change crosses boundaries.

Example:

```shell
uv run pytest src/aws_client_adapter/tests/test_service_adapter.py -q --no-cov
uv run pytest tests/integration/test_adapter_integration.py -q --no-cov
uv run ruff check src/aws_client_adapter/tests/test_service_adapter.py
uv run pytest src/ -q --cov --cov-report=term-missing
```
