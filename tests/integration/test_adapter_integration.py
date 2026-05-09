"""Integration tests for the service adapter over the real FastAPI app."""

from __future__ import annotations

from pathlib import Path

import pytest
from aws_client_service.deps import require_oauth_session
from aws_client_service.main import app, get_storage_client
from cloud_storage_api import (
    AuthenticationError,
    CloudStorageClient,
    ContainerNotFoundError,
    ObjectInfo,
    StorageBackendError,
)
from fastapi.testclient import TestClient
from test_support.storage_fakes import FileBackedStorageClient

from aws_client_adapter import CloudStorageServiceAdapter
from aws_client_adapter import (
    get_client_impl as get_adapter_client_impl,
)
from aws_s3_cloud_storage_service_client import AuthenticatedClient

pytestmark = pytest.mark.integration

API_KEY = "test-token"


class _ContainerMissingStorageClient(FileBackedStorageClient):
    """Storage fake that reports a missing container through the domain API."""

    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        """Report the container as missing."""
        msg = f"Container '{container}' not found"
        raise ContainerNotFoundError(msg)


class _AuthFailingStorageClient(FileBackedStorageClient):
    """Storage fake that reports an authentication failure."""

    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        """Report authentication failure."""
        msg = "Storage credentials rejected"
        raise AuthenticationError(msg)


class _BackendFailingStorageClient(FileBackedStorageClient):
    """Storage fake that reports a backend failure."""

    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        """Report a backend failure."""
        msg = "Upstream storage unavailable"
        raise StorageBackendError(msg)


@pytest.mark.circleci
def test_adapter_get_client_impl_returns_http_backed_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_client_impl from the adapter returns the HTTP-backed client."""
    monkeypatch.setenv("CLOUD_STORAGE_SERVICE_BASE_URL", "http://testserver")
    client = get_adapter_client_impl()

    assert isinstance(client, CloudStorageClient)
    assert isinstance(client, CloudStorageServiceAdapter)


def _build_adapter_client() -> AuthenticatedClient:
    """Create a generated client configured for the in-process test server."""
    return AuthenticatedClient(base_url="http://testserver", token=API_KEY)


def _bind_test_client(
    generated_client: AuthenticatedClient,
    test_client: TestClient,
    *,
    token: str,
) -> CloudStorageServiceAdapter:
    """Bind auth headers to the in-process client and return an adapter."""
    test_client.headers["Authorization"] = f"Bearer {token}"
    generated_client.set_httpx_client(test_client)
    return CloudStorageServiceAdapter(generated_client)


@pytest.mark.circleci
def test_service_adapter_preserves_storage_invariants_across_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Adapter + service preserve core storage invariants across a workflow."""
    storage_root = tmp_path / "storage"
    source_a = tmp_path / "report-a.txt"
    source_b = tmp_path / "report-b.txt"
    download_target = tmp_path / "downloaded.txt"
    source_a.write_text("alpha payload")
    source_b.write_text("beta payload")

    monkeypatch.setenv("API_KEY", API_KEY)
    app.dependency_overrides.pop(require_oauth_session, None)

    app.dependency_overrides[get_storage_client] = lambda: FileBackedStorageClient(
        storage_root
    )

    try:
        with TestClient(app) as test_client:
            generated_client = _build_adapter_client()
            adapter = _bind_test_client(
                generated_client,
                test_client,
                token=API_KEY,
            )

            upload_a = adapter.upload_file(
                "demo-bucket",
                str(source_a),
                "nested/report-a.txt",
            )
            upload_b = adapter.upload_file(
                "demo-bucket",
                str(source_b),
                "nested/report-b.txt",
            )
            assert upload_a.object_name == "nested/report-a.txt"
            assert upload_b.object_name == "nested/report-b.txt"

            listed = adapter.list_files("demo-bucket", "nested/")
            assert [info.object_name for info in listed] == [
                "nested/report-a.txt",
                "nested/report-b.txt",
            ]
            assert [info.size_bytes for info in listed] == [
                len("alpha payload"),
                len("beta payload"),
            ]

            info = adapter.get_file_info("demo-bucket", "nested/report-a.txt")
            assert info == listed[0]

            download_result = adapter.download_file(
                "demo-bucket",
                "nested/report-a.txt",
                str(download_target),
            )
            assert isinstance(download_result, ObjectInfo)
            assert download_target.read_text() == "alpha payload"
            assert download_result.object_name == "nested/report-a.txt"

            delete_result = adapter.delete_file("demo-bucket", "nested/report-a.txt")
            assert delete_result["deleted"] is True

            # Antithesis-style invariant: deleting a missing object is stable.
            repeated_delete = adapter.delete_file("demo-bucket", "nested/report-a.txt")
            assert repeated_delete == {
                "deleted": False,
                "version_id": None,
                "request_charged": None,
            }

            remaining = adapter.list_files("demo-bucket", "")
            assert [info.object_name for info in remaining] == ["nested/report-b.txt"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.circleci
def test_service_adapter_requires_valid_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authentication failures survive the service hop as domain errors."""
    monkeypatch.setenv("API_KEY", API_KEY)
    app.dependency_overrides.pop(require_oauth_session, None)
    app.dependency_overrides[get_storage_client] = lambda: FileBackedStorageClient(
        tmp_path / "storage"
    )

    try:
        with TestClient(app) as test_client:
            wrong_token = "wrong-token"
            generated_client = AuthenticatedClient(
                base_url="http://testserver",
                token=wrong_token,
            )
            adapter = _bind_test_client(
                generated_client,
                test_client,
                token=wrong_token,
            )

            with pytest.raises(AuthenticationError, match="Authentication required"):
                adapter.list_files("demo-bucket", "")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.circleci
@pytest.mark.parametrize(
    ("storage_cls", "expected_exception", "message"),
    [
        (_ContainerMissingStorageClient, ContainerNotFoundError, "Container"),
        (_AuthFailingStorageClient, AuthenticationError, "Storage credentials"),
        (_BackendFailingStorageClient, StorageBackendError, "List files failed"),
    ],
)
def test_service_adapter_preserves_service_failure_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    storage_cls: type[FileBackedStorageClient],
    expected_exception: type[Exception],
    message: str,
) -> None:
    """Adapter preserves container/auth/backend failures across HTTP transport."""
    monkeypatch.setenv("API_KEY", API_KEY)
    app.dependency_overrides.pop(require_oauth_session, None)
    app.dependency_overrides[get_storage_client] = lambda: storage_cls(
        tmp_path / "storage"
    )

    try:
        with TestClient(app) as test_client:
            generated_client = _build_adapter_client()
            adapter = _bind_test_client(
                generated_client,
                test_client,
                token=API_KEY,
            )

            with pytest.raises(expected_exception, match=message):
                adapter.list_files("demo-bucket", "")
    finally:
        app.dependency_overrides.clear()
