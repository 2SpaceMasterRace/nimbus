"""Domain exceptions for cloud storage operations.

The abstract API raises these provider-agnostic exceptions so callers can handle
errors without depending on HTTP, boto3, or any concrete implementation.
"""

from __future__ import annotations


class CloudStorageError(Exception):
    """Base exception for cloud storage domain errors."""


class InvalidContainerError(CloudStorageError, ValueError):
    """Raised when a container or bucket name is invalid."""


class ContainerNotFoundError(CloudStorageError, FileNotFoundError):
    """Raised when a requested container or bucket does not exist."""


class InvalidObjectNameError(CloudStorageError, ValueError):
    """Raised when an object key or path is invalid."""


class InvalidFileObjectError(CloudStorageError, ValueError):
    """Raised when a provided file object cannot be uploaded."""


class ObjectNotFoundError(CloudStorageError, FileNotFoundError):
    """Raised when a requested object does not exist."""


class StorageBackendError(CloudStorageError):
    """Raised when the underlying storage provider fails unexpectedly."""
