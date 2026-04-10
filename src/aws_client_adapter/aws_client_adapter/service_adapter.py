"""Service adapter implementing ``CloudStorageClient`` over HTTP."""

from __future__ import annotations

import json
import mimetypes
import os
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, BinaryIO, cast

import httpx
from aws_s3_cloud_storage_service_client.api.default import (
    delete_object_files_container_object_name_delete as delete_object_api,
)
from aws_s3_cloud_storage_service_client.api.default import (
    download_file_download_get as download_file_api,
)
from aws_s3_cloud_storage_service_client.api.default import (
    get_file_info_files_container_object_name_info_get as get_file_info_api,
)
from aws_s3_cloud_storage_service_client.api.default import (
    list_files_files_get as list_files_api,
)
from aws_s3_cloud_storage_service_client.api.default import (
    upload_object_files_container_object_name_post as upload_object_api,
)
from aws_s3_cloud_storage_service_client.models.delete_result_response import (
    DeleteResultResponse,
)
from aws_s3_cloud_storage_service_client.models.object_info_response import (
    ObjectInfoResponse,
)
from aws_s3_cloud_storage_service_client.types import Unset
from cloud_storage_api import (
    CloudStorageClient,
    DeleteResult,
    InvalidContainerError,
    InvalidFileObjectError,
    InvalidObjectNameError,
    LocalFileAccessError,
    ObjectInfo,
    ObjectNotFoundError,
    StorageBackendError,
)

from aws_s3_cloud_storage_service_client import AuthenticatedClient, Client

if TYPE_CHECKING:
    from aws_s3_cloud_storage_service_client.types import Response

UNREACHABLE_MSG = "unreachable"


class _MultipartUploadBody:
    """Provide a multipart body compatible with the generated upload endpoint."""

    def __init__(
        self,
        file_obj: BinaryIO,
        *,
        file_name: str,
        mime_type: str,
    ) -> None:
        """Initialize the multipart upload payload wrapper."""
        self._file_obj = file_obj
        self._file_name = file_name
        self._mime_type = mime_type

    def to_multipart(self) -> list[tuple[str, tuple[str, BinaryIO, str]]]:
        """Return the multipart payload expected by the generated client."""
        return [("file", (self._file_name, self._file_obj, self._mime_type))]


def _or_none(value: object) -> object:
    """Convert generated-client ``Unset`` sentinels to ``None``."""
    if isinstance(value, Unset):
        return None
    return value


def _response_to_object_info(resp: ObjectInfoResponse) -> ObjectInfo:
    """Map a generated ``ObjectInfoResponse`` to a domain ``ObjectInfo``."""
    raw_metadata = _or_none(resp.metadata)
    metadata: dict[str, str] | None = None
    if raw_metadata is not None:
        metadata = dict(raw_metadata.additional_properties)
    return ObjectInfo(
        object_name=resp.object_name,
        version_id=_or_none(resp.version_id),
        data_type=_or_none(resp.data_type),
        integrity=_or_none(resp.integrity),
        encryption=_or_none(resp.encryption),
        storage_tier=_or_none(resp.storage_tier),
        size_bytes=_or_none(resp.size_bytes),
        updated_at=_or_none(resp.updated_at),
        metadata=metadata,
    )


def _response_to_delete_result(resp: DeleteResultResponse) -> DeleteResult:
    """Map a generated ``DeleteResultResponse`` to a domain ``DeleteResult``."""
    return DeleteResult(
        deleted=resp.deleted,
        version_id=_or_none(resp.version_id),
        request_charged=_or_none(resp.request_charged),
    )


