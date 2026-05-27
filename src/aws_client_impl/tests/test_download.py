"""Tests for S3Client.download_file method."""

from typing import TYPE_CHECKING

from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _client_error(op: str = "DownloadFile") -> ClientError:
    """Create a mock ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": "500", "Message": "boom"}},
        operation_name=op,
    )


def _make_client(mocker: "MockerFixture", fake_boto_client: object) -> S3Client:
    """Return an S3Client whose boto3 clients are replaced by mocks."""
    fake_session = mocker.Mock()
    fake_session.client.return_value = fake_boto_client
    fake_session.region_name = "us-east-1"
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)
    # _s3_resource.meta.client is used for download_file; mock it via the resource prop
    fake_resource = mocker.Mock()
    fake_resource.meta.client = fake_boto_client
    mocker.patch.object(
        S3Client,
        "_s3_resource",
        new_callable=lambda: property(lambda _: fake_resource),
    )
    return S3Client(bucket_name="ignored")


def test_download_file_returns_true_on_success(mocker: "MockerFixture") -> None:
    """Test download_file returns True on success."""
    fake_boto_client = mocker.Mock()
    c = _make_client(mocker, fake_boto_client)

    ok = c.download_file("my-bucket", "my-key", "local.txt")

    assert ok is True
    fake_boto_client.download_file.assert_called_once_with(
        "my-bucket", "my-key", "local.txt"
    )


def test_download_file_returns_false_on_client_error(mocker: "MockerFixture") -> None:
    """Test download_file returns False on ClientError."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.download_file.side_effect = _client_error()
    c = _make_client(mocker, fake_boto_client)

    ok = c.download_file("my-bucket", "my-key", "local.txt")

    assert ok is False
    fake_boto_client.download_file.assert_called_once()
