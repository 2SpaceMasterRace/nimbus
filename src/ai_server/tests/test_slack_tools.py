"""Tests for the wrapper-facing chat-safe storage tool bindings."""

from __future__ import annotations

import pytest
from ai_server.fakes import FakeDeleteResult, FakeObjectInfo, FakeStorageClient
from ai_server.slack_tools import (
    DeleteFileArgs,
    GetFileInfoArgs,
    ListFilesArgs,
    build_slack_tools,
)
from pydantic import ValidationError

from ai_client_api import AIToolArgsInvalidError, Tool

pytestmark = pytest.mark.unit

_TEST_CONTAINER = "test-wrapper-bucket"


class _MappingDeleteStorage:
    """Storage double whose delete result matches cloud_storage_api at runtime."""

    def __init__(self) -> None:
        self.deletes: list[dict[str, str]] = []

    def list_files(self, *, container: str, prefix: str = "") -> list[FakeObjectInfo]:
        """Return no objects; only present to satisfy the tool builder."""
        del container, prefix
        return []

    def get_file_info(self, *, container: str, object_name: str) -> FakeObjectInfo:
        """Return basic metadata; only present to satisfy the tool builder."""
        del container
        return FakeObjectInfo(object_name=object_name)

    def delete_file(self, *, container: str, object_name: str) -> dict[str, object]:
        """Return a dict-backed DeleteResult, like cloud_storage_api."""
        self.deletes.append({"container": container, "object_name": object_name})
        return {"deleted": False, "version_id": "v-delete-marker"}


def _tools_by_name(
    storage: FakeStorageClient, *, include_delete_tool: bool = False
) -> dict[str, Tool]:
    tools = build_slack_tools(
        storage=storage,  # type: ignore[arg-type]
        container=_TEST_CONTAINER,
        include_delete_tool=include_delete_tool,
    )
    return {tool.name: tool for tool in tools}


def _proposal_tools_by_name(storage: FakeStorageClient) -> dict[str, Tool]:
    tools = build_slack_tools(
        storage=storage,  # type: ignore[arg-type]
        container=_TEST_CONTAINER,
        include_delete_tool=True,
        mutation_proposal_handler=lambda operation, args: {
            "operation": operation,
            "status": "requires_runtime_action",
            **dict(args),
        },
    )
    return {tool.name: tool for tool in tools}


class TestToolRegistry:
    """The wrapper route should default to a read-only tool surface."""

    def test_returns_read_only_tools_by_default(self) -> None:
        names = set(_tools_by_name(FakeStorageClient()))
        assert names == {"list_files", "get_file_info", "read_file"}

    def test_can_include_delete_tool_explicitly(self) -> None:
        names = set(_tools_by_name(FakeStorageClient(), include_delete_tool=True))
        assert names == {
            "list_files",
            "get_file_info",
            "read_file",
            "delete_file",
            "copy_file",
            "move_file",
            "write_file",
        }

    def test_all_are_tool_instances(self) -> None:
        for tool in _tools_by_name(FakeStorageClient()).values():
            assert isinstance(tool, Tool)

    def test_each_tool_has_non_empty_description(self) -> None:
        for tool in _tools_by_name(FakeStorageClient()).values():
            assert tool.description
            assert len(tool.description) > 10

    def test_each_tool_has_json_schema(self) -> None:
        for tool in _tools_by_name(
            FakeStorageClient(), include_delete_tool=True
        ).values():
            assert "properties" in tool.parameters_schema


class TestListFilesSchema:
    def test_prefix_is_optional(self) -> None:
        assert ListFilesArgs().prefix == ""

    def test_prefix_accepts_valid_path(self) -> None:
        assert ListFilesArgs(prefix="reports/2024/").prefix == "reports/2024/"

    def test_prefix_rejects_above_max_length(self) -> None:
        with pytest.raises(ValidationError):
            ListFilesArgs(prefix="x" * 1025)

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ListFilesArgs(unknown_field="oops")


class TestGetFileInfoSchema:
    def test_remote_path_is_required(self) -> None:
        with pytest.raises(ValidationError):
            GetFileInfoArgs()  # type: ignore[call-arg]

    def test_remote_path_min_length_one(self) -> None:
        with pytest.raises(ValidationError):
            GetFileInfoArgs(remote_path="")

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GetFileInfoArgs(remote_path="a.txt", extra="nope")


class TestDeleteFileSchema:
    def test_remote_path_is_required(self) -> None:
        with pytest.raises(ValidationError):
            DeleteFileArgs()  # type: ignore[call-arg]

    def test_confirm_defaults_to_false(self) -> None:
        assert DeleteFileArgs(remote_path="old.csv").confirm is False

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            DeleteFileArgs(remote_path="a.csv", confirm=True, extra="bad")


