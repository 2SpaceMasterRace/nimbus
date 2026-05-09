# nimbus-cli

`nimbus-cli` is the Python-only command-line adapter for Nimbus. For normal
local use it runs `NimbusRuntime` in the current process; no local HTTP server
is required.

It supports two profile modes:

- `local`: runs `NimbusRuntime` in-process and stores sessions/events under
  `~/.nimbus/sessions/cli` by default.
- `remote`: sends canonical `/ai/chat/turn` requests to a self-hosted Nimbus
  server using HMAC request signing.

The CLI stores non-secret profile metadata in `~/.nimbus/config.json`. Secrets
go to the OS keyring when available, with a `0600` `~/.nimbus/secrets.json`
fallback for headless development environments.

## Onboard

First run with no profile prints a welcome panel pointing to the auth command
instead of crashing — no env vars or pre-existing config required.

```shell
uv run nimbus auth
uv run nimbus auth paste < credentials.env
uv run nimbus auth local --openrouter-key "$OPENROUTER_API_KEY" --no-aws
uv run nimbus auth local \
  --openrouter-key "$OPENROUTER_API_KEY" \
  --aws-access-key-id "$AWS_ACCESS_KEY_ID" \
  --aws-secret-access-key "$AWS_SECRET_ACCESS_KEY" \
  --aws-region "$AWS_REGION" \
  --container "$AWS_BUCKET_NAME"
uv run nimbus setup remote --profile prod --base-url https://nimbus.example.com --auth hmac
uv run nimbus auth status
uv run nimbus auth profile list
uv run nimbus auth profile use prod
uv run nimbus auth doctor --profile prod
```

The local profile defaults to `openai/gpt-oss-120b:free`. The CLI also loads
the first dotenv file found while walking up from the current directory:
`credentials.env`, `.env`, then any other `*.env` file alphabetically. A
repo-local dotenv can provide `OPENROUTER_API_KEY`, `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, and `NIMBUS_CONTAINER`/`AWS_BUCKET_NAME`.
Set `NIMBUS_ENV_FILE=/absolute/path/to/demo.env` when multiple dotenv files are
present and the demo must use one exact profile. If no local profile exists
yet, `nimbus` and `nimbus chat` bootstrap the default local profile from that
environment.
Bare `nimbus auth` is the friendly setup path: it imports nearby
`credentials.env`, prompts for a missing OpenRouter key, and stores metadata
under `~/.nimbus` without printing secret values. `nimbus auth paste` accepts
dotenv-style `KEY=value` text from stdin or an argument and only echoes the
field names that were imported.

## Chat

```shell
# Starts the default local REPL.
uv run nimbus

# Starts a new session by default.
uv run nimbus chat "list files under reports/" --profile local

# Resume the last session explicitly.
uv run nimbus resume "continue where we left off" --profile local

# Show a demo-friendly latency HUD for the request.
uv run nimbus chat "list files under reports/" --profile local --profile-timings hud

# Show diagnostic measured/opaque spans.
uv run nimbus resume "verify the manifest now" --profile local --profile-timings full
```

Local and remote modes both render the full runtime turn result, including
confirmation prompts and action/artifact summaries for guarded storage actions.
`--profile-timing` maps to the default `half` view; `--profile-timings` accepts
`half`, `full`, `hud`, `waterfall`, and `off`.

## Use Cases

These examples match the current CLI surface:

```shell
# DevOps cleanup: identify date-prefixed build artifacts, then confirm exact deletes.
uv run nimbus chat "list everything under builds/nightly/ and identify the oldest five files -- I want to delete those" --profile local
uv run nimbus resume "yes, delete builds/nightly/2026-04-01-abc123.tar.gz" --profile local

# Session handoff: continue the last saved session for this profile.
uv run nimbus resume "what did we decide to delete from raw-ingest/ yesterday, and what's still left to do?" --profile local

# SRE/deploy check through a remote HMAC-authenticated Nimbus server.
uv run nimbus chat "check if configs/prod/feature-flags.json exists and tell me its last-modified time and size" --profile prod

# Security audit by filename before downloading anything.
uv run nimbus chat "list everything under uploads/temp/ and flag filenames that look like they might contain credentials, keys, or PII" --profile local

# CI artifact presence check with a remote profile.
uv run nimbus chat "does artifacts/$GITHUB_SHA/coverage-report.json exist? What is its size?" --profile ci
```

For multi-turn comparisons, start the REPL and keep the same session:

```text
nimbus> list files under models/v1/checkpoints/
nimbus> now list models/v2/checkpoints/ -- what's in v2 that's missing from v1?
```

## REPL Slash Commands

Inside the `nimbus` REPL, lines starting with `/` are handled directly without
hitting the model:

| Command         | Action                                               |
| --------------- | ---------------------------------------------------- |
| `/help`         | Show the slash-command grid                          |
| `/clear`        | Clear the screen                                     |
| `/new`          | Start a fresh session under the active profile      |
| `/model [id]`   | Switch model (interactive picker if no `[id]`)       |
| `/profile`      | Show the active-profile banner                       |
| `/exit`, `/quit`| Leave the REPL                                       |

## Diagnose Setup

`nimbus doctor` runs an end-to-end sanity check on a profile and prints a
one-line ✓/✗ per check. Exits 0 if everything passes, 1 otherwise.

```shell
uv run nimbus doctor
uv run nimbus doctor --profile prod
```

What it checks, in order:

1. Profile exists and basic fields are populated.
2. OpenRouter API key is present (keyring or env).
3. Session directory is writable (local profiles only).
4. AWS credentials exist when a storage container is pinned.
5. Remote `/health` reachable within 5 seconds (remote profiles only).

## Switch Model

Pick a different model for a local profile without re-running `auth local`.

```shell
# Interactive arrow-key picker grouped by Free / Paid / Custom.
uv run nimbus model

