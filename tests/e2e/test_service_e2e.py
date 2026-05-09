"""End-to-end tests for the FastAPI storage service."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from aws_client_service.deps import require_oauth_session
from aws_client_service.main import app, get_storage_client
from fastapi.testclient import TestClient
from test_support.storage_fakes import FileBackedStorageClient

pytestmark = pytest.mark.e2e

_WORKSPACE_ROOT = Path(__file__).parent.parent.parent

HTTP_OK = 200
API_KEY = "service-e2e-api-key"


def _subprocess_env() -> dict[str, str]:
    """Build environment dict with PYTHONPATH matching pytest's pythonpath config."""
    env = os.environ.copy()
    root = str(_WORKSPACE_ROOT)
    src = str(_WORKSPACE_ROOT / "src")
    env["PYTHONPATH"] = os.pathsep.join(
        [root, src, env.get("PYTHONPATH", "")],
    )
    return env


@pytest.mark.circleci
def test_service_structure_integrity() -> None:
    """Tests that the service has the expected file structure."""
    expected_files = [
        "src/aws_client_service/aws_client_service/__init__.py",
        "src/aws_client_service/aws_client_service/main.py",
        "src/aws_client_service/pyproject.toml",
    ]

    missing_files = [
        file_path
        for file_path in expected_files
        if not (_WORKSPACE_ROOT / file_path).exists()
    ]

    if missing_files:
        pytest.fail(f"Missing required files: {missing_files}")


@pytest.mark.circleci
def test_service_module_imports() -> None:
    """Tests that aws_client_service.main can be imported without errors."""
    import_test_code = 'import aws_client_service.main; print("All imports successful")'

    command = [sys.executable, "-c", import_test_code]

    try:
        result = subprocess.run(  # noqa: S603  # command list is constructed from trusted constants
            command,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            env=_subprocess_env(),
        )

        assert "All imports successful" in result.stdout

    except subprocess.CalledProcessError as e:
        pytest.fail(
            f"aws_client_service.main import failed:\n{e.stderr}",
        )


@pytest.mark.circleci
def test_service_module_imports_without_session_secret() -> None:
    """Missing SESSION_SECRET_KEY is reported by readiness, not import startup."""
    import_test_code = 'import aws_client_service.main; print("All imports successful")'
    env = _subprocess_env()
    env.pop("SESSION_SECRET_KEY", None)

    result = subprocess.run(  # noqa: S603  # command list is constructed from trusted constants
        [sys.executable, "-c", import_test_code],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env=env,
    )

    assert "All imports successful" in result.stdout


