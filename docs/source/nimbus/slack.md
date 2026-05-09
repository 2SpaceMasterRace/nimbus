# Nimbus Slack

`nimbus_slack` is the Slack channel adapter and workspace control plane for
Nimbus. It accepts Slack Events API callbacks, verifies Slack signatures,
deduplicates Slack retries, calls the signed Nimbus HTTP turn endpoint, and
posts threaded replies back to Slack.

Some Slack intents are adapter-owned because they need Slack workspace state
before model reasoning:

- `@nimbus save all the files in this channel`
- `@nimbus save files from #legal and #design`
- `@nimbus what files in this channel are not saved in my s3 bucket?`
- `@nimbus find duplicate files in my bucket`

Those commands scan Slack `files.list`, persist current file metadata, compare
against the S3 manifest, download only missing files, upload through the
workspace BYOK AWS credentials, and write manifest evidence before reporting
success. Workspace-wide duplicate prompts use the Nimbus Slack manifest across
saved Slack files; they do not claim to scan arbitrary S3 objects uploaded
outside Nimbus.

Saved Slack files use readable S3 prefixes:
`{tenant-prefix}/slack/{workspace-name}/{channel-or-chat-name}/{filename}`.
When two files in the same channel share a name, a short deterministic suffix
derived from the Slack file ID is appended before the extension (e.g.
`photo-a1b2c3d4.png`) to avoid collisions. Slack IDs remain the manifest
authority for idempotency, and Nimbus falls back to an ID segment if Slack
does not expose a name during metadata lookup.

## Use Cases

These prompts are adapter-owned today, so Nimbus handles them deterministically
before falling back to the model:

| Situation | Channel | Message |
|---|---|---|
| Employee offboarding asset capture | `#proj-roadmap-h1` | `@Nimbus save all the files in this channel` |
| Pre-audit compliance gap check | `#legal-contracts` | `@Nimbus what files in this channel are not saved in my s3 bucket?` |
| Weekly design sync check | `#design-deliverables` | `@Nimbus which files changed since the last sync?` |
| Pre-rebrand duplicate audit | `#brand-assets` | `@Nimbus find duplicate files` |
| Incident post-mortem archive | `#incident-2026-05-17` | `@Nimbus save all the files in this channel` |
| Contractor project close | `#freelancer-videoprod` | `@Nimbus save all the files in this channel` |
| QBR delivery inventory | `#client-acme-deliverables` | `@Nimbus what files are in this channel?` |

Object deletes use the shared Nimbus runtime confirmation flow rather than a
raw model tool. Send `@nimbus delete path/to/object`, then confirm from the same
Slack user in the same thread with `@nimbus yes, delete path/to/object`.

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

The local naked topology is one `nimbus_slack` process and one durable SQLite
database under `NIMBUS_SLACK_STATE_DIR`. Render free deployments set
`NIMBUS_SLACK_STORE_BACKEND=postgres` so the Slack control plane survives
instance restarts without a persistent disk.

| Table | Purpose |
|---|---|
| `slack_installations` | Installed Slack teams, bot user IDs, scopes, and encrypted bot tokens |
| `tenant_configs` | Encrypted per-workspace OpenRouter and AWS credentials plus S3 bucket/prefix |
| `setup_sessions` | One-time hashed setup tokens with expiry and consumption timestamps |
| `slack_files` | Slack file metadata observed during bounded channel scans |
| `s3_file_manifest` | Durable evidence that a Slack file was saved to a tenant S3 key |
| `slack_thread_follows` | Active Slack threads where Nimbus accepts unmentioned follow-up replies |
| `slack_drift_alerts` | Exactly-once claims for scheduled saved-manifest drift alerts |

