# nimbus-runtime

Transport-neutral chat orchestration for Nimbus.

`nimbus_runtime` is the shared runtime beneath the HTTP AI server and any future
Slack, Discord, CLI, or webhook adapters. It owns session loading/persistence,
per-conversation locking, AI turn execution, storage tool policy, attachment
handling, destructive-action confirmation, and runtime telemetry.

If you are building a new chat frontend, this is the package to integrate with.
Construct `NimbusRuntime`, convert your transport event into `ChatTurnInput`,
call `run_chat_turn()`, and render the returned `ChatTurnResult`.

## Role

This is the transport-neutral runtime. It owns behavior that should be shared by
Slack, HTTP, CLI, and future adapters: sessions, confirmation policy,
attachment handling, tool exposure, and telemetry.

## Dependencies

| Dependency | Why it is here |
| --- | --- |
| `ai-client-api` | Model-provider-neutral AI contract |
| `cloud-storage-api` | Optional storage tool contract |
| `pydantic` | Runtime request/response value models |
| `structlog` | Structured runtime logging |

## Architecture

```text
transport adapter
  ai_server, Slack app, CLI, future frontend
        |
        | ChatTurnInput
        v
nimbus_runtime
        |
        | AIClient                    | CloudStorageClient
        v                             v
openrouter_ai_client_impl        aws_client_impl or aws_client_adapter
```

The runtime deliberately does not import FastAPI, Slack SDKs, OpenRouter SDK
types, or boto3. Provider and transport details stay outside the orchestration
core.

## Public API

### NimbusRuntime

Create one runtime per process and reuse it:

```python
from pathlib import Path

from aws_client_impl.s3_client import get_client_impl as get_storage_client
from nimbus_runtime import NimbusRuntime
from openrouter_ai_client_impl.openrouter_client import get_client_impl as get_ai_client

runtime = NimbusRuntime(
    ai_client=get_ai_client(),
    storage=get_storage_client(),
    session_dir=Path("~/.nimbus/sessions/ai_server").expanduser(),
    system_prompt="You are Nimbus, a cloud-storage assistant.",
    tool_container="my-s3-bucket",
)
```

Use `storage=None` to run without storage tools.

### run_chat_turn

`run_chat_turn()` performs the full turn:

1. Serializes concurrent work for the same `conversation_id`.
2. Loads or creates the persisted conversation.
3. Detects confirmation replies and guarded direct actions.
4. Binds storage tools when a storage client and tool container are available.
5. Calls the configured `AIClient`.
6. Persists the updated session atomically.
7. Records telemetry and returns a transport-neutral result.

```python
from nimbus_runtime.models import ChatTurnInput

turn = ChatTurnInput(
    request_id="req-001",
    conversation_id="slack:T123TEAM:C456CHAN:U789USER",
    platform="slack",
    workspace_id="T123TEAM",
    channel_id="C456CHAN",
    thread_id=None,
    message_id="1713840000.123456",
    user_id="U789USER",
    text="List the files in the bucket.",
    attachments=(),
)

result = await runtime.run_chat_turn(turn)
print(result.outcome)
print(result.text)
```

### get_session_lock

`get_session_lock(session_id)` returns the shared `asyncio.Lock` for a
conversation. `run_chat_turn()` acquires this lock internally. Transport adapters
only need to use it directly for operations that must coordinate with active
turns, such as deleting a session.

```python
from nimbus_runtime import get_session_lock

async with get_session_lock(session_id):
    await delete_session_file(session_id)
```

## Data Models

### ChatTurnInput

| Field | Type | Description |
| --- | --- | --- |
| `request_id` | `str` | Unique request identifier for tracing |
| `conversation_id` | `str` | Stable logical session key |
| `platform` | `str` | Source platform such as `slack` or `cli` |
| `workspace_id` | `str` | Platform workspace/team identifier |
| `channel_id` | `str` | Channel/conversation identifier |
| `thread_id` | `str \| None` | Optional thread identifier |
| `message_id` | `str` | Source message/event identifier |
| `user_id` | `str` | Source user identifier |
| `text` | `str` | User prompt text |
| `attachments` | `tuple[TurnAttachment, ...]` | Optional file references |

### TurnAttachment

| Field | Type | Description |
| --- | --- | --- |
| `platform_file_id` | `str` | Platform-specific file ID |
| `filename` | `str` | Original filename |
| `content_type` | `str` | MIME type |
| `size_bytes` | `int` | Declared file size |
| `content_base64` | `str \| None` | Optional inline content |
| `sha256_hex` | `str \| None` | Optional digest checked against decoded bytes |

### ChatTurnResult

| Field | Type | Description |
| --- | --- | --- |
| `request_id` | `str` | Echo of the request ID |
| `conversation_id` | `str` | Echo of the conversation ID |
| `text` | `str` | Text to render to the user |
| `outcome` | `TurnOutcome` | `reply`, `confirmation_required`, `partial_success`, or `error` |
| `confirmation_required` | `bool` | Whether the user must confirm before execution |
| `confirmation` | `ConfirmationDetails \| None` | Pending action details |
| `suggested_next_actions` | `tuple[str, ...]` | Optional follow-up prompts |
| `model` | `str` | Model that produced the response |
| `steps` | `int` | Agent loop steps used |
| `fallback_used` | `bool` | Whether the AI client used its fallback model |

