from http import HTTPStatus
from typing import Any, cast

import httpx

from ... import errors
from ...client import AuthenticatedClient, Client
from ...models.http_validation_error import HTTPValidationError
from ...types import UNSET, Response, Unset


def _get_kwargs(
    *,
    container: str,
    object_name: str,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> dict[str, Any]:
    headers: dict[str, Any] = {}
    if not isinstance(x_api_key, Unset):
        headers["X-API-Key"] = x_api_key

    if not isinstance(authorization, Unset):
        headers["authorization"] = authorization

    params: dict[str, Any] = {}

    params["container"] = container

    params["object_name"] = object_name

    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}

    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/download",
        "params": params,
    }

    _kwargs["headers"] = headers
    return _kwargs


def _parse_response(
    *, client: AuthenticatedClient | Client, response: httpx.Response
) -> Any | HTTPValidationError | None:
    if response.status_code == 200:
        response_200 = cast(Any, None)
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
) -> Response[Any | HTTPValidationError]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    container: str,
    object_name: str,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Download File

     Download an S3 object and return it as a file response.

    Args:
        container: The name of the bucket to download from.
        object_name: The name of the key to download from.
        client: Injected cloud storage client.

    Returns:
        A streaming file response containing the downloaded object.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the object or container does not exist.
        HTTPException: 502 if the storage backend fails.

    Args:
        container (str):
        object_name (str):
        x_api_key (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
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
    *,
    client: AuthenticatedClient | Client,
    container: str,
    object_name: str,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Download File

     Download an S3 object and return it as a file response.

    Args:
        container: The name of the bucket to download from.
        object_name: The name of the key to download from.
        client: Injected cloud storage client.

    Returns:
        A streaming file response containing the downloaded object.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the object or container does not exist.
        HTTPException: 502 if the storage backend fails.

    Args:
        container (str):
        object_name (str):
        x_api_key (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return sync_detailed(
        client=client,
        container=container,
        object_name=object_name,
        x_api_key=x_api_key,
        authorization=authorization,
    ).parsed


async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    container: str,
    object_name: str,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> Response[Any | HTTPValidationError]:
    """Download File

     Download an S3 object and return it as a file response.

    Args:
        container: The name of the bucket to download from.
        object_name: The name of the key to download from.
        client: Injected cloud storage client.

    Returns:
        A streaming file response containing the downloaded object.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the object or container does not exist.
        HTTPException: 502 if the storage backend fails.

    Args:
        container (str):
        object_name (str):
        x_api_key (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[Any | HTTPValidationError]
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
    *,
    client: AuthenticatedClient | Client,
    container: str,
    object_name: str,
    x_api_key: None | str | Unset = UNSET,
    authorization: None | str | Unset = UNSET,
) -> Any | HTTPValidationError | None:
    """Download File

     Download an S3 object and return it as a file response.

    Args:
        container: The name of the bucket to download from.
        object_name: The name of the key to download from.
        client: Injected cloud storage client.

    Returns:
        A streaming file response containing the downloaded object.

    Raises:
        HTTPException: 400 if the container or object key is invalid.
        HTTPException: 401 if credentials are rejected.
        HTTPException: 404 if the object or container does not exist.
        HTTPException: 502 if the storage backend fails.

    Args:
        container (str):
        object_name (str):
        x_api_key (None | str | Unset):
        authorization (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Any | HTTPValidationError
    """

    return (
        await asyncio_detailed(
            client=client,
            container=container,
            object_name=object_name,
            x_api_key=x_api_key,
            authorization=authorization,
        )
    ).parsed
