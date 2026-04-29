# Nimbus AI Agent — CLI & Slack Redesign

**Status:** Draft, in active discussion with the team
**Branch:** `hw3-stage`
**Last updated:** 2026-05-08
**Companion to:** `specs/ai-system-redesign.md` (covers harness, runtime,
guardrails, state machine — the substrate this document sits on)

This document covers the user-facing layer of Nimbus: the CLI agent
(`nimbus`) and the Slack app. It is the "what does the user actually see and
touch" half of the redesign. The runtime/harness half lives in
`ai-system-redesign.md`.

The four product priorities from our last discussion are:

1. **Onboarding** — first-run experience for both Slack and CLI
2. **Friendly responses** — streaming, conversational framing, progressive disclosure
3. **Guardrails** — bad-actor defense and intent identification
4. **Pi-style seamless UX** — steering, hooks, compaction, skills

This document focuses on (1) and (2) and the parts of (3) and (4) that
surface as user experience. Policy enforcement and harness internals are in
the companion doc.

---

## 0. What we read

I went deep on four codebases under `/Users/nanodijkstra/work/experiments`:

| Codebase | Stack | Surface | What it teaches |
|---|---|---|---|
| `pi` | TypeScript, monorepo (`pi-ai`, `pi-agent-core`, `coding-agent`, `tui`, `web-ui`) | TUI + web | Hooks at every step, AgentMessage vs LLM Message, compaction with file-op tracking, steering queues, skills as `.md` files |
| `claude-code` | TypeScript + Ink (React) | TUI | Step-based onboarding flow, dialog component library, `useKeybindings` hook, slash command directory tree, terminal setup helpers |
| `codex` | Rust (workspace of ~50 crates) + minimal TS shim (`codex-cli`) | TUI | Many small crates, `protocol` crate as the contract boundary, `login` as a separate crate (PKCE, device code), `core-skills` separate crate, "resist adding to core" discipline |
| `opencode` | TypeScript + Effect-ts | TUI + Slack + SDK | Permission system with `permission + pattern + action` rules, multiple skill discovery roots (`.claude/`, `.agents/`, `opencode/`), Bolt-based Slack package separate from core, session-per-thread mapping |

The architectural moves that show up in *more than one* of these are the
ones worth taking seriously. Single-codebase patterns are interesting but
weaker signal.

---

## 1. Patterns that show up everywhere

### 1.1 The agent core is a separate package from the surface

| | Core | CLI surface | Web/extra surface |
|---|---|---|---|
| Pi | `packages/agent`, `packages/ai` | `packages/tui` | `packages/web-ui` |
| Codex | `codex-rs/core`, `codex-rs/protocol` | `codex-rs/tui`, `codex-rs/cli` | `codex-rs/app-server` |
| Opencode | `packages/opencode/src` (a server) | `packages/opencode` CLI | `packages/slack`, `packages/web`, `packages/desktop` |
| Claude Code | (private monolith) | `src/cli`, `src/components` | `src/bridge` |

Three of four cleanly separate the agent from the surface. The fourth
(Claude Code) is a private monolith but still organizes by `src/cli/`,
`src/components/`, `src/bridge/` so the surface lives apart from the core.

**What this means for Nimbus.** Today our CLI (`nimbus`) lives inside
`openrouter_ai_client_impl/` — wrong package. The CLI is a *consumer* of the
runtime, not part of the OpenRouter implementation. Move it. Recommended:

```
src/
  ai_client_api/             contract
  openrouter_ai_client_impl/ provider impl ONLY (no CLI)
  nimbus_runtime/            the agent kernel (already there)
  nimbus_cli/                Python+TS CLI surface (NEW package)
  nimbus_slack/              Slack app (rename slack_bridge → split)
  ai_server/                 HTTP transport (already there)
```

The CLI talks to `ai_server` over HTTP (or in-process via direct
`NimbusRuntime` calls in dev mode). The Slack app talks to `ai_server` over
HTTP. Both surfaces are thin; the agent kernel is shared.

### 1.2 The protocol between surface and core is explicit and versioned

Codex makes this most extreme: a whole crate (`codex-rs/protocol/`) just for
the message types crossing the boundary. Pi has `pi-ai`'s `EventStream` and
`Message` types. Opencode has its OpenAPI-generated SDK.

**What this means for Nimbus.** Today the wrapper request schema is in
`ai_server/router.py` and the runtime input is in `nimbus_runtime/domain.py`
and the CLI parses tool events ad-hoc from `pydantic-ai`'s output. There is
no single document that says "this is the protocol." Add one. Concrete
shape: a `nimbus_protocol` module (or thin package) that owns:

- `TurnInput`, `TurnResult`, `TurnEvent` (streaming events)
- `ToolCall`, `ToolResult`, `ToolApprovalRequest`
- `ConfirmationRequest`, `ConfirmationReply`
- `RuntimeSpec` (already discussed)
- `NimbusError` (the three-presentation error model)

Both the CLI and Slack app depend on `nimbus_protocol`. They never reach
into `nimbus_runtime` internals.

### 1.3 Skills are `.md` files, discovered from multiple roots

Pi loads `SKILL.md` files from configured directories. Opencode loads them
from four roots (in priority order):

