# Verifying Nimbus

You do not need a container to know whether Nimbus works. Start with cheap local
checks, then move outward only when the previous boundary is green.

## Fast Confidence

Run package tests without cloud credentials:

```shell
uv run pytest --no-cov \
  src/nimbus_cli/tests \
  src/nimbus_slack/tests \
  src/nimbus_runtime/tests/test_runtime.py \
  src/ai_server/tests/test_wrapper_contract.py \
  tests/evals \
  -q
```

Run strict typing on the AI/runtime packages:

```shell
uv run mypy --strict \
  src/nimbus_cli \
  src/nimbus_slack \
  src/nimbus_protocol \
  src/nimbus_runtime \
  src/ai_server \
  src/ai_client_api \
  src/openrouter_ai_client_impl
```

These checks prove the local CLI profile system, Slack event verification and
dedupe, signed wrapper contract, destructive-action guardrails, and replay evals.

## Local CLI Smoke

Use local mode without storage tools first:

```shell
export OPENROUTER_API_KEY="sk-or-v1-..."
export NIMBUS_HOME="$PWD/.nimbus-dev"

uv run nimbus auth
uv run nimbus auth local --openrouter-key "$OPENROUTER_API_KEY" --no-aws
uv run nimbus chat "Reply with exactly: nimbus-ok" --profile local --no-tools
```

Then enable storage tools:

```shell
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="your-dev-bucket"
export NIMBUS_CONTAINER="$AWS_BUCKET_NAME"

# Optional if you keep these values in a gitignored credentials.env:
# uv run nimbus auth paste < credentials.env
uv run nimbus auth local \
  --openrouter-key "$OPENROUTER_API_KEY" \
  --aws-access-key-id "$AWS_ACCESS_KEY_ID" \
  --aws-secret-access-key "$AWS_SECRET_ACCESS_KEY" \
  --aws-region "$AWS_REGION" \
  --container "$NIMBUS_CONTAINER"

uv run nimbus chat "List files under demo/." --profile local
```

This proves the local runtime can reach OpenRouter and the configured storage
backend.

## Self-Hosted HTTP Smoke

The CLI does not need a local HTTP server. Use this smoke only when validating
the deployed/self-hosted API boundary that Slack and other web adapters call.
Run the combined FastAPI app directly:

```shell
SESSION_SECRET_KEY=dev-session-secret \
API_KEY=dev-storage-api-key \
AI_SERVER_API_KEY=dev-ai-api-key \
AI_SERVER_SIGNING_SECRET=dev-wrapper-signing-secret \
OPENROUTER_API_KEY="$OPENROUTER_API_KEY" \
AWS_ACCESS_KEY_ID="$AWS_ACCESS_KEY_ID" \
AWS_SECRET_ACCESS_KEY="$AWS_SECRET_ACCESS_KEY" \
AWS_REGION="$AWS_REGION" \
AWS_BUCKET_NAME="$AWS_BUCKET_NAME" \
NIMBUS_CONTAINER="$NIMBUS_CONTAINER" \
uv run uvicorn aws_client_service.main:app --reload --port 8000
```

In another terminal:

```shell
curl http://localhost:8000/health
curl http://localhost:8000/ready
curl http://localhost:8000/ai/health
```

Then configure a remote CLI profile against the self-hosted server:

```shell
uv run nimbus setup remote \
  --profile local-server \
  --base-url http://localhost:8000 \
  --auth hmac \
  --signing-secret dev-wrapper-signing-secret

uv run nimbus chat "hello through the self-hosted server" --profile local-server
```

This proves the signed wrapper boundary works without Slack.

## Slack Adapter Smoke Without Slack

Run the adapter locally:

```shell
SLACK_SIGNING_SECRET=dev-slack-signing-secret \
SLACK_BOT_TOKEN=xoxb-not-used-for-this-smoke \
AI_SERVER_BASE_URL=http://localhost:8000 \
AI_SERVER_SIGNING_SECRET=dev-wrapper-signing-secret \
uv run uvicorn nimbus_slack.main:app --reload --port 8081
```

Check liveness:

```shell
curl http://localhost:8081/health
```

The unit tests cover Slack signature verification, URL verification, event
dedupe, and background dispatch. For a real Slack round trip, expose port 8081
with a tunneling tool such as ngrok or Cloudflare Tunnel and point Slack's Event
Subscriptions request URL at `https://<tunnel>/slack/events`.

## Render Staging Smoke

After deploying the Render blueprint, check:

```shell
curl https://nimbus-staging.onrender.com/ready
curl https://nimbus-slack-staging.onrender.com/ready
```

Then point a development Slack app at:

```text
https://nimbus-slack-staging.onrender.com/slack/events
```

If Slack accepts URL verification and an `@Nimbus` mention produces a threaded
reply, the public callback path is working.

## What Each Layer Proves

| Check | Proves |
| --- | --- |
| Unit tests | Boundary validation, auth checks, dedupe, state transitions. |
| Runtime evals | Golden safety behavior: confirmation and replay invariants. |
| Local CLI | OpenRouter, runtime, and optional storage in one process. |
| Self-hosted HTTP | Signed `/ai/chat/turn` contract and server wiring. |
| Slack adapter health | Public adapter process can boot with Slack/Nimbus env. |
| Real Slack event | Slack app config, signatures, public URL, bot token, Nimbus backend. |
