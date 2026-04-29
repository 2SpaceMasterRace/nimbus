# Nimbus Slack

`nimbus_slack` is the Slack channel adapter and workspace control plane for
Nimbus. It accepts Slack Events API callbacks, verifies Slack signatures,
deduplicates Slack retries, calls the signed Nimbus HTTP turn endpoint, and
posts threaded replies back to Slack.

Some Slack intents are adapter-owned because they need Slack workspace state
before model reasoning:

- `@nimbus save all the files in this channel`
- `@nimbus what files in this channel are not saved in my s3 bucket?`

Those commands scan Slack `files.list`, persist current file metadata, compare
against the S3 manifest, download only missing files, upload through the
workspace BYOK AWS credentials, and write manifest evidence before reporting
success.

The package also owns Slack workspace installation state:

- `GET /slack/install` starts Slack OAuth.
- `GET /slack/oauth/callback` exchanges the Slack code, stores the workspace
  bot token encrypted at rest, and mints a short-lived BYOK setup link.
- `GET /slack/setup/{token}` renders the setup page.
- `POST /slack/setup/{token}` stores encrypted OpenRouter and AWS S3
  credentials for that Slack workspace.

BYOK credentials are never accepted through Slack messages. Slack is a command
surface, not the secret-entry trust boundary.

## State Model

The current naked topology is one `nimbus_slack` process and one durable SQLite
database under `NIMBUS_SLACK_STATE_DIR`.

| Table | Purpose |
|---|---|
| `slack_installations` | Installed Slack teams, bot user IDs, scopes, and encrypted bot tokens |
| `tenant_configs` | Encrypted per-workspace OpenRouter and AWS credentials plus S3 bucket/prefix |
| `setup_sessions` | One-time hashed setup tokens with expiry and consumption timestamps |
| `slack_files` | Slack file metadata observed during bounded channel scans |
| `s3_file_manifest` | Durable evidence that a Slack file was saved to a tenant S3 key |

SQLite is enough for the first production deployment because the Slack service
runs as one writable instance. The graduation trigger is explicit: move this
store to Postgres before running multiple writable `nimbus_slack` instances,
before adding background workers that share setup/job state, or when setup/file
operation volume makes a single persistent disk the first bottleneck.

## Deployment Contract

Required for Slack callbacks and OAuth:

| Variable | Purpose |
|---|---|
| `SLACK_SIGNING_SECRET` | Verifies Slack callback signatures |
| `SLACK_CLIENT_ID` | Slack OAuth client ID |
| `SLACK_CLIENT_SECRET` | Slack OAuth client secret |
| `NIMBUS_SLACK_PUBLIC_BASE_URL` | Public HTTPS base URL used in OAuth and setup links |
| `NIMBUS_SLACK_STATE_SECRET` | HMAC secret for OAuth state |
| `NIMBUS_SLACK_SECRET_KEY` | Fernet key for encrypting Slack and BYOK secrets |
| `NIMBUS_SLACK_STATE_DIR` | Directory containing the SQLite database |
| `NIMBUS_SLACK_SESSION_DIR` | Directory for tenant-local Nimbus runtime sessions |
| `NIMBUS_SLACK_MODEL_MODE` | `remote` for `ai_server`, `tenant-local` for BYOK in-process model calls |
| `NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE` | Slack `files.list` count per page, default `100` |
| `NIMBUS_SLACK_FILE_SCAN_MAX_PAGES` | Maximum Slack file pages per request, default `3` |
| `NIMBUS_SLACK_MAX_FILE_BYTES` | Maximum Slack file download size, default `26214400` |
| `AI_SERVER_BASE_URL` | Nimbus HTTP service base URL |
| `AI_SERVER_SIGNING_SECRET` | HMAC secret for signed `/ai/chat/turn` calls |

Generate `NIMBUS_SLACK_SECRET_KEY` locally with:

```shell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`SLACK_BOT_TOKEN` is only a local single-workspace fallback. Multi-workspace
deployments should rely on OAuth-installed workspace tokens.

Set `NIMBUS_SLACK_MODEL_MODE=tenant-local` when Slack model turns must use the
workspace's stored OpenRouter key. In `remote` mode, ordinary chat turns still
call `AI_SERVER_BASE_URL`; adapter-owned file commands always use the tenant
AWS S3 credentials.

## Failure Behavior

- Missing or invalid Slack signatures return `401` before event parsing.
- Duplicate Slack events are acknowledged and ignored before any Nimbus turn.
- Invalid OAuth state is rejected before code exchange.
- Slack OAuth transport failures become `502` responses.
- Setup tokens are stored only as SHA-256 hashes and are one-time, expiring
  bearer tokens.
- Tenant setup writes the BYOK config and consumes the setup token in one
  SQLite transaction.
- File saves are idempotent through `s3_file_manifest`: a Slack retry or a
  repeated user command skips already-recorded files.
- File scans are bounded by page count and byte limits; if Slack has more pages
  than the configured bound, the reply says the scan was truncated.
- If the encrypted store is unavailable or the Fernet key is missing, setup
  and OAuth fail closed with `503`.
- Tenant-local model mode fails closed when BYOK setup is missing, instead of
  silently using a shared model-provider key.

## Render Notes

`render.yaml` deploys `nimbus-slack-staging` and
`nimbus-slack-production` as paid single-instance services with a persistent
disk mounted at `/var/data/nimbus-slack`. Render free web services have an
ephemeral filesystem, so they are not appropriate for the SQLite control plane.

The next scale step is not adding more app instances blindly. The next step is a
shared Postgres control-plane store, durable file-operation jobs, and worker
idempotency keys so Slack retries, background retries, and user replays all
observe the same authoritative action state.
