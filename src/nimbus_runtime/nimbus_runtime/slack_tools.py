"""Chat-safe cloud-storage tool bindings for wrapper-facing Nimbus turns."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cloud_storage_api import ObjectNotFoundError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ai_client_api import AIToolArgsInvalidError, Tool
from nimbus_runtime.capabilities import capability_for_ai_tool

if TYPE_CHECKING:
    from cloud_storage_api import CloudStorageClient

MutationProposalHandler = Callable[[str, Mapping[str, object]], Mapping[str, object]]

_MAX_LIST_ENTRIES_IN_RESULT = 50
_BYTES_PER_KIB = 1024
_DISPLAY_SIZE_UNITS = ("KB", "MB", "GB")
_READ_FILE_DEFAULT_BYTES = 65_536  # 64 KB default keeps model context cheap
_READ_FILE_MAX_BYTES = 10 * 1_048_576  # 10 MB ceiling — covers typical media
_WRITE_FILE_MAX_BYTES = 10 * 1_048_576  # 10 MB ceiling on inbound content
_TextOrBytesEncoding = Literal["utf-8", "base64"]


class ListFilesArgs(BaseModel):
    """Arguments for the ``list_files`` tool."""

    model_config = ConfigDict(extra="forbid")

    prefix: str = Field(
        default="",
        description=("Optional object-key prefix; empty string lists the bucket root."),
        max_length=1024,
    )
    continuation_token: str = Field(
        default="",
        description=(
            "Opaque token returned by a previous list_files call when has_more=true. "
            "Pass it to retrieve the next page."
        ),
        max_length=2048,
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


_DEST_KEY_DESC = "Destination object key. Must not already exist unless overwrite=true."
_OVERWRITE_DESC = (
    "If true, replace an existing object at dest_path. Default is to refuse."
)


class CopyFileArgs(BaseModel):
    """Arguments for the ``copy_file`` tool."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(
        description="Existing object key to copy from.",
        min_length=1,
        max_length=1024,
    )
    dest_path: str = Field(
        description=_DEST_KEY_DESC,
        min_length=1,
        max_length=1024,
    )
    overwrite: bool = Field(default=False, description=_OVERWRITE_DESC)


class MoveFileArgs(BaseModel):
    """Arguments for the ``move_file`` tool (copy then delete source)."""

    model_config = ConfigDict(extra="forbid")

    source_path: str = Field(
        description="Existing object key to move from. Deleted on success.",
        min_length=1,
        max_length=1024,
    )
    dest_path: str = Field(
        description=_DEST_KEY_DESC,
        min_length=1,
        max_length=1024,
    )
    overwrite: bool = Field(default=False, description=_OVERWRITE_DESC)
    confirm: bool = Field(
        default=False,
        description=(
            "Must be true. Move is destructive (deletes the source object). "
            "Only pass confirm=true after explicit user approval."
        ),
    )


class ReadFileArgs(BaseModel):
    """Arguments for the ``read_file`` tool."""

    model_config = ConfigDict(extra="forbid")

    remote_path: str = Field(
        description="Exact object key to read.",
        min_length=1,
        max_length=1024,
    )
    max_bytes: int = Field(
        default=_READ_FILE_DEFAULT_BYTES,
        ge=1,
        le=_READ_FILE_MAX_BYTES,
        description=(
            "Maximum number of bytes to return. Default 64 KB, hard ceiling 10 MB. "
            "Larger files are truncated; truncated=true is reported in the response."
        ),
    )
    encoding: _TextOrBytesEncoding = Field(
        default="utf-8",
        description=(
            "How to decode the returned bytes. Use 'utf-8' for text files. "
            "Use 'base64' to read binary content (or to force a binary-safe "
            "transport when the file may not be valid UTF-8)."
        ),
    )


