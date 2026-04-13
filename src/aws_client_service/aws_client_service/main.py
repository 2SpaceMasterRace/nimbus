"""AWS S3 FastAPI service."""

from __future__ import annotations

import os
import tempfile
from datetime import (
    datetime,  # noqa: TC003  # Pydantic models need datetime at runtime for schema generation
)
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import structlog
from ai_server.router import router as ai_router
from cloud_storage_api import (
    AuthenticationError,
    CloudStorageClient,
    ContainerNotFoundError,
    InvalidContainerError,
    InvalidFileObjectError,
    InvalidObjectNameError,
    ObjectNotFoundError,
    StorageBackendError,
)
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from starlette.middleware.sessions import SessionMiddleware
from starlette.staticfiles import StaticFiles

from aws_client_service.deps import require_oauth_session
from aws_client_service.routes.auth import router as auth_router

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

from aws_client_impl.s3_client import get_client_impl  # noqa: E402, I001  # env must be loaded before constructing the client

log: Any = structlog.get_logger()

app = FastAPI(title="AWS S3 Cloud Storage Service", version="0.1.0")
SPHINX_HTML_DIR = Path(__file__).resolve().parents[3] / "docs" / "build" / "html"

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET_KEY"],
)

app.include_router(auth_router)
app.include_router(ai_router, prefix="/ai")

if SPHINX_HTML_DIR.exists():
    app.mount(
        "/guide",
        StaticFiles(directory=SPHINX_HTML_DIR, html=True),
        name="sphinx-guide",
    )


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ObjectInfoResponse(BaseModel):
    """JSON response model for object metadata."""

    object_name: str
    version_id: str | None = None
    data_type: str | None = None
    integrity: str | None = None
    encryption: str | None = None
    storage_tier: str | None = None
    size_bytes: int | None = None
    updated_at: datetime | None = None
    metadata: dict[str, str] | None = None


class DeleteResultResponse(BaseModel):
    """JSON response model for delete operations."""

    deleted: bool
    version_id: str | None = None
    request_charged: bool | None = None


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_storage_client() -> CloudStorageClient:
    """Dependency that provides a CloudStorageClient instance."""
    return get_client_impl()


