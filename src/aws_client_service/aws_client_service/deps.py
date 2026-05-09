"""Dependencies for authenticated routes."""

from __future__ import annotations

import hmac
import os
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

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

    if (
        expected_api_key
        and provided_api_key is not None
        and hmac.compare_digest(provided_api_key, expected_api_key)
    ):
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


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in {"1", "true", "yes", "on"}


def _production_environment() -> bool:
    env = (
        os.environ.get("NIMBUS_ENV")
        or os.environ.get("ENVIRONMENT")
        or os.environ.get("APP_ENV")
        or ""
    )
    return env.strip().lower() in {"prod", "production"}


def _raw_mutations_enabled() -> bool:
    configured = os.environ.get("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED")
    if configured is not None:
        return _truthy(configured)
    return not _production_environment()


def require_storage_mutation_admin(
    _request: Request,
    base_auth: Annotated[str, Depends(require_oauth_session)],
    x_storage_admin_key: Annotated[
        str | None,
        Header(alias="X-Nimbus-Storage-Admin-Key"),
    ] = None,
) -> str:
    """Require explicit admin/developer authorization for raw storage mutations."""
    if not _raw_mutations_enabled():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Raw storage mutations are disabled; use the Nimbus runtime "
                "action ledger for public destructive or tenant-scoped changes."
            ),
        )

    expected_admin_key = os.environ.get("NIMBUS_RAW_STORAGE_ADMIN_KEY", "").strip()
    if expected_admin_key:
        if x_storage_admin_key is not None and hmac.compare_digest(
            x_storage_admin_key,
            expected_admin_key,
        ):
            return "raw-storage-admin"
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Raw storage mutation admin key is required.",
        )

    if _production_environment():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="NIMBUS_RAW_STORAGE_ADMIN_KEY is required in production.",
        )
    return base_auth
