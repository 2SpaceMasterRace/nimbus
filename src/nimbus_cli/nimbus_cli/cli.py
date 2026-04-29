"""Python-only Nimbus CLI for local runtime and remote server profiles."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from pathlib import Path
from typing import Annotated, NoReturn

import httpx
import typer
from nimbus_runtime.models import ChatTurnInput
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from ai_client_api import AIClientError
from nimbus_cli.auth import encode_json_body, remote_auth_headers
from nimbus_cli.config import (
    DEFAULT_MODEL,
    DEFAULT_OPENROUTER_BASE_URL,
    DEFAULT_REMOTE_PATH,
    ConfigStore,
    NimbusProfile,
    RemoteAuthKind,
    SessionRecord,
    default_session_dir,
)
from nimbus_cli.runtime import build_local_runtime
from nimbus_cli.secrets import NimbusSecrets
from nimbus_protocol import StreamEventType

_APP_HELP = "Nimbus — local and remote AI system CLI."
_ERROR_STYLE = "bold red"
_INFO_STYLE = "dim"
_ACCENT_STYLE = "cyan"
_REMOTE_TIMEOUT_SECONDS = 60.0

app = typer.Typer(
    name="nimbus",
    help=_APP_HELP,
    add_completion=False,
    rich_markup_mode="rich",
)
setup_app = typer.Typer(help="Create or update Nimbus profiles.")
auth_app = typer.Typer(help="Inspect Nimbus auth state.")
app.add_typer(setup_app, name="setup")
app.add_typer(auth_app, name="auth")


@setup_app.command("local")
def setup_local(
    profile: Annotated[str, typer.Option("--profile", "-p")] = "local",
    api_key: Annotated[
        str | None,
        typer.Option("--openrouter-key", help="OpenRouter API key to store."),
    ] = None,
    model: Annotated[str, typer.Option("--model")] = DEFAULT_MODEL,
    fallback_model: Annotated[
        str | None,
        typer.Option("--fallback", help="Fallback model, or 'none'."),
    ] = None,
    container: Annotated[
        str | None,
        typer.Option("--container", help="Pinned cloud-storage container."),
    ] = None,
    session_dir: Annotated[
        Path | None,
        typer.Option("--session-dir", help="Local Nimbus session directory."),
    ] = None,
) -> None:
    """Onboard a local profile that runs ``NimbusRuntime`` in-process."""
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    config = store.load()
    selected_key = api_key or Prompt.ask(
        "OpenRouter API key",
        password=True,
        default="",
    )
    profile_obj = NimbusProfile(
        name=profile,
        mode="local",
        model=model,
        fallback_model=_normalize_optional_model(fallback_model),
        openrouter_base_url=DEFAULT_OPENROUTER_BASE_URL,
        storage_container=container,
        session_dir=str(session_dir.expanduser()) if session_dir else None,
    )
    config = config.with_profile(profile_obj)
    store.save(config)
    if selected_key:
        secrets.set(profile=profile, kind="openrouter_api_key", value=selected_key)
    Console().print(f"[{_ACCENT_STYLE}]saved local profile[/] {profile!r}")


@setup_app.command("remote")
def setup_remote(
    profile: Annotated[str, typer.Option("--profile", "-p")] = "remote",
    base_url: Annotated[str, typer.Option("--base-url")] = "",
    auth: Annotated[RemoteAuthKind, typer.Option("--auth")] = "hmac",
    token: Annotated[
        str | None,
        typer.Option("--token", help="Bearer token for --auth bearer."),
    ] = None,
    signing_secret: Annotated[
        str | None,
        typer.Option("--signing-secret", help="HMAC secret for --auth hmac."),
    ] = None,
) -> None:
    """Onboard a remote/self-hosted Nimbus server profile."""
    if not base_url:
        raise _exit("remote profiles require --base-url", code=2)
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    secret_value = _remote_secret_from_args(auth, token, signing_secret)
    if secret_value is None:
        prompt_label = "Bearer token" if auth == "bearer" else "HMAC signing secret"
        secret_value = Prompt.ask(prompt_label, password=True, default="")
    profile_obj = NimbusProfile(
        name=profile,
        mode="remote",
        remote_base_url=base_url.rstrip("/"),
        remote_auth=auth,
    )
    config = store.load().with_profile(profile_obj)
    store.save(config)
    if secret_value:
        kind = "remote_bearer_token" if auth == "bearer" else "remote_signing_secret"
        secrets.set(profile=profile, kind=kind, value=secret_value)
    Console().print(f"[{_ACCENT_STYLE}]saved remote profile[/] {profile!r}")


@auth_app.command("status")
def auth_status() -> None:
    """Show configured profiles and whether required secrets exist."""
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    config = store.load()
    table = Table(title="Nimbus profiles")
    table.add_column("profile", style=f"bold {_ACCENT_STYLE}")
    table.add_column("mode")
    table.add_column("target")
    table.add_column("auth")
    table.add_column("secret")
    for profile in config.profiles.values():
        table.add_row(
            profile.name,
            profile.mode,
            _profile_target(profile),
            _profile_auth_label(profile),
            "stored" if _profile_secret_present(profile, secrets) else "missing",
        )
    Console().print(table)


@app.command()
def chat(
    message: Annotated[str | None, typer.Argument()] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    session: Annotated[
        str | None,
        typer.Option("--session", help="External readable session id to use."),
    ] = None,
    resume_last: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option(
            "--resume-last",
            help="Resume the profile's last CLI session instead of starting fresh.",
        ),
    ] = False,
    no_tools: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--no-tools/--with-tools", help="Disable storage tools locally."),
    ] = False,
) -> None:
    """Send one message, or start a small REPL when no message is supplied."""
    _load_dotenv_best_effort()
    _run_chat_command(
        message=message,
        profile_name=profile,
        session_external_id=session,
        resume_last=resume_last,
        no_tools=no_tools,
    )


@app.command()
def resume(
    message: Annotated[str | None, typer.Argument()] = None,
    profile: Annotated[str | None, typer.Option("--profile", "-p")] = None,
    no_tools: Annotated[  # noqa: FBT002 - Typer bool options need defaults.
        bool,
        typer.Option("--no-tools/--with-tools", help="Disable storage tools locally."),
    ] = False,
) -> None:
    """Resume the active profile's last session."""
    _load_dotenv_best_effort()
    _run_chat_command(
        message=message,
        profile_name=profile,
        session_external_id=None,
        resume_last=True,
        no_tools=no_tools,
    )


