"""AWS client implementation package.

Provides ``S3Client``, the boto3-backed implementation of
``CloudStorageClient``, and a ``get_client_impl`` factory function that
constructs a configured instance from environment variables.
"""

from aws_client_impl.s3_client import get_client_impl

__all__ = ["get_client_impl"]
