# Nimbus AI Service

Nimbus AI Service is the HTTP service that a chat wrapper, such as a Slack app,
calls when it wants Nimbus to answer a user message.

For the rest of this guide, the preferred name for that integration layer is the
**Nimbus Chat Bridge**. The first concrete bridge is the **Nimbus Slack Bridge**.
Older references to a "wrapper" mean the same bridge layer.

If you are new to the project, the shortest mental model is:

```text
chat app -> Nimbus AI Service -> AI model + storage tools -> reply
```

The chat app owns chat-platform details such as Slack signatures, Slack bot
tokens, slash commands, and posting messages back into channels. Nimbus owns the
AI conversation, the storage operations, the safety rules, and the final reply.

## What This Service Does

Nimbus AI Service:

- accepts a normalized chat message from a wrapper,
- remembers the conversation for that chat thread,
- calls the AI provider,
- lets the model use chat-safe cloud-storage tools,
- returns a structured reply for the wrapper to post back.

Current wrapper-route storage surface:

- `list_files(prefix="")`
- `get_file_info(remote_path)`
- runtime-managed `delete_file` with explicit confirmation state
- runtime-managed bounded attachment uploads when the wrapper provides inline bytes

The AI-facing tool surface for the wrapper path remains read-only. Destructive
delete and attachment upload flows are handled explicitly by the runtime so the
wrapper contract stays machine-readable and confirmation-safe.

Nimbus AI Service does **not**:

- verify Slack signatures,
- hold Slack bot tokens,
- receive Slack webhooks directly,
- format Slack block-kit payloads.

Those are wrapper responsibilities.

## Two HTTP Paths

There are currently two ways to call the service.

### 1. Legacy path: `POST /ai/chat`

This is the older API.

- auth: `X-API-Key`
- request shape: simple `message`, `session_id`, optional `user_id`
- intended users: existing/manual callers and older integrations

This path still exists for backward compatibility.

### 2. Wrapper path: `POST /ai/chat/turn`

This is the new canonical boundary for the future Slack wrapper and other chat
wrappers.

- auth: signed request headers
- request shape: chat-neutral normalized fields
- intended users: chat wrappers such as Slack, Discord, or Telegram adapters

If you are building a new chat wrapper, **use `POST /ai/chat/turn`**.

## What Is `X-API-Key`?

`X-API-Key` is a legacy shared-secret header.

Example:

```text
X-API-Key: my-secret-value
```

The server compares it against `AI_SERVER_API_KEY`.

Why it exists:

- it was the simplest way to protect the original `/ai/chat` endpoint,
- it works for older callers,
- it is easy to test locally.

Why the wrapper should avoid it:

- it is a long-lived shared secret,
- it carries less context than a signed request,
- it is not the strongest service-to-service boundary we can offer.

So the wrapper should use signed headers on `POST /ai/chat/turn`, not
`X-API-Key` on `POST /ai/chat`.

## The Wrapper-Facing Contract

### Endpoint

```text
POST /ai/chat/turn
```

### Request body

```json
{
  "platform": "slack",
  "workspace_id": "T123TEAM",
  "channel_id": "C123CHAN",
  "thread_id": "1713840000.123456",
  "message_id": "1713840000.123457",
  "user_id": "U123USER",
  "text": "What files are under reports/?",
  "idempotency_key": "slack:T123TEAM:event:evt-123",
  "request_id": "req-wrapper-123",
  "attachments": [
    {
      "platform_file_id": "F123FILE",
      "filename": "report.csv",
      "content_type": "text/csv",
      "size_bytes": 183210
    }
  ]
}
```

### What each field means

- `platform`: chat provider name, such as `slack`
- `workspace_id`: workspace or team identifier
- `channel_id`: channel or DM identifier
- `thread_id`: thread anchor if the platform has one
- `message_id`: unique source message identifier
- `user_id`: the chat user who sent the message
- `text`: plain text to send to Nimbus
- `idempotency_key`: retry-safe logical request key
- `request_id`: optional correlation ID for logs and traces
- `attachments`: optional wrapper-owned attachment metadata for this turn

### Response body

```json
{
  "request_id": "req-wrapper-123",
  "conversation_id": "slack:T123TEAM:C123CHAN:1713840000.123456",
  "text": "Hello from Nimbus!",
  "outcome": "reply",
  "confirmation_required": false,
  "confirmation": null,
  "suggested_next_actions": [],
  "model": "test-model:free",
  "steps": 1,
  "fallback_used": false
}
```

### What the response means

