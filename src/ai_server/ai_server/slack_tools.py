"""Scaffolded cloud-storage tool bindings for the Slack frontend.

All tool handlers currently raise ``NotImplementedError``.  The scaffold
is complete — schemas, descriptions, and argument validation are in place
so the middleman API team can see the exact contract and the server can
wire in a real ``CloudStorageClient`` without any API or schema changes.

How to complete the implementation
-----------------------------------
1. Accept a ``CloudStorageClient`` parameter in ``build_slack_tools``.
2. Replace each ``raise NotImplementedError`` with real logic.  The
   battle-tested reference implementations live in
   ``openrouter_ai_client_impl.cloud_storage_tools``.
3. In ``router.py``, switch the ``tools=None`` line to::

       tools = build_slack_tools(storage=get_storage_client())

Intentionally omitted tools
-----------------------------
``upload_file`` and ``download_file`` are excluded because they require
local filesystem paths that do not exist in a Slack interaction.  A future
``get_download_url`` tool (returns a presigned S3 URL) is the right shape
for downloading from Slack; add it here when the S3 presigned-URL flow is
ready.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_client_api import AIToolArgsInvalidError, Tool

# ---------------------------------------------------------------------------
# Argument schemas — these are the LLM's view of each tool's parameters.
# ---------------------------------------------------------------------------


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
            "Must be true.  The model must call list_files first and only "
            "pass confirm=true after the user has explicitly agreed to delete."
        ),
    )


# ---------------------------------------------------------------------------
# Stub handlers — replace with real storage calls when wiring up.
# ---------------------------------------------------------------------------


def _list_files(**_raw: object) -> dict[str, object]:
    msg = "list_files: CloudStorageClient not yet wired up"
    raise NotImplementedError(msg)


def _get_file_info(**_raw: object) -> dict[str, object]:
    msg = "get_file_info: CloudStorageClient not yet wired up"
    raise NotImplementedError(msg)


def _delete_file(**raw: object) -> dict[str, object]:
    """Validate confirm=true before delegating to storage (even in stub form)."""
    tool = "delete_file"
    try:
        # model_validate accepts Any; avoids mypy complaint about
        # unpacking dict[str, object] into typed keyword arguments.
        args = DeleteFileArgs.model_validate(dict(raw))
    except ValidationError as exc:
        first = exc.errors()[0]["msg"]
        raise AIToolArgsInvalidError(tool, str(first)) from exc
    if not args.confirm:
        msg = "refusing to delete without confirm=true"
        raise AIToolArgsInvalidError(tool, msg)
    msg = "delete_file: CloudStorageClient not yet wired up"
    raise NotImplementedError(msg)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def build_slack_tools() -> list[Tool]:
    """Return the Slack tool set.

    All handlers raise ``NotImplementedError`` until a real
    ``CloudStorageClient`` is injected.  The function signature will gain a
    ``storage: CloudStorageClient`` parameter when that wiring is done.
    """
    return [
        Tool(
            name="list_files",
            description=(
                "List objects in the cloud storage bucket, optionally filtered "
                "by a key prefix.  Always call this before delete_file."
            ),
            parameters_schema=ListFilesArgs.model_json_schema(),
            handler=_list_files,
        ),
        Tool(
            name="get_file_info",
            description=(
                "Fetch metadata (size, version, last-modified) for a single "
                "stored object."
            ),
            parameters_schema=GetFileInfoArgs.model_json_schema(),
            handler=_get_file_info,
        ),
        Tool(
            name="delete_file",
            description=(
                "Delete a single object from the bucket.  Requires confirm=true "
                "as a guardrail — always list_files first."
            ),
            parameters_schema=DeleteFileArgs.model_json_schema(),
            handler=_delete_file,
        ),
    ]