class WriteFileArgs(BaseModel):
    """Arguments for the ``write_file`` tool (create or overwrite)."""

    model_config = ConfigDict(extra="forbid")

    remote_path: str = Field(
        description="Destination object key to write to.",
        min_length=1,
        max_length=1024,
    )
    content: str = Field(
        description=(
            "File body. Treated as UTF-8 text when encoding='utf-8', or as "
            "base64-encoded bytes when encoding='base64'."
        ),
        max_length=_WRITE_FILE_MAX_BYTES * 2,  # base64 expansion + headroom
    )
    encoding: _TextOrBytesEncoding = Field(
        default="utf-8",
        description="How to interpret the 'content' field.",
    )
    overwrite: bool = Field(
        default=False,
        description=(
            "If true, replace an existing object at remote_path. Default refuses."
        ),
    )
    confirm: bool = Field(
        default=False,
        description=(
            "Must be true if overwrite=true (overwriting an existing object "
            "destroys its prior content). Not required when creating a new key."
        ),
    )


def _validate(model: type[BaseModel], raw: dict[str, object], *, tool_name: str) -> Any:  # noqa: ANN401 - returning the concrete validated Pydantic model keeps call sites simple
    """Validate tool arguments and raise a structured AI-tool error."""
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]["msg"]
        raise AIToolArgsInvalidError(tool_name, str(first)) from exc


def _delete_result_value(result: object, key: str) -> object | None:
    """Return a field from either dict-backed or attribute-backed delete results."""
    if isinstance(result, Mapping):
        return result.get(key)
    return getattr(result, key, None)


def _delete_result_deleted(result: object) -> bool:
    """Return the provider-reported delete outcome, defaulting to prior behavior."""
    value = _delete_result_value(result, "deleted")
    return value if isinstance(value, bool) else True


def _delete_result_version_id(result: object) -> str | None:
    """Return the provider version ID from a delete result, if one exists."""
    value = _delete_result_value(result, "version_id")
    return value if isinstance(value, str) else None


def _human_size(size_bytes: object) -> str:
    """Return a compact human-readable size label for model summaries."""
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        return "unknown"
    if size_bytes < _BYTES_PER_KIB:
        return f"{size_bytes} B"
    value = float(size_bytes)
    for unit in _DISPLAY_SIZE_UNITS:
        value /= _BYTES_PER_KIB
        if value < _BYTES_PER_KIB:
            return f"{value:.1f}".rstrip("0").rstrip(".") + f" {unit}"
    value /= _BYTES_PER_KIB
    return f"{value:.1f}".rstrip("0").rstrip(".") + " TB"


def _tool_description(tool_name: str, fallback: str) -> str:
    capability = capability_for_ai_tool(tool_name)
    if capability is None:
        return fallback
    return f"{capability.description} {fallback}"


