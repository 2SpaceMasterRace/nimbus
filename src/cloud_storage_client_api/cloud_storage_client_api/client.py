"""Abstract base class for cloud storage clients.

This module defines the public interface contract. It has zero coupling to any
concrete implementation, SDK, or framework. Consumers depend only on this ABC;
they never import from aws_client_impl or any other implementation package.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO


class CloudStorageClient(ABC):
    """Abstract base class defining the contract for a cloud storage client.

    All concrete implementations (e.g. S3Client) must subclass this class and
    implement every abstract method. The contract is intentionally minimal and
    cloud-provider-agnostic: bucket names, regions, and SDK details are
    implementation concerns, not interface concerns.
    """

    @abstractmethod
    def upload_file(
        self,
        container: str,
        local_path: str,
        remote_path: str,
    ) -> bool:
        """Upload a file to cloud storage.

        Args:
            container: Name of the container / bucket to upload to.
            local_path: Path to the local file.
            remote_path: The destination object key / path within the bucket.

        Returns:
            True if the upload was successful.

        Raises:
            InvalidContainerError: If container is empty or otherwise invalid.
            InvalidObjectNameError: If remote_path is empty or otherwise invalid.
            FileNotFoundError: If local_path does not exist.
            StorageBackendError: If the backing storage provider fails.

        """
        raise NotImplementedError

    @abstractmethod
    def upload_obj(
        self,
        container: str,
        file_obj: BinaryIO,
        remote_path: str,
    ) -> bool:
        """Upload a binary file-like object to cloud storage.

        Args:
            container: Name of the container / bucket to upload to.
            file_obj: A file-like object opened in binary mode.
            remote_path: Destination object key / path within the bucket.

        Returns:
            True if the upload was successful.

        Raises:
            InvalidContainerError: If container is empty or otherwise invalid.
            InvalidObjectNameError: If remote_path is empty or otherwise invalid.
            InvalidFileObjectError: If file_obj is invalid.
            StorageBackendError: If the backing storage provider fails.

        """
        raise NotImplementedError

    @abstractmethod
    def download_file(
        self,
        container: str,
        object_name: str,
        file_name: str,
    ) -> bool:
        """Download a file from cloud storage.

        Args:
            container: Name of the container / bucket to download from.
            object_name: Key of the object to download.
            file_name: Local filesystem path to write the downloaded file to.

        Returns:
            True if the download was successful.

        Raises:
            InvalidContainerError: If container is empty or otherwise invalid.
            InvalidObjectNameError: If object_name is empty or otherwise invalid.
            ObjectNotFoundError: If the requested object does not exist.
            StorageBackendError: If the backing storage provider fails.

        """
        raise NotImplementedError

    @abstractmethod
    def list_files(self, container: str, prefix: str = "") -> list[str]:
        """List files in cloud storage.

        Args:
            container: Name of the container / bucket to list from.
            prefix: Optional prefix to filter results. Defaults to "" (all files).

        Returns:
            A list of object keys / paths matching the prefix.

        Raises:
            InvalidContainerError: If container is empty or otherwise invalid.
            StorageBackendError: If the backing storage provider fails.

        """
        raise NotImplementedError

    @abstractmethod
    def delete_file(
        self,
        container: str,
        object_name: str,
    ) -> bool:
        """Delete a file from cloud storage.

        Args:
            container: Name of the container / bucket containing the object.
            object_name: Key of the object to delete.

        Returns:
            True if the deletion was successful.

        Raises:
            InvalidContainerError: If container is empty or otherwise invalid.
            InvalidObjectNameError: If object_name is empty or otherwise invalid.
            ObjectNotFoundError: If the requested object does not exist.
            StorageBackendError: If the backing storage provider fails.

        """
        raise NotImplementedError
