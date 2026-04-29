"""Tests for slack_bridge.dedupe."""

from __future__ import annotations

import pytest
from slack_bridge.dedupe import EventDedupeCache

pytestmark = pytest.mark.unit


def test_add_returns_true_on_first_insertion() -> None:
    """First insertion of a key reports it as newly added."""
    cache = EventDedupeCache()
    assert cache.add("k1") is True


def test_add_returns_false_on_duplicate() -> None:
    """Repeated insertion of the same key returns False."""
    cache = EventDedupeCache()
    cache.add("k1")
    assert cache.add("k1") is False


def test_distinct_keys_are_independent() -> None:
    """Distinct keys do not collide."""
    cache = EventDedupeCache()
    assert cache.add("a") is True
    assert cache.add("b") is True
    assert cache.add("a") is False
    assert cache.add("b") is False


def test_max_size_evicts_oldest_keys() -> None:
    """When the cache fills up, the oldest key is evicted FIFO/LRU."""
    cache = EventDedupeCache(max_size=2)
    cache.add("a")
    cache.add("b")
    cache.add("c")
    assert "a" not in cache
    assert "b" in cache
    assert "c" in cache
    assert cache.add("a") is True


def test_duplicate_refreshes_lru_position() -> None:
    """Re-adding an existing key keeps it from being evicted next."""
    cache = EventDedupeCache(max_size=2)
    cache.add("a")
    cache.add("b")
    assert cache.add("a") is False
    cache.add("c")
    assert "a" in cache
    assert "b" not in cache
    assert "c" in cache


def test_len_reflects_current_size() -> None:
    """``len`` matches the number of unique tracked keys."""
    cache = EventDedupeCache(max_size=3)
    assert len(cache) == 0
    cache.add("a")
    cache.add("a")
    cache.add("b")
    assert len(cache) == 2


def test_invalid_max_size_rejected() -> None:
    """Non-positive max_size is a programming error."""
    with pytest.raises(ValueError, match="max_size"):
        EventDedupeCache(max_size=0)
