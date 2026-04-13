"""Domain exceptions for the AI client layer.

The hierarchy intentionally separates *configuration* problems (detected at
construction time, before any network I/O) from *runtime* problems (surfaced
during ``send_message``). Callers can then catch at whichever granularity is
appropriate without swallowing bugs.
"""

from __future__ import annotations


class AIClientError(Exception):
    """Base class for all AI-client domain errors."""


class AIClientConfigError(AIClientError):
    """Raised when the client is misconfigured (missing key, bad model id, etc.)."""


class AIAuthenticationError(AIClientError):
    """Raised when the provider rejects the caller's credentials (HTTP 401)."""


class AIRateLimitError(AIClientError):
    """Raised when the provider rate-limits the caller (HTTP 429)."""


class AIProviderError(AIClientError):
    """Raised for upstream provider failures other than auth or rate-limit."""


class AITimeoutError(AIClientError):
    """Raised when a request exceeds the configured timeout."""


class AIStepBudgetExceededError(AIClientError):
    """Raised when the agentic loop exceeds the configured ``max_steps``."""


class AIToolExecutionError(AIClientError):
    """Raised when a tool handler raises an unhandled exception."""

    def __init__(self, tool_name: str, message: str) -> None:
        """Record the tool name so callers can attribute the failure."""
        super().__init__(f"Tool '{tool_name}' failed: {message}")
        self.tool_name = tool_name


class AIToolArgsInvalidError(AIClientError):
    """Raised when the model supplied arguments that fail tool-arg validation."""

    def __init__(self, tool_name: str, message: str) -> None:
        """Record the tool name for diagnostics."""
        super().__init__(f"Invalid arguments for tool '{tool_name}': {message}")
        self.tool_name = tool_name


class AIUnknownToolError(AIClientError):
    """Raised when the model asks to invoke a tool we did not expose."""

    def __init__(self, tool_name: str) -> None:
        """Record the unknown tool name for diagnostics."""
        super().__init__(f"Model requested unknown tool '{tool_name}'")
        self.tool_name = tool_name
