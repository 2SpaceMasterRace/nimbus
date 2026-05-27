"""Unit tests for the HTTP-backed cloud storage adapter."""

from __future__ import annotations

from http import HTTPStatus
from io import BytesIO, StringIO
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from aws_client_adapter.service_adapter import (
    CloudStorageServiceAdapter,
    get_client_impl,
)
from aws_s3_cloud_storage_service_client.models.delete_result_response import (
    DeleteResultResponse,
)
from aws_s3_cloud_storage_service_client.models.object_info_response import (
    ObjectInfoResponse,
)
from aws_s3_cloud_storage_service_client.types import UNSET, Response
from cloud_storage_api import (
    AuthenticationError,
    ContainerNotFoundError,
    InvalidContainerError,
    InvalidFileObjectError,
    InvalidObjectNameError,
    LocalFileAccessError,
    ObjectInfo,
    ObjectNotFoundError,
    StorageBackendError,
)

from aws_s3_cloud_storage_service_client import AuthenticatedClient, Client

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_mock import MockerFixture

pytestmark = pytest.mark.unit

UPLOAD_API = "aws_client_adapter.service_adapter.upload_object_api.sync_detailed"
DOWNLOAD_API = "aws_client_adapter.service_adapter.download_file_api.sync_detailed"
LIST_API = "aws_client_adapter.service_adapter.list_files_api.sync_detailed"
DELETE_API = "aws_client_adapter.service_adapter.delete_object_api.sync_detailed"
INFO_API = "aws_client_adapter.service_adapter.get_file_info_api.sync_detailed"


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
    response = ObjectInfoResponse(object_name=name)
    response.version_id = UNSET
    response.size_bytes = UNSET
    return response


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


def test_list_files_sorts_service_results_by_object_name(
    mocker: MockerFixture,
) -> None:
    """list_files provides stable ordering even if the service does not."""
    mocker.patch(
        "aws_client_adapter.service_adapter.list_files_api.sync_detailed",
        return_value=_response(
            HTTPStatus.OK,
            parsed=[
                ObjectInfoResponse(object_name="docs/b.txt"),
                ObjectInfoResponse(object_name="docs/a.txt"),
            ],
        ),
    )
    adapter = _make_adapter()

    result = adapter.list_files("docs-bucket", "docs/")

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


def test_download_file_maps_local_write_failure_to_local_access_error(
    mocker: MockerFixture,
) -> None:
    """download_file keeps local filesystem failures distinct from service errors."""
    mocker.patch(
        "aws_client_adapter.service_adapter.download_file_api.sync_detailed",
        return_value=_response(HTTPStatus.OK, content=b"downloaded bytes"),
    )
    mocker.patch(
        "aws_client_adapter.service_adapter.Path.write_bytes",
        side_effect=OSError("permission denied"),
    )
    adapter = _make_adapter()

    with pytest.raises(LocalFileAccessError, match="Cannot write to local path"):
        adapter.download_file("docs-bucket", "docs/a.txt", "/readonly/a.txt")


def test_download_file_maps_transport_failure_to_backend_error(
    mocker: MockerFixture,
) -> None:
    """download_file translates HTTP transport failures into domain errors."""
    mocker.patch(
        "aws_client_adapter.service_adapter.download_file_api.sync_detailed",
        side_effect=httpx.ConnectError("connection reset"),
    )
    adapter = _make_adapter()

    with pytest.raises(StorageBackendError, match="Download failed"):
        adapter.download_file("docs-bucket", "docs/a.txt", "a.txt")


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