SQLite is enough for local development and one durable VM. Postgres is the first
cloud primitive because it removes the persistent-disk requirement and lets all
Slack processes observe the same installation, setup, BYOK, and file-manifest
state. The next graduation trigger is durable background jobs once file saves
regularly exceed Slack request deadlines or need retry after process crashes.

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
| `NIMBUS_SLACK_STORE_BACKEND` | `sqlite` locally, `postgres` on Render free deployments |
| `NIMBUS_SLACK_DATABASE_URL` | Optional Slack-specific Postgres URL; falls back to `DATABASE_URL` |
| `NIMBUS_SLACK_STATE_DIR` | Local-only directory containing the SQLite database |
| `NIMBUS_STATE_BACKEND`, `DATABASE_URL` | Set to `postgres` plus a Postgres URL for tenant-local Nimbus runtime sessions/actions |
| `NIMBUS_SLACK_SESSION_DIR` | Ephemeral runtime fallback path when file state is enabled |
| `NIMBUS_SLACK_MODEL_MODE` | `auto` by default; `tenant-local` forces BYOK in-process model calls, `remote` forces `ai_server` |
| `NIMBUS_SLACK_FILE_SCAN_PAGE_SIZE` | Slack `files.list` count per page, default `100` |
| `NIMBUS_SLACK_FILE_SCAN_MAX_PAGES` | Maximum Slack file pages per request, default `3` |
| `NIMBUS_SLACK_MAX_FILE_BYTES` | Maximum Slack file download size, default `26214400` |
| `NIMBUS_SLACK_THREAD_FOLLOW_TTL_SECONDS` | Seconds Nimbus listens for unmentioned replies in an active Slack thread, default `1800` |
| `NIMBUS_SLACK_VERIFIER_ENABLED` | Enables scheduled saved-manifest drift alerts, default `true` |
| `NIMBUS_SLACK_VERIFIER_INTERVAL_SECONDS` | Seconds between saved-manifest verifier sweeps, default `300` |
| `NIMBUS_SLACK_VERIFIER_INITIAL_DELAY_SECONDS` | Startup delay before the first verifier sweep, default `30` |
| `NIMBUS_SLACK_VERIFIER_MAX_RECORDS` | Saved manifest rows checked per workspace per sweep, default `500` |
| `AI_SERVER_BASE_URL` | Nimbus HTTP service base URL |
| `AI_SERVER_SIGNING_SECRET` | HMAC secret for signed `/ai/chat/turn` calls |

Generate `NIMBUS_SLACK_SECRET_KEY` locally with:

```shell
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

The key is a symmetric Fernet encryption key, not a Slack secret. Nimbus Slack
uses it to encrypt Slack OAuth tokens and workspace BYOK OpenRouter/AWS
credentials before storing them in SQLite or Postgres. Keep one stable key per
environment in Render env vars or a secret manager; rotating it requires
re-encrypting the stored workspace secrets.

`SLACK_BOT_TOKEN` is only a local single-workspace fallback. Multi-workspace
deployments should rely on OAuth-installed workspace tokens.

`NIMBUS_SLACK_MODEL_MODE` defaults to `auto`. In auto mode, ordinary model turns
use the workspace's stored OpenRouter key as soon as BYOK setup exists; before
setup, Nimbus can fall back to `AI_SERVER_BASE_URL` if that remote runtime is
configured. Set `tenant-local` to fail closed until BYOK setup exists, or
`remote` to force all ordinary chat turns through `ai_server`. Adapter-owned
file commands always use the tenant AWS S3 credentials.

### Scheduled saved-manifest verifier

The Slack process starts a scheduled verifier for every active BYOK workspace
unless `NIMBUS_SLACK_VERIFIER_ENABLED=false`. Each sweep reads Nimbus-saved
Slack manifest rows, HEAD-checks the recorded S3 object, and posts a channel
alert when the object is missing or its observed size/hash no longer matches
the receipt. The verifier records an idempotency claim in `slack_drift_alerts`
before posting, so a deleted object alerts once instead of every interval.

This is intentionally scoped to Nimbus-owned evidence: it verifies Slack files
that Nimbus saved and recorded. It does not claim to monitor arbitrary bucket
prefixes that were never saved, protected, or snapshotted by Nimbus. AWS Health
links on the card are advisory context; the live bucket probe is the source of
truth for the alert.

For live demos, shorten the interval in the Slack service environment:

```shell
NIMBUS_SLACK_VERIFIER_INTERVAL_SECONDS=15
NIMBUS_SLACK_VERIFIER_INITIAL_DELAY_SECONDS=2
```

### Profile timing modes

Slack users can append `--profile-timing` to any `@Nimbus` message for the
default half-depth timing card, or `--profile-timings=half|full|hud|waterfall`
for a specific view:

| Mode | Use it for |
|---|---|
| `half` | Executive critical-path budget: parse, adapter/model work, storage, Slack post |
| `full` | Diagnostic tree with measured versus opaque SDK/network spans |
| `hud` | Demo-friendly game-style bars and the current bottleneck |
| `waterfall` | Offset timeline from Slack event receipt to final post |

The flag is stripped before command parsing, so
`@Nimbus status --profile-timings=hud` still routes to the normal status
command. Profiling is opt-in; when no flag is present the trace object skips
span recording.

## Slack App Settings

Use one Slack app per deployed Nimbus Slack service. For the current production
Render service, configure Slack like this:

| Slack page | Setting |
|---|---|
| **Basic Information** | Copy **Signing Secret** into `SLACK_SIGNING_SECRET`. Copy **Client ID** and **Client Secret** into `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET`. |
| **OAuth & Permissions** | Add the redirect URL `https://nimbus-slack-production.onrender.com/slack/oauth/callback`. |
| **OAuth & Permissions → Bot Token Scopes** | `app_mentions:read`, `channels:history`, `channels:read`, `chat:write`, `files:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `mpim:read`, `users:read`. |
| **Event Subscriptions** | Enable events and set the request URL to `https://nimbus-slack-production.onrender.com/slack/events`. |
| **Subscribe to bot events** | Add `app_mention` for channel mentions. Add `message.im` if you want direct-message conversations with the bot. Add `message.channels` and `message.groups` only when you want unmentioned follow-up replies in active Nimbus threads. |
| **Interactivity & Shortcuts** | Enable interactivity and set the request URL to `https://nimbus-slack-production.onrender.com/slack/interactive`. Without this URL, Slack displays "This app is not configured to handle interactive responses" when users click Nimbus buttons. |
| **App Home** | Enable the Home tab and subscribe to `app_home_opened` if you want the Nimbus home dashboard. Turn on the Messages tab if you want users to DM Nimbus. |
| **Manage Distribution** | Keep the app private while testing. Public distribution requires Slack's review and a stable privacy/support story. |

