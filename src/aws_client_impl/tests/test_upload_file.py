"""Tests for S3Client.upload_file method."""

from typing import TYPE_CHECKING

import pytest
from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError
from cloud_storage_api import LocalFileAccessError, ObjectInfo, StorageBackendError

from aws_client_impl import s3_client as s3_mod

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.unit


def _client_error() -> ClientError:
    """Create a mock ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": "500", "Message": "boom"}},
        operation_name="UploadFile",
    )


def _stub_object_info(key: str = "ok/key") -> ObjectInfo:
    """Return a minimal ObjectInfo for use in test stubs."""
    return ObjectInfo(object_name=key)


def test_upload_file_raises_value_error_on_empty_key(mocker: "MockerFixture") -> None:  # noqa: ARG001  # pytest-mock fixture injected by pytest; not used directly in this test body
    """Test upload_file raises ValueError when remote_path is empty."""
    c = S3Client()

    with pytest.raises(ValueError, match="Key cannot be empty"):
        c.upload_file(container="my-bucket", local_path="local.txt", remote_path="")


def test_upload_file_raises_value_error_on_leading_slash(
    mocker: "MockerFixture",  # noqa: ARG001  # pytest-mock fixture injected by pytest; not used directly in this test body
) -> None:
    """Test upload_file raises ValueError when remote_path starts with '/'."""
    c = S3Client()

    with pytest.raises(ValueError, match="leading slash"):
        c.upload_file(container="my-bucket", local_path="local.txt", remote_path="/bad")


def test_upload_file_calls_singlepart_upload_when_small(
    mocker: "MockerFixture",
) -> None:
    """Test upload_file uses client.upload_file for small files."""
    fake_session = mocker.Mock()
    fake_client = mocker.Mock()
    fake_session.client.return_value = fake_client
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)

    fake_stat = mocker.Mock()
    fake_stat.st_size = s3_mod.MULTIPART_THRESHOLD - 1
    mocker.patch("aws_client_impl.s3_client.Path.stat", return_value=fake_stat)

    expected = _stub_object_info("ok/key")
    mocker.patch.object(S3Client, "_head_object_info", return_value=expected)

    c = S3Client()
    result = c.upload_file(
        container="my-bucket", local_path="local.txt", remote_path="ok/key"
    )

    assert isinstance(result, ObjectInfo)
    assert result.object_name == "ok/key"
    fake_client.upload_file.assert_called_once_with("local.txt", "my-bucket", "ok/key")


def test_upload_file_calls_multipart_upload_when_large(
    mocker: "MockerFixture",
) -> None:
    """Test upload_file uses multipart upload for large files."""
    fake_session = mocker.Mock()
    fake_client = mocker.Mock()
    fake_session.client.return_value = fake_client
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)

    fake_stat = mocker.Mock()
    fake_stat.st_size = s3_mod.MULTIPART_THRESHOLD + 1
    mocker.patch("aws_client_impl.s3_client.Path.stat", return_value=fake_stat)

    c = S3Client()
    expected = _stub_object_info("big/key")
    mp = mocker.patch.object(c, "_multipart_upload_file", return_value=expected)

    result = c.upload_file(
        container="my-bucket", local_path="big.bin", remote_path="big/key"
    )

    assert isinstance(result, ObjectInfo)
    assert result.object_name == "big/key"
    mp.assert_called_once_with("my-bucket", "big.bin", "big/key")
    fake_client.upload_file.assert_not_called()


def test_upload_file_raises_local_file_access_error(mocker: "MockerFixture") -> None:
    """Test upload_file raises LocalFileAccessError when local file is missing."""
    fake_session = mocker.Mock()
    fake_client = mocker.Mock()
    fake_session.client.return_value = fake_client
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)

    mocker.patch(
        "aws_client_impl.s3_client.Path.stat",
        side_effect=FileNotFoundError,
    )

    c = S3Client()

    with pytest.raises(LocalFileAccessError):
        c.upload_file(container="my-bucket", local_path="missing.bin", remote_path="k")


def test_upload_file_raises_client_error_on_upload_failure(
    mocker: "MockerFixture",
) -> None:
    """Test upload_file re-raises ClientError when upload fails."""
    fake_session = mocker.Mock()
    fake_client = mocker.Mock()
    fake_client.upload_file.side_effect = _client_error()
    fake_session.client.return_value = fake_client
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)

    fake_stat = mocker.Mock()
    fake_stat.st_size = s3_mod.MULTIPART_THRESHOLD - 1
    mocker.patch("aws_client_impl.s3_client.Path.stat", return_value=fake_stat)

    c = S3Client()

    with pytest.raises(StorageBackendError):
        c.upload_file(
            container="my-bucket", local_path="local.txt", remote_path="ok/key"
        )

    fake_client.upload_file.assert_called_once()
