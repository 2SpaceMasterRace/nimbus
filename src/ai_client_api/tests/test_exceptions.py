"""Tests for the exception hierarchy.

These cover the constructors that stash ``tool_name`` and format messages so
downstream error handlers can attribute failures correctly.
"""

from __future__ import annotations

import pytest

from ai_client_api import (
    AIClientError,
    AIToolArgsInvalidError,
    AIToolExecutionError,
    AIUnknownToolError,
)

pytestmark = pytest.mark.unit


def test_tool_execution_error_records_name() -> None:
    """``AIToolExecutionError`` preserves the tool name on the instance."""
    err = AIToolExecutionError("upload_file", "boom")
    assert err.tool_name == "upload_file"
    assert "upload_file" in str(err)
    assert "boom" in str(err)
    assert isinstance(err, AIClientError)


def test_tool_args_invalid_error_records_name() -> None:
    """``AIToolArgsInvalidError`` preserves the tool name on the instance."""
    err = AIToolArgsInvalidError("download_file", "missing remote_path")
    assert err.tool_name == "download_file"
    assert "download_file" in str(err)
    assert isinstance(err, AIClientError)


def test_unknown_tool_error_records_name() -> None:
    """``AIUnknownToolError`` preserves the tool name on the instance."""
    err = AIUnknownToolError("rm_rf")
    assert err.tool_name == "rm_rf"
    assert "rm_rf" in str(err)
    assert isinstance(err, AIClientError)