class CloudStorageServiceAdapter(CloudStorageClient):
    """Route cloud storage operations through the generated HTTP client."""

    def __init__(self, service_client: Client | AuthenticatedClient) -> None:
        """Initialize the adapter with a generated service client instance."""
        self._service_client = service_client

    def upload_file(
        self, container: str, local_path: str, remote_path: str
    ) -> ObjectInfo:
        """Upload a local file to the remote service."""
        try:
            with Path(local_path).open("rb") as file_obj:
                return self.upload_obj(container, file_obj, remote_path)
        except (FileNotFoundError, OSError) as exc:
            msg = f"Cannot read local file '{local_path}'"
            raise LocalFileAccessError(msg) from exc

    def upload_obj(
        self, container: str, file_obj: BinaryIO, remote_path: str
    ) -> ObjectInfo:
        """Upload a binary file-like object through the service."""
        self._validate_container_name(container)
        self._validate_object_name(remote_path)
        self._validate_file_obj(file_obj)

        if file_obj.seekable():
            file_obj.seek(0)

        file_name = PurePosixPath(getattr(file_obj, "name", remote_path)).name
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        body = cast(
            "Any",
            _MultipartUploadBody(
                file_obj,
                file_name=file_name,
                mime_type=mime_type,
            ),
        )

        try:
            response = upload_object_api.sync_detailed(
                container=container,
                object_name=remote_path,
                client=self._service_client,
                body=body,
            )
        except httpx.HTTPError as exc:
            msg = "Upload failed due to a transport error"
            raise StorageBackendError(msg) from exc

        if response.status_code == HTTPStatus.OK and isinstance(
            response.parsed, ObjectInfoResponse
        ):
            return _response_to_object_info(response.parsed)

        self._raise_for_response(response)
        raise AssertionError(UNREACHABLE_MSG)

    def download_file(
        self, container: str, object_name: str, file_name: str
    ) -> ObjectInfo:
        """Download an object through the service and write it locally."""
        self._validate_container_name(container)
        self._validate_object_name(object_name)

        try:
            response = download_file_api.sync_detailed(
                client=self._service_client,
                container=container,
                object_name=object_name,
            )
        except httpx.HTTPError as exc:
            msg = "Download failed due to a transport error"
            raise StorageBackendError(msg) from exc

        if response.status_code == HTTPStatus.OK:
            try:
                Path(file_name).write_bytes(response.content)
            except OSError as exc:
                msg = f"Cannot write to local path '{file_name}'"
                raise LocalFileAccessError(msg) from exc
            return self.get_file_info(container, object_name)

        self._raise_for_response(response)
        raise AssertionError(UNREACHABLE_MSG)

    def list_files(self, container: str, prefix: str) -> list[ObjectInfo]:
        """List files in a remote container through the service."""
        self._validate_container_name(container)

        try:
            response = list_files_api.sync_detailed(
                client=self._service_client,
                container=container,
                prefix=prefix,
            )
        except httpx.HTTPError as exc:
            msg = "List files failed due to a transport error"
            raise StorageBackendError(msg) from exc

        if response.status_code == HTTPStatus.OK and isinstance(response.parsed, list):
            return sorted(
                [_response_to_object_info(item) for item in response.parsed],
                key=lambda info: info.object_name,
            )

        self._raise_for_response(response)
        raise AssertionError(UNREACHABLE_MSG)

    def delete_file(self, container: str, object_name: str) -> DeleteResult:
        """Delete an object through the remote service."""
        self._validate_container_name(container)
        self._validate_object_name(object_name)

        try:
            response = delete_object_api.sync_detailed(
                container=container,
                object_name=object_name,
                client=self._service_client,
            )
        except httpx.HTTPError as exc:
            msg = "Delete failed due to a transport error"
            raise StorageBackendError(msg) from exc

        if response.status_code == HTTPStatus.OK and isinstance(
            response.parsed, DeleteResultResponse
        ):
            return _response_to_delete_result(response.parsed)

        self._raise_for_response(response)
        raise AssertionError(UNREACHABLE_MSG)

    def get_file_info(self, container: str, object_name: str) -> ObjectInfo:
        """Return metadata for a single stored object."""
        self._validate_container_name(container)
        self._validate_object_name(object_name)

        try:
            response = get_file_info_api.sync_detailed(
                container=container,
                object_name=object_name,
                client=self._service_client,
            )
        except httpx.HTTPError as exc:
            msg = "Get file info failed due to a transport error"
            raise StorageBackendError(msg) from exc

        if response.status_code == HTTPStatus.OK and isinstance(
            response.parsed, ObjectInfoResponse
        ):
            return _response_to_object_info(response.parsed)

        self._raise_for_response(response)
        raise AssertionError(UNREACHABLE_MSG)

    def _validate_container_name(self, container: str) -> None:
        """Validate a container / bucket name before making a request."""
        if not container:
            msg = "Container cannot be empty"
            raise InvalidContainerError(msg)

    def _validate_object_name(self, object_name: str) -> None:
        """Validate an object key before making a request."""
        if not object_name:
            msg = "Key cannot be empty"
            raise InvalidObjectNameError(msg)
        if object_name.startswith("/"):
            msg = "S3 object key cannot start with a leading slash"
            raise InvalidObjectNameError(msg)

    def _validate_file_obj(self, file_obj: BinaryIO) -> None:
        """Validate a file-like object before upload."""
        try:
            if not file_obj.readable():
                msg = "file_obj must be readable"
                raise InvalidFileObjectError(msg)
            if isinstance(file_obj.read(0), str):
                msg = "file_obj must be opened in binary mode, not text mode"
                raise InvalidFileObjectError(msg)
        except AttributeError as exc:
            msg = "file_obj must be a file-like object with a read() method"
            raise InvalidFileObjectError(msg) from exc

    def _raise_for_response(self, response: object) -> None:
        """Translate service responses back into domain exceptions."""
        typed_response = cast("Response[object]", response)
        detail = self._extract_detail(typed_response.content)
        lowered_detail = detail.lower()

        if typed_response.status_code in {
            HTTPStatus.BAD_REQUEST,
            HTTPStatus.UNPROCESSABLE_ENTITY,
        }:
            if "container" in lowered_detail or "bucket" in lowered_detail:
                raise InvalidContainerError(detail)
            if "file_obj" in lowered_detail or "file-like" in lowered_detail:
                raise InvalidFileObjectError(detail)
            raise InvalidObjectNameError(detail)

        if typed_response.status_code == HTTPStatus.NOT_FOUND:
            raise ObjectNotFoundError(detail)

        msg = (
            detail or f"Service request failed with status {typed_response.status_code}"
        )
        raise StorageBackendError(msg)

    def _extract_detail(self, content: bytes) -> str:
        """Extract a human-readable error detail from a service response."""
        try:
            payload = json.loads(content.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return content.decode(errors="ignore") or "Service request failed"

        detail = payload.get("detail", payload)
        if isinstance(detail, str):
            return detail
        if isinstance(detail, list):
            messages: list[str] = []
            for item in detail:
                if not isinstance(item, dict):
                    continue
                message = str(item.get("msg", "Validation error"))
                location = item.get("loc", [])
                if isinstance(location, list) and location:
                    location_text = ".".join(str(part) for part in location)
                    messages.append(f"{location_text}: {message}")
                else:
                    messages.append(message)
            if messages:
                return "; ".join(messages)
        return json.dumps(detail)


def get_client_impl(*, interactive: bool = False) -> CloudStorageServiceAdapter:  # noqa: ARG001  # reserved for future CLI interactive-mode support; signature must match the factory callable protocol
    """Return a service-backed CloudStorageClient adapter instance."""
    base_url = os.environ.get("CLOUD_STORAGE_SERVICE_BASE_URL", "http://127.0.0.1:8000")
    api_key = os.environ.get("API_KEY")

    if api_key:
        service_client: Client | AuthenticatedClient = AuthenticatedClient(
            base_url=base_url,
            token=api_key,
        )
    else:
        service_client = Client(base_url=base_url)

    return CloudStorageServiceAdapter(service_client)