### ConfirmationDetails

| Field | Type | Description |
| --- | --- | --- |
| `action_id` | `str` | Pending action ID |
| `kind` | `str` | Action kind, currently `delete_file` |
| `prompt` | `str` | Prompt to display to the user |
| `expected_reply` | `str` | Exact confirmation reply required |
| `expires_at` | `str` | ISO-8601 expiry time |

## Storage Tool Policy

The runtime exposes a small, wrapper-safe storage tool set to the AI:

| Tool | Purpose |
| --- | --- |
| `list_files(prefix="")` | List objects in the pinned container |
| `get_file_info(remote_path)` | Fetch metadata for one object |
| `delete_file(remote_path, confirm)` | Delete only after confirmation policy allows it |

The storage container is pinned when the runtime is built. The model cannot
select another bucket by prompt injection. Destructive actions are routed through
the confirmation flow instead of being exposed as ordinary one-shot tool calls.

Attachment uploads are handled by the runtime when the user asks to upload
attached files. The runtime validates inline bytes and optional SHA-256 digests
before writing to storage.

## Action Ledger

Side-effecting runtime work is stored in `nimbus_runtime.sqlite3` under the
session directory. The ledger keeps actions, ordered session events, and
artifacts in one SQLite database so action state and audit events commit or roll
back together.

Action input, result, and failure payloads are typed domain records such as
`DeleteFileInput`, `UploadAttachmentInput`, `DeleteFileResult`,
`UploadAttachmentResult`, and `ActionFailure`. Wrapper responses still expose
small JSON-safe summaries.

## Confirmation Flow

Delete requests are intentionally two-step:

1. User asks to delete a remote path.
2. Runtime stores a pending action and returns `confirmation_required`.
3. Frontend renders the prompt and expected reply.
4. User sends the expected confirmation phrase.
5. Runtime executes the delete and clears the pending state.

Pending confirmations expire after `NIMBUS_PENDING_DELETE_TTL_SECONDS`.

## State Store

The state store is a JSON-file-backed expiring key-value store used by adapters
for small retry/replay state. Runtime-managed actions and confirmations live in
the SQLite action ledger instead.

```python
import time

from nimbus_runtime.state_store import delete_state, get_state, put_state

put_state(
    namespace="my_adapter",
    key="pending_action:conv-123",
    value={"action": "delete", "path": "reports/old.csv"},
    expires_at=time.time() + 900,
)

entry = get_state("my_adapter", "pending_action:conv-123")
if entry.value is not None:
    print(entry.value["path"])

delete_state("my_adapter", "pending_action:conv-123")
```

State lives under `AI_SESSION_DIR/_request_state/<namespace>/` so it survives
process restarts when `AI_SESSION_DIR` is backed by a persistent volume.

## Telemetry

`runtime_telemetry` is an in-memory metrics collector used by the runtime and
HTTP adapter. It records counters and histogram summaries with stable metric
names plus labels.

```python
from nimbus_runtime.telemetry import runtime_telemetry

runtime_telemetry.record_wrapper_turn(
    platform="slack",
    outcome="reply",
    latency_ms=342.0,
)

snapshot = runtime_telemetry.snapshot()
assert (
    snapshot["counters"]["nimbus_wrapper_turns_total|outcome=reply,platform=slack"]
    == 1
)

runtime_telemetry.reset()
```

Record methods:

| Method | Use |
| --- | --- |
| `record_wrapper_turn(platform, outcome, latency_ms)` | End of each chat turn |
| `record_idempotent_replay(backend)` | Cached idempotency response |
| `record_auth_result(mechanism, result, reason)` | Auth success/failure |
| `record_ai_response(model, latency_ms, fallback_used, stop_reason)` | AI call success |
| `record_ai_failure(error_kind)` | AI call failure |
| `record_tool_call(tool_name, success, latency_ms)` | Tool completion |

## Environment

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `AI_SESSION_DIR` | No | `~/.nimbus/sessions/ai_server` | Session and state directory |
| `NIMBUS_PENDING_DELETE_TTL_SECONDS` | No | `900` | Pending delete confirmation lifetime |
| `NIMBUS_CONTAINER` | Storage tools | `$AWS_BUCKET_NAME` | Bucket/container pinned to tools |

## Failure Model

| Failure | Runtime behavior |
| --- | --- |
| AI provider error | Returns `outcome="error"` instead of raising to transport |
| Tool execution error | Feeds error back through the AI/tool loop when possible |
| Expired confirmation | Treats the reply as a fresh request |
| Corrupt session file | Logs and resets that session instead of crashing the process |
| Concurrent same-session turns | Per-session lock serializes execution |
| Missing storage client/container | Runs without storage tools |
| Event append failure during action write | Rolls back the matching action change |

The SQLite-backed action ledger is production-credible for the current
single-node deployment. Move to Postgres or another shared transactional backend
before scaling to multiple writable instances.

## Tests

```bash
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/ -q
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/test_runtime.py -v
```

Tests use fake AI and storage clients. No OpenRouter key, AWS credentials, or
network access is required.

## Full Documentation

- `docs/source/nimbus-ai-service.md`
- `docs/source/nimbus/sessions.md`
- `docs/source/nimbus/attachments.md`
- `src/ai_server/README.md`
