"""Unit tests for the upload endpoint in the AWS S3 FastAPI service."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from aws_client_service.main import app, get_storage_client
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

HTTP_OK = 200
HTTP_BAD_REQUEST= 400
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
def test_rott_returns_hello(client: TestClient) -> None:
    """GET / returns 200 with a hello world message."""
    response = client.get("/")

    assert response.status_code == HTTP_OK
    assert response.json() == {"message": "Hello World"}

@pytest.mark.circleci
def test_upload_returns_ok_on_success(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """POST /files/{container}/{object_name} returns 200 with ok = true when upload_obj succeeds"""
    mock_storage_client.upload_obj.return_value = True

    response = client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"fake image bytes", "image/jpeg")},
    )

    assert response.status_code == HTTP_OK
    assert response.json() == {"ok": True}

@pytest.mark.circleci
def test_upload_returns_502_on_exception(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """POST /files/{container}/{object_name} returns 502 when upload_obj raises an exception."""
    mock_storage_client.upload_obj.side_effect = RuntimeError("connection lost")

    response = client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"fake image bytes", "image/jpeg")},
    )

    assert response.status_code == HTTP_BAD_GATEWAY
    assert response.json()["detail"] == "Upload failed due to a storage error"

@pytest.mark.circleci
def test_upload_returns_400_on_value_error(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """POST returns 400 when upload_obj raises ValueError (e.g. bad key)."""
    mock_storage_client.upload_obj.side_effect = ValueError("Key cannot be empty")

    response = client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"fake image bytes", "image/jpeg")},
    )

    assert response.status_code == HTTP_BAD_REQUEST
    assert response.json()["detail"] == "Key cannot be empty"

@pytest.mark.circleci
def test_upload_calls_client_with_correct_args(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """POST /files/{container}/{object_name} passes correct key to upload_obj."""
    mock_storage_client.upload_obj.return_value = True

    client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"fake image bytes", "image/jpeg")},
    )

    args, kwargs = mock_storage_client.upload_obj.call_args
    assert args[1] == "photo.jpg"

@pytest.mark.circleci
def test_upload_nested_key(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """POST endpoint handles nested S3 keys with slashes."""

    mock_storage_client.upload_obj.return_value = True

    response = client.post(
        "/files/my-bucket/folder/sub/photo.jpg",
        files={"file": ("photo.jpg", b"fake image bytes", "image/jpeg")},
    )

    assert response.status_code == HTTP_OK
    assert response.json() == {"ok": True}
    args, kwargs = mock_storage_client.upload_obj.call_args
    assert args[1] == "folder/sub/photo.jpg"