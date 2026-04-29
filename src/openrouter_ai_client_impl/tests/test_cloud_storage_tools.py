"""Tests for the cloud-storage tool bindings.

These tests focus on the guardrails, not on the underlying S3 client — we
stub the storage layer with a hand-rolled fake so we can verify precisely
which arguments the handler forwards, and prove that the Pydantic layer
rejects bad input before any network I/O would occur.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

import pytest
from openrouter_ai_client_impl.cloud_storage_tools import (
    DEFAULT_MAX_UPLOAD_BYTES,
    build_cloud_storage_tools,
)

from ai_client_api import AIToolArgsInvalidError

pytestmark = pytest.mark.unit


# --- test-only fake storage ------------------------------------------------


@dataclass
class _FakeObjectInfo:
    """Minimal stand-in for ``cloud_storage_api.ObjectInfo``."""

    object_name: str
    size_bytes: int | None = None
    version_id: str | None = None
    updated_at: str | None = None


@dataclass
class FakeStorage:
    """Records every tool→storage call for assertions in tests."""

    uploads: list[dict[str, Any]] = field(default_factory=list)
    downloads: list[dict[str, Any]] = field(default_factory=list)
    lists: list[dict[str, Any]] = field(default_factory=list)
    deletes: list[dict[str, Any]] = field(default_factory=list)
    infos: list[dict[str, Any]] = field(default_factory=list)
    # Canned return values — tests mutate these when needed.
    upload_return: _FakeObjectInfo = field(
        default_factory=lambda: _FakeObjectInfo(object_name="k", size_bytes=3)
    )
    download_return: _FakeObjectInfo = field(
        default_factory=lambda: _FakeObjectInfo(object_name="k", version_id="v1")
    )
    list_return: list[_FakeObjectInfo] = field(default_factory=list)
    delete_return: dict[str, Any] = field(
        default_factory=lambda: {"deleted": True, "version_id": None}
    )
    info_return: _FakeObjectInfo = field(
        default_factory=lambda: _FakeObjectInfo(object_name="k", size_bytes=42)
    )

    def upload_file(
        self, *, container: str, local_path: str, remote_path: str
    ) -> _FakeObjectInfo:
        self.uploads.append(
            {
                "container": container,
                "local_path": local_path,
                "remote_path": remote_path,
            }
        )
        return self.upload_return

    def upload_obj(
        self, *, container: str, file_obj: BinaryIO, remote_path: str
    ) -> _FakeObjectInfo:  # pragma: no cover - unused in these tests
        del container, file_obj, remote_path
        return self.upload_return

    def download_file(
        self, *, container: str, object_name: str, file_name: str
    ) -> _FakeObjectInfo:
        # Simulate a real download by writing bytes so size_bytes is populated.
        Path(file_name).write_bytes(b"hello")
        self.downloads.append(
            {"container": container, "object_name": object_name, "file_name": file_name}
        )
        return self.download_return

    def list_files(self, *, container: str, prefix: str = "") -> list[_FakeObjectInfo]:
        self.lists.append({"container": container, "prefix": prefix})
        return self.list_return

    def delete_file(self, *, container: str, object_name: str) -> dict[str, Any]:
        self.deletes.append({"container": container, "object_name": object_name})
        return self.delete_return

    def get_file_info(self, *, container: str, object_name: str) -> _FakeObjectInfo:
        self.infos.append({"container": container, "object_name": object_name})
        return self.info_return


CONTAINER = "my-bucket"


def _tools_by_name(
    storage: FakeStorage,
    safe_root: Path,
    *,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    require_delete_confirmation: bool = True,
) -> dict[str, Any]:
    tools = build_cloud_storage_tools(
        storage=storage,  # type: ignore[arg-type]
        container=CONTAINER,
        safe_root=safe_root,
        max_upload_bytes=max_upload_bytes,
        require_delete_confirmation=require_delete_confirmation,
    )
    return {t.name: t for t in tools}


# --- build & schema --------------------------------------------------------


def test_build_returns_all_five_tools(tmp_path: Path) -> None:
    """We expose exactly the five cloud-storage operations the model needs."""
    tools = _tools_by_name(FakeStorage(), tmp_path)
    assert set(tools) == {
        "upload_file",
        "download_file",
        "list_files",
        "delete_file",
        "get_file_info",
    }


def test_tool_schemas_forbid_extra_args(tmp_path: Path) -> None:
    """The Pydantic models are declared ``extra='forbid'``; schema reflects that."""
    tools = _tools_by_name(FakeStorage(), tmp_path)
    for name in ("upload_file", "download_file", "list_files", "delete_file"):
        schema = tools[name].parameters_schema
        assert schema["additionalProperties"] is False, name


# --- upload guardrails -----------------------------------------------------


def test_upload_happy_path_pins_container(tmp_path: Path) -> None:
    """``upload_file`` forwards to the storage client with the pinned container."""
    source = tmp_path / "doc.txt"
    source.write_text("hello")
    storage = FakeStorage(
        upload_return=_FakeObjectInfo(
            object_name="uploads/doc.txt", size_bytes=5, version_id="v7"
        )
    )
    tool = _tools_by_name(storage, tmp_path)["upload_file"]

    result = tool.handler(local_path="doc.txt", remote_path="uploads/doc.txt")

    assert result == {
        "object_name": "uploads/doc.txt",
        "size_bytes": 5,
        "version_id": "v7",
    }
    assert storage.uploads == [
        {
            "container": CONTAINER,
            "local_path": str((tmp_path / "doc.txt").resolve()),
            "remote_path": "uploads/doc.txt",
        }
    ]


def test_upload_rejects_absolute_path(tmp_path: Path) -> None:
    """Absolute ``local_path`` is rejected before any storage call."""
    storage = FakeStorage()
    tool = _tools_by_name(storage, tmp_path)["upload_file"]
    with pytest.raises(AIToolArgsInvalidError) as exc_info:
        tool.handler(local_path=str(tmp_path / "abs.txt"), remote_path="x")
    assert exc_info.value.tool_name == "upload_file"
    assert storage.uploads == []


def test_upload_rejects_path_escape(tmp_path: Path) -> None:
    """Paths that escape ``safe_root`` via ``..`` are rejected."""
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    outside = tmp_path / "evil.txt"
    outside.write_text("hi")
    storage = FakeStorage()
    tool = _tools_by_name(storage, safe_root)["upload_file"]
    with pytest.raises(AIToolArgsInvalidError, match="escapes safe_root"):
        tool.handler(local_path="../evil.txt", remote_path="x")
    assert storage.uploads == []


def test_upload_rejects_missing_file(tmp_path: Path) -> None:
    """``upload_file`` must check the file exists locally before uploading."""
    storage = FakeStorage()
    tool = _tools_by_name(storage, tmp_path)["upload_file"]
    with pytest.raises(AIToolArgsInvalidError, match="does not exist"):
        tool.handler(local_path="missing.txt", remote_path="x")
    assert storage.uploads == []


def test_upload_enforces_size_cap(tmp_path: Path) -> None:
    """Files larger than ``max_upload_bytes`` are refused locally."""
    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * 1024)
    storage = FakeStorage()
    tool = _tools_by_name(storage, tmp_path, max_upload_bytes=128)["upload_file"]
    with pytest.raises(AIToolArgsInvalidError, match="refusing to upload"):
        tool.handler(local_path="big.bin", remote_path="big.bin")
    assert storage.uploads == []


def test_upload_rejects_extra_args(tmp_path: Path) -> None:
    """Unknown keyword args are rejected by the Pydantic ``extra='forbid'`` rule."""
    (tmp_path / "doc.txt").write_text("hi")
    storage = FakeStorage()
    tool = _tools_by_name(storage, tmp_path)["upload_file"]
    with pytest.raises(AIToolArgsInvalidError):
        tool.handler(
            local_path="doc.txt",
            remote_path="doc.txt",
            container="other-bucket",  # attempted override — must fail
        )


# --- download guardrails ---------------------------------------------------


def test_download_writes_into_safe_root(tmp_path: Path) -> None:
    """Download target is resolved under ``safe_root`` and parent dirs are created."""
    storage = FakeStorage()
    tool = _tools_by_name(storage, tmp_path)["download_file"]
    result = tool.handler(remote_path="k", save_as="out/dl.txt")

    expected_path = (tmp_path / "out" / "dl.txt").resolve()
    assert result["saved_to"] == str(expected_path)
    assert result["size_bytes"] == len(b"hello")
    assert storage.downloads == [
        {"container": CONTAINER, "object_name": "k", "file_name": str(expected_path)}
    ]


def test_download_rejects_path_escape(tmp_path: Path) -> None:
    """A ``save_as`` pointing outside ``safe_root`` is rejected."""
    safe_root = tmp_path / "safe"
    safe_root.mkdir()
    storage = FakeStorage()
    tool = _tools_by_name(storage, safe_root)["download_file"]
    with pytest.raises(AIToolArgsInvalidError, match="escapes safe_root"):
        tool.handler(remote_path="k", save_as="../escape.txt")
    assert storage.downloads == []


# --- list ------------------------------------------------------------------


def test_list_summarizes_and_truncates(tmp_path: Path) -> None:
    """``list_files`` returns counts + a truncated entries slice."""
    many = [_FakeObjectInfo(object_name=f"k-{i}", size_bytes=i) for i in range(100)]
    storage = FakeStorage(list_return=many)
    tool = _tools_by_name(storage, tmp_path)["list_files"]

    result = tool.handler(prefix="k-")

    assert result["count"] == 100
    assert result["returned"] == 50
    assert result["truncated"] is True
    assert len(result["entries"]) == 50
    assert storage.lists == [{"container": CONTAINER, "prefix": "k-"}]


def test_list_defaults_prefix_to_empty(tmp_path: Path) -> None:
    """Prefix is optional — omitting it lists the container root."""
    storage = FakeStorage(list_return=[])
    tool = _tools_by_name(storage, tmp_path)["list_files"]
    result = tool.handler()
    assert result["count"] == 0
    assert result["truncated"] is False
    assert storage.lists == [{"container": CONTAINER, "prefix": ""}]


# --- delete ----------------------------------------------------------------


def test_delete_requires_confirm_true(tmp_path: Path) -> None:
    """Without ``confirm=true``, deletion is refused before any storage call."""
    storage = FakeStorage()
    tool = _tools_by_name(storage, tmp_path)["delete_file"]
    with pytest.raises(AIToolArgsInvalidError, match="confirm=true"):
        tool.handler(remote_path="k")
    assert storage.deletes == []


def test_delete_with_confirm_forwards_to_storage(tmp_path: Path) -> None:
    """With ``confirm=true``, the storage client is invoked on the pinned bucket."""
    storage = FakeStorage(delete_return={"deleted": True, "version_id": "v9"})
    tool = _tools_by_name(storage, tmp_path)["delete_file"]
    result = tool.handler(remote_path="k", confirm=True)
    assert result == {"deleted": True, "remote_path": "k", "version_id": "v9"}
    assert storage.deletes == [{"container": CONTAINER, "object_name": "k"}]


def test_delete_confirmation_can_be_disabled(tmp_path: Path) -> None:
    """Tests / trusted callers can disable the belt-and-braces confirm check."""
    storage = FakeStorage()
    tool = _tools_by_name(storage, tmp_path, require_delete_confirmation=False)[
        "delete_file"
    ]
    # Without confirm=True this would raise; here it should succeed.
    result = tool.handler(remote_path="k")
    assert result["deleted"] is True


# --- get_file_info ---------------------------------------------------------


def test_get_file_info_returns_summary(tmp_path: Path) -> None:
    """``get_file_info`` normalizes the provider metadata into a plain dict."""
    storage = FakeStorage(
        info_return=_FakeObjectInfo(
            object_name="k", size_bytes=42, version_id="v3", updated_at="2026-04-01"
        )
    )
    tool = _tools_by_name(storage, tmp_path)["get_file_info"]
    result = tool.handler(remote_path="k")
    assert result == {
        "object_name": "k",
        "size_bytes": 42,
        "updated_at": "2026-04-01",
        "version_id": "v3",
    }
