"""Unit tests for the HTTP-backed cloud storage adapter."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import pytest
from aws_client_adapter.service_adapter import CloudStorageServiceAdapter
from aws_s3_cloud_storage_service_client.models.delete_result_response import (
    DeleteResultResponse,
)
from aws_s3_cloud_storage_service_client.models.object_info_response import (
    ObjectInfoResponse,
)
from aws_s3_cloud_storage_service_client.types import Response
from cloud_storage_api import (
    InvalidContainerError,
    ObjectInfo,
    ObjectNotFoundError,
)

from aws_s3_cloud_storage_service_client import Client

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

pytestmark = pytest.mark.unit


def _make_adapter() -> CloudStorageServiceAdapter:
    """Create an adapter backed by a dummy generated client."""
    return CloudStorageServiceAdapter(Client(base_url="http://service.test"))


def _response(
    status_code: HTTPStatus,
    *,
    content: bytes = b"",
    parsed: object = None,
) -> Response[object]:
    """Build a generated-client response object for tests."""
    return Response(
        status_code=status_code,
        content=content,
        headers={},
        parsed=parsed,
    )


def _stub_object_info_response(name: str = "reports/report.txt") -> ObjectInfoResponse:
    """Return a minimal generated ObjectInfoResponse."""
    return ObjectInfoResponse(object_name=name)


def _stub_delete_result_response(*, deleted: bool = True) -> DeleteResultResponse:
    """Return a minimal generated DeleteResultResponse."""
    return DeleteResultResponse(deleted=deleted)


def test_upload_file_posts_container_and_multipart_body(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """upload_file sends the requested container and file bytes to the service."""
    source = tmp_path / "report.txt"
    source.write_bytes(b"hello service")
    captured: dict[str, object] = {}

    def fake_upload(**kwargs: object) -> Response[object]:
        captured.update(kwargs)
        body = cast("Any", kwargs["body"])
        multipart = body.to_multipart()
        _, file_tuple = multipart[0]
        file_name, file_obj, mime_type = file_tuple
        assert hasattr(file_obj, "read")
        file_obj.seek(0)
        captured["file_name"] = file_name
        captured["payload"] = file_obj.read()
        captured["mime_type"] = mime_type
        return _response(
            HTTPStatus.OK, parsed=_stub_object_info_response("reports/report.txt")
        )

    mocker.patch(
        "aws_client_adapter.service_adapter.upload_object_api.sync_detailed",
        side_effect=fake_upload,
    )
    adapter = _make_adapter()

    result = adapter.upload_file("docs-bucket", str(source), "reports/report.txt")

    assert isinstance(result, ObjectInfo)
    assert result.object_name == "reports/report.txt"
    assert captured["container"] == "docs-bucket"
    assert captured["object_name"] == "reports/report.txt"
    assert captured["file_name"] == "report.txt"
    assert captured["payload"] == b"hello service"
    assert captured["mime_type"] == "text/plain"


def test_list_files_returns_parsed_object_infos(mocker: MockerFixture) -> None:
    """list_files returns ObjectInfo instances from the generated client."""
    mocker.patch(
        "aws_client_adapter.service_adapter.list_files_api.sync_detailed",
        return_value=_response(
            HTTPStatus.OK,
            parsed=[
                ObjectInfoResponse(object_name="docs/a.txt"),
                ObjectInfoResponse(object_name="docs/b.txt"),
            ],
        ),
    )
    adapter = _make_adapter()

    result = adapter.list_files("docs-bucket", "docs/")

    expected_count = 2
    assert len(result) == expected_count
    assert all(isinstance(item, ObjectInfo) for item in result)
    assert [item.object_name for item in result] == ["docs/a.txt", "docs/b.txt"]


def test_download_file_writes_response_content(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """download_file writes the response bytes to the requested path."""
    mocker.patch(
        "aws_client_adapter.service_adapter.download_file_api.sync_detailed",
        return_value=_response(HTTPStatus.OK, content=b"downloaded bytes"),
    )
    mocker.patch(
        "aws_client_adapter.service_adapter.get_file_info_api.sync_detailed",
        return_value=_response(
            HTTPStatus.OK,
            parsed=_stub_object_info_response("docs/a.txt"),
        ),
    )
    adapter = _make_adapter()
    destination = tmp_path / "downloaded.txt"

    result = adapter.download_file("docs-bucket", "docs/a.txt", str(destination))

    assert isinstance(result, ObjectInfo)
    assert destination.read_bytes() == b"downloaded bytes"


def test_delete_file_maps_not_found_to_domain_exception(
    mocker: MockerFixture,
) -> None:
    """delete_file raises ObjectNotFoundError for a 404 service response."""
    mocker.patch(
        "aws_client_adapter.service_adapter.delete_object_api.sync_detailed",
        return_value=_response(
            HTTPStatus.NOT_FOUND,
            content=b'{"detail":"Object was missing"}',
        ),
    )
    adapter = _make_adapter()

    with pytest.raises(ObjectNotFoundError, match="Object was missing"):
        adapter.delete_file("docs-bucket", "docs/a.txt")


def test_delete_file_returns_delete_result_on_success(
    mocker: MockerFixture,
) -> None:
    """delete_file returns a DeleteResult on a successful 200 response."""
    mocker.patch(
        "aws_client_adapter.service_adapter.delete_object_api.sync_detailed",
        return_value=_response(
            HTTPStatus.OK,
            parsed=_stub_delete_result_response(deleted=True),
        ),
    )
    adapter = _make_adapter()

    result = adapter.delete_file("docs-bucket", "docs/a.txt")

    assert isinstance(result, dict)
    assert result["deleted"] is True


def test_list_files_maps_bad_container_validation_error(
    mocker: MockerFixture,
) -> None:
    """list_files raises InvalidContainerError for service validation failures."""
    mocker.patch(
        "aws_client_adapter.service_adapter.list_files_api.sync_detailed",
        return_value=_response(
            HTTPStatus.BAD_REQUEST,
            content=b'{"detail":"Container cannot be empty"}',
        ),
    )
    adapter = _make_adapter()

    with pytest.raises(InvalidContainerError, match="Container cannot be empty"):
        adapter.list_files("bad", "")