After changing scopes or events, reinstall the app from `/slack/install`; Slack
does not retroactively grant new bot-token scopes to an existing installation.
For channel use, invite Nimbus into the channel with `/invite @Nimbus`. If file
operations target private channels, the app must also be invited into those
private channels.

### Thread follow-up mode

Slack only sends Nimbus unmentioned channel messages when the app subscribes to
the relevant `message.*` events. To avoid turning Nimbus into a channel-wide
listener, the adapter handles unmentioned replies only after a user explicitly
mentions Nimbus in that same Slack thread:

```text
@Nimbus save all files in this channel
what changed since the last sync?
find duplicate files in my bucket
```

The first line activates the thread for
`NIMBUS_SLACK_THREAD_FOLLOW_TTL_SECONDS` seconds. Later unmentioned replies
must stay in that thread; top-level channel messages are ignored.

The fastest product smoke test is:

1. Mention `@Nimbus hello there` in a channel where the bot is invited. This
   exercises event delivery, tenant-local model routing, OpenRouter, and
   `chat.postMessage`.
2. Mention `@Nimbus list file` or `@Nimbus what files in this channel are not
   saved in my s3 bucket?`. This exercises the Slack adapter-owned file path,
   Slack file scopes, BYOK AWS credentials, S3 access, and manifest persistence.
3. If the first works and the second fails, inspect file/AWS settings. If the
   second works and the first fails, inspect `NIMBUS_SLACK_MODEL_MODE`,
   OpenRouter BYOK setup, and Render logs for `slack_event_processing_failed`.

## Block Kit Responses

Nimbus Slack replies use Slack's Block Kit structure, but the visible copy is
conversation-first. A reply should sound like a careful teammate: answer first,
show only the evidence needed to trust the answer, then offer the next safe
action. Avoid emoji status markers and avoid raw markdown markers in plain-text
surfaces such as headers and button labels.

### Reply examples

| Prompt | Expected reply shape |
|---|---|
| `@Nimbus what files are in this channel?` | "I found 6 files in this channel." Then a short file preview with names, sizes, and types, plus `Save all to S3`, `Find duplicates`, and `What's missing?` buttons. |
| `@Nimbus save all files in this channel` | "I saved the channel files to S3." Then scanned/saved/skipped counts, the S3 destination, and a small saved-key preview. |
| `@Nimbus save files from #legal and #design` | One summary covering each mentioned channel. Already-recorded Slack files are skipped. |
| `@Nimbus what files are not saved in my S3 bucket?` | "Some channel files are missing from S3." Then the checked count, destination, and the first unsaved files with a `Save unsaved files` button. |
| `@Nimbus find duplicate files` | Current-channel saved-manifest duplicate/stale report using real object keys first, with hashes as supporting evidence. |
| `@Nimbus find duplicate files in my bucket` | Workspace-wide Nimbus-saved Slack manifest duplicate report. The card states that arbitrary bucket uploads are outside this command's current scope. |
| `@Nimbus status` | "Everything looks healthy..." or "I found workspace items that need attention..." Then one sentence with running, awaiting, done, failed, pending approval, and proposed-plan counts. |
| `@Nimbus tools` | "I use a shared runtime tool catalog across Slack and CLI." Then a compact list of live and roadmap capabilities. |

