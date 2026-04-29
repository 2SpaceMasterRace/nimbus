"""OpenRouter-backed implementation of the ``ai-client-api`` contract.

Public surface:

* :class:`OpenRouterClient` — concrete implementation of
  :class:`ai_client_api.AIClient`.
* :class:`OpenRouterConfig` — environment-driven configuration.
* :func:`get_client_impl` — factory matching the standard
  ``get_client_impl(*, interactive: bool)`` protocol used elsewhere in this
  workspace.
"""

from openrouter_ai_client_impl.config import (
    DEFAULT_FALLBACK_MODEL as DEFAULT_FALLBACK_MODEL,
)
from openrouter_ai_client_impl.config import DEFAULT_MODEL as DEFAULT_MODEL
from openrouter_ai_client_impl.config import (
    DEFAULT_SYSTEM_PROMPT as DEFAULT_SYSTEM_PROMPT,
)
from openrouter_ai_client_impl.config import OpenRouterConfig as OpenRouterConfig
from openrouter_ai_client_impl.openrouter_client import (
    OpenRouterClient as OpenRouterClient,
)
from openrouter_ai_client_impl.openrouter_client import (
    get_client_impl as get_client_impl,
)

__all__ = [
    "DEFAULT_FALLBACK_MODEL",
    "DEFAULT_MODEL",
    "DEFAULT_SYSTEM_PROMPT",
    "OpenRouterClient",
    "OpenRouterConfig",
    "get_client_impl",
]