@pytest.mark.circleci
def test_service_openapi_schema_includes_upload() -> None:
    """Tests that the OpenAPI schema exposes the upload endpoint."""
    client = TestClient(app)

    response = client.get("/openapi.json")
    assert response.status_code == HTTP_OK

    schema = response.json()
    upload_path = "/files/{container}/{object_name}"
    assert upload_path in schema["paths"]

    upload_op = schema["paths"][upload_path]["post"]
    param_names = [p["name"] for p in upload_op["parameters"]]
    assert "container" in param_names
    assert "object_name" in param_names

    response_schema = upload_op["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema["$ref"].endswith("/ObjectInfoResponse")


@pytest.mark.circleci
def test_service_openapi_schema_includes_download() -> None:
    """Tests that the OpenAPI schema exposes the /download endpoint."""
    client = TestClient(app)

    response = client.get("/openapi.json")
    assert response.status_code == HTTP_OK

    schema = response.json()
    assert "/download" in schema["paths"]

    download_op = schema["paths"]["/download"]["get"]
    param_names = [p["name"] for p in download_op["parameters"]]
    assert "container" in param_names
    assert "object_name" in param_names


@pytest.mark.circleci
def test_service_openapi_schema_includes_list_container() -> None:
    """Tests that the list endpoint requires a container query parameter."""
    client = TestClient(app)

    response = client.get("/openapi.json")
    assert response.status_code == HTTP_OK

    schema = response.json()
    assert "/files" in schema["paths"]

    list_op = schema["paths"]["/files"]["get"]
    param_names = [p["name"] for p in list_op["parameters"]]
    assert "container" in param_names
    assert "prefix" in param_names


@pytest.mark.circleci
def test_service_openapi_schema_includes_delete() -> None:
    """Tests that the OpenAPI schema exposes the delete endpoint."""
    client = TestClient(app)

    response = client.get("/openapi.json")
    assert response.status_code == HTTP_OK

    schema = response.json()
    delete_path = "/files/{container}/{object_name}"
    assert delete_path in schema["paths"]

    delete_op = schema["paths"][delete_path]["delete"]
    param_names = [p["name"] for p in delete_op["parameters"]]
    assert "container" in param_names
    assert "object_name" in param_names

    response_schema = delete_op["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema["$ref"].endswith("/DeleteResultResponse")


@pytest.mark.circleci
def test_service_health_endpoint_e2e() -> None:
    """Tests that the /health endpoint returns 200 with expected payload."""
    client = TestClient(app)

    response = client.get("/health")
    assert response.status_code == HTTP_OK
    assert response.json() == {"status": "ok"}


@pytest.mark.circleci
def test_service_requires_auth_when_override_is_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Protected endpoints reject unauthenticated requests."""
    monkeypatch.setenv("API_KEY", API_KEY)
    app.dependency_overrides.pop(require_oauth_session, None)

    try:
        client = TestClient(app)
        response = client.get("/files", params={"container": "demo-bucket"})
        assert response.status_code == 401
        assert response.json() == {"detail": "Authentication required"}
    finally:
        app.dependency_overrides.pop(require_oauth_session, None)


@pytest.mark.circleci
def test_service_file_workflow_over_http_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real FastAPI app preserves storage workflow invariants end to end."""
    monkeypatch.setenv("API_KEY", API_KEY)
    app.dependency_overrides.pop(require_oauth_session, None)
    app.dependency_overrides[get_storage_client] = lambda: FileBackedStorageClient(
        tmp_path / "storage"
    )

    headers = {"X-API-Key": API_KEY}

    try:
        client = TestClient(app)

        upload = client.post(
            "/files/demo-bucket/reports/q1.txt",
            files={"file": ("q1.txt", b"quarterly results", "text/plain")},
            headers=headers,
        )
        assert upload.status_code == HTTP_OK
        assert upload.json()["object_name"] == "reports/q1.txt"

        listed = client.get(
            "/files",
            params={"container": "demo-bucket", "prefix": "reports/"},
            headers=headers,
        )
        assert listed.status_code == HTTP_OK
        assert listed.json() == [
            {
                "object_name": "reports/q1.txt",
                "version_id": None,
                "data_type": None,
                "integrity": None,
                "encryption": None,
                "storage_tier": None,
                "size_bytes": len(b"quarterly results"),
                "updated_at": None,
                "metadata": None,
            }
        ]

        info = client.get(
            "/files/demo-bucket/reports/q1.txt/info",
            headers=headers,
        )
        assert info.status_code == HTTP_OK
        assert info.json()["size_bytes"] == len(b"quarterly results")

        download = client.get(
            "/download",
            params={"container": "demo-bucket", "object_name": "reports/q1.txt"},
            headers=headers,
        )
        assert download.status_code == HTTP_OK
        assert download.content == b"quarterly results"
        assert download.headers["content-disposition"].endswith('filename="q1.txt"')

        deleted = client.delete(
            "/files/demo-bucket/reports/q1.txt",
            headers=headers,
        )
        assert deleted.status_code == HTTP_OK
        assert deleted.json() == {
            "deleted": True,
            "version_id": None,
            "request_charged": None,
        }

        deleted_again = client.delete(
            "/files/demo-bucket/reports/q1.txt",
            headers=headers,
        )
        assert deleted_again.status_code == HTTP_OK
        assert deleted_again.json() == {
            "deleted": False,
            "version_id": None,
            "request_charged": None,
        }

        listed_after_delete = client.get(
            "/files",
            params={"container": "demo-bucket", "prefix": "reports/"},
            headers=headers,
        )
        assert listed_after_delete.status_code == HTTP_OK
        assert listed_after_delete.json() == []
    finally:
        app.dependency_overrides.clear()
