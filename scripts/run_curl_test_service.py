"""Run the FastAPI service with a local storage override for curl testing."""

from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import uvicorn
from cloud_storage_api import CloudStorageClient, DeleteResult, ObjectInfo


class LocalCurlTestClient(CloudStorageClient):
    """Simple filesystem-backed client used only for local curl testing."""

    def __init__(self, root: Path) -> None:
        """Initialize the local storage root directory."""
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve_remote_path(self, container: str, remote_path: str) -> Path:
        """Resolve a container and object key into a safe local filesystem path."""
        if not container:
            msg = "Container cannot be empty"
            raise ValueError(msg)

        if not remote_path or remote_path.startswith("/"):
            msg = "Key cannot be empty"
            raise ValueError(msg)

        object_path = PurePosixPath(remote_path)
        if ".." in object_path.parts:
            msg = "Path traversal is not allowed"
            raise ValueError(msg)

        container_path = PurePosixPath(container)
        return self._root.joinpath(*container_path.parts, *object_path.parts)

    def _object_info(self, remote_path: str, local_path: Path) -> ObjectInfo:
        """Build ObjectInfo from a local file path."""
        return ObjectInfo(
            object_name=remote_path,
            size_bytes=local_path.stat().st_size if local_path.exists() else None,
        )

    def upload_file(
        self, container: str, local_path: str, remote_path: str
    ) -> ObjectInfo:
        """Upload a local file into the test storage root."""
        source_path = Path(local_path)
        if not source_path.exists():
            msg = f"File not found: {local_path}"
            raise FileNotFoundError(msg)

        destination_path = self._resolve_remote_path(container, remote_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination_path)
        return self._object_info(remote_path, destination_path)

    def upload_obj(
        self, container: str, file_obj: BinaryIO, remote_path: str
    ) -> ObjectInfo:
        """Upload a file-like object into the test storage root."""
        destination_path = self._resolve_remote_path(container, remote_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_bytes(file_obj.read())
        return self._object_info(remote_path, destination_path)

    def download_file(
        self,
        container: str,
        object_name: str,
        file_name: str,
    ) -> ObjectInfo:
        """Download a stored object into a local file path."""
        source_path = self._resolve_remote_path(container, object_name)
        if not source_path.exists():
            msg = f"Object '{object_name}' not found in container '{container}'"
            raise FileNotFoundError(msg)

        destination_path = Path(file_name)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination_path)
        return self._object_info(object_name, source_path)

    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        """List stored object keys filtered by a prefix."""
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
        """Delete a stored object if it exists."""
        object_path = self._resolve_remote_path(container, object_name)
        if not object_path.exists():
            return DeleteResult(deleted=False)

        object_path.unlink()
        return DeleteResult(deleted=True)

    def get_file_info(self, container: str, object_name: str) -> ObjectInfo:
        """Return metadata for a stored object."""
        target = self._resolve_remote_path(container, object_name)
        if not target.exists():
            msg = f"Object '{object_name}' not found in container '{container}'"
            raise FileNotFoundError(msg)
        return self._object_info(object_name, target)


def main() -> None:
    """Start the FastAPI service with local storage and API-key auth defaults."""
    os.environ.setdefault("SESSION_SECRET_KEY", "local-curl-test-session-secret")
    os.environ.setdefault("API_KEY", "local-curl-test-key")
    os.environ.setdefault("GITHUB_AUTH_URI", "https://github.com/login/oauth/authorize")
    os.environ.setdefault(
        "GITHUB_TOKEN_URI",
        "https://github.com/login/oauth/access_token",
    )
    os.environ.setdefault("GITHUB_CLIENT_ID", "dummy-client-id")
    os.environ.setdefault("GITHUB_CLIENT_SECRET", "dummy-client-secret")
    os.environ.setdefault(
        "GITHUB_LOCAL_REDIRECT_URI",
        "http://127.0.0.1:8001/auth/callback",
    )

    from aws_client_service.main import (  # noqa: PLC0415
        app,
        get_storage_client,
    )

    storage_root = Path(__file__).resolve().parents[1] / ".curl-test-storage"
    client = LocalCurlTestClient(storage_root)
    app.dependency_overrides[get_storage_client] = lambda: client

    uvicorn.run(app, host="127.0.0.1", port=8001)


if __name__ == "__main__":
    main()
