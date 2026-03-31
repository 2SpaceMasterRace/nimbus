"""Dependencies for OAuth-protected routes."""

from fastapi import HTTPException, Request, status


def require_oauth_session(request: Request) -> str:
    """Require an authenticated GitHub OAuth session.

    Args:
        request: FastAPI request object containing session state.

    Returns:
        The GitHub access token stored in session.

    Raises:
        HTTPException: If the user is not authenticated.

    """
    token = request.session.get("github_access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return str(token)
