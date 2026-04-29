# Nimbus HW3 System Design

**Status:** Working design document
**Last updated:** 2026-04-28

## Purpose

This is the living system-design document for HW3.

It exists so a future chat can resume the design and implementation work without
reconstructing the architectural decisions from scratch.

## Locked Decisions From This Design Pass

- Optimize the system for chat-first use, not CLI-first use.
- Treat Slack as the first concrete chat shape, but keep the AI side generic so
  Discord or another chat vertical can use the same functionality later.
- The chat bridge will be a separate repository later. This repository owns the
  AI-side runtime and HTTP surface that the bridge will call.
- `ai_server` and `nimbus_runtime` will be one deployed AI service, but remain
  separate internal layers.
- Use Model A identity for HW3: `workspace_id + Slack user_id` defines the
  Nimbus principal.
- Slack never calls the Nimbus AI API directly. Slack always talks to the Slack
  app wrapper first, and the wrapper calls the Nimbus AI service.
- The Slack app wrapper is the public edge. The Nimbus AI service should be as
  protected as possible and should trust only the wrapper, not arbitrary
  internet callers.
- We are not building MCP host/client/server paths here.
- We are borrowing MCP concepts: schema-first tools, transport-agnostic
  capability boundaries, clear runtime responsibilities, and explicit safety
  policies.
- The AI HTTP surface should not be folded into the cloud-storage backend
  service. Keep the storage service and the AI/chat runtime as separate
  components.
- Add a shared runtime package so the HTTP server does not own business logic.
- Failures are the default case. Design timeouts, retries, idempotency,
  backpressure, and observability intentionally.
- Add observability seams in the implementation now, even if the final metrics
  backend and dashboards are added later.
- Deployment/IaC design will be handled later, but the runtime shape must leave
  room for them cleanly.
- Test infrastructure is now in place: 38 Hypothesis property-based tests, 3
  Atheris fuzz harnesses (smoke mode), and dedicated `property-tests` and
  `fuzz-smoke` CircleCI jobs. See `docs/source/testing.md` for the full guide.
- Integration/e2e coverage for the storage vertical now follows deterministic
  simulation-testing principles: fake-backed whole-workflow tests, reproducible
  subprocess e2e for `main.py`, and explicit auth / failure-path assertions
  across the service-adapter boundary.

## Problem Statement

HW3 is not about inventing chat completion. It is about building an intelligent,
deployed application that:

- integrates an external AI provider cleanly,
- integrates at least one other vertical through a shared API package,
- preserves clear architecture boundaries,
- emits latency, success, and failure telemetry,
- and can support a real chat-facing workflow.

For Team 2, the product is Nimbus: an AI-powered cloud-storage assistant.

## Product Vision

Nimbus should feel like a chat-native cloud-storage assistant.

From Slack first, and later other chat providers, a user should be able to:

- ask what files exist under a prefix,
- inspect metadata for a file,
- ask for a safe summary of a supported text object,
- request deletion of an object with explicit confirmation,
- and get concise, readable responses that fit chat workflows.

The AI side should expose this capability in a chat-neutral way so a Slack,
Discord, or Telegram wrapper can all drive the same runtime.

## How Nimbus Should Look In Slack

Slack is the first concrete UX target, even though the AI side must remain
generic.

### User experience goals

- A user talks to Nimbus in a channel or thread.
- Nimbus replies in-thread so context stays local to that conversation.
- Nimbus remembers prior turns within the same thread.
- Nimbus gives short answers first, then details only when asked.
- Nimbus never exposes raw stack traces, provider internals, local paths, or
  secrets.
- Nimbus treats destructive actions as two-step flows.

### Example Slack interactions

List files:

```text
User: What files do we have under reports/2026/?

Nimbus: I found 4 files under `reports/2026/`:
- `reports/2026/january.csv`
- `reports/2026/february.csv`
- `reports/2026/march.csv`
- `reports/2026/april.csv`

If you want, I can also inspect one of them or summarize a supported text file.
```

Inspect metadata:

```text
User: Tell me about reports/2026/april.csv

Nimbus: `reports/2026/april.csv`
- Size: 183 KB
- Last updated: 2026-04-18T14:22:05Z
- Version: 9f0f...
```

Summarize a text object:

```text
User: Summarize docs/release-notes.txt

Nimbus: Here is a short summary of `docs/release-notes.txt`:
1. Added monthly reporting export.
2. Fixed duplicate invoice handling.
3. Updated retention policy notes.
```

Delete with confirmation:

```text
User: Delete reports/2024/old.csv

Nimbus: I can delete `reports/2024/old.csv`, but this is destructive.
Reply with `yes, delete reports/2024/old.csv` if you want me to proceed.
```

```text
User: yes, delete reports/2024/old.csv

Nimbus: Deleted `reports/2024/old.csv`.
```

Provider or backend failure:

```text
Nimbus: I could not reach the AI provider in time. Please try again.
```

