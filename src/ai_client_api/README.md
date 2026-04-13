# ai-client-api

Provider-agnostic contract for AI chat clients with tool-calling support.

This package defines the `AIClient` abstract base class and shared value types
(`Tool`, `AIResponse`, `Message`, `Conversation`, exception hierarchy). It has
no provider-specific code — concrete implementations (for example,
`openrouter-ai-client-impl`) depend on this package.

## Public surface

```python
from ai_client_api import (
    AIClient,
    Conversation,
    Tool,
    AIResponse,
    AIClientError,
)
```

See the workspace docs under `docs/source/ai-client-*.md` for usage.