### Card types

| Card | Trigger |
|---|---|
| **File list** | `@Nimbus what files are in this channel?` |
| **Diff report** | `@Nimbus what files are not saved in my S3 bucket?` |
| **Changed since sync** | `@Nimbus which files changed since the last sync?` |
| **Dedupe report** | `@Nimbus find duplicate files` |
| **Save progress** | Live progress edits during `@Nimbus save all files` |
| **Save report** | Final result after save completes |
| **Task status** | Background task state transitions |
| **Approval request** | Destructive actions that need explicit confirmation |
| **Capability list** | `@Nimbus tools`, `@Nimbus what can you do?` |
| **Failure** | Any adapter-owned command error |

### Streaming save

The save command posts a progress placeholder immediately, then edits it in
place as files are uploaded. The final edit replaces the placeholder with a
save report. If the initial post fails (cold start, token error), Nimbus still
runs the save and falls back to a single final message.

### Approval requests

The `approval_request_card` posts a Block Kit card with **Approve** and
**Reject** buttons. Both buttons carry a signed `action_id` in the format
`approve:<action-id>` / `reject:<action-id>`. The action ID is the handle the
runtime uses to resolve the confirmation when the button is pressed.

### Accessibility fallback

Every `send_blocks` call includes a `text` fallback produced by
`blocks_to_fallback_text()`. This plain-text version strips mrkdwn markers
(`*bold*`, `_italic_`) and is used by Slack notifications, screen readers, and
any poster that does not support Block Kit.

### Extending

Block Kit renderers live in `nimbus_slack.blocks`. All functions are pure —
no network calls, no imports from `flow.py`. `SlackPoster` was extended with
two new methods:

```python
def send_blocks(
    self,
    channel_id: str,
    blocks: list[dict[str, object]],
    fallback_text: str,
    *,
    thread_ts: str | None = None,
) -> object: ...

def update_blocks(
    self,
    channel_id: str,
    ts: str,
    blocks: list[dict[str, object]],
    fallback_text: str,
) -> object: ...
```

Test doubles that implement only `send_message` / `update_message` still work
— `flow.py` falls back automatically on `AttributeError`.

## Failure Behavior

- Missing or invalid Slack signatures return `401` before event parsing.
- Duplicate Slack events are acknowledged and ignored before any Nimbus turn.
- Invalid OAuth state is rejected before code exchange.
- Slack OAuth transport failures become `502` responses.
- Setup tokens are stored only as SHA-256 hashes and are one-time, expiring
  bearer tokens.
- Tenant setup writes the BYOK config and consumes the setup token in one
  durable-store transaction; Postgres mode locks the setup row before consuming
  it so concurrent submitters cannot both succeed.
- File saves are idempotent through `s3_file_manifest`: a Slack retry or a
  repeated user command skips already-recorded files.
- File scans are bounded by page count and byte limits; if Slack has more pages
  than the configured bound, the reply says the scan was truncated.
- If the encrypted store is unavailable or the Fernet key is missing, setup
  and OAuth fail closed with `503`.
- `/ready` fails with `503` when the selected durable store cannot be reached
  or its schema metadata is missing.
- Tenant-local model mode fails closed when BYOK setup is missing, instead of
  silently using a shared model-provider key.

## Render Notes

`render.yaml` deploys `nimbus-slack-staging` and `nimbus-slack-production` as
free single-instance services backed by the same Render Postgres databases as
the Nimbus runtime. Set:

```shell
NIMBUS_SLACK_MODEL_MODE=auto
NIMBUS_SLACK_STORE_BACKEND=postgres
NIMBUS_SLACK_DATABASE_URL=<Render internal Postgres URL>
NIMBUS_STATE_BACKEND=postgres
DATABASE_URL=<Render internal Postgres URL>
```

Use `/ready` as the Render health check. Free instances can still cold-start, so
Slack's first retry may be the request that succeeds after spin-up. Move to a
paid always-on instance when cold-start latency becomes unacceptable; move file
save execution to durable jobs when the synchronous save path becomes the first
bottleneck.
