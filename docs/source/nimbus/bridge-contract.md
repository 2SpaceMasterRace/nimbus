# Signed Wrapper Contract

`POST /ai/chat/turn` is the canonical boundary between a chat-platform wrapper
and Nimbus. The wrapper normalizes platform events into a `ChatTurnRequest`,
signs the HTTP request, and receives a machine-readable `ChatTurnResponse`.

## Canonical request

The wrapper signs this byte string with HMAC-SHA256 using
`AI_SERVER_SIGNING_SECRET`:

```text
METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + SHA256(BODY)
```

For the deployed route:

```text
POST
/ai/chat/turn
<unix timestamp seconds>
<single-use nonce>
<sha256 hex digest of raw request body bytes>
```

The server rejects:

- missing signed-request headers
- timestamps more than five minutes away from server time
- repeated nonces, using both memory and expiring persistent request state
- signatures that do not match the body actually received

## Minimal request body

```json
{
  "platform": "slack",
  "workspace_id": "T123",
  "channel_id": "C123",
  "thread_id": "1714330000.000100",
  "message_id": "1714330000.000200",
  "user_id": "U123",
  "text": "list the files under reports/",
  "idempotency_key": "evt-123",
  "request_id": "req-123",
  "attachments": []
}
```

Nimbus derives the internal conversation ID as:

```text
platform:workspace_id:channel_id:(thread_id or message_id)
```

## Signing example

```python
import hashlib
import hmac
import json
import time
import uuid

secret = "dev-wrapper-signing-secret"
body = json.dumps(
    {
        "platform": "slack",
        "workspace_id": "T123",
        "channel_id": "C123",
        "thread_id": "1714330000.000100",
        "message_id": "1714330000.000200",
        "user_id": "U123",
        "text": "list the files under reports/",
        "idempotency_key": "evt-123",
        "request_id": "req-123",
        "attachments": [],
    },
    separators=(",", ":"),
).encode("utf-8")

timestamp = str(int(time.time()))
nonce = uuid.uuid4().hex
payload = "\n".join(
    [
        "POST",
        "/ai/chat/turn",
        timestamp,
        nonce,
        hashlib.sha256(body).hexdigest(),
    ]
).encode("utf-8")
signature = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
```

Send `body` exactly as signed. Pretty-printing, changing whitespace, or changing
field order after signing changes the body digest.

## Response outcomes

| Outcome | Meaning | Wrapper behavior |
|---|---|---|
| `reply` | Normal assistant response or completed runtime action | Post `text`. |
| `confirmation_required` | Destructive work is pending | Post `text` and wait for the exact `expected_reply`. |
| `partial_success` | Some attachment uploads succeeded and some failed | Post `text`, keep the response visible to the user. |
| `error` | Runtime or model could not complete the request safely | Post `text` or route to support depending on wrapper policy. |

## Idempotency

The cache key is `platform:workspace_id:idempotency_key`. A repeated request
with the same key returns the cached `ChatTurnResponse` while the entry is live.
The default TTL is one hour and can be changed with
`AI_IDEMPOTENCY_TTL_SECONDS`.
