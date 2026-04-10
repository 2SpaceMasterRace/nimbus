"""Integration tests for client wiring.

This module verifies that the ``get_client_impl`` factory in aws_client_impl
returns a concrete ``CloudStorageClient`` instance without requiring the old
DI factory from cloud_storage_client_api.
"""

import pytest
from aws_client_impl.s3_client import S3Client, get_client_impl
from cloud_storage_api import CloudStorageClient

pytestmark = pytest.mark.integration


@pytest.mark.circleci
def test_get_client_impl_returns_s3_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_client_impl returns an S3Client that satisfies CloudStorageClient."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    client = get_client_impl()
    assert isinstance(client, CloudStorageClient)
    assert isinstance(client, S3Client)


@pytest.mark.circleci
def test_factory_returns_distinct_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_client_impl() returns distinct CloudStorageClient instances on each call."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    client1 = get_client_impl()
    client2 = get_client_impl()
    assert isinstance(client1, CloudStorageClient)
    assert isinstance(client2, CloudStorageClient)
    assert client1 is not client2
