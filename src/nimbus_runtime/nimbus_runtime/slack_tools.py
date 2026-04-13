"""Chat-safe cloud-storage tool bindings for wrapper-facing Nimbus turns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_client_api import AIToolArgsInvalidError, Tool

if TYPE_CHECKING:
    from cloud_storage_api import CloudStorageClient

_MAX_LIST_ENTRIES_IN_RESULT = 50


class ListFilesArgs(BaseModel):
    """Arguments for the ``list_files`` tool."""

    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(
        default="",
        description=("Optional object-key prefix; empty string lists the bucket root."),
        max_length=1024,
    )


class GetFileInfoArgs(BaseModel):
    """Arguments for the ``get_file_info`` tool."""

    model_config = ConfigDict(extra="forbid")

    remote_path: str = Field(
        description="Object key to inspect.",
        min_length=1,
        max_length=1024,
    )


class DeleteFileArgs(BaseModel):
    """Arguments for the ``delete_file`` tool."""

    model_config = ConfigDict(extra="forbid")

    remote_path: str = Field(
        description="Exact object key to delete.",
        min_length=1,
        max_length=1024,
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Must be true. The model must call list_files first and only pass "
            "confirm=true after the user has explicitly agreed to delete."
        ),
    )


def _validate(model: type[BaseModel], raw: dict[str, object], *, tool_name: str) -> Any:  # noqa: ANN401 - returning the concrete validated Pydantic model keeps call sites simple
    """Validate tool arguments and raise a structured AI-tool error."""
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]["msg"]
        raise AIToolArgsInvalidError(tool_name, str(first)) from exc


def build_slack_tools(
    *,
    storage: CloudStorageClient,
    container: str,
    include_delete_tool: bool = False,
) -> list[Tool]:
    """Return the wrapper-facing chat tool set bound to one storage container."""

    def _list_files(**raw: object) -> dict[str, object]:
        args = _validate(ListFilesArgs, dict(raw), tool_name="list_files")
        entries = storage.list_files(container=container, prefix=args.prefix)
        summaries = [
            {
                "object_name": getattr(entry, "object_name", ""),
                "size_bytes": getattr(entry, "size_bytes", None),
            }
            for entry in entries[:_MAX_LIST_ENTRIES_IN_RESULT]
        ]
        return {
            "count": len(entries),
            "returned": len(summaries),
            "truncated": len(entries) > len(summaries),
            "entries": summaries,
        }

    def _get_file_info(**raw: object) -> dict[str, object]:
        args = _validate(GetFileInfoArgs, dict(raw), tool_name="get_file_info")
        info = storage.get_file_info(container=container, object_name=args.remote_path)
        return {
            "object_name": getattr(info, "object_name", args.remote_path),
            "size_bytes": getattr(info, "size_bytes", None),
            "updated_at": str(getattr(info, "updated_at", "") or ""),
            "version_id": getattr(info, "version_id", None),
        }

    def _delete_file(**raw: object) -> dict[str, object]:
        tool_name = "delete_file"
        args = _validate(DeleteFileArgs, dict(raw), tool_name=tool_name)
        if not args.confirm:
            msg = "refusing to delete without confirm=true"
            raise AIToolArgsInvalidError(tool_name, msg)
        result = storage.delete_file(container=container, object_name=args.remote_path)
        return {
            "deleted": bool(getattr(result, "deleted", True)),
            "remote_path": args.remote_path,
            "version_id": getattr(result, "version_id", None),
        }

    tools = [
        Tool(
            name="list_files",
            description=(
                "List objects in the pinned cloud-storage container, optionally "
                "filtered by a key prefix."
            ),
            parameters_schema=ListFilesArgs.model_json_schema(),
            handler=_list_files,
        ),
        Tool(
            name="get_file_info",
            description=(
                "Fetch metadata such as size, version, and last-modified time "
                "for a single stored object."
            ),
            parameters_schema=GetFileInfoArgs.model_json_schema(),
            handler=_get_file_info,
        ),
    ]
    if include_delete_tool:
        tools.append(
            Tool(
                name="delete_file",
                description=(
                    "Delete a single object from the pinned container. "
                    "Requires confirm=true and should only be exposed behind an "
                    "explicit confirmation flow."
                ),
                parameters_schema=DeleteFileArgs.model_json_schema(),
                handler=_delete_file,
            )
        )
    return tools
