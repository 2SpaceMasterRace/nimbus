"""Tests for S3Client.delete_file method."""

from typing import TYPE_CHECKING

from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _client_error(op: str = "DeleteObject") -> ClientError:
    """Create a mock ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": "500", "Message": "boom"}},
        operation_name=op,
    )


def _make_client(mocker: "MockerFixture", fake_boto_client: object) -> S3Client:
    """Return an S3Client whose boto3 low-level client is replaced by a mock."""
    fake_session = mocker.Mock()
    fake_session.client.return_value = fake_boto_client
    fake_session.region_name = "us-east-1"
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)
    return S3Client(bucket_name="ignored")


def test_delete_file_returns_true_on_success(mocker: "MockerFixture") -> None:
    """Test that delete_file returns True when delete_object succeeds."""
    fake_boto_client = mocker.Mock()
    c = _make_client(mocker, fake_boto_client)

    ok = c.delete_file("my-bucket", "my-key")

    assert ok is True
    fake_boto_client.delete_object.assert_called_once_with(
        Bucket="my-bucket", Key="my-key"
    )


def test_delete_file_returns_false_on_client_error(mocker: "MockerFixture") -> None:
    """Test that delete_file returns False when client fails."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.delete_object.side_effect = _client_error()
    c = _make_client(mocker, fake_boto_client)

    ok = c.delete_file("my-bucket", "my-key")

    assert ok is False
