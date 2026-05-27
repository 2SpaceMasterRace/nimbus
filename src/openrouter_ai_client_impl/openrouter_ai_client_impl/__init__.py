"""OpenRouter-backed implementation of the ``ai-client-api`` contract."""

from ai_client_api import register_client_factory
from openrouter_ai_client_impl.config import OpenRouterConfig as OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import (
    OpenRouterClient as OpenRouterClient,
)


def get_client_impl() -> OpenRouterClient:
    """Return an OpenRouter-backed AI client configured from the environment."""
    return OpenRouterClient(OpenRouterConfig.from_env())


register_client_factory(get_client_impl)
