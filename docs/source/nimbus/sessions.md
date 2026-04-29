# Sessions and State

Nimbus persists wrapper conversations as JSON files under `AI_SESSION_DIR`.
When the environment variable is missing, the default is:

```text
~/.nimbus/sessions/ai_server
```

For Fly.io deployment, set `AI_SESSION_DIR=/data/sessions` and mount a
persistent volume at `/data`.

## Conversation files

`nimbus_runtime` validates session IDs, hashes very long IDs, and writes
conversation JSON atomically:

1. Serialize `Conversation.to_json()`.
2. Write the data to a sibling `.tmp` file.
3. Replace the target session file.

This keeps readers from seeing partially written JSON after process failure.

## Locks

`get_session_lock(session_id)` returns a per-session `asyncio.Lock`. Chat turns
for the same conversation run in order; different conversations can proceed
independently.

## Expiring state

| Namespace | Owner | Purpose |
|---|---|---|
| `signed_request_nonces` | `ai_server.request_state` | Cross-restart replay defense for signed wrapper requests |
| `idempotent_turns` | `ai_server.router` | Cached `ChatTurnResponse` for safe wrapper retries |
| `pending_delete_actions` | `nimbus_runtime.state_store` | Runtime-managed delete confirmations |

Entries are small JSON files named by SHA-256 of the key. Cleanup happens
opportunistically when the namespace is read or written.

## Session management endpoints

Read history:

```shell
curl "http://localhost:8000/ai/sessions/slack:T123:C123:thread1/history" \
  -H "X-API-Key: $AI_SERVER_API_KEY"
```

Delete/reset a session:

```shell
curl -X DELETE \
  "http://localhost:8000/ai/sessions/slack:T123:C123:thread1" \
  -H "X-API-Key: $AI_SERVER_API_KEY"
```

Deletion is idempotent. If no session file exists, the response still succeeds
with `deleted: false`.
