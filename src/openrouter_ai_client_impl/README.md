# openrouter-ai-client-impl

OpenRouter-backed implementation of the `ai-client-api` contract, with
tool-calling, streaming, primary-to-fallback model switching on 429/5xx, and
a bounded agentic loop.

## Quick start

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
export OPENROUTER_MODEL="openai/gpt-oss-120b:free"
export OPENROUTER_FALLBACK_MODEL="nvidia/nemotron-3-super:free"
uv run nimbus chat
```

## Public surface

```python
from openrouter_ai_client_impl import OpenRouterClient, get_client_impl
from openrouter_ai_client_impl.cloud_storage_tools import build_cloud_storage_tools
```

See `docs/source/ai-client-tutorial.md` for the end-to-end walkthrough.