```
.claude/skills/**/SKILL.md         # claude code compatibility
.agents/skills/**/SKILL.md         # generic
opencode/skill/**/SKILL.md         # primary
opencode/skills/**/SKILL.md        # alt
```

Each skill has YAML frontmatter (`name`, `description`,
`disable-model-invocation`) plus markdown body. Slash commands like `/foo`
expand to `<skill name="foo" location="...">...content...</skill>` and get
injected into the next user message.

Codex has a `core-skills` crate. Claude Code has a `commands/skills/`
directory. Three of four converge on this.

**What this means for Nimbus.** Add a `skills/` directory at the project
root. Skills are markdown. Discovery is deterministic. The CLI exposes
`nimbus skills list` and `nimbus skills new <name>`. The Slack app exposes
`/nimbus skills` to list available skills. Skills are typed entities with a
schema (frontmatter validated against a Pydantic model) — invalid skills
emit warnings, not crashes.

### 1.4 Permissions are typed rules, not inline checks

Opencode's permission system is the most explicit:

```typescript
type Action = "allow" | "deny" | "ask"
type Rule = { permission: string; pattern: string; action: Action }
type Ruleset = Rule[]
```

Rules are evaluated by `evaluate(rules, request)` returning an `Action`.
Pi has `beforeToolCall` hook that can return `{ block: bool, reason: string }`.
Claude Code has a `permissions/` subdirectory with mode dialogs
(`BypassPermissionsModeDialog`).

**What this means for Nimbus.** The `PolicyEngine` we already have in the
runtime spec (and partially in `nimbus_runtime/policy.py`) should match
Opencode's shape: tuple of `(permission_kind, target_pattern, action)`.
Permission kinds: `storage.read`, `storage.write`, `storage.delete`,
`storage.bulk_delete`. Pattern is a glob like `reports/2025/**` or `*`.
Action is `allow | deny | ask`. The CLI surface for "ask" is an inline
prompt; the Slack surface is a button-based block. Same rule, two
presentations.

### 1.5 Hooks at every boundary

Pi has them everywhere: `transformContext`, `beforeToolCall`,
`afterToolCall`, `onPayload`, `onResponse`. Opencode has Effect's `Layer`
which is structurally similar — every concern can be intercepted by
providing a layer. Claude Code has `useKeybindings({...}, { context, isActive })`
for context-scoped keybinding hooks.

**What this means for Nimbus.** The harness defined in `ai-system-redesign.md`
needs hook points the CLI and Slack app can register against. Concrete API:

```python
class NimbusHarness(Protocol):
    def add_hook(self, name: HookName, handler: Callable) -> None: ...
    # before_model_call, after_model_call,
    # before_tool_call, after_tool_call,
    # transform_context, on_event
```

The CLI registers `before_tool_call` to render an approval prompt for `ask`
permissions. The Slack app registers the same hook to post a button block.
The hook contract is the same; the renderer differs.

### 1.6 Streaming is delta-based and the UI accumulates

Pi: `streamSimple` yields `START_MESSAGE`, `ADD_CONTENT_BLOCK`,
`UPDATE_CONTENT_BLOCK`, `UPDATE_CONTENT_BLOCK_METADATA` events. The UI
maintains a partial state and rerenders.

Codex: `event.rs` types stream from core to TUI. TUI's `chatwidget` keeps
partial state.

Claude Code: similar React-driven re-rendering as content blocks update.

Opencode (Slack): `event.subscribe()` returns a stream; tool updates get
posted as separate Slack messages in the same thread.

**What this means for Nimbus.** Today everything is request/response — POST
turn, get full result back. To get Pi-style perceived speed we need:
1. The harness emits `TurnEvent` deltas (token deltas, tool start/end,
   thinking).
2. The CLI subscribes via SSE and prints deltas as they arrive.
3. The Slack app subscribes and edits the message in place (for early
   tokens) or posts new messages (for tool completions).

This requires `ai_server` to expose a streaming endpoint, which it doesn't
today.

---

## 2. Architecture for Nimbus's CLI surface

### 2.1 Decision: keep the runtime in Python, build the CLI as a TypeScript+Ink companion

This is the question I flagged at the end of the previous doc. Three
options:

| Option | Pros | Cons |
|---|---|---|
| A. Pure Python CLI (Typer/Rich) | Single language, no extra build | No Ink-equivalent for Python; rendering quality below the bar |
| B. Pure TypeScript runtime + CLI | Pi-shaped; one ecosystem | Rewriting `nimbus_runtime` is enormous; storage tools are Python |
| C. **Python runtime, TS+Ink CLI** | Best UX; runtime stays where storage code is | Two languages; protocol must be explicit |

**Recommendation: option C.** This is also what Pi did when they shipped a
web UI — they kept the agent core stable and added a TS surface. The
protocol package (Section 1.2) makes the boundary clean.

The CLI is a thin TS+Ink program that:
1. Reads local config (`~/.nimbus/config.toml`)
2. Talks to a Nimbus server (local in-process, or a remote `ai_server`)
3. Renders streaming events with Ink
4. Handles slash commands, skills, permissions UI

