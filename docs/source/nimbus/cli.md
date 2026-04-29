# Nimbus CLI

Nimbus CLI is the terminal surface for Nimbus. It can run the runtime locally in
the current process, or it can talk to a self-hosted Nimbus server over the same
signed `/ai/chat/turn` boundary used by chat adapters.

Use it when you want to ask questions about storage, test runtime behavior before
deploying Slack, or debug a Nimbus server without opening Slack.

## Install

From the repository root:

```shell
uv sync --all-packages
uv run nimbus --help
```

If you activate the virtual environment, `nimbus` is available directly:

```shell
source .venv/bin/activate
nimbus --help
```

Without an active virtual environment, use `uv run nimbus ...`.

## Configure a Local Profile

A local profile runs `NimbusRuntime` in-process. It is the fastest way to verify
model, runtime, and storage behavior together.

```shell
export OPENROUTER_API_KEY="sk-or-v1-..."

uv run nimbus setup local \
  --openrouter-key "$OPENROUTER_API_KEY"
```

By default, the profile uses `openai/gpt-oss-120b:free`. To enable storage
tools, pin the container the model is allowed to use:

```shell
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="your-bucket"
export NIMBUS_CONTAINER="$AWS_BUCKET_NAME"

uv run nimbus setup local \
  --openrouter-key "$OPENROUTER_API_KEY" \
  --container "$NIMBUS_CONTAINER"
```

Secrets are stored in the OS keyring when possible. In headless environments,
including many containers, Nimbus falls back to `0600` JSON under
`~/.nimbus/secrets.json`. Set `NIMBUS_HOME` to choose a different profile and
secret directory:

```shell
export NIMBUS_HOME="$PWD/.nimbus-dev"
```

## Chat

Send one message:

```shell
uv run nimbus chat "Summarize this project in one sentence." --profile local --no-tools
```

Start a small interactive prompt:

```shell
uv run nimbus chat --profile local
```

With storage tools:

```shell
uv run nimbus chat "List files under reports/." --profile local
```

`chat` starts a fresh session by default. Resume the previous session
explicitly:

```shell
uv run nimbus resume "Continue from the previous answer." --profile local
```

Use a named external session id when you need repeatable local debugging:

```shell
uv run nimbus chat "hello" --profile local --session demo-session
uv run nimbus chat "what did I just ask?" --profile local --session demo-session
```

## Configure a Remote Profile

Remote profiles call a running Nimbus server instead of constructing the runtime
locally. This is useful for staging, production, and self-hosted deployments.

```shell
uv run nimbus setup remote \
  --profile staging \
  --base-url https://nimbus-staging.onrender.com \
  --auth hmac \
  --signing-secret "$AI_SERVER_SIGNING_SECRET"

uv run nimbus chat "hello through HTTP" --profile staging
```

Bearer auth is also supported for environments that expose a bearer-protected
wrapper endpoint:

```shell
uv run nimbus setup remote \
  --profile dev \
  --base-url http://localhost:8000 \
  --auth bearer \
  --token "$AI_SERVER_API_KEY"
```

## Inspect Auth State

```shell
uv run nimbus auth status
```

This shows profiles, their target, auth mode, and whether a secret is present.
It never prints the secret.

## Credentials and `credentials.env`

The CLI onboarding flow is the source of truth. It stores profile metadata under
`NIMBUS_HOME` and secrets in keyring or the fallback secret file.

For developer convenience, `nimbus chat` and `nimbus resume` still load the
first `credentials.env` or `.env` found while walking up from the current
directory. That is best-effort compatibility, not the recommended production
auth path.

## What Runs Locally

Local mode:

```text
nimbus_cli
  -> NimbusRuntime
  -> OpenRouterClient
  -> CloudStorageClient, when a container is configured
```

Remote mode:

```text
nimbus_cli
  -> signed HTTP /ai/chat/turn
  -> ai_server
  -> NimbusRuntime
```

Local streaming writes replayable runtime events as the model produces output.
Remote mode currently renders the server's final turn response.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `Missing command` | Run `nimbus chat`, `nimbus resume`, `nimbus setup`, or `nimbus auth status`. |
| `profile 'local' is missing an OpenRouter API key` | Run `uv run nimbus setup local --openrouter-key "$OPENROUTER_API_KEY"`. |
| Storage requests say no tools are available | Re-run `setup local` with `--container "$NIMBUS_CONTAINER"` and ensure AWS env vars are present. |
| Secrets disappear in a container | Mount a persistent directory and set `NIMBUS_HOME=/path/to/mount`. |
| Remote requests return `401` | Check the remote profile auth mode and `AI_SERVER_SIGNING_SECRET`/bearer token. |
| Remote requests return `503` | The server is missing provider/storage env vars or its readiness dependencies are unhealthy. |
