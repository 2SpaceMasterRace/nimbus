"""Integration tests for the FastAPI service dependency injection wiring.

This module verifies that the FastAPI ``Depends()`` mechanism correctly
resolves ``get_storage_client`` to a concrete ``CloudStorageClient``
implementation, and that endpoints work end-to-end with a mocked storage
backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from aws_client_impl.s3_client import S3Client, get_client_impl
from aws_client_service.main import app, get_storage_client
from cloud_storage_client_api.client import CloudStorageClient
from cloud_storage_client_api.factory import register_client
from starlette.testclient import TestClient

import aws_client_impl  # noqa: F401  — registers S3Client factory as side-effect

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.integration

HTTP_OK = 200


@pytest.mark.circleci
def test_service_di_returns_cloud_storage_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_storage_client() returns a concrete CloudStorageClient via the factory."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    register_client(get_client_impl)
    client = get_storage_client()
    assert isinstance(client, CloudStorageClient)
    assert isinstance(client, S3Client)


@pytest.mark.circleci
def test_service_health_with_real_app() -> None:
    """The /health endpoint returns 200 with the expected payload."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == HTTP_OK
    assert response.json() == {"status": "ok"}


@pytest.mark.circleci
def test_upload_endpoint_uses_injected_client(
    mocker: MockerFixture,
) -> None:
    """The upload endpoint resolves the DI client and returns JSON confirmation."""
    mock_client = mocker.create_autospec(
        CloudStorageClient,
        instance=True,
    )
    mock_client.upload_obj.return_value = True

    app.dependency_overrides[get_storage_client] = lambda: mock_client
    try:
        client = TestClient(app)
        response = client.post(
            "/files/test-bucket/some-key.txt",
            files={
                "file": ("some-key.txt", b"fake content", "application/octet-stream")
            },
        )
        assert response.status_code == HTTP_OK
        assert response.json() == {"ok": True}
        args, _kwargs = mock_client.upload_obj.call_args
        assert args[0] == "test-bucket"
        assert args[2] == "some-key.txt"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.circleci
def test_download_endpoint_uses_injected_client(
    mocker: MockerFixture,
) -> None:
    """The download endpoint resolves the DI client and streams the file back."""
    mock_client = mocker.create_autospec(
        CloudStorageClient,
        instance=True,
    )

    def _fake_download(
        _container: str,
        _object_name: str,
        file_name: str,
    ) -> bool:
        Path(file_name).write_text("hello from mock")
        return True

    mock_client.download_file.side_effect = _fake_download

    app.dependency_overrides[get_storage_client] = lambda: mock_client
    try:
        client = TestClient(app)
        response = client.get(
            "/download",
            params={"container": "demo-bucket", "object_name": "key.txt"},
        )
        assert response.status_code == HTTP_OK
        assert response.text == "hello from mock"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.circleci
def test_delete_endpoint_uses_injected_client(
    mocker: MockerFixture,
) -> None:
    """The delete endpoint resolves the DI client and returns JSON confirmation."""
    mock_client = mocker.create_autospec(
        CloudStorageClient,
        instance=True,
    )
    mock_client.delete_file.return_value = True

    app.dependency_overrides[get_storage_client] = lambda: mock_client
    try:
        client = TestClient(app)
        response = client.delete("/files/test-bucket/some-key.txt")
        assert response.status_code == HTTP_OK
        assert response.json() == {"ok": True}
        mock_client.delete_file.assert_called_once_with(
            "test-bucket",
            "some-key.txt",
        )
    finally:
        app.dependency_overrides.clear()
