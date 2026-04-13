"""API-key authentication dependency for AI server routes.

The middleman API (or any caller) passes the shared secret in the
``X-API-Key`` header.  The expected value is read from the
``AI_SERVER_API_KEY`` environment variable at request time so that tests
can swap it via ``monkeypatch.setenv`` without restarting the process.
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import Header, HTTPException, status


def require_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """Require a valid ``AI_SERVER_API_KEY`` in the ``X-API-Key`` header.

    Returns the validated key on success.

    Raises:
        HTTPException 503: ``AI_SERVER_API_KEY`` is not configured in the
            process environment.
        HTTPException 401: The supplied key is missing or does not match.

    """
    expected = os.environ.get("AI_SERVER_API_KEY", "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI_SERVER_API_KEY is not configured on this server.",
        )
    if x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "API-Key"},
        )
    return x_api_key  # type: ignore[return-value]  # guarded above
