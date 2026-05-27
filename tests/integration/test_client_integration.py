"""Integration tests for dependency injection wiring.

This module verifies that importing the aws_client_impl package registers
the S3Client factory with the cloud_storage_client_api factory, so that
``get_client()`` returns a concrete implementation without callers needing
to know which class they receive.
"""

import pytest
from aws_client_impl.s3_client import S3Client
from cloud_storage_client_api.client import CloudStorageClient
from cloud_storage_client_api.factory import get_client

import aws_client_impl  # noqa: F401  — registers S3Client factory as side-effect

pytestmark = pytest.mark.integration


@pytest.mark.circleci
def test_dependency_injection_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing aws_client_impl registers S3Client with the interface factory."""
    monkeypatch.setenv("AWS_BUCKET_NAME", "test-bucket")
    client = get_client()
    assert isinstance(client, CloudStorageClient)
    assert isinstance(client, S3Client)


@pytest.mark.circleci
def test_factory_returns_distinct_instances(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_client() returns distinct CloudStorageClient instances on each call."""
    monkeypatch.setenv("AWS_BUCKET_NAME", "test-bucket")
    client1 = get_client()
    client2 = get_client()
    assert isinstance(client1, CloudStorageClient)
    assert isinstance(client2, CloudStorageClient)
    assert client1 is not client2