- `request_id`: correlation ID to join logs across systems
- `conversation_id`: Nimbus's normalized conversation key
- `text`: what the wrapper should post back to the user
- `outcome`: machine-readable result class: `reply`, `confirmation_required`,
  `partial_success`, or `error`
- `confirmation_required`: whether the wrapper should treat the reply as a
  confirmation prompt
- `confirmation`: explicit pending-action metadata for destructive flows, or
  `null` when no confirmation is pending
- `suggested_next_actions`: safe follow-up options
- `model`: AI model used, or `nimbus-runtime` when the runtime handled the turn
  directly without a model round
- `steps`: number of model rounds taken; `0` for direct runtime-managed actions
- `fallback_used`: whether Nimbus had to switch to a fallback model

### Outcome examples

Normal reply:

```json
{
  "request_id": "req-slack-evt-123",
  "conversation_id": "slack:T123TEAM:C123CHAN:1713840000.123456",
  "text": "I found 4 files under `reports/2026/`.",
  "outcome": "reply",
  "confirmation_required": false,
  "confirmation": null,
  "suggested_next_actions": [
    "inspect a file",
    "summarize a text object"
  ],
  "model": "test-model:free",
  "steps": 1,
  "fallback_used": false
}
```

Confirmation-required delete:

```json
{
  "request_id": "req-slack-evt-456",
  "conversation_id": "slack:T123TEAM:C123CHAN:1713840000.123456",
  "text": "I can delete `reports/2024/old.csv`, but this is destructive. Reply with `yes, delete reports/2024/old.csv` if you want me to proceed.",
  "outcome": "confirmation_required",
  "confirmation_required": true,
  "confirmation": {
    "action_id": "act-abc123",
    "kind": "delete_file",
    "prompt": "I can delete `reports/2024/old.csv`, but this is destructive. Reply with `yes, delete reports/2024/old.csv` if you want me to proceed.",
    "expected_reply": "yes, delete reports/2024/old.csv",
    "expires_at": "2026-04-21T20:15:00+00:00"
  },
  "suggested_next_actions": [
    "yes, delete reports/2024/old.csv"
  ],
  "model": "nimbus-runtime",
  "steps": 0,
  "fallback_used": false
}
```

Partial-success upload:

```json
{
  "request_id": "req-slack-evt-789",
  "conversation_id": "slack:T123TEAM:C123CHAN:1713840000.123456",
  "text": "Uploaded 1 attachment(s) to `finance/april/`, but skipped 1: report-too-large.csv (attachment bytes were not provided).",
  "outcome": "partial_success",
  "confirmation_required": false,
  "confirmation": null,
  "suggested_next_actions": [
    "list files under finance/april"
  ],
  "model": "nimbus-runtime",
  "steps": 0,
  "fallback_used": false
}
```

Structured error outcome:

```json
{
  "request_id": "req-slack-evt-999",
  "conversation_id": "slack:T123TEAM:C123CHAN:1713840000.123456",
  "text": "Only the original requester can confirm this delete. The pending action is still `yes, delete reports/2024/old.csv`.",
  "outcome": "error",
  "confirmation_required": false,
  "confirmation": null,
  "suggested_next_actions": [
    "yes, delete reports/2024/old.csv"
  ],
  "model": "nimbus-runtime",
  "steps": 0,
  "fallback_used": false
}
```

## Conversation Identity

Nimbus derives conversation identity from chat fields.

Rule:

```text
platform:workspace_id:channel_id:(thread_id or message_id)
```

Examples:

- `slack:T123TEAM:C123CHAN:1713840000.123456`
- `slack:T123TEAM:C123CHAN:1713840000.123457`

This means:

- if the wrapper sends `thread_id`, Nimbus keeps one conversation per thread
- if `thread_id` is absent, Nimbus uses `message_id` as the conversation anchor

The wrapper therefore controls how chat turns map into Nimbus memory.

Nimbus persists this logical `conversation_id` safely even when the derived
value is longer than the on-disk filename limit. The wrapper should therefore
treat `conversation_id` as a stable logical identity, not as a filesystem key.

## Signed Request Authentication

The wrapper route uses signed headers.

Required headers:

- `X-Nimbus-Timestamp`
- `X-Nimbus-Nonce`
- `X-Nimbus-Signature`

Nimbus expects the signature to be:

```text
hex(HMAC_SHA256(
  AI_SERVER_SIGNING_SECRET,
  METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + SHA256(BODY)
))
```

In plain English:

1. hash the exact HTTP body bytes with SHA-256
2. build one canonical string from method, path, timestamp, nonce, and body hash
3. sign that string with HMAC-SHA256 using the shared signing secret
4. send the result in `X-Nimbus-Signature`

Nimbus then checks:

- the signing secret is configured,
- the timestamp is fresh,
- the nonce was not already used,
- the signature matches the request.

Nonce replay state is persisted under `AI_SESSION_DIR` so successful signed
requests are still protected against replay after a service restart.

Current deployment assumption:

- one Nimbus machine
- one uvicorn process
- persistent volume mounted at `AI_SESSION_DIR`

That is enough for restart-safe replay protection today, but it is not a
multi-machine shared replay store yet.

## Idempotency

Idempotency means: if the wrapper retries the same logical request, Nimbus
should avoid doing the expensive work twice when possible.

For the wrapper path, Nimbus uses:

```text
platform + workspace_id + idempotency_key
```

as the retry identity.

### Why the wrapper needs this

Chat systems retry.

Networks fail.

Service calls time out.

Without idempotency, one Slack event could cause:

- two model calls,
- duplicated storage actions,
- duplicated chat replies.

### Current behavior

Nimbus currently provides **best-effort idempotent replay**.

That means:

- repeated calls with the same idempotency key return the cached response,
- the AI model is not called again for that duplicate request,
- the cache is persisted under `AI_SESSION_DIR`, so it survives service restarts,
- the current production shape still assumes one Nimbus machine and one process,
- it is not yet a multi-machine shared idempotency backend.

So this is already useful for the wrapper team and safe across restarts on the
current Fly.io deployment shape, but it is not yet a globally shared guarantee
across multiple machines.

### Wrapper guidance

- use one stable idempotency key per incoming chat event
- do not generate a new key on a blind retry
- a good Slack key shape is something like:

```text
slack:<workspace_id>:event:<slack_event_id>
```

## Slack Event To Nimbus Request Mapping

This table shows how a Slack wrapper should map incoming Slack events into the
Nimbus `POST /ai/chat/turn` request body.

### Message event or app mention

Recommended rule:

- `thread_id = event.thread_ts if present, else event.ts`
- this applies to top-level channel messages, app mentions, and direct messages
- a reply inside an existing thread keeps using `event.thread_ts`

That rule means a top-level Slack message starts a thread-scoped Nimbus
conversation, and later replies in that thread continue the same conversation.

Practical Slack semantics:

- top-level mention in a channel: use `event.ts` as both the `thread_id` anchor
  and the first `message_id`
- thread reply: keep `thread_id = event.thread_ts` and `message_id = event.ts`
- direct message to Nimbus: treat the DM channel like any other channel and use
  `event.ts` as the initial thread anchor
- slash command: the wrapper chooses whether it is one-shot or intentionally
  attached to an existing thread; Nimbus will follow whichever `thread_id` the
  wrapper sends

| Nimbus field | Slack source | Wrapper rule |
| --- | --- | --- |
| `platform` | wrapper constant | always `slack` |
| `workspace_id` | top-level `team_id` | copy directly |
| `channel_id` | `event.channel` | copy directly |
| `thread_id` | `event.thread_ts` or `event.ts` | use `event.thread_ts` if present, otherwise use `event.ts` |
| `message_id` | `event.ts` | copy directly |
| `user_id` | `event.user` | copy directly |
| `text` | `event.text` | use plain text; for `app_mention`, strip the leading bot mention first |
| `idempotency_key` | top-level `event_id` + `team_id` | `slack:{team_id}:event:{event_id}` |
| `request_id` | top-level `event_id` | `req-slack-{event_id}` |

### Slash command

Slash commands do not naturally come with a Slack thread anchor, so the wrapper
must choose how to model them.

For a simple one-shot command flow:

| Nimbus field | Slack source | Wrapper rule |
| --- | --- | --- |
| `platform` | wrapper constant | always `slack` |
| `workspace_id` | form `team_id` | copy directly |
| `channel_id` | form `channel_id` | copy directly |
| `thread_id` | wrapper decision | use `None` unless the wrapper is intentionally attaching the command to an existing thread |
| `message_id` | wrapper-generated from `trigger_id` | use a stable synthetic ID such as `cmd:{trigger_id}` |
| `user_id` | form `user_id` | copy directly |
| `text` | form `text` | use the command body without the `/nimbus` prefix |
| `idempotency_key` | `team_id` + `trigger_id` | `slack:{team_id}:command:{trigger_id}` |
| `request_id` | `trigger_id` | `req-slack-cmd-{trigger_id}` |

### What Nimbus derives from this

Nimbus computes:

```text
conversation_id = platform:workspace_id:channel_id:(thread_id or message_id)
```

So for a top-level Slack message:

- `thread_id = event.ts`
- `message_id = event.ts`

For a reply in the thread:

- `thread_id = event.thread_ts`
- `message_id = event.ts`

