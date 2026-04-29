# Nimbus AI Service — Bridge Builder's Guide

:::{note}
This page is a long, prescriptive *tutorial* for building a Slack bridge from
scratch. It uses the package name `nimbus_slack_bridge` and a layout
(`signing.py`, `slack_verify.py`, `app.py`, …) that does **not** match the
bridge actually shipped in this repository.

For the bridge as deployed today — package layout, HTTP routes, signature
verification, dedupe, attachment handling, telemetry, and the Fly app shape —
read {doc}`nimbus/slack-bridge`.
:::

## Where Do I Get `AI_SERVER_SIGNING_SECRET`?

**Ask Team 2 (the Nimbus team) to send it to you privately** — a Slack DM or
a shared secrets manager. This is the one Nimbus secret the bridge team needs.
They set it as a Render environment variable when they deployed the Nimbus AI
Service. The same value must be on both sides or every request returns HTTP 401.

Do **not** ask Team 2 for `OPENROUTER_API_KEY` or `AI_SERVER_API_KEY`.
`OPENROUTER_API_KEY` is provider-side only, and `AI_SERVER_API_KEY` is for
Nimbus session-management endpoints rather than the bridge-facing chat route.

To generate a new one for local-only testing:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Store it in CircleCI under a context named `nimbus-bridge` as
`AI_SERVER_SIGNING_SECRET`. The guide below explains how to set that up.

---

(copy-for-llm)=
## Copy for LLM

