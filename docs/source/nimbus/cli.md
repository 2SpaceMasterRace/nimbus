# Nimbus CLI

Nimbus CLI is the terminal surface for Nimbus. The normal local path runs
`NimbusRuntime` directly in the current Python process; it does not require a
local HTTP server. Remote profiles still exist for staging or self-hosted
servers, but Slack and other chat platforms are separate deployed adapters.

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
uv run nimbus auth
uv run nimbus auth local --openrouter-key "sk-or-v1-..." --no-aws
```

You can also keep personal local credentials in a gitignored `credentials.env`
at the repository root:

```shell
OPENROUTER_API_KEY=sk-or-v1-...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
AWS_BUCKET_NAME=your-bucket
NIMBUS_CONTAINER=your-bucket
```

With that file present, `nimbus chat` can bootstrap the default local profile
directly from the environment:

```shell
uv run nimbus chat "hello" --profile local
```

To persist those credentials into the CLI secret store, run:

```shell
uv run nimbus auth local
uv run nimbus auth paste < credentials.env
```

By default, the profile uses `openai/gpt-oss-120b:free`. To enable storage
tools explicitly, store AWS credentials and pin the container the model is
allowed to use:

```shell
uv run nimbus auth local \
  --openrouter-key "$OPENROUTER_API_KEY" \
  --aws-access-key-id "$AWS_ACCESS_KEY_ID" \
  --aws-secret-access-key "$AWS_SECRET_ACCESS_KEY" \
  --aws-region "$AWS_REGION" \
  --container "$AWS_BUCKET_NAME"
```

Secrets are stored in the OS keyring when possible. In headless environments,
including many containers, Nimbus falls back to `0600` JSON under
`~/.nimbus/secrets.json`. Set `NIMBUS_HOME` to choose a different profile and
secret directory:

```shell
export NIMBUS_HOME="$PWD/.nimbus-dev"
```

The auth commands never print secret values. Bare `nimbus auth` is the fastest
new-user path: it loads a nearby `credentials.env`, announces the path, prompts
for a missing OpenRouter key, and creates the default `local` profile. Use
`nimbus auth paste` when a credential manager gives you a block of dotenv-style
`KEY=value` text to import.

## Chat

Send one message:

```shell
uv run nimbus chat "Summarize this project in one sentence." --profile local --no-tools
```

Start a small interactive prompt:

```shell
uv run nimbus
uv run nimbus chat --profile local
```

With storage tools:

```shell
uv run nimbus chat "List files under reports/." --profile local
```

Add `--profile-timing` when you want the default latency budget, or
`--profile-timings half|full|hud|waterfall` for a specific rendering:

```shell
uv run nimbus chat "list files under reports/" --profile local --profile-timings hud
uv run nimbus resume "now verify the manifest" --profile local --profile-timings full
```

The modes are the same as Slack:

| Mode | Output shape |
|---|---|
| `half` | compact critical-path table |
| `full` | span table with measured/opaque labels |
| `hud` | game-style bars plus bottleneck callout |
| `waterfall` | offset timeline from command start |

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
locally. This is useful for staging, production, and self-hosted deployments,
not for ordinary local CLI use.

```shell
uv run nimbus setup remote \
  --profile staging \
  --base-url https://nimbus-staging.onrender.com \
  --auth hmac \
  --signing-secret "$AI_SERVER_SIGNING_SECRET"

