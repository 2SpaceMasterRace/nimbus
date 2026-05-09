"""Tests for S3Client._get_session helper method."""

from typing import TYPE_CHECKING

import pytest
from aws_client_impl.s3_client import S3Client

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.unit


def test_get_session_uses_aws_region_env_var(mocker: "MockerFixture") -> None:
    """Test _get_session uses the region configured on the client."""
    fake_session = mocker.Mock()
    mock_boto3_session = mocker.patch(
        "aws_client_impl.s3_client.boto3.Session",
        return_value=fake_session,
    )

    c = S3Client(region_name="us-west-2")
    session = (
        c._get_session()
    )  # accessing private method directly to unit-test credential loading logic

    assert session is fake_session
    mock_boto3_session.assert_called_once_with(region_name="us-west-2")


def test_get_session_defaults_to_us_east_1(mocker: "MockerFixture") -> None:
    """Test _get_session defaults to the constructor's default region."""
    fake_session = mocker.Mock()
    mock_boto3_session = mocker.patch(
        "aws_client_impl.s3_client.boto3.Session",
        return_value=fake_session,
    )

    c = S3Client()
    session = (
        c._get_session()
    )  # accessing private method directly to unit-test credential loading logic

    assert session is fake_session
    mock_boto3_session.assert_called_once_with(region_name="us-east-1")


def test_get_session_uses_explicit_tenant_credentials(
    mocker: "MockerFixture",
) -> None:
    """Explicit tenant credentials should flow into the boto3 session."""
    fake_session = mocker.Mock()
    mock_boto3_session = mocker.patch(
        "aws_client_impl.s3_client.boto3.Session",
        return_value=fake_session,
    )

    c = S3Client(
        region_name="us-west-2",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="tenant-secret",  # noqa: S106
        aws_session_token="tenant-session-token",  # noqa: S106
    )
    session = c._get_session()

    assert session is fake_session
    mock_boto3_session.assert_called_once_with(
        region_name="us-west-2",
        aws_access_key_id="AKIA_TEST_SECRET",
        aws_secret_access_key="tenant-secret",  # noqa: S106
        aws_session_token="tenant-session-token",  # noqa: S106
    )
