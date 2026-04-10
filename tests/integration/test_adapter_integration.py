"""Integration tests for the service adapter over the real FastAPI app."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pytest
from aws_client_adapter.service_adapter import CloudStorageServiceAdapter
from aws_client_adapter.service_adapter import (
    get_client_impl as get_adapter_client_impl,
)
from aws_client_service.main import app, get_storage_client
from cloud_storage_api import CloudStorageClient, DeleteResult, ObjectInfo
from fastapi.testclient import TestClient

from aws_s3_cloud_storage_service_client import AuthenticatedClient

if TYPE_CHECKING:
    from typing import BinaryIO

pytestmark = pytest.mark.integration


class FileBackedStorageClient(CloudStorageClient):
    """Simple container-aware storage fake used for adapter integration tests."""

    def __init__(self, root: Path) -> None:
        """Initialize the storage root for test objects."""
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, container: str, object_name: str) -> Path:
        """Resolve a container/object pair into a safe local filesystem path."""
        object_path = PurePosixPath(object_name)
        if ".." in object_path.parts:
            msg = "Path traversal is not allowed"
            raise ValueError(msg)

        return self._root.joinpath(container, *object_path.parts)

    def upload_file(
        self, container: str, local_path: str, remote_path: str
    ) -> ObjectInfo:
        """Upload a local file into the file-backed test storage."""
        source = Path(local_path)
        destination = self._resolve_path(container, remote_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return ObjectInfo(object_name=remote_path, size_bytes=source.stat().st_size)

    def upload_obj(
        self, container: str, file_obj: BinaryIO, remote_path: str
    ) -> ObjectInfo:
        """Upload a file-like object into the file-backed test storage."""
        destination = self._resolve_path(container, remote_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = file_obj.read()
        destination.write_bytes(data)
        return ObjectInfo(object_name=remote_path, size_bytes=len(data))

    def download_file(
        self, container: str, object_name: str, file_name: str
    ) -> ObjectInfo:
        """Download an object from test storage into a local file path."""
        source = self._resolve_path(container, object_name)
        if not source.exists():
            msg = f"Object '{object_name}' not found"
            raise FileNotFoundError(msg)

        destination = Path(file_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return ObjectInfo(object_name=object_name, size_bytes=source.stat().st_size)

    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        """List files in a container with an optional prefix filter."""
        container_root = self._root / container
        if not container_root.exists():
            return []

        return sorted(
            (
                ObjectInfo(
                    object_name=path.relative_to(container_root).as_posix(),
                    size_bytes=path.stat().st_size,
                )
                for path in container_root.rglob("*")
                if path.is_file()
                and path.relative_to(container_root).as_posix().startswith(prefix)
            ),
            key=lambda info: info.object_name,
        )

    def delete_file(self, container: str, object_name: str) -> DeleteResult:
        """Delete an object from the file-backed test storage."""
        target = self._resolve_path(container, object_name)
        if not target.exists():
            return DeleteResult(deleted=False)

        target.unlink()
        return DeleteResult(deleted=True)

    def get_file_info(self, container: str, object_name: str) -> ObjectInfo:
        """Return metadata for a stored object."""
        target = self._resolve_path(container, object_name)
        if not target.exists():
            msg = f"Object '{object_name}' not found"
            raise FileNotFoundError(msg)
        return ObjectInfo(object_name=object_name, size_bytes=target.stat().st_size)


@pytest.mark.circleci
def test_adapter_get_client_impl_returns_http_backed_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_client_impl from the adapter returns the HTTP-backed client."""
    monkeypatch.setenv("CLOUD_STORAGE_SERVICE_BASE_URL", "http://testserver")
    client = get_adapter_client_impl()

    assert isinstance(client, CloudStorageClient)
    assert isinstance(client, CloudStorageServiceAdapter)


@pytest.mark.circleci
def test_service_adapter_exercises_real_service_path(tmp_path: Path) -> None:
    """The adapter works end-to-end against the real FastAPI service."""
    storage_root = tmp_path / "storage"
    source_file = tmp_path / "source.txt"
    download_target = tmp_path / "downloaded.txt"
    source_file.write_text("hello from adapter")

    app.dependency_overrides[get_storage_client] = lambda: FileBackedStorageClient(
        storage_root
    )

    try:
        with TestClient(app) as test_client:
            generated_client = AuthenticatedClient(
                base_url="http://testserver",
                token="test-token",  # noqa: S106 - test-only token for in-process integration test
            )
            generated_client.set_httpx_client(test_client)
            adapter = CloudStorageServiceAdapter(generated_client)

            upload_result = adapter.upload_file(
                "demo-bucket",
                str(source_file),
                "nested/source.txt",
            )
            assert isinstance(upload_result, ObjectInfo)
            assert upload_result.object_name == "nested/source.txt"

            listed = adapter.list_files("demo-bucket", "nested/")
            assert len(listed) == 1
            assert listed[0].object_name == "nested/source.txt"

            download_result = adapter.download_file(
                "demo-bucket",
                "nested/source.txt",
                str(download_target),
            )
            assert isinstance(download_result, ObjectInfo)
            assert download_target.read_text() == "hello from adapter"

            delete_result = adapter.delete_file("demo-bucket", "nested/source.txt")
            assert isinstance(delete_result, dict)
            assert delete_result["deleted"] is True

            assert adapter.list_files("demo-bucket", "") == []
    finally:
        app.dependency_overrides.clear()
