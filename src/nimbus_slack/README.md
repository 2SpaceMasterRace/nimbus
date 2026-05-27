# nimbus-slack

`nimbus-slack` is the Slack adapter for Nimbus. It verifies Slack Events API
requests, acknowledges Slack quickly, deduplicates retry bursts, forwards
canonical turns to `/ai/chat/turn`, and posts Nimbus replies back into the
source Slack thread.

It also owns the Slack control plane for the first production topology:
multi-workspace OAuth installation, encrypted bot-token storage, one-time BYOK
setup links, and encrypted tenant configuration for OpenRouter and AWS S3.

Adapter-owned file commands are handled before model fallback. The parser
recognises the following intents (synonyms in parentheses):

- `@Nimbus save all the files in this channel` (also `upload`, `back up`)
- `@Nimbus save files from #legal and #design`
- `@Nimbus what files in this channel are not saved in my s3 bucket?`
- `@Nimbus what files are in this channel?` (`list`/`show files in here`)
- `@Nimbus which files changed since the last sync?`
- `@Nimbus find duplicate files` (also detects stale manifest entries)
- `@Nimbus find duplicate files in my bucket` checks all Nimbus-saved Slack
  manifest rows for the workspace. It does not scan arbitrary S3 objects that
  were uploaded outside Nimbus Slack.

These commands scan Slack `files.list`, compare against Nimbus's S3 manifest,
download only missing files, upload them with the workspace's BYOK AWS
credentials, and record manifest evidence so Slack retries do not re-upload the
same file.

### Profile timing

Add `--profile-timing` anywhere in an `@Nimbus` message to receive a follow-up
Block Kit card showing how long each step of the request took. The flag mirrors
the Nimbus CLI's `--profile-timing` global option. For demos and investigations,
use `--profile-timings=half|full|hud|waterfall`:

- `half` shows the executive critical path.
- `full` labels each span as measured or opaque.
- `hud` renders a compact game-style bottleneck view.
- `waterfall` shows offsets from Slack event receipt.

- The token is stripped before command parsing, so `@Nimbus status --profile-timing`
  still routes to the `status` adapter command — the LLM and command parser
  never see the flag.
- Spans cover `slack.parse_command`, the adapter or model-turn branch, the
  runtime/remote call (`slack.runtime.tenant_local` or `slack.runtime.remote`),
  and `slack.post_result`. Each span carries small structured metadata
  (`kind`, `outcome`, `model`, etc.) for cross-referencing with logs.
- Profiling is opt-in and zero-cost when absent — the no-op trace skips all
  span recording.

Use it to spot slow steps in the Slack handler when investigating latency
without flipping global telemetry switches.

### Scheduled drift verifier

Nimbus Slack starts a scheduled verifier for BYOK-configured workspaces by
default. The verifier checks saved Slack-file manifest rows against live S3
metadata and posts a channel alert when a saved object is missing or has changed
size/hash. Alerts are claimed durably in `slack_drift_alerts`, so the same
missing object does not spam the channel every interval.

Configuration:

| Variable | Default | Meaning |
| --- | --- | --- |
| `NIMBUS_SLACK_VERIFIER_ENABLED` | `true` | Set `false` to disable the loop. |
| `NIMBUS_SLACK_VERIFIER_INTERVAL_SECONDS` | `300` | Seconds between sweeps. |
| `NIMBUS_SLACK_VERIFIER_INITIAL_DELAY_SECONDS` | `30` | Startup delay before the first sweep. |
| `NIMBUS_SLACK_VERIFIER_MAX_RECORDS` | `500` | Saved manifest rows checked per workspace per sweep. |

### Thread follow-up mode

After an explicit channel mention, Nimbus records that Slack thread as active
for unmentioned follow-up replies. The default follow window is 30 minutes and
can be adjusted with `NIMBUS_SLACK_THREAD_FOLLOW_TTL_SECONDS`.

Example:

```text
@Nimbus save all files in this channel
what changed since the last sync?
find duplicate files in my bucket
```

The first line must mention Nimbus. Later unmentioned replies work only in the
same thread while the follow record is active. Plain top-level channel messages
are ignored, even if the Slack app subscribes to broad message events.

## Use Cases

