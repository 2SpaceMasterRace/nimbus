"""Tests for S3Client.delete_bucket method."""

from typing import TYPE_CHECKING

from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _client_error() -> ClientError:
    """Create a mock ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": "500", "Message": "boom"}},
        operation_name="DeleteBucket",
    )


def _make_client(
    mocker: "MockerFixture",
    fake_boto_client: object,
    fake_resource: object,
) -> S3Client:
    """Return an S3Client with mocked boto3 low-level client and resource."""
    fake_session = mocker.Mock()
    fake_session.client.return_value = fake_boto_client
    fake_session.region_name = "us-east-1"
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)
    mocker.patch.object(
        S3Client,
        "_s3_resource",
        new_callable=lambda: property(lambda _: fake_resource),
    )
    return S3Client(bucket_name="ignored")


def test_delete_bucket_returns_true_on_success(mocker: "MockerFixture") -> None:
    """Test delete_bucket returns True on success."""
    fake_bucket = mocker.Mock()
    fake_resource = mocker.Mock()
    fake_resource.Bucket.return_value = fake_bucket
    c = _make_client(mocker, mocker.Mock(), fake_resource)

    ok = c.delete_bucket(bucket_name="my-bucket")

    assert ok is True
    fake_resource.Bucket.assert_called_once_with("my-bucket")
    fake_bucket.delete.assert_called_once_with()
    fake_bucket.wait_until_not_exists.assert_called_once_with()


def test_delete_bucket_returns_false_on_client_error(mocker: "MockerFixture") -> None:
    """Test delete_bucket returns False on ClientError."""
    fake_bucket = mocker.Mock()
    fake_bucket.delete.side_effect = _client_error()
    fake_resource = mocker.Mock()
    fake_resource.Bucket.return_value = fake_bucket
    c = _make_client(mocker, mocker.Mock(), fake_resource)

    ok = c.delete_bucket(bucket_name="my-bucket")

    assert ok is False
    fake_resource.Bucket.assert_called_once_with("my-bucket")
    fake_bucket.delete.assert_called_once_with()