That keeps the conversation stable for the whole Slack thread.

## Plan Of Action For The Wrapper Team

If the wrapper team wants the fastest path to a working integration, use this
order.

### Phase 1: text-only Slack integration

This phase now also supports read-only storage questions on the Nimbus side.

1. Receive Slack events and slash commands.
2. Verify Slack signatures.
3. Ignore bot/self-noise.
4. Normalize Slack payloads into the Nimbus request shape.
5. Generate idempotency keys.
6. Sign the Nimbus request.
7. Call `POST /ai/chat/turn`.
8. Post the returned `text` back to the right Slack thread.

This phase is fully supported by the current Nimbus contract.

Examples that work in the current contract:

- "What files are under reports/?"
- "Tell me about reports/april.csv"

### Phase 2: operational hardening

1. Add wrapper-side event dedupe by Slack `event_id`.
2. Add wrapper-side correlation logging using `request_id` and `conversation_id`.
3. Add wrapper retry policy with backoff and jitter.
4. Surface `confirmation_required` and the `confirmation` payload cleanly in the
   Slack UX.
5. Keep destructive deletes bound to the same actor and same conversation.

### Phase 3: file and attachment workflows

This phase now has both a stable metadata contract and a bounded byte-ingestion
contract on the Nimbus side.

`POST /ai/chat/turn` accepts a bounded `attachments` array. Nimbus currently uses
that array as validated attachment context for the AI turn. For runtime-managed
upload requests, each attachment may also carry inline bytes as base64 plus an
optional SHA-256 digest. Nimbus still does **not** dereference arbitrary
external URLs on this endpoint.

## Files And Attachments

You are right to call this out: the mapping table above does **not** solve file
sending by itself.

### What works today

- text messages
- app mentions
- thread replies
- direct messages
- slash-command text
- read-only storage questions through `list_files` and `get_file_info`
- runtime-managed delete confirmation flows
- attachment-aware turns through wrapper-provided attachment metadata
- bounded attachment uploads when the wrapper provides inline bytes

### What does not have a full execution path yet

- Slack-native interactive confirmation buttons
- upload workflows where the wrapper wants Nimbus to pull bytes from a URL
- unbounded or streaming attachment ingestion

### Why this is a separate problem

Slack files are not local files on the Nimbus machine.

So the wrapper cannot reuse the CLI mental model of:

```text
local path -> upload to cloud storage
```

Instead, Slack file workflows need a different boundary:

```text
Slack file reference -> wrapper resolves metadata/download -> Nimbus ingests through a chat-safe attachment contract
```

### Attachment request contract

`POST /ai/chat/turn` now accepts an attachment array with this shape:

```json
{
  "attachments": [
    {
      "platform_file_id": "F123",
      "filename": "report.csv",
      "content_type": "text/csv",
      "size_bytes": 183210,
      "content_base64": "c29tZS1ieXRlcy1oZXJl",
      "sha256_hex": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ]
}
```

`content_base64` and `sha256_hex` are optional. Omit them for metadata-only
turns. Include them only when the wrapper wants Nimbus to execute a bounded
runtime-managed upload from the attached bytes.

### Attachment bounds

- maximum attachments per turn: `10`
- maximum declared attachment size: `20 MiB` per attachment
- maximum total decoded inline-byte payload per turn: `20 MiB`
- `content_type` must be a syntactically valid MIME type such as `text/csv`
- `platform_file_id` must be a stable wrapper/platform file identifier
- `content_base64`, when present, must be standard base64 for the attachment
  bytes; Nimbus validates the decoded size against `size_bytes`

### Ownership split

Wrapper owns:

- Slack file discovery
- Slack auth and permission checks
- fetching file bytes from Slack if a future upload workflow needs them
- validating that the file belongs to the triggering workspace/context before
  it is referenced to Nimbus
- deciding which bounded attachments should carry inline bytes to Nimbus

Nimbus owns:

- validating the attachment metadata schema and bounds
- using attachment metadata as AI-turn context
- rejecting oversized or malformed attachment references early
- validating inline attachment bytes when they are provided
- executing runtime-managed uploads into the pinned storage container

Important constraint:

- `POST /ai/chat/turn` is metadata-only by default, with optional inline bytes
  for bounded runtime-managed uploads
- Nimbus does not dereference arbitrary `download_url` values on this endpoint
- the wrapper must fetch Slack bytes itself before sending inline upload payloads

### Exact Slack file -> Nimbus mapping

For a Slack message event with attached files, the wrapper should map each Slack
file object like this:

