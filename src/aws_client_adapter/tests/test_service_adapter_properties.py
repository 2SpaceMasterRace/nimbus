"""Property-based tests for the HTTP-backed storage service adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from http import HTTPStatus
from unittest.mock import patch

import pytest
from aws_client_adapter.service_adapter import (
    CloudStorageServiceAdapter,
    _response_to_delete_result,
)
from aws_s3_cloud_storage_service_client.models.delete_result_response import (
    DeleteResultResponse,
)
from aws_s3_cloud_storage_service_client.models.object_info_response import (
    ObjectInfoResponse,
)
from aws_s3_cloud_storage_service_client.types import Response
from cloud_storage_api import (
    AuthenticationError,
    ContainerNotFoundError,
    InvalidContainerError,
    InvalidFileObjectError,
    InvalidObjectNameError,
    ObjectNotFoundError,
    StorageBackendError,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from aws_s3_cloud_storage_service_client import Client

pytestmark = pytest.mark.property

LIST_API = "aws_client_adapter.service_adapter.list_files_api.sync_detailed"

_SAFE_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=0,
    max_size=30,
)
_SAFE_NAME = st.from_regex(
    r"^[A-Za-z0-9_.:-]{1,12}/[A-Za-z0-9_.:-]{1,24}$",
    fullmatch=True,
)
_OPTIONAL_TEXT = st.one_of(st.none(), _SAFE_TEXT)
_JSON_SCALAR = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1_000, max_value=1_000),
    _SAFE_TEXT,
)
_JSON_PAYLOAD = st.one_of(
    _JSON_SCALAR,
    st.lists(_JSON_SCALAR, max_size=5),
    st.dictionaries(_SAFE_TEXT, _JSON_SCALAR, max_size=5),
)


def _make_adapter() -> CloudStorageServiceAdapter:
    return CloudStorageServiceAdapter(Client(base_url="http://service.test"))


def _response(
    status_code: HTTPStatus,
    *,
    content: bytes = b"",
    parsed: object = None,
) -> Response[object]:
    return Response(status_code=status_code, content=content, headers={}, parsed=parsed)


def _object_info_response(
    *,
    object_name: str,
    version_id: str | None,
    data_type: str | None,
    integrity: str | None,
    size_bytes: int | None,
    metadata: dict[str, str],
) -> ObjectInfoResponse:
    return ObjectInfoResponse.from_dict(
        {
            "object_name": object_name,
            "version_id": version_id,
            "data_type": data_type,
            "integrity": integrity,
            "size_bytes": size_bytes,
            "updated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
            "metadata": metadata,
        }
    )


@given(
    object_names=st.lists(_SAFE_NAME, min_size=1, max_size=8),
    version_id=_OPTIONAL_TEXT,
    data_type=_OPTIONAL_TEXT,
    integrity=_OPTIONAL_TEXT,
    size_bytes=st.one_of(st.none(), st.integers(min_value=0, max_value=10_000_000)),
    metadata=st.dictionaries(_SAFE_TEXT, _SAFE_TEXT, max_size=5),
)
@settings(max_examples=30, deadline=None)
def test_list_files_maps_every_response_and_sorts_by_object_name(
    object_names: list[str],
    version_id: str | None,
    data_type: str | None,
    integrity: str | None,
    size_bytes: int | None,
    metadata: dict[str, str],
) -> None:
    """The adapter returns deterministic domain objects from service list output."""
    parsed = [
        _object_info_response(
            object_name=name,
            version_id=version_id,
            data_type=data_type,
            integrity=integrity,
            size_bytes=size_bytes,
            metadata=metadata,
        )
        for name in reversed(object_names)
    ]
    with patch(LIST_API, return_value=_response(HTTPStatus.OK, parsed=parsed)):
        result = _make_adapter().list_files("docs-bucket", "docs/")

    assert [item.object_name for item in result] == sorted(object_names)
    assert [item.version_id for item in result] == [version_id] * len(result)
    assert [item.data_type for item in result] == [data_type] * len(result)
    assert [item.integrity for item in result] == [integrity] * len(result)
    assert [item.size_bytes for item in result] == [size_bytes] * len(result)
    assert all(item.metadata == metadata for item in result)


@given(
    deleted=st.booleans(),
    version_id=_OPTIONAL_TEXT,
    request_charged=st.one_of(st.none(), st.booleans()),
)
@settings(max_examples=25, deadline=None)
def test_delete_response_mapping_preserves_optional_service_fields(
    deleted: bool,
    version_id: str | None,
    request_charged: bool | None,
) -> None:
    """Generated delete responses map without losing service-provided fields."""
    response = DeleteResultResponse.from_dict(
        {
            "deleted": deleted,
            "version_id": version_id,
            "request_charged": request_charged,
        }
    )

    result = _response_to_delete_result(response)

    assert result == {
        "deleted": deleted,
        "version_id": version_id,
        "request_charged": request_charged,
    }


@given(payload=_JSON_PAYLOAD)
@settings(max_examples=40, deadline=None)
def test_extract_detail_is_total_for_json_payload_shapes(payload: object) -> None:
    """Malformed or surprising service JSON should still become a stable message."""
    content = json.dumps(payload).encode()

    detail = _make_adapter()._extract_detail(content)

    assert isinstance(detail, str)
    assert detail != ""


@given(content=st.binary(max_size=64))
@settings(max_examples=40, deadline=None)
def test_extract_detail_is_total_for_raw_response_bytes(content: bytes) -> None:
    """Any raw service body should become a domain exception message."""
    detail = _make_adapter()._extract_detail(content)

    assert isinstance(detail, str)
    assert detail != ""


@pytest.mark.parametrize(
    ("status_code", "detail", "expected_error"),
    [
        (HTTPStatus.BAD_REQUEST, "Container cannot be empty", InvalidContainerError),
        (HTTPStatus.BAD_REQUEST, "file_obj must be readable", InvalidFileObjectError),
        (
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "object name missing",
            InvalidObjectNameError,
        ),
        (HTTPStatus.UNAUTHORIZED, "bad API key", AuthenticationError),
        (HTTPStatus.NOT_FOUND, "Bucket docs-bucket missing", ContainerNotFoundError),
        (HTTPStatus.NOT_FOUND, "Object docs/a.txt missing", ObjectNotFoundError),
        (HTTPStatus.BAD_GATEWAY, "upstream unavailable", StorageBackendError),
    ],
)
@given(extra=st.dictionaries(_SAFE_TEXT, _JSON_SCALAR, max_size=3))
@settings(max_examples=10, deadline=None)
def test_response_error_classification_ignores_unrelated_json_fields(
    status_code: HTTPStatus,
    detail: str,
    expected_error: type[Exception],
    extra: dict[str, object],
) -> None:
    """HTTP status and detail drive domain error type, not incidental JSON fields."""
    content = json.dumps({**extra, "detail": detail}).encode()

    with pytest.raises(expected_error, match=detail):
        _make_adapter()._raise_for_response(_response(status_code, content=content))