# Set a model directly without the picker.
uv run nimbus model anthropic/claude-3-5-sonnet

# Switch the model for a named profile.
uv run nimbus model openai/gpt-4o --profile prod
```

The picker shows the current model in its title and pre-selects it if it's
in the catalogue. Pick `Enter model ID…` to type any OpenRouter model
identifier that isn't in the curated list. In CI / non-tty environments,
the picker falls back to a numbered prompt.

## Task Management

Background tasks created in Slack (file saves, diffs, approvals) can be
inspected and controlled from the CLI using the `nimbus task` subcommands.

```shell
# List recent tasks for the active profile.
uv run nimbus task list --profile local

# Filter by status.
uv run nimbus task list --status scanning --limit 5

# Inspect a single task (status, intent, session ID, timestamps).
uv run nimbus task inspect <task-id> --profile local

# Show the ordered event stream for a task.
uv run nimbus task events <task-id> --profile local

# Show evidence artifacts (S3 receipts, diff reports).
uv run nimbus task artifacts <task-id> --profile local
uv run nimbus proof show latest --json

# Snapshot and verify a protected S3 root.
uv run nimbus root protect --container "$AWS_BUCKET_NAME" --prefix team/
uv run nimbus generation create <root-id> --json
uv run nimbus verify <manifest-artifact-id>
uv run nimbus blame team/report.csv --json
uv run nimbus heal root <root-id> --json
uv run nimbus heal replica <source-manifest> \
  --replica-manifest <replica-manifest> --allow-missing-repair --json
uv run nimbus heal replica <source-manifest> \
  --replica-manifest <replica-manifest> \
  --allow-missing-repair --apply --json

# Turn cleanup candidates into ordered storage changes with conflict detection.
uv run nimbus plan cleanup <manifest-artifact-id> --json
uv run nimbus stack propose <plan-id> --json
uv run nimbus stack diff <stack-id> --json
uv run nimbus stack approve <stack-id>
uv run nimbus stack restack <stack-id> --manifest <fresh-manifest-artifact-id>
uv run nimbus stack apply <stack-id> --yes --json

# Review learning-derived policy changes and deterministic traces.
uv run nimbus policy patch propose --capability delete_file --json
uv run nimbus policy patch accept <proposal-id> --json
uv run nimbus spec check --json
uv run nimbus trace export <session-id> --json
uv run nimbus trace replay <session-id> --expected trace.json --json

# Inspect provider readiness and write provider-health evidence.
uv run nimbus provider capabilities --json
uv run nimbus provider health --prefix team/ --json

# Export, preview, and compact durable artifact payloads.
uv run nimbus evidence export <artifact-id> --json
uv run nimbus evidence preview <artifact-id> --json
uv run nimbus evidence compact <artifact-id> [<artifact-id> ...] --json

# Create a decision packet for an S3 replica/region plan without switching routes.
uv run nimbus migration evaluate <root-id> \
  --candidate-container "$REPLICA_BUCKET" \
  --candidate-prefix team/ --json

# Watch a running task, exit when it reaches a terminal state.
uv run nimbus task watch latest --profile local       # most recent task
uv run nimbus task watch <task-id> --interval 5.0

# Cancel an in-progress task.
uv run nimbus task cancel <task-id> --profile local
```

Pass `--workspace <slack-workspace-id>` to inspect tasks that originated in a
Slack workspace (`slack:<workspace-id>` namespace) rather than the CLI
(`cli:<profile-name>` namespace).

## Tool Catalog

Nimbus has a runtime-owned capability catalog so Slack, CLI, and model-facing
tools describe the same product surface. Use it to see what is live today,
what is partial, and what roadmap tools plug into the same task/action system.

```shell
# List all current, partial, and roadmap capabilities.
uv run nimbus tools list

# Show only Slack-visible capabilities.
uv run nimbus tools list --surface slack

# Hide roadmap tools.
uv run nimbus tools list --current-only

# Inspect one capability.
uv run nimbus tools inspect candidate_plans
```

Roadmap entries such as automation templates, richer choice prompts, candidate
plans, and parallel candidate agents are intentionally visible here before they
execute. They are product contracts, not ad hoc adapter commands.

See `docs/source/nimbus/cli.md` for the full user guide and
`docs/source/nimbus/verification.md` for local and deployed smoke tests.