| Nimbus field | Slack source | Wrapper rule |
| --- | --- | --- |
| `platform_file_id` | `file.id` | copy directly |
| `filename` | `file.name` | copy directly |
| `content_type` | `file.mimetype` | copy directly |
| `size_bytes` | `file.size` | copy directly |

If a Slack file exceeds the Nimbus size bound, the wrapper should omit it from
the `attachments` array and surface that exclusion in its own logs or UX.

If the wrapper wants Nimbus to upload a bounded attachment directly, it should
also include `content_base64` and ideally `sha256_hex` for that attachment.

## Runtime-Managed Delete Flow

Nimbus now handles destructive deletes as an explicit two-step runtime flow:

1. Wrapper sends text such as `delete reports/2024/old.csv`.
2. Nimbus returns `outcome = confirmation_required`, `confirmation_required = true`,
   and a non-null `confirmation` object.
3. The same actor in the same conversation must reply with the exact
   `confirmation.expected_reply` value.
4. Nimbus performs the delete and returns a normal `reply` outcome.

If a different actor or a mismatched path tries to confirm, Nimbus returns
`outcome = error` and leaves the pending action in place.

## Runtime-Managed Attachment Uploads

Nimbus handles bounded attachment uploads directly when the turn text clearly
requests an upload and the wrapper provides inline bytes.

Supported intent shapes today:

- `upload these files to finance/april`
- `upload attached files to finance/april`
- `upload all files in this channel to finance/april`

Behavior:

- Nimbus validates each attachment's base64 and size independently
- each valid attachment is written to a temporary local file and uploaded through
  the pinned `CloudStorageClient`
- if some uploads succeed and others fail, Nimbus returns
  `outcome = partial_success`
- if all uploads fail, Nimbus returns `outcome = error`

This makes partial failure explicit instead of turning one bad attachment into a
silent all-or-nothing outcome.

## `/nimbus recent` Strategy

`/nimbus recent` is a wrapper/product feature, not a transport concern.

For HW3, the recommended backing store is the existing persisted conversation
history. The wrapper can fetch the last few user turns for the current user or
conversation without introducing Redis. A wrapper-local cache is also fine for a
single-instance deployment. Redis only becomes necessary if the wrapper later
needs shared recent-command state across multiple replicas.

Recommended MVP stance:

- keep `/nimbus recent` wrapper-local first
- use Nimbus conversation history only if that is simpler than maintaining a
  small wrapper-local recent-command cache
- do not add a new Nimbus API surface for `/nimbus recent` in the MVP

## Wrapper Team Checklist

If you are building the Slack wrapper, this is what you need to do.

### You own

1. Verify Slack request signatures.
2. Check request freshness and replay safety on the Slack side.
3. Ignore bot/self noise that should not reach Nimbus.
4. Map Slack `team_id + user_id` into the Nimbus actor identity.
5. Normalize Slack messages into the `POST /ai/chat/turn` request body.
6. Generate one idempotency key per Slack event.
7. Compute the Nimbus signed-request headers.
8. Call Nimbus AI Service with a method such as `send_chat_turn_to_nimbus(...)`.
9. Post the returned `text` back into the correct Slack thread.

### Nimbus owns

1. Conversation state.
2. AI provider calls.
3. Storage tool execution.
4. Rate limiting by caller principal.
   For `POST /ai/chat/turn`, Nimbus uses `platform:workspace_id:user_id`.
5. Session persistence.
6. Model fallback behavior.
7. The final reply text returned to the wrapper.

## For The Wrapper Team Specifically

If you are building the Slack middleware against Nimbus, the fastest Python-first
implementation path is:

1. Build one normalizer for Slack message/app-mention/DM events.
2. Build one normalizer for Slack slash commands.
3. Build one Python signer and HTTP client for `POST /ai/chat/turn`.
4. Route the Nimbus response by `outcome`, not by parsing `text` heuristically.
5. Treat `/nimbus recent` as wrapper-owned product behavior backed by persisted
   conversation history or a wrapper-local cache, not as a Nimbus transport
   feature.

Recommended implementation order in the wrapper repository:

1. Normalize top-level mentions, thread replies, and DMs into the canonical
   Nimbus request body.
2. Normalize slash commands into the same request body with a stable synthetic
   `message_id` such as `cmd:{trigger_id}`.
3. Reuse one Python signer for every call to `/ai/chat/turn`.
4. Handle `reply`, `confirmation_required`, `partial_success`, and `error`
   explicitly in the Slack post-back path.
5. Add wrapper-side retry/dedupe using Slack `event_id` or `trigger_id`.
6. Add `/nimbus recent` only after the main turn flow is stable.

Reference helpers now exist in this repository:

- `ai_server.wrapper_client.build_message_event_turn`
- `ai_server.wrapper_client.build_slash_command_turn`
- `ai_server.wrapper_client.encode_turn_body`
- `ai_server.wrapper_client.sign_nimbus_request`

These are intentionally small Python helpers for the wrapper team. They do not
replace Slack verification or wrapper-side dedupe logic.

Current Nimbus-side status for that build order:

- the request/response contract is stable and documented
- Slack mapping rules are explicit for mentions, thread replies, DMs, and slash
  commands
- destructive confirmation outcomes are machine-readable
- bounded attachment uploads and partial-success outcomes are represented
- contract tests in `src/ai_server/tests/test_wrapper_contract.py` exercise these
  shapes directly

## Build The Nimbus Slack Bridge From Scratch

This section is written for the Slack team starting from these repositories:

- concrete chat repo:
  `https://github.com/HarshithKoriRaj/CS-GY-9223-Open-Source/tree/Hw2`
- shared chat API repo:
  `https://github.com/HarshithKoriRaj/Shared-API`

### Objective

Build a Slack-side integration that:

1. receives Slack events and slash commands
2. ACKs Slack quickly
3. normalizes the Slack payload into the Nimbus request shape
4. signs and sends the request to `POST /ai/chat/turn`
5. posts Nimbus's response back to the correct Slack thread or channel
6. handles retries and duplicate Slack deliveries safely
7. treats confirmation and partial-success outcomes explicitly instead of
   parsing human text heuristically

### Definition Of Done

The Nimbus Slack Bridge is done when all of these are true:

1. top-level mentions/messages, thread replies, DMs, and slash commands all
   reach Nimbus successfully
2. the bridge posts replies back into the correct thread or channel context
3. retries reuse the same Nimbus `idempotency_key`
4. delete confirmations are only completed by the same actor in the same
   conversation
5. attachment uploads either succeed, fail cleanly, or surface
   `partial_success` without silent drops
6. the team can run local smoke checks and deployed e2e checks without guessing
   about payload shape or signing

### Clone And Bootstrap

Clone the Slack repo and the shared chat API repo:

```bash
git clone https://github.com/HarshithKoriRaj/CS-GY-9223-Open-Source.git nimbus-slack-bridge
cd nimbus-slack-bridge
git checkout Hw2
uv sync --all-packages --all-groups

git clone https://github.com/HarshithKoriRaj/Shared-API.git ../shared-chat-api
```

If the Slack repo is going to consume the shared API directly as a dependency,
add it explicitly:

```bash
uv add git+https://github.com/HarshithKoriRaj/Shared-API.git
```

### Understand The Chat Vertical Repos First

`Shared-API` is the chat-vertical contract source of truth. At the time of
writing, it defines:

- `send_message(channel_id, text)`
- `get_channels()`
- `get_channel(channel_id)`
- `get_messages(channel_id, limit=10, cursor=None)`
- `get_message(message_id)`
- `delete_message(message_id)`

The Slack team's `Hw2` repo already contains these concrete pieces:

- `components/slack_client_impl`
- `components/chat_client_service`
- `components/chat_client_service_api_client`
- `components/chat_client_adapter`

Important integration note:

- the shared chat API does **not** currently expose a thread-reply argument on
  `send_message`
- Nimbus, however, is thread-centric on Slack
- that means the Nimbus Slack Bridge should remain a **Slack-specific bridge**
  for HW3 instead of forcing thread reply behavior through the shared abstract
  chat API as it exists today

Recommended stance:

- use the shared API to understand the baseline chat contract
- use the Slack repo's concrete Slack-side implementation/service for the actual
  Slack bridge behavior
- do not block Nimbus integration on a shared-API redesign unless the vertical
  explicitly chooses to add thread-aware send semantics

### Recommended Bridge Module Shape

Create a small Slack-specific module in the chat repo such as
`components/nimbus_slack_bridge/`.

Its job is not to implement Slack itself. Its job is to translate between Slack
events and Nimbus.

Recommended methods:

- `handle_message_event(team_id, event_id, event)`
- `handle_slash_command(form)`
- `call_nimbus(body)`
- `post_nimbus_result(channel_id, thread_id, payload)`

Minimal sketch:

