"""Unit tests for the HTTP-backed cloud storage adapter."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import pytest
from aws_client_adapter.service_adapter import CloudStorageServiceAdapter
from aws_s3_cloud_storage_service_client.models.list_files_response import (
    ListFilesResponse,
)
from aws_s3_cloud_storage_service_client.models.operation_result import OperationResult
from aws_s3_cloud_storage_service_client.types import Response
from cloud_storage_client_api.exceptions import (
    ContainerNotFoundError,
    InvalidContainerError,
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
        return _response(HTTPStatus.OK, parsed=OperationResult(ok=True))

    mocker.patch(
        "aws_client_adapter.service_adapter.upload_object_api.sync_detailed",
        side_effect=fake_upload,
    )
    adapter = _make_adapter()

    assert adapter.upload_file("docs-bucket", str(source), "reports/report.txt") is True
    assert captured["container"] == "docs-bucket"
    assert captured["object_name"] == "reports/report.txt"
    assert captured["file_name"] == "report.txt"
    assert captured["payload"] == b"hello service"
    assert captured["mime_type"] == "text/plain"


def test_list_files_returns_parsed_files(mocker: MockerFixture) -> None:
    """list_files returns the parsed file list from the generated client."""
    mocker.patch(
        "aws_client_adapter.service_adapter.list_files_api.sync_detailed",
        return_value=_response(
            HTTPStatus.OK,
            parsed=ListFilesResponse(files=["docs/a.txt", "docs/b.txt"]),
        ),
    )
    adapter = _make_adapter()

    assert adapter.list_files("docs-bucket", "docs/") == ["docs/a.txt", "docs/b.txt"]


def test_download_file_writes_response_content(
    mocker: MockerFixture,
    tmp_path: Path,
) -> None:
    """download_file writes the response bytes to the requested path."""
    mocker.patch(
        "aws_client_adapter.service_adapter.download_file_api.sync_detailed",
        return_value=_response(HTTPStatus.OK, content=b"downloaded bytes"),
    )
    adapter = _make_adapter()
    destination = tmp_path / "downloaded.txt"

    assert adapter.download_file("docs-bucket", "docs/a.txt", str(destination)) is True
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


def test_list_files_maps_missing_container_to_domain_exception(
    mocker: MockerFixture,
) -> None:
    """A 404 for a missing container maps to ContainerNotFoundError."""
    mocker.patch(
        "aws_client_adapter.service_adapter.list_files_api.sync_detailed",
        return_value=_response(
            HTTPStatus.NOT_FOUND,
            content=b'{"detail":"Container was not found"}',
        ),
    )
    adapter = _make_adapter()

    with pytest.raises(ContainerNotFoundError, match="Container was not found"):
        adapter.list_files("docs-bucket", "")


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