### Slack-specific presentation, generic AI-side behavior

Slack may later use formatting such as blocks, buttons, or thread replies, but
the AI side should not depend on Slack-only affordances.

The AI side should return chat-neutral response intent:

- plain response text,
- whether confirmation is required,
- whether the request was partially completed,
- what safe next action the user can take.

That lets future chat bridges format the same result for Slack, Discord, or
another provider.

## Slack Workflow: Top To Bottom

This section is the workflow-centric view of how Nimbus gets added to Slack.

We are not designing the separate chat-wrapper repository in detail yet, but we
do need a precise picture of how the AI-side system interacts with it.

### High-level workflow

```text
+------+     +-------------+     +----------------+     +----------------+
| User | --> | Slack client| --> | Slack platform | --> | Chat wrapper   |
+------+     +-------------+     +----------------+     +----------------+
                                                          | verify request
                                                          | dedupe event
                                                          | ack < 3 sec
                                                          v
                                                    +----------------+
                                                    |   ai_server    |
                                                    +----------------+
                                                          |
                                                          v
                                                    +----------------+
                                                    | nimbus_runtime |
                                                    +----------------+
                                                     | AIClient
                                                     | chat-safe tools
                                                     v
                              +-----------------------------------------------+
                              | CloudStorageClient -> local impl or HTTP path |
                              +-----------------------------------------------+
                                                          |
                                                          v
                                                    +----------------+
                                                     | Storage backend |
                                                     +----------------+
```

Important correction: Slack does not send requests directly to the Nimbus AI
API. The wrapper remains in the middle. If any earlier wording implied
"Slack -> ai_server" directly, that was the wrong mental model.

### Core Slack constraint

Slack requires a quick ACK for incoming events.

So the correct workflow is not:

```text
Slack -> bridge -> wait for full LLM/tool work -> respond
```

It is:

```text
Slack -> bridge -> verify + dedupe + ACK fast -> async Nimbus processing -> post reply later
```

That single constraint shapes the whole workflow.

## Should This Be A Slack App?

Yes.

If Nimbus is going to work well in Slack, the wrapper repository should be a real
Slack app, not just a generic webhook receiver.

### Why a Slack app makes sense

- Slack events, slash commands, app mentions, shortcuts, and app home all hang
  off the Slack app model.
- The Slack app gives us signed requests from Slack, scoped bot tokens, and a
  real installation lifecycle per workspace.
- It is the right place to own Slack-specific concerns such as OAuth install,
  bot membership, file access through Slack APIs, and thread reply behavior.

### Slack-app responsibilities

The Slack app wrapper should own:

- Slack request verification,
- workspace installation state,
- Slack bot token management,
- slash command handling,
- event subscription handling,
- message and thread posting,
- Slack-specific file fetches when the workflow needs channel attachments,
- mapping Slack users/workspaces into Nimbus principals.

### Important boundary

The Slack app is still not the AI runtime.

It is a transport and identity adapter around Slack.

## Can `ai_server` And `nimbus_runtime` Be One?

They can be one deployable, but they should not be one layer.

That is the important distinction.

### Good version

One deployed service, one container, one process if we want, but two clean
internal layers:

```text
single deployed service
  -> ai_server = HTTP transport adapter
  -> nimbus_runtime = chat/AI/storage orchestration layer
```

### Bad version

One big FastAPI router module that owns:

- HTTP parsing,
- auth,
- prompt building,
- tool policy,
- confirmation state,
- session lifecycle,
- retry logic,
- telemetry policy.

That shape works at first and then becomes hard to reason about, hard to test,
and hard to reuse for CLI or future chat providers.

### Recommendation

Ship them together if that reduces operational complexity.

Keep them logically separate so:

- the HTTP surface stays thin,
- runtime behavior is reusable,
- future Discord support does not duplicate logic,
- testing can target transport and runtime separately.

## Identity And Trust Chain

You explicitly called out an important point: the wrapper must not just accept
requests from anyone pretending to be Nimbus in Slack.

There are multiple trust hops here.

### Hop 1: Slack authenticates to the Slack app wrapper

The wrapper should verify:

- Slack request signature,
- request timestamp freshness,
- event replay resistance,
- workspace installation exists.

If this fails, the request never reaches Nimbus.

### Hop 2: Slack user is mapped to a real Nimbus principal

We should not treat any random inbound payload as a valid Nimbus actor.

The wrapper should establish a Nimbus principal from the Slack identity.

There are two reasonable models.

#### Model A: workspace-bound identity

- Slack workspace installs Nimbus.
- Slack `team_id + user_id` is treated as the Nimbus identity.
- Permissions are scoped by workspace configuration.

This is the chosen HW3 model.

#### Model B: linked Nimbus account

- User installs or opens Nimbus in Slack.
- User runs something like `/nimbus login`.
- Wrapper links `team_id + user_id` to a first-class Nimbus account.
- Requests are authorized as that Nimbus user.

