# Nimbus AI Runtime

Nimbus is the AI side of the system. It lets a chat frontend or the CLI ask
natural-language questions about cloud storage while preserving the same
contract boundaries as the storage stack.

## What lives where

| Package | Responsibility |
|---|---|
| `nimbus_protocol` | Shared DTOs for turns, events, errors, approvals, and permissions |
| `ai_client_api` | Provider-agnostic AI contract and conversation/tool models |
| `openrouter_ai_client_impl` | OpenRouter implementation, pydantic-ai loop, provider streaming, model fallback, cloud-storage tool bindings |
| `nimbus_runtime` | Transport-neutral chat orchestration, sessions, stream replay, durable actions, artifacts, confirmation flows, attachment uploads, ACL-aware search projection, telemetry |
| `nimbus_cli` | Python-only CLI with local in-process and remote/self-hosted profiles |
| `nimbus_slack` | Slack Events API adapter with OAuth install, encrypted BYOK setup, Slack file diff/save commands, retry dedupe, and threaded replies |
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
- Policy decisions are typed records stored on actions, including policy
  version, reason, actor, target, and decision.
- Attachment bytes are size bounded and can be checked against `sha256_hex`.
- Runtime-managed deletes and uploads return action and artifact summaries.
- Model-backed turns can stream durable `NimbusEvent` records and replay from a
  sequence cursor.
- Storage tools are pinned to `NIMBUS_CONTAINER` or `AWS_BUCKET_NAME`.
- Search results are filtered by tenant and ACL before scoring, and extracted
  chunk text is treated as untrusted evidence.

## Pages

```{toctree}
:maxdepth: 2

cli
demo-playbook
slack
agent-platform-implementation-1
bridge-contract
sessions
attachments
search
policy
smoke-tests
verification
```

The canonical system design now lives in root `SYSTEM_DESIGN.md`; the
{doc}`../complete-system-design` page is the Sphinx companion. The pages in this
section are the maintained runtime path.
