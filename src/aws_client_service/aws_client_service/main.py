"""AWS S3 FastAPI service."""

import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import structlog
from cloud_storage_client_api.client import CloudStorageClient
from cloud_storage_client_api.exceptions import (
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

from aws_client_impl.s3_client import get_client_impl  # noqa: E402, I001  # load_dotenv must run before importing the concrete client module

log: Any = structlog.get_logger()

app = FastAPI(title="AWS S3 Cloud Storage Service", version="0.1.0")
SPHINX_HTML_DIR = Path(__file__).resolve().parents[3] / "docs" / "build" / "html"

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET_KEY"],
)

app.include_router(auth_router)

if SPHINX_HTML_DIR.exists():
    app.mount(
        "/guide",
        StaticFiles(directory=SPHINX_HTML_DIR, html=True),
        name="sphinx-guide",
    )


class OperationResult(BaseModel):
    """JSON response model for operations that return a boolean result."""

    ok: bool


class ListFilesResponse(BaseModel):
    """JSON response model for listing files within a container."""

    files: list[str]


def get_storage_client() -> CloudStorageClient:
    """Dependency that provides a CloudStorageClient instance."""
    return get_client_impl()


def remove_temp_file(path: str) -> None:
    """Best-effort cleanup for a temporary file created for download responses."""
    Path(path).unlink(missing_ok=True)


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
) -> OperationResult:
    """Upload an object to a bucket (container).

    Args:
        container: The name of the target bucket.
        object_name: The key of the object to upload.
        file: The file to upload.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 400 if the key is invalid (empty or leading slash).

    """
    try:
        client.upload_obj(container, file.file, object_name)
    except (
        InvalidContainerError,
        InvalidFileObjectError,
        InvalidObjectNameError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ContainerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
    return OperationResult(ok=True)


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
        HTTPException: 404 if the download fails.
        HTTPException: 502 if the storage backend raises an unexpected error.

    """
    suffix = PurePosixPath(object_name).suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(  # noqa: SIM115  # must use delete=False to hand path to FileResponse; context manager would delete prematurely
        delete=False,
        suffix=suffix,
    )
    tmp.close()
    tmp_path = tmp.name

    try:
        success = client.download_file(
            container,
            object_name,
            tmp_path,
        )
    except (InvalidContainerError, InvalidObjectNameError) as exc:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ObjectNotFoundError, ContainerNotFoundError) as exc:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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

    if not success:  # pragma: no cover
        Path(tmp_path).unlink(missing_ok=True)  # pragma: no cover
        raise HTTPException(
            status_code=404, detail="Download failed"
        )  # pragma: no cover

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
) -> OperationResult:
    """Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the object does not exist.

    """
    try:
        ok = client.delete_file(container, object_name)
    except (InvalidContainerError, InvalidObjectNameError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (ObjectNotFoundError, ContainerNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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

    if not ok:  # pragma: no cover
        raise HTTPException(status_code=404, detail="Delete failed")  # pragma: no cover

    return OperationResult(ok=True)


@app.get("/files")
def list_files(
    container: Annotated[str, Query(min_length=3, max_length=63)],
    _: Annotated[str, Depends(require_oauth_session)],
    client: Annotated[CloudStorageClient, Depends(get_storage_client)],
    prefix: Annotated[str, Query(max_length=1024)] = "",
) -> ListFilesResponse:
    """List files that match a given prefix.

    Args:
        container: Container used to scope the listing operation.
        prefix: Prefix used to filter objects.
        client: Injected cloud storage client.

    Returns:
        A JSON object containing matching file keys.

    Raises:
        HTTPException: 400 if the container name is invalid.
        HTTPException: 502 if the storage backend fails.

    """
    try:
        files = client.list_files(container, prefix)
    except InvalidContainerError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ContainerNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StorageBackendError as exc:
        log.exception("List files failed", container=container, prefix=prefix)
        raise HTTPException(
            status_code=502,
            detail="List files failed due to a storage error",
        ) from exc

    return ListFilesResponse(files=files)