This is the more rigorous long-term model.

### Hop 3: wrapper authenticates to `ai_server`

The wrapper should use a service credential to call our AI-side API.

Examples:

- short-lived signed service JWT,
- cloud workload identity / service identity,
- mTLS if the deployment environment supports it cleanly.

This protects `ai_server` from arbitrary third parties posting fake chat turns.

### Production-safe posture for `ai_server`

The strongest design stance is:

- the Slack app wrapper is the public internet-facing edge,
- the Nimbus AI service is not treated as a public endpoint for arbitrary
  clients,
- the wrapper is the only caller that should be able to reach `ai_server`,
- service-to-service authentication should use strong service identity rather
  than only a long-lived shared secret if the platform allows it.

In other words:

```text
Slack/public internet -> Slack app wrapper -> protected Nimbus AI service
```

not:

```text
Slack/public internet -> directly reachable ai_server
```

If infrastructure limits force public exposure for `ai_server`, the design
should still assume defense in depth:

- strong service authentication,
- strict request validation,
- replay resistance where practical,
- correlation IDs,
- rate limiting,
- auditability.

### Hop 4: runtime authorizes actions using actor context

The runtime should not see only text. It should also receive:

- platform,
- workspace/team,
- user identity,
- conversation identity,
- request ID,
- idempotency key.

That allows action logs, rate limiting, and permission-aware behavior later.

## Investor-Style Story: What Nimbus Feels Like

This is the kind of product story I would pitch.

### Story 1: "What files do we have here?"

```text
You are in Slack. You type:

  @Nimbus what files do we have under reports/2026?

Slack sends the event to the Nimbus Slack app.

The Slack app verifies that the request really came from Slack, checks that your
workspace actually has Nimbus installed, confirms that you are a real Nimbus
user in that workspace, and immediately ACKs Slack so the chat UI stays fast.

Then the Slack app sends a normalized request to Nimbus's AI service.

Nimbus's AI service passes that request into the shared runtime, which loads the
thread's prior context, sees who you are, builds the right prompt, and gives the
AI a safe, chat-sized tool surface.

The model decides it needs storage data, so it calls `list_files(prefix="reports/2026")`.

The runtime executes that through `CloudStorageClient`, gets the object list,
summarizes it into a bounded result, and feeds that back into the model.

The model writes a concise answer.

Nimbus persists the updated thread state, records latency/success telemetry, and
returns a chat-neutral response to the Slack app.

The Slack app posts the answer back into the same thread.
```

### Story 2: "Delete this file safely"

```text
You type:

  @Nimbus delete reports/2024/old.csv

Slack sends the message to the Slack app.

The app authenticates the request, ACKs Slack quickly, and forwards the request
 to Nimbus.

Nimbus does not blindly delete anything.

The runtime recognizes that this is destructive. It creates a pending action,
binds it to your identity and the current conversation, and returns a
confirmation prompt instead of executing immediately.

The Slack app replies:

  Reply with: yes, delete reports/2024/old.csv

When you confirm, Slack sends another real signed event to the app.

The app again authenticates and forwards it.

Nimbus matches the confirmation to the pending action, verifies that the same
conversation and user are confirming it, performs the delete through the storage
API, records the result, and sends a completion message back to Slack.
```

### Story 3: "/nimbus upload all files in this channel"

This is the story that reveals why the Slack app matters.

```text
You type:

  /nimbus upload all files in this channel to finance/april/

Slack sends the slash command to the Nimbus Slack app.

The Slack app verifies the signed request, identifies your workspace and user,
ACKs Slack immediately, and parses the command intent.

Because Slack owns channel files, the Slack app uses its bot token to inspect
the channel context and discover which Slack files are relevant.

The Slack app then sends Nimbus a normalized request that includes your intent
plus a safe attachment reference set derived from Slack.

Nimbus's runtime decides what to do with that attachment set. It can validate
the destination prefix, ask for confirmation if the action is broad, and then
drive upload operations through a chat-safe attachment ingestion flow instead of
CLI-style local-path upload.

Nimbus uploads the files through the storage API, records what succeeded and
what failed, and returns a structured outcome.

The Slack app posts back something like:

  Uploaded 7 files to `finance/april/`.
  1 file was skipped because it exceeded the allowed size.
```

The important insight is that channel-file upload is not the same as local-path
upload. It requires the Slack app boundary to resolve Slack-native files into a
shape Nimbus can safely ingest.

## ASCII Story Diagram

