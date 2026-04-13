"""Abstract contract every AI client implementation satisfies.

Callers depend only on this ABC and the shared ``models`` vocabulary; they
never import a provider SDK. The ABC is deliberately narrow: a single
``send_message`` entry point covers one-shot chats, multi-turn conversations,
and the agentic tool-calling loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ai_client_api.conversation import Conversation
    from ai_client_api.models import AgentEvent, AIResponse, EventListener, Tool


class AIClient(ABC):
    """Abstract AI client with tool-calling and event observability."""

    @abstractmethod
    def send_message(
        self,
        prompt: str | Conversation,
        *,
        tools: Sequence[Tool] | None = None,
        max_steps: int = 5,
        dry_run: bool = False,
        stream: bool = False,
    ) -> AIResponse:
        """Send a message and return the assistant's final reply.

        Args:
            prompt: Either a plain string (one-shot turn) or a ``Conversation``
                whose state will be mutated in place to include the final
                exchange.
            tools: Tools the model may call. If ``None`` or empty, the model
                cannot invoke tools. Each tool's handler is invoked with
                keyword arguments matching its JSON schema.
            max_steps: Maximum LLM calls in the agentic loop before
                raising :class:`~ai_client_api.exceptions.AIStepBudgetExceededError`.
                One step is one call to the provider, regardless of how many
                tools fire within that response.
            dry_run: If ``True``, tool handlers are not executed; the loop
                records the tool calls that *would* have run and returns a
                response describing them. Useful for previews and tests.
            stream: If ``True``, the implementation streams tokens and emits
                ``AgentEvent`` notifications as tokens and tool calls arrive.
                The returned ``AIResponse.text`` is still the assembled final
                text. Streaming has no effect on the return shape.

        Returns:
            An :class:`~ai_client_api.models.AIResponse` with the final text,
            token usage, tool-call audit records, and metadata.

        Raises:
            AIAuthenticationError: Provider rejected credentials.
            AIRateLimitError: Provider is rate-limiting the caller.
            AIProviderError: Provider returned a non-auth, non-rate-limit error.
            AITimeoutError: The request exceeded the configured timeout.
            AIStepBudgetExceededError: Loop exceeded ``max_steps``.
            AIToolArgsInvalidError: Model repeatedly produced invalid tool args.
            AIToolExecutionError: Tool handler raised during execution.
            AIUnknownToolError: Model called a tool not in ``tools``.

        """
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> bool:
        """Probe the provider for reachability.

        Returns ``True`` if the provider responds successfully, ``False``
        otherwise. Implementations should not raise; a failure to reach the
        provider is the expected "false" path.
        """
        raise NotImplementedError

    @abstractmethod
    def on_event(self, listener: EventListener) -> None:
        """Register a listener for agent events.

        Listeners are invoked synchronously in registration order. Listener
        exceptions are caught and logged; they never interrupt the agentic
        loop.
        """
        raise NotImplementedError

    def emit(self, event: AgentEvent) -> None:  # pragma: no cover - default impl
        """Emit an event to all registered listeners.

        Implementations are free to override for richer dispatch (thread
        pools, async queues, etc.).
        """
        raise NotImplementedError