These are current Slack adapter workflows:

| Situation | Channel | Message |
| --- | --- | --- |
| Employee offboarding asset capture | `#proj-roadmap-h1` | `@Nimbus save all the files in this channel` |
| Pre-audit compliance gap check | `#legal-contracts` | `@Nimbus what files in this channel are not saved in my s3 bucket?` |
| Weekly design sync check | `#design-deliverables` | `@Nimbus which files changed since the last sync?` |
| Pre-rebrand duplicate audit | `#brand-assets` | `@Nimbus find duplicate files` |
| Incident post-mortem archive | `#incident-2026-05-17` | `@Nimbus save all the files in this channel` |
| Contractor project close | `#freelancer-videoprod` | `@Nimbus save all the files in this channel` |
| QBR delivery inventory | `#client-acme-deliverables` | `@Nimbus what files are in this channel?` |

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
| `NIMBUS_SLACK_STORE_BACKEND` | Render free | `sqlite` locally, `postgres` for durable Render state |
| `NIMBUS_SLACK_DATABASE_URL` | Render free | Optional Slack-specific Postgres URL; falls back to `DATABASE_URL` |
| `NIMBUS_SLACK_STATE_DIR` | Local SQLite | Directory for the SQLite control-plane database |
| `NIMBUS_STATE_BACKEND`, `DATABASE_URL` | Tenant-local mode on Render | Use `postgres` plus the Render Postgres URL for Nimbus runtime sessions/actions |
| `NIMBUS_SLACK_SESSION_DIR` | Optional | Ephemeral runtime fallback directory when file state is enabled |
| `NIMBUS_SLACK_MODEL_MODE` | Optional | `auto` by default; `tenant-local` forces BYOK in-process model calls, `remote` forces `ai_server` |
| `NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE` | Optional | Slack `files.list` count per page, default `100` |
| `NIMBUS_SLACK_FILE_SCAN_MAX_PAGES` | Optional | Maximum pages per request, default `3` |
| `NIMBUS_SLACK_MAX_FILE_BYTES` | Optional | Maximum Slack file download size, default `26214400` |
| `NIMBUS_SLACK_SETUP_RATE_LIMIT_RPM` | Optional | Setup-token requests per minute per IP, default `10` |
| `NIMBUS_SLACK_SETUP_RATE_LIMIT_BURST` | Optional | Setup-token request burst per IP, default `10` |
| `NIMBUS_SLACK_SETUP_RATE_LIMIT_MAX_KEYS` | Optional | Bound on tracked client IPs, default `1024` |
| `NIMBUS_SLACK_THREAD_FOLLOW_TTL_SECONDS` | Optional | Seconds Nimbus listens for unmentioned replies in an active Slack thread, default `1800` |
| `SLACK_BOT_TOKEN` | Optional | Local single-workspace fallback for reply posting |
| `AI_SERVER_BASE_URL` | Yes | Nimbus server base URL |
| `AI_SERVER_SIGNING_SECRET` | Yes | Signs outbound `/ai/chat/turn` calls |

Generate `NIMBUS_SLACK_SECRET_KEY` with:

```shell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

This is a symmetric encryption key for Python's Fernet authenticated-encryption
format. Nimbus Slack uses it to encrypt Slack OAuth tokens and workspace BYOK
OpenRouter/AWS credentials before writing them to SQLite or Postgres. Generate
it once per environment, store it only in Render env vars or your secret
manager, and do not rotate it without re-encrypting stored workspace secrets.

## Run

```shell
uv run uvicorn nimbus_slack.main:app --reload --port 8080
```

Slack should point its Events API request URL at:

```text
https://<public-host>/slack/events
```

Slack's **Interactivity & Shortcuts** request URL must be set to:

```text
https://<public-host>/slack/interactive
```

Nimbus uses interactive Block Kit buttons (`[Save all to S3]`,
`[Find duplicates]`, `[Approve]`, `[Reject]`) — without this URL configured,
those buttons silently do nothing.

Slack OAuth should point the redirect URL at:

```text
https://<public-host>/slack/oauth/callback
```

In Render, `render.yaml` defines separate `nimbus-slack-staging` and
`nimbus-slack-production` services that run this app with
`scripts/render/start-slack.sh`. The Slack services use Postgres for their
control plane, `/ready` for the health check, and
`NIMBUS_SLACK_MODEL_MODE=auto` so Slack model turns use each workspace's stored
OpenRouter key after BYOK setup and can fall back to the remote `ai_server`
before setup. Free instances can cold-start; move to an always-on paid instance
when first-request latency becomes unacceptable, and move file saves to durable
jobs when synchronous file operations become the first bottleneck.

## Slack App Settings

For production, configure Slack with:

- Request URL: `https://nimbus-slack-production.onrender.com/slack/events`
- Interactivity & Shortcuts request URL:
  `https://nimbus-slack-production.onrender.com/slack/interactive`