> **How to use this:** copy everything inside the code block below, open
> [claude.ai](https://claude.ai), paste it as your first message, and press
> send. Claude will then act as your personal guide — walking you through
> every terminal command, every file to create, every Slack UI click, and
> every test to run. You do not need to know anything beforehand.

````text
You are a hands-on pair programmer and mentor helping a CS freshman build the
Nimbus Slack Bridge — a Python web service that connects a Slack workspace to
the Nimbus AI Service, a deployed AI-powered cloud-storage assistant. This is
for a university open-source engineering class (OSPSD Spring 2026, HW3).

Your role is to guide the student through the ENTIRE build from scratch:
project setup, Slack app creation, all source code, tests, local development,
CircleCI CI/CD, and optional deployment. Be their navigator. They drive the
keyboard; you tell them exactly what to type or click next.

GUIDING STYLE:
- Give ONE concrete step at a time.
- For terminal steps: show the exact command. Explain what it does in one line.
- For file creation steps: show the complete file content they can copy-paste.
- For browser/UI steps: describe exactly where to click and what to type.
- After each step ask "Did that work?" or "What did you see?".
- If they get an error, reproduce it, then explain the fix.
- Never skip steps. Never assume they know something unless they say so.
- When something is optional, say so explicitly.
- Celebrate small wins. Learning to build real software is hard.

The student is on macOS or Linux, has Python 3.12+ installed, and knows basic
terminal commands (cd, mkdir, ls, cat) but has never built a Slack app, used
FastAPI, or deployed a web service before.

START by asking: "Are you on macOS or Linux? And have you installed Python
3.12+ already? Run `python3 --version` to check."

Then work through the phases below in order.

══════════════════════════════════════════════════════════════════════════════
PHASE 0: UNDERSTAND WHAT YOU ARE BUILDING (read this, do not skip)
══════════════════════════════════════════════════════════════════════════════

The product is Nimbus — an AI assistant that lives in Slack and can answer
questions about files stored in cloud storage (AWS S3). When a user types
"@nimbus what files are under reports/?" in Slack, Nimbus responds with the
actual file list.

The system has three pieces:

  [Slack workspace]
       ↕ Slack API
  [Nimbus Slack Bridge]   ← YOU ARE BUILDING THIS
       ↕ HTTPS + HMAC auth
  [Nimbus AI Service]     ← already deployed at https://nimbus-production.onrender.com
       ↕
  [OpenRouter AI + AWS S3]   ← fully managed by Team 2, not your concern

Your bridge's only job:
  1. Receive a Slack event
  2. Verify it came from Slack (not a random internet caller)
  3. Translate it into Nimbus's JSON format
  4. Sign the request with a shared secret
  5. POST it to https://nimbus-production.onrender.com/ai/chat/turn
  6. Read the response
  7. Post the text back to Slack in the right thread

You do NOT build AI logic, storage tools, conversation memory, rate limiting,
or the delete confirmation system. All of that is inside Nimbus. Your bridge
is a thin, focused translation layer.

Why does it need to be a separate service? Because Slack needs a public HTTPS
URL to send events to. Your bridge lives on the internet; it receives raw
Slack payloads and converts them into Nimbus's clean API format.

══════════════════════════════════════════════════════════════════════════════
PHASE 1: SET UP YOUR DEVELOPMENT ENVIRONMENT
══════════════════════════════════════════════════════════════════════════════

Step 1.1 — Install uv (the Python package manager used by this project)

  macOS/Linux:
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source ~/.bashrc        # or: source ~/.zshrc  (on macOS with zsh)

  Verify it worked:
    uv --version
    # should print something like: uv 0.5.x

  Why uv instead of pip? uv creates isolated virtual environments automatically,
  locks dependencies exactly, and installs packages much faster than pip. It is
  what this project and Team 2's repo both use.

Step 1.2 — Install ngrok (needed to expose your local server to Slack)

  ngrok is a tool that creates a temporary public HTTPS URL pointing at your
  laptop. Slack needs a real HTTPS URL to send events to, so without ngrok you
  cannot test with real Slack events locally.

  macOS (with Homebrew):
    brew install ngrok/ngrok/ngrok

  Linux:
    curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc \
      | sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null
    echo "deb https://ngrok-agent.s3.amazonaws.com buster main" \
      | sudo tee /etc/apt/sources.list.d/ngrok.list
    sudo apt update && sudo apt install ngrok

  Then sign up for a free account at https://ngrok.com and run:
    ngrok config add-authtoken <your-token-from-ngrok-dashboard>

  Verify:
    ngrok --version

Step 1.3 — Create the project

  cd ~                      # or wherever you keep your projects
  mkdir nimbus-slack-bridge
  cd nimbus-slack-bridge
  uv init --python 3.12
  rm hello.py               # uv init creates a sample file we don't need
  git init
  git add .
  git commit -m "chore: initial project scaffold"

Step 1.4 — Add dependencies

  uv add fastapi "uvicorn[standard]" httpx "slack-bolt>=1.21" \
         python-dotenv structlog

  uv add --dev pytest pytest-cov pytest-mock ruff mypy

Step 1.5 — Create the directory structure

  mkdir -p src/nimbus_slack_bridge tests .circleci scripts
  touch src/__init__.py
  touch src/nimbus_slack_bridge/__init__.py
  touch tests/__init__.py

  The project should now look like this:
    nimbus-slack-bridge/
    ├── .circleci/
    │   └── config.yml
    ├── src/
    │   └── nimbus_slack_bridge/
    │       ├── __init__.py
    │       ├── signing.py
    │       ├── body.py
    │       ├── client.py
    │       ├── slack_verify.py
    │       └── app.py
    ├── tests/
    │   ├── __init__.py
    │   ├── test_signing.py
    │   ├── test_body.py
    │   └── test_client.py
    ├── scripts/
    │   └── smoke_test.py
    ├── AGENTS.md
    ├── pyproject.toml
    ├── .env                 ← your secrets (git-ignored)
    ├── .env.example         ← template to commit
    ├── .gitignore
    └── main.py

Step 1.6 — Create a .gitignore

  Create .gitignore with this content:
  ---- .gitignore ----
  .env
  __pycache__/
  *.pyc
  .venv/
  .mypy_cache/
  .pytest_cache/
  .ruff_cache/
  dist/
  coverage/
  .coverage*
  ---- end .gitignore ----

══════════════════════════════════════════════════════════════════════════════
PHASE 2: CREATE YOUR SLACK APP
══════════════════════════════════════════════════════════════════════════════

This phase is all browser clicks. You will create a Slack app, configure it,
and collect four credentials you need to run your bridge.

You will need a Slack workspace to test in. If your team does not have one,
go to https://slack.com/create and create a free workspace.

Step 2.1 — Create the app

  1. Go to https://api.slack.com/apps in your browser.
  2. Click the green "Create New App" button (top right).
  3. A modal appears. Click "From scratch".
  4. Fill in:
       App Name:        Nimbus
       Pick a workspace: choose your team's Slack workspace
  5. Click "Create App".
  6. You are now on the app's settings page. Keep this tab open — you will
     come back to it many times.

Step 2.2 — Get your Signing Secret (first credential)

  The Signing Secret lets your bridge verify that events came from Slack and
  not from a random attacker. Slack signs every event with HMAC-SHA256 using
  this secret.

  On the app settings page:
  1. In the left sidebar, click "Basic Information".
  2. Scroll down to the section "App Credentials".
  3. Find "Signing Secret". Click "Show".
  4. Copy the value. This is SLACK_SIGNING_SECRET.
     Save it somewhere — you will need it in Step 2.7.

Step 2.3 — Configure OAuth scopes (what your bot is allowed to do)

  Scopes are permissions. You must declare which actions your bot needs
  before Slack will let it do anything.

  1. In the left sidebar, click "OAuth & Permissions".
  2. Scroll down to "Scopes".
  3. Under "Bot Token Scopes", click "Add an OAuth Scope" and add EACH of:
       chat:write         — lets the bot post messages
       app_mentions:read  — lets the bot receive @mention events
       channels:history   — lets the bot read messages in public channels
       im:history         — lets the bot read direct messages
       im:write           — lets the bot open DMs
       files:read         — lets the bot see file metadata

  Add them one at a time. After each one, it appears in the list.

Step 2.4 — Install the app to your workspace

  1. Scroll back up on the "OAuth & Permissions" page.
  2. Click the big "Install to Workspace" button.
  3. Slack shows you a permission consent screen. Click "Allow".
  4. You are redirected back to the app settings.
  5. You now see a "Bot User OAuth Token" that starts with "xoxb-".
     Copy it. This is SLACK_BOT_TOKEN.

Step 2.5 — Get your Bot User ID (third credential)

  The Bot User ID lets your bridge ignore messages that your own bot sent
  (otherwise the bot would reply to itself forever).

  1. In the left sidebar, click "App Home".
  2. Scroll down. You will see "Your App's Presence in Slack".
  3. If you see a button "Review Scopes to Add" — click it and add
     "users:read" scope, then reinstall the app.
  4. Under "Show Tabs", enable "Messages Tab" if you want DMs to work.
  5. To find your Bot User ID, use the Slack API test tool:
     - Go to https://api.slack.com/methods/auth.test/test
     - In the token field, paste your xoxb- bot token
     - Click "Test Method"
     - In the JSON response, find "user_id" — that is SLACK_BOT_USER_ID.
     It looks like "U0123456789".

Step 2.6 — Enable Event Subscriptions

  Event Subscriptions is how Slack sends your bridge a message every time
  something happens in the workspace (someone mentions the bot, etc.).

  IMPORTANT: Slack will immediately try to verify your URL when you save it.
  Your server must be running and reachable at that moment. We will set up
  the URL in Step 3.5 after your server is running.

  For now, just enable the feature:
  1. In the left sidebar, click "Event Subscriptions".
  2. Toggle "Enable Events" to ON.
  3. Leave the Request URL blank for now.
  4. Scroll down to "Subscribe to bot events".
  5. Click "Add Bot User Event" and add each of:
       app_mention        — when someone @mentions your bot
       message.channels   — messages in public channels your bot is in
       message.im         — direct messages sent to your bot
  6. Click "Save Changes" (even with no URL set, this saves the event list).

Step 2.7 — Create a Slash Command

  A slash command lets users type "/nimbus list reports/" to talk to the bot.

  1. In the left sidebar, click "Slash Commands".
  2. Click "Create New Command".
  3. Fill in:
       Command:          /nimbus
       Request URL:      leave blank for now (we will fill in Step 3.5)
       Short Description: Ask Nimbus about your cloud storage
       Usage Hint:       list reports/ | delete old.csv | recent
  4. Click "Save".

Step 2.8 — Write down all four credentials

  You should now have:
    SLACK_SIGNING_SECRET  = (from Step 2.2)
    SLACK_BOT_TOKEN       = xoxb-... (from Step 2.4)
    SLACK_BOT_USER_ID     = U... (from Step 2.5)
    AI_SERVER_SIGNING_SECRET = (ask Team 2 for this — or generate one
                                locally with:
                                python -c "import secrets; print(secrets.token_hex(32))")

  The bridge should not ask Team 2 for `OPENROUTER_API_KEY` or
  `AI_SERVER_API_KEY`. Those are Nimbus-side secrets, not bridge-side inputs.

  You also have:
    AI_SERVER_BASE_URL    = https://nimbus-production.onrender.com

══════════════════════════════════════════════════════════════════════════════
PHASE 3: WRITE ALL THE CODE
══════════════════════════════════════════════════════════════════════════════

Now write the source files. Create each file exactly as shown.

Step 3.1 — Replace pyproject.toml

  Replace the entire contents of pyproject.toml with:

  ---- pyproject.toml ----
  [project]
  name = "nimbus-slack-bridge"
  version = "0.1.0"
  description = "Slack bridge for the Nimbus AI cloud-storage assistant"
  requires-python = ">=3.12"
  dependencies = [
      "fastapi>=0.115.0",
      "uvicorn[standard]>=0.34.0",
      "httpx>=0.27.0",
      "slack-bolt>=1.21.0",
      "python-dotenv>=1.0.0",
      "structlog>=25.0.0",
  ]

  [tool.ruff]
  src = ["src"]

  [tool.ruff.lint]
  select = ["ALL"]
  ignore = ["S101", "INP001", "COM812", "D203", "D213"]

  [tool.ruff.lint.per-file-ignores]
  "tests/**" = ["S101", "S105", "PLR2004", "D101", "D102", "D107",
                "ANN", "TC002", "TC003", "PT011", "ARG002"]
  "scripts/**" = ["T201", "D103", "ANN"]

  [tool.mypy]
  strict = true
  ignore_missing_imports = true

  [tool.pytest.ini_options]
  testpaths = ["tests"]
  addopts = ["--import-mode=importlib"]
  markers = [
      "unit: fast isolated tests with no real I/O",
      "integration: tests against a running local Nimbus instance",
      "e2e: full end-to-end against real Slack and deployed Nimbus",
  ]

  [tool.coverage.run]
  source = ["src"]
  omit = ["*/tests/*"]

  [tool.coverage.report]
  fail_under = 80
  ---- end pyproject.toml ----

Step 3.2 — Create .env.example and .env

  Create .env.example (this is committed to git — NO real secrets):
  ---- .env.example ----
  # Copy this file to .env and fill in your values.
  # NEVER commit .env to git.

  AI_SERVER_BASE_URL=https://nimbus-production.onrender.com
  AI_SERVER_SIGNING_SECRET=ask-team-2-for-this

  SLACK_SIGNING_SECRET=your-slack-app-signing-secret
  SLACK_BOT_TOKEN=xoxb-your-bot-token
  SLACK_BOT_USER_ID=U0123456789
  ---- end .env.example ----

  Now create .env (this is NOT committed — already in .gitignore):
    cp .env.example .env
  Open .env in any text editor and fill in your real values from Phase 2.

Step 3.3 — Create AGENTS.md

  Create AGENTS.md at the project root:

  ---- AGENTS.md ----
  # Agent Development Guide

  Repository-wide guidance for coding agents and contributors.

  ---

  ## What This Repo Is

  The Nimbus Slack Bridge receives Slack events (mentions, messages, DMs, slash
  commands), translates them into the Nimbus AI Service request format, signs
  them, and POSTs to POST /ai/chat/turn on the deployed Nimbus AI Service at
  https://nimbus-production.onrender.com. It then posts the returned text back to the
  correct Slack thread.

  The bridge does NOT implement AI, storage tools, conversation memory, rate
  limiting, or confirmation logic. Those all live inside Nimbus.

  Key modules:
    src/nimbus_slack_bridge/signing.py      HMAC-SHA256 signing
    src/nimbus_slack_bridge/body.py         Slack event → Nimbus request body
    src/nimbus_slack_bridge/client.py       calls POST /ai/chat/turn
    src/nimbus_slack_bridge/slack_verify.py verifies Slack request signatures
    src/nimbus_slack_bridge/app.py          FastAPI handlers

  ---

  ## Highest-Priority Rules

  - Read the code before editing. Do not guess from names alone.
  - Prefer the smallest correct change.
  - Keep the bridge thin: translate and forward, do not accumulate logic.
  - Run ruff and mypy after every change.
  - Never commit .env, credentials, or secrets.
  - Add tests for any behavior change.

  ---

  ## The Nimbus Contract

  Endpoint:  POST /ai/chat/turn
  Base URL:  https://nimbus-production.onrender.com

  Signing headers required on every request:
    Content-Type:        application/json
    X-Nimbus-Timestamp:  str(int(time.time()))
    X-Nimbus-Nonce:      unique string per request (e.g. uuid4().hex)
    X-Nimbus-Signature:  hmac_sha256(AI_SERVER_SIGNING_SECRET, canonical).hexdigest()

  Canonical string:
    "POST\n/ai/chat/turn\n{timestamp}\n{nonce}\n{sha256(body_bytes).hexdigest()}"

  Required request fields:
    platform         "slack"
    workspace_id     Slack team_id
    channel_id       event.channel
    thread_id        event.thread_ts if present, else event.ts (null for /commands)
    message_id       event.ts  (or "cmd:{trigger_id}" for slash commands)
    user_id          event.user
    text             message text with leading <@BOT> mention stripped
    idempotency_key  "slack:{team_id}:event:{event_id}"

  Response fields:
    outcome    "reply" | "confirmation_required" | "partial_success" | "error"
    text       always post this to Slack; never parse it for logic
    conversation_id  "platform:workspace_id:channel_id:(thread_id or message_id)"

  Thread identity rule (critical):
    Set thread_id = event.thread_ts if it exists, else event.ts.
    Every message in the same Slack thread must use the same thread_id so
    Nimbus keeps one persistent conversation per thread.

  ---

  ## Dev Commands

  uv sync                          install dependencies
  uv run pytest -m unit -v         run unit tests
  uv run pytest --cov              run all tests with coverage
  uv run ruff check .              lint
  uv run ruff format .             format
  uv run mypy --strict src/        type check
  uv run uvicorn src.nimbus_slack_bridge.app:app --reload --port 8080
                                   start local dev server
  uv run python scripts/smoke_test.py
                                   smoke test against deployed Nimbus

  ---

  ## Testing Expectations

  - Mock httpx.post in unit tests (no real HTTP calls).
  - Mock Slack SDK calls in unit tests.
  - Cover: signing algorithm, body builders, all four Nimbus outcomes,
    Slack signature verification (valid / invalid / stale), event dedup.
  - Minimum 80% coverage enforced.

  ---

  ## Code Standards

  - ruff ALL ruleset + mypy --strict.
  - Every function and class must have a docstring.
  - Comments explain why, not what.
  - No mutable default arguments.
  - Use raise ... from exc when re-raising.
  - No broad except Exception without a specific reason.
  - Read env vars at call time with a clear error if missing.
  ---- end AGENTS.md ----

Step 3.4 — Create src/nimbus_slack_bridge/signing.py

  ---- signing.py ----
  """HMAC-SHA256 signing helpers for the Nimbus wrapper-facing endpoint.

  The Nimbus AI Service requires every POST /ai/chat/turn request to include
  three signed headers. This module builds those headers from the raw body
  bytes and the shared signing secret.

  Canonical string format (must match exactly what the server verifies):
    METHOD + "\\n" + PATH + "\\n" + TIMESTAMP + "\\n" + NONCE + "\\n" + SHA256(BODY)
  """

  from __future__ import annotations

  import hashlib
  import hmac
  import json
  import time
  import uuid


  def encode_body(body: dict[str, object]) -> bytes:
      """Serialize the request body to compact UTF-8 JSON bytes.

      Uses compact separators so the byte sequence is identical on every call
      for the same input — required for signing to be stable.
      """
      return json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


  def sign_request(
      body_bytes: bytes,
      *,
      secret: str,
      path: str = "/ai/chat/turn",
      method: str = "POST",
  ) -> dict[str, str]:
      """Return the four HTTP headers required by the Nimbus signed endpoint.

      Args:
          body_bytes: The exact UTF-8 bytes of the request body.
          secret: The shared AI_SERVER_SIGNING_SECRET value.
          path: Request path — default /ai/chat/turn.
          method: HTTP method — default POST.

      Returns:
          Dict with Content-Type, X-Nimbus-Timestamp, X-Nimbus-Nonce, and
          X-Nimbus-Signature. Merge this into your httpx headers.
      """
      timestamp = str(int(time.time()))
      nonce = f"nonce-{uuid.uuid4().hex}"
      body_digest = hashlib.sha256(body_bytes).hexdigest()
      canonical = f"{method.upper()}\n{path}\n{timestamp}\n{nonce}\n{body_digest}"
      signature = hmac.new(
          secret.encode("utf-8"),
          canonical.encode("utf-8"),
          hashlib.sha256,
      ).hexdigest()
      return {
          "Content-Type": "application/json",
          "X-Nimbus-Timestamp": timestamp,
          "X-Nimbus-Nonce": nonce,
          "X-Nimbus-Signature": signature,
      }
  ---- end signing.py ----

Step 3.5 — Create src/nimbus_slack_bridge/body.py

  ---- body.py ----
  """Translate Slack events into Nimbus ChatTurnRequest JSON bodies.

  Every call to POST /ai/chat/turn needs a specific JSON shape. These builders
  convert the raw Slack payloads your app receives into that shape.

  Thread identity rule (most important concept in this file):
    Nimbus uses thread_id to decide which conversation a message belongs to.
    Rule: set thread_id = event.thread_ts if present, else event.ts.
    This means every reply in a Slack thread maps to the same Nimbus
    conversation, so the AI remembers the full history of the thread.
  """

  from __future__ import annotations


  def _strip_mention(text: str) -> str:
      """Remove a leading Slack app-mention token from a message string.

      Slack encodes mentions as '<@U123BOT>'. When a user @mentions the bot,
      that token appears at the start of the message text. We strip it so
      Nimbus receives the clean user intent, not '<@U123BOT> list reports/'.
      """
      stripped = text.strip()
      if stripped.startswith("<@") and ">" in stripped:
          return stripped.split(">", 1)[1].strip()
      return stripped


  def build_message_event_body(
      *,
      team_id: str,
      event_id: str,
      event: dict[str, object],
  ) -> dict[str, object]:
      """Build a Nimbus request body from a Slack message or app_mention event.

      Args:
          team_id: Slack team_id from the top-level event_callback payload.
          event_id: Slack event_id from the top-level payload (for idempotency).
          event: The inner event dict from the Slack payload.

      Returns:
          A dict ready to be JSON-serialized and signed for /ai/chat/turn.
      """
      message_ts = str(event["ts"])
      thread_ts = event.get("thread_ts")
      thread_id: str = str(thread_ts) if thread_ts else message_ts
      text = _strip_mention(str(event.get("text", "")))

      body: dict[str, object] = {
          "platform": "slack",
          "workspace_id": team_id,
          "channel_id": str(event["channel"]),
          "thread_id": thread_id,
          "message_id": message_ts,
          "user_id": str(event["user"]),
          "text": text,
          "idempotency_key": f"slack:{team_id}:event:{event_id}",
          "request_id": f"req-slack-{event_id}",
      }

      # Include Slack file metadata if files are attached.
      # Nimbus can use this context even without the file bytes.
      # Files over 20 MiB are skipped — Nimbus rejects them anyway.
      raw_files = event.get("files", [])
      files: list[dict[str, object]] = [
          f for f in raw_files  # type: ignore[union-attr]
          if isinstance(f, dict)
      ]
      max_bytes = 20 * 1024 * 1024
      attachments = [
          {
              "platform_file_id": str(f["id"]),
              "filename": str(f["name"]),
              "content_type": str(f.get("mimetype", "application/octet-stream")),
              "size_bytes": int(str(f.get("size", 0))),
          }
          for f in files[:10]
          if 0 < int(str(f.get("size", 0))) <= max_bytes
      ]
      if attachments:
          body["attachments"] = attachments

      return body


  def build_slash_command_body(form: dict[str, str]) -> dict[str, object]:
      """Build a Nimbus request body from a Slack slash command form POST.

      Slash commands arrive as URL-encoded form data, not JSON. The thread_id
      is None because slash commands do not have a thread anchor. Nimbus will
      treat the command as a one-shot conversation unless a thread_id is given.

      Args:
          form: URL-decoded form fields from the Slack slash command POST body.
      """
      team_id = form["team_id"]
      trigger_id = form["trigger_id"]
      return {
          "platform": "slack",
          "workspace_id": team_id,
          "channel_id": form["channel_id"],
          "thread_id": None,
          "message_id": f"cmd:{trigger_id}",
          "user_id": form["user_id"],
          "text": form.get("text", "").strip(),
          "idempotency_key": f"slack:{team_id}:command:{trigger_id}",
          "request_id": f"req-slack-cmd-{trigger_id}",
      }
  ---- end body.py ----

Step 3.6 — Create src/nimbus_slack_bridge/client.py

  ---- client.py ----
  """HTTP client for the Nimbus AI Service wrapper-facing endpoint.

  Reads configuration from environment variables at call time. Raises a clear
  RuntimeError if required variables are missing so misconfiguration is obvious.
  """

  from __future__ import annotations

  import os

  import httpx
  import structlog

  from nimbus_slack_bridge.signing import encode_body, sign_request

  log = structlog.get_logger()

  _NIMBUS_PATH = "/ai/chat/turn"
  _TIMEOUT_SECONDS = 30.0


  def _base_url() -> str:
      """Return the Nimbus base URL from the environment."""
      url = os.environ.get("AI_SERVER_BASE_URL", "").strip().rstrip("/")
      if not url:
          msg = "AI_SERVER_BASE_URL is not set"
          raise RuntimeError(msg)
      return url


  def _signing_secret() -> str:
      """Return the Nimbus signing secret from the environment."""
      secret = os.environ.get("AI_SERVER_SIGNING_SECRET", "").strip()
      if not secret:
          msg = "AI_SERVER_SIGNING_SECRET is not set"
          raise RuntimeError(msg)
      return secret


  def call_nimbus(body: dict[str, object]) -> dict[str, object]:
      """Sign and POST one chat turn to the Nimbus AI Service.

      Reads AI_SERVER_BASE_URL and AI_SERVER_SIGNING_SECRET from the
      environment. Signs the request body with HMAC-SHA256 and sends it to
      POST /ai/chat/turn.

      Args:
          body: A valid Nimbus ChatTurnRequest dict. Build this with the
                helpers in body.py.

      Returns:
          The parsed JSON response. Always contains: outcome, text,
          conversation_id, model, steps, fallback_used, confirmation.

      Raises:
          httpx.HTTPStatusError: Nimbus returned a non-2xx status.
          RuntimeError: A required environment variable is missing.
      """
      body_bytes = encode_body(body)
      headers = sign_request(body_bytes, secret=_signing_secret())
      log.info(
          "nimbus_request_sent",
          workspace_id=body.get("workspace_id"),
          channel_id=body.get("channel_id"),
          user_id=body.get("user_id"),
          idempotency_key=body.get("idempotency_key"),
      )
      response = httpx.post(
          f"{_base_url()}{_NIMBUS_PATH}",
          content=body_bytes,
          headers=headers,
          timeout=_TIMEOUT_SECONDS,
      )
      response.raise_for_status()
      payload: dict[str, object] = response.json()
      log.info(
          "nimbus_response_received",
          outcome=payload.get("outcome"),
          conversation_id=payload.get("conversation_id"),
          model=payload.get("model"),
          steps=payload.get("steps"),
      )
      return payload
  ---- end client.py ----

Step 3.7 — Create src/nimbus_slack_bridge/slack_verify.py

  ---- slack_verify.py ----
  """Verify that incoming HTTP requests genuinely came from Slack.

  Slack signs every request it sends using HMAC-SHA256 with your app's Signing
  Secret. Without this check, anyone on the internet could POST fake events to
  your bridge and impersonate Slack. Always call verify_slack_signature before
  processing any incoming event.
  """

  from __future__ import annotations

  import hashlib
  import hmac
  import os
  import time


  def verify_slack_signature(
      *,
      body: bytes,
      timestamp: str,
      slack_signature: str,
  ) -> bool:
      """Return True only if this request provably came from Slack.

      Slack includes X-Slack-Request-Timestamp and X-Slack-Signature on every
      event. This function recomputes the expected signature from the raw body
      and compares it to what Slack sent. It also rejects requests older than
      5 minutes to prevent replay attacks (attacker records a valid request and
      replays it later).

      Args:
          body: Raw request body bytes. Do not decode before passing.
          timestamp: Value of the X-Slack-Request-Timestamp header.
          slack_signature: Value of the X-Slack-Signature header (starts with v0=).

      Returns:
          True if the signature is valid and fresh. False otherwise.
          Never raises — invalid inputs return False.
      """
      slack_secret = os.environ.get("SLACK_SIGNING_SECRET", "").strip()
      if not slack_secret:
          return False
      try:
          ts = float(timestamp)
      except (ValueError, TypeError):
          return False
      # Reject requests older than 5 minutes (prevents replay attacks)
      if abs(time.time() - ts) > 300:
          return False
      sig_base = f"v0:{timestamp}:{body.decode('utf-8')}"
      computed = "v0=" + hmac.new(
          slack_secret.encode("utf-8"),
          sig_base.encode("utf-8"),
          hashlib.sha256,
      ).hexdigest()
      # Use constant-time comparison to prevent timing attacks
      return hmac.compare_digest(computed, slack_signature)
  ---- end slack_verify.py ----

Step 3.8 — Create src/nimbus_slack_bridge/app.py

  This is the largest file. It is the FastAPI application that receives Slack
  events and forwards them to Nimbus.

  ---- app.py ----
  """FastAPI application for the Nimbus Slack Bridge.

  Exposes:
    POST /slack/events    Slack Event API callbacks (mentions, messages, DMs)
    POST /slack/commands  Slack slash command handler (/nimbus)
    GET  /health          Liveness probe, no auth

  Both POST routes verify the Slack signature before doing any work.
  Slack requires an HTTP 200 within 3 seconds of receiving an event.
  Because the Nimbus call can take up to 30 seconds (AI provider round trip),
  we ACK Slack immediately and process the Nimbus call in a background task.
  """

  from __future__ import annotations

  import asyncio
  import os
  from typing import Any

  import structlog
  from dotenv import load_dotenv
  from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status

  from nimbus_slack_bridge.body import build_message_event_body, build_slash_command_body
  from nimbus_slack_bridge.client import call_nimbus
  from nimbus_slack_bridge.slack_verify import verify_slack_signature

  load_dotenv()  # loads .env file in local dev; no effect in production

  log = structlog.get_logger()
  app = FastAPI(title="Nimbus Slack Bridge", version="0.1.0")

  # In-memory event deduplication set. Prevents processing the same Slack event
  # twice if Slack retries delivery. Fine for a single-process deployment;
  # replace with Redis if you scale to multiple replicas.
  _seen_event_ids: set[str] = set()

  _BOT_USER_ID = os.environ.get("SLACK_BOT_USER_ID", "")


  def _get_bot_token() -> str:
      """Return the Slack bot token from the environment."""
      token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
      if not token:
          msg = "SLACK_BOT_TOKEN is not set"
          raise RuntimeError(msg)
      return token


  def _should_ignore(event: dict[str, Any]) -> bool:
      """Return True if this Slack event should be silently dropped.

      We ignore:
        - Messages from any bot (bot_id is set)
        - Message subtypes (edits, deletes, channel joins, etc.)
        - Messages from our own bot user (avoid infinite reply loops)
        - Events with no text (nothing to send to Nimbus)
      """
      if event.get("bot_id"):
          return True
      if event.get("subtype"):
          return True
      if _BOT_USER_ID and event.get("user") == _BOT_USER_ID:
          return True
      if not str(event.get("text", "")).strip():
          return True
      return False


  def _post_to_slack(
      *,
      channel_id: str,
      thread_ts: str,
      text: str,
  ) -> None:
      """Post a reply into a Slack thread using the Slack Web API.

      Uses the requests-based Slack WebClient. Called from background tasks
      so it is allowed to be synchronous.
      """
      from slack_sdk import WebClient  # noqa: PLC0415

      client = WebClient(token=_get_bot_token())
      client.chat_postMessage(
          channel=channel_id,
          thread_ts=thread_ts,
          text=text,
      )


  async def _handle_nimbus_turn(
      *,
      team_id: str,
      event_id: str,
      event: dict[str, Any],
  ) -> None:
      """Build, sign, send one Nimbus turn, and post the reply to Slack.

      This runs in a background task so the main handler can ACK Slack
      immediately without waiting for the AI response.
      """
      body = build_message_event_body(
          team_id=team_id,
          event_id=event_id,
          event=event,
      )
      channel_id = str(event["channel"])
      thread_ts = str(body["thread_id"])

      try:
          payload = await asyncio.to_thread(call_nimbus, body)
      except Exception:
          log.exception(
              "nimbus_call_failed",
              idempotency_key=body.get("idempotency_key"),
          )
          await asyncio.to_thread(
              _post_to_slack,
              channel_id=channel_id,
              thread_ts=thread_ts,
              text="Sorry, I could not reach Nimbus right now. Please try again.",
          )
          return

      reply_text = str(payload.get("text", "No response from Nimbus."))
      outcome = payload.get("outcome", "reply")
      log.info("posting_to_slack", outcome=outcome, channel_id=channel_id)

      await asyncio.to_thread(
          _post_to_slack,
          channel_id=channel_id,
          thread_ts=thread_ts,
          text=reply_text,
      )


  @app.post("/slack/events")
  async def slack_events(
      request: Request,
      background_tasks: BackgroundTasks,
  ) -> dict[str, Any]:
      """Handle all Slack Event API callbacks.

      Slack sends events here when: someone @mentions the bot, someone sends a
      DM, or a message is posted in a channel the bot is in.

      Flow:
        1. Verify the Slack signature (reject fakes immediately with 401)
        2. Handle the URL verification challenge (Slack sends this once on setup)
        3. Deduplicate by event_id (Slack sometimes delivers events twice)
        4. Filter noise (bot messages, edits, etc.)
        5. ACK immediately with 200 (Slack requires this within 3 seconds)
        6. Process the Nimbus call in a background task
      """
      body = await request.body()
      timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
      slack_sig = request.headers.get("X-Slack-Signature", "")

      if not verify_slack_signature(
          body=body,
          timestamp=timestamp,
          slack_signature=slack_sig,
      ):
          raise HTTPException(
              status_code=status.HTTP_401_UNAUTHORIZED,
              detail="Invalid Slack signature.",
          )

      payload: dict[str, Any] = await request.json()

      # Slack sends a one-time challenge when you first register the URL.
      # We must echo it back to prove we control this endpoint.
      if payload.get("type") == "url_verification":
          return {"challenge": payload["challenge"]}

      event_id = str(payload.get("event_id", ""))
      if event_id in _seen_event_ids:
          log.info("duplicate_event_dropped", event_id=event_id)
          return {"ok": True}
      _seen_event_ids.add(event_id)

      event: dict[str, Any] = payload.get("event", {})
      team_id = str(payload.get("team_id", ""))

      if _should_ignore(event):
          return {"ok": True}

      # ACK Slack NOW. The Nimbus call (up to 30s) runs in the background.
      background_tasks.add_task(
          _handle_nimbus_turn,
          team_id=team_id,
          event_id=event_id,
          event=event,
      )
      return {"ok": True}


  @app.post("/slack/commands")
  async def slack_commands(request: Request) -> dict[str, Any]:
      """Handle Slack slash commands (e.g. /nimbus list reports/).

      Slash commands must respond within 3 seconds with a text response.
      We call Nimbus synchronously here because slash commands have a
      different response mechanism than events (they respond in-place, not
      via chat.postMessage).

      If the Nimbus call takes too long, we return an error message.
      """
      body = await request.body()
      timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
      slack_sig = request.headers.get("X-Slack-Signature", "")

      if not verify_slack_signature(
          body=body,
          timestamp=timestamp,
          slack_signature=slack_sig,
      ):
          raise HTTPException(
              status_code=status.HTTP_401_UNAUTHORIZED,
              detail="Invalid Slack signature.",
          )

      form_data = await request.form()
      form = {k: str(v) for k, v in form_data.items()}
      nimbus_body = build_slash_command_body(form)

      try:
          payload = await asyncio.to_thread(call_nimbus, nimbus_body)
      except Exception:
          log.exception(
              "nimbus_slash_call_failed",
              idempotency_key=nimbus_body.get("idempotency_key"),
          )
          return {
              "response_type": "ephemeral",
              "text": "Nimbus is unavailable. Try again shortly.",
          }

      return {
          "response_type": "in_channel",
          "text": str(payload.get("text", "No response from Nimbus.")),
      }


  @app.get("/health")
  async def health() -> dict[str, str]:
      """Liveness probe — no auth required. Used by CircleCI and load balancers."""
      return {"status": "ok", "service": "nimbus-slack-bridge"}
  ---- end app.py ----

Step 3.9 — Create main.py

  ---- main.py ----
  """Entry point for the Nimbus Slack Bridge server."""

  from __future__ import annotations

  import uvicorn

  if __name__ == "__main__":
      uvicorn.run(
          "src.nimbus_slack_bridge.app:app",
          host="0.0.0.0",  # noqa: S104 - intentional for deployment
          port=8080,
          reload=True,
      )
  ---- end main.py ----

Step 3.10 — Create scripts/smoke_test.py

  ---- scripts/smoke_test.py ----
  """Smoke test: send one signed request to deployed Nimbus without needing Slack.

  Run this to verify your signing code and Nimbus connectivity before wiring
  up any real Slack events:

    AI_SERVER_BASE_URL=https://nimbus-production.onrender.com \\
    AI_SERVER_SIGNING_SECRET=<your-secret> \\
    uv run python scripts/smoke_test.py
  """

  from __future__ import annotations

  import json
  import os
  import sys

  from dotenv import load_dotenv

  load_dotenv()

  from nimbus_slack_bridge.body import build_message_event_body  # noqa: E402
  from nimbus_slack_bridge.client import call_nimbus  # noqa: E402

  TEAM_ID = os.environ.get("SMOKE_WORKSPACE_ID", "T-SMOKE-TEAM")
  CHANNEL_ID = os.environ.get("SMOKE_CHANNEL_ID", "C-SMOKE-CHAN")
  USER_ID = os.environ.get("SMOKE_USER_ID", "U-SMOKE-USER")
  TEXT = os.environ.get(
      "SMOKE_TEXT",
      "Reply with exactly one sentence: 'Nimbus smoke test passed.'",
  )

  event: dict[str, object] = {
      "channel": CHANNEL_ID,
      "ts": "1713840000.999999",
      "user": USER_ID,
      "text": TEXT,
  }
  body = build_message_event_body(team_id=TEAM_ID, event_id="smoke-001", event=event)

  print("=== Request body ===")
  print(json.dumps(body, indent=2))
  print()

  try:
      payload = call_nimbus(body)
  except Exception as exc:
      print(f"ERROR calling Nimbus: {exc}", file=sys.stderr)
      sys.exit(1)

  print("=== Nimbus response ===")
  print(json.dumps(payload, indent=2))
  print()
  print(f"outcome : {payload.get('outcome')}")
  print(f"text    : {payload.get('text')}")
  print(f"model   : {payload.get('model')}")
  ---- end scripts/smoke_test.py ----

══════════════════════════════════════════════════════════════════════════════
PHASE 4: WRITE THE TESTS
══════════════════════════════════════════════════════════════════════════════

Step 4.1 — Create tests/test_signing.py

  ---- tests/test_signing.py ----
  """Unit tests for HMAC-SHA256 signing helpers."""

  from __future__ import annotations

  import hashlib
  import hmac

  import pytest

  from nimbus_slack_bridge.signing import encode_body, sign_request


  @pytest.mark.unit
  def test_encode_body_produces_compact_json() -> None:
      body = {"platform": "slack", "text": "hello world"}
      result = encode_body(body)
      assert isinstance(result, bytes)
      assert b" " not in result  # compact — no spaces after separators


  @pytest.mark.unit
  def test_encode_body_is_utf8() -> None:
      body = {"text": "caf\u00e9"}
      result = encode_body(body)
      assert isinstance(result, bytes)
      result.decode("utf-8")  # must not raise


  @pytest.mark.unit
  def test_sign_request_returns_four_headers() -> None:
      body_bytes = encode_body({"platform": "slack", "text": "hi"})
      headers = sign_request(body_bytes, secret="test-secret")
      assert set(headers.keys()) == {
          "Content-Type",
          "X-Nimbus-Timestamp",
          "X-Nimbus-Nonce",
          "X-Nimbus-Signature",
      }
      assert headers["Content-Type"] == "application/json"


  @pytest.mark.unit
  def test_sign_request_signature_matches_canonical() -> None:
      """The signature must be HMAC-SHA256 over the exact canonical string."""
      body_bytes = b'{"platform":"slack"}'
      secret = "my-test-secret-xyz"
      headers = sign_request(body_bytes, secret=secret)

      ts = headers["X-Nimbus-Timestamp"]
      nonce = headers["X-Nimbus-Nonce"]
      body_digest = hashlib.sha256(body_bytes).hexdigest()
      canonical = f"POST\n/ai/chat/turn\n{ts}\n{nonce}\n{body_digest}"
      expected = hmac.new(
          secret.encode("utf-8"),
          canonical.encode("utf-8"),
          hashlib.sha256,
      ).hexdigest()
      assert headers["X-Nimbus-Signature"] == expected


  @pytest.mark.unit
  def test_sign_request_nonces_are_unique_across_calls() -> None:
      body_bytes = b'{"platform":"slack"}'
      h1 = sign_request(body_bytes, secret="s")
      h2 = sign_request(body_bytes, secret="s")
      assert h1["X-Nimbus-Nonce"] != h2["X-Nimbus-Nonce"]


  @pytest.mark.unit
  def test_sign_request_different_secrets_produce_different_signatures() -> None:
      body_bytes = b'{"platform":"slack"}'
      h1 = sign_request(body_bytes, secret="secret-one")
      h2 = sign_request(body_bytes, secret="secret-two")
      assert h1["X-Nimbus-Signature"] != h2["X-Nimbus-Signature"]
  ---- end tests/test_signing.py ----

Step 4.2 — Create tests/test_body.py

  ---- tests/test_body.py ----
  """Unit tests for Slack event → Nimbus body builders."""

  from __future__ import annotations

  import pytest

  from nimbus_slack_bridge.body import build_message_event_body, build_slash_command_body

  TEAM = "T123TEAM"
  EV = "Ev123ABC"


  @pytest.mark.unit
  def test_top_level_mention_thread_id_equals_ts() -> None:
      """A top-level message (no thread_ts) must use event.ts as thread_id."""
      event = {"channel": "C1", "ts": "1000.0001", "user": "U1", "text": "hi"}
      body = build_message_event_body(team_id=TEAM, event_id=EV, event=event)
      assert body["thread_id"] == "1000.0001"
      assert body["message_id"] == "1000.0001"


  @pytest.mark.unit
  def test_thread_reply_uses_thread_ts() -> None:
      """A thread reply must use event.thread_ts as thread_id, event.ts as message_id."""
      event = {
          "channel": "C1",
          "ts": "1000.0002",
          "thread_ts": "1000.0001",
          "user": "U1",
          "text": "reply here",
      }
      body = build_message_event_body(team_id=TEAM, event_id=EV, event=event)
      assert body["thread_id"] == "1000.0001"  # the thread root
      assert body["message_id"] == "1000.0002"  # this specific message


  @pytest.mark.unit
  def test_mention_prefix_is_stripped() -> None:
      event = {"channel": "C1", "ts": "1.0", "user": "U1",
               "text": "<@U456BOT> list reports/"}
      body = build_message_event_body(team_id=TEAM, event_id=EV, event=event)
      assert body["text"] == "list reports/"


  @pytest.mark.unit
  def test_text_without_mention_is_unchanged() -> None:
      event = {"channel": "C1", "ts": "1.0", "user": "U1", "text": "hello nimbus"}
      body = build_message_event_body(team_id=TEAM, event_id=EV, event=event)
      assert body["text"] == "hello nimbus"


  @pytest.mark.unit
  def test_idempotency_key_is_stable_per_event_id() -> None:
      event = {"channel": "C1", "ts": "1.0", "user": "U1", "text": "hi"}
      b1 = build_message_event_body(team_id=TEAM, event_id="Ev-unique", event=event)
      b2 = build_message_event_body(team_id=TEAM, event_id="Ev-unique", event=event)
      assert b1["idempotency_key"] == b2["idempotency_key"]
      assert "Ev-unique" in str(b1["idempotency_key"])


  @pytest.mark.unit
  def test_platform_is_always_slack() -> None:
      event = {"channel": "C1", "ts": "1.0", "user": "U1", "text": "hi"}
      body = build_message_event_body(team_id=TEAM, event_id=EV, event=event)
      assert body["platform"] == "slack"


  @pytest.mark.unit
  def test_slash_command_thread_id_is_none() -> None:
      form = {"team_id": "T1", "channel_id": "C1",
              "trigger_id": "trig1", "user_id": "U1", "text": "list reports/"}
      body = build_slash_command_body(form)
      assert body["thread_id"] is None


  @pytest.mark.unit
  def test_slash_command_message_id_uses_trigger_id() -> None:
      form = {"team_id": "T1", "channel_id": "C1",
              "trigger_id": "trig-xyz", "user_id": "U1", "text": "list"}
      body = build_slash_command_body(form)
      assert body["message_id"] == "cmd:trig-xyz"
  ---- end tests/test_body.py ----

Step 4.3 — Create tests/test_client.py

  ---- tests/test_client.py ----
  """Unit tests for the Nimbus HTTP client."""

  from __future__ import annotations

  import pytest
  from pytest_mock import MockerFixture


  FAKE_REPLY = {
      "outcome": "reply",
      "text": "Found 3 files under reports/.",
      "conversation_id": "slack:T1:C1:1000.0",
      "model": "test-model",
      "steps": 1,
      "fallback_used": False,
      "confirmation": None,
      "suggested_next_actions": [],
      "request_id": "req-1",
  }

  NIMBUS_BODY: dict[str, object] = {
      "platform": "slack",
      "workspace_id": "T1",
      "channel_id": "C1",
      "thread_id": "1000.0",
      "message_id": "1000.0",
      "user_id": "U1",
      "text": "list reports/",
      "idempotency_key": "slack:T1:event:Ev1",
  }


  @pytest.fixture(autouse=True)
  def _env(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("AI_SERVER_BASE_URL", "https://nimbus.example.com")
      monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", "test-secret-xyz")


  @pytest.mark.unit
  def test_call_nimbus_returns_payload_on_success(mocker: MockerFixture) -> None:
      from nimbus_slack_bridge.client import call_nimbus

      mock_resp = mocker.MagicMock()
      mock_resp.json.return_value = FAKE_REPLY
      mock_resp.raise_for_status.return_value = None
      mock_post = mocker.patch("httpx.post", return_value=mock_resp)

      result = call_nimbus(NIMBUS_BODY)

      assert result["outcome"] == "reply"
      assert result["text"] == "Found 3 files under reports/."
      mock_post.assert_called_once()
      url = mock_post.call_args[0][0]
      assert url.endswith("/ai/chat/turn")
      headers = mock_post.call_args[1]["headers"]
      assert "X-Nimbus-Signature" in headers
      assert "X-Nimbus-Timestamp" in headers
      assert "X-Nimbus-Nonce" in headers


  @pytest.mark.unit
  def test_call_nimbus_raises_on_http_error(mocker: MockerFixture) -> None:
      import httpx
      from nimbus_slack_bridge.client import call_nimbus

      mock_resp = mocker.MagicMock()
      mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
          "401 Unauthorized",
          request=mocker.MagicMock(),
          response=mocker.MagicMock(),
      )
      mocker.patch("httpx.post", return_value=mock_resp)

      with pytest.raises(httpx.HTTPStatusError):
          call_nimbus(NIMBUS_BODY)


  @pytest.mark.unit
  def test_call_nimbus_raises_if_base_url_missing(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      from nimbus_slack_bridge.client import call_nimbus

      monkeypatch.delenv("AI_SERVER_BASE_URL", raising=False)
      with pytest.raises(RuntimeError, match="AI_SERVER_BASE_URL"):
          call_nimbus(NIMBUS_BODY)


  @pytest.mark.unit
  def test_call_nimbus_raises_if_secret_missing(
      monkeypatch: pytest.MonkeyPatch,
  ) -> None:
      from nimbus_slack_bridge.client import call_nimbus

      monkeypatch.delenv("AI_SERVER_SIGNING_SECRET", raising=False)
      with pytest.raises(RuntimeError, match="AI_SERVER_SIGNING_SECRET"):
          call_nimbus(NIMBUS_BODY)
  ---- end tests/test_client.py ----

Step 4.4 — Create tests/test_slack_verify.py

  ---- tests/test_slack_verify.py ----
  """Unit tests for Slack request signature verification."""

  from __future__ import annotations

  import hashlib
  import hmac
  import time

  import pytest

  from nimbus_slack_bridge.slack_verify import verify_slack_signature

  SECRET = "test-slack-signing-secret"
  BODY = b'{"event": "test"}'


  def _make_sig(body: bytes, timestamp: str, secret: str) -> str:
      sig_base = f"v0:{timestamp}:{body.decode('utf-8')}"
      return "v0=" + hmac.new(
          secret.encode(), sig_base.encode(), hashlib.sha256
      ).hexdigest()


  @pytest.fixture(autouse=True)
  def _set_secret(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)


  @pytest.mark.unit
  def test_valid_signature_returns_true() -> None:
      ts = str(int(time.time()))
      sig = _make_sig(BODY, ts, SECRET)
      assert verify_slack_signature(body=BODY, timestamp=ts, slack_signature=sig)


  @pytest.mark.unit
  def test_wrong_secret_returns_false() -> None:
      ts = str(int(time.time()))
      sig = _make_sig(BODY, ts, "wrong-secret")
      assert not verify_slack_signature(body=BODY, timestamp=ts, slack_signature=sig)


  @pytest.mark.unit
  def test_stale_timestamp_returns_false() -> None:
      ts = str(int(time.time()) - 400)  # 400 seconds ago — too old
      sig = _make_sig(BODY, ts, SECRET)
      assert not verify_slack_signature(body=BODY, timestamp=ts, slack_signature=sig)


  @pytest.mark.unit
  def test_invalid_timestamp_returns_false() -> None:
      sig = _make_sig(BODY, "0", SECRET)
      assert not verify_slack_signature(body=BODY, timestamp="not-a-number",
                                        slack_signature=sig)


  @pytest.mark.unit
  def test_missing_secret_env_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
      ts = str(int(time.time()))
      sig = _make_sig(BODY, ts, SECRET)
      assert not verify_slack_signature(body=BODY, timestamp=ts, slack_signature=sig)
  ---- end tests/test_slack_verify.py ----

══════════════════════════════════════════════════════════════════════════════
PHASE 5: CREATE THE CIRCLECI CONFIG
══════════════════════════════════════════════════════════════════════════════

Step 5.1 — Create .circleci/config.yml

  ---- .circleci/config.yml ----
  version: 2.1

  executors:
    python-executor:
      docker:
        - image: cimg/python:3.12.10

  commands:
    setup-uv:
      description: "Install uv package manager"
      steps:
        - run:
            name: Install uv
            command: |
              curl -LsSf https://astral.sh/uv/install.sh | sh
              echo 'export PATH="$HOME/.cargo/bin:$PATH"' >> $BASH_ENV
              source $BASH_ENV

    restore-uv-cache:
      description: "Restore cached uv artifacts"
      steps:
        - restore_cache:
            keys:
              - uv-cache-v1-{{ arch }}-{{ checksum "uv.lock" }}
              - uv-cache-v1-{{ arch }}-

    save-uv-cache:
      description: "Save cached uv artifacts"
      steps:
        - save_cache:
            key: uv-cache-v1-{{ arch }}-{{ checksum "uv.lock" }}
            paths:
              - ~/.cache/uv

    restore-tool-caches:
      description: "Restore mypy, ruff, and pytest caches"
      steps:
        - restore_cache:
            keys:
              - tool-cache-v1-{{ arch }}-{{ .Branch }}-{{ checksum "pyproject.toml" }}
              - tool-cache-v1-{{ arch }}-{{ .Branch }}-
              - tool-cache-v1-{{ arch }}-

    save-tool-caches:
      description: "Save mypy, ruff, and pytest caches"
      steps:
        - run:
            name: Ensure tool cache directories exist
            command: mkdir -p .mypy_cache .pytest_cache .ruff_cache
        - save_cache:
            key: tool-cache-v1-{{ arch }}-{{ .Branch }}-{{ checksum "pyproject.toml" }}
            paths:
              - .mypy_cache
              - .pytest_cache
              - .ruff_cache

    install-dependencies:
      description: "Install all dependencies with uv (frozen lockfile)"
      steps:
        - run:
            name: Sync dependencies
            command: uv sync --frozen

  jobs:
    lint:
      executor: python-executor
      steps:
        - checkout
        - setup-uv
        - restore-uv-cache
        - restore-tool-caches
        - install-dependencies
        - save-uv-cache
        - run:
            name: Ruff — lint
            command: uv run ruff check .
        - run:
            name: Ruff — format check
            command: uv run ruff format --check .
        - run:
            name: Mypy — strict type checking
            command: uv run mypy --strict src/
        - save-tool-caches

    unit-tests:
      executor: python-executor
      steps:
        - checkout
        - setup-uv
        - restore-uv-cache
        - restore-tool-caches
        - install-dependencies
        - save-uv-cache
        - run:
            name: Run unit tests with coverage
            command: |
              mkdir -p test-results/unit coverage-data
              uv run coverage run --data-file=coverage-data/.coverage.unit \
                -m pytest tests/ -m unit \
                --junitxml=test-results/unit/results.xml -q
        - store_test_results:
            path: test-results/unit
        - save-tool-caches
        - persist_to_workspace:
            root: .
            paths:
              - coverage-data

    coverage-gate:
      executor: python-executor
      steps:
        - checkout
        - setup-uv
        - restore-uv-cache
        - install-dependencies
        - save-uv-cache
        - attach_workspace:
            at: .
        - run:
            name: Combine coverage and enforce 80% threshold
            command: |
              mkdir -p coverage
              uv run coverage combine --data-file=.coverage coverage-data/.coverage.*
              uv run coverage report
              uv run coverage xml --data-file=.coverage -o coverage/coverage.xml
        - store_artifacts:
            path: coverage

    ai-e2e-tests:
      executor: python-executor
      steps:
        - checkout
        - setup-uv
        - restore-uv-cache
        - install-dependencies
        - save-uv-cache
        - run:
            name: Health check — Nimbus liveness probe
            command: |
              curl --fail --silent "${AI_SERVER_BASE_URL%/}/health" \
                | grep -q '"status":"ok"'
        - run:
            name: Smoke test — signed request to deployed Nimbus
            command: |
              uv run python scripts/smoke_test.py
            environment:
              SMOKE_WORKSPACE_ID: TCIRCLECI
              SMOKE_CHANNEL_ID: CCIRCLECI
              SMOKE_USER_ID: UCIRCLECI
              SMOKE_TEXT: "Reply with one sentence confirming CI smoke test received."

  workflows:
    main:
      jobs:
        - lint
        - unit-tests
        - coverage-gate:
            requires:
              - unit-tests
        - ai-e2e-tests:
            context: nimbus-bridge
            requires:
              - coverage-gate
            filters:
              branches:
                only: main
  ---- end .circleci/config.yml ----

Step 5.2 — Set up the CircleCI context

  A CircleCI "context" is a named set of environment variables that are
  injected into CI jobs. The ai-e2e-tests job above uses a context named
  "nimbus-bridge". Here is how to create it:

  1. Go to https://app.circleci.com
  2. Click your organization in the left sidebar.
  3. Go to "Organization Settings" → "Contexts".
  4. Click "Create Context". Name it: nimbus-bridge
  5. Inside the context, click "Add Environment Variable" for each of:
       AI_SERVER_BASE_URL        = https://nimbus-production.onrender.com
       AI_SERVER_SIGNING_SECRET  = <value from Team 2>

  That is all. Do not add `AI_SERVER_API_KEY` or `OPENROUTER_API_KEY` here —
  the bridge repo should talk only to the signed wrapper route. The lint and
  unit-tests jobs do not need any secrets — they run without the context.

  To connect your repo to CircleCI:
  1. Go to https://app.circleci.com → "Projects".
  2. Click "Set Up Project" next to your repo.
  3. CircleCI detects .circleci/config.yml automatically.
  4. Every push to any branch triggers lint + unit-tests + coverage-gate.
  5. Pushes to the main branch also trigger ai-e2e-tests.

══════════════════════════════════════════════════════════════════════════════
PHASE 6: RUN YOUR TESTS LOCALLY
══════════════════════════════════════════════════════════════════════════════

Do this before touching Slack. Verify your code works in isolation first.

Step 6.1 — Install dependencies

  uv sync

Step 6.2 — Run the unit tests

  uv run pytest -m unit -v

  Expected output: all tests pass. No network requests are made.
  If any test fails, read the error message carefully. The most common
  issue at this stage is a missing import — check that all files are in
  the right place.

Step 6.3 — Run lint and type check

  uv run ruff check .
  uv run ruff format --check .
  uv run mypy --strict src/

  Fix any errors before moving on. ruff and mypy failures will block CI.

Step 6.4 — Run the smoke test against deployed Nimbus

  Load your .env file and run:

  source .env    # loads the env vars into your shell
  uv run python scripts/smoke_test.py

  You should see output like:
    === Request body ===
    {
      "platform": "slack",
      "workspace_id": "T-SMOKE-TEAM",
      ...
    }

    === Nimbus response ===
    {
      "outcome": "reply",
      "text": "Nimbus smoke test passed.",
      ...
    }

  If you get HTTP 401: your AI_SERVER_SIGNING_SECRET does not match what
  is configured on the Nimbus server. Contact Team 2 to verify the value.

  If you get "connection refused": AI_SERVER_BASE_URL is wrong or the
  Nimbus server is down. Try: curl https://nimbus-production.onrender.com/health

══════════════════════════════════════════════════════════════════════════════
PHASE 7: CONNECT TO REAL SLACK (local testing with ngrok)
══════════════════════════════════════════════════════════════════════════════

Now you will connect everything together and get a real Slack reply.

Step 7.1 — Start your local bridge server

  Open terminal window 1:
    source .env
    uv run uvicorn src.nimbus_slack_bridge.app:app --reload --port 8080

  You should see:
    INFO:     Started server process
    INFO:     Uvicorn running on http://0.0.0.0:8080

Step 7.2 — Start ngrok in a second terminal window

  Open terminal window 2:
    ngrok http 8080

  You will see output like:
    Forwarding  https://abc123.ngrok-free.app -> http://localhost:8080

  Copy the https://...ngrok-free.app URL. This is your temporary public URL.
  Keep ngrok running. It stops working if you close this terminal.

Step 7.3 — Register your Event URL with Slack

  1. Go back to https://api.slack.com/apps → your Nimbus app.
  2. Click "Event Subscriptions" in the left sidebar.
  3. In "Request URL", paste:
       https://abc123.ngrok-free.app/slack/events
     (use your real ngrok URL)
  4. Slack immediately sends a challenge request to verify you control it.
     Your running server handles it automatically (the url_verification code
     in app.py returns the challenge). You should see "Verified ✓" appear.
  5. If Slack says "Your URL didn't respond with the value of the
     challenge parameter" — check that your server is running and ngrok
     is forwarding correctly. Try:
       curl https://abc123.ngrok-free.app/health
     You should get: {"status":"ok","service":"nimbus-slack-bridge"}
  6. Click "Save Changes".

Step 7.4 — Register your Slash Command URL with Slack

  1. Click "Slash Commands" in the left sidebar.
  2. Click the edit pencil on your /nimbus command.
  3. Set Request URL to:
       https://abc123.ngrok-free.app/slack/commands
  4. Click "Save".

Step 7.5 — Add the bot to a channel

  1. In your Slack workspace, go to any channel.
  2. Type: /invite @Nimbus
  3. The bot appears in the channel member list.

Step 7.6 — Send your first message

  In the channel, type:
    @Nimbus What files are under reports/?

  Within a few seconds (AI round trip), Nimbus should reply in the thread
  with a list of files. You should see the reply in Slack.

  Check your terminal windows:
  - Terminal 1 (server): logs showing the request was received and processed
  - Terminal 2 (ngrok): the HTTP 200 from your server to Slack

Step 7.7 — Test the confirmation flow

  Type in Slack:
    @Nimbus delete reports/2024/old.csv

  Nimbus replies with something like:
    "I can delete `reports/2024/old.csv`, but this is destructive.
     Reply with `yes, delete reports/2024/old.csv` if you want to proceed."

  Then reply in the same thread:
    yes, delete reports/2024/old.csv

  Nimbus performs the delete and confirms it.

Step 7.8 — Test a slash command

  Type in Slack:
    /nimbus list reports/

  Nimbus responds in the channel with the file list.

══════════════════════════════════════════════════════════════════════════════
PHASE 8: THE FOUR OUTCOMES — UNDERSTANDING NIMBUS RESPONSES
══════════════════════════════════════════════════════════════════════════════

Every response from Nimbus has an "outcome" field. Never parse the "text"
field to decide what to do. Always read "outcome".

OUTCOME: "reply"
  Normal case. The AI answered the question.
  What to do: post text back to Slack in the same thread. Done.

  Example response:
  {
    "outcome": "reply",
    "text": "I found 4 files under `reports/2026/`: april.csv, may.csv, ...",
    "model": "openai/gpt-4o-mini:free",
    "steps": 1,
    "fallback_used": false,
    "confirmation": null
  }

OUTCOME: "confirmation_required"
  User asked to delete a file. Nimbus is waiting for explicit confirmation
  before doing anything destructive.

  What to do: post text back to Slack. The user's next message in the same
  thread is the confirmation. Your bridge sends it as another normal turn.
  Nimbus tracks the pending state internally — you just forward the messages.

  Example response:
  {
    "outcome": "confirmation_required",
    "confirmation_required": true,
    "text": "I can delete `reports/2024/old.csv`, but this is destructive.
             Reply with `yes, delete reports/2024/old.csv` to proceed.",
    "confirmation": {
      "action_id": "act-abc123",
      "kind": "delete_file",
      "expected_reply": "yes, delete reports/2024/old.csv",
      "expires_at": "2026-04-21T21:00:00+00:00"
    },
    "model": "nimbus-runtime",
    "steps": 0
  }

  Rules Nimbus enforces (you do NOT need to re-implement these):
    - Only the same user who requested the delete can confirm it.
    - The reply must match expected_reply exactly.
    - The pending action expires after ~15 minutes.
    - A wrong user or wrong text gets outcome="error".

OUTCOME: "partial_success"
  Some attachments were uploaded, others failed.
  What to do: post text as an informational message. Not a fatal error.

OUTCOME: "error"
  Nimbus could not complete the action.
  What to do: post text as a user-safe error message. It is already
  human-readable. Do not show stack traces or internal error codes.

In app.py, all four outcomes use the same code path because we always just
post payload["text"] back to Slack. If you want richer Slack UX (e.g. a
confirmation button instead of asking the user to type the exact string),
you would branch on outcome == "confirmation_required" and use Slack's
Block Kit to render a button — but that is optional and not required for HW3.

══════════════════════════════════════════════════════════════════════════════
PHASE 9: PUSH TO GITHUB AND VERIFY CIRCLECI
══════════════════════════════════════════════════════════════════════════════

Step 9.1 — Create a GitHub repository

  1. Go to https://github.com/new
  2. Repository name: nimbus-slack-bridge
  3. Visibility: Private (or Public if your team prefers)
  4. Do NOT add README, .gitignore, or license (you already have them)
  5. Click "Create repository"
  6. Copy the SSH URL (e.g. git@github.com:yourname/nimbus-slack-bridge.git)

Step 9.2 — Push your code

  git add .
  git commit -m "feat: initial Nimbus Slack Bridge implementation"
  git remote add origin git@github.com:yourname/nimbus-slack-bridge.git
  git push -u origin main

Step 9.3 — Connect CircleCI

  1. Go to https://app.circleci.com → "Projects"
  2. Find nimbus-slack-bridge and click "Set Up Project"
  3. Choose "Use Existing Config" (you already have .circleci/config.yml)
  4. Your first pipeline starts automatically
  5. Watch: lint → unit-tests → coverage-gate should all pass
  6. ai-e2e-tests only runs on the main branch and needs the context

Step 9.4 — Verify CircleCI passes

  Go to your CircleCI project page. All three jobs (lint, unit-tests,
  coverage-gate) should show green checkmarks. If any fail, click the
  failing job to see the error output.

══════════════════════════════════════════════════════════════════════════════
PHASE 10: HTTP ERRORS FROM NIMBUS — WHAT THEY MEAN
══════════════════════════════════════════════════════════════════════════════

If Nimbus returns a non-200 status code:

  401 — Bad signature, stale timestamp (>5 min), or replayed nonce.
        Check: is AI_SERVER_SIGNING_SECRET set correctly on both sides?
        Check: is your system clock accurate? (ntp sync issue)
        Check: are you reusing nonces? (each request needs a fresh nonce)

  422 — Request body failed validation.
        Check: is text non-empty? Nimbus rejects empty text.
        Check: do workspace_id/channel_id/user_id only use [A-Za-z0-9_.:-]?
        Check: is platform exactly "slack"?

  429 — Rate limited. Nimbus limits per platform:workspace_id:user_id.
        Back off and retry with the SAME idempotency_key.
        The same idempotency_key returns the cached response, so no duplicate AI call.

  502 — OpenRouter (the AI provider) returned an error or the Nimbus server
        could not establish a provider connection.
        Retry with exponential backoff. Not your fault.

  503 — Nimbus is misconfigured (missing env var on the Nimbus server).
        Contact Team 2.

  504 — AI provider timed out. Retry with backoff.

══════════════════════════════════════════════════════════════════════════════
PHASE 11: DEFINITION OF DONE
══════════════════════════════════════════════════════════════════════════════

The Nimbus Slack Bridge is done when ALL of these are true:

  Code quality:
  [ ] uv run pytest -m unit passes with ≥80% coverage
  [ ] uv run ruff check . returns no errors
  [ ] uv run ruff format --check . returns no errors
  [ ] uv run mypy --strict src/ returns no errors

  Nimbus integration:
  [ ] uv run python scripts/smoke_test.py returns outcome="reply" from
      the deployed Nimbus service (https://nimbus-production.onrender.com)

  Slack integration:
  [ ] @mentioning the bot in a channel produces a reply in the same thread
  [ ] A thread reply continues the same Nimbus conversation (Nimbus remembers
      what was said earlier in the thread)
  [ ] "delete <path>" → Nimbus returns confirmation_required → user confirms
      → Nimbus deletes the file and replies with outcome="reply"
  [ ] A different user trying to confirm gets outcome="error"
  [ ] Sending the same Slack event_id twice does NOT produce a duplicate reply

  CI/CD:
  [ ] CircleCI passes lint + unit-tests + coverage-gate on every push
  [ ] CircleCI ai-e2e-tests passes on the main branch

══════════════════════════════════════════════════════════════════════════════
END OF GUIDE.

Now let's start. Ask the student:
"Ready to build? First things first — are you on macOS or Linux?
Run this in your terminal and tell me what it prints:
  python3 --version
  uv --version   (if uv is already installed)
  ngrok --version  (if ngrok is already installed)
Tell me which ones you already have and we will install the missing ones."
══════════════════════════════════════════════════════════════════════════════
````

---

## Tutorial Reference

Quick-lookup reference for team members who have already built the bridge.

(thread-identity)=
### Thread Identity

```text
conversation_id = platform:workspace_id:channel_id:(thread_id or message_id)
```

| Slack event type | `thread_id` | `message_id` |
|---|---|---|
| Top-level @mention or message | `event.ts` | `event.ts` |
| Thread reply | `event.thread_ts` | `event.ts` |
| Direct message | `event.ts` | `event.ts` |
| Slash command | `null` | `"cmd:{trigger_id}"` |

(how-signing-works)=
### Signing

```text
canonical  = "POST\n/ai/chat/turn\n{timestamp}\n{nonce}\n{sha256(body_bytes)}"
signature  = hmac_sha256(AI_SERVER_SIGNING_SECRET, canonical).hexdigest()
headers    = X-Nimbus-Timestamp, X-Nimbus-Nonce, X-Nimbus-Signature
```

Timestamp must be within ±300 seconds of server time. Each nonce is
single-use — Nimbus rejects replays.

(idempotency)=
### Idempotency

```text
Message events:  "slack:{team_id}:event:{event_id}"
Slash commands:  "slack:{team_id}:command:{trigger_id}"
```

Nimbus caches responses for one hour per key. Retrying with the same key
returns the cached response without calling the AI again.

(attachments)=
### Attachments

Include up to 10 file metadata objects per turn (max 20 MiB each). Omit
`content_base64` for metadata-only turns. Include it with `sha256_hex` only
for runtime-managed uploads.

(nimbus-slack-bridge-from-scratch)=
### Key Files in the Nimbus Repo

- [`src/ai_server/ai_server/router.py`](https://github.com/2SpaceMasterRace/ospsd-team-2/blob/hw-3/src/ai_server/ai_server/router.py) — all request/response models
- [`src/ai_server/ai_server/auth.py`](https://github.com/2SpaceMasterRace/ospsd-team-2/blob/hw-3/src/ai_server/ai_server/auth.py) — exact signing verification
- [`src/ai_server/ai_server/wrapper_client.py`](https://github.com/2SpaceMasterRace/ospsd-team-2/blob/hw-3/src/ai_server/ai_server/wrapper_client.py) — reference signing helpers
- [`src/ai_server/tests/test_wrapper_contract.py`](https://github.com/2SpaceMasterRace/ospsd-team-2/blob/hw-3/src/ai_server/tests/test_wrapper_contract.py) — accepted request shapes

(nimbus-slack-bridge-ai-integration)=
### Smoke Test From This Repo

```bash
uv run python scripts/ai_server_wrapper_smoke.py \
  --base-url https://nimbus-production.onrender.com \
  --signing-secret "$AI_SERVER_SIGNING_SECRET" \
  message-event \
  --workspace-id T123TEAM \
  --event-id evt-test-001 \
  --channel-id C123CHAN \
  --message-ts 1713840000.123456 \
  --user-id U123USER \
  --text "What files are under reports/?"
```
