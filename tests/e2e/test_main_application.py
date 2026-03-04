"""End-to-End tests for the main application.

This module tests the application's main entry point (main.py) as a black box,
simulating real user interactions and verifying the complete workflow.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_WORKSPACE_ROOT = Path(__file__).parent.parent.parent


def _subprocess_env() -> dict[str, str]:
    """Build environment dict with PYTHONPATH matching pytest's pythonpath config."""
    env = os.environ.copy()
    root = str(_WORKSPACE_ROOT)
    src = str(_WORKSPACE_ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join([root, src, env.get("PYTHONPATH", "")])
    return env


@pytest.mark.circleci
def test_main_script_runs_successfully() -> None:
    """Tests that main.py executes the full S3 workflow end-to-end.

    Requires AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and
    AWS_BUCKET_NAME to be set. Verifies the entire flow: client creation
    via dependency injection, S3 API call, and response handling.
    """
    main_script = _WORKSPACE_ROOT / "main.py"

    if not main_script.exists():
        pytest.skip(f"main.py not found at {main_script}")

    required_env_vars = [
        "AWS_REGION",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_BUCKET_NAME",
    ]
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]

    if missing_vars:
        pytest.skip(
            f"Missing required environment variables: {missing_vars}",
        )

    command = [sys.executable, str(main_script)]

    try:
        result = subprocess.run(  # noqa: S603  # command list is constructed from trusted constants (sys.executable + known file path); no shell interpolation or user input
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            cwd=str(main_script.parent),
            env=_subprocess_env(),
        )

        output = result.stdout
        assert "Created cloud storage client" in output
        assert "Listed files in bucket" in output
        assert "Demo complete" in output

    except subprocess.TimeoutExpired:
        pytest.fail("E2E test timed out - main.py took too long to execute")
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"E2E test failed when running main.py.\n"
            f"Exit Code: {e.returncode}\nStdout: {e.stdout}\nStderr: {e.stderr}",
        )


@pytest.mark.circleci
def test_main_script_handles_no_credentials_gracefully() -> None:
    """Ensure main.py fails with a clear error when AWS_BUCKET_NAME is missing.

    Runs main.py as a subprocess with AWS_BUCKET_NAME stripped from the
    environment and verifies a non-zero exit code with a meaningful
    traceback in stderr.
    """
    main_script = _WORKSPACE_ROOT / "main.py"

    if not main_script.exists():
        pytest.skip(f"main.py not found at {main_script}")

    env = _subprocess_env()
    env.pop("AWS_BUCKET_NAME", None)

    command = [sys.executable, str(main_script)]

    result = subprocess.run(  # noqa: S603  # command list is constructed from trusted constants (sys.executable + known file path); no shell interpolation or user input
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=str(main_script.parent),
        env=env,
    )

    assert result.returncode != 0
    assert result.stderr  # some error must appear in stderr


@pytest.mark.circleci
def test_main_script_syntax_is_valid() -> None:
    """Tests that main.py has valid Python syntax.

    This can run in any environment.
    """
    main_script = _WORKSPACE_ROOT / "main.py"

    if not main_script.exists():
        pytest.skip(f"main.py not found at {main_script}")

    command = [sys.executable, "-m", "py_compile", str(main_script)]

    try:
        subprocess.run(  # noqa: S603  # command list is constructed from trusted constants (sys.executable + known file path); no shell interpolation or user input
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as e:
        pytest.fail(f"main.py has syntax errors:\n{e.stderr}")


@pytest.mark.circleci
def test_main_script_imports_work() -> None:
    """Tests that main.py can import all required modules.

    This can run in any environment.
    """
    main_script = _WORKSPACE_ROOT / "main.py"

    if not main_script.exists():
        pytest.skip(f"main.py not found at {main_script}")

    import_test_code = """
try:
    import cloud_storage_client_api
    import aws_client_impl
    print("All imports successful")
except ImportError as e:
    print(f"Import error: {e}")
    raise
"""

    command = [sys.executable, "-c", import_test_code]

    try:
        result = subprocess.run(  # noqa: S603  # command list is constructed from trusted constants (sys.executable + known file path); no shell interpolation or user input
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            cwd=str(main_script.parent),
            env=_subprocess_env(),
        )

        assert "All imports successful" in result.stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(f"main.py imports failed:\n{e.stderr}")


@pytest.mark.circleci
def test_application_structure_integrity() -> None:
    """Tests that the application has the expected file structure.

    This can run in any environment.
    """
    expected_files = [
        "main.py",
        "pyproject.toml",
        "src/cloud_storage_client_api/cloud_storage_client_api/__init__.py",
        "src/cloud_storage_client_api/cloud_storage_client_api/client.py",
        "src/aws_client_impl/aws_client_impl/__init__.py",
        "src/aws_client_impl/aws_client_impl/s3_client.py",
    ]

    missing_files = [
        file_path
        for file_path in expected_files
        if not (_WORKSPACE_ROOT / file_path).exists()
    ]

    if missing_files:
        pytest.fail(f"Missing required files: {missing_files}")
