# Smoke Tests

Use these checks when changing the AI wrapper path.

## Import and unit checks

```shell
uv run --package ai-server pytest src/ai_server/tests/ -q
uv run --package openrouter-ai-client-impl pytest src/openrouter_ai_client_impl/tests/ -q
uv run --package nimbus-runtime pytest src/nimbus_runtime/tests/ -q
```

## Local service health

```shell
export SESSION_SECRET_KEY="dev-session-secret"
export API_KEY="dev-storage-api-key"
export AI_SERVER_API_KEY="dev-ai-api-key"
export AI_SERVER_SIGNING_SECRET="dev-wrapper-signing-secret"
export AI_SESSION_DIR="$(pwd)/.nimbus-dev/sessions"

uv run uvicorn aws_client_service.main:app --reload
```

In another shell:

```shell
curl http://localhost:8000/health
curl http://localhost:8000/ai/health
```

## Session endpoint smoke

```shell
curl "http://localhost:8000/ai/sessions/slack:T1:C1:thread1/history" \
  -H "X-API-Key: $AI_SERVER_API_KEY"
```

For a missing session, `404` is correct.

## Signed chat-turn smoke

Use the signing example in {doc}`bridge-contract` to produce the body and
headers. A bad signature should return `401`; a valid signature should enter the
runtime. If `OPENROUTER_API_KEY` is missing and the request requires model
work, `503` is expected.

## Regression checks to keep

- Replayed signed request nonce returns `401`.
- Reused idempotency key returns the same cached response.
- Delete intent returns `confirmation_required` before executing storage work.
- Wrong user cannot confirm another user's delete.
- Attachment upload rejects mismatched `size_bytes` or `sha256_hex`.
