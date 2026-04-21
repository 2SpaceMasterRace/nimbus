"""OAuth routes for GitHub login flow."""

from typing import Annotated

from aws_client_impl.oauth import (
    OAuthProviderError,
    OAuthTransportError,
    build_github_auth_url,
    exchange_code_for_token,
    validate_state,
)
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from aws_client_service.token_store import create_oauth_session

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    """Start GitHub OAuth login flow.

    Args:
        request: FastAPI request object used for session storage.

    Returns:
        RedirectResponse to GitHub authorization URL.

    """
    auth_url, state = build_github_auth_url()
    request.session["oauth_state"] = state
    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@router.get("/callback")
def callback(
    request: Request,
    code: Annotated[str, Query(...)],
    state: Annotated[str, Query(...)],
) -> dict[str, str]:
    """Handle GitHub OAuth callback.

    Args:
        request: FastAPI request object used for session storage.
        code: Authorization code returned by GitHub.
        state: State token returned by GitHub.

    Returns:
        Success message after storing access token in session.

    Raises:
        HTTPException: If state validation fails or token exchange fails.

    """
    expected_state = request.session.get("oauth_state")
    if not expected_state or not validate_state(state, str(expected_state)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid OAuth state",
        )

    try:
        access_token = exchange_code_for_token(code)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except OAuthTransportError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=str(exc),
        ) from exc
    except OAuthProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    request.session["github_session_id"] = create_oauth_session(access_token)
    request.session.pop("oauth_state", None)

    return {"message": "OAuth login successful"}