The CLI can call `ai_server` over HTTP for remote, or spawn a local Python
subprocess that runs `NimbusRuntime` for offline/dev mode (similar to how
Codex's `codex-cli` is a thin shim over `codex-rs`).

### 2.2 Repository layout

```
src/
  nimbus_protocol/             Python package — typed wire types (NEW)
    __init__.py
    turns.py                   TurnInput, TurnResult, TurnEvent
    tools.py                   ToolCall, ToolResult, ToolApprovalRequest
    confirmations.py
    errors.py                  NimbusError + error_code enum
    runtime_spec.py
  nimbus_runtime/              (already exists) — kernel + harness
  nimbus_cli/                  Python entry point + REPL fallback (NEW)
    __init__.py
    main.py                    `nimbus` Typer entrypoint
    setup_wizard.py            `nimbus setup` flow
    quickstart.py              `nimbus quickstart`
    repl.py                    Plain-text fallback REPL
  ai_server/                   (already exists) — HTTP, streaming endpoints
  nimbus_slack/                NEW package; replaces slack_bridge if scope warrants

cli-ink/                       NEW directory at repo root (TypeScript)
  package.json
  src/
    main.tsx
    components/
      Onboarding.tsx           step-based flow (Claude Code pattern)
      ChatView.tsx             streaming message view
      ToolApproval.tsx         permission ask UX
      ConfirmationPrompt.tsx
      Status.tsx
    transport/
      http.ts                  ai_server HTTP client
      embedded.ts              spawn local Python subprocess
    skills/
      loader.ts                discover SKILL.md files
    keybindings.ts
    config.ts
```

The Python `nimbus_cli` package provides a fallback REPL for environments
where Node isn't available (CI, headless servers, the user's first Python
install before the Ink CLI is bootstrapped). It uses Typer + Rich. It does
the same protocol calls; it just renders worse.

### 2.3 What we delete or rename

- `slack_bridge/` → `nimbus_slack/` (rename, no functional change yet)
- The `nimbus` Typer command currently inside `openrouter_ai_client_impl`
  → moves to `nimbus_cli`. The `openrouter_ai_client_impl` becomes
  *only* the OpenRouter `AIClient` implementation, no CLI code.

---

## 3. CLI onboarding flow

This is the highest-priority deliverable.

### 3.1 What "good" looks like

From Claude Code (`src/components/Onboarding.tsx`), the steps are:

1. **Preflight** — system checks (Python version, network, etc.)
2. **Theme** — pick a color theme
3. **API key** — approve API key from env (if found)
4. **OAuth** — OAuth flow (skipped if API key approved)
5. **Security notes** — show security warnings (must read)
6. **Terminal setup** — offer to configure terminal shortcuts

Each step is a React component, conditional, with analytics. From Pookie
(Slack-side equivalent): 30-second OAuth + connect tools + start chatting.

The bar for Nimbus: **first run to working session in under 2 minutes for
a user who has their credentials in a password manager.**

### 3.2 The Nimbus CLI onboarding sequence

Implement as a step-based flow in `cli-ink/src/components/Onboarding.tsx`.
Each step is a React component, gated on what's missing:

```
Step 1. Welcome + preflight
  - Python 3.12 detected? (or download instructions)
  - Network connectivity to OpenRouter?
  - `uv` installed? (or download instructions)

Step 2. Pick a model
  - Default: openai/gpt-oss-120b:free (free tier)
  - Advanced: paste any OpenRouter model ID
  - "I have my own provider" → links to docs for swapping

Step 3. OpenRouter API key
  - "We need an OpenRouter API key. Get one at openrouter.ai/keys"
  - [paste field, masked]
  - Test connection: ping the model with "hello"
  - Pass → save to ~/.nimbus/credentials (chmod 600), continue
  - Fail → show error, allow retry

Step 4. Cloud storage provider
  - "Which cloud storage do you want Nimbus to manage?"
  - Choices: AWS S3 (now), GCS (coming soon — links to issue), Dropbox (coming soon)
  - For S3: prompt for AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION, AWS_BUCKET_NAME
  - Test connection: list bucket (read-only)
  - Pass → save, continue. Fail → show specific S3 error

Step 5. Permissions baseline
  - "Nimbus can read, write, and delete files in your bucket."
  - "By default, deletes require confirmation. Change later in ~/.nimbus/permissions.toml"
  - [Y to accept defaults / N to customize now]

Step 6. Security notes (Claude Code pattern)
  - "Nimbus can make mistakes. Always review actions before confirming."
  - "Object names from your bucket are treated as untrusted input."
  - "Nimbus does not send file contents to third parties unless you ask it to."

Step 7. Try it
  - Spawn a session with a pre-loaded prompt: "List my files in [bucket]"
  - Run the turn live, render the streaming response
  - On success: "You're set up. Type 'nimbus' to start chatting any time."
  - On failure: show the exact error and a retry button
```

**Implementation notes that come from reading Claude Code:**

- Use `useKeybindings({...}, { context: 'Onboarding', isActive: true })` to scope
  keybindings to the wizard. Pressing Esc anywhere skips to "later" with a
  way to resume via `nimbus setup --resume`.
- Each step emits a telemetry event (`nimbus_onboarding_step_start`,
  `nimbus_onboarding_step_complete`, `nimbus_onboarding_step_skip`). Time
  spent per step is the metric we care about.
