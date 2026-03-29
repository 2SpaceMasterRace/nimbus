"""Unit Tests for token_store.py."""

import pytest
from aws_client_impl.token_store import delete_token, get_token, store_token

from aws_client_impl import token_store


@pytest.fixture(autouse=True)
def clear_store() -> None:
    """Clear the token store before each test to prevent state leakage."""
    token_store.clear()


def test_store_token_stores_token() -> None:
    """store_token saves the token under the given user_id."""
    store_token("user1", "gho_abc123")
    assert get_token("user1") == "gho_abc123"


def test_store_token_overwrites_existing_token() -> None:
    """store_token replaces a previously stored token for the same user_id."""
    store_token("user1", "gho_old")
    store_token("user1", "gho_new")
    assert get_token("user1") == "gho_new"


def test_store_token_raises_on_empty_user_id() -> None:
    """store_token raises ValueError when user_id is empty."""
    with pytest.raises(ValueError, match="user_id cannot be empty"):
        store_token("", "gho_abc123")


def test_store_token_raises_on_empty_token() -> None:
    """store_token raises ValueError when token is empty."""
    with pytest.raises(ValueError, match="token cannot be empty"):
        store_token("user1", "")


def test_store_token_stores_multiple_users() -> None:
    """store_token correctly stores tokens for multiple distinct users."""
    store_token("user1", "token_a")
    store_token("user2", "token_b")
    assert get_token("user1") == "token_a"
    assert get_token("user2") == "token_b"


def test_get_token_returns_none_for_unknown_user() -> None:
    """get_token returns None when no token exists for the given user_id."""
    assert get_token("nonexistent") is None


def test_get_token_returns_correct_token() -> None:
    """get_token returns the exact token that was stored."""
    store_token("user1", "gho_abc123")
    assert get_token("user1") == "gho_abc123"


def test_delete_token_removes_token() -> None:
    """delete_token removes the token so get_token returns None afterward."""
    store_token("user1", "gho_abc123")
    delete_token("user1")
    assert get_token("user1") is None


def test_delete_token_is_silent_for_unknown_user() -> None:
    """delete_token does not raise when the user_id does not exist."""
    delete_token("nonexistent")


def test_delete_token_only_removes_target_user() -> None:
    """delete_token removes only the specified user's token, not others."""
    store_token("user1", "token_a")
    store_token("user2", "token_b")
    delete_token("user1")
    assert get_token("user1") is None
    assert get_token("user2") == "token_b"
