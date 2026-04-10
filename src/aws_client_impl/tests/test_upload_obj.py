"""Tests for S3Client.upload_obj method."""

import io
from typing import TYPE_CHECKING

import pytest
from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError
from cloud_storage_api import ObjectInfo, StorageBackendError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _client_error() -> ClientError:
    """Create a mock ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": "500", "Message": "boom"}},
        operation_name="UploadFileObj",
    )


def _stub_object_info(key: str = "k") -> ObjectInfo:
    """Return a minimal ObjectInfo for use in test stubs."""
    return ObjectInfo(object_name=key)


def test_upload_obj_raises_value_error_on_empty_key(
    mocker: "MockerFixture",  # noqa: ARG001  # pytest-mock fixture injected by pytest; not used directly in this test body
) -> None:
    """Test upload_obj raises ValueError when remote_path is empty."""
    c = S3Client()

    with pytest.raises(ValueError, match="Key cannot be empty"):
        c.upload_obj(container="my-bucket", file_obj=io.BytesIO(b"abc"), remote_path="")


def test_upload_obj_raises_value_error_on_leading_slash(
    mocker: "MockerFixture",  # noqa: ARG001  # pytest-mock fixture injected by pytest; not used directly in this test body
) -> None:
    """Test upload_obj raises ValueError when remote_path starts with '/'."""
    c = S3Client()

    with pytest.raises(ValueError, match="leading slash"):
        c.upload_obj(
            container="my-bucket", file_obj=io.BytesIO(b"abc"), remote_path="/bad"
        )


def test_upload_obj_calls_singlepart_upload_when_small(
    mocker: "MockerFixture",
) -> None:
    """Test upload_obj uses client.upload_fileobj for small seekable objects."""
    fake_session = mocker.Mock()
    fake_client = mocker.Mock()
    fake_session.client.return_value = fake_client
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)

    mocker.patch("aws_client_impl.s3_client.MULTIPART_THRESHOLD", 10_000_000)

    expected = _stub_object_info("k")
    mocker.patch.object(S3Client, "_head_object_info", return_value=expected)

    c = S3Client()
    buf = io.BytesIO(b"hello")
    result = c.upload_obj(container="my-bucket", file_obj=buf, remote_path="k")

    assert isinstance(result, ObjectInfo)
    assert result.object_name == "k"
    fake_client.upload_fileobj.assert_called_once()
    args = fake_client.upload_fileobj.call_args[0]
    assert args[1] == "my-bucket"
    assert args[2] == "k"


def test_upload_obj_calls_multipart_when_unseekable(
    mocker: "MockerFixture",
) -> None:
    """Test upload_obj forces multipart upload when file_obj is unseekable."""
    fake_session = mocker.Mock()
    fake_client = mocker.Mock()
    fake_session.client.return_value = fake_client
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)

    class Unseekable:
        def readable(self) -> bool:
            return True

        def read(self, n: int = -1) -> bytes:  # noqa: ARG002  # n is required by BinaryIO.read() protocol but intentionally unused in this stub
            return b""  # no content needed for this unit test

        def seekable(self) -> bool:
            return False

    c = S3Client()
    expected = _stub_object_info("k")
    mp = mocker.patch.object(c, "_multipart_upload_obj", return_value=expected)

    result = c.upload_obj(container="my-bucket", file_obj=Unseekable(), remote_path="k")  # type: ignore[arg-type]  # intentionally passing a duck-typed stub that doesn't satisfy BinaryIO formally, to exercise the unseekable upload path

    assert isinstance(result, ObjectInfo)
    mp.assert_called_once()
    fake_client.upload_fileobj.assert_not_called()


def test_upload_obj_raises_client_error_on_upload_failure(
    mocker: "MockerFixture",
) -> None:
    """Test upload_obj re-raises ClientError when upload_fileobj fails."""
    fake_session = mocker.Mock()
    fake_client = mocker.Mock()
    fake_client.upload_fileobj.side_effect = _client_error()
    fake_session.client.return_value = fake_client
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)

    mocker.patch("aws_client_impl.s3_client.MULTIPART_THRESHOLD", 10_000_000)

    c = S3Client()
    buf = io.BytesIO(b"hello")

    with pytest.raises(StorageBackendError):
        c.upload_obj(container="my-bucket", file_obj=buf, remote_path="k")

    fake_client.upload_fileobj.assert_called_once()
