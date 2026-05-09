"""Unit tests for the upload endpoint in the AWS S3 FastAPI service."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cloud_storage_api import (
    InvalidObjectNameError,
    ObjectInfo,
    StorageBackendError,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_BAD_GATEWAY = 502


def _stub_object_info(name: str = "photo.jpg") -> ObjectInfo:
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
def test_upload_returns_ok_on_success(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """POST returns 200 with object_name on success."""
    mock_storage_client.upload_obj.return_value = _stub_object_info("photo.jpg")

    response = client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"fake image bytes", "image/jpeg")},
    )

    assert response.status_code == HTTP_OK
    body = response.json()
    assert body["object_name"] == "photo.jpg"


@pytest.mark.circleci
def test_upload_returns_502_on_exception(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """POST returns 502 when upload_object raises an exception."""
    mock_storage_client.upload_obj.side_effect = StorageBackendError("connection lost")

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
    """POST returns 400 when upload_object raises ValueError (e.g. bad key)."""
    mock_storage_client.upload_obj.side_effect = InvalidObjectNameError(
        "Key cannot be empty"
    )

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
    """POST /files/{container}/{object_name} passes correct key to upload_object."""
    mock_storage_client.upload_obj.return_value = _stub_object_info("photo.jpg")

    client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"fake image bytes", "image/jpeg")},
    )

    args, _kwargs = mock_storage_client.upload_obj.call_args
    assert args[0] == "my-bucket"
    assert args[2] == "photo.jpg"


@pytest.mark.circleci
def test_upload_nested_key(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """POST endpoint handles nested S3 keys with slashes."""
    mock_storage_client.upload_obj.return_value = _stub_object_info(
        "folder/sub/photo.jpg"
    )

    response = client.post(
        "/files/my-bucket/folder/sub/photo.jpg",
        files={"file": ("photo.jpg", b"fake image bytes", "image/jpeg")},
    )

    assert response.status_code == HTTP_OK
    body = response.json()
    assert body["object_name"] == "folder/sub/photo.jpg"
    args, _kwargs = mock_storage_client.upload_obj.call_args
    assert args[0] == "my-bucket"
    assert args[2] == "folder/sub/photo.jpg"