def build_slack_tools(  # noqa: C901, PLR0915 - tool registry; closures keep storage capture cheap
    *,
    storage: CloudStorageClient,
    container: str,
    include_delete_tool: bool = False,
    mutation_proposal_handler: MutationProposalHandler | None = None,
) -> list[Tool]:
    """Return the wrapper-facing chat tool set bound to one storage container."""

    def _mutation_proposal(
        *,
        tool_name: str,
        args: Mapping[str, object],
    ) -> dict[str, object] | None:
        if mutation_proposal_handler is None:
            return None
        proposal = dict(mutation_proposal_handler(tool_name, args))
        proposal.setdefault("proposal_required", True)
        proposal.setdefault("operation", tool_name)
        proposal.setdefault("status", "requires_runtime_action")
        return proposal

    def _list_files(**raw: object) -> dict[str, object]:
        args = _validate(ListFilesArgs, dict(raw), tool_name="list_files")
        if hasattr(storage, "list_files_page"):
            # Single list_objects_v2 call with MaxKeys=50 — burns exactly one
            # GET regardless of total bucket size.  The full paginator path
            # below fetches ALL pages just to populate `count`, which at
            # 10 000 objects costs 10 GETs = half the monthly free-tier budget.
            entries, next_token = storage.list_files_page(
                container,
                args.prefix,
                _MAX_LIST_ENTRIES_IN_RESULT,
                args.continuation_token,
            )
            summaries = [
                {
                    "object_name": getattr(entry, "object_name", ""),
                    "size_bytes": getattr(entry, "size_bytes", None),
                    "size": _human_size(getattr(entry, "size_bytes", None)),
                }
                for entry in entries
            ]
            return {
                "returned": len(summaries),
                "has_more": bool(next_token),
                "next_token": next_token,
                "entries": summaries,
            }
        entries = storage.list_files(container=container, prefix=args.prefix)
        summaries = [
            {
                "object_name": getattr(entry, "object_name", ""),
                "size_bytes": getattr(entry, "size_bytes", None),
                "size": _human_size(getattr(entry, "size_bytes", None)),
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
            "size": _human_size(getattr(info, "size_bytes", None)),
            "updated_at": str(getattr(info, "updated_at", "") or ""),
            "version_id": getattr(info, "version_id", None),
        }

    def _delete_file(**raw: object) -> dict[str, object]:
        tool_name = "delete_file"
        args = _validate(DeleteFileArgs, dict(raw), tool_name=tool_name)
        proposal = _mutation_proposal(
            tool_name=tool_name,
            args={
                "remote_path": args.remote_path,
                "model_supplied_confirm": args.confirm,
            },
        )
        if proposal is not None:
            return proposal
        if not args.confirm:
            msg = "refusing to delete without confirm=true"
            raise AIToolArgsInvalidError(tool_name, msg)
        result = storage.delete_file(container=container, object_name=args.remote_path)
        return {
            "deleted": _delete_result_deleted(result),
            "remote_path": args.remote_path,
            "version_id": _delete_result_version_id(result),
        }

    def _refuse_if_dest_exists(*, dest: str, tool_name: str) -> None:
        try:
            storage.get_file_info(container=container, object_name=dest)
        except ObjectNotFoundError:
            return
        msg = f"destination {dest!r} already exists; pass overwrite=true to replace"
        raise AIToolArgsInvalidError(tool_name, msg)

    def _copy_via_roundtrip(*, source: str, dest: str) -> None:
        if hasattr(storage, "copy_object"):
            # Server-side S3 copy: zero bytes transit this process or the
            # Render dyno's network interface.  Saves the full GET + PUT
            # bandwidth cost and reduces latency from ~500 ms to ~50 ms.
            storage.copy_object(
                src_container=container,
                src_key=source,
                dst_container=container,
                dst_key=dest,
            )
            return
        if hasattr(storage, "read_object"):
            # In-memory path: avoids tempfile disk I/O on 512 MB Render dyno
            # but bytes still transit the process (GET → memory → PUT).
            body = storage.read_object(container=container, key=source)
            storage.upload_obj(
                container=container,
                file_obj=io.BytesIO(body),
                remote_path=dest,
            )
            return
        with tempfile.NamedTemporaryFile(suffix=".nimbus-copy", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            storage.download_file(
                container=container, object_name=source, file_name=tmp_path
            )
            storage.upload_file(
                container=container, local_path=tmp_path, remote_path=dest
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _copy_file(**raw: object) -> dict[str, object]:
        tool_name = "copy_file"
        args = _validate(CopyFileArgs, dict(raw), tool_name=tool_name)
        if args.source_path == args.dest_path:
            raise AIToolArgsInvalidError(
                tool_name, "source_path and dest_path must differ"
            )
        proposal = _mutation_proposal(
            tool_name=tool_name,
            args={
                "source_path": args.source_path,
                "dest_path": args.dest_path,
                "overwrite": args.overwrite,
            },
        )
        if proposal is not None:
            return proposal
        if not args.overwrite:
            _refuse_if_dest_exists(dest=args.dest_path, tool_name=tool_name)
        _copy_via_roundtrip(source=args.source_path, dest=args.dest_path)
        return {
            "copied": True,
            "source_path": args.source_path,
            "dest_path": args.dest_path,
            "overwrote": args.overwrite,
        }

    def _move_file(**raw: object) -> dict[str, object]:
        tool_name = "move_file"
        args = _validate(MoveFileArgs, dict(raw), tool_name=tool_name)
        if args.source_path == args.dest_path:
            raise AIToolArgsInvalidError(
                tool_name, "source_path and dest_path must differ"
            )
        proposal = _mutation_proposal(
            tool_name=tool_name,
            args={
                "source_path": args.source_path,
                "dest_path": args.dest_path,
                "overwrite": args.overwrite,
                "model_supplied_confirm": args.confirm,
            },
        )
        if proposal is not None:
            return proposal
        if not args.confirm:
            raise AIToolArgsInvalidError(
                tool_name, "refusing to move without confirm=true"
            )
        if not args.overwrite:
            _refuse_if_dest_exists(dest=args.dest_path, tool_name=tool_name)
        _copy_via_roundtrip(source=args.source_path, dest=args.dest_path)
        # Source existence is guaranteed by the successful copy above, so skip
        # the HEAD that delete_file does for its ObjectNotFoundError contract.
        if hasattr(storage, "force_delete"):
            delete_result = storage.force_delete(
                container=container, key=args.source_path
            )
        else:
            delete_result = storage.delete_file(
                container=container, object_name=args.source_path
            )
        return {
            "moved": True,
            "source_path": args.source_path,
            "dest_path": args.dest_path,
            "source_deleted": _delete_result_deleted(delete_result),
        }

    def _read_file(**raw: object) -> dict[str, object]:
        tool_name = "read_file"
        args = _validate(ReadFileArgs, dict(raw), tool_name=tool_name)
        if hasattr(storage, "get_object_range"):
            # HTTP Range request: only download the bytes we need.
            # S3 returns Content-Range: bytes 0-N/TOTAL so we learn the full
            # object size without a separate HeadObject call — saving 1 GET
            # from the monthly budget and transferring only max_bytes bytes
            # instead of the whole file.
            slice_, total_size = storage.get_object_range(
                container, args.remote_path, 0, args.max_bytes - 1
            )
            # total_size=0 means Content-Range was absent; object fits in
            # max_bytes so it was returned in full.
            effective_total = total_size if total_size > 0 else len(slice_)
            truncated = effective_total > args.max_bytes
        else:
            with tempfile.NamedTemporaryFile(
                suffix=".nimbus-read", delete=False
            ) as tmp:
                tmp_path = tmp.name
            try:
                storage.download_file(
                    container=container,
                    object_name=args.remote_path,
                    file_name=tmp_path,
                )
                raw_bytes = Path(tmp_path).read_bytes()
            finally:
                Path(tmp_path).unlink(missing_ok=True)
            effective_total = len(raw_bytes)
            truncated = len(raw_bytes) > args.max_bytes
            slice_ = raw_bytes[: args.max_bytes]
        if args.encoding == "utf-8":
            try:
                content = slice_.decode("utf-8")
                returned_encoding: _TextOrBytesEncoding = "utf-8"
            except UnicodeDecodeError:
                content = base64.b64encode(slice_).decode("ascii")
                returned_encoding = "base64"
        else:
            content = base64.b64encode(slice_).decode("ascii")
            returned_encoding = "base64"
        return {
            "remote_path": args.remote_path,
            "encoding": returned_encoding,
            "content": content,
            "bytes_returned": len(slice_),
            "total_bytes": effective_total,
            "truncated": truncated,
        }

    def _write_file(**raw: object) -> dict[str, object]:
        tool_name = "write_file"
        args = _validate(WriteFileArgs, dict(raw), tool_name=tool_name)
        if args.encoding == "base64":
            try:
                body = base64.b64decode(args.content, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise AIToolArgsInvalidError(
                    tool_name, f"content is not valid base64: {exc}"
                ) from exc
        else:
            body = args.content.encode("utf-8")
        if len(body) > _WRITE_FILE_MAX_BYTES:
            raise AIToolArgsInvalidError(
                tool_name,
                f"content exceeds {_WRITE_FILE_MAX_BYTES} bytes after decoding "
                f"({len(body)} bytes)",
            )
        proposal = _mutation_proposal(
            tool_name=tool_name,
            args={
                "remote_path": args.remote_path,
                "encoding": args.encoding,
                "overwrite": args.overwrite,
                "model_supplied_confirm": args.confirm,
                "content_base64": base64.b64encode(body).decode("ascii"),
                "content_sha256_hex": hashlib.sha256(body).hexdigest(),
                "content_bytes": len(body),
            },
        )
        if proposal is not None:
            return proposal
        try:
            storage.get_file_info(container=container, object_name=args.remote_path)
        except ObjectNotFoundError:
            existed = False
        else:
            existed = True
        if existed:
            if not args.overwrite:
                raise AIToolArgsInvalidError(
                    tool_name,
                    f"{args.remote_path!r} already exists; pass overwrite=true to "
                    "replace its contents",
                )
            if not args.confirm:
                raise AIToolArgsInvalidError(
                    tool_name,
                    "overwriting an existing object requires confirm=true",
                )
        # BytesIO avoids the tempfile round-trip: no open/write/fsync/unlink
        # syscalls, no disk pressure on the 512 MB Render dyno. upload_obj is
        # on the CloudStorageClient protocol so no duck-type check is needed.
        storage.upload_obj(
            container=container,
            file_obj=io.BytesIO(body),
            remote_path=args.remote_path,
        )
        return {
            "written": True,
            "remote_path": args.remote_path,
            "bytes_written": len(body),
            "encoding": args.encoding,
            "overwrote": existed,
        }

    tools = [
        Tool(
            name="list_files",
            description=_tool_description(
                "list_files",
                "List objects in the pinned cloud-storage container, optionally "
                "filtered by a key prefix.",
            ),
            parameters_schema=ListFilesArgs.model_json_schema(),
            handler=_list_files,
        ),
        Tool(
            name="get_file_info",
            description=_tool_description(
                "get_file_info",
                "Fetch metadata such as size, version, and last-modified time "
                "for a single stored object.",
            ),
            parameters_schema=GetFileInfoArgs.model_json_schema(),
            handler=_get_file_info,
        ),
        Tool(
            name="read_file",
            description=_tool_description(
                "read_file",
                "Read up to max_bytes (default 64 KB) from an object in the "
                "pinned container. Returns text when the file decodes as UTF-8, "
                "otherwise returns base64-encoded bytes. Use this when you need "
                "to inspect the contents of a file, not just its metadata.",
            ),
            parameters_schema=ReadFileArgs.model_json_schema(),
            handler=_read_file,
        ),
    ]
    if include_delete_tool:
        tools.append(
            Tool(
                name="delete_file",
                description=_tool_description(
                    "delete_file",
                    "Delete a single object from the pinned container. "
                    "Requires confirm=true and should only be exposed behind an "
                    "explicit confirmation flow.",
                ),
                parameters_schema=DeleteFileArgs.model_json_schema(),
                handler=_delete_file,
                is_destructive=True,
            )
        )
        tools.append(
            Tool(
                name="copy_file",
                description=_tool_description(
                    "copy_file",
                    "Copy an object to a new key within the pinned container. "
                    "Refuses to overwrite an existing destination unless "
                    "overwrite=true is passed.",
                ),
                parameters_schema=CopyFileArgs.model_json_schema(),
                handler=_copy_file,
                is_destructive=True,
            )
        )
        tools.append(
            Tool(
                name="move_file",
                description=_tool_description(
                    "move_file",
                    "Move (rename) an object: copy to dest_path, then delete "
                    "the source. Destructive — requires confirm=true and should "
                    "be exposed behind an explicit approval flow. Use this to "
                    "rename objects (S3 has no native rename).",
                ),
                parameters_schema=MoveFileArgs.model_json_schema(),
                handler=_move_file,
                is_destructive=True,
            )
        )
        tools.append(
            Tool(
                name="write_file",
                description=_tool_description(
                    "write_file",
                    "Create a new object or replace an existing one. Pass "
                    "encoding='utf-8' with text content, or encoding='base64' "
                    "with base64-encoded bytes. Refuses to overwrite an "
                    "existing object unless overwrite=true AND confirm=true.",
                ),
                parameters_schema=WriteFileArgs.model_json_schema(),
                handler=_write_file,
                is_destructive=True,
            )
        )
    return tools