```python
from __future__ import annotations

import httpx

from ai_server.wrapper_client import build_message_event_turn
from ai_server.wrapper_client import build_slash_command_turn
from ai_server.wrapper_client import encode_turn_body
from ai_server.wrapper_client import sign_nimbus_request


class NimbusSlackBridge:
    def __init__(self, *, nimbus_base_url: str, nimbus_signing_secret: str) -> None:
        self._nimbus_base_url = nimbus_base_url.rstrip("/")
        self._nimbus_signing_secret = nimbus_signing_secret

    def handle_message_event(
        self,
        *,
        team_id: str,
        event_id: str,
        event: dict[str, object],
    ) -> dict[str, object]:
        body = build_message_event_turn(
            workspace_id=team_id,
            event_id=event_id,
            event=event,
        )
        return self.call_nimbus(body)

    def handle_slash_command(self, *, form: dict[str, str]) -> dict[str, object]:
        body = build_slash_command_turn(
            workspace_id=form["team_id"],
            channel_id=form["channel_id"],
            trigger_id=form["trigger_id"],
            user_id=form["user_id"],
            text=form["text"],
        )
        return self.call_nimbus(body)

    def call_nimbus(self, body: dict[str, object]) -> dict[str, object]:
        body_bytes = encode_turn_body(body)
        headers = sign_nimbus_request(
            body=body_bytes,
            secret=self._nimbus_signing_secret,
        )
        response = httpx.post(
            f"{self._nimbus_base_url}/ai/chat/turn",
            content=body_bytes,
            headers=headers,
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Nimbus returned a non-object JSON payload")
        return payload
```

### Slack-Side Outcome Handling

The bridge should route on `payload["outcome"]`, not on string matching in
`payload["text"]`.

Required handling:

- `reply`: post the returned text normally
- `confirmation_required`: post the text and preserve the same thread context
- `partial_success`: post the text as a non-fatal operational result
- `error`: post the text as a user-safe failure result

### Secrets And CI Inputs

Bridge developers do **not** need Nimbus provider credentials such as
`OPENROUTER_API_KEY`. They are calling the deployed Nimbus service, not the AI
provider directly.

Local bridge development needs:

| Variable | Required | Why |
| --- | --- | --- |
| `AI_SERVER_BASE_URL` | yes | Nimbus base URL, local or deployed |
| `AI_SERVER_SIGNING_SECRET` | yes | Required to sign `POST /ai/chat/turn` |
| `SLACK_CLIENT_ID` | yes, Slack repo side | Existing Slack OAuth/client setup from the chat repo |
| `SLACK_CLIENT_SECRET` | yes, Slack repo side | Existing Slack OAuth/client setup from the chat repo |
| `SLACK_REDIRECT_URI` | yes, Slack repo side | Existing Slack OAuth callback setup |
| `AI_SERVER_API_KEY` | no | Only needed if someone still tests legacy `/ai/chat` |

For this repository's CircleCI setup, the live `ai-e2e-tests` job now expects a
context that provides at least:

| Context | Variables |
| --- | --- |
| `openrouter` (current name) | `AI_SERVER_BASE_URL`, `AI_SERVER_API_KEY`, `AI_SERVER_SIGNING_SECRET` |
| `aws-ospsd` | existing AWS deployment/test credentials |
| `flyio` | `FLY_API_TOKEN` |

Operational recommendation:

- keep using the current `openrouter` context if you want zero config churn
- if you want the context name to match reality better, rename it to something
  like `nimbus-ai-e2e` and move the same values there
- do **not** hand out `AI_SERVER_SIGNING_SECRET` casually outside the bridge team;
  it is the service-to-service secret for the canonical wrapper route

### Verification Path For The Slack Team

Use this order:

1. local unit tests around the bridge's normalization and Nimbus call path
2. local smoke checks against Nimbus with `scripts/ai_server_wrapper_smoke.py`
3. deployed e2e against Nimbus's live `/ai/chat/turn`
4. end-to-end bridge tests inside the Slack repo once Slack credentials are wired

## Files To Read

These are the most important files for the wrapper team.

### Production code

- `src/ai_server/ai_server/router.py`
  - wrapper-facing route definitions
  - request and response models
  - conversation ID derivation
  - idempotency behavior
- `src/ai_server/ai_server/auth.py`
  - signed-request auth rules
  - legacy API-key auth rules
- `src/ai_server/ai_server/wrapper_client.py`
  - Python reference helpers for request normalization and signing
- `src/nimbus_runtime/nimbus_runtime/runtime.py`
  - runtime-managed delete confirmation state
  - attachment upload behavior
  - direct/runtime-owned outcomes that do not require an AI round trip

### Tests that define the contract

- `src/ai_server/tests/test_wrapper_contract.py`
  - the best file to read first
  - shows the exact shape Nimbus accepts and returns
- `src/ai_server/tests/test_router.py`
  - covers legacy route behavior and error mapping
- `src/ai_server/tests/test_e2e.py`
  - live deployed-server contract checks

## Python Reference Flow