def _run_chat_command(
    *,
    message: str | None,
    profile_name: str | None,
    session_external_id: str | None,
    resume_last: bool,
    no_tools: bool,
) -> None:
    store = ConfigStore()
    secrets = NimbusSecrets(store.home)
    console = Console()
    config = store.load()
    try:
        profile = config.profile(profile_name)
        config, session = config.resolve_session(
            profile_name=profile.name,
            external_id=session_external_id,
            resume_last=resume_last,
        )
    except (KeyError, ValueError) as exc:
        raise _exit(str(exc), code=2) from exc
    store.save(config)
    _print_session_banner(console=console, profile=profile, session=session)
    if message is not None:
        _run_one_message(
            console=console,
            profile=profile,
            secrets=secrets,
            session=session,
            message=message,
            no_tools=no_tools,
        )
        return
    while True:
        try:
            line = Prompt.ask(Text("nimbus", style=f"bold {_ACCENT_STYLE}")).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not line:
            continue
        if line in {"/quit", "/exit"}:
            return
        _run_one_message(
            console=console,
            profile=profile,
            secrets=secrets,
            session=session,
            message=line,
            no_tools=no_tools,
        )


def _run_one_message(
    *,
    console: Console,
    profile: NimbusProfile,
    secrets: NimbusSecrets,
    session: SessionRecord,
    message: str,
    no_tools: bool,
) -> None:
    turn = _turn_input(profile=profile, session=session, message=message)
    if profile.mode == "local":
        runtime = build_local_runtime(
            profile=profile,
            secrets=secrets,
            no_tools=no_tools,
        )
        try:
            asyncio.run(_stream_local_turn(console=console, runtime=runtime, turn=turn))
        except AIClientError as exc:
            raise _exit(str(exc), code=1) from exc
        return
    _send_remote_turn(console=console, profile=profile, secrets=secrets, turn=turn)


async def _stream_local_turn(
    *,
    console: Console,
    runtime: object,
    turn: ChatTurnInput,
) -> None:
    """Render one local streaming runtime turn."""
    saw_delta = False
    final_text = ""
    async for event in runtime.stream_chat_turn(turn):  # type: ignore[attr-defined]
        if event.event_type == StreamEventType.TEXT_DELTA.value:
            delta = event.payload.get("delta")
            if isinstance(delta, str):
                saw_delta = True
                console.print(delta, end="")
        elif event.event_type == StreamEventType.TOOL_CALL_STARTED.value:
            name = event.payload.get("name")
            console.print(f"\n[{_INFO_STYLE}]tool[/] {name}")
        elif event.event_type == StreamEventType.TURN_COMPLETED.value:
            response = event.payload.get("response")
            if isinstance(response, dict):
                raw_text = response.get("text")
                if isinstance(raw_text, str):
                    final_text = raw_text
        elif event.event_type == StreamEventType.TURN_FAILED.value:
            error = event.payload.get("error")
            console.print(f"\n[{_ERROR_STYLE}]{error}[/]")
    if saw_delta:
        console.print()
    elif final_text:
        console.print(Markdown(final_text))


