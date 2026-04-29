"""Deterministic storage fakes shared by integration and e2e tests."""

from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from cloud_storage_api import CloudStorageClient, DeleteResult, ObjectInfo

if TYPE_CHECKING:
    from typing import BinaryIO


class FileBackedStorageClient(CloudStorageClient):
    """Container-aware storage fake backed by the local filesystem."""

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
            return DeleteResult(deleted=False, version_id=None, request_charged=None)

        target.unlink()
        return DeleteResult(deleted=True, version_id=None, request_charged=None)

    def get_file_info(self, container: str, object_name: str) -> ObjectInfo:
        """Return metadata for a stored object."""
        target = self._resolve_path(container, object_name)
        if not target.exists():
            msg = f"Object '{object_name}' not found"
            raise FileNotFoundError(msg)
        return ObjectInfo(object_name=object_name, size_bytes=target.stat().st_size)