uv run nimbus chat "hello through the deployed server" --profile staging
```

The bundled Nimbus server only accepts HMAC-signed remote chat requests.
`AI_SERVER_API_KEY` is for management/session endpoints and will not
authenticate `POST /ai/chat/turn`.

## Inspect Auth State

```shell
uv run nimbus auth status
uv run nimbus auth doctor
uv run nimbus auth profile list
uv run nimbus auth profile use staging
```

This shows profiles, their target, auth mode, and whether a secret is present.
It never prints the secret.

## Credentials and `credentials.env`

The CLI onboarding flow is the source of truth. It stores profile metadata under
`NIMBUS_HOME` and secrets in keyring or the fallback secret file.

For developer convenience, `nimbus`, `nimbus chat`, `nimbus resume`,
`nimbus auth`, `nimbus auth status`, `nimbus auth paste`, and
`nimbus auth local` load a nearby dotenv file while walking up from the current
directory. Set `NIMBUS_ENV_FILE=/path/to/nimbus-production.env` to pin an exact
file. Without that override, discovery checks the nearest directory for
`credentials.env`, `.env`, then any other `*.env` file in deterministic
alphabetical order.
Runtime precedence is:

1. Explicit CLI flags or pasted values.
2. Process environment variables.
3. Environment loaded from `credentials.env`, `.env`, or another nearby
   `*.env` file.
4. Stored CLI secret in keyring or `~/.nimbus/secrets.json`.
5. For AWS only, the normal boto3 credential chain when no explicit key pair is
   stored.

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

Local and remote modes render the final runtime turn response so guarded
storage actions, including delete confirmations, behave the same way in both
profiles.

## Use Cases

These examples are intentionally phrased as real terminal commands rather than
abstract product promises.

### DevOps Cleanup

Build artifacts often pile up in S3. When run IDs are date-prefixed, ask Nimbus
for a bounded candidate list, review it, then confirm the exact object to
delete:

```shell
uv run nimbus chat "list everything under builds/nightly/ and identify the oldest five files -- I want to delete those" --profile local
uv run nimbus resume "yes, delete builds/nightly/2026-04-01-abc123.tar.gz" --profile local
```

### Session Handoff

The CLI stores session records per profile, so a teammate can resume the last
cleanup conversation without reconstructing context manually:

```shell
uv run nimbus resume "what did we decide to delete from raw-ingest/ yesterday, and what's still left to do?" --profile local
```

### Pre-Deploy Verification

Remote profiles let deploy jobs call a production Nimbus server with HMAC auth
instead of carrying local AWS credentials:

```shell
uv run nimbus chat "check if configs/prod/feature-flags.json exists and tell me its last-modified time and size" --profile prod
```

### Security Audit

Nimbus can inspect object names and metadata before anything is downloaded:

```shell
uv run nimbus chat "list everything under uploads/temp/ and flag any filenames that look like they might contain credentials, keys, or PII" --profile local
```

### Cross-Version Comparison

Use the REPL when the second question depends on the first answer:

```text
nimbus> list files under models/v1/checkpoints/
nimbus> now list models/v2/checkpoints/ -- what's in v2 that's missing from v1?
```

### CI Artifact Check

Use a remote `ci` profile for downstream pipeline gates:

```shell
uv run nimbus chat "does artifacts/$GITHUB_SHA/coverage-report.json exist? What is its size?" --profile ci
```

## Background Task Sessions

Tasks started from Slack (file saves, diffs, approvals) are tracked as
background tasks in the Nimbus runtime. The `nimbus task` subcommands let you
inspect and control those tasks from the terminal without opening Slack.

The CLI's task namespace is `cli:<profile-name>`. Tasks started from Slack live
under `slack:<workspace-id>`. To inspect Slack-originated tasks you must pass
the workspace ID explicitly with `--workspace`.

### List tasks

```shell
uv run nimbus task list --profile local
uv run nimbus task list --status scanning --limit 5
uv run nimbus task list --workspace T0123ABCDEF  # Slack workspace
```

`--status` accepts any `TaskStatus` value:
`created`, `planning`, `scanning`, `diffing`, `awaiting_approval`, `applying`,
`verifying`, `done`, `failed`, `canceled`, `expired`, `rejected`.

### Inspect a task

```shell
uv run nimbus task inspect <task-id> --profile local
```

Prints a panel with `task_id`, `status`, `intent`, `session_id`, timestamps,
`failure_detail`, and any extra metadata the runtime attached.

### Show the event stream

```shell
uv run nimbus task events <task-id> --profile local
uv run nimbus task events <task-id> --limit 100
```

Events are the ordered log of runtime state transitions for the task's session:
`session_started`, `turn_completed`, `action_confirmed`, etc.

### Show evidence artifacts

```shell
uv run nimbus task artifacts <task-id> --profile local
uv run nimbus task artifacts latest --profile local
uv run nimbus proof show latest --json
```

Artifacts are durable records written by runtime actions — S3 upload receipts,
diff reports, manifest snapshots. Each artifact carries a `kind` and an optional
`action_id` linking it to the action that produced it. Proof receipts validate
the linked artifact payload digests before the CLI presents the receipt as
usable evidence.

### Watch a running task

```shell
uv run nimbus task watch <task-id> --profile local
uv run nimbus task watch latest --profile local     # most recent task
uv run nimbus task watch <task-id> --interval 5.0
```

Polls the store every `--interval` seconds (default `2.0`) and prints a line
whenever the status changes. Exits automatically when the task reaches a
terminal state (`done`, `failed`, `canceled`, `expired`, `rejected`).
Press Ctrl-C to stop early.

### Cancel a task

```shell
uv run nimbus task cancel <task-id> --profile local
```

Cancels an in-progress task. Only non-terminal statuses can be canceled:
`created`, `planning`, `scanning`, `diffing`, `awaiting_approval`, `applying`,
`verifying`. If the task's status changed concurrently, the cancel fails with
a non-zero exit code.

## Protected Roots And Generations

Protected roots make S3 snapshotting explicit and reviewable:

```shell
uv run nimbus root protect --container "$AWS_BUCKET_NAME" --prefix team/
uv run nimbus root list --json
uv run nimbus generation create <root-id> --json
uv run nimbus generation list <root-id>
uv run nimbus generation list --json
uv run nimbus manifest list
uv run nimbus generation diff <gen-a> <gen-b> --json
uv run nimbus blame team/report.csv --json
uv run nimbus heal root <root-id> --json
uv run nimbus heal replica <source-manifest> \
  --replica-manifest <replica-manifest> --allow-missing-repair --json