```text
You in Slack
   |
   |  @Nimbus summarize docs/release-notes.txt
   v
Slack client
   |
   v
Slack platform
   |
   |  signed event
   v
Nimbus Slack app
   |  verify signature
   |  check workspace install
   |  map Slack user -> Nimbus principal
   |  dedupe event
   |  ACK fast
   v
Nimbus AI service
  (`ai_server` + `nimbus_runtime`
   in one deployable service)
   |  authenticate wrapper
   |  validate request
   v
Nimbus runtime layer
   |  load thread state
   |  check pending confirmations
   |  build prompt and tool surface
   v
AI provider
   |  may request safe storage tool
   v
CloudStorageClient
   |  call storage impl/service
   v
Storage backend
   |
   v
Nimbus runtime
   |  summarize result
   |  persist state
   |  emit telemetry
   v
Nimbus Slack app
   |  format Slack reply
   v
Slack thread reply
```

## What `/nimbus recent` Means Architecturally

`/nimbus recent` is a good idea, but it is not a keyboard-history feature.

It is a product feature backed by storage.

### Does it require Redis?

Not necessarily at first.

### MVP options

#### Option 1: derive from the existing conversation/session store

- store user prompts in the runtime session history,
- fetch the last N user turns for that user or conversation,
- return them through the Slack app.

This does not require Redis.

#### Option 2: wrapper-local recent-command store

- wrapper stores the last N slash commands or prompts per user,
- useful if `/nimbus recent` is primarily a Slack UX feature.

This also does not require Redis for a single-instance deployment.

### When Redis becomes useful

Redis becomes attractive when we need:

- multi-instance wrapper deployments,
- short-lived dedupe caches for Slack event IDs,
- per-user recent prompt caches,
- rate limiting,
- lightweight shared state across replicas.

### Recommendation

For HW3, do not make Redis a prerequisite for `/nimbus recent`.

Treat Redis as a scale-up primitive later, not as a blocker for the workflow
design.

## The End-To-End Slack Turn

### Phase 1: User sends a message in Slack

Examples:

- mention in channel,
- thread reply to Nimbus,
- direct message to Nimbus.

Slack emits an event containing at least:

- team/workspace identifier,
- channel identifier,
- thread timestamp or root message timestamp,
- message timestamp,
- user identifier,
- text,
- event identifier.

### Phase 2: Slack delivers the event to the chat wrapper

The wrapper is responsible for transport-specific concerns:

- verify Slack signature,
- reject replayed or stale requests,
- ignore bot messages and irrelevant events,
- dedupe by Slack event ID,
- ACK Slack quickly,
- translate Slack's event shape into a canonical chat-turn request.

### Phase 3: Wrapper calls our AI-side HTTP API

The wrapper should call a stable AI-side endpoint with a chat-neutral payload.

The payload should contain enough identity to support:

- conversation continuity,
- idempotency,
- observability,
- future Discord compatibility.

Proposed logical request shape:

```text
platform
workspace_id
channel_id
thread_id
message_id
user_id
text
idempotency_key
```

### Phase 4: `ai_server` converts HTTP to a runtime request

`ai_server` should do only transport-edge work:

- authenticate the wrapper,
- validate the request schema,
- assign or propagate request IDs,
- map HTTP payloads to runtime request objects,
- map runtime failures back to HTTP responses.

It should not own:

- prompt assembly,
- tool rules,
- session business policy,
- delete confirmation state machine,
- AI retry/fallback logic.

### Phase 5: `nimbus_runtime` processes the turn

This is the real center of the workflow.

`nimbus_runtime` should:

1. build the normalized conversation ID,
2. acquire the per-conversation lock,
3. load conversation/session state,
4. check whether there is a pending confirmation action,
5. construct the prompt and tool set,
6. call the configured AI client,
7. execute chat-safe storage tools if requested,
8. record telemetry events,
9. persist the updated session,
10. return a chat-neutral response object.

### Phase 6: wrapper posts the result back to Slack

The wrapper should receive a response shape that tells it:

- what text to post,
- whether it is a normal reply or a confirmation prompt,
- which thread/channel to post into,
- whether the action partially succeeded,
- whether the user should retry.

The wrapper then translates that into Slack-specific messaging.

## Slack Workflow Variants

### A. Plain answer, no tool call

```text
User
  -> Slack message
  -> Slack event
  -> chat wrapper
  -> ai_server
  -> nimbus_runtime
  -> AIClient
  -> text response only
  -> ai_server
  -> chat wrapper
  -> Slack reply in thread
```

Example:

- User asks what Nimbus can do.
- No storage tool is needed.
- Nimbus replies with capabilities and guidance.

### B. Tool-backed answer

```text
User
  -> Slack message
  -> wrapper ACKs Slack
  -> wrapper calls ai_server
  -> ai_server calls nimbus_runtime
  -> runtime calls AIClient
  -> AIClient requests list_files(prefix="reports/")
  -> runtime tool handler calls CloudStorageClient
  -> storage result returns to runtime
  -> runtime feeds summarized result back to AIClient
  -> AIClient produces final answer
  -> runtime persists session
  -> ai_server returns response
  -> wrapper posts final Slack reply
```

### C. Destructive action with confirmation

This is important enough to call out separately.

For chat, confirmation should be runtime-managed, not just a model guessing when
to pass `confirm=true`.

