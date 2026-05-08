"""End-to-end live tests for the OpenRouter client.

These tests issue real HTTP requests to OpenRouter and require a valid
``OPENROUTER_API_KEY``.  They run in the ``e2e`` job in CI (which injects
the key from the ``openrouter`` context) and can be run locally with::

    uv run pytest -m e2e src/openrouter_ai_client_impl/tests/

Assertions are **shape-only** — we do not assert specific model output text
because free-tier models are non-deterministic.  We only verify that the
returned objects have the expected structure and plausible values.
"""

from __future__ import annotations

import os

import pytest
from openrouter_ai_client_impl.config import OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient

from ai_client_api import Conversation

pytestmark = [pytest.mark.e2e]

_SKIP_NO_KEY = pytest.mark.skipif(
    not os.environ.get("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set; skipping live e2e test",
)


@_SKIP_NO_KEY
def test_live_ping_round_trip() -> None:
    """``ping`` returns ``True`` for a valid key."""
    config = OpenRouterConfig.from_env()
    client = OpenRouterClient(config)
    assert client.ping() is True


@_SKIP_NO_KEY
def test_live_short_completion() -> None:
    """A minimal completion returns a non-empty response with expected shape."""
    config = OpenRouterConfig.from_env()
    client = OpenRouterClient(config)
    conv = Conversation(system="Respond with a single word.")
    conv.add_user("Say the word: ready")
    response = client.send_message(conv, tools=[], max_steps=1)
    # Shape-only assertions — free models are non-deterministic.
    assert isinstance(response.text, str)
    assert response.text.strip() != ""
    assert response.steps >= 1
    assert isinstance(response.model, str)
    assert response.model in {config.model, config.fallback_model}
    assert response.tokens.total >= 0
