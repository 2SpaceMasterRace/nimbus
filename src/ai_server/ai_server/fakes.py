"""Fake storage client and related types shared across ai_server tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


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
    """Records wrapper-route storage-tool calls for assertions in tests."""

    lists: list[dict[str, Any]] = field(default_factory=list)
    infos: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[dict[str, Any]] = field(default_factory=list)
    uploads: list[dict[str, Any]] = field(default_factory=list)
    list_return: list[FakeObjectInfo] = field(default_factory=list)
    info_return: FakeObjectInfo = field(
        default_factory=lambda: FakeObjectInfo(object_name="reports/q1.csv")
    )
    delete_return: FakeDeleteResult = field(default_factory=FakeDeleteResult)
    upload_return: FakeObjectInfo = field(
        default_factory=lambda: FakeObjectInfo(object_name="uploaded/report.csv")
    )
    upload_error_by_remote_path: dict[str, Exception] = field(default_factory=dict)

    def list_files(self, *, container: str, prefix: str = "") -> list[FakeObjectInfo]:
        """Record the call and return the configured list result."""
        self.lists.append({"container": container, "prefix": prefix})
        return self.list_return

    def get_file_info(self, *, container: str, object_name: str) -> FakeObjectInfo:
        """Record the call and return the configured info result."""
        self.infos.append({"container": container, "object_name": object_name})
        return self.info_return

    def delete_file(self, *, container: str, object_name: str) -> FakeDeleteResult:
        """Record the call and return the configured delete result."""
        self.deletes.append({"container": container, "object_name": object_name})
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
        return FakeObjectInfo(
            object_name=remote_path,
            size_bytes=self.upload_return.size_bytes,
            version_id=self.upload_return.version_id,
            updated_at=self.upload_return.updated_at,
        )
