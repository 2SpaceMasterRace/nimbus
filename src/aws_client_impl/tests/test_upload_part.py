"""Tests for S3Client.upload_part method."""

import io
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
        operation_name="UploadPart",
    )


def _make_client(mocker: "MockerFixture", fake_boto_client: object) -> S3Client:
    """Return an S3Client whose boto3 low-level client is replaced by a mock."""
    fake_session = mocker.Mock()
    fake_session.client.return_value = fake_boto_client
    fake_session.region_name = "us-east-1"
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)
    return S3Client(bucket_name="my-bucket")


def test_upload_part_raises_value_error_on_empty_key(mocker: "MockerFixture") -> None:
    """Test upload_part raises ValueError when key is empty."""
    c = _make_client(mocker, mocker.Mock())

    with pytest.raises(ValueError, match="Key cannot be empty"):
        c.upload_part(key="", upload_id="u", part_number=1, body=b"x")


def test_upload_part_raises_value_error_on_leading_slash(
    mocker: "MockerFixture",
) -> None:
    """Test upload_part raises ValueError when key starts with '/'."""
    c = _make_client(mocker, mocker.Mock())

    with pytest.raises(ValueError, match="leading slash"):
        c.upload_part(key="/bad", upload_id="u", part_number=1, body=b"x")


def test_upload_part_raises_value_error_on_invalid_part_number(
    mocker: "MockerFixture",
) -> None:
    """Test upload_part raises ValueError when part_number is out of range."""
    c = _make_client(mocker, mocker.Mock())

    with pytest.raises(ValueError, match="part_number"):
        c.upload_part(key="k", upload_id="u", part_number=0, body=b"x")

    with pytest.raises(ValueError, match="part_number"):
        c.upload_part(key="k", upload_id="u", part_number=10001, body=b"x")


def test_upload_part_returns_response_on_success(mocker: "MockerFixture") -> None:
    """Test upload_part returns boto3 response on success."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.upload_part.return_value = {"ETag": "e1"}
    c = _make_client(mocker, fake_boto_client)

    resp = c.upload_part(key="k", upload_id="u", part_number=1, body=b"abc")

    assert resp == {"ETag": "e1"}
    fake_boto_client.upload_part.assert_called_once_with(
        Bucket="my-bucket",
        Key="k",
        PartNumber=1,
        UploadId="u",
        Body=b"abc",
    )


def test_upload_part_raises_client_error_on_failure(mocker: "MockerFixture") -> None:
    """Test upload_part re-raises ClientError on failure."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.upload_part.side_effect = _client_error()
    c = _make_client(mocker, fake_boto_client)

    with pytest.raises(ClientError):
        c.upload_part(key="k", upload_id="u", part_number=1, body=io.BytesIO(b"abc"))

    fake_boto_client.upload_part.assert_called_once()
