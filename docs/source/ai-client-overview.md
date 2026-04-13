# AI Client Overview

The AI client lets a large-language model drive the existing
`CloudStorageClient` interface — upload, download, list, get info, and
delete — through the OpenRouter API using only free models.

The design follows the same provider-agnostic split you already see in the
cloud-storage packages:

| Package                       | Role                                                    |
| ----------------------------- | ------------------------------------------------------- |
| `ai_client_api`               | Pure ABC + dataclasses. Defines the public contract.    |
| `openrouter_ai_client_impl`   | Concrete backend using the OpenRouter / OpenAI SDK.     |
| `openrouter_ai_client_impl.cloud_storage_tools` | Pydantic-validated tools bound to `CloudStorageClient`. |
| `openrouter_ai_client_impl.cli` (`nimbus`)      | Terminal REPL that wires everything together.           |
| `ai_server`                   | HTTP service that chat wrappers call to use Nimbus.     |

## Why this shape

- **Swappable providers.** Nothing in `ai_client_api` imports `openai`.
  A future `anthropic_ai_client_impl` would only need to implement
  `AIClient`; the CLI and tools do not change.
- **Swappable storage.** The tool bindings accept any `CloudStorageClient`.
  The LLM does not know whether S3, GCS, or an in-memory fake is behind it.
- **Deterministic tests.** The abstract layer is validated by small
  contract tests; the implementation layer is validated with a stubbed
  `openai` SDK, so the test suite stays fast and offline.

## The agentic loop

Each call to `AIClient.send_message(...)` runs a bounded loop:

1. Send the conversation and available tools to the provider.
2. If the model returns plain text, append it and stop.
3. If the model returns one or more tool calls, execute each through the
   Pydantic validator and the pinned `CloudStorageClient`, append the
   results as `tool` messages, and go back to step 1.
4. Stop when the model ends its turn, the step budget (default: 8) is
   exhausted, or an unrecoverable error is raised.

A step counter plus a `max_steps` cap prevents runaway loops. A primary
model plus an optional fallback model absorbs 429 / 5xx hiccups without
breaking the conversation.

## Safety defaults

The client ships with guardrails that are on by default:

- The **container** (S3 bucket) is pinned at tool-bind time. The LLM
  cannot override it from a prompt.
- Local paths for `upload_file` / `download_file` are constrained to a
  caller-provided `safe_root`. Absolute paths and `..` segments are
  rejected before any network I/O.
- `upload_file` refuses anything above 100 MB by default.
- `delete_file` requires `confirm=true`, and the system prompt instructs
  the model to `list_files` before deleting.
- Tool results are wrapped in `<tool_result source="untrusted">` delimiters
  and truncated to 4000 characters before being fed back to the model —
  so a malicious object name cannot smuggle new instructions back in.

See {doc}`ai-client-guardrails` for the full threat model.

## Where to go next

- {doc}`nimbus-ai-service` — the HTTP contract that a Slack or Discord wrapper should call.
- {doc}`ai-client-tutorial` — end-to-end walkthrough with a real file.
- {doc}`ai-client-guardrails` — security and cost guardrails in detail.
- {doc}`ai-client-api` — reference for every public class and function.
