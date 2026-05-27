"""Tests for S3Client._multipart_upload_file helper method."""

from pathlib import Path
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


def test_multipart_upload_file_completes_on_success(
    mocker: "MockerFixture",
    tmp_path: Path,
) -> None:
    """Test _multipart_upload_file completes multipart upload on success."""
    mocker.patch("aws_client_impl.s3_client.MULTIPART_THRESHOLD", 3)

    p = tmp_path / "f.bin"
    p.write_bytes(b"abcdef")  # 6 bytes -> 2 parts of 3

    c = S3Client(bucket_name="my-bucket")

    mocker.patch.object(c, "create_multipart_upload", return_value={"UploadId": "u1"})
    mocker.patch.object(
        c,
        "upload_part",
        side_effect=[{"ETag": "e1"}, {"ETag": "e2"}],
    )
    complete = mocker.patch.object(c, "complete_multipart_upload", return_value=True)
    abort = mocker.patch.object(c, "abort_multipart_upload", return_value=True)

    ok = c._multipart_upload_file(local_path=str(p), key="k")  # noqa: SLF001  # accessing private method directly to unit-test chunked upload logic

    assert ok is True
    abort.assert_not_called()
    complete.assert_called_once_with(
        key="k",
        upload_id="u1",
        parts=[
            {"PartNumber": 1, "ETag": "e1"},
            {"PartNumber": 2, "ETag": "e2"},
        ],
    )


def test_multipart_upload_file_aborts_on_part_failure(
    mocker: "MockerFixture",
    tmp_path: Path,
) -> None:
    """Test _multipart_upload_file aborts multipart upload when a part fails."""
    mocker.patch("aws_client_impl.s3_client.MULTIPART_THRESHOLD", 3)

    p = tmp_path / "f.bin"
    p.write_bytes(b"abcdef")

    c = S3Client(bucket_name="my-bucket")

    mocker.patch.object(c, "create_multipart_upload", return_value={"UploadId": "u1"})
    mocker.patch.object(c, "upload_part", side_effect=_client_error())
    complete = mocker.patch.object(c, "complete_multipart_upload", return_value=True)
    abort = mocker.patch.object(c, "abort_multipart_upload", return_value=True)

    with pytest.raises(ClientError):
        c._multipart_upload_file(local_path=str(p), key="k")  # noqa: SLF001  # accessing private method directly to unit-test chunked upload logic

    abort.assert_called_once_with(key="k", upload_id="u1")
    complete.assert_not_called()