def _send_remote_turn(
    *,
    console: Console,
    profile: NimbusProfile,
    secrets: NimbusSecrets,
    turn: ChatTurnInput,
) -> None:
    """Send one turn to a remote Nimbus server and render the response."""
    if not profile.remote_base_url:
        raise _exit(f"profile {profile.name!r} is missing remote_base_url", code=2)
    body = encode_json_body(_turn_body(turn))
    headers = remote_auth_headers(profile=profile, secrets=secrets, body=body)
    url = f"{profile.remote_base_url}{DEFAULT_REMOTE_PATH}"
    try:
        response = httpx.post(
            url,
            content=body,
            headers=headers,
            timeout=_REMOTE_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise _exit(f"remote Nimbus request failed: {exc}", code=1) from exc
    payload = response.json()
    text = payload.get("text") if isinstance(payload, dict) else None
    if isinstance(text, str) and text:
        console.print(Markdown(text))
    else:
        console.print(f"[{_INFO_STYLE}]remote turn completed without text[/]")


def _turn_input(
    *,
    profile: NimbusProfile,
    session: SessionRecord,
    message: str,
) -> ChatTurnInput:
    """Build a transport-neutral turn from one CLI prompt."""
    message_id = f"msg-{time.time_ns()}"
    return ChatTurnInput(
        request_id=f"req-cli-{uuid.uuid4().hex}",
        conversation_id=f"cli:{profile.name}:{session.internal_id}",
        platform="cli",
        workspace_id=profile.name,
        channel_id="terminal",
        thread_id=session.external_id,
        message_id=message_id,
        user_id=os.environ.get("USER", "local-user"),
        text=message,
        idempotency_key=f"cli:{session.internal_id}:{message_id}",
    )


def _turn_body(turn: ChatTurnInput) -> dict[str, object]:
    """Encode a ``ChatTurnInput`` for ``POST /ai/chat/turn``."""
    return {
        "request_id": turn.request_id,
        "platform": turn.platform,
        "workspace_id": turn.workspace_id,
        "channel_id": turn.channel_id,
        "thread_id": turn.thread_id,
        "message_id": turn.message_id,
        "user_id": turn.user_id,
        "text": turn.text,
        "idempotency_key": turn.idempotency_key,
        "attachments": [],
    }


def _print_session_banner(
    *,
    console: Console,
    profile: NimbusProfile,
    session: SessionRecord,
) -> None:
    table = Table.grid(padding=(0, 1))
    table.add_column(style=_INFO_STYLE, justify="right")
    table.add_column()
    table.add_row("profile", profile.name)
    table.add_row("mode", profile.mode)
    table.add_row("session", session.external_id)
    table.add_row("internal", session.internal_id)
    if profile.mode == "local":
        table.add_row("model", profile.model)
        table.add_row("session dir", profile.session_dir or str(default_session_dir()))
    else:
        table.add_row("server", profile.remote_base_url or "")
        table.add_row("auth", profile.remote_auth or "")
    console.print(
        Panel(
            table,
            title=Text("Nimbus", style=f"bold {_ACCENT_STYLE}"),
            border_style=_ACCENT_STYLE,
        )
    )


def _normalize_optional_model(raw: str | None) -> str | None:
    if raw is None:
        return None
    return None if raw.lower() in {"none", "off", "clear", ""} else raw


def _remote_secret_from_args(
    auth: RemoteAuthKind,
    token: str | None,
    signing_secret: str | None,
) -> str | None:
    if auth == "bearer":
        return token
    return signing_secret


def _profile_target(profile: NimbusProfile) -> str:
    if profile.mode == "local":
        return profile.model
    return profile.remote_base_url or ""


def _profile_auth_label(profile: NimbusProfile) -> str:
    if profile.mode == "local":
        return "openrouter"
    return profile.remote_auth or "remote"


def _profile_secret_present(profile: NimbusProfile, secrets: NimbusSecrets) -> bool:
    if profile.mode == "local":
        return secrets.has(profile=profile.name, kind="openrouter_api_key") or bool(
            os.environ.get("OPENROUTER_API_KEY")
        )
    if profile.remote_auth == "bearer":
        return secrets.has(profile=profile.name, kind="remote_bearer_token")
    if profile.remote_auth == "hmac":
        return secrets.has(profile=profile.name, kind="remote_signing_secret")
    return False


def _load_dotenv_best_effort() -> None:
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


def _exit(message: str, *, code: int) -> NoReturn:
    typer.echo(typer.style(f"fatal: {message}", fg="red"), err=True)
    raise typer.Exit(code=code)
