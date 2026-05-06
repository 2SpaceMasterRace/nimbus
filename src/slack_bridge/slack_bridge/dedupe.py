"""Bounded in-memory dedupe for Slack Events API retry deliveries.

Slack retries the same ``event_id`` whenever the bridge fails to ACK a
delivery within ~3 seconds. Without dedupe, a slow Nimbus call would lead
to duplicate AI turns and duplicate replies posted back to the channel.

This cache is intentionally process-local: the bridge today is deployed
on a single Fly machine (``min=1, max=1``). Multi-machine deployments must
upgrade to a shared store (Redis, Postgres, etc.) before relying on cross-
instance dedupe.
"""

from __future__ import annotations

from collections import OrderedDict
from threading import Lock

_DEFAULT_MAX_SIZE = 4096


class EventDedupeCache:
    """Bounded LRU set of event keys we have already accepted.

    The cache is FIFO/LRU bounded by ``max_size`` to keep memory usage flat
    regardless of historical traffic. ``add`` returns ``True`` exactly once
    per key; subsequent calls for the same key return ``False`` even after
    eviction is unlikely to happen within Slack's 3-minute retry window.
    """

    def __init__(self, max_size: int = _DEFAULT_MAX_SIZE) -> None:
        """Initialize an empty cache bounded by ``max_size`` entries."""
        if max_size <= 0:
            msg = "max_size must be a positive integer"
            raise ValueError(msg)
        self._max_size = max_size
        self._items: OrderedDict[str, None] = OrderedDict()
        self._lock = Lock()

    def add(self, key: str) -> bool:
        """Record ``key`` and return ``True`` only on first insertion.

        On a duplicate the cache moves the key to the most-recently-used
        position so legitimately recurring keys stay tracked even when the
        cache is full.
        """
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return False
            self._items[key] = None
            if len(self._items) > self._max_size:
                self._items.popitem(last=False)
            return True

    def __contains__(self, key: object) -> bool:
        """Return whether ``key`` is currently tracked, without touching LRU."""
        with self._lock:
            return key in self._items

    def __len__(self) -> int:
        """Return the number of tracked keys."""
        with self._lock:
            return len(self._items)
