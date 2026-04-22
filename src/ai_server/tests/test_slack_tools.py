"""Tests for the wrapper-facing chat-safe storage tool bindings."""

from __future__ import annotations

import pytest
from ai_server.slack_tools import (
    DeleteFileArgs,
    GetFileInfoArgs,
    ListFilesArgs,
    build_slack_tools,
)
from pydantic import ValidationError

from ai_client_api import AIToolArgsInvalidError, Tool
from tests.conftest import FakeDeleteResult, FakeObjectInfo, FakeStorageClient

pytestmark = pytest.mark.unit

_TEST_CONTAINER = "test-wrapper-bucket"


def _tools_by_name(
    storage: FakeStorageClient, *, include_delete_tool: bool = False
) -> dict[str, Tool]:
    tools = build_slack_tools(
        storage=storage,  # type: ignore[arg-type]
        container=_TEST_CONTAINER,
        include_delete_tool=include_delete_tool,
    )
    return {tool.name: tool for tool in tools}


class TestToolRegistry:
    """The wrapper route should default to a read-only tool surface."""

    def test_returns_two_read_only_tools_by_default(self) -> None:
        names = set(_tools_by_name(FakeStorageClient()))
        assert names == {"list_files", "get_file_info"}

    def test_can_include_delete_tool_explicitly(self) -> None:
        names = set(_tools_by_name(FakeStorageClient(), include_delete_tool=True))
        assert names == {"list_files", "get_file_info", "delete_file"}

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
            list_return=[
                FakeObjectInfo(object_name="reports/jan.csv", size_bytes=10),
                FakeObjectInfo(object_name="reports/feb.csv", size_bytes=20),
            ]
        )
        tool = _tools_by_name(storage)["list_files"]

        result = tool.handler(prefix="reports/")

        assert result == {
            "count": 2,
            "returned": 2,
            "truncated": False,
            "entries": [
                {"object_name": "reports/jan.csv", "size_bytes": 10},
                {"object_name": "reports/feb.csv", "size_bytes": 20},
            ],
        }
        assert storage.lists == [{"container": _TEST_CONTAINER, "prefix": "reports/"}]

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