- Steps can be skipped if their precondition is met (e.g. if
  `OPENROUTER_API_KEY` is already in env, step 3 is skipped with a "Found
  existing key — use it? [Y/n]" confirmation).
- `~/.nimbus/credentials` uses 0600 perms. The wizard verifies perms after
  write; if it can't set 0600 (e.g. on Windows), it warns explicitly.

### 3.3 The Python fallback (`nimbus setup` in Typer + Rich)

Same logical steps, lower-fi rendering. Used when:
- Node isn't installed
- The CLI is run over SSH where Ink rendering is broken
- CI/headless environments

The Python wizard reuses the *same* validation logic (the credential test
calls, the connection probes) so behavior matches. Only the UI differs.

### 3.4 `pydantic-settings` as the single source of truth

`AGENTS.md` already lists 9+ env vars. The wizard should *not* hardcode
that list. Instead:

```python
class NimbusSettings(BaseSettings):
    openrouter_api_key: SecretStr = Field(description="...", examples=["sk-or-..."])
    openrouter_model: str = Field(default="openai/gpt-oss-120b:free", ...)
    aws_access_key_id: SecretStr = Field(description="...")
    # ... etc
```

The wizard iterates `NimbusSettings.model_fields`, asks for each missing
one, validates each as it's entered. New env vars added to `NimbusSettings`
automatically show up in the wizard. One source of truth.

---

## 4. Slack onboarding flow

Slack onboarding is fundamentally different from CLI onboarding. The user
isn't on a terminal. They're in a chat client. Different affordances.

### 4.1 The shape (Pookie-inspired)

```
1. User clicks "Add to Slack" on the Nimbus marketing page
2. Slack OAuth flow runs — user grants Nimbus the scopes it needs
3. Nimbus posts a welcome DM to the user immediately
4. The DM contains a "Get Started" button that opens a Slack modal
5. The modal collects: cloud provider choice, S3 credentials (or other)
6. Modal "Test Connection" button — Nimbus pings the bucket, reports back
7. On success: post a sample message in the DM showing what Nimbus can do
8. User invites @nimbus to channels via /invite @nimbus
9. In any channel where Nimbus is invited, mentioning @nimbus starts a thread
```

### 4.2 What we learn from Opencode's Slack package

Reading `opencode/packages/slack/src/index.ts`:

- **One session per Slack thread.** `sessionKey = ${channel}-${thread}`.
  The channel+thread tuple maps to a Nimbus session ID. New thread = new
  session. Reply in same thread = same session.
- **Session sharing URL.** Opencode posts a `session.share()` URL in the
  thread on first reply, so the user can open the full session in a web
  view. Useful for long sessions where the Slack UI gets unwieldy.
- **Tool updates as separate messages.** Opencode subscribes to live events
  and posts each tool completion as its own message in the thread. This is
  good — it gives the user real-time visibility into what the agent is
  doing without forcing it all into one message.
- **Bolt's `socketMode`.** No public HTTP endpoint required for development.
  Nimbus should support both: socket mode for dev, HTTP webhook for
  production behind Render.
- **Skip non-text messages.** Subtype filter, then text presence check.
  This is correct.

### 4.3 The Slack onboarding modal

Slack supports modals via `views.open`. The flow:

```
User clicks "Get Started" button
  ↓
trigger_id passed to views.open
  ↓
Modal renders with:
  - Storage provider radio: S3 / GCS (disabled "coming soon")
  - AWS Access Key ID (input)
  - AWS Secret Access Key (input, type=password)
  - AWS Region (select)
  - AWS Bucket Name (input)
  - "Test Connection" button (action handler)
  - "Save" button (only enabled after Test Connection succeeds)
  ↓
Save → POST /nimbus/onboarding/complete with workspace_id, user_id, creds
  ↓
Backend stores creds (encrypted) in Postgres, scoped to (workspace_id, user_id)
  ↓
Nimbus DM: "You're set up. Try mentioning @nimbus in any channel."
```

**Critical security point.** In Slack, every workspace member can DM the
bot. Credentials must be scoped to *(workspace, user)*, not workspace-wide.
User A's S3 keys cannot be used to fulfill User B's requests. The
`VerifiedActor` already supports this; the Slack onboarding flow needs to
honor it by storing per-user creds.

### 4.4 First-message UX

After onboarding, the first time @nimbus is mentioned in a channel:

```
@user mentions @nimbus to list files
  ↓
Nimbus ACKs immediately (3-second deadline) with a "thinking" status
  ↓
Nimbus opens a thread on the original message
  ↓
First reply in thread: a friendly "Hi! I can help with..." block with
  - 3 example things the user can ask
  - A link to /nimbus help
  ↓
User replies with their actual request
  ↓
Nimbus streams the response (edits the message in place as tokens arrive)
  ↓
Tool calls appear as separate small messages in the thread
  ↓
Final response is the last message in the thread
```

The "thinking" status uses Slack's chat.update — Nimbus posts a placeholder
"🤔 Thinking..." then edits it as the response streams. This solves the
3-second ACK problem without exposing internal turn state to the user.

### 4.5 Slack-specific commands

```
/nimbus              opens an ephemeral panel with status, settings link
/nimbus help         lists capabilities and example prompts
/nimbus skills       lists available skills
/nimbus settings     opens a modal for permission rules, model choice
/nimbus disconnect   removes the user's stored creds
@nimbus <message>    starts/continues a thread (the main interface)
```

`/nimbus settings` is the same UX surface as `nimbus config edit` in the
CLI — the underlying config is the same per-user record in Postgres,
expressed as a TOML file in the CLI and a modal in Slack.

---

## 5. Slash commands and skills

### 5.1 Skills as `.md` files (Pi + Opencode pattern)

Add `skills/` at the repo root. Discovery scans this directory plus
optional user-level `~/.nimbus/skills/`. Each skill is a markdown file with
YAML frontmatter:

```markdown
---
name: archive-old
description: Archive files older than N days to a cold-storage prefix
disable-model-invocation: false
---

# Archive Old Files

Use this skill when the user asks to clean up or archive old files.

Steps:
1. List files under the configured prefix.
2. For each file with `last_modified > N days ago`, copy to `archive/`.
3. After all copies succeed, ask the user to confirm before deleting originals.
4. Delete originals only after explicit "yes archive".

Defaults:
- N defaults to 90 days unless the user specifies.
- Cold-storage prefix is `archive/` unless the user specifies.
```

In the CLI, `/archive-old` expands to inject this content as a system
message in the next turn. In Slack, the user types `/nimbus archive-old`
or includes `/archive-old` inline in an @nimbus message.

### 5.2 Built-in commands (Claude Code pattern)

Reading Claude Code's `src/commands/` directory shows ~70 commands. We
don't need that many. The minimal set for Nimbus:

| Command | What it does | CLI | Slack |
|---|---|---|---|
| `/help` | show capabilities + examples | ✓ | ✓ |
| `/clear` | clear current session, start fresh | ✓ | ✓ |
| `/compact` | force conversation summarization | ✓ | ✓ |
| `/cost` | show token + $ usage for the session | ✓ | ✓ |
| `/model` | list/change model | ✓ | ✓ |
| `/skills` | list available skills | ✓ | ✓ |
| `/permissions` | edit permission rules | ✓ (opens editor) | ✓ (modal) |
| `/sessions` | list past sessions | ✓ | ✓ (own threads) |
| `/resume <id>` | resume a past session | ✓ | (use thread) |
| `/share` | get a shareable URL for current session | ✓ | ✓ |
| `/feedback` | report a problem | ✓ | ✓ |

Each command is a typed function `(args, ctx) → CommandResult`. Same
implementation, different rendering. Lives in `nimbus_protocol.commands`,
imported by both surfaces.

### 5.3 Skill discovery — the Opencode pattern

Opencode searches *four* directories for compatibility with users coming
from other agents:

```
.claude/skills/**/SKILL.md     # claude code
.agents/skills/**/SKILL.md     # agents.md spec
opencode/skill/**/SKILL.md     # primary
opencode/skills/**/SKILL.md    # alt naming
```

For Nimbus, mirror this:

```
nimbus/skills/**/SKILL.md      # primary (in repo)
.nimbus/skills/**/SKILL.md     # user-local (in home dir)
.claude/skills/**/SKILL.md     # claude code compatibility
.agents/skills/**/SKILL.md     # agents.md spec compatibility
```

Lower-priority directories override only with a warning. Discovery is
deterministic — same files in same order produce the same list. Skills with
duplicate names emit a warning naming both sources.

### 5.4 Skill schema validation

```python
class SkillFrontmatter(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    description: str = Field(min_length=1, max_length=1024)
    disable_model_invocation: bool = False
```

Invalid skills emit warnings (not crashes) and are excluded from the
exposed set. The CLI's `nimbus skills check` command lints all discovered
skills and reports problems with file:line precision.

---

## 6. Tool permissions UX

### 6.1 The opencode shape, applied

```
Permission := { permission: kind, pattern: glob, action: "allow" | "deny" | "ask" }
```

For Nimbus, the kinds are:

```
storage.read           list_files, get_file_info, download_file
storage.write          upload_file
storage.delete         delete_file (single)
storage.bulk           any operation touching > N objects (configurable)
```

Default ruleset (in `~/.nimbus/permissions.toml`):

```toml
[[rule]]
permission = "storage.read"
pattern = "*"
action = "allow"

[[rule]]
permission = "storage.write"
pattern = "*"
action = "ask"

[[rule]]
permission = "storage.delete"
pattern = "*"
action = "ask"

[[rule]]
permission = "storage.bulk"
pattern = "*"
action = "ask"
```

Read is allow-by-default; everything else is ask-by-default. Users can
edit the file to add specific allows: `pattern = "scratch/**", action = "allow"`
for a scratch directory where the agent has free hands.

### 6.2 The "ask" UX

**CLI:** an Ink dialog appears inline in the chat:

```
┌─ Nimbus wants to use a tool ─────────────────────────────────┐
│ Tool: delete_file                                            │
│ Target: reports/2025-old/q1.pdf                              │
│ Reason: User asked to clean up Q1 files.                     │
│                                                              │
│ [Allow once]  [Allow always for reports/2025-old/**]  [Deny] │
└──────────────────────────────────────────────────────────────┘
```

Three buttons. "Allow always" appends a rule to `permissions.toml`.

**Slack:** the same prompt as a Block Kit message in the thread:

```
Nimbus wants to delete reports/2025-old/q1.pdf
Reason: User asked to clean up Q1 files.

[Allow once]  [Allow always]  [Deny]
```

Buttons carry signed payloads. Click → Slack POSTs the action handler →
Nimbus records the approval (with the user's Slack ID as the actor) and
proceeds.

### 6.3 The "always" decision is durable per (tenant, user)

When a user clicks "Allow always", we append a rule. The rule is scoped to
the user, not the workspace. User A approving "always allow delete in
/reports" does not grant User B the same in the same workspace. This is
the actor-scoped idempotency point from `INVARIANTS.md` applied to
permissions.

### 6.4 What happens when permissions disagree across surfaces

A user might run the CLI and Slack against the same backend with the same
identity. The permission rules live in Postgres (per-user), not in local
files. The local `~/.nimbus/permissions.toml` is a *cache* of the server
state. On `nimbus setup`, the wizard syncs the cache. On every CLI
invocation, the CLI checks for staleness (last sync > 1 hour) and
re-syncs.

---

## 7. Streaming and progressive disclosure

### 7.1 The streaming protocol

Add a new `ai_server` endpoint:

```
POST /ai/chat/turn/stream
Server-Sent Events response:
  event: turn_started
  data: { turn_id, runtime_spec }

  event: model_token
  data: { delta: "Looking at " }

  event: model_token
  data: { delta: "your bucket..." }

  event: tool_call_started
  data: { tool: "list_files", args: {...} }

  event: tool_call_completed
  data: { tool: "list_files", result_summary: "Found 47 files." }

  event: model_token
  data: { delta: "I found 47 files..." }

  event: turn_completed
  data: { turn_id, final_response, cost_usd, tokens }
```

This shape comes directly from Pi's `streamSimple` design and matches
Anthropic and OpenAI streaming formats.

### 7.2 CLI rendering

Ink's `<Text>` component re-renders on prop change. The chat view holds a
list of `MessagePart` objects in state. As `model_token` events arrive,
the last assistant message's text accumulates and the component
re-renders. As `tool_call_started` events arrive, a new `ToolPart`
appears in the list. As `tool_call_completed` arrives, the `ToolPart`
updates with the result summary.

This is exactly Pi's pattern (`packages/agent/src/agent-loop.ts`).

### 7.3 Slack rendering

Two strategies:

**Strategy A (in-place edit).** The first model token triggers `chat.update`
on a placeholder message. Subsequent tokens trigger more `chat.update`
calls. Slack rate-limits these to ~1 per second per channel — so we
batch-edit at most every 750ms.

**Strategy B (one message per part).** Each tool call posts as its own
message. The final response posts as a final message. Less smooth than
streaming text but never hits rate limits.

**Recommendation: hybrid.** Stream text via Strategy A (in-place edits at
≤1Hz). Post tool calls as separate messages (Strategy B). This matches
what Opencode's Slack package does.

### 7.4 Progressive disclosure for long results

A tool result with 500 file rows shouldn't dump 500 rows into the chat. The
harness post-processes results:

- If result fits in 10 lines → render fully
- If 10–100 lines → render top 10 + "and 47 more" with a `[show all]` button
- If 100+ lines → render summary statistics + first 5 + `[show all]`

The `[show all]` button (CLI: keypress; Slack: button) opens a modal/file
view with the full result. This is Cursor's approach in their TUI and
matches what a senior engineer expects: show the answer, not the data.

---

## 8. Configuration management

### 8.1 The hierarchy

From most local to most global:

```
1. CLI flag                       --model openai/gpt-4o
2. Environment variable           NIMBUS_MODEL=openai/gpt-4o
3. Project config                 ./.nimbus/config.toml
4. User config                    ~/.nimbus/config.toml
5. Built-in defaults              hardcoded in NimbusSettings
```

`pydantic-settings` natively supports this with `Settings.Config.env_file`
and a custom source loader for the project file.

### 8.2 What lives where

| File | Owner | Contents |
|---|---|---|
| `~/.nimbus/credentials.env` | user, 0600 | API keys, secrets — never committed |
| `~/.nimbus/config.toml` | user | model preference, theme, keybindings |
| `~/.nimbus/permissions.toml` | user (synced) | permission rules cache |
| `./.nimbus/config.toml` | project | per-project model/skill overrides |
| `./skills/*.md` | project | project-local skills |
| `./.nimbus/agents.md` | project | optional project context for the agent |

Slack-side config lives in Postgres, not files. The Slack `/nimbus settings`
modal is the editor for that config.

### 8.3 Credentials never in the project config

`pydantic-settings` has `SecretStr` for this. A field marked `SecretStr`
errors if it appears in a TOML file. Only `credentials.env` (gitignored,
0600) holds secrets. The CLI emits a warning with file:line if it finds a
credential-shaped string in a non-secret config file.

---

## 9. Session management

### 9.1 CLI sessions

```
nimbus                      # start new session, interactive
nimbus -c "list my files"   # one-shot, prints result, exits
nimbus --resume             # resume most recent session
nimbus --resume <id>        # resume specific session
nimbus sessions list        # list past sessions
nimbus sessions delete <id> # delete a session
```

Sessions live in Postgres (in production) or `~/.nimbus/sessions/` (in
local dev). A session ID is a short typeable string (e.g. `peaceful-otter-3a4b`)
not a UUID — discoverable by tab-completion in the shell.

### 9.2 Slack sessions

The thread *is* the session. Channel-thread → session ID is a 1:1 mapping
managed by the Slack adapter. Users don't need session IDs. The session
share URL (`/share` command) returns a link to a web view where the
session can be referenced from outside Slack.

### 9.3 Session resume across surfaces

A session started in the CLI can be continued in Slack and vice versa, as
long as the user's identity is the same. The user's CLI is authenticated
via API key; Slack is authenticated via OAuth. Both produce the same
`VerifiedActor.principal_key`. Session lookup by principal returns the
same sessions regardless of which surface created them.

This is a *latent* feature — we expose it via `/share`, but the
underlying capability is "any session with a known ID can be resumed by
any surface authenticated as its principal."

---

## 10. Error handling UX (continued from system redesign)

### 10.1 The three presentations, applied to surfaces

Companion doc covered the three-presentation model. The *rendering* of
each presentation differs by surface:

| Surface | User message | Developer detail | Audit |
|---|---|---|---|
| CLI | Inline message in chat, friendly tone | `nimbus debug last-error` shows full detail | event log |
| Slack | Block Kit error block in thread | `/nimbus debug last-error` ephemeral message | event log |
| HTTP | `error_code` + `user_message` in JSON | `request_id` for correlation | event log |

### 10.2 Error codes that matter for users

```
STORAGE_NOT_FOUND          "I couldn't find that file."
STORAGE_PERMISSION_DENIED  "I don't have permission to do that."
STORAGE_QUOTA_EXCEEDED     "Your storage quota is full."
RATE_LIMITED               "Slow down — too many requests."
MODEL_TIMEOUT              "The AI took too long to respond. Try again."
MODEL_UNAVAILABLE          "The AI is temporarily unavailable."
BUDGET_EXCEEDED            "This turn would exceed your budget."
POLICY_DENIED              "That action isn't allowed by your settings."
CONFIRMATION_REQUIRED      (handled separately as a flow, not an error)
UNKNOWN_ERROR              "Something went wrong. Reference: req-abc123"
```

The CLI maps each error code to a phrase. Slack does the same. The phrases
are in `nimbus_protocol/error_phrases.py` so both surfaces share the
translation.

### 10.3 The "something is wrong" path

When something genuinely unexpected happens (a Python exception we didn't
classify), the user sees:

```
Something went wrong on my end.
Reference: req-abc123-2026-05-08T15:33:21Z

Run `nimbus debug last-error` for details, or share this reference with support.
```

The detail is *always* available behind the request_id. Never inline. The
audit log records the full exception. Sentry receives it. The user gets a
phrase + a way to get more.

---

## 11. Friendly responses — concrete patterns

### 11.1 Conversational framing

Today's REPL: `[error] AIClientError: ...`. Future Nimbus:

```
> List my files in /reports

Looking at your reports/ folder...
  Found 47 files.

Here's what's there:
  • 12 PDFs (most recent: q3-summary.pdf, 2 days ago)
  • 30 spreadsheets
  • 5 images

Want me to break that down by date or size?
```

The phrasing comes from the system prompt. Pi's `system-prompt.ts` is
the model. Templates with variables for the active container, recent
operations, current date.

### 11.2 Acknowledge intent before acting

Pi-style: when the agent decides to call a tool, it *narrates* before the
call:

```
> Find the largest file in my bucket

Looking at the file sizes — let me list everything first.
  [list_files: 47 files]
  [get_file_info: 47 calls in parallel]

The largest file is reports/q3-summary.pdf at 18.2 MB.
```

The narration is the model's text *between* tool calls. Today our
streaming doesn't expose that intermediate text. The streaming protocol
(Section 7.1) does — `model_token` events fire continuously, including
between tool calls.

### 11.3 Confirmation phrasing

Bad:

```
Confirm: delete reports/q1.pdf? Type 'confirm' to proceed.
```

Good:

```
I'm about to delete reports/q1.pdf.
This file is 2.4 MB, last modified 6 months ago.
There's no undo for delete operations.

Type "yes" to delete, or anything else to cancel.
```

Concrete information ("2.4 MB, 6 months ago, no undo") + clear action +
clear escape hatch. Same words in CLI and Slack.

### 11.4 Streaming "thinking" phases

When the model is "thinking" (computing the next token before any text),
show a status:

```
🤔 Thinking about your bucket...
```

When a tool is running:

```
📂 Looking at reports/...
```

When all done:

```
✓ Done.
```

Phrases come from a small dictionary keyed by tool name. These are *not*
generated by the model — they're harness-level UI. The model's actual
response streams in alongside.

### 11.5 The "I don't know" path

When the model can't answer or the user's intent is unclear:

Bad: silence, or a wall of clarifying questions.

Good:

```
I'm not sure exactly what you're asking. Did you mean:
  1. List files in /reports
  2. Search for files containing "report"
  3. Show me the latest report

Or tell me more about what you're looking for.
```

Three concrete options + escape. Borrowed from Pi's clarification pattern.

---

## 12. Implementation phases

This is a lot. Order matters.

### Phase 0 — protocol package (1 sprint)

- Create `nimbus_protocol/` package with the typed wire types
- Move `ChatTurnInput`, `ChatTurnResult` from `ai_server` into protocol
- Move `RuntimeSpec` definition from runtime into protocol
- Add `TurnEvent`, `ToolApprovalRequest`, `NimbusError` types
- Update `ai_server` and `nimbus_runtime` to import from protocol

Nothing user-visible changes. This is foundational.

### Phase 1 — Python CLI fallback + setup wizard (1 sprint)

- Move `nimbus` Typer command out of `openrouter_ai_client_impl` into new
  `nimbus_cli/` package
- Implement `nimbus setup` wizard in Python (Typer + Rich)
- Implement `pydantic-settings` `NimbusSettings`
- Implement `nimbus quickstart` interactive demo

This gives a working onboarding flow even before the Ink CLI lands.

### Phase 2 — streaming endpoint + CLI subscriber (1 sprint)

- Add `POST /ai/chat/turn/stream` SSE endpoint to `ai_server`
- Implement streaming in the Python REPL (no Ink yet)
- The harness emits `TurnEvent` deltas (this lives in the runtime)

### Phase 3 — Ink CLI (2 sprints)

- New `cli-ink/` directory at repo root
- TypeScript + Ink, talks to `ai_server` over HTTP
- `Onboarding.tsx` step-based wizard (Claude Code pattern)
- `ChatView.tsx` streaming view
- `ToolApproval.tsx` permission UX
- `ConfirmationPrompt.tsx`
- Skills loader + slash commands

### Phase 4 — Slack onboarding + modal + first-message UX (2 sprints)

- Rename `slack_bridge` to `nimbus_slack`
- Slack OAuth flow + welcome DM
- Onboarding modal (`views.open`) for credential collection
- Per-user credential storage in Postgres (encrypted)
- First-message thread + intro block
- Streaming via in-place edit at ≤1Hz
- Tool calls as separate messages

### Phase 5 — skills + permission rules + config sync (1 sprint)

- Skills discovery from `nimbus/skills/`, `~/.nimbus/skills/`,
  `.claude/skills/`, `.agents/skills/`
- Skills validation
- Permission rules in `~/.nimbus/permissions.toml`, synced to Postgres
- `/nimbus settings` modal for Slack
- `nimbus permissions edit` for CLI

### Phase 6 — friendly responses + system prompt overhaul (1 sprint)

- Rewrite system prompt template with variables (Pi pattern)
- Phrase dictionary for status messages
- Confirmation phrasing improvements
- "I don't know" clarification flow

After phase 6, all four product priorities have shipped.

---

## 13. Open questions

1. **Do we ship the Python CLI fallback at all, or commit fully to TS+Ink?**
   The fallback exists for environments without Node. Maintaining two CLIs
   is real cost. If our user is always a developer who has Node, we can
   skip the Python fallback. Recommendation: ship fallback for the first
   release (so "first install" doesn't require Node), then evaluate.

2. **Skill format — markdown only, or also Python plugins?**
   Markdown skills are great for prompt scaffolds. They can't *do* anything
   beyond shape the model's behavior. If a skill needs to call code (e.g.,
   "compute file checksum"), it needs to be a tool. Recommendation:
   skills are markdown only; tools are Python. Skill that needs custom
   computation = ship a tool that the skill references.

3. **Slack credential storage — per-user or per-workspace-with-per-user-overrides?**
   Per-user is safest. Per-workspace shared creds are easier for teams.
   Recommendation: per-user only. Shared workspace creds are a future
   feature gated behind explicit admin approval and audit.

4. **Session ID format.** Short readable (`peaceful-otter-3a4b`) vs UUID
   (`f5d2-...`). Short readable is better UX, UUID is collision-proof.
   Recommendation: short readable for the "find me my session" use case;
   internal IDs are still UUIDs.

5. **Do we want a local web UI** (Pi has one)? Browser-based, talks to
   `ai_server`. Useful for users who don't live in terminals. Out of scope
   for this redesign but the protocol package makes it cheap to add later.

---

## 14. References

- Pi: `/Users/nanodijkstra/work/experiments/pi/packages/agent/src/harness/`
- Claude Code: `/Users/nanodijkstra/work/experiments/claude-code/src/components/Onboarding.tsx`,
  `/Users/nanodijkstra/work/experiments/claude-code/src/commands/`
- Codex: `/Users/nanodijkstra/work/experiments/codex/codex-rs/{cli,tui,protocol,login,core-skills}/`,
  `/Users/nanodijkstra/work/experiments/codex/AGENTS.md`
- Opencode: `/Users/nanodijkstra/work/experiments/opencode/packages/opencode/src/{permission,skill,tool}/`,
  `/Users/nanodijkstra/work/experiments/opencode/packages/slack/src/index.ts`
- Companion spec: `specs/ai-system-redesign.md`