def remove_temp_file(path: str) -> None:
    """Best-effort cleanup for a temporary file created for download responses."""
    Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint."""
    return {"message": "Hello World"}


@app.post("/files/{container}/{object_name:path}")
def upload_object(
    container: Annotated[str, ApiPath(min_length=3, max_length=63)],
    object_name: Annotated[str, ApiPath(min_length=1, max_length=1024)],
    file: UploadFile,
    _: Annotated[str, Depends(require_oauth_session)],
    client: Annotated[CloudStorageClient, Depends(get_storage_client)],
) -> ObjectInfoResponse:
    """Upload an object to a bucket (container).

    Args:
        container: The name of the target bucket.
        object_name: The key of the object to upload.
        file: The file to upload.
        client: Injected cloud storage client.

    Returns:
        Metadata describing the uploaded object.

    Raises:
        HTTPException: 400 if the key or container is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the container does not exist.
        HTTPException: 502 if the storage backend fails.

    """
    try:
        info = client.upload_obj(container, file.file, object_name)
    except (
        InvalidContainerError,
        InvalidFileObjectError,
        InvalidObjectNameError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ContainerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except StorageBackendError as exc:
        log.exception(
            "Upload failed",
            container=container,
            object_name=object_name,
        )
        raise HTTPException(
            status_code=502,
            detail="Upload failed due to a storage error",
        ) from exc
    return ObjectInfoResponse(
        object_name=info.object_name,
        version_id=info.version_id,
        data_type=info.data_type,
        integrity=info.integrity,
        encryption=info.encryption,
        storage_tier=info.storage_tier,
        size_bytes=info.size_bytes,
        updated_at=info.updated_at,
        metadata=dict(info.metadata) if info.metadata else None,
    )


@app.get("/download", response_class=FileResponse)
def download_file(
    container: Annotated[str, Query(min_length=3, max_length=63)],
    object_name: Annotated[str, Query(min_length=1, max_length=1024)],
    _: Annotated[str, Depends(require_oauth_session)],
    client: Annotated[CloudStorageClient, Depends(get_storage_client)],
) -> FileResponse:
    """Download an S3 object and return it as a file response.

    Args:
        container: The name of the bucket to download from.
        object_name: The name of the key to download from.
        client: Injected cloud storage client.

    Returns:
        A streaming file response containing the downloaded object.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the object or container does not exist.
        HTTPException: 502 if the storage backend fails.

    """
    suffix = PurePosixPath(object_name).suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115  # must use delete=False to hand path to FileResponse; context manager would delete prematurely
        delete=False,
        suffix=suffix,
    )
    tmp.close()
    tmp_path = tmp.name

    try:
        client.download_file(container, object_name, tmp_path)
    except (InvalidContainerError, InvalidObjectNameError) as exc:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ObjectNotFoundError, ContainerNotFoundError) as exc:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthenticationError as exc:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except StorageBackendError as exc:
        Path(tmp_path).unlink(missing_ok=True)
        log.exception(
            "Download failed",
            container=container,
            object_name=object_name,
        )
        raise HTTPException(
            status_code=502,
            detail="Download failed due to a storage error",
        ) from exc

    return FileResponse(
        path=tmp_path,
        filename=PurePosixPath(object_name).name,
        background=BackgroundTask(remove_temp_file, tmp_path),
    )


@app.delete("/files/{container}/{object_name:path}")
def delete_object(
    container: Annotated[str, ApiPath(min_length=3, max_length=63)],
    object_name: Annotated[str, ApiPath(min_length=1, max_length=1024)],
    _: Annotated[str, Depends(require_oauth_session)],
    client: Annotated[CloudStorageClient, Depends(get_storage_client)],
) -> DeleteResultResponse:
    """Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        Deletion metadata.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the object or container does not exist.
        HTTPException: 502 if the storage backend fails.

    """
    try:
        result = client.delete_file(container, object_name)
    except (InvalidContainerError, InvalidObjectNameError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ObjectNotFoundError, ContainerNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except StorageBackendError as exc:
        log.exception(
            "Delete failed",
            container=container,
            object_name=object_name,
        )
        raise HTTPException(
            status_code=502,
            detail="Delete failed due to a storage error",
        ) from exc

    return DeleteResultResponse(
        deleted=result["deleted"],
        version_id=result.get("version_id"),
        request_charged=result.get("request_charged"),
    )


@app.get("/files")
def list_files(
    container: Annotated[str, Query(min_length=3, max_length=63)],
    _: Annotated[str, Depends(require_oauth_session)],
    client: Annotated[CloudStorageClient, Depends(get_storage_client)],
    prefix: Annotated[str, Query(max_length=1024)] = "",
) -> list[ObjectInfoResponse]:
    """List files that match a given prefix.

    Args:
        container: Container used to scope the listing operation.
        prefix: Prefix used to filter objects.
        client: Injected cloud storage client.

    Returns:
        A list of object metadata entries.

    Raises:
        HTTPException: 400 if the container name is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the container does not exist.
        HTTPException: 502 if the storage backend fails.

    """
    try:
        items = client.list_files(container, prefix)
    except InvalidContainerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ContainerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except StorageBackendError as exc:
        log.exception("List files failed", container=container, prefix=prefix)
        raise HTTPException(
            status_code=502,
            detail="List files failed due to a storage error",
        ) from exc

    return [
        ObjectInfoResponse(
            object_name=info.object_name,
            version_id=info.version_id,
            data_type=info.data_type,
            integrity=info.integrity,
            encryption=info.encryption,
            storage_tier=info.storage_tier,
            size_bytes=info.size_bytes,
            updated_at=info.updated_at,
            metadata=dict(info.metadata) if info.metadata else None,
        )
        for info in items
    ]


@app.get("/files/{container}/{object_name:path}/info")
def get_file_info(
    container: Annotated[str, ApiPath(min_length=3, max_length=63)],
    object_name: Annotated[str, ApiPath(min_length=1, max_length=1024)],
    _: Annotated[str, Depends(require_oauth_session)],
    client: Annotated[CloudStorageClient, Depends(get_storage_client)],
) -> ObjectInfoResponse:
    """Return metadata for a single stored object.

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to inspect.
        client: Injected cloud storage client.

    Returns:
        Object metadata.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the object or container does not exist.
        HTTPException: 502 if the storage backend fails.

    """
    try:
        info = client.get_file_info(container, object_name)
    except (InvalidContainerError, InvalidObjectNameError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ObjectNotFoundError, ContainerNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except StorageBackendError as exc:
        log.exception(
            "Get file info failed",
            container=container,
            object_name=object_name,
        )
        raise HTTPException(
            status_code=502,
            detail="Get file info failed due to a storage error",
        ) from exc

    return ObjectInfoResponse(
        object_name=info.object_name,
        version_id=info.version_id,
        data_type=info.data_type,
        integrity=info.integrity,
        encryption=info.encryption,
        storage_tier=info.storage_tier,
        size_bytes=info.size_bytes,
        updated_at=info.updated_at,
        metadata=dict(info.metadata) if info.metadata else None,
    )
