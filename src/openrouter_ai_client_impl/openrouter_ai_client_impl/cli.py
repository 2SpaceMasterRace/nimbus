"""Nimbus CLI: a Typer-powered REPL over the OpenRouter AI client.

The look-and-feel is modelled on modern AI CLIs (opencode, amp, claude-code):

* A framed banner at startup showing the active model, fallback, tool list,
  and session id.
* A spinner while the model is thinking.
* Markdown rendering for assistant text.
* Colour-coded inline events for every tool call (cyan for the call, green
  tick on success, red cross on failure, dim yellow for dry-run, magenta for
  model fallbacks).
* Slash commands rendered in a table by ``/help``.
* A ``/debug`` command that dumps the last few raw provider responses — the
  first thing you want when tool calls stop firing.

Environment contract:

* ``OPENROUTER_API_KEY`` — required.
* ``OPENROUTER_MODEL`` — primary model; optional.
* ``OPENROUTER_FALLBACK_MODEL`` — fallback on 429/5xx; optional.
* ``AWS_REGION`` / standard AWS creds — required if using cloud tools.
* ``NIMBUS_CONTAINER`` — S3 bucket the LLM is pinned to. Falls back to
  ``AWS_BUCKET_NAME`` (the workspace's existing convention) if unset.
* ``NIMBUS_SAFE_ROOT`` — local directory the LLM can read/write (default: cwd).
* ``NIMBUS_SESSION_DIR`` — where conversation JSON is persisted
  (default: ``~/.nimbus/sessions``).
"""

from __future__ import annotations

import json
import os
import signal
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.rule import Rule
from rich.status import Status
from rich.table import Table
from rich.text import Text

from ai_client_api import AIClientError, Conversation
from openrouter_ai_client_impl.config import (
    DEFAULT_MAX_STEPS,
    DEFAULT_SYSTEM_PROMPT,
    RECOMMENDED_FREE_MODELS,
    OpenRouterConfig,
)
from openrouter_ai_client_impl.openrouter_client import OpenRouterClient

if TYPE_CHECKING:
    from types import FrameType

    from ai_client_api import AgentEvent, Tool

# --- Theme constants -------------------------------------------------------
# Colours are chosen so they read on both dark and light terminal themes.
_USER_COLOR = "cyan"
_ASSISTANT_COLOR = "white"
_TOOL_CALL_COLOR = "cyan"
_TOOL_OK_COLOR = "green"
_TOOL_FAIL_COLOR = "red"
_TOOL_DRYRUN_COLOR = "yellow"
_FALLBACK_COLOR = "magenta"
_INFO_COLOR = "dim"
_ERROR_COLOR = "bold red"
_BANNER_BORDER = "cyan"

# --- Typer app ------------------------------------------------------------

app = typer.Typer(
    name="nimbus",
    help="[bold cyan]Nimbus[/bold cyan] — AI-powered cloud-storage assistant.",
    add_completion=False,
    rich_markup_mode="rich",
    no_args_is_help=False,
)


