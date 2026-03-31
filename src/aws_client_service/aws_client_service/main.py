"""AWS S3 FastAPI service."""
"""AWS S3 FastAPI service."""

from aws_client_service.routes.auth import router as auth_router
from aws_client_service.deps import require_oauth_session
from starlette.middleware.sessions import SessionMiddleware
from starlette.background import BackgroundTask
from pydantic import BaseModel
from fastapi.responses import FileResponse
from fastapi import Depends, FastAPI, HTTPException, Query, UploadFile
from cloud_storage_client_api.factory import get_client
from cloud_storage_client_api.client import CloudStorageClient
import structlog
from dotenv import load_dotenv
from typing import Annotated, Any
from pathlib import Path, PurePosixPath
import os
import tempfile


load_dotenv(Path(__file__).resolve().parents[3] / ".env")


import aws_client_impl  # noqa: F401  # triggers dependency injection

log: Any = structlog.get_logger()

app = FastAPI(title="AWS S3 Cloud Storage Service", version="0.1.0")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET_KEY"],
)

app.include_router(auth_router)


class OperationResult(BaseModel):
    """JSON response model for operations that return a boolean result."""

    ok: bool


def get_storage_client() -> CloudStorageClient:
    """Dependency that provides a CloudStorageClient instance."""
    return get_client()


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
    container: str,
    object_name: str,
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
        client.upload_obj(file.file, object_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
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
@app.get("/download", response_class=FileResponse)
def download_file(
    bucket_name: str,
    object_name: str,
    _: Annotated[str, Depends(require_oauth_session)],
    client: Annotated[CloudStorageClient, Depends(get_storage_client)],
) -> FileResponse:
    """Download an S3 object and return it as a file response.

    Args:
        bucket_name: The name of the bucket to download from.
        object_name: The name of the key to download from.
        client: Injected cloud storage client.

    Returns:
        A streaming file response containing the downloaded object.

    Raises:
        HTTPException: 404 if the download fails.

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
            bucket_name,
            object_name,
            tmp_path,
        )
    except Exception as exc:
        Path(tmp_path).unlink(missing_ok=True)
        log.exception(
            "Download failed",
            bucket_name=bucket_name,
            object_name=object_name,
        )
        raise HTTPException(
            status_code=502,
            detail="Download failed due to a storage error",
        ) from exc

    if not success:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(
            status_code=404,
            detail="Object not found or download failed",
        )

    return FileResponse(
        path=tmp_path,
        filename=PurePosixPath(object_name).name,
        background=BackgroundTask(remove_temp_file, tmp_path),
    )


@app.delete("/files/{container}/{object_name:path}")
def delete_object(
    container: str,
    object_name: str,
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
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the deletion returns failure.

    """
    try:
        ok = client.delete_file(container, object_name)
    except Exception as exc:
        log.exception(
            "Delete failed",
            container=container,
            object_name=object_name,
        )
        raise HTTPException(
            status_code=502,
            detail="Delete failed due to a storage error",
        ) from exc

    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Object not found or delete failed",
        )

    return OperationResult(ok=True)


def validate_prefix(prefix: str | None = Query(None)) -> str:
    """Validate that a prefix query parameter is provided.

    Args:
        prefix: Optional prefix from the query string.

    Returns:
        The validated prefix string.

    Raises:
        HTTPException: If prefix is not provided.

    """
    if prefix is None:
        raise HTTPException(status_code=422, detail="prefix is required")
    return prefix


@app.get("/files")
def list_files(
    prefix: Annotated[str, Depends(validate_prefix)],
    _: Annotated[str, Depends(require_oauth_session)],
    client: Annotated[CloudStorageClient, Depends(get_storage_client)],
) -> dict[str, list[str]]:
    """List files that match a given prefix.

    Args:
        prefix: Prefix used to filter objects.
        client: Injected cloud storage client.

    Returns:
        A JSON object containing matching file keys.

    Raises:
        HTTPException: If the storage backend fails.

    """
    try:
        files = client.list_files(prefix)
    except Exception as exc:
        log.exception("List files failed", prefix=prefix)
        raise HTTPException(
            status_code=502,
            detail="List files failed due to a storage error",
        ) from exc

    return {"files": files}
