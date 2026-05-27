"""Tests for S3Client._get_session helper method."""

import os
from typing import TYPE_CHECKING

import pytest
from aws_client_impl.s3_client import S3Client

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def test_get_session_uses_aws_region_env_var(mocker: "MockerFixture") -> None:
    """Test _get_session uses AWS_REGION environment variable."""
    mocker.patch.dict(os.environ, {"AWS_REGION": "us-west-2"}, clear=True)

    fake_session = mocker.Mock()
    mock_boto3_session = mocker.patch(
        "aws_client_impl.s3_client.boto3.Session",
        return_value=fake_session,
    )

    c = S3Client(bucket_name="ignored")
    session = c._get_session()  # noqa: SLF001  # accessing private method directly to unit-test credential loading logic

    assert session is fake_session
    mock_boto3_session.assert_called_once_with(region_name="us-west-2")


def test_get_session_raises_key_error_when_env_missing(mocker: "MockerFixture") -> None:
    """Test _get_session raises KeyError when AWS_REGION is not set."""
    mocker.patch.dict(os.environ, {}, clear=True)

    c = S3Client(bucket_name="ignored")
    with pytest.raises(KeyError):
        c._get_session()  # noqa: SLF001  # accessing private method directly to unit-test credential loading logic
