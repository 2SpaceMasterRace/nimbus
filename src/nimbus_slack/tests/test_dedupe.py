"""Tests for Slack event dedupe."""

from __future__ import annotations

import pytest
from nimbus_slack.dedupe import SlackEventDedupe

pytestmark = pytest.mark.unit


def test_dedupe_claims_once_until_ttl_expires() -> None:
    """Slack retry bursts should not enqueue duplicate Nimbus turns."""
    dedupe = SlackEventDedupe(ttl_seconds=10)

    assert dedupe.claim("event-1", now=100.0) is True
    assert dedupe.claim("event-1", now=101.0) is False
    assert dedupe.claim("event-1", now=111.0) is True


def test_dedupe_evicts_oldest_when_full() -> None:
    """Memory is bounded by max_entries."""
    dedupe = SlackEventDedupe(ttl_seconds=100, max_entries=1)

    assert dedupe.claim("event-1", now=100.0) is True
    assert dedupe.claim("event-2", now=101.0) is True
    assert dedupe.claim("event-1", now=102.0) is True
