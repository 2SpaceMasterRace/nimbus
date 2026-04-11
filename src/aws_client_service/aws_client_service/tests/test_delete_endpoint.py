"""Unit tests for the delete endpoint in the AWS S3 FastAPI service."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from aws_client_service.main import app, get_storage_client
from cloud_storage_api import DeleteResult, ObjectNotFoundError, StorageBackendError
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
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


def _stub_delete_result(*, deleted: bool = True) -> DeleteResult:
    """Return a minimal DeleteResult for use in test stubs."""
    return DeleteResult(deleted=deleted, version_id=None, request_charged=None)


@pytest.mark.circleci
def test_delete_returns_ok_on_success(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """DELETE /files/{container}/{object_name} returns 200 with deleted=true."""
    mock_storage_client.delete_file.return_value = _stub_delete_result(deleted=True)

    response = client.delete("/files/my-bucket/my-key")

    assert response.status_code == HTTP_OK
    body = response.json()
    assert body["deleted"] is True


@pytest.mark.circleci
def test_delete_returns_404_on_failure(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """DELETE returns 404 when delete_file raises ObjectNotFoundError."""
    mock_storage_client.delete_file.side_effect = ObjectNotFoundError(
        "Object 'my-key' was not found in container 'my-bucket'"
    )

    response = client.delete("/files/my-bucket/my-key")

    assert response.status_code == HTTP_NOT_FOUND
    assert (
        response.json()["detail"]
        == "Object 'my-key' was not found in container 'my-bucket'"
    )


@pytest.mark.circleci
def test_delete_returns_502_on_exception(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """DELETE /files/{container}/{object_name} returns 502 when delete_file raises."""
    mock_storage_client.delete_file.side_effect = StorageBackendError("connection lost")

    response = client.delete("/files/my-bucket/my-key")

    assert response.status_code == HTTP_BAD_GATEWAY
    assert response.json()["detail"] == "Delete failed due to a storage error"


@pytest.mark.circleci
def test_delete_calls_client_with_correct_args(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """DELETE /files/{container}/{object_name} passes path params to delete_file."""
    mock_storage_client.delete_file.return_value = _stub_delete_result(deleted=True)

    client.delete("/files/my-bucket/my-key")

    mock_storage_client.delete_file.assert_called_once_with("my-bucket", "my-key")


@pytest.mark.circleci
def test_delete_nested_key(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """DELETE endpoint handles nested S3 keys with slashes."""
    mock_storage_client.delete_file.return_value = _stub_delete_result(deleted=True)

    response = client.delete("/files/my-bucket/folder/sub/file.txt")

    assert response.status_code == HTTP_OK
    body = response.json()
    assert body["deleted"] is True
    mock_storage_client.delete_file.assert_called_once_with(
        "my-bucket",
        "folder/sub/file.txt",
    )
