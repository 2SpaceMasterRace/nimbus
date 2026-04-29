"""Bounded in-memory dedupe for Slack event callbacks."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

_DEFAULT_TTL_SECONDS = 300.0
_DEFAULT_MAX_ENTRIES = 4096


@dataclass(slots=True)
class SlackEventDedupe:
    """Small TTL cache for Slack event IDs.

    This is intentionally process-local. It handles Slack's normal retry burst
    behavior for the one-process deployment. The graduation trigger is multiple
    writable bridge processes, at which point the same interface should move to
    Redis or the Nimbus request-state store.
    """

    ttl_seconds: float = _DEFAULT_TTL_SECONDS
    max_entries: int = _DEFAULT_MAX_ENTRIES
    _expires_at_by_key: dict[str, float] = field(default_factory=dict)

    def claim(self, key: str, *, now: float | None = None) -> bool:
        """Return true only the first time an event key is seen while live."""
        if not key:
            msg = "dedupe key cannot be empty"
            raise ValueError(msg)
        current = time.monotonic() if now is None else now
        self._cleanup(current)
        if key in self._expires_at_by_key:
            return False
        if len(self._expires_at_by_key) >= self.max_entries:
            oldest_key = min(
                self._expires_at_by_key,
                key=self._expires_at_by_key.__getitem__,
            )
            del self._expires_at_by_key[oldest_key]
        self._expires_at_by_key[key] = current + self.ttl_seconds
        return True

    def _cleanup(self, now: float) -> None:
        expired = [
            key
            for key, expires_at in self._expires_at_by_key.items()
            if expires_at <= now
        ]
        for key in expired:
            del self._expires_at_by_key[key]
