"""Entry point for the OSPSD Team 2 application.

Demonstrates the S3 cloud-storage client by creating a client,
listing files in a bucket, and printing the results.
"""

from __future__ import annotations

from typing import Any

import structlog
from cloud_storage_client_api.factory import get_client

import aws_client_impl  # noqa: F401  # triggers dependency injection

log: Any = structlog.get_logger()


def main() -> None:
    """Create a cloud storage client and demonstrate S3 operations."""
    client = get_client()
    log.info("Created cloud storage client")

    files = client.list_files("")
    log.info("Listed files in bucket", count=len(files))
    for key in files[:10]:
        log.info("Found file", key=key)

    log.info("Demo complete")


if __name__ == "__main__":
    main()
