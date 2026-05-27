"""Tests for S3Client.create_bucket method."""

from typing import TYPE_CHECKING

from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _client_error() -> ClientError:
    """Create a mock ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": "500", "Message": "boom"}},
        operation_name="CreateBucket",
    )


def _make_client(mocker: "MockerFixture", fake_boto_client: object) -> S3Client:
    """Return an S3Client whose boto3 low-level client is replaced by a mock."""
    fake_session = mocker.Mock()
    fake_session.client.return_value = fake_boto_client
    fake_session.region_name = "us-east-1"
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)
    # Also mock _s3_resource so bucket.wait_until_exists() doesn't call AWS
    fake_resource = mocker.Mock()
    mocker.patch.object(
        S3Client,
        "_s3_resource",
        new_callable=lambda: property(lambda _: fake_resource),
    )
    return S3Client(bucket_name="ignored")


def test_create_bucket_us_east_1_does_not_send_location_constraint(
    mocker: "MockerFixture",
) -> None:
    """Test that us-east-1 region does not send location constraint."""
    fake_boto_client = mocker.Mock()
    c = _make_client(mocker, fake_boto_client)

    ok = c.create_bucket(bucket_name="my-bucket", region_name="us-east-1")

    assert ok is True
    fake_boto_client.create_bucket.assert_called_once_with(Bucket="my-bucket")


def test_create_bucket_other_region_sends_location_constraint(
    mocker: "MockerFixture",
) -> None:
    """Test that other regions send location constraint."""
    fake_boto_client = mocker.Mock()
    c = _make_client(mocker, fake_boto_client)

    ok = c.create_bucket(bucket_name="my-bucket", region_name="us-west-2")

    assert ok is True
    fake_boto_client.create_bucket.assert_called_once_with(
        Bucket="my-bucket",
        CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
    )


def test_create_bucket_returns_false_on_client_error(mocker: "MockerFixture") -> None:
    """Test that create_bucket returns False on client error."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.create_bucket.side_effect = _client_error()
    c = _make_client(mocker, fake_boto_client)

    ok = c.create_bucket(bucket_name="my-bucket", region_name="us-west-2")

    assert ok is False
    fake_boto_client.create_bucket.assert_called_once()
