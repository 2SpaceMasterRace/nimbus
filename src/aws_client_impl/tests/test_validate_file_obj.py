"""Tests for S3Client._validate_file_obj helper method."""

import io
from typing import TYPE_CHECKING

import pytest
from aws_client_impl.s3_client import S3Client
from cloud_storage_api import InvalidFileObjectError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.unit


def test_validate_file_obj_raises_value_error_when_not_readable(
    mocker: "MockerFixture",  # noqa: ARG001  # pytest-mock fixture injected by pytest; not used directly in this test body
) -> None:
    """Test _validate_file_obj raises ValueError when file_obj is not readable."""
    c = S3Client()

    class NotReadable:
        def readable(self) -> bool:
            return False

        # n is part of the BinaryIO.read() signature; unused in this stub.
        def read(self, n: int = -1) -> bytes:
            return b""

    with pytest.raises(InvalidFileObjectError, match="readable"):
        c._validate_file_obj(file_obj=NotReadable())  # type: ignore[arg-type]  # passing intentionally invalid type to test validation; accessing private method directly to unit-test it


def test_validate_file_obj_raises_type_error_when_text_mode(
    mocker: "MockerFixture",  # noqa: ARG001  # pytest-mock fixture injected by pytest; not used directly in this test body
) -> None:
    """Test _validate_file_obj raises TypeError when file_obj appears to be text."""
    c = S3Client()

    class TextLike:
        def readable(self) -> bool:
            return True

        def read(self, n: int = -1) -> str:
            # Code checks: isinstance(file_obj.read(0), str)
            return "" if n == 0 else "hello"

    with pytest.raises(InvalidFileObjectError, match="binary mode"):
        c._validate_file_obj(file_obj=TextLike())  # type: ignore[arg-type]  # passing intentionally invalid type to test validation; accessing private method directly to unit-test it


def test_validate_file_obj_raises_value_error_when_not_file_like(
    mocker: "MockerFixture",  # noqa: ARG001  # pytest-mock fixture injected by pytest; not used directly in this test body
) -> None:
    """Test _validate_file_obj raises ValueError when file_obj lacks read()."""
    c = S3Client()

    class NotAFile:
        pass

    with pytest.raises(InvalidFileObjectError, match="file-like object"):
        c._validate_file_obj(file_obj=NotAFile())  # type: ignore[arg-type]  # passing intentionally invalid type to test validation; accessing private method directly to unit-test it


def test_validate_file_obj_passes_for_binary_file_like(
    mocker: "MockerFixture",  # noqa: ARG001  # pytest-mock fixture injected by pytest; not used directly in this test body
) -> None:
    """Test _validate_file_obj does not raise for a valid binary file-like object."""
    c = S3Client()

    buf = io.BytesIO(b"abc")  # readable and binary
    # Should not raise:
    c._validate_file_obj(
        file_obj=buf
    )  # accessing private method directly to unit-test internal validation logic
