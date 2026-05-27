"""Tests for S3Client.list_files method."""

from typing import TYPE_CHECKING

import pytest
from aws_client_impl.s3_client import S3Client
from botocore.exceptions import ClientError
from cloud_storage_api import ObjectInfo, StorageBackendError

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

pytestmark = pytest.mark.unit


def _client_error() -> ClientError:
    """Create a mock ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": "500", "Message": "boom"}},
        operation_name="ListObjectsV2",
    )


def _make_client(mocker: "MockerFixture", fake_boto_client: object) -> S3Client:
    """Return an S3Client whose boto3 low-level client is replaced by a mock."""
    fake_session = mocker.Mock()
    fake_session.client.return_value = fake_boto_client
    fake_session.region_name = "us-east-1"
    mocker.patch.object(S3Client, "_get_session", return_value=fake_session)
    return S3Client()


def test_list_files_returns_object_info_on_success(mocker: "MockerFixture") -> None:
    """Test list_files returns ObjectInfo instances when Contents is present."""
    fake_boto_client = mocker.Mock()
    fake_paginator = mocker.Mock()
    fake_paginator.paginate.return_value = [
        {"Contents": [{"Key": "a.txt", "Size": 10}, {"Key": "b.txt", "Size": 20}]}
    ]
    fake_boto_client.get_paginator.return_value = fake_paginator
    c = _make_client(mocker, fake_boto_client)

    items = c.list_files(container="my-bucket", prefix="")

    expected_count = 2
    assert len(items) == expected_count
    assert all(isinstance(item, ObjectInfo) for item in items)
    assert [item.object_name for item in items] == ["a.txt", "b.txt"]
    fake_boto_client.get_paginator.assert_called_once_with("list_objects_v2")
    fake_paginator.paginate.assert_called_once_with(
        Bucket="my-bucket",
        Prefix="",
    )


def test_list_files_returns_empty_list_when_no_contents(
    mocker: "MockerFixture",
) -> None:
    """Test list_files returns [] when Contents is missing."""
    fake_boto_client = mocker.Mock()
    fake_paginator = mocker.Mock()
    fake_paginator.paginate.return_value = [{}]
    fake_boto_client.get_paginator.return_value = fake_paginator
    c = _make_client(mocker, fake_boto_client)

    items = c.list_files(container="my-bucket", prefix="x/")

    assert items == []
    fake_paginator.paginate.assert_called_once_with(
        Bucket="my-bucket",
        Prefix="x/",
    )


def test_list_files_raises_storage_backend_error_on_client_error(
    mocker: "MockerFixture",
) -> None:
    """Test list_files raises StorageBackendError on ClientError."""
    fake_boto_client = mocker.Mock()
    fake_boto_client.get_paginator.side_effect = _client_error()
    c = _make_client(mocker, fake_boto_client)

    with pytest.raises(StorageBackendError):
        c.list_files(container="my-bucket", prefix="")

    fake_boto_client.get_paginator.assert_called_once_with("list_objects_v2")