```python
from __future__ import annotations

import httpx

from ai_server.wrapper_client import build_message_event_turn
from ai_server.wrapper_client import build_slash_command_turn
from ai_server.wrapper_client import encode_turn_body
from ai_server.wrapper_client import sign_nimbus_request

def send_turn_to_nimbus(*, base_url: str, signing_secret: str, body: dict[str, object]) -> dict[str, object]:
    path = "/ai/chat/turn"
    body_bytes = encode_turn_body(body)
    headers = sign_nimbus_request(body=body_bytes, secret=signing_secret)
    response = httpx.post(f"{base_url}{path}", content=body_bytes, headers=headers)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("Nimbus returned a non-object JSON payload")
    return payload


def handle_message_event(slack_event: dict[str, object], *, team_id: str, event_id: str) -> None:
    body = build_message_event_turn(
        workspace_id=team_id,
        event_id=event_id,
        event=slack_event,
    )
    payload = send_turn_to_nimbus(
        base_url=AI_SERVER_BASE_URL,
        signing_secret=AI_SERVER_SIGNING_SECRET,
        body=body,
    )
    post_reply_to_slack(text=str(payload["text"]))


def handle_slash_command(form: dict[str, str]) -> None:
    body = build_slash_command_turn(
        workspace_id=form["team_id"],
        channel_id=form["channel_id"],
        trigger_id=form["trigger_id"],
        user_id=form["user_id"],
        text=form["text"],
    )
    payload = send_turn_to_nimbus(
        base_url=AI_SERVER_BASE_URL,
        signing_secret=AI_SERVER_SIGNING_SECRET,
        body=body,
    )
    post_reply_to_slack(text=str(payload["text"]))
```

The important parts are:

- one normalizer for Slack message-ish events
- one normalizer for slash commands
- one shared signer for every Nimbus request
- one response handler keyed by `outcome`

You should not need TypeScript or a browser-side SDK to integrate with Nimbus.
A thin Python wrapper service is the intended first implementation path.

## Python Smoke Checks

For local or deployed HTTP contract smoke checks, this repository now ships a
Python `httpx` smoke client:

`scripts/ai_server_wrapper_smoke.py`

Top-level message/mention/thread/DM shape:

```bash
uv run python scripts/ai_server_wrapper_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --signing-secret "$AI_SERVER_SIGNING_SECRET" \
  message-event \
  --workspace-id T123TEAM \
  --event-id evt-123 \
  --channel-id C123CHAN \
  --message-ts 1713840000.123456 \
  --user-id U123USER \
  --text "What files are under reports/?"
```

Slash-command shape:

```bash
uv run python scripts/ai_server_wrapper_smoke.py \
  --base-url http://127.0.0.1:8000 \
  --signing-secret "$AI_SERVER_SIGNING_SECRET" \
  slash-command \
  --workspace-id T123TEAM \
  --channel-id C123CHAN \
  --trigger-id 1337-trigger \
  --user-id U123USER \
  --text "recent"
```

These commands print the exact normalized request body plus the JSON response,
which makes them useful for wrapper-side local integration checks and for quick
validation against a deployed Nimbus service.

## Logging And Observability

Nimbus uses `structlog` for structured logging in the AI, storage, and service
paths.

For the wrapper boundary, this means the most useful shared fields are:

- `request_id`
- `workspace_id`
- `channel_id`
- `user_id`
- `conversation_id`
- `idempotency_key`

Nimbus also records in-process metrics for the wrapper and AI boundaries,
including:

- `nimbus_wrapper_turns_total`
- `nimbus_wrapper_turn_latency_ms`
- `nimbus_wrapper_idempotent_replays_total`
- `nimbus_wrapper_auth_total`
- `nimbus_ai_requests_total`
- `nimbus_ai_latency_ms`
- `nimbus_ai_tool_calls_total`
- `nimbus_ai_tool_latency_ms`

If the wrapper also logs these fields, debugging across both repos becomes much
easier.

## Running Live End-to-End Tests

Live `ai_server` e2e tests are intentionally opt-in.

You must set:

```bash
export RUN_AI_SERVER_E2E=1
export AI_SERVER_BASE_URL=https://ospsd-team-2.fly.dev
export AI_SERVER_API_KEY=<legacy-api-key-if-testing-legacy-chat-endpoint>
export AI_SERVER_SIGNING_SECRET=<wrapper-signing-secret-for-/ai/chat/turn>
```

Then run:

```bash
uv run pytest src/ai_server/tests/test_e2e.py -v -m e2e
```

This opt-in gate exists so normal local and CI runs stay deterministic even if a
developer happens to have real deployment credentials in their shell.
