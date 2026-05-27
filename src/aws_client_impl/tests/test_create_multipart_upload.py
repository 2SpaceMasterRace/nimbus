"""Tests for S3Client.create_multipart_upload method."""

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
        operation_name="CreateMultipartUpload",
    )


def _make_client(mocker: "MockerFixture", fake_boto_client: object) -> S3Client:
    """Return an S3Client whose boto3 low-level client is replaced by a mock."""
    fake_session = mocker.Mock()
    fake_session.client.return_value = fake_boto_client
    fake_session.region_name = "us-east-1"
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)
    return S3Client(bucket_name="my-bucket")


def test_create_multipart_upload_raises_value_error_on_empty_key(
    mocker: "MockerFixture",
) -> None:
    """Test create_multipart_upload raises ValueError when key is empty."""
    c = _make_client(mocker, mocker.Mock())

    with pytest.raises(ValueError, match="Key cannot be empty"):
        c.create_multipart_upload(key="")


def test_create_multipart_upload_raises_value_error_on_leading_slash(
    mocker: "MockerFixture",
) -> None:
    """Test create_multipart_upload raises ValueError when key starts with '/'."""
    c = _make_client(mocker, mocker.Mock())

    with pytest.raises(ValueError, match="leading slash"):
        c.create_multipart_upload(key="/bad")


def test_create_multipart_upload_returns_response_on_success(
    mocker: "MockerFixture",
) -> None:
    """Test create_multipart_upload returns boto3 response on success."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.create_multipart_upload.return_value = {"UploadId": "u1"}
    c = _make_client(mocker, fake_boto_client)

    resp = c.create_multipart_upload(key="k")

    assert resp == {"UploadId": "u1"}
    fake_boto_client.create_multipart_upload.assert_called_once_with(
        Bucket="my-bucket",
        Key="k",
    )


def test_create_multipart_upload_raises_client_error_on_failure(
    mocker: "MockerFixture",
) -> None:
    """Test create_multipart_upload re-raises ClientError on failure."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.create_multipart_upload.side_effect = _client_error()
    c = _make_client(mocker, fake_boto_client)

    with pytest.raises(ClientError):
        c.create_multipart_upload(key="k")

    fake_boto_client.create_multipart_upload.assert_called_once()
