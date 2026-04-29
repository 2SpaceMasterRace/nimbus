"""End-to-end tests for the main application entry point."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

_WORKSPACE_ROOT = Path(__file__).parent.parent.parent
FAKE_BUCKET = "demo-bucket"


def _subprocess_env(*, extra_pythonpath: list[str] | None = None) -> dict[str, str]:
    """Build environment dict with PYTHONPATH matching pytest's pythonpath config."""
    env = os.environ.copy()
    root = str(_WORKSPACE_ROOT)
    src = str(_WORKSPACE_ROOT / "src")
    pythonpath_entries = [root, src]
    if extra_pythonpath:
        pythonpath_entries = [*extra_pythonpath, *pythonpath_entries]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    return env


def _main_script() -> Path:
    """Return the path to the main entry-point script."""
    return _WORKSPACE_ROOT / "main.py"


def _write_fake_aws_client_impl(root: Path) -> None:
    """Write a deterministic fake ``aws_client_impl`` package for subprocess tests."""
    package_dir = root / "aws_client_impl"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(
        "from .s3_client import get_client_impl\n",
        encoding="utf-8",
    )
    (package_dir / "s3_client.py").write_text(
        """
from __future__ import annotations

from cloud_storage_api import ObjectInfo, StorageBackendError


class _FakeClient:
    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        if container == "explode-bucket":
            raise StorageBackendError("simulated storage failure")
        return [
            ObjectInfo(object_name="reports/alpha.txt", size_bytes=13),
            ObjectInfo(object_name="reports/beta.txt", size_bytes=12),
        ]


def get_client_impl():
    return _FakeClient()
""".strip()
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.circleci
def test_main_script_runs_successfully_with_deterministic_fake_backend(
    tmp_path: Path,
) -> None:
    """main.py completes its workflow against a reproducible fake backend."""
    main_script = _main_script()

    if not main_script.exists():
        pytest.skip(f"main.py not found at {main_script}")

    fake_pkg_root = tmp_path / "fake_packages"
    _write_fake_aws_client_impl(fake_pkg_root)

    env = _subprocess_env(extra_pythonpath=[str(fake_pkg_root)])
    env["AWS_BUCKET_NAME"] = FAKE_BUCKET
    command = [sys.executable, str(main_script)]

    try:
        result = subprocess.run(  # noqa: S603  # command list is constructed from trusted constants (sys.executable + known file path); no shell interpolation or user input
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
            cwd=str(main_script.parent),
            env=env,
        )

        combined_output = f"{result.stdout}\n{result.stderr}"
        assert "Created cloud storage client" in combined_output
        assert "Listed files in bucket" in combined_output
        assert "reports/alpha.txt" in combined_output
        assert "reports/beta.txt" in combined_output
        assert "Demo complete" in combined_output

    except subprocess.TimeoutExpired:
        pytest.fail("E2E test timed out - main.py took too long to execute")
    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"E2E test failed when running main.py.\n"
            f"Exit Code: {e.returncode}\nStdout: {e.stdout}\nStderr: {e.stderr}",
        )


@pytest.mark.circleci
def test_main_script_handles_no_credentials_gracefully() -> None:
    """main.py fails deterministically when ``AWS_BUCKET_NAME`` is missing."""
    main_script = _main_script()

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
    assert "AWS_BUCKET_NAME" in result.stderr


@pytest.mark.circleci
def test_main_script_propagates_backend_failure(
    tmp_path: Path,
) -> None:
    """main.py surfaces deterministic storage failures from the backend."""
    main_script = _main_script()

    if not main_script.exists():
        pytest.skip(f"main.py not found at {main_script}")

    fake_pkg_root = tmp_path / "fake_packages"
    _write_fake_aws_client_impl(fake_pkg_root)

    env = _subprocess_env(extra_pythonpath=[str(fake_pkg_root)])
    env["AWS_BUCKET_NAME"] = "explode-bucket"
    command = [sys.executable, str(main_script)]

    result = subprocess.run(  # noqa: S603  # trusted command list
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=str(main_script.parent),
        env=env,
    )

    assert result.returncode != 0
    assert "simulated storage failure" in result.stderr


@pytest.mark.circleci
def test_main_script_syntax_is_valid() -> None:
    """Tests that main.py has valid Python syntax.

    This can run in any environment.
    """
    main_script = _main_script()

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
    main_script = _main_script()

    if not main_script.exists():
        pytest.skip(f"main.py not found at {main_script}")

    import_test_code = """
try:
    import cloud_storage_api
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
