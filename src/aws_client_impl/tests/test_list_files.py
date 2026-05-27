"""Tests for S3Client.list_files method."""

from typing import TYPE_CHECKING

import pytest
from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _client_error() -> ClientError:
    """Create a mock ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": "500", "Message": "boom"}},
        operation_name="ListObjectsV2",
    )


def _make_client(mocker: "MockerFixture", fake_boto_client: object) -> S3Client:
    """Return an S3Client whose boto3 low-level client is replaced by a mock."""
    fake_session = mocker.Mock()
    fake_session.client.return_value = fake_boto_client
    fake_session.region_name = "us-east-1"
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)
    return S3Client(bucket_name="my-bucket")


def test_list_files_returns_keys_on_success(mocker: "MockerFixture") -> None:
    """Test list_files returns object keys when Contents is present."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.list_objects_v2.return_value = {
        "Contents": [{"Key": "a.txt"}, {"Key": "b.txt"}],
    }
    c = _make_client(mocker, fake_boto_client)

    keys = c.list_files(prefix="")

    assert keys == ["a.txt", "b.txt"]
    fake_boto_client.list_objects_v2.assert_called_once_with(
        Bucket="my-bucket",
        Prefix="",
    )


def test_list_files_returns_empty_list_when_no_contents(
    mocker: "MockerFixture",
) -> None:
    """Test list_files returns [] when Contents is missing."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.list_objects_v2.return_value = {}
    c = _make_client(mocker, fake_boto_client)

    keys = c.list_files(prefix="x/")

    assert keys == []
    fake_boto_client.list_objects_v2.assert_called_once_with(
        Bucket="my-bucket",
        Prefix="x/",
    )


def test_list_files_raises_notimplementederror_on_client_error(
    mocker: "MockerFixture",
) -> None:
    """Test list_files raises NotImplementedError on ClientError."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.list_objects_v2.side_effect = _client_error()
    c = _make_client(mocker, fake_boto_client)

    with pytest.raises(NotImplementedError):
        c.list_files(prefix="")

    fake_boto_client.list_objects_v2.assert_called_once()
