"""Unit tests for the download endpoint in the AWS S3 FastAPI service."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from aws_client_service.main import app, get_storage_client
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
HTTP_BAD_GATEWAY = 502


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Provide a TestClient and clean up dependency overrides after each test."""
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def mock_storage_client() -> MagicMock:
    """Provide a mock CloudStorageClient wired into the FastAPI dependency system."""
    mock_client = MagicMock()
    app.dependency_overrides[get_storage_client] = lambda: mock_client
    return mock_client


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

    def fake_download(_bucket: str, _key: str, dest: str) -> bool:
        Path(dest).write_bytes(b"file content")
        return True

    mock_storage_client.download_file.side_effect = fake_download

    response = client.get(
        "/download",
        params={"bucket_name": "my-bucket", "object_name": "report.csv"},
    )

    assert response.status_code == HTTP_OK
    assert response.content == b"file content"


@pytest.mark.circleci
def test_download_returns_404_on_failure(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /download returns 404 when download_file returns False."""
    mock_storage_client.download_file.return_value = False

    response = client.get(
        "/download",
        params={"bucket_name": "my-bucket", "object_name": "missing.txt"},
    )

    assert response.status_code == HTTP_NOT_FOUND
    assert response.json()["detail"] == "Object not found or download failed"


@pytest.mark.circleci
def test_download_returns_502_on_exception(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /download returns 502 when download_file raises an exception."""
    mock_storage_client.download_file.side_effect = RuntimeError("connection lost")

    response = client.get(
        "/download",
        params={"bucket_name": "my-bucket", "object_name": "data.bin"},
    )

    assert response.status_code == HTTP_BAD_GATEWAY
    assert response.json()["detail"] == "Download failed due to a storage error"


@pytest.mark.circleci
@pytest.mark.usefixtures("mock_storage_client")
def test_download_missing_bucket_name(client: TestClient) -> None:
    """GET /download without bucket_name returns 422 validation error."""
    response = client.get("/download", params={"object_name": "file.txt"})

    assert response.status_code == HTTP_UNPROCESSABLE


@pytest.mark.circleci
@pytest.mark.usefixtures("mock_storage_client")
def test_download_missing_object_name(client: TestClient) -> None:
    """GET /download without object_name returns 422 validation error."""
    response = client.get("/download", params={"bucket_name": "my-bucket"})

    assert response.status_code == HTTP_UNPROCESSABLE


@pytest.mark.circleci
def test_download_sets_filename_header(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /download sets Content-Disposition header with the object filename."""

    def fake_download(_bucket: str, _key: str, dest: str) -> bool:
        Path(dest).write_bytes(b"data")
        return True

    mock_storage_client.download_file.side_effect = fake_download

    response = client.get(
        "/download",
        params={"bucket_name": "my-bucket", "object_name": "reports/archive.zip"},
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

    def fake_download(_bucket: str, _key: str, dest: str) -> bool:
        captured_dest["path"] = dest
        Path(dest).write_bytes(b"cleanup me")
        return True

    mock_storage_client.download_file.side_effect = fake_download

    response = client.get(
        "/download",
        params={"bucket_name": "my-bucket", "object_name": "cleanup.txt"},
    )

    assert response.status_code == HTTP_OK
    assert response.content == b"cleanup me"
    assert "path" in captured_dest
    assert not Path(captured_dest["path"]).exists()