Recommended logical flow:

```text
User: delete reports/2024/old.csv

runtime:
  detect destructive intent
  create pending action record
  do NOT execute delete yet
  return confirmation_required response

Wrapper posts:
  "Reply with: yes, delete reports/2024/old.csv"

User: yes, delete reports/2024/old.csv

runtime:
  match pending action
  verify confirmation still valid
  execute delete
  clear pending action
  return completion response
```

This is safer than making confirmation a pure LLM convention.

### D. Failure path

```text
Slack event
  -> wrapper ACKs fast
  -> wrapper calls ai_server
  -> runtime calls AI provider
  -> provider times out
  -> runtime classifies timeout
  -> runtime records telemetry
  -> ai_server returns structured failure
  -> wrapper posts user-safe Slack reply
```

Example user-visible result:

```text
Nimbus: I could not reach the AI provider in time. Please try again.
```

## Boundary Between The Wrapper And Our AI Side

Even though the wrapper is a separate repo, the workflow only works if this
boundary is precise.

### What the wrapper should send us

- canonical platform name: `slack`
- workspace/team ID
- channel ID
- thread ID or root message ID
- source message ID
- user ID
- plain text body
- idempotency key
- request ID if it has one

### What we should send back

- response text
- normalized conversation ID
- whether confirmation is required
- optional confirmation prompt text
- optional safe next actions
- machine-readable outcome class

### What should not cross this boundary

- Slack signature verification logic
- Slack-specific block JSON in the runtime
- provider-specific AI payloads
- raw storage backend exceptions

## Implemented Wrapper Contract

The current implemented AI-side contract is:

```text
POST /ai/chat/turn
```

### Request body

```json
{
  "platform": "slack",
  "workspace_id": "T123TEAM",
  "channel_id": "C123CHAN",
  "thread_id": "1713840000.123456",
  "message_id": "1713840000.123457",
  "user_id": "U123USER",
  "text": "What files are under reports/?",
  "idempotency_key": "slack:T123TEAM:event:evt-123",
  "request_id": "req-wrapper-123"
}
```

### Conversation identity rule

Nimbus derives the internal conversation ID as:

```text
platform:workspace_id:channel_id:(thread_id or message_id)
```

Examples:

- `slack:T123TEAM:C123CHAN:1713840000.123456`
- `slack:T123TEAM:C123CHAN:1713840000.123457`

The wrapper therefore controls conversation anchoring explicitly by deciding
what to send as `thread_id`.

### Response body

```json
{
  "request_id": "req-wrapper-123",
  "conversation_id": "slack:T123TEAM:C123CHAN:1713840000.123456",
  "text": "Hello from Nimbus!",
  "outcome": "reply",
  "confirmation_required": false,
  "suggested_next_actions": [],
  "model": "test-model:free",
  "steps": 1,
  "fallback_used": false
}
```

### Auth contract

The wrapper-facing route uses signed-request auth instead of the legacy API key.

Required headers:

- `X-Nimbus-Timestamp`
- `X-Nimbus-Nonce`
- `X-Nimbus-Signature`

The signature is a hex HMAC-SHA256 over:

```text
METHOD + "\n" + PATH + "\n" + TIMESTAMP + "\n" + NONCE + "\n" + SHA256(BODY)
```

The server validates:

- signing secret configured,
- timestamp freshness,
- nonce replay resistance,
- signature correctness.

### Current guarantees

- best-effort idempotent replay by `platform + workspace_id + idempotency_key`
- per-user rate limiting keyed by `user_id`
- persisted conversation continuity keyed by derived conversation ID
- stable machine-readable response envelope for the wrapper
- `/ai/chat/turn` is the only supported chat entrypoint

## ASCII Sequence Diagram

```text
User            Slack         Chat Wrapper         ai_server        nimbus_runtime      AIClient       Storage
 |                |                |                   |                 |                 |              |
 | send message   |                |                   |                 |                 |              |
 |--------------->|                |                   |                 |                 |              |
 |                | event webhook  |                   |                 |                 |              |
 |                |--------------->|                   |                 |                 |              |
 |                |                | verify signature  |                 |                 |              |
 |                |                | dedupe event      |                 |                 |              |
 |                |                | ACK 200           |                 |                 |              |
 |                |<---------------|                   |                 |                 |              |
|                |                | POST /chat/turn   |                 |                 |              |
 |                |                |------------------>|                 |                 |              |
 |                |                |                   | map request     |                 |              |
 |                |                |                   |---------------->| load session    |              |
 |                |                |                   |                 | call AI         |              |
 |                |                |                   |                 |---------------->|              |
 |                |                |                   |                 |                 | tool call     |
 |                |                |                   |                 |<----------------|-------------->|
 |                |                |                   |                 | final response  |              |
 |                |                |                   |<----------------|                 |              |
 |                |                | HTTP response     |                 |                 |              |
 |                |                |<------------------|                 |                 |              |
 |                |                | post thread reply |                 |                 |              |
 |                |<---------------|                   |                 |                 |              |
 | read reply     |                |                   |                 |                 |              |
```

