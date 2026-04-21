# Nimbus AI Service

Nimbus AI Service is the HTTP service that a chat wrapper, such as a Slack app,
calls when it wants Nimbus to answer a user message.

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
- lets the model use safe cloud-storage tools,
- returns a structured reply for the wrapper to post back.

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
  "request_id": "req-wrapper-123"
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

### Response body

```json
{
  "request_id": "req-wrapper-123",
  "conversation_id": "slack:T123TEAM:C123CHAN:1713840000.123456",
  "text": "Hello from Nimbus!",
  "outcome": "reply",
  "confirmation_required": false,
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
- `outcome`: current machine-readable result class
- `confirmation_required`: whether the wrapper should treat the reply as a
  confirmation prompt
- `suggested_next_actions`: safe follow-up options
- `model`: AI model used
- `steps`: number of model rounds taken
- `fallback_used`: whether Nimbus had to switch to a fallback model

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
- the cache is in memory in the current service process,
- it is not a cross-process durable idempotency store yet.

So this is already useful for the wrapper team, but it is not yet a globally
durable guarantee across service restarts or multiple replicas.

### Wrapper guidance

- use one stable idempotency key per incoming chat event
- do not generate a new key on a blind retry
- a good Slack key shape is something like:

```text
slack:<workspace_id>:event:<slack_event_id>
```

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
8. Call Nimbus AI Service.
9. Post the returned `text` back into the correct Slack thread.

### Nimbus owns

1. Conversation state.
2. AI provider calls.
3. Storage tool execution.
4. Rate limiting by `user_id`.
5. Session persistence.
6. Model fallback behavior.
7. The final reply text returned to the wrapper.

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

### Tests that define the contract

- `src/ai_server/tests/test_wrapper_contract.py`
  - the best file to read first
  - shows the exact shape Nimbus accepts and returns
- `src/ai_server/tests/test_router.py`
  - covers legacy route behavior and error mapping
- `src/ai_server/tests/test_e2e.py`
  - live deployed-server contract checks

## Minimal Wrapper Pseudocode

```python
slack_event = receive_event_from_slack()
verify_slack_signature(slack_event)

body = {
    "platform": "slack",
    "workspace_id": slack_event.team_id,
    "channel_id": slack_event.channel_id,
    "thread_id": slack_event.thread_ts,
    "message_id": slack_event.message_ts,
    "user_id": slack_event.user_id,
    "text": slack_event.text,
    "idempotency_key": f"slack:{slack_event.team_id}:event:{slack_event.event_id}",
    "request_id": f"req-{slack_event.event_id}",
}

headers = sign_nimbus_request(
    method="POST",
    path="/ai/chat/turn",
    body=body,
    secret=AI_SERVER_SIGNING_SECRET,
)

reply = post_to_nimbus(body=body, headers=headers)
post_reply_to_slack(thread=slack_event.thread_ts, text=reply["text"])
```

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

If the wrapper also logs these fields, debugging across both repos becomes much
easier.

## Running Live End-to-End Tests

Live `ai_server` e2e tests are intentionally opt-in.

You must set:

```bash
export RUN_AI_SERVER_E2E=1
export AI_SERVER_BASE_URL=https://ospsd-team-2.fly.dev
export AI_SERVER_API_KEY=<legacy-api-key-if-testing-legacy-chat-endpoint>
```

Then run:

```bash
uv run pytest src/ai_server/tests/test_e2e.py -v -m e2e
```

This opt-in gate exists so normal local and CI runs stay deterministic even if a
developer happens to have real deployment credentials in their shell.