class TestReadOnlyHandlers:
    def test_list_files_forwards_to_storage_with_pinned_container(self) -> None:
        storage = FakeStorageClient(
            list_page_return=(
                [
                    FakeObjectInfo(object_name="reports/jan.csv", size_bytes=10),
                    FakeObjectInfo(object_name="reports/feb.csv", size_bytes=20),
                ],
                "",
            )
        )
        tool = _tools_by_name(storage)["list_files"]

        result = tool.handler(prefix="reports/")

        assert result == {
            "returned": 2,
            "has_more": False,
            "next_token": "",
            "entries": [
                {"object_name": "reports/jan.csv", "size_bytes": 10, "size": "10 B"},
                {"object_name": "reports/feb.csv", "size_bytes": 20, "size": "20 B"},
            ],
        }
        assert storage.lists == [
            {
                "container": _TEST_CONTAINER,
                "prefix": "reports/",
                "max_keys": 50,
                "continuation_token": "",
            }
        ]

    def test_list_files_includes_human_readable_sizes(self) -> None:
        storage = FakeStorageClient(
            list_page_return=(
                [FakeObjectInfo(object_name="video.mov", size_bytes=5_500_000)],
                "",
            )
        )
        tool = _tools_by_name(storage)["list_files"]

        result = tool.handler(prefix="")

        assert result["entries"] == [
            {
                "object_name": "video.mov",
                "size_bytes": 5_500_000,
                "size": "5.2 MB",
            }
        ]

    def test_get_file_info_forwards_to_storage_with_pinned_container(self) -> None:
        storage = FakeStorageClient(
            info_return=FakeObjectInfo(
                object_name="reports/q1.csv",
                size_bytes=42,
                version_id="v7",
                updated_at="2026-04-21T10:00:00Z",
            )
        )
        tool = _tools_by_name(storage)["get_file_info"]

        result = tool.handler(remote_path="reports/q1.csv")

        assert result == {
            "object_name": "reports/q1.csv",
            "size_bytes": 42,
            "size": "42 B",
            "updated_at": "2026-04-21T10:00:00Z",
            "version_id": "v7",
        }
        assert storage.infos == [
            {"container": _TEST_CONTAINER, "object_name": "reports/q1.csv"}
        ]


class TestDeleteGuardrail:
    @pytest.fixture
    def delete_tool(self) -> Tool:
        return _tools_by_name(FakeStorageClient(), include_delete_tool=True)[
            "delete_file"
        ]

    def test_rejects_when_confirm_false(self, delete_tool: Tool) -> None:
        with pytest.raises(AIToolArgsInvalidError, match="confirm=true"):
            delete_tool.handler(remote_path="old.csv", confirm=False)

    def test_rejects_when_confirm_omitted(self, delete_tool: Tool) -> None:
        with pytest.raises(AIToolArgsInvalidError, match="confirm=true"):
            delete_tool.handler(remote_path="old.csv")

    def test_forwards_delete_when_confirmed(self) -> None:
        storage = FakeStorageClient(delete_return=FakeDeleteResult(version_id="v9"))
        delete_tool = _tools_by_name(storage, include_delete_tool=True)["delete_file"]

        result = delete_tool.handler(remote_path="old.csv", confirm=True)

        assert result == {
            "deleted": True,
            "remote_path": "old.csv",
            "version_id": "v9",
        }
        assert storage.deletes == [
            {"container": _TEST_CONTAINER, "object_name": "old.csv"}
        ]

    def test_preserves_dict_backed_delete_result_fields(self) -> None:
        storage = _MappingDeleteStorage()
        tools = build_slack_tools(
            storage=storage,  # type: ignore[arg-type]
            container=_TEST_CONTAINER,
            include_delete_tool=True,
        )
        delete_tool = next(tool for tool in tools if tool.name == "delete_file")

        result = delete_tool.handler(remote_path="old.csv", confirm=True)

        assert result == {
            "deleted": False,
            "remote_path": "old.csv",
            "version_id": "v-delete-marker",
        }
        assert storage.deletes == [
            {"container": _TEST_CONTAINER, "object_name": "old.csv"}
        ]

    def test_confirmed_delete_is_proposal_only_with_runtime_handler(self) -> None:
        storage = FakeStorageClient(delete_return=FakeDeleteResult(version_id="v9"))
        delete_tool = _proposal_tools_by_name(storage)["delete_file"]

        result = delete_tool.handler(remote_path="old.csv", confirm=True)

        assert result["proposal_required"] is True
        assert result["operation"] == "delete_file"
        assert result["remote_path"] == "old.csv"
        assert storage.deletes == []


