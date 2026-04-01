"""AWS S3 implementation of the cloud storage client."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, BinaryIO

import boto3
import structlog
from botocore.exceptions import ClientError
from cloud_storage_client_api.client import CloudStorageClient
from cloud_storage_client_api.exceptions import (
    InvalidContainerError,
    InvalidFileObjectError,
    InvalidObjectNameError,
    ObjectNotFoundError,
    StorageBackendError,
)

# Constants
MULTIPART_THRESHOLD = 100 * 1024 * 1024
MAX_LIMIT = 10000

log: Any = structlog.get_logger()


class S3Client(CloudStorageClient):
    """AWS S3 implementation of the CloudStorageClient interface.

    Session and low-level client are created lazily on first use so that
    importing this module and constructing instances is side-effect-free
    with respect to AWS credentials and network access.
    """

    def __init__(self, region_name: str = "us-east-1") -> None:
        """Initialize S3Client with the AWS region.

        No network calls are made here. The boto3 session and S3 client are
        created lazily on first use, so constructing an instance does not
        require AWS credentials to be present.

        Args:
            region_name: AWS region name. Defaults to 'us-east-1'.

        """
        self._region_name = region_name

    @property
    def _s3_client(self) -> Any:  # noqa: ANN401  # boto3.Session.client() has no precise stub; the return type is a dynamic ServiceClient with no public type annotation
        """Boto3 low-level S3 client for the configured region."""
        return self._get_session().client("s3")

    @property
    def _s3_resource(self) -> Any:  # noqa: ANN401  # boto3.resource() returns a dynamically generated ServiceResource with no public type annotation
        """Lazy boto3 S3 resource, initialised on first access.

        Reference: https://docs.aws.amazon.com/boto3/latest/reference/
        services/s3/service-resource/create_bucket.html
        """
        return self._get_session().resource("s3")  # pragma: no cover

    def upload_file(self, container: str, local_path: str, key: str) -> bool:
        """Upload a file to the S3 bucket.

        Automatically uses multipart upload for files exceeding
        ``MULTIPART_THRESHOLD``. For smaller files, a standard single-part
        upload is performed.

        Args:
            container: The name of the bucket to upload to.
            local_path: The path to the local file to upload.
            key: The S3 object key (destination path within the bucket).

        Returns:
            True, if the upload was successful

        Raises:
            InvalidContainerError: If container is empty or otherwise invalid.
            InvalidObjectNameError: If key is empty or starts with a leading slash.
            StorageBackendError: If the upload fails due to AWS service errors.
            FileNotFoundError: If the local_path does not exist.

        """
        self._validate_container_name(container)
        self._validate_object_name(key)
        try:
            file_size = Path(local_path).stat().st_size
            if file_size > MULTIPART_THRESHOLD:
                return self._multipart_upload_file(container, local_path, key)
            log.info("Commencing file upload...")
            self._s3_client.upload_file(local_path, container, key)
            log.info("File Uploaded Sucessfully !")
        except ClientError as exc:
            log.exception("Failed to upload", container=container, key=key)
            raise self._translate_client_error(
                exc, container=container, key=key
            ) from exc
        except FileNotFoundError:
            log.exception(
                "Unable to locate file. Check file path",
                local_path=local_path,
            )
            raise
        return True

    def upload_obj(self, container: str, file_obj: BinaryIO, key: str) -> bool:
        """Upload a binary file-like object to S3.

        Seekable objects smaller than ``MULTIPART_THRESHOLD`` use a standard
        upload. Larger or unseekable objects use multipart upload.

        Args:
            container: The name of the bucket to upload to.
            file_obj: A readable binary file-like object.
            key: The destination object key within the bucket.

        Returns:
            True if the upload succeeds.

        Raises:
            InvalidContainerError: If container is invalid.
            InvalidObjectNameError: If key is invalid.
            InvalidFileObjectError: If file_obj is not a readable binary object.
            StorageBackendError: If S3 returns an unexpected error.

        """
        self._validate_container_name(container)
        self._validate_object_name(key)
        self._validate_file_obj(file_obj=file_obj)

        if not file_obj.seekable():
            return self._multipart_upload_obj(container, file_obj, key)

        file_obj.seek(0, 2)
        file_size = file_obj.tell()
        file_obj.seek(0)

        if file_size > MULTIPART_THRESHOLD:  # pragma: no cover
            return self._multipart_upload_obj(
                container, file_obj, key
            )  # pragma: no cover

        try:
            log.info("Commencing object upload...")
            self._s3_client.upload_fileobj(file_obj, container, key)
        except ClientError as exc:
            log.exception("Failed to upload object", container=container, key=key)
            raise self._translate_client_error(
                exc, container=container, key=key
            ) from exc
        return True

    def _multipart_upload_file(self, container: str, local_path: str, key: str) -> bool:
        """Upload a large local file using S3 multipart upload.

        Args:
            container: The name of the bucket to upload to.
            local_path: The local file path.
            key: The destination object key.

        Returns:
            True if the upload succeeds.

        Raises:
            ClientError: If any multipart step fails.

        """
        response = self.create_multipart_upload(container=container, key=key)
        upload_id = response["UploadId"]
        parts = []

        try:
            with Path(local_path).open("rb") as f:
                part_number = 1
                while chunk := f.read(MULTIPART_THRESHOLD):
                    part_response = self.upload_part(
                        container=container,
                        key=key,
                        upload_id=upload_id,
                        part_number=part_number,
                        body=chunk,
                    )
                    parts.append(
                        {
                            "PartNumber": part_number,
                            "ETag": part_response["ETag"],
                        },
                    )
                    part_number += 1
        except ClientError:  # pragma: no cover
            log.exception(
                "Multipart upload failed, aborting...",
                container=container,
                key=key,
                upload_id=upload_id,
            )
            self.abort_multipart_upload(
                container=container,
                key=key,
                upload_id=upload_id,
            )
            raise

        self.complete_multipart_upload(
            container=container,
            key=key,
            upload_id=upload_id,
            parts=parts,
        )
        return True

    def _multipart_upload_obj(
        self, container: str, file_obj: BinaryIO, key: str
    ) -> bool:  # pragma: no cover
        """Upload a large file-like object using S3 multipart upload.

        Args:
            container: The name of the bucket to upload to.
            file_obj: A readable binary file-like object.
            key: The destination object key.

        Returns:
            True if the upload succeeds.

        Raises:
            ClientError: If any multipart step fails.

        """
        response = self.create_multipart_upload(container=container, key=key)
        upload_id = response["UploadId"]
        parts = []

        try:
            part_number = 1
            while chunk := file_obj.read(MULTIPART_THRESHOLD):
                part_response = self.upload_part(
                    container=container,
                    key=key,
                    upload_id=upload_id,
                    part_number=part_number,
                    body=chunk,
                )
                parts.append(
                    {
                        "PartNumber": part_number,
                        "ETag": part_response["ETag"],
                    },
                )
                part_number += 1
        except ClientError:
            log.exception(
                "Multipart upload failed, aborting...",
                container=container,
                key=key,
                upload_id=upload_id,
            )
            self.abort_multipart_upload(
                container=container,
                key=key,
                upload_id=upload_id,
            )
            raise

        self.complete_multipart_upload(
            container=container,
            key=key,
            upload_id=upload_id,
            parts=parts,
        )
        return True

    def create_bucket(
        self,
        bucket_name: str,
        region_name: str | None = None,
    ) -> bool:
        """Create an S3 bucket.

        Args:
            bucket_name: The bucket name to create.
            region_name: Optional AWS region for the new bucket.

        Returns:
            True if the bucket was created successfully, otherwise False.

        Raises:
            ClientError: If bucket creation fails.

        """
        if region_name is None:  # pragma: no cover
            region_name = "us-east-1"  # pragma: no cover

        # Note: Consider adding resilience patterns in future iterations
        try:
            bucket_config: dict[str, Any] = {}
            if region_name != "us-east-1":
                bucket_config["CreateBucketConfiguration"] = {
                    "LocationConstraint": region_name,
                }
            log.info("Creating Amazon S3 Bucket...")
            self._s3_client.create_bucket(Bucket=bucket_name, **bucket_config)
            self._s3_resource.Bucket(bucket_name).wait_until_exists()
            log.info(
                "SUCCESS! Created bucket %s in region: %s",
                bucket_name,
                region_name,
            )
        except ClientError:
            log.exception(
                "Failed to create Amazon S3 Bucket",
                bucket_name=bucket_name,
                region_name=region_name,
            )
            return False
        return True

    # There's two methods: delete and delete_bucket which basically do the same thing,
    # don't know how to navigate this
    def delete_bucket(
        self,
        bucket_name: str,
    ) -> bool:
        """Delete an Amazon S3 bucket.

        All objects in the bucket must be deleted
        before the bucket itself can be deleted.

        Args:
             bucket_name: Bucket to delete.

        Returns:
             True if the bucket was deleted successfully,
             False otherwise.

        Raises:
             ClientError: If the bucket deletion fails due to
                 AWS service errors (logged and caught).

        """
        try:
            bucket = self._s3_resource.Bucket(bucket_name)
            log.info("Deleting Amazon S3 Bucket...")
            bucket.delete()
            bucket.wait_until_not_exists()
            log.info("SUCCESS! Deleted bucket %s", bucket_name)
        except ClientError:
            log.exception("Failed to delete Amazon S3 Bucket", bucket_name=bucket_name)
            return False
        return True

    # Note: ExtraArgs parameter and Callback parameter can be used to implement
    # a progress monitor. Reference for ExtraArgs parameters:
    # https://docs.aws.amazon.com/boto3/latest/reference/
    # customizations/s3.html#boto3.s3.transfer.S3Transfer.ALLOWED_UPLOAD_ARGS
    def download_file(
        self,
        container: str,
        object_name: str,
        file_name: str,
    ) -> bool:
        """Download an S3 object to a file.

        The ``download_file`` method accepts the names of the bucket
        and object to download and the filename to save the file to.

        Args:
            container: The name of the bucket to download from.
            object_name: The name of the key to download from.
            file_name: The path to the file to download to.

        Returns:
            True if the file was downloaded successfully.

        Raises:
            InvalidContainerError: If container is empty or otherwise invalid.
            InvalidObjectNameError: If object_name is empty or otherwise invalid.
            ObjectNotFoundError: If the object does not exist.
            StorageBackendError: If the download fails due to AWS service errors.

        """
        self._validate_container_name(container)
        self._validate_object_name(object_name)
        try:
            log.info("Downloading S3 Object to a file...")
            self._s3_resource.meta.client.download_file(
                container, object_name, file_name
            )
        except ClientError as exc:
            log.exception(
                "Failed to download file from Amazon S3 Bucket",
                container=container,
                object_name=object_name,
                file_name=file_name,
            )
            raise self._translate_client_error(
                exc,
                container=container,
                key=object_name,
            ) from exc
        return True

    # Note: Consider combining download_file and download_fileobj in the future
    # Also look into adding more exceptions for file path access
    def download_fileobj(
        self,
        bucket_name: str,
        object_name: str,
        file_name: str,
        file_object: str,
    ) -> bool:
        """Download an S3 object to a file-like object.

        The file-like object must be in binary mode. The
        ``download_fileobj`` method is a managed transfer which
        will perform a multipart download in multiple threads
        if necessary.

        Args:
            bucket_name: The name of the bucket to download from.
            object_name: The name of the key to download from.
            file_name: The path to the file to download to.
            file_object: A file-like object to download into.

        Returns:
            True if the file-like object was successfully
            downloaded into, False otherwise.

        Raises:
            ClientError: If the download fails due to
                AWS service errors (logged and caught).

        """
        try:
            log.info("Downloading S3 Object to a file...")
            with Path(file_name).open("wb") as f:
                self._s3_resource.meta.client.download_fileobj(
                    bucket_name, object_name, f
                )

        except ClientError:
            log.exception(
                "Failed to download file from Amazon S3 Bucket",
                bucket_name=bucket_name,
                object_name=object_name,
                file_name=file_name,
                file_object=file_object,
            )
            return False
        return True

    def list_files(self, container: str, prefix: str = "") -> list[str]:
        """List object keys in an S3 bucket.

        Args:
            container: The name of the bucket to list from.
            prefix: Optional key prefix filter.

        Returns:
            A list of matching object keys.

        Raises:
            InvalidContainerError: If container is invalid.
            StorageBackendError: If listing fails.

        """
        self._validate_container_name(container)
        try:
            paginator = self._s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=container, Prefix=prefix)
            return [obj["Key"] for page in pages for obj in page.get("Contents", [])]
        except ClientError as exc:
            raise self._translate_client_error(exc, container=container) from exc

    # Note: Batch deletion of multiple files could be added for efficiency
    def delete_file(
        self,
        container: str,
        object_name: str,
    ) -> bool:
        """Remove an object from a bucket.

        Args:
            container: The name of the bucket containing the object.
            object_name: The object key to delete.

        Returns:
            True if the object was deleted successfully.

        Raises:
            InvalidContainerError: If container is invalid.
            InvalidObjectNameError: If object_name is invalid.
            ObjectNotFoundError: If the object does not exist.
            StorageBackendError: If the deletion fails.

        """
        self._validate_container_name(container)
        self._validate_object_name(object_name)
        try:
            self._s3_client.head_object(Bucket=container, Key=object_name)
            log.info("Deleting S3 Object...")
            self._s3_client.delete_object(Bucket=container, Key=object_name)
        except ClientError as exc:
            log.exception(
                "Failed to delete object from Amazon S3 Bucket",
                container=container,
                object_name=object_name,
            )
            raise self._translate_client_error(
                exc,
                container=container,
                key=object_name,
            ) from exc
        return True

    # Helpers
    def _get_session(self) -> boto3.Session:
        """Create and return a boto3 Session for the configured region."""
        return boto3.Session(region_name=self._region_name)

    def get_session(self) -> boto3.Session:  # pragma: no cover
        """Return a boto3 Session for the configured region."""
        return self._get_session()  # pragma: no cover

    def _validate_file_obj(self, file_obj: BinaryIO) -> None:
        """Validate a file-like object for upload.

        Args:
            file_obj: The file-like object to validate.

        Raises:
            InvalidFileObjectError: If the file object is not readable or not binary.

        """
        try:
            if not file_obj.readable():
                msg = "file_obj must be readable"
                log.error(msg)
                raise InvalidFileObjectError(msg)
            if isinstance(file_obj.read(0), str):
                msg = "file_obj must be opened in binary mode, not text mode"
                log.error(msg)
                raise InvalidFileObjectError(msg)
        except AttributeError as exc:
            msg = "file_obj must be a file-like object with a read() method"
            log.exception(msg)
            raise InvalidFileObjectError(msg) from exc

    def create_multipart_upload(self, container: str, key: str) -> dict[str, Any]:
        """Initiate a multipart upload and return the upload ID.

        Must be called before ``upload_part``. The ``UploadId`` in the
        returned response is required for all subsequent ``upload_part``,
        ``complete_multipart_upload``, and ``abort_multipart_upload`` calls.

        Note:
            You must either complete or abort the multipart upload after
            initiating it, otherwise AWS will continue charging for the
            stored parts indefinitely.

        Args:
            container: The name of the bucket to upload to.
            key: The S3 object key to upload to.

        Returns:
            The raw boto3 response dict containing UploadId

        Raises:
            InvalidContainerError: If container is empty or otherwise invalid.
            InvalidObjectNameError: If ``key`` is empty or starts with a leading slash.
            StorageBackendError: If the request fails due to AWS service errors.

        """
        self._validate_container_name(container)
        self._validate_object_name(key)
        kwargs = {
            "Bucket": container,
            "Key": key,
        }
        try:
            log.info("Initializing Multipart Upload...")
            response: dict[str, Any] = self._s3_client.create_multipart_upload(**kwargs)
            log.info("SUCCESS! Multipart Upload is completed")
        except ClientError as exc:
            log.exception(
                "Failed Multipart Upload",
                container=container,
                key=key,
            )
            raise self._translate_client_error(
                exc, container=container, key=key
            ) from exc
        return response

    def upload_part(
        self,
        container: str,
        key: str,
        upload_id: str,
        part_number: int,
        body: bytes | BinaryIO,
    ) -> dict[str, object]:
        """Upload a single part in a multipart upload.

        Args:
            container: The name of the bucket to upload to.
            key: The destination object key.
            upload_id: The multipart upload identifier.
            part_number: Part index in the range 1-10000.
            body: Raw bytes or a binary file-like object for the part.

        Returns:
            The raw boto3 response dictionary for the uploaded part.

        Raises:
            InvalidContainerError: If container is invalid.
            InvalidObjectNameError: If key is invalid.
            ValueError: If part_number is outside the allowed range.
            StorageBackendError: If S3 rejects the part upload.

        """
        self._validate_container_name(container)
        self._validate_object_name(key)
        if not 1 <= part_number <= MAX_LIMIT:
            msg = "part_number must be in the range 1-10000 (inclusive)"
            log.error(msg)
            raise ValueError(msg)
        kwargs: dict[str, object] = {
            "Bucket": container,
            "Key": key,
            "PartNumber": part_number,
            "UploadId": upload_id,
            "Body": body,
        }

        try:
            log.info(
                "Uploading part...",
                part_number=part_number,
            )
            response: dict[str, object] = self._s3_client.upload_part(**kwargs)
        except ClientError as exc:
            log.exception(
                "Failed to upload part",
                container=container,
                key=key,
                part_number=part_number,
                upload_id=upload_id,
            )
            raise self._translate_client_error(
                exc, container=container, key=key
            ) from exc
        return response

    def abort_multipart_upload(self, container: str, key: str, upload_id: str) -> bool:
        """Abort an in-progress multipart upload.

        Args:
            container: The name of the bucket that owns the upload.
            key: The destination object key.
            upload_id: The multipart upload identifier.

        Returns:
            True if the abort succeeds.

        Raises:
            InvalidContainerError: If container is invalid.
            InvalidObjectNameError: If key is invalid.
            StorageBackendError: If S3 rejects the abort request.

        """
        self._validate_container_name(container)
        self._validate_object_name(key)
        try:
            log.info(
                "Aborting multipart upload...",
                container=container,
                key=key,
                upload_id=upload_id,
            )
            self._s3_client.abort_multipart_upload(
                Bucket=container,
                Key=key,
                UploadId=upload_id,
            )
            log.info(
                "SUCCESS! Multipart upload aborted",
                container=container,
                key=key,
                upload_id=upload_id,
            )
        except ClientError as exc:
            log.exception(
                "Failed to abort multipart upload",
                container=container,
                key=key,
                upload_id=upload_id,
            )
            raise self._translate_client_error(
                exc, container=container, key=key
            ) from exc
        return True

    def complete_multipart_upload(
        self,
        container: str,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> bool:
        """Complete a multipart upload by assembling uploaded parts.

        Args:
            container: The name of the bucket that owns the upload.
            key: The destination object key.
            upload_id: The multipart upload identifier.
            parts: Uploaded part metadata including part numbers and ETags.

        Returns:
            True if the multipart upload is completed successfully.

        Raises:
            InvalidContainerError: If container is invalid.
            InvalidObjectNameError: If key is invalid.
            StorageBackendError: If S3 rejects the completion request.

        """
        self._validate_container_name(container)
        self._validate_object_name(key)
        try:
            log.info(
                "Assembling uploaded parts for multipart upload...",
                container=container,
                key=key,
            )
            self._s3_client.complete_multipart_upload(
                Bucket=container,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            log.info("SUCESS! All parts are assesmbled and finished uploading")
        except ClientError as exc:
            log.exception(
                "Failed to assemble parts and complete multipart upload",
                container=container,
                key=key,
                parts=parts,
            )
            raise self._translate_client_error(
                exc, container=container, key=key
            ) from exc
        return True

    def _validate_container_name(self, container: str) -> None:
        """Validate a container / bucket name before calling S3."""
        if not container:
            msg = "Container cannot be empty"
            log.error(msg)
            raise InvalidContainerError(msg)

    def _validate_object_name(self, key: str) -> None:
        """Validate an S3 object key before calling S3."""
        if not key:
            msg = "Key cannot be empty"
            log.error(msg)
            raise InvalidObjectNameError(msg)
        if key.startswith("/"):
            msg = "S3 object key cannot start with a leading slash"
            log.error(msg)
            raise InvalidObjectNameError(msg)

    def _translate_client_error(
        self,
        exc: ClientError,
        *,
        container: str,
        key: str | None = None,
    ) -> StorageBackendError | ObjectNotFoundError:
        """Convert boto3 errors into provider-agnostic domain exceptions."""
        error = exc.response.get("Error", {})
        error_code = str(error.get("Code", "")).lower()
        if error_code in {"404", "nosuchkey", "nosuchbucket", "notfound"}:
            if key is None:
                msg = f"Container '{container}' was not found"
            else:
                msg = f"Object '{key}' was not found in container '{container}'"
            return ObjectNotFoundError(msg)

        if key is None:
            msg = f"S3 operation failed for container '{container}'"
        else:
            msg = f"S3 operation failed for object '{key}' in container '{container}'"
        return StorageBackendError(msg)


def get_client_impl(*, interactive: bool = False) -> S3Client:  # noqa: ARG001  # reserved for future CLI interactive-mode support; signature must match the factory callable protocol
    """Concrete factory that returns an S3Client instance."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    # Handle case where environment variable is not properly expanded
    # (e.g., CircleCI sets it to literal "${AWS_REGION}" string)
    if region.startswith("${") and region.endswith("}"):
        region = "us-east-1"

    return S3Client(region_name=region)
