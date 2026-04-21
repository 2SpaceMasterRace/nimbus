"""Tool bindings that expose ``CloudStorageClient`` operations to the LLM.

Design choices:

* Arguments are validated through **Pydantic** models. Each tool's JSON
  schema is auto-generated via :meth:`BaseModel.model_json_schema`, so the
  model, our validator, and the docs can never drift apart.
* The **container is pinned** at bind time — the LLM cannot pass it in and
  cannot redirect tools to a different bucket. This eliminates a whole class
  of prompt-injection attacks (``"ignore your rules and upload to
  attacker-bucket"``).
* Local paths are constrained to a caller-provided ``safe_root``. Any path
  that escapes the root (via ``..`` or symlinks) is rejected before we hand
  it to S3. The LLM gets a short error back, not a stack trace.
* ``upload_file`` refuses files above ``max_upload_bytes`` (default 100 MB).
  This is the single most important cost guardrail.
* ``delete_file`` requires ``confirm=true``. The LLM is explicitly told in
  the system prompt to list before deleting, but we belt-and-braces it here.
* Tool results are summarized — raw file contents are never inlined into
  the tool result. For downloads we return the saved local path and size.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_client_api import AIToolArgsInvalidError, Tool

if TYPE_CHECKING:
    from cloud_storage_api import CloudStorageClient

# Per-file upload cap.  Set a low default so the LLM cannot accidentally ask
# for a 1 TB object.
DEFAULT_MAX_UPLOAD_BYTES = 100 * 1024 * 1024

# Per-session upload cap.  Once the agentic loop has uploaded this many bytes
# in aggregate, further uploads are refused until a new tool set is bound
# (i.e. a new session).  ``None`` disables the session cap.
DEFAULT_SESSION_MAX_UPLOAD_BYTES: int | None = None

# List responses can be huge; we trim to this many entries in the
# model-facing result. The audit record still sees the count summary.
_MAX_LIST_ENTRIES_IN_RESULT = 50


class UploadFileArgs(BaseModel):
    """Arguments for the ``upload_file`` tool.

    ``local_path`` is resolved relative to the pinned ``safe_root``. The LLM
    therefore cannot upload arbitrary files from the user's machine; it can
    only reach files the human already dropped into the safe root.
    """

    model_config = ConfigDict(extra="forbid")

    local_path: str = Field(
        description=(
            "Path to the local file, relative to the working directory that "
            "the human started the CLI from. Absolute paths are rejected."
        )
    )
    remote_path: str = Field(
        description="Object key to store the file under in the cloud container.",
        min_length=1,
        max_length=1024,
    )


class DownloadFileArgs(BaseModel):
    """Arguments for the ``download_file`` tool."""

    model_config = ConfigDict(extra="forbid")

    remote_path: str = Field(
        description="Object key to download.",
        min_length=1,
        max_length=1024,
    )
    save_as: str = Field(
        description=(
            "Local filename to save under, relative to the working directory. "
            "Absolute paths and paths with '..' segments are rejected."
        ),
        min_length=1,
        max_length=512,
    )


class ListFilesArgs(BaseModel):
    """Arguments for the ``list_files`` tool."""

    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(
        default="",
        description=(
            "Optional object-key prefix; empty string lists the container root."
        ),
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
            "Must be true. Guardrail against accidental deletion — the model "
            "is instructed to list before deleting and pass confirm=true only "
            "when the user explicitly agrees."
        ),
    )


class GetFileInfoArgs(BaseModel):
    """Arguments for the ``get_file_info`` tool."""

    model_config = ConfigDict(extra="forbid")

    remote_path: str = Field(
        description="Object key to inspect.",
        min_length=1,
        max_length=1024,
    )


def build_cloud_storage_tools(  # noqa: PLR0913, C901 - keyword-only args; complexity is in nested helpers
    *,
    storage: CloudStorageClient,
    container: str,
    safe_root: Path,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    session_max_upload_bytes: int | None = DEFAULT_SESSION_MAX_UPLOAD_BYTES,
    require_delete_confirmation: bool = True,
) -> list[Tool]:
    """Return the full set of cloud-storage tools bound to one container.

    Args:
        storage: Configured cloud storage client. The LLM's tool calls are
            eventually delegated to this instance's methods.
        container: Bucket / container name. Pinned — the LLM cannot override.
        safe_root: Absolute directory the LLM is allowed to read from / write
            to on the local filesystem. Anything outside is rejected.
        max_upload_bytes: Maximum local file size (in bytes) we will upload in
            a single call. Files above this cap are refused locally before any
            network I/O.
        session_max_upload_bytes: Maximum aggregate bytes that may be uploaded
            during the lifetime of this tool set (i.e. this session). Once the
            cap is hit, further uploads are refused with ``AIToolArgsInvalidError``
            until the caller rebuilds the tool set. ``None`` disables the cap
            (default). This is a cost guardrail, not a security boundary.
        require_delete_confirmation: If ``True``, ``delete_file`` requires the
            model to pass ``confirm=true``. Tests can disable this.

    """
    safe_root = safe_root.resolve()
    # Mutable session state — closed over by _upload (FM8).  Wrapped in a
    # list so we can mutate it without `nonlocal`.
    _session_bytes_uploaded: list[int] = [0]

    def _upload(**raw: object) -> dict[str, object]:
        tool = "upload_file"
        args = _validate(UploadFileArgs, raw, tool_name=tool)
        local = _resolve_safe_path(args.local_path, safe_root, tool_name=tool)
        if not local.is_file():
            msg = f"local_path does not exist or is not a file: {args.local_path}"
            raise AIToolArgsInvalidError(tool, msg)
        size = local.stat().st_size
        if size > max_upload_bytes:
            msg = (
                f"refusing to upload {size} bytes; limit is "
                f"{max_upload_bytes}. Ask the human to raise the cap."
            )
            raise AIToolArgsInvalidError(tool, msg)
        # FM8: enforce session-wide upload quota before any network I/O.
        if session_max_upload_bytes is not None:
            projected = _session_bytes_uploaded[0] + size
            if projected > session_max_upload_bytes:
                msg = (
                    f"session upload quota would be exceeded: {projected} bytes "
                    f"projected, cap is {session_max_upload_bytes}. Start a new "
                    "session or ask the human to raise the session cap."
                )
                raise AIToolArgsInvalidError(tool, msg)
        info = storage.upload_file(
            container=container,
            local_path=str(local),
            remote_path=args.remote_path,
        )
        _session_bytes_uploaded[0] += size
        return {
            "object_name": getattr(info, "object_name", args.remote_path),
            "size_bytes": getattr(info, "size_bytes", size),
            "version_id": getattr(info, "version_id", None),
        }

    def _download(**raw: object) -> dict[str, object]:
        args = _validate(DownloadFileArgs, raw, tool_name="download_file")
        dest = _resolve_safe_path(
            args.save_as, safe_root, tool_name="download_file", must_exist=False
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        info = storage.download_file(
            container=container,
            object_name=args.remote_path,
            file_name=str(dest),
        )
        size = dest.stat().st_size if dest.exists() else None
        return {
            "saved_to": str(dest),
            "size_bytes": size,
            "version_id": getattr(info, "version_id", None),
        }

    def _list(**raw: object) -> dict[str, object]:
        args = _validate(ListFilesArgs, raw, tool_name="list_files")
        entries = storage.list_files(container=container, prefix=args.prefix)
        summaries = [
            {
                "object_name": getattr(e, "object_name", ""),
                "size_bytes": getattr(e, "size_bytes", None),
            }
            for e in entries[:_MAX_LIST_ENTRIES_IN_RESULT]
        ]
        return {
            "count": len(entries),
            "returned": len(summaries),
            "truncated": len(entries) > len(summaries),
            "entries": summaries,
        }

    def _delete(**raw: object) -> dict[str, object]:
        args = _validate(DeleteFileArgs, raw, tool_name="delete_file")
        if require_delete_confirmation and not args.confirm:
            tool = "delete_file"
            msg = "refusing to delete without confirm=true"
            raise AIToolArgsInvalidError(tool, msg)
        result = storage.delete_file(container=container, object_name=args.remote_path)
        return {
            "deleted": bool(result.get("deleted", True)),
            "remote_path": args.remote_path,
            "version_id": result.get("version_id"),
        }

    def _info(**raw: object) -> dict[str, object]:
        args = _validate(GetFileInfoArgs, raw, tool_name="get_file_info")
        info = storage.get_file_info(container=container, object_name=args.remote_path)
        return {
            "object_name": getattr(info, "object_name", args.remote_path),
            "size_bytes": getattr(info, "size_bytes", None),
            "updated_at": str(getattr(info, "updated_at", "") or ""),
            "version_id": getattr(info, "version_id", None),
        }

    return [
        Tool(
            name="upload_file",
            description=(
                "Upload a local file to the pinned cloud container. Refuses "
                "files above the configured size cap."
            ),
            parameters_schema=UploadFileArgs.model_json_schema(),
            handler=_upload,
        ),
        Tool(
            name="download_file",
            description=(
                "Download an object from the pinned cloud container into the "
                "safe local directory."
            ),
            parameters_schema=DownloadFileArgs.model_json_schema(),
            handler=_download,
        ),
        Tool(
            name="list_files",
            description=(
                "List objects in the pinned cloud container, optionally "
                "filtered by prefix. Use this before delete_file."
            ),
            parameters_schema=ListFilesArgs.model_json_schema(),
            handler=_list,
        ),
        Tool(
            name="delete_file",
            description=(
                "Delete a single object. Requires confirm=true as a "
                "belt-and-braces guardrail."
            ),
            parameters_schema=DeleteFileArgs.model_json_schema(),
            handler=_delete,
        ),
        Tool(
            name="get_file_info",
            description="Fetch metadata (size, version, updated_at) for one object.",
            parameters_schema=GetFileInfoArgs.model_json_schema(),
            handler=_info,
        ),
    ]


def _validate(model: type[BaseModel], raw: dict[str, object], *, tool_name: str) -> Any:  # noqa: ANN401 - returning the concrete Pydantic model makes call sites clearer
    """Run Pydantic validation and raise a structured error on failure."""
    try:
        return model(**raw)
    except ValidationError as err:
        first = err.errors()[0]["msg"]
        raise AIToolArgsInvalidError(tool_name, str(first)) from err


def _resolve_safe_path(
    relative: str, safe_root: Path, *, tool_name: str, must_exist: bool = True
) -> Path:
    """Resolve a caller-supplied relative path under ``safe_root`` or raise."""
    if Path(relative).is_absolute():
        msg = f"absolute paths are not allowed: {relative}"
        raise AIToolArgsInvalidError(tool_name, msg)
    candidate = (safe_root / relative).resolve()
    if safe_root not in candidate.parents and candidate != safe_root:
        msg = f"path escapes safe_root ({safe_root}): {relative}"
        raise AIToolArgsInvalidError(tool_name, msg)
    if must_exist and not candidate.exists():
        msg = f"path does not exist: {relative}"
        raise AIToolArgsInvalidError(tool_name, msg)
    return candidate