class TestCopyAndMoveHandlers:
    """Tests for the copy_file / move_file CRUD tools exposed alongside delete."""

    def _tools(self, storage: FakeStorageClient) -> dict[str, Tool]:
        return _tools_by_name(storage, include_delete_tool=True)

    def test_copy_file_refuses_when_destination_exists(self) -> None:
        storage = FakeStorageClient()  # default: get_file_info always returns
        copy_tool = self._tools(storage)["copy_file"]

        with pytest.raises(AIToolArgsInvalidError, match="already exists"):
            copy_tool.handler(source_path="a.txt", dest_path="b.txt")

        assert storage.uploads == []
        assert storage.deletes == []

    def test_copy_file_allows_overwrite_when_flagged(self) -> None:
        storage = FakeStorageClient()
        copy_tool = self._tools(storage)["copy_file"]

        result = copy_tool.handler(
            source_path="a.txt", dest_path="b.txt", overwrite=True
        )

        assert result["copied"] is True
        assert result["overwrote"] is True
        # Fast path: server-side copy — no bytes uploaded through this process.
        assert len(storage.copies) == 1
        assert storage.copies[0]["dst_key"] == "b.txt"
        assert storage.copies[0]["src_key"] == "a.txt"

    def test_copy_file_succeeds_for_free_destination(self) -> None:
        storage = FakeStorageClient(missing_objects={"new/dest.txt"})
        copy_tool = self._tools(storage)["copy_file"]

        result = copy_tool.handler(source_path="src.txt", dest_path="new/dest.txt")

        assert result == {
            "copied": True,
            "source_path": "src.txt",
            "dest_path": "new/dest.txt",
            "overwrote": False,
        }

    def test_copy_file_rejects_same_source_and_dest(self) -> None:
        storage = FakeStorageClient()
        copy_tool = self._tools(storage)["copy_file"]

        with pytest.raises(AIToolArgsInvalidError, match="must differ"):
            copy_tool.handler(source_path="same.txt", dest_path="same.txt")

    def test_move_file_requires_confirm(self) -> None:
        storage = FakeStorageClient(missing_objects={"dest.txt"})
        move_tool = self._tools(storage)["move_file"]

        with pytest.raises(AIToolArgsInvalidError, match="confirm=true"):
            move_tool.handler(source_path="src.txt", dest_path="dest.txt")

        assert storage.uploads == []
        assert storage.deletes == []

    def test_move_file_copies_then_deletes_source(self) -> None:
        storage = FakeStorageClient(missing_objects={"dest.txt"})
        move_tool = self._tools(storage)["move_file"]

        result = move_tool.handler(
            source_path="src.txt", dest_path="dest.txt", confirm=True
        )

        assert result["moved"] is True
        assert result["source_path"] == "src.txt"
        assert result["dest_path"] == "dest.txt"
        # Fast path: server-side copy → force_delete (no bytes in memory).
        assert len(storage.copies) == 1
        assert storage.copies[0]["src_key"] == "src.txt"
        assert storage.copies[0]["dst_key"] == "dest.txt"
        assert storage.deletes == [
            {"container": _TEST_CONTAINER, "object_name": "src.txt"}
        ]

    def test_move_file_refuses_overwrite_by_default(self) -> None:
        storage = FakeStorageClient()  # dest exists by default
        move_tool = self._tools(storage)["move_file"]

        with pytest.raises(AIToolArgsInvalidError, match="already exists"):
            move_tool.handler(source_path="a.txt", dest_path="b.txt", confirm=True)

        assert storage.uploads == []
        assert storage.deletes == []

    def test_move_file_is_marked_destructive(self) -> None:
        storage = FakeStorageClient()
        move_tool = self._tools(storage)["move_file"]
        assert move_tool.is_destructive is True

    def test_copy_file_is_not_destructive(self) -> None:
        storage = FakeStorageClient()
        copy_tool = self._tools(storage)["copy_file"]
        assert copy_tool.is_destructive is True

    def test_move_file_is_proposal_only_with_runtime_handler(self) -> None:
        storage = FakeStorageClient(missing_objects={"dest.txt"})
        move_tool = _proposal_tools_by_name(storage)["move_file"]

        result = move_tool.handler(
            source_path="src.txt",
            dest_path="dest.txt",
            confirm=True,
        )

        assert result["proposal_required"] is True
        assert result["operation"] == "move_file"
        assert storage.uploads == []
        assert storage.deletes == []


