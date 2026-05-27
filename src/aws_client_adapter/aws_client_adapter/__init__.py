"""HTTP-backed cloud storage adapter package.

Provides ``CloudStorageServiceAdapter``, which implements
``CloudStorageClient`` by forwarding operations through the generated
HTTP service client.
"""

from aws_client_adapter.service_adapter import (
    CloudStorageServiceAdapter as CloudStorageServiceAdapter,
)
from aws_client_adapter.service_adapter import get_client_impl as get_client_impl