uv run nimbus heal replica <source-manifest> \
  --replica-manifest <replica-manifest> \
  --allow-missing-repair --apply --json
```

`generation create` lists the S3 prefix through the configured storage client,
canonicalizes the object pointers, writes a `GenerationManifest` artifact, and
writes a proof receipt linked to that manifest. The manifest digest is stable
under provider listing order, so retries converge when the object set has not
changed. `generation list` without a root and `manifest list` give you the
history view you expect from source control: newest snapshots first, with the
manifest IDs you can verify later.
`heal root` verifies the latest generation, computes a health score, and returns
repair advice. `heal replica` compares source and replica generation manifests
for an S3-only lane. Missing replicas are repairable only when the operator
passes the policy flag; checksum mismatches and unknown hashes remain blocked
and produce reconciliation next steps. With `--apply`, Nimbus uses provider-side
copy for missing replicas, verifies the destination SHA-256, and writes
`repair_receipt` artifacts before reporting success.

## Plan Review And Approval

Plans are visible before mutation:

```shell
uv run nimbus plan list --json
uv run nimbus plan show <plan-id> --json
uv run nimbus plan diff <plan-id>
uv run nimbus plan approve <plan-id>
uv run nimbus plan reject <plan-id>
uv run nimbus plan cleanup <manifest-artifact-id> --json
```

`plan diff` shows the target, estimated size/count, restore-story field, and
approval binding. It does not pretend execution happened; execution still needs
the worker/action/verifier path.

Candidate cleanup plans are sibling choices. Approving one candidate
atomically supersedes the other strategies so retries converge on one approved
restore story.

## Storage Stacks, Learning, And Replay

```shell
uv run nimbus stack propose <plan-id> --json
uv run nimbus stack diff <stack-id> --json
uv run nimbus stack approve <stack-id>
uv run nimbus stack restack <stack-id> --manifest <fresh-manifest-artifact-id>
uv run nimbus stack apply <stack-id> --yes --json

