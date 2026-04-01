from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.list_files_files_get_response_list_files_files_get import ListFilesFilesGetResponseListFilesFilesGet
from ...types import UNSET, Unset
from typing import cast



def _get_kwargs(
    *,
    prefix: None | str | Unset = UNSET,

) -> dict[str, Any]:
    

    

    params: dict[str, Any] = {}

    json_prefix: None | str | Unset
    if isinstance(prefix, Unset):
        json_prefix = UNSET
    else:
        json_prefix = prefix
    params["prefix"] = json_prefix


    params = {k: v for k, v in params.items() if v is not UNSET and v is not None}


    _kwargs: dict[str, Any] = {
        "method": "get",
        "url": "/files",
        "params": params,
    }


    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet | None:
    if response.status_code == 200:
        response_200 = ListFilesFilesGetResponseListFilesFilesGet.from_dict(response.json())



        return response_200

    if response.status_code == 422:
        response_422 = HTTPValidationError.from_dict(response.json())



        return response_422

    if client.raise_on_unexpected_status:
        raise errors.UnexpectedStatus(response.status_code, response.content)
    else:
        return None


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet]:
    return Response(
        status_code=HTTPStatus(response.status_code),
        content=response.content,
        headers=response.headers,
        parsed=_parse_response(client=client, response=response),
    )


def sync_detailed(
    *,
    client: AuthenticatedClient | Client,
    prefix: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet]:
    """ List Files

     List files that match a given prefix.

    Args:
        prefix: Prefix used to filter objects.
        client: Injected cloud storage client.

    Returns:
        A JSON object containing matching file keys.

    Raises:
        HTTPException: If the storage backend fails.

    Args:
        prefix (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet]
     """


    kwargs = _get_kwargs(
        prefix=prefix,

    )

    response = client.get_httpx_client().request(
        **kwargs,
    )

    return _build_response(client=client, response=response)

def sync(
    *,
    client: AuthenticatedClient | Client,
    prefix: None | str | Unset = UNSET,

) -> HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet | None:
    """ List Files

     List files that match a given prefix.

    Args:
        prefix: Prefix used to filter objects.
        client: Injected cloud storage client.

    Returns:
        A JSON object containing matching file keys.

    Raises:
        HTTPException: If the storage backend fails.

    Args:
        prefix (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet
     """


    return sync_detailed(
        client=client,
prefix=prefix,

    ).parsed

async def asyncio_detailed(
    *,
    client: AuthenticatedClient | Client,
    prefix: None | str | Unset = UNSET,

) -> Response[HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet]:
    """ List Files

     List files that match a given prefix.

    Args:
        prefix: Prefix used to filter objects.
        client: Injected cloud storage client.

    Returns:
        A JSON object containing matching file keys.

    Raises:
        HTTPException: If the storage backend fails.

    Args:
        prefix (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet]
     """


    kwargs = _get_kwargs(
        prefix=prefix,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    *,
    client: AuthenticatedClient | Client,
    prefix: None | str | Unset = UNSET,

) -> HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet | None:
    """ List Files

     List files that match a given prefix.

    Args:
        prefix: Prefix used to filter objects.
        client: Injected cloud storage client.

    Returns:
        A JSON object containing matching file keys.

    Raises:
        HTTPException: If the storage backend fails.

    Args:
        prefix (None | str | Unset):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | ListFilesFilesGetResponseListFilesFilesGet
     """


    return (await asyncio_detailed(
        client=client,
prefix=prefix,

    )).parsed
