"""Tests for Nimbus protocol models."""

from __future__ import annotations

import pytest

from nimbus_protocol import NimbusEvent, event_from_mapping

pytestmark = pytest.mark.unit


def test_event_from_mapping_accepts_valid_replay_event() -> None:
    """Replay event decoding should preserve ordered event identity."""
    event = event_from_mapping(
        {
            "session_id": "sess-1",
            "sequence": 7,
            "event_id": "evt-1",
            "event_type": "text.delta",
            "payload": {"delta": "hi"},
            "turn_id": "turn-1",
            "created_at": "2026-05-08T20:00:00+00:00",
        }
    )

    assert event == NimbusEvent(
        session_id="sess-1",
        sequence=7,
        event_id="evt-1",
        event_type="text.delta",
        payload={"delta": "hi"},
        turn_id="turn-1",
        created_at="2026-05-08T20:00:00+00:00",
    )


def test_event_from_mapping_rejects_non_integer_sequence() -> None:
    """Replay cursors depend on integer sequence values."""
    with pytest.raises(TypeError, match="sequence"):
        event_from_mapping(
            {
                "session_id": "sess-1",
                "sequence": "7",
                "event_id": "evt-1",
                "event_type": "text.delta",
            }
        )
