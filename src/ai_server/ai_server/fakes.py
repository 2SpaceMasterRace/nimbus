"""Fake storage client and related types shared across ai_server tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from cloud_storage_api import ObjectNotFoundError


@dataclass
class FakeObjectInfo:
    """Minimal stand-in for ``cloud_storage_api.ObjectInfo`` in ai_server tests."""

    object_name: str
    size_bytes: int | None = None
    version_id: str | None = None
    updated_at: str | None = None


@dataclass
class FakeDeleteResult:
    """Minimal stand-in for ``cloud_storage_api.DeleteResult`` in ai_server tests."""

    deleted: bool = True
    version_id: str | None = None


@dataclass
class FakeStorageClient:
    """Records wrapper-route storage-tool calls for assertions in tests.

    Implements the full ``CloudStorageClient`` protocol plus the optional
    S3-specific extensions (``read_object``, ``get_object_range``,
    ``copy_object``, ``list_files_page``, ``force_delete``) so the test
    suite exercises the fast in-memory paths rather than the tempfile
    fallbacks.
    """

    lists: list[dict[str, Any]] = field(default_factory=list)
    infos: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[dict[str, Any]] = field(default_factory=list)
    uploads: list[dict[str, Any]] = field(default_factory=list)
    copies: list[dict[str, Any]] = field(default_factory=list)
    list_return: list[FakeObjectInfo] = field(default_factory=list)
    # (items, next_token) returned by list_files_page.  next_token="" means
    # no further pages.
    list_page_return: tuple[list[FakeObjectInfo], str] = field(
        default_factory=lambda: ([], "")
    )
    info_return: FakeObjectInfo = field(
        default_factory=lambda: FakeObjectInfo(object_name="reports/q1.csv")
    )
    delete_return: FakeDeleteResult = field(default_factory=FakeDeleteResult)
    upload_return: FakeObjectInfo = field(
        default_factory=lambda: FakeObjectInfo(object_name="uploaded/report.csv")
    )
    upload_error_by_remote_path: dict[str, Exception] = field(default_factory=dict)
    # Keys in this set raise ObjectNotFoundError from get_file_info — used to
    # simulate "destination key is free" for the copy_file / move_file tools.
    missing_objects: set[str] = field(default_factory=set)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    # Configurable body returned by read_object and get_object_range.
    read_body: bytes = b""
    # Total size reported in the get_object_range response (simulates the
    # Content-Range header total).  Set to 0 to simulate a response where the
    # entire object fit within max_bytes (Content-Range absent).
    range_total: int = 0

    def list_files(self, *, container: str, prefix: str = "") -> list[FakeObjectInfo]:
        """Record the call and return the configured list result."""
        self.lists.append({"container": container, "prefix": prefix})
        return self.list_return

    def get_file_info(self, *, container: str, object_name: str) -> FakeObjectInfo:
        """Record the call and return the configured info result."""
        self.infos.append({"container": container, "object_name": object_name})
        if object_name in self.missing_objects:
            msg = f"{container}/{object_name} not found"
            raise ObjectNotFoundError(msg)
        return self.info_return

    def download_file(
        self, *, container: str, object_name: str, file_name: str
    ) -> FakeObjectInfo:
        """Record the call; write empty bytes locally so upload_file can re-read."""
        self.downloads.append(
            {
                "container": container,
                "object_name": object_name,
                "file_name": file_name,
            }
        )
        Path(file_name).write_bytes(b"")
        return FakeObjectInfo(object_name=object_name)

    def delete_file(self, *, container: str, object_name: str) -> FakeDeleteResult:
        """Record the call and return the configured delete result."""
        self.deletes.append({"container": container, "object_name": object_name})
        self.missing_objects.add(object_name)
        return self.delete_return

    def upload_file(
        self, *, container: str, local_path: str, remote_path: str
    ) -> FakeObjectInfo:
        """Record the call and return the configured upload result."""
        self.uploads.append(
            {
                "container": container,
                "local_path": local_path,
                "remote_path": remote_path,
            }
        )
        if remote_path in self.upload_error_by_remote_path:
            raise self.upload_error_by_remote_path[remote_path]
        self.missing_objects.discard(remote_path)
        return FakeObjectInfo(
            object_name=remote_path,
            size_bytes=self.upload_return.size_bytes,
            version_id=self.upload_return.version_id,
            updated_at=self.upload_return.updated_at,
        )

    def upload_obj(
        self, *, container: str, file_obj: BinaryIO, remote_path: str
    ) -> FakeObjectInfo:
        """Record the call; read body bytes from file_obj for assertion."""
        body = file_obj.read()
        self.uploads.append(
            {
                "container": container,
                "remote_path": remote_path,
                "body": body,
            }
        )
        if remote_path in self.upload_error_by_remote_path:
            raise self.upload_error_by_remote_path[remote_path]
        self.missing_objects.discard(remote_path)
        return FakeObjectInfo(object_name=remote_path, size_bytes=len(body))

    def copy_object(
        self,
        src_container: str,
        src_key: str,
        dst_container: str,
        dst_key: str,
    ) -> FakeObjectInfo:
        """Record a server-side copy; no bytes read or uploaded."""
        self.copies.append(
            {
                "src_container": src_container,
                "src_key": src_key,
                "dst_container": dst_container,
                "dst_key": dst_key,
            }
        )
        self.missing_objects.discard(dst_key)
        return FakeObjectInfo(object_name=dst_key)

    def list_files_page(
        self,
        container: str,
        prefix: str,
        max_keys: int,
        continuation_token: str = "",
    ) -> tuple[list[FakeObjectInfo], str]:
        """Record the call and return the configured page result."""
        self.lists.append(
            {
                "container": container,
                "prefix": prefix,
                "max_keys": max_keys,
                "continuation_token": continuation_token,
            }
        )
        items, next_token = self.list_page_return
        return items[:max_keys], next_token

    def force_delete(self, container: str, key: str) -> FakeDeleteResult:
        """Record a delete without an existence-check HEAD."""
        self.deletes.append({"container": container, "object_name": key})
        self.missing_objects.add(key)
        return self.delete_return

    # Optional S3 extensions — present on FakeStorageClient so tests exercise
    # the fast in-memory code paths in the tool layer.

    def read_object(self, container: str, key: str) -> bytes:
        """Return the configured read body."""
        del container, key
        return self.read_body

    def get_object_range(
        self, container: str, key: str, start: int, end: int
    ) -> tuple[bytes, int]:
        """Return a slice of read_body and the configured total size."""
        del container, key
        body = self.read_body
        content = body[start : min(end + 1, len(body))] if body else b""
        return content, self.range_total