def test_get_file_info_returns_metadata_and_unset_values_as_none(
    mocker: MockerFixture,
) -> None:
    """get_file_info maps generated response fields into domain metadata."""
    parsed = ObjectInfoResponse.from_dict(
        {
            "object_name": "docs/a.txt",
            "metadata": {"owner": "nimbus", "purpose": "docs"},
        }
    )
    parsed.version_id = UNSET
    parsed.size_bytes = 42
    mocker.patch(
        "aws_client_adapter.service_adapter.get_file_info_api.sync_detailed",
        return_value=_response(HTTPStatus.OK, parsed=parsed),
    )
    adapter = _make_adapter()

    result = adapter.get_file_info("docs-bucket", "docs/a.txt")

    assert result.object_name == "docs/a.txt"
    assert result.version_id is None
    assert result.size_bytes == 42
    assert result.metadata == {"owner": "nimbus", "purpose": "docs"}


def test_get_file_info_maps_container_404_to_container_not_found(
    mocker: MockerFixture,
) -> None:
    """get_file_info preserves container-missing classification from service 404s."""
    mocker.patch(
        "aws_client_adapter.service_adapter.get_file_info_api.sync_detailed",
        return_value=_response(
            HTTPStatus.NOT_FOUND,
            content=b'{"detail":"Bucket docs-bucket was missing"}',
        ),
    )
    adapter = _make_adapter()

    with pytest.raises(ContainerNotFoundError, match="Bucket docs-bucket"):
        adapter.get_file_info("docs-bucket", "docs/a.txt")


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


@pytest.mark.parametrize(
    ("method_name", "args", "remote_failure"),
    [
        (
            "upload_obj",
            ("docs-bucket", BytesIO(b"payload"), "docs/a.txt"),
            (
                UPLOAD_API,
                "upload failed",
                "Upload failed",
            ),
        ),
        (
            "list_files",
            ("docs-bucket", "docs/"),
            (
                LIST_API,
                "list failed",
                "List files failed",
            ),
        ),
        (
            "delete_file",
            ("docs-bucket", "docs/a.txt"),
            (
                DELETE_API,
                "delete failed",
                "Delete failed",
            ),
        ),
        (
            "get_file_info",
            ("docs-bucket", "docs/a.txt"),
            (
                INFO_API,
                "info failed",
                "Get file info failed",
            ),
        ),
    ],
)
def test_remote_operations_map_transport_failures_to_backend_errors(
    mocker: MockerFixture,
    method_name: str,
    args: tuple[object, ...],
    remote_failure: tuple[str, str, str],
) -> None:
    """Remote operations should not leak raw httpx exceptions."""
    api_path, transport_message, error_message = remote_failure
    mocker.patch(api_path, side_effect=httpx.ReadTimeout(transport_message))
    adapter = _make_adapter()

    with pytest.raises(StorageBackendError, match=error_message):
        getattr(adapter, method_name)(*args)


def test_upload_file_maps_missing_local_file_to_local_access_error(
    tmp_path: Path,
) -> None:
    """upload_file validates local readability before calling the service."""
    adapter = _make_adapter()
    missing_path = tmp_path / "missing.txt"

    with pytest.raises(LocalFileAccessError, match="Cannot read local file"):
        adapter.upload_file("docs-bucket", str(missing_path), "docs/a.txt")


def test_upload_obj_rejects_text_file_objects() -> None:
    """upload_obj rejects text streams before sending a remote request."""
    adapter = _make_adapter()

    with pytest.raises(InvalidFileObjectError, match="binary mode"):
        adapter.upload_obj("docs-bucket", StringIO("text"), "docs/a.txt")  # type: ignore[arg-type]