## What We Can Borrow From MCP's Transport Layer

We are not adopting MCP transport, but we can borrow its discipline.

### 1. Separate transport from capability semantics

Slack delivery format should stop at the wrapper boundary.

Inside our system, we should use one canonical request model and one canonical
response model. That is the same conceptual benefit MCP gets from transport-
independent tool and resource semantics.

### 2. Treat the wrapper-to-AI boundary as a versioned protocol

Even if it is just HTTP + JSON, define it like a real contract:

- stable request schema,
- stable response schema,
- explicit error shapes,
- explicit IDs and correlation fields,
- versioning if the contract changes.

### 3. Keep request IDs, correlation IDs, and action IDs explicit

Borrow the idea that messages should be traceable across boundaries.

We should preserve:

- source event ID,
- request ID,
- conversation ID,
- pending-action ID for confirmations.

### 4. Design for async boundaries explicitly

Slack is not a synchronous request-response chat transport from our point of
view. The wrapper must ACK Slack and post later.

That means our HTTP contract should assume:

- asynchronous upstream transport,
- retries and duplicate delivery,
- separate inbound and outbound message timing.

### 5. Keep transport adapters thin

This is a major MCP-like lesson.

Transport adapters should do transport work, not runtime policy.

For us:

- wrapper handles Slack transport,
- `ai_server` handles HTTP transport,
- `nimbus_runtime` handles application behavior.

## What We Can Borrow From MCP's Data Layer

Again, not the protocol itself, but the modeling discipline.

### 1. Distinguish raw text from structured content

Do not treat everything as one big string.

Internally, we should model:

- user text,
- assistant text,
- tool results,
- confirmation prompts,
- metadata summaries,
- failure outcomes.

This will keep Slack and future Discord behavior more consistent.

### 2. Keep tool inputs and outputs schema-first

This is already aligned with the repo's direction.

Each tool should have:

- strict typed input schema,
- stable output shape,
- bounded content size,
- sanitized content.

### 3. Separate transport metadata from domain state

Slack transport fields are not the same thing as runtime conversation state.

We should conceptually keep separate:

- transport metadata: platform, team, event ID, message ID,
- runtime state: conversation history, pending confirmation, summaries,
- tool domain data: object metadata, list results, delete outcome.

### 4. Use structured response envelopes, not just plain text

Even if Slack initially only posts plain text, our AI-side response model should
be richer than a string.

For example:

- `text`
- `outcome`
- `confirmation_required`
- `suggested_next_actions`
- `conversation_id`

That is similar to borrowing MCP's idea that content can carry structure and not
just presentation.

### 5. Preserve provenance for tool-derived facts

When Nimbus says something based on a storage operation, the runtime should know
that the answer came from:

- model-only reasoning,
- file listing metadata,
- a text summary derived from a specific object,
- a delete action outcome.

That is useful for correctness, observability, and future auditing.

## Slack Input History: Up Arrow / Down Arrow

This is the one place where the answer is mostly "no" for the normal Slack
composer.

### What is not realistically under our control

We do not control Slack's native message input box behavior.

That means we cannot reliably make up-arrow and down-arrow cycle through Nimbus-
specific input history inside the standard Slack composer the way a terminal REPL
can.

### What Slack does support instead

Slack supports things like:

- slash commands,
- buttons and interactive actions,
- modals,
- app home surfaces,
- message shortcuts.

So if we want "history recall" later, it would need to be expressed as a Slack-
native UX pattern, not by hijacking arrow keys.

### Better alternatives for history recall

- `history` or `recent prompts` view in the app home,
- a message shortcut like `Reuse prompt`,
- a slash command like `/nimbus recent`,
- explicit follow-up suggestions in replies.

So for design purposes, I would treat arrow-key history in Slack as a non-goal.

## Top-To-Bottom Request Flow

Ignoring the external chat bridge for now, the AI side should look like this:

```text
Chat wrapper repo
-> ai_server HTTP API
-> nimbus_runtime shared runtime
-> AIClient implementation
-> chat-safe storage tool surface
-> CloudStorageClient
-> storage backend or adapter/service path
```

### End-to-end flow

1. A chat wrapper normalizes an inbound chat message and calls `ai_server`.
2. `ai_server` authenticates the caller and maps the HTTP request into a
   runtime request model.
3. `nimbus_runtime` derives the conversation key, acquires the per-session
   lock, loads session state, and validates policy limits.
4. `nimbus_runtime` calls the configured `AIClient` with a curated tool set.
5. The AI client may call chat-safe storage tools.
6. Tool handlers call `CloudStorageClient` through the injected implementation.
7. `nimbus_runtime` records events for telemetry, updates session state, and
   returns a chat-neutral response object.
