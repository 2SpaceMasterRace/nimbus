"""Tests for S3Client.download_fileobj method."""

from pathlib import Path
from typing import TYPE_CHECKING

from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _client_error(op: str = "DownloadFileobj") -> ClientError:
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
    # mock _s3_resource.meta.client used by download_fileobj
    fake_resource = mocker.Mock()
    fake_resource.meta.client = fake_boto_client
    mocker.patch.object(
        S3Client,
        "_s3_resource",
        new_callable=lambda: property(lambda _: fake_resource),
    )
    return S3Client(bucket_name="ignored")


def test_download_fileobj_returns_true_on_success(
    mocker: "MockerFixture",
    tmp_path: Path,
) -> None:
    """Test that download_fileobj returns True on success."""
    fake_boto_client = mocker.Mock()
    c = _make_client(mocker, fake_boto_client)

    target = tmp_path / "out.bin"
    ok = c.download_fileobj("my-bucket", "my-key", str(target), "unused")

    assert ok is True
    args, _kwargs = fake_boto_client.download_fileobj.call_args
    assert args[0] == "my-bucket"
    assert args[1] == "my-key"
    assert hasattr(args[2], "write")  # file handle


def test_download_fileobj_returns_false_on_client_error(
    mocker: "MockerFixture",
    tmp_path: Path,
) -> None:
    """Test that download_fileobj returns False on client error."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.download_fileobj.side_effect = _client_error()
    c = _make_client(mocker, fake_boto_client)

    target = tmp_path / "out.bin"
    ok = c.download_fileobj("my-bucket", "my-key", str(target), "unused")

    assert ok is False
    fake_boto_client.download_fileobj.assert_called_once()
