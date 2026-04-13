"""Provider-agnostic AI client contract.

Public surface:

* :class:`AIClient` — the abstract interface every provider implementation
  satisfies.
* :class:`Conversation` — bounded, in-process chat history.
* :class:`Tool`, :class:`ToolCallRequest`, :class:`ToolCallRecord`,
  :class:`Message`, :class:`AIResponse`, :class:`TokenUsage`,
  :class:`AgentEvent`, :class:`AuditEvent`, :class:`Role` — shared value
  types passed across the interface.
* Exception hierarchy rooted at :class:`AIClientError`.
"""

from ai_client_api.client import AIClient as AIClient
from ai_client_api.conversation import Conversation as Conversation
from ai_client_api.exceptions import AIAuthenticationError as AIAuthenticationError
from ai_client_api.exceptions import AIClientConfigError as AIClientConfigError
from ai_client_api.exceptions import AIClientError as AIClientError
from ai_client_api.exceptions import AIProviderError as AIProviderError
from ai_client_api.exceptions import AIRateLimitError as AIRateLimitError
from ai_client_api.exceptions import (
    AIStepBudgetExceededError as AIStepBudgetExceededError,
)
from ai_client_api.exceptions import AITimeoutError as AITimeoutError
from ai_client_api.exceptions import (
    AIToolArgsInvalidError as AIToolArgsInvalidError,
)
from ai_client_api.exceptions import AIToolExecutionError as AIToolExecutionError
from ai_client_api.exceptions import AIUnknownToolError as AIUnknownToolError
from ai_client_api.models import AgentEvent as AgentEvent
from ai_client_api.models import AIResponse as AIResponse
from ai_client_api.models import AuditEvent as AuditEvent
from ai_client_api.models import EventListener as EventListener
from ai_client_api.models import Message as Message
from ai_client_api.models import Role as Role
from ai_client_api.models import StopReason as StopReason
from ai_client_api.models import TokenUsage as TokenUsage
from ai_client_api.models import Tool as Tool
from ai_client_api.models import ToolCallRecord as ToolCallRecord
from ai_client_api.models import ToolCallRequest as ToolCallRequest
from ai_client_api.models import normalize_tools as normalize_tools

__all__ = [
    "AIAuthenticationError",
    "AIClient",
    "AIClientConfigError",
    "AIClientError",
    "AIProviderError",
    "AIRateLimitError",
    "AIResponse",
    "AIStepBudgetExceededError",
    "AITimeoutError",
    "AIToolArgsInvalidError",
    "AIToolExecutionError",
    "AIUnknownToolError",
    "AgentEvent",
    "AuditEvent",
    "Conversation",
    "EventListener",
    "Message",
    "Role",
    "StopReason",
    "TokenUsage",
    "Tool",
    "ToolCallRecord",
    "ToolCallRequest",
    "normalize_tools",
]
