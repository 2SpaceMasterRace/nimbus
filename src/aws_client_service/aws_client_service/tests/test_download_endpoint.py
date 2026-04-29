"""Unit tests for the download endpoint in the AWS S3 FastAPI service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from cloud_storage_api import ObjectInfo, ObjectNotFoundError, StorageBackendError
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
HTTP_BAD_GATEWAY = 502


def _stub_object_info(name: str = "report.csv") -> ObjectInfo:
    """Return a minimal ObjectInfo for use in test stubs."""
    return ObjectInfo(object_name=name)


@pytest.mark.circleci
def test_health_returns_ok(client: TestClient) -> None:
    """GET /health returns 200 with status ok."""
    response = client.get("/health")

    assert response.status_code == HTTP_OK
    assert response.json() == {"status": "ok"}


@pytest.mark.circleci
def test_root_returns_hello(client: TestClient) -> None:
    """GET / returns 200 with a hello world message."""
    response = client.get("/")

    assert response.status_code == HTTP_OK
    assert response.json() == {"message": "Hello World"}


@pytest.mark.circleci
def test_download_returns_file_on_success(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /download returns the file content when download_file succeeds."""

    def fake_download(_bucket: str, _key: str, dest: str) -> ObjectInfo:
        Path(dest).write_bytes(b"file content")
        return _stub_object_info(_key)

    mock_storage_client.download_file.side_effect = fake_download

    response = client.get(
        "/download",
        params={"container": "my-bucket", "object_name": "report.csv"},
    )

    assert response.status_code == HTTP_OK
    assert response.content == b"file content"


@pytest.mark.circleci
def test_download_returns_404_on_failure(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /download returns 404 when download_file raises ObjectNotFoundError."""
    mock_storage_client.download_file.side_effect = ObjectNotFoundError(
        "Object 'missing.txt' was not found in container 'my-bucket'"
    )

    response = client.get(
        "/download",
        params={"container": "my-bucket", "object_name": "missing.txt"},
    )

    assert response.status_code == HTTP_NOT_FOUND
    assert (
        response.json()["detail"]
        == "Object 'missing.txt' was not found in container 'my-bucket'"
    )


@pytest.mark.circleci
def test_download_returns_502_on_exception(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /download returns 502 when download_file raises an exception."""
    mock_storage_client.download_file.side_effect = StorageBackendError(
        "connection lost"
    )

    response = client.get(
        "/download",
        params={"container": "my-bucket", "object_name": "data.bin"},
    )

    assert response.status_code == HTTP_BAD_GATEWAY
    assert response.json()["detail"] == "Download failed due to a storage error"


@pytest.mark.circleci
@pytest.mark.usefixtures("mock_storage_client")
def test_download_missing_bucket_name(client: TestClient) -> None:
    """GET /download without container returns 422 validation error."""
    response = client.get("/download", params={"object_name": "file.txt"})

    assert response.status_code == HTTP_UNPROCESSABLE


@pytest.mark.circleci
@pytest.mark.usefixtures("mock_storage_client")
def test_download_missing_object_name(client: TestClient) -> None:
    """GET /download without object_name returns 422 validation error."""
    response = client.get("/download", params={"container": "my-bucket"})

    assert response.status_code == HTTP_UNPROCESSABLE


@pytest.mark.circleci
def test_download_sets_filename_header(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /download sets Content-Disposition header with the object filename."""

    def fake_download(_bucket: str, _key: str, dest: str) -> ObjectInfo:
        Path(dest).write_bytes(b"data")
        return _stub_object_info(_key)

    mock_storage_client.download_file.side_effect = fake_download

    response = client.get(
        "/download",
        params={"container": "my-bucket", "object_name": "reports/archive.zip"},
    )

    assert response.status_code == HTTP_OK
    content_disposition = response.headers["content-disposition"]
    assert "archive.zip" in content_disposition
    assert "reports/archive.zip" not in content_disposition


@pytest.mark.circleci
def test_download_removes_temp_file_after_successful_response(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /download removes the temp file after FileResponse finishes sending."""
    captured_dest: dict[str, str] = {}

    def fake_download(_bucket: str, _key: str, dest: str) -> ObjectInfo:
        captured_dest["path"] = dest
        Path(dest).write_bytes(b"cleanup me")
        return _stub_object_info(_key)

    mock_storage_client.download_file.side_effect = fake_download

    response = client.get(
        "/download",
        params={"container": "my-bucket", "object_name": "cleanup.txt"},
    )

    assert response.status_code == HTTP_OK
    assert response.content == b"cleanup me"
    assert "path" in captured_dest
    assert not Path(captured_dest["path"]).exists()
