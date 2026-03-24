"""AWS S3 FastAPI service."""

import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import structlog
from cloud_storage_client_api.client import CloudStorageClient
from cloud_storage_client_api.factory import get_client
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

import aws_client_impl  # noqa: F401  # triggers dependency injection

log: Any = structlog.get_logger()

app = FastAPI(title="AWS S3 Cloud Storage Service", version="0.1.0")


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


@app.get("/download", response_class=FileResponse)
def download_file(
    bucket_name: str,
    object_name: str,
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
