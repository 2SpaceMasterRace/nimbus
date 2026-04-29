# Nimbus REPL Tutorial

This walkthrough shows how to install the OpenRouter AI client, start the
`nimbus` REPL, and have the LLM upload, list, and clean up a real file
through the existing S3 backend.

## What you need

- Python 3.12 or newer, installed through the workspace `uv` setup.
- A free [OpenRouter](https://openrouter.ai/) API key. Free-tier models
  are listed at <https://openrouter.ai/models?supported_parameters=tools&max_price=0>.
- AWS credentials that can reach the bucket you want to use (the same
  ones the rest of this project already uses).
- A bucket name, e.g. `ospsd-team-2-tutorial`.

## 1. Set up your environment

You have two options.

**Option A — a `credentials.env` file at the repo root (recommended).**
The CLI auto-loads `credentials.env` (falling back to `.env`) on startup,
walking up from the working directory. `credentials.env` is already
gitignored in this workspace.

```ini
OPENROUTER_API_KEY=sk-or-v1-...
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=us-east-1
AWS_BUCKET_NAME=ospsd-team-2-tutorial
```

`AWS_BUCKET_NAME` is the workspace's existing convention (it's what the
FastAPI service reads, too). `nimbus` uses it as the pinned S3 bucket
when `NIMBUS_CONTAINER` is not set — so you do not need to duplicate the
value.

If you prefer the Nimbus-native name, set `NIMBUS_CONTAINER` instead; it
wins when both are present. Optionally also set
`NIMBUS_SAFE_ROOT=/absolute/path/to/workspace` to restrict the LLM's
local filesystem view (default: the current working directory).

Already-exported shell variables take precedence over the file, so you
can still override values on the command line.

**Option B — export them yourself.**

```console
$ export OPENROUTER_API_KEY="sk-or-v1-..."   # required
$ export NIMBUS_CONTAINER="ospsd-team-2-tutorial"  # pinned bucket
$ export NIMBUS_SAFE_ROOT="$(pwd)"           # LLM cannot read outside this
$ export AWS_REGION="us-east-1"              # any valid region
```

Optional overrides:

```console
$ export OPENROUTER_MODEL="z-ai/glm-4.5-air:free"
$ export OPENROUTER_FALLBACK_MODEL="nousresearch/hermes-3-llama-3.1-405b:free"
$ export OPENROUTER_TIMEOUT="60"
$ export NIMBUS_SESSION_DIR="$HOME/.nimbus/sessions"
```

The defaults route to independent upstreams (Novita + DeepInfra) so a
single provider outage does not take out both. Any model ID from the
OpenRouter free-tools
[catalog](https://openrouter.ai/models?supported_parameters=tools&max_price=0)
works. Use `/models` inside the REPL to see a curated list of non-Venice
free models.

## 2. Install

From the repository root:

```console
$ uv sync --all-packages
```

This installs the workspace, including the `nimbus` console script.

## 3. Create a small file for the model to upload

```console
$ printf 'hello from Nimbus\n' > hello.txt
```

`hello.txt` must live inside `NIMBUS_SAFE_ROOT`. The path-escape guard
will reject anything outside.

## 4. Start the REPL

```console
$ uv run nimbus
╭─ Nimbus  your cloud-storage assistant.  Type /help for commands. ─╮
│     model  z-ai/glm-4.5-air:free                                   │
│  fallback  nousresearch/hermes-3-llama-3.1-405b:free               │
│   session  session-4f2c9a1b                                        │
│     tools  upload_file, download_file, list_files, delete_file,    │
│            get_file_info                                           │
╰────────────────────────────────────────────────────────────────────╯
▍
```

Each run picks a fresh `session-<8-hex>` id by default, so state from
previous conversations never leaks into the new one. Pass
`--session default` (or any other id) to reuse and persist a named
session — the tutorial's `/session` command does the same thing mid-run.

If you see `[info] NIMBUS_CONTAINER is not set ...`, go back to step 1
and export the variable.

## 5. Upload the file through the LLM

Ask in plain English:

```
> Please upload hello.txt to the bucket as tutorial/hello.txt and tell me the result.
```

You should see something like:

```
  ● upload_file({"local_path": "hello.txt", "remote_path": "tutorial/hello.txt"})
    ✓ upload_file: ok
╭─ nimbus ─────────────────────────────────────────────────────────╮
│ I uploaded hello.txt to tutorial/hello.txt. The object is 18     │
│ bytes.                                                            │
╰───────────────────────────────────────────────────────────────────╯
  steps=2  tokens=1847  model=z-ai/glm-4.5-air:free
```

The `●` / `✓` / `✗` glyphs come from the event listener attached by the
CLI; they are your live audit trail. A `↻` means the primary model hit a
429 or 5xx and Nimbus fell over to the fallback.

## 6. List and verify

```
> List everything under tutorial/.
```

The model will call `list_files` and summarise the result. The tool
returns counts plus up to 50 entries, so huge listings cannot blow up
your context window.

## 7. Download and delete

```
> Download tutorial/hello.txt as hello-copy.txt.
> Now delete tutorial/hello.txt. I confirm.
```

`delete_file` requires `confirm=true`, so the model will ask you to
confirm in natural language before passing that argument. If it forgets,
the Pydantic guard will refuse the call and the REPL will print
`<< delete_file: fail: refusing to delete without confirm=true`.

## 8. Slash commands you will actually use

| Command            | Effect                                                  |
| ------------------ | ------------------------------------------------------- |
| `/help`            | Show the inline command list.                           |
| `/clear`           | Wipe conversation history, keep the system prompt.      |
| `/history`         | Dump the current conversation as JSON.                  |
| `/model <name>`    | Switch the primary model mid-session.                   |
| `/dry-run on`      | Log tool calls but do not execute them — great for demo.|
| `/debug [on\|off]` | Print the last few raw provider responses. Invaluable when tool calls stop firing: you see exactly whether the model emitted a `tool_calls` block or just text. Without an argument it prints the current tail. |
| `/cost`            | Print cumulative tokens used this session.              |
| `/session <id>`    | Swap to a different persisted conversation file.        |
| `/quit` / `Ctrl-D` | Exit the REPL. The session is saved automatically.      |

## 9. Persisted sessions

Conversations are saved as JSON under `NIMBUS_SESSION_DIR`
(default: `~/.nimbus/sessions/<session-id>.json`). Each fresh `nimbus`
invocation picks a new random id (`session-<8-hex>`) so your context does
not silently drift across unrelated runs. Re-running
`nimbus --session default` (or any other explicit id) picks up where you
left off. `Ctrl-C` also triggers a save before printing `[interrupted]
session saved.`

## 10. Clean up

```
> delete the hello.txt locally for me
```

The model cannot — it is sandboxed to cloud calls only. Do it yourself:

```console
$ rm hello.txt hello-copy.txt
```