class NimbusCLI:
    """Stateful wrapper around an :class:`OpenRouterClient` and a Rich REPL."""

    def __init__(
        self,
        *,
        client: OpenRouterClient,
        tools: list[Tool],
        session_id: str,
        session_dir: Path,
        system_prompt: str,
        max_steps: int = DEFAULT_MAX_STEPS,
        console: Console | None = None,
    ) -> None:
        """Set up the REPL state and attach the event listener."""
        self._client = client
        self._tools = tools
        self._session_dir = session_dir
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._session_id = session_id
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        self._dry_run = False
        self._debug = False
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._console = console or Console()
        self._conversation = self._load_conversation(session_id)
        self._client.on_event(self._on_event)

    # --- persistence -------------------------------------------------------

    def _conv_path(self, session_id: str) -> Path:
        safe = "".join(c for c in session_id if c.isalnum() or c in "_-")
        return self._session_dir / f"{safe or 'default'}.json"

    def _load_conversation(self, session_id: str) -> Conversation:
        path = self._conv_path(session_id)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                conv = Conversation.from_json(data)
            except (OSError, ValueError, json.JSONDecodeError) as err:
                self._console.print(
                    f"[{_ERROR_COLOR}]could not load session '{session_id}': {err}[/]"
                )
            else:
                # Pin the current system prompt on load so prompt tweaks in
                # code take effect on next startup.
                conv.system = self._system_prompt
                conv.session_id = session_id
                return conv
        return Conversation(system=self._system_prompt, session_id=session_id)

    def _save_conversation(self) -> None:
        """Persist the conversation atomically (FM5: no half-written files on crash)."""
        path = self._conv_path(self._session_id)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(self._conversation.to_json(), indent=2), encoding="utf-8"
            )
            tmp.replace(path)  # atomic on POSIX; near-atomic on Windows
        except OSError as err:
            self._console.print(f"[{_ERROR_COLOR}]could not save session: {err}[/]")

    # --- run loop ----------------------------------------------------------

    def run(self) -> int:
        """Run the REPL until the user exits. Returns a shell exit code."""
        self._print_banner()
        signal.signal(signal.SIGINT, self._on_sigint)

        while True:
            try:
                line = Prompt.ask(
                    Text("▍", style=f"bold {_USER_COLOR}"),
                    console=self._console,
                ).strip()
            except (EOFError, KeyboardInterrupt):
                self._console.print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if self._handle_slash(line) is False:
                    break
                continue
            self._send_user_turn(line)
        return 0

    def _send_user_turn(self, text: str) -> None:
        self._conversation.add_user(text)
        self._console.print()  # breathe before spinner
        try:
            with Status(
                "[dim]  thinking…[/]",
                console=self._console,
                spinner="dots",
            ):
                response = self._client.send_message(
                    self._conversation,
                    tools=self._tools,
                    max_steps=self._max_steps,
                    dry_run=self._dry_run,
                )
        except AIClientError as err:
            # Roll back the optimistic user-message append so the failed
            # message is not re-sent on the next request (P2).
            self._conversation.pop_last_user()
            self._console.print(f"[{_ERROR_COLOR}]{type(err).__name__}:[/] {err}")
            return
        self._total_input_tokens += response.tokens.input_tokens
        self._total_output_tokens += response.tokens.output_tokens
        self._save_conversation()

        # A dim Rule + bare Markdown keeps the response readable without the
        # weight of a full panel box. Empty reply is common when the model
        # finishes after a tool call without additional commentary.
        text_out = response.text.strip()
        if text_out:
            self._console.print(Rule(style="dim"))
            self._console.print(Markdown(text_out))
        footer = Text("  ")
        footer.append(f"steps={response.steps}", style=_INFO_COLOR)
        footer.append("  ·  ", style="dim")
        footer.append(f"tokens={response.tokens.total}", style=_INFO_COLOR)
        footer.append("  ·  ", style="dim")
        footer.append(response.model, style=_INFO_COLOR)
        if response.fallback_used:
            footer.append("  (fallback)", style=_FALLBACK_COLOR)
        self._console.print(footer)
        self._console.print()  # breathe between turns
        if self._debug:
            self._print_debug_tail()

    # --- slash commands ----------------------------------------------------

    def _handle_slash(self, line: str) -> bool:
        parts = line.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        handler = _SLASH_COMMANDS.get(cmd)
        if handler is None:
            self._console.print(
                f"[{_ERROR_COLOR}]unknown command:[/] {cmd}. Type /help."
            )
            return True
        return handler(self, arg)

    def _cmd_quit(self, _arg: str) -> bool:
        self._save_conversation()
        return False

    def _cmd_help(self, _arg: str) -> bool:
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style=f"bold {_USER_COLOR}")
        table.add_column()
        for cmd, desc in _HELP_ROWS:
            table.add_row(cmd, desc)
        self._console.print(table)
        return True

    def _cmd_clear(self, _arg: str) -> bool:
        self._conversation.clear()
        self._save_conversation()
        self._console.print(f"[{_INFO_COLOR}]history cleared[/]")
        return True

    def _cmd_history(self, _arg: str) -> bool:
        payload = json.dumps(self._conversation.to_json(), indent=2)
        self._console.print(payload)
        return True

    def _cmd_model(self, arg: str) -> bool:
        if arg:
            self._client._config = replace(self._client._config, model=arg)  # noqa: SLF001 - CLI intentionally rewrites config
            self._console.print(f"[{_INFO_COLOR}]primary model →[/] {arg}")
        else:
            self._console.print(
                f"[{_INFO_COLOR}]primary:[/] {self._client._config.model}  "  # noqa: SLF001
                f"[{_INFO_COLOR}]fallback:[/] "
                f"{self._client._config.fallback_model or 'none'}"  # noqa: SLF001
            )
        return True

    def _cmd_fallback(self, arg: str) -> bool:
        fb = arg.strip() or None
        if arg.lower() in ("none", "off", "clear"):
            fb = None
        self._client._config = replace(self._client._config, fallback_model=fb)  # noqa: SLF001
        label = fb or "disabled"
        self._console.print(f"[{_INFO_COLOR}]fallback model →[/] {label}")
        return True

    def _cmd_models(self, _arg: str) -> bool:
        """Show the curated free-tier model list (non-Venice upstreams)."""
        table = Table(title="Recommended free models", box=None, padding=(0, 2))
        table.add_column("id", style=f"bold {_USER_COLOR}")
        table.add_column("label", style=_INFO_COLOR)
        table.add_column("upstream", style=_INFO_COLOR)
        for model_id, label, upstream in RECOMMENDED_FREE_MODELS:
            table.add_row(model_id, label, upstream)
        self._console.print(table)
        self._console.print(
            f"[{_INFO_COLOR}]Use[/] /model <id>  [{_INFO_COLOR}]or[/] "
            f"/fallback <id>  [{_INFO_COLOR}]to switch.[/]"
        )
        return True

    def _cmd_steps(self, arg: str) -> bool:
        """Show or change the per-request step budget."""
        if not arg:
            self._console.print(f"[{_INFO_COLOR}]max_steps:[/] {self._max_steps}")
            return True
        try:
            value = int(arg)
        except ValueError:
            self._console.print(f"[{_ERROR_COLOR}]steps must be an integer[/]")
            return True
        if value <= 0:
            self._console.print(f"[{_ERROR_COLOR}]steps must be > 0[/]")
            return True
        self._max_steps = value
        self._console.print(f"[{_INFO_COLOR}]max_steps →[/] {value}")
        return True

    def _cmd_cost(self, _arg: str) -> bool:
        total = self._total_input_tokens + self._total_output_tokens
        self._console.print(
            f"tokens: input={self._total_input_tokens} "
            f"output={self._total_output_tokens} total={total}  "
            f"[{_INFO_COLOR}](free-tier models are $0; cost is informational)[/]"
        )
        return True

    def _cmd_dry_run(self, arg: str) -> bool:
        if arg.lower() == "on":
            self._dry_run = True
        elif arg.lower() == "off":
            self._dry_run = False
        self._console.print(f"[{_INFO_COLOR}]dry_run={self._dry_run}[/]")
        return True

    def _cmd_debug(self, arg: str) -> bool:
        if arg.lower() == "on":
            self._debug = True
            self._console.print(f"[{_INFO_COLOR}]debug=True[/]")
        elif arg.lower() == "off":
            self._debug = False
            self._console.print(f"[{_INFO_COLOR}]debug=False[/]")
        else:
            # Show current tail on bare /debug.
            self._print_debug_tail()
        return True

    def _cmd_session(self, arg: str) -> bool:
        if not arg:
            self._console.print(
                f"[{_INFO_COLOR}]current session:[/] {self._session_id}"
            )
        else:
            self._save_conversation()
            self._session_id = arg
            self._conversation = self._load_conversation(arg)
            self._console.print(f"[{_INFO_COLOR}]session →[/] {arg}")
        return True

    def _cmd_ping(self, _arg: str) -> bool:
        """Probe the AI provider with a minimal call and report reachability."""
        with Status(
            "[dim]Pinging provider...[/]", console=self._console, spinner="dots"
        ):
            reachable = self._client.ping()
        if reachable:
            self._console.print(
                Text.assemble(("  ✓ ", _TOOL_OK_COLOR), "provider reachable")
            )
        else:
            self._console.print(
                Text.assemble(
                    ("  ✗ ", _TOOL_FAIL_COLOR),
                    ("provider unreachable", _TOOL_FAIL_COLOR),
                )
            )
        return True

    def _cmd_status(self, _arg: str) -> bool:
        """Print a compact status panel: session, model, tokens, and runtime flags."""
        ctx_tokens = self._conversation.approximate_tokens()
        api_total = self._total_input_tokens + self._total_output_tokens
        msg_count = len(self._conversation) - 1  # exclude the pinned system prompt
        table = Table.grid(padding=(0, 1))
        table.add_column(style=_INFO_COLOR, justify="right")
        table.add_column()
        table.add_row(
            "model",
            f"[{_USER_COLOR}]{self._client._config.model}[/]",  # noqa: SLF001
        )
        table.add_row(
            "fallback",
            self._client._config.fallback_model or "none",  # noqa: SLF001
        )
        table.add_row("session", self._session_id)
        table.add_row("messages", str(msg_count))
        table.add_row("context ~tokens", str(ctx_tokens))
        table.add_row(
            "api tokens",
            (
                f"in={self._total_input_tokens}"
                f"  out={self._total_output_tokens}"
                f"  total={api_total}"
            ),
        )
        table.add_row("max_steps", str(self._max_steps))
        table.add_row(
            "flags",
            f"dry_run={self._dry_run}  debug={self._debug}",
        )
        self._console.print(
            Panel(
                table,
                title=Text("status", style=f"bold {_ASSISTANT_COLOR}"),
                border_style=_INFO_COLOR,
                padding=(0, 1),
            )
        )
        return True

    # --- rendering helpers -------------------------------------------------

    def _print_banner(self) -> None:
        table = Table.grid(padding=(0, 1))
        table.add_column(style=_INFO_COLOR, justify="right")
        table.add_column()
        table.add_row(
            "model",
            f"[{_USER_COLOR}]{self._client._config.model}[/]",  # noqa: SLF001 - banner
        )
        table.add_row(
            "fallback",
            self._client._config.fallback_model or "none",  # noqa: SLF001
        )
        table.add_row("session", self._session_id)
        tool_names = ", ".join(t.name for t in self._tools) or "(none)"
        table.add_row("tools", tool_names)
        body = Text.assemble(
            ("Nimbus", f"bold {_USER_COLOR}"),
            "  your cloud-storage assistant.  ",
            ("Type /help for commands.", _INFO_COLOR),
        )
        self._console.print(
            Panel(
                table,
                title=body,
                border_style=_BANNER_BORDER,
                padding=(0, 1),
            )
        )
        self._console.print()  # gap between banner and first prompt

    def _print_debug_tail(self) -> None:
        tail = self._client.last_raw_completions()
        if not tail:
            self._console.print(f"[{_INFO_COLOR}]no raw responses captured yet[/]")
            return
        self._console.print(Rule("debug: last raw completions", style=_INFO_COLOR))
        self._console.print(json.dumps(tail, indent=2, default=str))

    def _on_event(self, event: AgentEvent) -> None:
        payload = event.payload
        if event.kind == "tool_call_started":
            name = payload.get("name")
            args = _short(payload.get("arguments"))
            prefix = f"[{_TOOL_DRYRUN_COLOR}]dry-run [/]" if self._dry_run else ""
            self._console.print(
                Text.assemble(
                    ("  ● ", _TOOL_CALL_COLOR),
                    prefix,
                    (f"{name}", f"bold {_TOOL_CALL_COLOR}"),
                    (f"({args})", "dim"),
                )
            )
        elif event.kind == "tool_call_completed":
            name = payload.get("name")
            success = payload.get("success")
            if success:
                marker = Text("    ✓ ", style=_TOOL_OK_COLOR)
                self._console.print(Text.assemble(marker, (f"{name}", _INFO_COLOR)))
            else:
                reason = payload.get("reason", "fail")
                marker = Text("    ✗ ", style=_TOOL_FAIL_COLOR)
                self._console.print(
                    Text.assemble(marker, f"{name}: ", (reason, _TOOL_FAIL_COLOR))
                )
        elif event.kind == "model_fallback":
            from_m = payload.get("from_model")
            to_m = payload.get("to_model")
            reason = payload.get("reason")
            self._console.print(
                Text.assemble(
                    ("  ↻ ", _FALLBACK_COLOR),
                    (f"fallback: {from_m} → {to_m}", _FALLBACK_COLOR),
                    (f"  ({reason})", _INFO_COLOR),
                )
            )
        elif event.kind == "error":
            reason = payload.get("reason", "")
            self._console.print(f"[{_ERROR_COLOR}]error:[/] {reason}")

    def _on_sigint(self, _signum: int, _frame: FrameType | None) -> None:
        self._save_conversation()
        self._console.print(
            f"\n[{_INFO_COLOR}]interrupted — session saved. Type /quit to exit.[/]"
        )


