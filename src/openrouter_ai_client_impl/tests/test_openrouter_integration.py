"""Gated live integration test for the OpenRouter client.

This test issues a real HTTP request to OpenRouter and is skipped by default.
Opt in by exporting ``OPENROUTER_API_KEY`` and running with the
``local_credentials`` marker::

    uv run pytest -m local_credentials src/openrouter_ai_client_impl/tests

We keep the test tiny (one round trip, no tools, a short max_tokens) so the
free-tier budget is sufficient and CI stays cheap when someone does enable it.
"""

from __future__ import annotations

import os

import pytest

from ai_client_api import Conversation
from openrouter_ai_client_impl import OpenRouterClient, OpenRouterConfig

pytestmark = [pytest.mark.integration, pytest.mark.local_credentials]


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set; skipping live integration test",
)
def test_live_ping_round_trip() -> None:
    """A ``ping`` against the real API should return ``True`` for a valid key."""
    config = OpenRouterConfig.from_env()
    client = OpenRouterClient(config)
    assert client.ping() is True


@pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set; skipping live integration test",
)
def test_live_short_completion() -> None:
    """A minimal completion against a free model should return non-empty text."""
    config = OpenRouterConfig.from_env()
    client = OpenRouterClient(config)
    conv = Conversation(system="Respond with a single word.")
    conv.add_user("Say the word: ready")
    response = client.send_message(conv, tools=[], max_steps=1)
    # The model is free and non-deterministic; we only assert the shape of the
    # response, not its exact contents.
    assert response.text.strip() != ""
    assert response.steps == 1
    assert response.model in {config.model, config.fallback_model}