uv run nimbus policy patch propose --capability delete_file --json
uv run nimbus policy patch accept <proposal-id> --json

uv run nimbus spec check --json
uv run nimbus trace export <session-id> --json
uv run nimbus trace replay <session-id> --expected trace.json --json
```

Stacks turn approved plans into ordered storage changes with immutable
revisions and an operation log. `restack` compares approved target digests
against a fresh manifest and writes `conflict_artifact` evidence when a target
changed. `apply` fails closed unless the stack is approved and each destructive
change passes a verifier gate.

Policy patches are learning-derived proposals, not silent authority changes.
The CLI records capability deltas, learning evidence, a base policy version,
and an explicit reviewer before accept/reject. `spec check` exposes the
executable runtime status specification included in replay traces, with a
stable digest, per-domain counts, and the TLA+/Lean formal artifact digests.
Trace export/replay gives CI and operators a deterministic envelope for
event/artifact replay and strict diffs.

## Evidence Export And Preview

```shell
uv run nimbus evidence export <artifact-id> --json
uv run nimbus evidence preview <artifact-id> --json
uv run nimbus evidence compact <artifact-id> [<artifact-id> ...] --json
```

`evidence export` writes canonical artifact payload bytes into a tenant-scoped
content-addressed local object store under the profile session directory. The
record includes payload digest, compressed object digest, byte counts, encoding,
retention class, and verification status. Re-exporting the same payload dedupes
the bytes while keeping artifact records separate.

`evidence preview` renders a compact artifact summary and tells the operator
whether the backing evidence object exists. `evidence compact` exports the
requested artifacts if needed, verifies every source object, and writes a
compressed bundle index. It does not delete the original payload objects; source
deletion stays behind a future retention policy and verifier gate.

## Verification And Migration Evidence

```shell
uv run nimbus verify <manifest-artifact-id>
uv run nimbus verify manifest <manifest-artifact-id> --strict
uv run nimbus provider capabilities --json
uv run nimbus provider health --prefix team/ --json
uv run nimbus migration evaluate <root-id> \
  --candidate-container "$REPLICA_BUCKET" \
  --candidate-prefix team/ \
  --json
```

`verify` supports both Slack backup manifests and protected-root generation
manifests. Strict mode treats unknown hashes as drift. `migration evaluate`
creates a durable S3-only `migration_decision_packet`; it measures source-list
latency, records the latest generation size when available, names assumptions
and safety checks, and leaves route switching approval-gated.

`provider capabilities` inspects the configured storage client and reports
which optional provider protocols Nimbus can use, such as bounded pagination,
server-side copy, checksum reads, version listing, and restore. It is a readiness
view, not a promise that a non-S3 provider is production-supported.

`provider health` runs bounded live Nimbus probes against the configured S3
bucket/prefix, writes a `provider_health` artifact, and exits non-zero when the
provider is degraded, blocked, or unavailable. The JSON output names each probe,
outcome, latency, confidence, expiry, next operator step, and AWS status-page
links as advisory context. Nimbus does not trust those pages as proof; live
bucket/prefix probes are the authoritative evidence.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `profile 'local' is not configured` | Run `uv run nimbus auth local`, or create a `credentials.env` and run it again. |
| `profile 'local' is missing an OpenRouter API key` | Run `uv run nimbus auth local --openrouter-key "$OPENROUTER_API_KEY" --no-aws`, or set `OPENROUTER_API_KEY` in `credentials.env`. |
| Storage requests say no tools are available | Run `uv run nimbus auth local --container "$AWS_BUCKET_NAME"` and store AWS credentials or use the boto3 credential chain. |
| Secrets disappear in a container | Mount a persistent directory and set `NIMBUS_HOME=/path/to/mount`. |
| Remote requests return `401` | Check the HMAC signing secret, request freshness, nonce reuse, and server `AI_SERVER_SIGNING_SECRET`. |
| Remote requests return `503` | The server is missing provider/storage env vars or its readiness dependencies are unhealthy. |