# --- command table ---------------------------------------------------------

_HELP_ROWS: tuple[tuple[str, str], ...] = (
    ("/help", "show this help"),
    ("/clear", "wipe conversation history (keeps system prompt)"),
    ("/history", "dump the current conversation as JSON"),
    ("/model [name]", "show or change the primary model"),
    ("/fallback [name|none]", "show or change the fallback model"),
    ("/models", "list curated free-tier models (non-Venice upstreams)"),
    ("/steps [n]", "show or change the per-request step budget"),
    ("/cost", "cumulative tokens used this session"),
    ("/ping", "probe provider reachability (live network call)"),
    ("/status", "show session, model, token, and runtime state"),
    ("/dry-run on|off", "toggle dry-run (tools are logged, not executed)"),
    ("/debug [on|off]", "print the last raw provider responses"),
    ("/session <id>", "switch to a different persisted session"),
    ("/quit", "exit the REPL"),
)

_SlashHandler = Callable[["NimbusCLI", str], bool]
_SLASH_COMMANDS: dict[str, _SlashHandler] = {}


def _register(name: str, method_name: str) -> None:
    def _run(cli: NimbusCLI, arg: str) -> bool:
        result = getattr(cli, method_name)(arg)
        return bool(result)

    _SLASH_COMMANDS[name] = _run