8. `ai_server` serializes that response to HTTP for the external chat wrapper.

## Proposed Component Model

The storage service and the AI/chat runtime should stay separate.

### Why not put the AI routes inside `aws_client_service`

- The storage service should stay a storage transport adapter.
- The AI system has different responsibilities: conversations, tool orchestration,
  chat semantics, confirmation flows, rate limiting, AI error handling, and
  telemetry.
- Keeping them separate preserves cleaner boundaries and makes future chat
  wrappers simpler.

### New shared runtime package

Add a new package dedicated to the reusable chat-oriented AI runtime.

This runtime should own:

- chat-neutral request and response models,
- session lifecycle and locking,
- system prompt assembly,
- tool registration and confirmation policy,
- AI invocation and provider fallback behavior,
- runtime-level error classification,
- observability hooks.

## Target Project Structure

```text
src/
  ai_client_api/
    ai_client_api/
      client.py
      conversation.py
      exceptions.py
      models.py
    tests/

  openrouter_ai_client_impl/
    openrouter_ai_client_impl/
      cli.py
      config.py
      openrouter_client.py
      cloud_storage_tools.py
    tests/

  nimbus_runtime/
    nimbus_runtime/
      __init__.py
      runtime.py
      models.py
      sessions.py
      prompts.py
      tool_registry.py
      storage_chat_tools.py
      confirmation.py
      telemetry.py
      errors.py
      policies.py
    tests/
    pyproject.toml

  ai_server/
    ai_server/
      __init__.py
      auth.py
      deps.py
      router.py
      http_models.py
      http_error_map.py
    tests/
    pyproject.toml

  aws_client_impl/
  aws_client_service/
  aws_client_adapter/
  aws_s3_cloud_storage_service_client/
```

## Responsibility Split

### `ai_client_api`

- stable provider-agnostic AI contract
- core conversation and tool models
- domain exceptions for AI failures

### `openrouter_ai_client_impl`

- concrete AI provider integration
- model fallback behavior
- provider-specific request/response translation
- CLI remains here for now, but should eventually depend more on
  `nimbus_runtime` so the runtime is not duplicated

### `nimbus_runtime`

- reusable, chat-optimized orchestration layer
- the real center of the AI application
- owns the contract the future chat wrapper should depend on indirectly through
  `ai_server`

### `ai_server`

- HTTP transport adapter around `nimbus_runtime`
- auth, request parsing, response serialization, HTTP error mapping
- should not own storage tools, session logic, or business policy

### Storage packages

- remain focused on the storage vertical
- continue to expose `CloudStorageClient`
- do not absorb chat or AI runtime concerns

## MCP Parallels We Are Intentionally Borrowing

We are not implementing MCP, but its concepts are useful.

### MCP-like concept: tools

Our equivalent is a chat-safe tool registry in `nimbus_runtime`.

- schema-first definitions
- explicit names and descriptions
- narrow, safe capability surface
- confirmation requirements for destructive actions

### MCP-like concept: resources

Our equivalent is bounded context fed into the runtime, not an external
resource server.

Examples:

- current conversation history,
- object metadata summaries,
- short safe text extracts,
- policy/config context.

### MCP-like concept: prompts

Our equivalent is prompt construction inside `nimbus_runtime.prompts`.

- one place to define system policy
- one place to encode tool-usage instructions
- one place to keep provider-independent prompt behavior

### MCP-like concept: host orchestration

Our equivalent is `nimbus_runtime.runtime`.

- the runtime owns the loop around session state, AI calls, tool calls,
  confirmation rules, and telemetry
- transport adapters stay outside of it

### Important non-goal

There is no MCP host/client/server transport path in this design.

All of these concepts stay in-process and repo-local.

## Chat-Neutral Runtime Contract

The runtime should not accept a Slack-specific request model.

It should accept something closer to this shape:

```python
ChatTurnRequest(
    platform="slack",
    channel_id="C123",
    thread_id="1713840000.123",
    user_id="U123",
    message_id="slack-msg-123",
    text="What files are in reports/?",
    idempotency_key="slack:team:T1:channel:C123:message:slack-msg-123",
)
```

And it should return something like:

```python
ChatTurnResponse(
    text="I found 4 files under reports/.",
    conversation_id="slack:C123:1713840000.123",
    confirmation_required=False,
    suggested_next_actions=("inspect a file", "summarize a text object"),
)
```

That keeps the AI side generic while still optimizing for chat semantics.

## Session Model

The conversation identity should be chat-oriented.

### Conversation key

Use a normalized runtime conversation key such as:

```text
platform:channel_id:thread_id_or_message_id
```

Examples:

- `slack:C123:1713840000.123`
- `discord:channel42:thread99`

### Why this matters

- Slack threads and Discord threads should both map cleanly into one runtime
  concept.
- The AI side should remember context where users expect it: inside the thread.
- Session locking, idempotency, and observability should all key off the same
  normalized conversation identity.

