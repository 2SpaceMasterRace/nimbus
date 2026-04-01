from http import HTTPStatus
from typing import Any, cast
from urllib.parse import quote

import httpx

from ...client import AuthenticatedClient, Client
from ...types import Response, UNSET
from ... import errors

from ...models.http_validation_error import HTTPValidationError
from ...models.operation_result import OperationResult
from typing import cast



def _get_kwargs(
    container: str,
    object_name: str,

) -> dict[str, Any]:
    

    

    

    _kwargs: dict[str, Any] = {
        "method": "delete",
        "url": "/files/{container}/{object_name}".format(container=quote(str(container), safe=""),object_name=quote(str(object_name), safe=""),),
    }


    return _kwargs



def _parse_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> HTTPValidationError | OperationResult | None:
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


def _build_response(*, client: AuthenticatedClient | Client, response: httpx.Response) -> Response[HTTPValidationError | OperationResult]:
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

) -> Response[HTTPValidationError | OperationResult]:
    """ Delete Object

     Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the deletion returns failure.

    Args:
        container (str):
        object_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OperationResult]
     """


    kwargs = _get_kwargs(
        container=container,
object_name=object_name,

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

) -> HTTPValidationError | OperationResult | None:
    """ Delete Object

     Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the deletion returns failure.

    Args:
        container (str):
        object_name (str):

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

    ).parsed

async def asyncio_detailed(
    container: str,
    object_name: str,
    *,
    client: AuthenticatedClient | Client,

) -> Response[HTTPValidationError | OperationResult]:
    """ Delete Object

     Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the deletion returns failure.

    Args:
        container (str):
        object_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        Response[HTTPValidationError | OperationResult]
     """


    kwargs = _get_kwargs(
        container=container,
object_name=object_name,

    )

    response = await client.get_async_httpx_client().request(
        **kwargs
    )

    return _build_response(client=client, response=response)

async def asyncio(
    container: str,
    object_name: str,
    *,
    client: AuthenticatedClient | Client,

) -> HTTPValidationError | OperationResult | None:
    """ Delete Object

     Delete an object from a bucket (container).

    Args:
        container: The name of the bucket containing the object.
        object_name: The key of the object to delete.
        client: Injected cloud storage client.

    Returns:
        OperationResult with ok=True on success.

    Raises:
        HTTPException: 502 if the storage backend raises an exception.
        HTTPException: 404 if the deletion returns failure.

    Args:
        container (str):
        object_name (str):

    Raises:
        errors.UnexpectedStatus: If the server returns an undocumented status code and Client.raise_on_unexpected_status is True.
        httpx.TimeoutException: If the request takes longer than Client.timeout.

    Returns:
        HTTPValidationError | OperationResult
     """


    return (await asyncio_detailed(
        container=container,
object_name=object_name,
client=client,

    )).parsed
