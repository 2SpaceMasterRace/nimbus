"""AWS S3 implementation of the cloud storage client."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, BinaryIO

import boto3
import structlog
from botocore.exceptions import ClientError

from cloud_storage_client_api.client import CloudStorageClient

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

    def __init__(self, bucket_name: str, region_name: str = "us-east-1") -> None:
        """Initialize S3Client with bucket name and region.

        No network calls are made here. The boto3 session and S3 client are
        created lazily on first use, so constructing an instance does not
        require AWS credentials to be present.

        Args:
            bucket_name: Name of the S3 bucket to operate on.
            region_name: AWS region name. Defaults to 'us-east-1'.

        """
        self._bucket_name = bucket_name
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
        session = self._get_session()  # pragma: no cover
        return boto3.resource("s3", region_name=session.region_name)  # pragma: no cover

    def upload_file(self, local_path: str, key: str) -> bool:
        """Upload a file to the S3 bucket.

        Automatically uses multipart upload for files exceeding
        ``MULTIPART_THRESHOLD``. For smaller files, a standard single-part
        upload is performed.

        Args:
            local_path: The path to the local file to upload.
            key: The S3 object key (destination path within the bucket).

        Returns:
            True, if the upload was successful

        Raises:
            Value Error         : If key is empty or stars with a leading slash
            ClientError         : If the upload fails due to
                                AWS service errors (logged and caught).
            FileNotFoundError   : If the local_path does not exist.

        """
        if not key:
            msg = "Key cannot be empty"
            log.error(msg)
            raise ValueError(msg)
        if key.startswith("/"):
            msg = "S3 object key cannot start with a leading slash"
            log.error(msg)
            raise ValueError(msg)
        try:
            file_size = Path(local_path).stat().st_size
            if file_size > MULTIPART_THRESHOLD:
                return self._multipart_upload_file(local_path, key)
            log.info("Commencing file upload...")
            self._s3_client.upload_file(local_path, self._bucket_name, key)
            log.info("File Uploaded Sucessfully !")
        except ClientError:
            log.exception("Failed to upload", key=key)
            raise
        except FileNotFoundError:
            log.exception(
                "Unable to locate file. Check file path",
                local_path=local_path,
            )
            raise
        return True

    def upload_obj(self, file_obj: BinaryIO, key: str) -> bool:
        """Upload a binary file-like object to the S3 bucket.

        Automatically uses multipart upload for unseekable streams or
        objects exceeding ``MULTIPART_THRESHOLD``. For smaller seekable
        objects, a standard single-part upload is performed.

        Args:
            file_obj: A file-like object to upload. Must be opened in
                binary mode or be an in-memory bytes buffer
                (e.g., ``io.BytesIO``). Unseekable streams are supported
                but will always use multipart upload.
            key: The S3 object key (destination path within the bucket).

        Returns:
            True if the upload was successful.

        Raises:
            ValueError : If key is empty, starts with a leading slash,
                         or file_obje is not a valid binary file-like object.
            ClientError: If the upload fails due to
                         AWS service errors (logged and caught).

        """
        if not key:
            msg = "Key cannot be empty"
            log.error(msg)
            raise ValueError(msg)
        if key.startswith("/"):
            msg = "S3 object key cannot start with a leading slash"
            log.error(msg)
            raise ValueError(msg)
        self._validate_file_obj(file_obj=file_obj)

        if not file_obj.seekable():
            return self._multipart_upload_obj(file_obj, key)

        file_obj.seek(0, 2)
        file_size = file_obj.tell()
        file_obj.seek(0)

        if file_size > MULTIPART_THRESHOLD:  # pragma: no cover
            return self._multipart_upload_obj(file_obj, key)  # pragma: no cover

        try:
            log.info("Commencing object upload...")
            self._s3_client.upload_fileobj(file_obj, self._bucket_name, key)
        except ClientError:
            log.exception("Failed to upload object", key=key)
            raise
        return True

    def _multipart_upload_file(self, local_path: str, key: str) -> bool:
        """Upload a large file using multipart upload.

        Reads the file in chunks of ``MULTIPART_THRESHOLD`` size,
        uploading each as a separate part. Automatically aborts the
        multipart upload if any part fails.

        Args:
            local_path: The path to the local file to upload.
            key: The S3 object key (destination path within the bucket).

        Returns:
            True if the upload was successful.

        Raises:
            ClientError: If any stage of the multipart upload fails.

        """
        response = self.create_multipart_upload(key=key)
        upload_id = response["UploadId"]
        parts = []

        try:
            with Path(local_path).open("rb") as f:
                part_number = 1
                while chunk := f.read(MULTIPART_THRESHOLD):
                    part_response = self.upload_part(
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
                key=key,
                upload_id=upload_id,
            )
            self.abort_multipart_upload(key=key, upload_id=upload_id)
            raise

        self.complete_multipart_upload(key=key, upload_id=upload_id, parts=parts)
        return True

    def _multipart_upload_obj(
        self, file_obj: BinaryIO, key: str
    ) -> bool:  # pragma: no cover
        """Upload a large file-like object using multipart upload.

        Reads the file-like object in chunks of ``MULTIPART_THRESHOLD`` size,
        uploading each as a separate part. Automatically aborts the
        multipart upload if any part fails.

        Args:
            file_obj: A file-like object opened in binary mode.
            key: The S3 object key (destination path within the bucket).

        Returns:
            True if the upload was successful.

        Raises:
            ClientError: If any stage of the multipart upload fails.

        """
        response = self.create_multipart_upload(key=key)
        upload_id = response["UploadId"]
        parts = []

        try:
            part_number = 1
            while chunk := file_obj.read(MULTIPART_THRESHOLD):
                part_response = self.upload_part(
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
                key=key,
                upload_id=upload_id,
            )
            self.abort_multipart_upload(key=key, upload_id=upload_id)
            raise

        self.complete_multipart_upload(key=key, upload_id=upload_id, parts=parts)
        return True

    def create_bucket(
        self,
        bucket_name: str,
        region_name: str | None = None,
    ) -> bool:
        """Create an Amazon S3 bucket.

        The name of an Amazon S3 bucket must be unique across
        all regions of the AWS platform. If region is not specified,
        the bucket is created in the S3 default region (us-east-1).

        Args:
            bucket_name: Bucket to create.
            region_name: String region to create a bucket in,
                e.g., ``us-west-2``.

        Returns:
            True if the bucket was created successfully,
            False otherwise.

        Raises:
            ClientError: If the bucket creation fails due to
                AWS service errors (logged and caught).

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
        bucket_name: str,
        object_name: str,
        file_name: str,
    ) -> bool:
        """Download an S3 object to a file.

        The ``download_file`` method accepts the names of the bucket
        and object to download and the filename to save the file to.

        Args:
            bucket_name: The name of the bucket to download from.
            object_name: The name of the key to download from.
            file_name: The path to the file to download to.

        Returns:
            True if the file was downloaded successfully,
            False otherwise.

        Raises:
            ClientError: If the download fails due to
                AWS service errors (logged and caught).

        """
        try:
            log.info("Downloading S3 Object to a file...")
            self._s3_resource.meta.client.download_file(
                bucket_name, object_name, file_name
            )
        except ClientError:
            log.exception(
                "Failed to download file from Amazon S3 Bucket",
                bucket_name=bucket_name,
                object_name=object_name,
                file_name=file_name,
            )
            return False
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

    def list_files(self, prefix: str = "") -> list[str]:
        """List objects in the S3 bucket.

        The ``list_files`` method returns a list of object keys
        in the bucket, optionally filtered by a prefix.

        Args:
            prefix: The prefix to filter objects by. Defaults to
                empty string which returns all objects.

        Returns:
            A list of object keys matching the prefix.

        Raises:
            ClientError : If the listing fails due to
                        AWS service errors (logged and caught).

        """
        try:
            response = self._s3_client.list_objects_v2(
                Bucket=self._bucket_name,
                Prefix=prefix,
            )
            contents = response.get("Contents", [])
            return [obj["Key"] for obj in contents]
        except ClientError as exc:
            raise NotImplementedError from exc

    # Note: Batch deletion of multiple files could be added for efficiency
    def delete_file(
        self,
        bucket_name: str,
        object_name: str,
    ) -> bool:
        """Remove an object from a bucket.

        The behavior depends on the bucket's versioning state:

        - If bucket versioning is not enabled, the operation
          permanently deletes the object.
        - If bucket versioning is enabled, the operation inserts
          a delete marker, which becomes the current version of
          the object. To permanently delete an object in a
          versioned bucket, you must include the object's
          ``versionId`` in the request.

        You can delete objects by explicitly calling DELETE Object
        or calling (PutBucketLifecycle) to enable Amazon S3 to
        remove them for you.

        Args:
            bucket_name: The name of the bucket containing
                the object.
            object_name: The name of object to delete.

        Returns:
            A dict with ``DeleteMarker`` (bool) indicating
            whether the specified object version that was
            permanently deleted was a delete marker before
            deletion.

        Raises:
            ClientError: If the deletion fails due to
                AWS service errors (logged and caught).

        """
        try:
            log.info("Deleting S3 Object...")
            self._s3_client.delete_object(Bucket=bucket_name, Key=object_name)
        except ClientError:
            log.exception(
                "Failed to delete object from Amazon S3 Bucket",
                bucket_name=bucket_name,
                object_name=object_name,
            )
            return False
        return True

    # Helpers
    def _get_session(self) -> boto3.Session:
        """Create and return a boto3 Session using the AWS_REGION env var."""
        region = os.environ["AWS_REGION"]  # KeyError if missing (tests expect this)
        return boto3.Session(region_name=region)

    def get_session(self) -> boto3.Session:  # pragma: no cover
        """Return a boto3 Session for the configured region."""
        return self._get_session()  # pragma: no cover

    def _validate_file_obj(self, file_obj: BinaryIO) -> None:
        """Validate a file-like object for upload.

        Args:
            file_obj: The file-like object to validate.

        Raises:
            ValueError: If the file object is not readable or not in binary mode.

        """
        try:
            if not file_obj.readable():
                msg = "file_obj must be readable"
                log.error(msg)
                raise ValueError(msg)
            if isinstance(file_obj.read(0), str):
                msg = "file_obj must be opened in binary mode, not text mode"
                log.error(msg)
                raise TypeError(msg)
        except AttributeError as exc:
            msg = "file_obj must be a file-like object with a read() method"
            log.exception(msg)
            raise ValueError(msg) from exc

    def create_multipart_upload(self, key: str) -> dict[str, Any]:
        """Initiate a multipart upload and return the upload ID.

        Must be called before ``upload_part``. The ``UploadId`` in the
        returned response is required for all subsequent ``upload_part``,
        ``complete_multipart_upload``, and ``abort_multipart_upload`` calls.

        Note:
            You must either complete or abort the multipart upload after
            initiating it, otherwise AWS will continue charging for the
            stored parts indefinitely.

        Args:
            key: The S3 object key to upload to.

        Returns:
            The raw boto3 response dict containing UploadId

        Raises:
            ValueError  : If ``key`` is empty or starts with a leading slash.
            ClientError : If the request fails due to AWS service errors.

        """
        if not key:
            msg = "Key cannot be empty"
            log.error(msg)
            raise ValueError(msg)
        if key.startswith("/"):
            msg = "S3 object key cannot start with a leading slash"
            log.error(msg)
            raise ValueError(msg)
        kwargs = {
            "Bucket": self._bucket_name,
            "Key": key,
        }
        try:
            log.info("Initializing Multipart Upload...")
            response: dict[str, Any] = self._s3_client.create_multipart_upload(**kwargs)
            log.info("SUCCESS! Multipart Upload is completed")
        except ClientError:
            log.exception(
                "Failed Multipart Upload",
                key=key,
            )
            raise
        return response

    def upload_part(
        self,
        key: str,
        upload_id: str,
        part_number: int,
        body: bytes | BinaryIO,
    ) -> dict[str, object]:
        """Upload a single part in a multipart upload.

        Must be called after ``create_multipart_upload`` and before
        ``complete_multipart_upload`` or ``abort_multipart_upload``.
        ***Collect the returned ``ETag``*** from each part — you will need
        them to complete the upload.

        Part numbers must be between 1 and 10,000 inclusive and define
        the part's position in the final assembled object. Uploading a
        new part with an already-used part number overwrites the
        previous part.

        Args:
            key: The S3 object key that was supplied to
                ``create_multipart_upload``.
            upload_id: The upload ID returned by
                ``create_multipart_upload``.
            part_number: Position of this part in the object (1-10,000).
            body: The raw bytes or a binary file-like object for this
                part. All parts except the last must be at least 5 MB.

        Returns:
            The raw boto3 response dict. The ``'ETag'`` key is required
            when assembling the ``Parts`` list for
            ``complete_multipart_upload``::

                {
                    'ETag': '"d8c2eafd90c266e19ab9dcacc479f8af"',
                    'ChecksumCRC32': 'string',
                    'ChecksumSHA256': 'string',
                    ...
                }

        Raises:
            ValueError: If ``key`` is empty, starts with ``'/'``, or
                ``part_number`` is outside the 1-10,000 range.
            ClientError: If the upload fails due to AWS service errors,
                including ``NoSuchUpload`` (404) when the ``upload_id``
                is invalid or the multipart upload has already been
                completed or aborted.

        """
        if not key:
            msg = "Key cannot be empty"
            log.error(msg)
            raise ValueError(msg)
        if key.startswith("/"):
            msg = "S3 object key cannot start with a leading slash"
            log.error(msg)
            raise ValueError(msg)
        if not 1 <= part_number <= MAX_LIMIT:
            msg = "part_number must be in the range 1-10000 (inclusive)"
            log.error(msg)
            raise ValueError(msg)
        kwargs: dict[str, object] = {
            "Bucket": self._bucket_name,
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
        except ClientError:
            log.exception(
                "Failed to upload part",
                key=key,
                part_number=part_number,
                upload_id=upload_id,
            )
            raise
        return response

    def abort_multipart_upload(self, key: str, upload_id: str) -> bool:
        """Abort an in-progress multipart upload and discard all uploaded parts.

        Should be called when a multipart upload fails or is no longer needed.
        Once aborted, the ``upload_id`` becomes invalid and all stored parts
        are deleted, stopping AWS from charging for them.

        Args:
            key: The S3 object key supplied to ``create_multipart_upload``.
            upload_id: The upload ID returned by ``create_multipart_upload``.

        Returns:
            True if the multipart upload was successfully aborted,
            False otherwise.

        Raises:
            ValueError: If ``key`` is empty or starts with a leading slash.
            ClientError: If the request fails due to AWS service errors,
                including ``NoSuchUpload`` (404) if the ``upload_id`` is
                invalid or has already been completed or aborted.

        """
        if not key:
            msg = "Key cannot be empty"
            log.error(msg)
            raise ValueError(msg)
        if key.startswith("/"):
            msg = "S3 object key cannot start with a leading slash"
            log.error(msg)
            raise ValueError(msg)
        try:
            log.info("Aborting multipart upload...", key=key, upload_id=upload_id)
            self._s3_client.abort_multipart_upload(
                Bucket=self._bucket_name,
                Key=key,
                UploadId=upload_id,
            )
            log.info("SUCCESS! Multipart upload aborted", key=key, upload_id=upload_id)
        except ClientError:
            log.exception(
                "Failed to abort multipart upload",
                key=key,
                upload_id=upload_id,
            )
            return False
        return True

    def complete_multipart_upload(
        self,
        key: str,
        upload_id: str,
        parts: list[dict[str, Any]],
    ) -> bool:
        """Complete a multipart upload by assembling the uploaded parts.

        Must be called after all ``upload_part`` calls have succeeded.
        Parts are assembled in order of their ``PartNumber``, not the
        order they were uploaded.

        Args:
            key: The S3 object key supplied to ``create_multipart_upload``.
            upload_id: The upload ID returned by ``create_multipart_upload``.
            parts: List of dicts collected from each ``upload_part`` response,
                each containing ``'PartNumber'`` and ``'ETag'``::

                    [
                        {"PartNumber": 1, "ETag": '"abc123..."'},
                        {"PartNumber": 2, "ETag": '"def456..."'},
                    ]

        Returns:
            True if the uploaded parts were successfully assembled and uploaded,
            False otherwise.

        Raises:
            ValueError: If ``key`` is empty or starts with a leading slash.
            ClientError: If the request fails due to AWS service errors,
                including ``NoSuchUpload`` (404) if the ``upload_id`` is
                invalid or has already been completed or aborted.

        """
        if not key:
            msg = "Key cannot be empty"
            log.error(msg)
            raise ValueError(msg)
        if key.startswith("/"):
            msg = "S3 object key cannot start with a leading slash"
            log.error(msg)
            raise ValueError(msg)
        try:
            log.info("Assembling uploaded parts for multipart upload...", key=key)
            self._s3_client.complete_multipart_upload(
                Bucket=self._bucket_name,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            log.info("SUCESS! All parts are assesmbled and finished uploading")
        except ClientError:
            log.exception(
                "Failed to assemble parts and complete multipart upload",
                key=key,
                parts=parts,
            )
            return False
        return True


def get_client_impl(*, interactive: bool = False) -> S3Client:  # noqa: ARG001  # reserved for future CLI interactive-mode support; signature must match the factory callable protocol
    """Concrete factory that returns an S3Client instance."""
    region = os.environ.get("AWS_REGION", "us-east-1")
    # Handle case where environment variable is not properly expanded
    # (e.g., CircleCI sets it to literal "${AWS_REGION}" string)
    if region.startswith("${") and region.endswith("}"):
        region = "us-east-1"

    return S3Client(
        bucket_name=os.environ["AWS_BUCKET_NAME"],
        region_name=region,
    )
