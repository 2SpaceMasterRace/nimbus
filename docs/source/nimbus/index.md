# Nimbus AI Runtime

Nimbus is the AI side of the HW3 system. It lets a chat frontend or the CLI ask
natural-language questions about cloud storage while preserving the same
contract boundaries as the storage stack.

## What lives where

| Package | Responsibility |
|---|---|
| `ai_client_api` | Provider-agnostic AI contract and conversation/tool models |
| `openrouter_ai_client_impl` | OpenRouter implementation, pydantic-ai loop, CLI, model fallback, cloud-storage tool bindings |
| `nimbus_runtime` | Transport-neutral chat orchestration, sessions, durable actions, artifacts, confirmation flows, attachment uploads, telemetry |
| `ai_server` | FastAPI wrapper routes, HMAC auth, idempotency, rate limiting, session endpoints |

## Runtime behavior

`NimbusRuntime.run_chat_turn()` decides whether a turn can be handled directly
or needs the model:

| User intent | Runtime path |
|---|---|
| `delete path/to/file.txt` | Create a durable delete action and return `confirmation_required`. |
| `yes, delete path/to/file.txt` | Validate same user, same conversation, same target, then execute and create a delete report artifact. |
| `upload attached files to prefix/` | Validate inline attachments, execute upload actions, and create upload report artifacts. |
| ordinary chat or storage query | Load conversation, call `OpenRouterClient`, and persist the result. |

## Safety defaults

- Wrapper requests are signed with HMAC and checked for freshness and replay.
- Per-user token buckets limit wrapper traffic.
- Idempotency keys make wrapper retries safe.
- Conversation writes use temp-file-then-rename semantics.
- Deletes require exact confirmation.
- Attachment bytes are size bounded and can be checked against `sha256_hex`.
- Runtime-managed deletes and uploads return action and artifact summaries.
- Storage tools are pinned to `NIMBUS_CONTAINER` or `AWS_BUCKET_NAME`.

## Pages

```{toctree}
:maxdepth: 2

agent-platform-design-3
agent-platform-design-2
agent-platform-design
agent-platform-implementation-1
bridge-contract
sessions
attachments
smoke-tests
```

The older {doc}`../nimbus-ai-service` page remains as the long bridge-builder
reference. The pages in this section are the shorter maintained path for HW3.