- Redirect URL: `https://nimbus-slack-production.onrender.com/slack/oauth/callback`
- Bot scopes: `app_mentions:read`, `channels:history`, `channels:read`,
  `chat:write`, `files:read`, `groups:history`, `groups:read`, `im:history`,
  `im:read`, `mpim:read`, `users:read`
- Bot events: `app_mention`; add `message.im` when direct messages are enabled
  in App Home. Add `message.channels` and `message.groups` if you want
  unmentioned follow-up replies in active Nimbus threads.
- App Home events: add `app_home_opened` and enable the Home tab in Slack's
  **App Home** settings if you want the Nimbus home dashboard to appear.

Reinstall the app after scope changes. Invite Nimbus into every channel where it
should reply or inspect files.

## Block Kit Responses

Nimbus Slack replies use Block Kit, but the visible copy should read like a
helpful teammate in Slack rather than a terminal dashboard. Start with the
answer, add the smallest useful evidence, and end with the next safe action
when there is one. Avoid emoji status markers and avoid raw markdown markers in
plain-text surfaces such as headers and button labels.

Five representative prompts and expected reply shape:

| Prompt | Reply shape |
|---|---|
| `@Nimbus what files are in this channel?` | "I found 6 files in this channel." Then a short file preview with names, sizes, and types, followed by `Save all to S3`, `Find duplicates`, and `What's missing?` buttons. |
| `@Nimbus save all files in this channel` | "I saved the channel files to S3." Then scanned/saved/skipped counts, the S3 destination, and a small saved-key preview. |
| `@Nimbus save files from #legal and #design` | One summary covering each mentioned channel. Already-recorded Slack files are skipped. |
| `@Nimbus what files are not saved in S3?` | "Some channel files are missing from S3." Then the checked count, destination, and the first unsaved files with a `Save unsaved files` button. |
| `@Nimbus find duplicate files` | Current-channel saved-manifest duplicate/stale report. |
| `@Nimbus find duplicate files in my bucket` | Workspace-wide Nimbus-saved Slack manifest duplicate report; arbitrary bucket uploads are outside this Slack command's current scope. |
| `@Nimbus status` | "Everything looks healthy..." or "I found workspace items that need attention..." Then one sentence with running, awaiting, done, failed, pending approval, and proposed-plan counts. |
| `@Nimbus tools` | "I use a shared runtime tool catalog across Slack and CLI." Then a compact list of live and roadmap capabilities. |

Card renderers live in `nimbus_slack.blocks` as pure functions with no network
calls. `SlackPoster` was extended with `send_blocks()` and `update_blocks()`.
Existing callers that implement only `send_message`/`update_message` continue
to work — `flow.py` catches `AttributeError` and falls back to plain text.

| Card | When posted |
|---|---|
| `file_list_card` | `@Nimbus what files are in this channel?` |
| `diff_report_card` | `@Nimbus what files are not in S3?` |
| `changed_since_sync_card` | `@Nimbus which files changed since the last sync?` |
| `dedupe_report_card` | `@Nimbus find duplicate files` |
| `save_progress_card` | Live edits during `@Nimbus save all files` |
| `save_report_card` | Final save result |
| `approval_request_card` | Destructive action requiring explicit approval |
| `capability_list_card` | `@Nimbus tools`, `@Nimbus what can you do?` |
| `failure_card` | Any adapter-owned command error |

The save command posts a placeholder immediately and edits it in place as files
are uploaded, keeping users informed without flooding the channel. If the
initial post fails, Nimbus still runs the save and posts a single final reply.
