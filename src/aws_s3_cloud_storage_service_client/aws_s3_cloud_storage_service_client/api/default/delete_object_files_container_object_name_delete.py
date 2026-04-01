from http import HTTPStatus
from typing import Any
from urllib.parse import quote

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...models.operation_result import OperationResult
from ...types import UNSET, Response, Unset


def _get_kwargs(
    container: str,
    object_name: str,
    *,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(x_api_key, Unset):
        headers["X-API-Key"] = x_api_key

    if not isinstance(authorization, Unset):
        headers["authorization"] = authorization

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/files/{container}/{object_name}".format(
            container=quote(str(container), safe=""),
            object_name=quote(str(object_name), safe=""),
        ),
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> HTTPValidationError | OperationResult | None:
    if response.status_code == 200:
        response_200 = OperationResult.from_dict(response.json())

        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())

        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Response[HTTPValidationError | OperationResult]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    container: str,
    object_name: str,
    *,
    client: AuthenticatedClient | Client,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | OperationResult]:
    """Delete Object

     Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the object does not exist.

    Args:
        container (str):
        object_name (str):
        x_api_key (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OperationResult]
    """

    kwargs = _get_kwargs(
        container=container,
        object_name=object_name,
        x_api_key=x_api_key,
        authorization=authorization,
    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)


def sync(
    container: str,
    object_name: str,
    *,
    client: AuthenticatedClient | Client,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> HTTPValidationError | OperationResult | None:
    """Delete Object

     Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the object does not exist.

    Args:
        container (str):
        object_name (str):
        x_api_key (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OperationResult
    """

    return sync_detailed(
        container=container,
        object_name=object_name,
        client=client,
        x_api_key=x_api_key,
        authorization=authorization,
    ).parsed


async def asyncio_detailed(
    container: str,
    object_name: str,
    *,
    client: AuthenticatedClient | Client,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> Response[HTTPValidationError | OperationResult]:
    """Delete Object

     Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the object does not exist.

    Args:
        container (str):
        object_name (str):
        x_api_key (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OperationResult]
    """

    kwargs = _get_kwargs(
        container=container,
        object_name=object_name,
        x_api_key=x_api_key,
        authorization=authorization,
    )

    response = await client.get_async_httpx_client().request(**kwargs)

    return _build_response(client=client, response=response)


async def asyncio(
    container: str,
    object_name: str,
    *,
    client: AuthenticatedClient | Client,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> HTTPValidationError | OperationResult | None:
    """Delete Object

     Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the object does not exist.

    Args:
        container (str):
        object_name (str):
        x_api_key (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OperationResult
    """

    return (
        await asyncio_detailed(
            container=container,
            object_name=object_name,
            client=client,
            x_api_key=x_api_key,
            authorization=authorization,
        )
    ).parsed