class TestReadFileHandler:
    """Tests for read_file — included in the always-on read-only tool set."""

    def _tool(self, storage: FakeStorageClient) -> Tool:
        return _tools_by_name(storage)["read_file"]

    def test_returns_utf8_text_when_decodable(self) -> None:
        storage = FakeStorageClient(read_body=b"hello, nimbus")
        result = self._tool(storage).handler(remote_path="notes/hello.txt")

        assert result["encoding"] == "utf-8"
        assert result["content"] == "hello, nimbus"
        assert result["bytes_returned"] == len(b"hello, nimbus")
        assert result["truncated"] is False

    def test_falls_back_to_base64_for_binary(self) -> None:
        storage = FakeStorageClient(read_body=b"\xff\xfe\xfd")
        result = self._tool(storage).handler(remote_path="bin/blob")

        assert result["encoding"] == "base64"
        import base64 as _b64

        assert _b64.b64decode(result["content"]) == b"\xff\xfe\xfd"

    def test_truncates_to_max_bytes(self) -> None:
        # range_total simulates the Content-Range total from S3 so the handler
        # knows the full object size without downloading more than max_bytes.
        storage = FakeStorageClient(read_body=b"x" * 10_000, range_total=10_000)
        result = self._tool(storage).handler(remote_path="big.txt", max_bytes=128)

        assert result["bytes_returned"] == 128
        assert result["total_bytes"] == 10_000
        assert result["truncated"] is True


class TestWriteFileHandler:
    """Tests for write_file — gated alongside the destructive tools."""

    def _tool(self, storage: FakeStorageClient) -> Tool:
        return _tools_by_name(storage, include_delete_tool=True)["write_file"]

    def test_creates_new_object_when_destination_is_free(self) -> None:
        storage = FakeStorageClient(missing_objects={"new/notes.md"})
        result = self._tool(storage).handler(
            remote_path="new/notes.md", content="# hello"
        )

        assert result == {
            "written": True,
            "remote_path": "new/notes.md",
            "bytes_written": len(b"# hello"),
            "encoding": "utf-8",
            "overwrote": False,
        }
        assert len(storage.uploads) == 1
        assert storage.uploads[0]["remote_path"] == "new/notes.md"

    def test_refuses_overwrite_by_default(self) -> None:
        storage = FakeStorageClient()  # default: dest exists
        with pytest.raises(AIToolArgsInvalidError, match="already exists"):
            self._tool(storage).handler(remote_path="existing.txt", content="new body")
        assert storage.uploads == []

    def test_overwrite_requires_confirm(self) -> None:
        storage = FakeStorageClient()  # default: dest exists
        with pytest.raises(AIToolArgsInvalidError, match="confirm=true"):
            self._tool(storage).handler(
                remote_path="existing.txt", content="new body", overwrite=True
            )
        assert storage.uploads == []

    def test_overwrite_with_confirm_replaces(self) -> None:
        storage = FakeStorageClient()  # default: dest exists
        result = self._tool(storage).handler(
            remote_path="existing.txt",
            content="new body",
            overwrite=True,
            confirm=True,
        )
        assert result["overwrote"] is True
        assert len(storage.uploads) == 1

    def test_write_file_is_proposal_only_with_runtime_handler(self) -> None:
        storage = FakeStorageClient()
        result = _proposal_tools_by_name(storage)["write_file"].handler(
            remote_path="existing.txt",
            content="new body",
            overwrite=True,
            confirm=True,
        )

        assert result["proposal_required"] is True
        assert result["operation"] == "write_file"
        assert result["content_bytes"] == len(b"new body")
        assert "content" not in result
        assert storage.uploads == []

    def test_base64_content_is_decoded(self) -> None:
        import base64 as _b64

        storage = FakeStorageClient(missing_objects={"bin/blob"})
        payload = b"\xde\xad\xbe\xef"
        result = self._tool(storage).handler(
            remote_path="bin/blob",
            content=_b64.b64encode(payload).decode("ascii"),
            encoding="base64",
        )
        assert result["bytes_written"] == len(payload)

    def test_rejects_invalid_base64(self) -> None:
        storage = FakeStorageClient(missing_objects={"bin/blob"})
        with pytest.raises(AIToolArgsInvalidError, match="not valid base64"):
            self._tool(storage).handler(
                remote_path="bin/blob",
                content="!!!not base64!!!",
                encoding="base64",
            )

    def test_is_marked_destructive(self) -> None:
        storage = FakeStorageClient()
        assert self._tool(storage).is_destructive is True
