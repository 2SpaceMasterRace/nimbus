"""Entry point for the OSPSD Team 2 application.

Demonstrates the S3 cloud-storage client by creating a client,
listing files in a bucket, and printing the results.
"""

from __future__ import annotations

import os
from typing import Any

import structlog
from aws_client_impl.s3_client import get_client_impl

log: Any = structlog.get_logger()


def main() -> None:
    """Create a cloud storage client and demonstrate S3 operations."""
    client = get_client_impl()
    container = os.environ["AWS_BUCKET_NAME"]
    log.info("Created cloud storage client")

    files = client.list_files(container, "")
    log.info("Listed files in bucket", container=container, count=len(files))
    for info in files[:10]:
        log.info("Found file", key=info.object_name)

    log.info("Demo complete")


if __name__ == "__main__":
    main()