def test_upload_obj_rejects_objects_without_file_interface() -> None:
    """upload_obj rejects values that do not implement the file-like contract."""
    adapter = _make_adapter()

    with pytest.raises(InvalidFileObjectError, match="file-like object"):
        adapter.upload_obj("docs-bucket", object(), "docs/a.txt")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("upload_obj", ("", BytesIO(b"payload"), "docs/a.txt")),
        ("download_file", ("", "docs/a.txt", "downloaded.txt")),
        ("list_files", ("", "docs/")),
        ("delete_file", ("", "docs/a.txt")),
        ("get_file_info", ("", "docs/a.txt")),
    ],
)
def test_operations_reject_empty_container_before_remote_call(
    mocker: MockerFixture,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    """Adapter methods validate container names before crossing the HTTP boundary."""
    upload = mocker.patch(UPLOAD_API)
    download = mocker.patch(DOWNLOAD_API)
    list_files = mocker.patch(LIST_API)
    delete = mocker.patch(DELETE_API)
    info = mocker.patch(INFO_API)
    adapter = _make_adapter()

    with pytest.raises(InvalidContainerError, match="Container cannot be empty"):
        getattr(adapter, method_name)(*args)

    for remote_call in (upload, download, list_files, delete, info):
        remote_call.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("upload_obj", ("docs-bucket", BytesIO(b"payload"), "")),
        ("download_file", ("docs-bucket", "", "downloaded.txt")),
        ("delete_file", ("docs-bucket", "")),
        ("get_file_info", ("docs-bucket", "")),
    ],
)
def test_operations_reject_empty_object_name_before_remote_call(
    mocker: MockerFixture,
    method_name: str,
    args: tuple[object, ...],
) -> None:
    """Adapter methods reject empty object keys before crossing the HTTP boundary."""
    upload = mocker.patch(UPLOAD_API)
    download = mocker.patch(DOWNLOAD_API)
    delete = mocker.patch(DELETE_API)
    info = mocker.patch(INFO_API)
    adapter = _make_adapter()

    with pytest.raises(InvalidObjectNameError, match="Key cannot be empty"):
        getattr(adapter, method_name)(*args)

    for remote_call in (upload, download, delete, info):
        remote_call.assert_not_called()


def test_upload_obj_rejects_leading_slash_object_name() -> None:
    """Object keys with leading slashes are invalid S3 keys."""
    adapter = _make_adapter()

    with pytest.raises(InvalidObjectNameError, match="leading slash"):
        adapter.upload_obj("docs-bucket", BytesIO(b"payload"), "/docs/a.txt")


@pytest.mark.parametrize(
    ("status_code", "content", "expected_error", "match"),
    [
        (
            HTTPStatus.UNAUTHORIZED,
            b'{"detail":"bad API key"}',
            AuthenticationError,
            "bad API key",
        ),
        (
            HTTPStatus.BAD_REQUEST,
            b'{"detail":"file_obj must be readable"}',
            InvalidFileObjectError,
            "file_obj",
        ),
        (
            HTTPStatus.UNPROCESSABLE_ENTITY,
            b'{"detail":[{"loc":["query","object_name"],"msg":"Field required"}]}',
            InvalidObjectNameError,
            "query.object_name: Field required",
        ),
        (
            HTTPStatus.BAD_GATEWAY,
            b"upstream unavailable",
            StorageBackendError,
            "upstream unavailable",
        ),
        (
            HTTPStatus.INTERNAL_SERVER_ERROR,
            b"\xff",
            StorageBackendError,
            "Service request failed",
        ),
    ],
)
def test_response_errors_are_translated_to_domain_exceptions(
    status_code: HTTPStatus,
    content: bytes,
    expected_error: type[Exception],
    match: str,
) -> None:
    """Service response failures are exposed as storage-domain exceptions."""
    adapter = _make_adapter()

    with pytest.raises(expected_error, match=match):
        adapter._raise_for_response(_response(status_code, content=content))


@pytest.mark.parametrize(
    ("env", "expected_type"),
    [
        ({}, Client),
        ({"API_KEY": "secret-api-key"}, AuthenticatedClient),
    ],
)
def test_get_client_impl_uses_authenticated_client_when_api_key_is_set(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    expected_type: type[Client | AuthenticatedClient],
) -> None:
    """get_client_impl selects the generated client shape from environment."""
    monkeypatch.setenv("CLOUD_STORAGE_SERVICE_BASE_URL", "https://storage.test")
    monkeypatch.delenv("API_KEY", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    adapter = get_client_impl()

    assert isinstance(adapter._service_client, expected_type)
