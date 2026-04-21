"""In-memory token store for OAuth access tokens."""

from threading import Lock

_store: dict[str, str] = {}
_lock = Lock()


def store_token(user_id: str, token: str) -> None:
    """Store an access token for a user.

    Args:
        user_id: A unique identifier for the user.
        token: The OAuth access token to store.

    Raises:
        ValueError: If user_id or token is empty.

    """
    if not user_id:
        msg = "user_id cannot be empty"
        raise ValueError(msg)
    if not token:
        msg = "token cannot be empty"
        raise ValueError(msg)

    with _lock:
        _store[user_id] = token


def get_token(user_id: str) -> str | None:
    """Retrieve the access token for a user.

    Args:
        user_id: A unique identifier for the user.

    Returns:
        The access token if found, otherwise None.

    """
    with _lock:
        return _store.get(user_id)


def delete_token(user_id: str) -> None:
    """Delete the access token for a user.

    Args:
        user_id: A unique identifier for the user.

    """
    with _lock:
        _store.pop(user_id, None)


def clear() -> None:
    """Clear all tokens. For testing only."""
    with _lock:
        _store.clear()