## Tool Surface For Chat

The chat tool surface must be smaller and safer than the CLI tool surface.

### V1 tools

- `list_files(prefix="")`
- `get_file_info(remote_path)`
- `delete_file(remote_path, confirm)`

### Likely V1.5 tool

- `summarize_text_object(remote_path)`

This should only work for:

- explicitly supported content types,
- small bounded object sizes,
- bounded extraction limits.

### Intentionally excluded from chat

- raw local-path upload
- raw local-path download
- arbitrary filesystem access

These fit the CLI, not chat.

### Design rules for chat tools

- every tool must have a strict schema
- destructive tools require explicit confirmation
- tools must return summarized, sanitized results
- tools must not expose raw file contents unless the capability is explicitly
  designed for that use case
- tool calls must be bounded in size, time, and result shape

## Failure-First Design

Failures are not edge cases here. They are the main design input.

### Ingress failures

- malformed request
- missing auth
- duplicate message delivery
- oversized text or invalid IDs

Design response:

- validate at the boundary
- require caller auth
- support idempotency keys
- reject unsafe or oversized input early

### Session failures

- concurrent turns in the same thread
- session file corruption
- session save failure
- unbounded context growth

Design response:

- per-conversation lock
- atomic writes
- discard corrupted session files safely instead of crashing
- plan for rolling summarization or truncation

### AI provider failures

- timeout
- rate limit
- provider 5xx
- tool loop runaway

Design response:

- provider timeout budget
- bounded retries only where safe
- fallback model where appropriate
- explicit step budget
- clear user-facing error text

### Tool and storage failures

- storage timeout
- object not found
- invalid path
- delete race
- prompt injection through tool output

Design response:

- translate backend failures into stable runtime errors
- sanitize tool output before feeding it back to the model
- keep delete idempotent where possible
- pin bucket/container and enforce allowlists

### Overload failures

- too many concurrent requests
- queue growth
- chat burst traffic

Design response:

- bounded concurrency
- rate limiting by user and conversation
- clear overload failure instead of silent collapse
- telemetry on saturation and queueing

## Observability Seams To Implement Now

Even before selecting the final monitoring stack, the runtime should emit
structured events and metrics-friendly hooks.

### Runtime events

- request received
- request completed
- request failed
- AI call started
- AI call completed
- AI fallback used
- tool call started
- tool call completed
- confirmation requested
- confirmation accepted
- confirmation denied
- session save failed
- rate limited

### Minimum data to attach

- request_id
- conversation_id
- user_id
- platform
- model
- tool_name
- latency_ms
- success or failure
- failure class

### Implementation direction

Add a runtime-local telemetry abstraction first, such as `TelemetryRecorder`,
so the runtime can emit consistent signals without binding immediately to a
specific metrics backend.

## Immediate Implementation Implications

This design suggests the following refactor path.

### 1. Create `nimbus_runtime`

- add a new workspace package
- move reusable runtime logic there

### 2. Move session ownership out of `ai_server`

- session storage, locking, and conversation policy belong to the runtime
- `ai_server` should call the runtime, not manage sessions directly

### 3. Replace `slack_tools.py` with generic chat-safe storage tools

- move tool ownership into `nimbus_runtime.storage_chat_tools`
- keep the surface chat-safe and provider-neutral

### 4. Refactor `ai_server` into a transport adapter

- keep auth and HTTP mapping
- remove runtime policy from router handlers

### 5. Add observability hooks in the runtime

- instrument request, AI, tool, and session lifecycle now
- wire the concrete backend later

### 6. Keep the chat bridge out of this repo for now

- this repo should expose a stable AI-side API for the future chat wrapper

## Work Backlog From This Design

### Architecture

- create `src/nimbus_runtime/`
- define chat-neutral runtime request and response models
- decide what moves from `ai_server` into `nimbus_runtime`
- decide whether CLI should start consuming `nimbus_runtime` now or later

### Tooling

- define the exact chat-safe tool surface
- decide whether `summarize_text_object` is in the first implementation wave
- keep delete confirmation explicit and stateful

### Resilience

- add provider timeout budgets
- classify retry-safe failures
- plan idempotency-key handling
- add per-user and per-conversation rate limiting later

### Observability

- add a telemetry abstraction
- emit runtime lifecycle events consistently
- keep IDs stable across the request path

### Future bridge readiness

- keep request and response shapes chat-neutral
- avoid Slack-only response types in the AI-side runtime
- preserve enough metadata for a future wrapper to map into Slack or Discord

## Next Deep-Dive Sections

These should be handled in follow-up system-design passes.

1. Exact chat tool surface and confirmation UX.
2. Exact runtime request and response schemas.
3. Detailed failure matrix and retry policy.
4. Observability event model and metrics naming.
5. HTTP API shape exposed by `ai_server` for the future chat wrapper.
6. How CLI should share the runtime without regressing local workflows.
