# Sessions and State

Render deployments persist wrapper conversations and request-state records in
Postgres. Local development and tests can still persist conversations as JSON
files under `AI_SESSION_DIR`. When the environment variable is missing, the
fallback default is:

```text
~/.nimbus/sessions/ai_server
```

For Render deployment, set `NIMBUS_STATE_BACKEND=postgres` and `DATABASE_URL`.
Run `uv run python scripts/db/migrate.py` before serving traffic.

## Local conversation files

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

On Render, expiring request state is stored in Postgres so nonce replay,
idempotency, and in-flight turn claims survive process restarts and work across
future replicas. In local fallback mode, entries are small JSON files named by
SHA-256 of the key.

| Namespace | Owner | Purpose |
|---|---|---|
| `signed_request_nonces` | `ai_server.request_state` | Cross-restart replay defense for signed wrapper requests |
| `idempotent_turns` | `ai_server.router` | Cached `ChatTurnResponse` for safe wrapper retries |
| `idempotent_turn_claims` | `ai_server.router` | In-flight duplicate protection before side effects |
| `pending_delete_actions` | `nimbus_runtime.state_store` | Runtime-managed delete confirmations |

Cleanup happens opportunistically when the namespace is read or written.

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
