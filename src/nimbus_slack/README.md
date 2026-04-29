# nimbus-slack

`nimbus-slack` is the Slack adapter for Nimbus. It verifies Slack Events API
requests, acknowledges Slack quickly, deduplicates retry bursts, forwards
canonical turns to `/ai/chat/turn`, and posts Nimbus replies back into the
source Slack thread.

It also owns the Slack control plane for the first production topology:
multi-workspace OAuth installation, encrypted bot-token storage, one-time BYOK
setup links, and encrypted tenant configuration for OpenRouter and AWS S3.

Adapter-owned file commands are handled before model fallback:

- `@nimbus save all the files in this channel`
- `@nimbus what files in this channel are not saved in my s3 bucket?`

These commands scan Slack `files.list`, compare against Nimbus's S3 manifest,
download only missing files, upload them with the workspace's BYOK AWS
credentials, and record manifest evidence so Slack retries do not re-upload the
same file.

## Runtime Contract

- Inbound auth: Slack `X-Slack-Signature` and
  `X-Slack-Request-Timestamp`.
- Outbound auth: Nimbus HMAC signed request headers using
  `AI_SERVER_SIGNING_SECRET`.
- Workspace auth: Slack OAuth stores one bot token per installed `team_id`.
- BYOK auth: workspace OpenRouter and AWS credentials are accepted only through
  short-lived HTTPS setup links, not through Slack messages.
- Event idempotency: Slack `event_id` is used in both the local retry dedupe
  cache and the Nimbus `idempotency_key`.
- Threading: replies are posted to the Slack `thread_ts` when present, or the
  source message timestamp for new threads.

## Environment

| Variable | Required | Description |
| --- | --- | --- |
| `SLACK_SIGNING_SECRET` | Yes | Verifies inbound Slack callbacks |
| `SLACK_CLIENT_ID` | Yes for OAuth | Slack app OAuth client ID |
| `SLACK_CLIENT_SECRET` | Yes for OAuth | Slack app OAuth client secret |
| `NIMBUS_SLACK_PUBLIC_BASE_URL` | Yes for OAuth/setup | Public HTTPS base URL for callback and setup links |
| `NIMBUS_SLACK_STATE_SECRET` | Yes for OAuth | HMAC secret for Slack OAuth state |
| `NIMBUS_SLACK_SECRET_KEY` | Yes for OAuth/setup | Fernet key used to encrypt Slack and BYOK secrets |
| `NIMBUS_SLACK_STATE_DIR` | Recommended | Directory for the SQLite control-plane database |
| `NIMBUS_SLACK_SESSION_DIR` | Optional | Directory for tenant-local Nimbus runtime sessions |
| `NIMBUS_SLACK_MODEL_MODE` | Optional | `remote` for `ai_server`, `tenant-local` for BYOK in-process model calls |
| `NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE` | Optional | Slack `files.list` count per page, default `100` |
| `NIMBUS_SLACK_FILE_SCAN_MAX_PAGES` | Optional | Maximum pages per request, default `3` |
| `NIMBUS_SLACK_MAX_FILE_BYTES` | Optional | Maximum Slack file download size, default `26214400` |
| `SLACK_BOT_TOKEN` | Optional | Local single-workspace fallback for reply posting |
| `AI_SERVER_BASE_URL` | Yes | Nimbus server base URL |
| `AI_SERVER_SIGNING_SECRET` | Yes | Signs outbound `/ai/chat/turn` calls |

Generate `NIMBUS_SLACK_SECRET_KEY` with:

```shell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## Run

```shell
uv run uvicorn nimbus_slack.main:app --reload --port 8080
```

Slack should point its Events API request URL at:

```text
https://<public-host>/slack/events
```

Slack OAuth should point the redirect URL at:

```text
https://<public-host>/slack/oauth/callback
```

In Render, `render.yaml` defines separate `nimbus-slack-staging` and
`nimbus-slack-production` services that run this app with
`scripts/render/start-slack.sh`. The Slack services use a persistent disk for
the SQLite control plane and set `NIMBUS_SLACK_MODEL_MODE=tenant-local` so
Slack model turns use each workspace's stored OpenRouter and AWS keys. Graduate
this store to Postgres when the Slack app needs more than one writable instance
or file operations need background workers.
