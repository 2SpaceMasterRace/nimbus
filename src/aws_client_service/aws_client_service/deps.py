"""Dependencies for authenticated routes."""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

from aws_client_service.token_store import get_oauth_session


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Return the bearer token portion of an Authorization header."""
    if authorization is None:
        return None

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def require_oauth_session(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Require either a GitHub OAuth session or a configured API key.

    Args:
        request: FastAPI request object containing session state.
        x_api_key: Optional API key passed via the ``X-API-Key`` header.
        authorization: Optional ``Authorization`` header using bearer format.

    Returns:
        The authenticated token value.

    Raises:
        HTTPException: If the request is not authenticated.

    """
    expected_api_key = os.environ.get("API_KEY")
    provided_api_key = x_api_key or _extract_bearer_token(authorization)

    if expected_api_key and provided_api_key == expected_api_key:
        return expected_api_key

    session_id = request.session.get("github_session_id")
    if isinstance(session_id, str) and session_id:
        session = get_oauth_session(session_id)
        if session is not None:
            return session.access_token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )
