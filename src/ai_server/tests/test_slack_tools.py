"""Contract tests for the Slack tool schemas and delete guardrail.

These tests verify the *API contract* that the LLM and the middleman team
depend on — parameter shapes, required vs optional fields, and the safety
check on delete_file.  They do not test implementation details like whether
a particular error message string matches.
"""

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

# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------


class TestToolRegistry:
    """The set of tools exposed to the LLM must be stable."""

    def test_returns_three_tools(self) -> None:
        assert len(build_slack_tools()) == 3

    def test_all_are_tool_instances(self) -> None:
        for t in build_slack_tools():
            assert isinstance(t, Tool)

    def test_tool_names_are_exactly(self) -> None:
        names = {t.name for t in build_slack_tools()}
        assert names == {"list_files", "get_file_info", "delete_file"}

    def test_each_tool_has_non_empty_description(self) -> None:
        for t in build_slack_tools():
            assert t.description
            assert len(t.description) > 10

    def test_each_tool_has_json_schema(self) -> None:
        for t in build_slack_tools():
            schema = t.parameters_schema
            assert isinstance(schema, dict)
            assert "properties" in schema


# ---------------------------------------------------------------------------
# ListFilesArgs schema contract
# ---------------------------------------------------------------------------


class TestListFilesSchema:
    def test_prefix_is_optional(self) -> None:
        args = ListFilesArgs()
        assert args.prefix == ""

    def test_prefix_defaults_to_empty_string(self) -> None:
        assert ListFilesArgs.model_fields["prefix"].default == ""

    def test_prefix_accepts_valid_path(self) -> None:
        args = ListFilesArgs(prefix="reports/2024/")
        assert args.prefix == "reports/2024/"

    def test_prefix_accepts_empty_string(self) -> None:
        ListFilesArgs(prefix="")

    def test_prefix_rejects_above_max_length(self) -> None:
        with pytest.raises(ValidationError):
            ListFilesArgs(prefix="x" * 1025)

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            ListFilesArgs(unknown_field="oops")

    def test_schema_has_prefix_property(self) -> None:
        schema = ListFilesArgs.model_json_schema()
        assert "prefix" in schema["properties"]


# ---------------------------------------------------------------------------
# GetFileInfoArgs schema contract
# ---------------------------------------------------------------------------


class TestGetFileInfoSchema:
    def test_remote_path_is_required(self) -> None:
        with pytest.raises(ValidationError):
            GetFileInfoArgs()  # type: ignore[call-arg]

    def test_remote_path_min_length_one(self) -> None:
        with pytest.raises(ValidationError):
            GetFileInfoArgs(remote_path="")

    def test_remote_path_accepts_valid_key(self) -> None:
        args = GetFileInfoArgs(remote_path="reports/q1.csv")
        assert args.remote_path == "reports/q1.csv"

    def test_remote_path_rejects_above_max_length(self) -> None:
        with pytest.raises(ValidationError):
            GetFileInfoArgs(remote_path="x" * 1025)

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            GetFileInfoArgs(remote_path="a.txt", extra="nope")

    def test_schema_marks_remote_path_as_required(self) -> None:
        schema = GetFileInfoArgs.model_json_schema()
        assert "remote_path" in schema.get("required", [])


# ---------------------------------------------------------------------------
# DeleteFileArgs schema contract + safety guardrail
# ---------------------------------------------------------------------------


class TestDeleteFileSchema:
    def test_remote_path_is_required(self) -> None:
        with pytest.raises(ValidationError):
            DeleteFileArgs()  # type: ignore[call-arg]

    def test_confirm_defaults_to_false(self) -> None:
        args = DeleteFileArgs(remote_path="old.csv")
        assert args.confirm is False

    def test_confirm_accepts_true(self) -> None:
        args = DeleteFileArgs(remote_path="old.csv", confirm=True)
        assert args.confirm is True

    def test_extra_fields_are_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            DeleteFileArgs(remote_path="a.csv", confirm=True, extra="bad")

    def test_schema_marks_remote_path_as_required(self) -> None:
        schema = DeleteFileArgs.model_json_schema()
        assert "remote_path" in schema.get("required", [])


class TestHandlerContractBeforeWiring:
    """Handlers raise ``NotImplementedError`` until storage is wired in.

    They must not crash with AttributeError or silently return None.
    """

    def test_list_files_raises_not_implemented(self) -> None:
        tool = next(t for t in build_slack_tools() if t.name == "list_files")
        with pytest.raises(NotImplementedError):
            tool.handler(prefix="reports/")

    def test_get_file_info_raises_not_implemented(self) -> None:
        tool = next(t for t in build_slack_tools() if t.name == "get_file_info")
        with pytest.raises(NotImplementedError):
            tool.handler(remote_path="reports/q1.csv")


class TestDeleteFileGuardrail:
    """The delete handler must refuse unless ``confirm=True``.

    This guardrail applies even before the real storage client is wired in.
    """

    @pytest.fixture
    def delete_tool(self) -> Tool:
        return next(t for t in build_slack_tools() if t.name == "delete_file")

    def test_rejects_when_confirm_false(self, delete_tool: Tool) -> None:
        with pytest.raises(AIToolArgsInvalidError):
            delete_tool.handler(remote_path="old.csv", confirm=False)

    def test_rejects_when_confirm_omitted(self, delete_tool: Tool) -> None:
        # confirm defaults to False — must still be rejected
        with pytest.raises(AIToolArgsInvalidError):
            delete_tool.handler(remote_path="old.csv")

    def test_rejects_empty_remote_path_even_when_confirmed(
        self, delete_tool: Tool
    ) -> None:
        with pytest.raises(AIToolArgsInvalidError):
            delete_tool.handler(remote_path="", confirm=True)

    def test_error_mentions_confirm_when_omitted(self, delete_tool: Tool) -> None:
        with pytest.raises(AIToolArgsInvalidError, match="confirm"):
            delete_tool.handler(remote_path="a.csv", confirm=False)

    def test_proceeds_past_guard_when_confirmed(self, delete_tool: Tool) -> None:
        # Storage is not yet wired up; NotImplementedError means we passed the guard.
        with pytest.raises(NotImplementedError):
            delete_tool.handler(remote_path="a.csv", confirm=True)
