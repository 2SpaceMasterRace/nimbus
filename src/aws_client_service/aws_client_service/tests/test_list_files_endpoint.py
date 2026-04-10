"""Unit tests for the list files endpoint in the AWS S3 FastAPI service."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from aws_client_service.main import app, get_storage_client
from cloud_storage_api import ObjectInfo, StorageBackendError
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit

HTTP_OK = 200
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


def test_list_files_returns_matching_files(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /files returns a list of matching object metadata."""
    mock_storage_client.list_files.return_value = [
        ObjectInfo(object_name="docs/a.txt"),
        ObjectInfo(object_name="docs/b.txt"),
    ]

    response = client.get(
        "/files",
        params={"container": "docs-bucket", "prefix": "docs/"},
    )

    assert response.status_code == HTTP_OK
    body = response.json()
    expected_count = 2
    assert len(body) == expected_count
    assert body[0]["object_name"] == "docs/a.txt"
    assert body[1]["object_name"] == "docs/b.txt"
    mock_storage_client.list_files.assert_called_once_with("docs-bucket", "docs/")


def test_list_files_returns_empty_list(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /files returns an empty list when no files match."""
    mock_storage_client.list_files.return_value = []

    response = client.get(
        "/files",
        params={"container": "docs-bucket", "prefix": "missing/"},
    )

    assert response.status_code == HTTP_OK
    assert response.json() == []
    mock_storage_client.list_files.assert_called_once_with("docs-bucket", "missing/")


def test_list_files_requires_container(client: TestClient) -> None:
    """GET /files returns 422 when container is missing."""
    response = client.get("/files", params={"prefix": "docs/"})

    assert response.status_code == HTTP_UNPROCESSABLE


def test_list_files_storage_error(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /files returns 502 when the storage backend raises an exception."""
    mock_storage_client.list_files.side_effect = StorageBackendError("backend failure")

    response = client.get(
        "/files",
        params={"container": "docs-bucket", "prefix": "docs/"},
    )

    assert response.status_code == HTTP_BAD_GATEWAY
    assert response.json() == {"detail": "List files failed due to a storage error"}


def test_list_files_defaults_prefix_to_empty(
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """GET /files lists the whole container when prefix is omitted."""
    mock_storage_client.list_files.return_value = [
        ObjectInfo(object_name="a.txt"),
        ObjectInfo(object_name="b.txt"),
    ]

    response = client.get("/files", params={"container": "docs-bucket"})

    assert response.status_code == HTTP_OK
    body = response.json()
    expected_count = 2
    assert len(body) == expected_count
    assert body[0]["object_name"] == "a.txt"
    assert body[1]["object_name"] == "b.txt"
    mock_storage_client.list_files.assert_called_once_with("docs-bucket", "")
