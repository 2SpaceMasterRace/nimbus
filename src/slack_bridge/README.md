# slack_bridge

Standalone Slack-facing service that translates Slack Events API webhooks
and `/nimbus` slash commands into signed Nimbus turns and posts the AI
reply back to Slack.

The canonical reference lives in the Sphinx docs:

- Reference: `docs/source/nimbus/slack-bridge.md` (Sphinx target: `nimbus/slack-bridge`)
- Wrapper contract consumed downstream: `docs/source/nimbus/bridge-contract.md`
- Deployment shape and operational signals: `docs/source/deployment-operations.md`

Build the docs locally with:

```shell
uv run sphinx-build docs/source docs/build/html
```

## Quick local run

```shell
export SLACK_SIGNING_SECRET="dev-slack-signing-secret"
export SLACK_BOT_TOKEN="xoxb-dev-token"
export AI_SERVER_BASE_URL="http://localhost:8000"
export AI_SERVER_SIGNING_SECRET="dev-wrapper-signing-secret"

uv run --package slack-bridge \
  uvicorn slack_bridge.main:app --reload --port 8080
```

## Tests

```shell
uv run --package slack-bridge pytest src/slack_bridge/tests/ -q
```

## Container build

The build context must be the repository root because the bridge depends on
the `nimbus_runtime` workspace package:

```shell
docker build -f src/slack_bridge/Dockerfile -t nimbus-slack-bridge .
```

## Deployment constraint

The bridge holds in-memory LRU dedupe caches for Slack retries. The Fly app
is pinned to one machine until those caches are moved to a shared store —
see the deployment doc for details.
