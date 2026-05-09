"""Tests for OpenRouter package-level AI factory registration."""

from __future__ import annotations

import importlib

import ai_client_api.client as client_mod
import pytest
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient

import openrouter_ai_client_impl as openrouter_impl
from ai_client_api import get_client

pytestmark = pytest.mark.unit


def test_importing_openrouter_registers_ai_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing the implementation makes ``ai_client_api.get_client`` usable."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(client_mod, "_client_factory", None)

    importlib.reload(openrouter_impl)

    assert isinstance(get_client(), OpenRouterClient)