_register("/quit", "_cmd_quit")
_register("/exit", "_cmd_quit")
_register("/help", "_cmd_help")
_register("/clear", "_cmd_clear")
_register("/history", "_cmd_history")
_register("/model", "_cmd_model")
_register("/fallback", "_cmd_fallback")
_register("/models", "_cmd_models")
_register("/steps", "_cmd_steps")
_register("/cost", "_cmd_cost")
_register("/ping", "_cmd_ping")
_register("/status", "_cmd_status")
_register("/dry-run", "_cmd_dry_run")
_register("/debug", "_cmd_debug")
_register("/session", "_cmd_session")


# --- helpers ---------------------------------------------------------------


def _short(args: object) -> str:
    text = json.dumps(args, default=str) if not isinstance(args, str) else args
    limit = 120
    return text if len(text) <= limit else text[:limit] + "..."


def _load_dotenv_best_effort() -> None:
    """Populate ``os.environ`` from a repo-level ``credentials.env`` / ``.env``.

    Walks upward from the working directory looking for ``credentials.env``
    (preferred, the workspace convention) or ``.env`` (fallback). Exported
    shell variables win so the user can always override on the command
    line. A missing ``python-dotenv`` is tolerated silently.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    cwd = Path.cwd().resolve()
    for candidate_dir in (cwd, *cwd.parents):
        for name in ("credentials.env", ".env"):
            path = candidate_dir / name
            if path.is_file():
                load_dotenv(path, override=False)
                return


def _build_tools_or_empty(container: str | None, safe_root: Path) -> list[Tool]:
    """Build cloud tools if AWS/container config is present; else an empty list."""
    if not container:
        typer.echo(
            typer.style(
                "NIMBUS_CONTAINER / AWS_BUCKET_NAME is not set; "
                "starting without cloud-storage tools.",
                dim=True,
            )
        )
        return []
    try:
        from aws_client_impl.s3_client import (
            get_client_impl as get_s3_client,
        )

        from openrouter_ai_client_impl.cloud_storage_tools import (
            build_cloud_storage_tools,
        )
    except ImportError as err:
        typer.echo(
            typer.style("cloud-storage tools unavailable: ", fg="red", bold=True)
            + str(err)
        )
        return []
    storage = get_s3_client()
    return build_cloud_storage_tools(
        storage=storage, container=container, safe_root=safe_root
    )


# --- CLI entry point -------------------------------------------------------


@app.command()
def main(
    session: Annotated[
        str | None,
        typer.Option(
            "--session",
            help=(
                "Session id to load/save. Omit to start a fresh auto-named "
                "session so prior runs don't leak into context."
            ),
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            help=(
                "Primary model id (overrides OPENROUTER_MODEL). "
                "Use /models in the REPL to see curated choices."
            ),
        ),
    ] = None,
    fallback_model: Annotated[
        str | None,
        typer.Option(
            "--fallback",
            help=(
                "Fallback model id on 429/5xx (overrides "
                "OPENROUTER_FALLBACK_MODEL). Pass 'none' to disable."
            ),
        ),
    ] = None,
    max_steps: Annotated[
        int | None,
        typer.Option(
            "--max-steps",
            help=(
                "Per-request agentic step budget. Higher values let the "
                "model chain more tool calls; keep low under concurrent load."
            ),
        ),
    ] = None,
    no_tools: Annotated[  # noqa: FBT002 - Typer bool options are a framework pattern, not a positional default
        bool,
        typer.Option(
            "--no-tools/--with-tools",
            help="Start without cloud-storage tools.",
        ),
    ] = False,
) -> None:
    """[bold cyan]Nimbus[/bold cyan] — AI-powered cloud-storage REPL."""
    _load_dotenv_best_effort()
    console = Console()

    try:
        config = OpenRouterConfig.from_env()
    except AIClientError as err:
        typer.echo(
            typer.style("fatal: ", fg="red", bold=True) + str(err),
            err=True,
        )
        raise typer.Exit(code=2) from err

    if model:
        config = replace(config, model=model)
    if fallback_model is not None:
        fb = None if fallback_model.lower() in ("none", "off", "") else fallback_model
        config = replace(config, fallback_model=fb)

    client = OpenRouterClient(config)
    session_dir = Path(
        os.environ.get("NIMBUS_SESSION_DIR", Path.home() / ".nimbus" / "sessions")
    )
    safe_root = Path(os.environ.get("NIMBUS_SAFE_ROOT", Path.cwd()))
    container = os.environ.get("NIMBUS_CONTAINER") or os.environ.get("AWS_BUCKET_NAME")
    tools = [] if no_tools else _build_tools_or_empty(container, safe_root)
    session_id = session or f"session-{uuid.uuid4().hex[:8]}"

    cli = NimbusCLI(
        client=client,
        tools=tools,
        session_id=session_id,
        session_dir=session_dir,
        system_prompt=config.system_prompt or DEFAULT_SYSTEM_PROMPT,
        max_steps=max_steps if max_steps and max_steps > 0 else DEFAULT_MAX_STEPS,
        console=console,
    )
    raise typer.Exit(code=cli.run())
