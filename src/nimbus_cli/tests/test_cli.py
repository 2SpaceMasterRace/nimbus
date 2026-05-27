"""Tests for the Nimbus CLI command surface."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path

import pytest
from cloud_storage_api import ObjectInfo
from nimbus_cli.cli import app
from nimbus_cli.config import ConfigStore, NimbusProfile
from nimbus_cli.secrets import NimbusSecrets
from nimbus_runtime.generations import FileGenerationStore
from nimbus_runtime.models import ChatTurnInput, ChatTurnResult
from rich.console import Console
from typer.testing import CliRunner

from nimbus_cli import cli as cli_module

pytestmark = pytest.mark.unit

_runner = CliRunner()
_DOTENV_ENV_NAMES = (
    "OPENROUTER_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "NIMBUS_CONTAINER",
    "AWS_BUCKET_NAME",
    "NIMBUS_ENV_FILE",
)


@pytest.fixture(autouse=True)
def _restore_dotenv_environment() -> Iterator[None]:
    """Keep dotenv-loaded variables from leaking into later test modules."""
    original = {name: os.environ.get(name) for name in _DOTENV_ENV_NAMES}
    yield
    for name, value in original.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _disable_dotenv_and_clear_storage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep profile tests independent from repo or CI credential variables."""
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("nimbus_cli.cli._load_dotenv_best_effort", lambda: None)


@dataclass
class _FakeRuntime:
    turns: list[ChatTurnInput] = field(default_factory=list)

    async def run_chat_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
        """Record the turn and return a final runtime result."""
        self.turns.append(turn)
        return ChatTurnResult(
            request_id=turn.request_id,
            conversation_id=turn.conversation_id,
            text="hello from local",
            outcome="reply",
            confirmation_required=False,
            model="nimbus-runtime",
            steps=0,
            fallback_used=False,
        )


def test_local_chat_defaults_new_session_and_resume_uses_last(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default chat creates a new session; resume reuses it explicitly."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    fake_runtime = _FakeRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    setup = _runner.invoke(
        app,
        ["setup", "local", "--openrouter-key", "sk-test"],
    )
    first = _runner.invoke(app, ["chat", "hello"])
    second = _runner.invoke(app, ["resume", "again"])

    assert setup.exit_code == 0
    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "hello from local" in first.output
    assert len(fake_runtime.turns) == 2
    assert (
        fake_runtime.turns[0].conversation_id == fake_runtime.turns[1].conversation_id
    )


def test_remote_hmac_profile_posts_signed_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote HMAC profiles should sign canonical chat-turn requests."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    captured: dict[str, object] = {}

    class _Response:
        status_code: int = 200
        content: bytes = b'{"text": "remote ok"}'

        def raise_for_status(self) -> None:
            return

        def json(self) -> dict[str, object]:
            return {"text": "remote ok"}

    def _fake_post(url: str, **kwargs: object) -> _Response:
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr("nimbus_cli.cli.httpx.post", _fake_post)

    setup = _runner.invoke(
        app,
        [
            "setup",
            "remote",
            "--profile",
            "prod",
            "--base-url",
            "https://nimbus.example",
            "--auth",
            "hmac",
            "--signing-secret",
            "secret",
        ],
    )
    result = _runner.invoke(app, ["chat", "hello", "--profile", "prod"])

    assert setup.exit_code == 0
    assert result.exit_code == 0
    assert "remote ok" in result.output
    assert captured["url"] == "https://nimbus.example/ai/chat/turn"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert "X-Nimbus-Signature" in headers


def test_setup_remote_rejects_bearer_auth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote chat profiles should not save bearer auth that the server rejects."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")

    result = _runner.invoke(
        app,
        [
            "setup",
            "remote",
            "--profile",
            "prod",
            "--base-url",
            "https://nimbus.example",
            "--auth",
            "bearer",
            "--token",
            "tok-abc",
        ],
    )

    assert result.exit_code == 2
    assert "not accepted" in result.output
    assert "prod" not in ConfigStore(tmp_path).load().profiles


def test_auth_local_reads_credentials_env_for_provider_and_aws(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local auth should import root credentials.env without a web server."""
    home = tmp_path / "home"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIMBUS_HOME", str(home))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in (
        "OPENROUTER_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "NIMBUS_CONTAINER",
        "AWS_BUCKET_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "credentials.env").write_text(
        """OPENROUTER_API_KEY=sk-or-test
AWS_ACCESS_KEY_ID=AKIA_TEST
AWS_SECRET_ACCESS_KEY=aws-secret
AWS_SESSION_TOKEN=aws-token
AWS_REGION=eu-west-1
NIMBUS_CONTAINER=env-bucket
""",
        encoding="utf-8",
    )

    result = _runner.invoke(app, ["auth", "local"])

    assert result.exit_code == 0
    config = ConfigStore(home).load()
    profile = config.profile("local")
    secrets = NimbusSecrets(home)
    assert profile.storage_container == "env-bucket"
    assert profile.aws_region == "eu-west-1"
    assert secrets.get(profile="local", kind="openrouter_api_key") == "sk-or-test"
    assert secrets.get(profile="local", kind="aws_access_key_id") == "AKIA_TEST"
    assert secrets.get(profile="local", kind="aws_secret_access_key") == "aws-secret"
    assert secrets.get(profile="local", kind="aws_session_token") == "aws-token"


def test_bare_auth_imports_credentials_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`nimbus auth` should be the friendly default onboarding command."""
    home = tmp_path / "home"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIMBUS_HOME", str(home))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "credentials.env").write_text(
        """OPENROUTER_API_KEY=sk-or-default
AWS_REGION=us-east-1
NIMBUS_CONTAINER=default-bucket
""",
        encoding="utf-8",
    )

    result = _runner.invoke(app, ["auth"])

    assert result.exit_code == 0, result.output
    assert "Loaded credentials.env" in result.output
    config = ConfigStore(home).load()
    profile = config.profile("local")
    secrets = NimbusSecrets(home)
    assert profile.storage_container == "default-bucket"
    assert profile.aws_region == "us-east-1"
    assert secrets.get(profile="local", kind="openrouter_api_key") == "sk-or-default"
    assert "sk-or-default" not in result.output


def test_auth_status_loads_arbitrary_dotenv_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`auth status` should see a nearby arbitrary ``*.env`` file."""
    home = tmp_path / "home"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIMBUS_HOME", str(home))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "demo-production.env").write_text(
        """OPENROUTER_API_KEY=sk-or-prod
AWS_ACCESS_KEY_ID=AKIA_PROD
AWS_SECRET_ACCESS_KEY=prod-secret
NIMBUS_CONTAINER=prod-bucket
""",
        encoding="utf-8",
    )
    ConfigStore(home).save(
        ConfigStore(home)
        .load()
        .with_profile(
            NimbusProfile(
                name="local",
                mode="local",
                storage_container="prod-bucket",
            )
        )
    )

    result = _runner.invoke(app, ["auth", "status"])

    assert result.exit_code == 0, result.output
    assert "env" in result.output
    assert "prod-bucket" in result.output
    assert "sk-or-prod" not in result.output


def test_auth_local_uses_explicit_nimbus_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NIMBUS_ENV_FILE should allow arbitrary dotenv filenames safely."""
    home = tmp_path / "home"
    env_file = tmp_path / "demo.env"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIMBUS_HOME", str(home))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NIMBUS_ENV_FILE", str(env_file))
    env_file.write_text(
        """OPENROUTER_API_KEY=sk-or-explicit
AWS_ACCESS_KEY_ID=AKIA_EXPLICIT
AWS_SECRET_ACCESS_KEY=explicit-secret
AWS_REGION=us-west-2
NIMBUS_CONTAINER=explicit-bucket
""",
        encoding="utf-8",
    )

    result = _runner.invoke(app, ["auth", "local"])

    assert result.exit_code == 0, result.output
    profile = ConfigStore(home).load().profile("local")
    secrets = NimbusSecrets(home)
    assert profile.storage_container == "explicit-bucket"
    assert profile.aws_region == "us-west-2"
    assert secrets.get(profile="local", kind="openrouter_api_key") == "sk-or-explicit"
    assert secrets.get(profile="local", kind="aws_access_key_id") == "AKIA_EXPLICIT"


# ── Tool capability catalog ────────────────────────────────────────────────


def test_tools_list_shows_current_and_roadmap_capabilities() -> None:
    """The CLI should expose the shared runtime capability catalog."""
    result = _runner.invoke(app, ["tools", "list"])

    assert result.exit_code == 0
    assert "list_files" in result.output
    assert "candidate_plans" in result.output
    assert "Nimbus tools" in result.output


def test_tools_list_can_filter_by_surface() -> None:
    """Surface filters make it clear what Slack can see."""
    result = _runner.invoke(app, ["tools", "list", "--surface", "slack"])

    assert result.exit_code == 0
    assert "channel_backup" in result.output
    assert "automation_templates" in result.output


def test_tools_inspect_shows_one_capability() -> None:
    """Inspect shows details for a single tool/capability."""
    result = _runner.invoke(app, ["tools", "inspect", "candidate_plans"])

    assert result.exit_code == 0
    assert "Generate speculative candidate plans" in result.output
    assert "Feature 18" in result.output


def test_tools_inspect_unknown_capability_exits_nonzero() -> None:
    """Unknown capability names should fail instead of silently guessing."""
    result = _runner.invoke(app, ["tools", "inspect", "does_not_exist"])

    assert result.exit_code == 1
    assert "unknown Nimbus capability" in result.output


def test_auth_paste_imports_bulk_credentials_without_echoing_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bulk pasted dotenv credentials should be stored but never printed."""
    home = tmp_path / "home"
    monkeypatch.setenv("NIMBUS_HOME", str(home))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    payload = """OPENROUTER_API_KEY=sk-or-paste
AWS_ACCESS_KEY_ID=AKIA_PASTE
AWS_SECRET_ACCESS_KEY=paste-secret
AWS_SESSION_TOKEN=paste-token
AWS_REGION=eu-west-1
AWS_BUCKET_NAME=paste-bucket"""
    result = _runner.invoke(app, ["auth", "paste", payload])

    assert result.exit_code == 0, result.output
    config = ConfigStore(home).load()
    profile = config.profile("local")
    secrets = NimbusSecrets(home)
    assert profile.storage_container == "paste-bucket"
    assert profile.aws_region == "eu-west-1"
    assert secrets.get(profile="local", kind="openrouter_api_key") == "sk-or-paste"
    assert secrets.get(profile="local", kind="aws_access_key_id") == "AKIA_PASTE"
    assert secrets.get(profile="local", kind="aws_secret_access_key") == "paste-secret"
    assert secrets.get(profile="local", kind="aws_session_token") == "paste-token"
    assert "OPENROUTER_API_KEY" in result.output
    assert "AWS_SECRET_ACCESS_KEY" in result.output
    assert "sk-or-paste" not in result.output
    assert "paste-secret" not in result.output


def test_auth_profile_use_switches_active_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Users should be able to choose the default profile without editing JSON."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])
    _runner.invoke(
        app,
        [
            "setup",
            "remote",
            "--profile",
            "prod",
            "--base-url",
            "https://nimbus.example.com",
            "--auth",
            "hmac",
            "--signing-secret",
            "x",
        ],
    )

    use = _runner.invoke(app, ["auth", "profile", "use", "local"])
    listed = _runner.invoke(app, ["auth", "profile", "list"])

    assert use.exit_code == 0, use.output
    assert listed.exit_code == 0, listed.output
    assert ConfigStore(tmp_path).load().active_profile == "local"
    assert "local" in listed.output
    assert "prod" in listed.output


def test_auth_doctor_alias_runs_profile_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`nimbus auth doctor` should reuse the top-level doctor check."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _disable_dotenv_and_clear_storage_env(monkeypatch)
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    result = _runner.invoke(app, ["auth", "doctor"])

    assert result.exit_code == 0, result.output
    assert "all checks passed" in result.output


def test_chat_bootstraps_default_profile_from_credentials_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repo credentials.env should be enough to run local chat."""
    home = tmp_path / "home"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NIMBUS_HOME", str(home))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in (
        "OPENROUTER_API_KEY",
        "AWS_REGION",
        "NIMBUS_CONTAINER",
        "AWS_BUCKET_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "credentials.env").write_text(
        """OPENROUTER_API_KEY=sk-or-test
AWS_REGION=eu-west-1
NIMBUS_CONTAINER=env-bucket
""",
        encoding="utf-8",
    )
    fake_runtime = _FakeRuntime()
    captured: dict[str, object] = {}

    def _build_local_runtime(**kwargs: object) -> _FakeRuntime:
        captured.update(kwargs)
        return fake_runtime

    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", _build_local_runtime)

    result = _runner.invoke(app, ["chat", "hello", "--no-tools"])

    assert result.exit_code == 0
    assert "hello from local" in result.output
    config = ConfigStore(home).load()
    profile = config.profile("local")
    assert profile.storage_container == "env-bucket"
    assert profile.aws_region == "eu-west-1"
    captured_profile = captured["profile"]
    assert isinstance(captured_profile, type(profile))
    assert captured_profile.storage_container == "env-bucket"


def test_local_chat_uses_full_runtime_turn_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local CLI turns should use run_chat_turn so direct actions work."""

    @dataclass
    class _DeleteRuntime:
        turns: list[ChatTurnInput] = field(default_factory=list)

        async def run_chat_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
            self.turns.append(turn)
            return ChatTurnResult(
                request_id=turn.request_id,
                conversation_id=turn.conversation_id,
                text="I can delete `reports/old.csv`, but this is destructive.",
                outcome="confirmation_required",
                confirmation_required=True,
                suggested_next_actions=("yes, delete reports/old.csv",),
                model="nimbus-runtime",
                steps=0,
                fallback_used=False,
            )

        async def stream_chat_turn(self, _turn: ChatTurnInput) -> object:
            msg = "local CLI should not bypass run_chat_turn"
            raise AssertionError(msg)

    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    fake_runtime = _DeleteRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    setup = _runner.invoke(
        app,
        ["setup", "local", "--openrouter-key", "sk-test"],
    )
    result = _runner.invoke(app, ["chat", "delete reports/old.csv"])

    assert setup.exit_code == 0
    assert result.exit_code == 0
    assert "destructive" in result.output
    assert fake_runtime.turns[0].text == "delete reports/old.csv"


# ── nimbus task subcommands ──────────────────────────────────────────────────


from datetime import timedelta  # noqa: E402

from nimbus_runtime.domain import (  # noqa: E402
    Approval,
    ApprovalStatus,
    Artifact,
    GenerationManifest,
    ObjectPointer,
    Plan,
    PlanRiskLevel,
    PlanStatus,
    ProofReceipt,
    Task,
    TaskStatus,
    TenantIdentity,
    UploadReport,
    VerifiedActor,
)
from nimbus_runtime.learning_store import FilePolicyPatchStore  # noqa: E402
from nimbus_runtime.stacks import FileStorageStackStore  # noqa: E402
from nimbus_runtime.stores import (  # noqa: E402
    FileApprovalStore,
    FileArtifactStore,
    FilePlanStore,
    FileSessionEventStore,
    FileTaskStore,
)


def _tenant() -> TenantIdentity:
    return TenantIdentity(platform="cli", workspace_id="local")


def _actor(tenant: TenantIdentity) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id="test-user",
        auth_source="cli_local",
        bridge_id="cli",
        verified_at=datetime.now(UTC),
    )


def _make_task(
    *,
    store: FileTaskStore,
    tenant: TenantIdentity,
    task_id: str,
    status: TaskStatus = TaskStatus.DONE,
    intent: str = "Save all files",
) -> Task:
    actor = _actor(tenant)
    now = datetime.now(UTC)
    task = Task(
        task_id=task_id,
        tenant=tenant,
        session_id=f"sess-{task_id}",
        created_by=actor,
        status=status,
        intent=intent,
        source_ref=None,
        idempotency_key=f"idem-{task_id}",
        metadata={},
        failure_detail=None,
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    return store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key=task.idempotency_key,
        create=lambda: task,
    )


def _fake_profile(name: str, session_dir: str = "") -> NimbusProfile:
    return NimbusProfile(
        name=name,
        mode="local",
        storage_container="bucket",
        session_dir=session_dir or None,
    )


class _FakeStorage:
    def __init__(self, objects: list[ObjectInfo]) -> None:
        self._objects = {obj.object_name: obj for obj in objects}

    def list_files(self, _container: str, prefix: str) -> list[ObjectInfo]:
        return [
            obj for obj in self._objects.values() if obj.object_name.startswith(prefix)
        ]

    def get_file_info(self, _container: str, object_name: str) -> ObjectInfo:
        return self._objects[object_name]


class _FakePagedStorage(_FakeStorage):
    def list_files_page(
        self,
        container: str,
        prefix: str,
        max_keys: int,
        continuation_token: str = "",
    ) -> tuple[list[ObjectInfo], str]:
        objects = self.list_files(container, prefix)
        return objects[:max_keys], ""


class _FakeUnboundedOnlyStorage:
    def list_files(self, _container: str, _prefix: str) -> list[ObjectInfo]:
        msg = "provider health must not call unbounded list_files"
        raise AssertionError(msg)


def test_profile_hud_renderer_shows_bottleneck() -> None:
    """The CLI profiler can render a compact HUD-style bottleneck view."""
    trace = cli_module._ProfileTrace(enabled=True, mode="hud")
    with trace.span("cli.config.load"):
        pass
    console = Console(record=True, force_terminal=False)

    cli_module._render_profile_trace(console=console, trace=trace)

    rendered = console.export_text()
    assert "Profile HUD" in rendered
    assert "bottleneck" in rendered


def test_profile_full_renderer_labels_opaque_spans() -> None:
    """Full profiling distinguishes app-measured spans from opaque work."""
    trace = cli_module._ProfileTrace(enabled=True, mode="full")
    with trace.span("cli.config.load"):
        pass
    with trace.span("remote.http.post"):
        pass
    console = Console(record=True, force_terminal=False)

    cli_module._render_profile_trace(console=console, trace=trace)

    rendered = console.export_text()
    assert "measured" in rendered
    assert "opaque" in rendered


class _FakeRepairStorage:
    def __init__(self, source_sha256: str) -> None:
        self.source_sha256 = source_sha256
        self.copied: list[tuple[str, str, str, str]] = []

    def copy_object(
        self,
        src_container: str,
        src_key: str,
        dst_container: str,
        dst_key: str,
    ) -> ObjectInfo:
        self.copied.append((src_container, src_key, dst_container, dst_key))
        return ObjectInfo(
            object_name=dst_key,
            size_bytes=10,
            integrity=f"sha256:{self.source_sha256}",
        )

    def get_file_info(self, _container: str, object_name: str) -> ObjectInfo:
        return ObjectInfo(
            object_name=object_name,
            size_bytes=10,
            integrity=f"sha256:{self.source_sha256}",
        )


def test_task_list_no_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Task list reports 'no tasks' cleanly when the store is empty."""
    FileTaskStore(tmp_path)  # create empty store

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "list"])
    assert result.exit_code == 0
    assert "No tasks" in result.output


def test_task_list_shows_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Task list rows include task_id and intent."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="task-001",
        intent="Backup all files",
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "list"])
    assert result.exit_code == 0
    assert "task-001" in result.output
    assert "Backup all files" in result.output


def test_task_list_invalid_status_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task list --status with a bogus value exits non-zero."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "list", "--status", "not_real"])
    assert result.exit_code != 0


def test_task_inspect_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Task inspect shows task_id and intent for an existing task."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="task-inspect-001",
        status=TaskStatus.DONE,
        intent="Find duplicates",
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "inspect", "task-inspect-001"])
    assert result.exit_code == 0
    assert "task-inspect-001" in result.output
    assert "Find duplicates" in result.output


def test_task_inspect_missing_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task inspect for an unknown task exits non-zero."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "inspect", "no-such-task"])
    assert result.exit_code != 0


def test_task_events_no_events(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Task events for a task with no events in session exits zero."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(store=store, tenant=tenant, task_id="task-evt-001")

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "events", "task-evt-001"])
    assert result.exit_code == 0


def test_task_events_missing_task_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task events for a non-existent task exits non-zero."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "events", "ghost-task"])
    assert result.exit_code != 0


def test_task_artifacts_no_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task artifacts for a task with no artifacts exits zero."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(store=store, tenant=tenant, task_id="task-art-001")

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "artifacts", "task-art-001"])
    assert result.exit_code == 0
    assert "No artifacts" in result.output


def test_task_artifacts_missing_task_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task artifacts for a non-existent task exits non-zero."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "artifacts", "ghost-task"])
    assert result.exit_code != 0


def test_task_cancel_scanning_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task cancel transitions a scanning task to canceled."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="task-cancel-001",
        status=TaskStatus.SCANNING,
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "cancel", "task-cancel-001"])
    assert result.exit_code == 0

    canceled = store.get(tenant=tenant, task_id="task-cancel-001")
    assert canceled is not None
    assert canceled.status is TaskStatus.CANCELED


def test_task_cancel_done_task_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canceling a terminal task exits non-zero."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="task-cancel-done",
        status=TaskStatus.DONE,
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "cancel", "task-cancel-done"])
    assert result.exit_code != 0


def test_task_cancel_missing_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canceling a non-existent task exits non-zero."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "cancel", "ghost-task"])
    assert result.exit_code != 0


def test_task_watch_no_tasks_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task watch latest exits non-zero when no tasks exist."""
    FileTaskStore(tmp_path)  # create empty store

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "watch", "latest"])
    assert result.exit_code != 0


def test_task_watch_terminal_task_exits_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Task watch a completed task prints status and exits 0."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store, tenant=tenant, task_id="task-done-watch", status=TaskStatus.DONE
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )
    monkeypatch.setattr(time, "sleep", lambda _: None)

    result = _runner.invoke(app, ["task", "watch", "task-done-watch"])
    assert result.exit_code == 0
    assert "done" in result.output.lower() or "✅" in result.output


def test_task_watch_failed_task_exits_nonzero_and_does_not_announce_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task watch that terminates in FAILED must NOT render ui.success.

    Regression for the honesty bug where ``task_watch`` always called
    ``ui.success`` for any terminal status, producing a green
    ``task reached failed`` message and exit code 0. Real failures must
    surface through both the rendered terminal status (error styling) and
    a non-zero process exit so shell scripts can react.
    """
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="task-failed-watch",
        status=TaskStatus.FAILED,
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )
    monkeypatch.setattr(time, "sleep", lambda _: None)

    result = _runner.invoke(app, ["task", "watch", "task-failed-watch"])
    assert result.exit_code == 1, result.output
    assert "failed" in result.output.lower()
    # Critical: the success checkmark must not appear next to the failure.
    assert "✅" not in result.output


# ── Feature 1: First-run welcome panel ────────────────────────────────────


def test_chat_with_no_profile_and_no_env_shows_welcome_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold `nimbus chat` should print a friendly welcome panel.

    It should exit 0 instead of crashing with code 2 when there is no profile
    and no API key in the environment.
    """
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    # Defensively clear env vars the bootstrap might pick up
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    # Don't let the test load a real credentials.env from cwd
    monkeypatch.setattr("nimbus_cli.cli._load_dotenv_best_effort", lambda: None)

    result = _runner.invoke(app, ["chat", "hello"])

    # Welcome panel renders and exits 0
    assert result.exit_code == 0, result.output
    assert "Nimbus" in result.output
    assert "openrouter.ai/keys" in result.output
    assert "nimbus auth local" in result.output


def test_chat_with_unknown_profile_shows_welcome_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unknown explicit profile should point at the auth command.

    The requested profile name should be preserved in the welcome panel.
    """
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("nimbus_cli.cli._load_dotenv_best_effort", lambda: None)

    result = _runner.invoke(app, ["chat", "hi", "--profile", "staging"])

    assert result.exit_code == 0, result.output
    assert "staging" in result.output
    assert "nimbus auth local" in result.output


# ── Feature 2: Thinking spinner ────────────────────────────────────────────


def test_local_chat_invokes_thinking_spinner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The local-turn path should wrap the AI call in `ui.thinking`."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    fake_runtime = _FakeRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    # Patch ui.thinking to count invocations
    calls: list[str] = []
    from contextlib import contextmanager

    @contextmanager
    def fake_thinking(_console: object, text: str = "thinking…") -> Iterator[None]:
        calls.append(text)
        yield

    monkeypatch.setattr("nimbus_cli.ui.thinking", fake_thinking)

    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])
    result = _runner.invoke(app, ["chat", "hello"])

    assert result.exit_code == 0
    assert any("thinking" in c for c in calls), f"spinner not invoked: {calls}"


# ── Feature 3: Full result rendering ──────────────────────────────────────


@dataclass
class _ConfirmationFakeRuntime:
    """Returns a ChatTurnResult that includes a confirmation block."""

    turns: list[ChatTurnInput] = field(default_factory=list)

    async def run_chat_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
        from nimbus_protocol import ActionSummary, ArtifactSummary, ConfirmationDetails

        self.turns.append(turn)
        return ChatTurnResult(
            request_id=turn.request_id,
            conversation_id=turn.conversation_id,
            text="I need confirmation before deleting.",
            outcome="confirmation_required",
            confirmation_required=True,
            confirmation=ConfirmationDetails(
                action_id="act-123",
                kind="delete_file",
                prompt="Confirm delete of reports/old.csv",
                expected_reply="yes, delete reports/old.csv",
                expires_at="2026-05-17T14:33:00Z",
            ),
            actions=(
                ActionSummary(
                    action_id="act-123",
                    kind="delete_file",
                    status="pending",
                    target={"container": "my-bucket", "object_name": "reports/old.csv"},
                ),
            ),
            artifacts=(
                ArtifactSummary(
                    artifact_id="art-abc",
                    kind="delete_report",
                ),
            ),
            model="nimbus-runtime",
            steps=0,
            fallback_used=False,
        )


def test_local_chat_renders_confirmation_and_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI should render more than plain response text.

    Confirmation prompts, action lines, and artifact links are part of the
    runtime turn result and should be visible in the terminal.
    """
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    monkeypatch.setattr(
        "nimbus_cli.cli.build_local_runtime",
        lambda **_: _ConfirmationFakeRuntime(),
    )

    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])
    result = _runner.invoke(app, ["chat", "delete reports/old.csv"])

    assert result.exit_code == 0, result.output
    # The original text is shown
    assert "I need confirmation before deleting" in result.output
    # The exact confirmation phrase appears so users can copy-paste it
    assert "yes, delete reports/old.csv" in result.output
    # Action line appears
    assert "delete_file" in result.output
    # Artifact id appears
    assert "art-abc" in result.output


# ── Feature 6: `nimbus model` interactive picker ───────────────────────────


def test_model_with_argument_updates_local_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passing a model ID directly should skip the picker and save it."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    result = _runner.invoke(app, ["model", "anthropic/claude-3-5-sonnet"])

    assert result.exit_code == 0, result.output
    assert "anthropic/claude-3-5-sonnet" in result.output

    # Persisted in config
    from nimbus_cli.config import ConfigStore

    config = ConfigStore().load()
    assert config.profile(None).model == "anthropic/claude-3-5-sonnet"


def test_model_without_argument_uses_picker_in_non_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """In CI/non-tty environments, the picker falls back to a numbered prompt."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    # CliRunner gives a non-tty Console, so we expect the numbered fallback.
    # Choose option 1 (the default free model).
    result = _runner.invoke(app, ["model"], input="1\n")

    assert result.exit_code == 0, result.output


def test_model_with_unknown_profile_prints_welcome_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown model profile names should reuse the first-run welcome panel."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")

    result = _runner.invoke(app, ["model", "x", "--profile", "ghost"])
    assert result.exit_code == 0, result.output
    assert "ghost" in result.output


def test_model_against_remote_profile_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model is set server-side for remote profiles — CLI should refuse."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(
        app,
        [
            "setup",
            "remote",
            "--profile",
            "prod",
            "--base-url",
            "https://x.example.com",
            "--auth",
            "hmac",
            "--signing-secret",
            "x",
        ],
    )

    result = _runner.invoke(app, ["model", "openai/gpt-4o", "--profile", "prod"])

    assert result.exit_code != 0
    assert "remote" in result.output.lower()


def test_model_picker_supports_vim_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The interactive picker should handle j/k/l without readline echo loops."""
    from rich.console import Console

    from nimbus_cli import picker, ui

    keys = iter(["j", "l"])
    monkeypatch.setattr(picker, "_read_one_key", lambda: next(keys))
    console = Console(file=StringIO(), force_terminal=True, width=100)

    selected = picker.select_one(
        [
            ui.SelectOption(label="first", value="first"),
            ui.SelectOption(label="second", value="second"),
        ],
        title="Switch model",
        console=console,
    )

    assert selected == "second"


def test_model_picker_supports_arrow_navigation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ANSI arrow sequences should select items without optional readchar."""
    from rich.console import Console

    from nimbus_cli import picker, ui

    keys = iter(["\x1b[B", "\r"])
    monkeypatch.setattr(picker, "_read_one_key", lambda: next(keys))
    console = Console(file=StringIO(), force_terminal=True, width=100)

    selected = picker.select_one(
        [
            ui.SelectOption(label="first", value="first"),
            ui.SelectOption(label="second", value="second"),
        ],
        title="Switch model",
        console=console,
    )

    assert selected == "second"


# ── Bonus: nimbus doctor health check ──────────────────────────────────────


def test_doctor_local_profile_with_complete_setup_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fully-configured local profile should exit 0 with all green checks."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _disable_dotenv_and_clear_storage_env(monkeypatch)
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    result = _runner.invoke(app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert "all checks passed" in result.output
    # No DANGER markers
    assert "missing" not in result.output.lower()


def test_doctor_local_profile_without_openrouter_key_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing API key should surface as a doctor failure with exit 1."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("nimbus_cli.cli._load_dotenv_best_effort", lambda: None)
    # Create a profile but skip the API key by mutating the config directly.
    from nimbus_cli.config import ConfigStore, NimbusProfile

    store = ConfigStore()
    store.save(store.load().with_profile(NimbusProfile(name="local", mode="local")))

    result = _runner.invoke(app, ["doctor"])

    assert result.exit_code == 1, result.output
    assert "missing" in result.output.lower() or "failed" in result.output.lower()


def test_doctor_with_unknown_profile_shows_welcome_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Doctor against a non-existent profile shows the welcome panel."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    for name in _DOTENV_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("nimbus_cli.cli._load_dotenv_best_effort", lambda: None)

    result = _runner.invoke(app, ["doctor", "--profile", "ghost"])

    assert result.exit_code != 0
    assert "ghost" in result.output


def test_doctor_remote_profile_probes_reachability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remote profiles should attempt a HEAD/GET on /health."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(
        app,
        [
            "setup",
            "remote",
            "--profile",
            "prod",
            "--base-url",
            "https://nimbus.example.com",
            "--auth",
            "hmac",
            "--signing-secret",
            "x",
        ],
    )

    # Stub httpx.get to return a healthy response so the test doesn't hit the network.
    class _StubResponse:
        status_code = 200

    monkeypatch.setattr("httpx.get", lambda *_, **__: _StubResponse())

    result = _runner.invoke(app, ["doctor", "--profile", "prod"])

    assert result.exit_code == 0, result.output
    assert "nimbus.example.com" in result.output


def test_provider_health_json_writes_typed_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider health probes should persist machine-readable evidence."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local", str(tmp_path)),
    )
    monkeypatch.setattr(
        "nimbus_cli.cli._build_storage_for_profile",
        lambda *_: _FakePagedStorage(
            [
                ObjectInfo(
                    object_name="team/a.txt",
                    size_bytes=10,
                    integrity="sha256:a",
                )
            ]
        ),
    )

    result = _runner.invoke(app, ["provider", "health", "--prefix", "team/", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["report"]["status"] == "healthy"
    assert any(
        "AWS Service Health Dashboard" in item
        for item in payload["report"]["advisory_context"]
    )
    assert payload["artifact"]["kind"] == "provider_health"
    assert payload["artifact"]["payload_digest"].startswith("sha256:")


def test_provider_capabilities_json_reports_structural_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider capability discovery gives users a provider-readiness demo."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local", str(tmp_path)),
    )
    monkeypatch.setattr(
        "nimbus_cli.cli._build_storage_for_profile",
        lambda *_: _FakePagedStorage([]),
    )

    result = _runner.invoke(app, ["provider", "capabilities", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["provider_name"] == "_FakePagedStorage"
    assert payload["supports"]["pagination"] is True
    assert payload["supports"]["copy"] is False


def test_provider_health_without_bounded_probe_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI reports degraded health instead of doing an unbounded list."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local", str(tmp_path)),
    )
    monkeypatch.setattr(
        "nimbus_cli.cli._build_storage_for_profile",
        lambda *_: _FakeUnboundedOnlyStorage(),
    )

    result = _runner.invoke(app, ["provider", "health", "--prefix", "team/"])

    assert result.exit_code == 1, result.output
    assert "degraded" in result.output
    assert "ProviderPagination" in result.output


# ── Bonus: REPL slash commands ─────────────────────────────────────────────


def test_repl_help_lists_known_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/help inside the REPL shows the slash-command grid."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    fake_runtime = _FakeRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    # /help, then /exit — the REPL should print help and then leave.
    result = _runner.invoke(app, [], input="/help\n/exit\n")

    assert result.exit_code == 0, result.output
    assert "/help" in result.output
    assert "/model" in result.output
    assert "/clear" in result.output
    # /help shouldn't have invoked the runtime
    assert fake_runtime.turns == []


def test_repl_model_slash_with_argument_updates_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/model openai/gpt-4o` inside the REPL should persist."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    fake_runtime = _FakeRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    _runner.invoke(app, [], input="/model openai/gpt-4o\n/exit\n")

    from nimbus_cli.config import ConfigStore

    config = ConfigStore().load()
    assert config.profile(None).model == "openai/gpt-4o"


def test_repl_unknown_slash_warns_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown slash commands should produce a warning, not a crash."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    fake_runtime = _FakeRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    result = _runner.invoke(app, [], input="/bogus\n/exit\n")

    assert result.exit_code == 0
    assert "unknown" in result.output.lower()
    assert fake_runtime.turns == []


def test_repl_exit_slash_leaves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/exit should leave the REPL without sending a model turn."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    fake_runtime = _FakeRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    result = _runner.invoke(app, [], input="/exit\n")

    assert result.exit_code == 0
    assert fake_runtime.turns == []


def test_repl_new_slash_rotates_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`/new` should start a fresh session and report its id."""
    monkeypatch.setenv("NIMBUS_HOME", str(tmp_path))
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    _runner.invoke(app, ["setup", "local", "--openrouter-key", "sk-test"])

    fake_runtime = _FakeRuntime()
    monkeypatch.setattr("nimbus_cli.cli.build_local_runtime", lambda **_: fake_runtime)

    result = _runner.invoke(app, [], input="/new\n/exit\n")

    assert result.exit_code == 0
    assert "new session" in result.output.lower()


# ── Helpers shared by approve / plan / artifact tests ──────────────────────


def _make_plan(
    *,
    store: FilePlanStore,
    tenant: TenantIdentity,
    plan_id: str,
    status: PlanStatus = PlanStatus.PROPOSED,
    task_id: str = "task-001",
) -> Plan:
    """Insert a minimal Plan into the store and return it."""
    now = datetime.now(UTC)
    plan = Plan(
        plan_id=plan_id,
        tenant=tenant,
        session_id=f"sess-{plan_id}",
        task_id=task_id,
        action_id=None,
        created_by=_actor(tenant),
        status=status,
        risk_level=PlanRiskLevel.SMALL_WRITE,
        title="Test Plan",
        summary="Testing plan operations",
        target=None,
        estimated_count=3,
        estimated_bytes=None,
        idempotency_key=f"idem-{plan_id}",
        metadata={},
        created_at=now,
        updated_at=now,
        expires_at=None,
    )
    return store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key=plan.idempotency_key,
        create=lambda: plan,
    )


def _make_approval(
    *,
    store: FileApprovalStore,
    tenant: TenantIdentity,
    approval_id: str,
    task_id: str,
    status: ApprovalStatus = ApprovalStatus.PENDING,
) -> Approval:
    """Insert a minimal Approval into the store and return it."""
    now = datetime.now(UTC)
    approval = Approval(
        approval_id=approval_id,
        tenant=tenant,
        session_id=f"sess-{approval_id}",
        task_id=task_id,
        plan_id=None,
        action_id=None,
        requested_by=_actor(tenant),
        required_actor_id="cli",
        allowed_actor_ids=("cli",),
        status=status,
        risk_level=PlanRiskLevel.SMALL_WRITE,
        exact_target="test-target",
        reason="testing",
        idempotency_key=f"idem-{approval_id}",
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    return store.create_or_get_by_idempotency(
        tenant=tenant,
        idempotency_key=approval.idempotency_key,
        create=lambda: approval,
    )


def _create_duplicate_manifest(*, tmp_path: Path, tenant: TenantIdentity) -> Artifact:
    """Create a generation manifest artifact with duplicate object hashes."""
    return _create_manifest_artifact(
        tmp_path=tmp_path,
        tenant=tenant,
        artifact_id="art-cleanup-manifest",
        container="bucket",
        prefix="docs/",
        objects=(
            ("docs/a.txt", "a" * 64),
            ("docs/copy/a.txt", "a" * 64),
        ),
    )


def _create_manifest_artifact(
    *,
    tmp_path: Path,
    tenant: TenantIdentity,
    artifact_id: str,
    container: str,
    prefix: str,
    objects: tuple[tuple[str, str], ...],
) -> Artifact:
    """Create a generation manifest artifact for CLI tests."""
    now = datetime.now(UTC)
    manifest = Artifact(
        artifact_id=artifact_id,
        tenant=tenant,
        session_id="sess-cleanup",
        action_id=None,
        kind="manifest",
        uri=None,
        payload=GenerationManifest(
            root_id="root-cleanup",
            generation_id="gen-cleanup",
            manifest_digest="sha256:cleanup",
            provider="s3",
            container=container,
            prefix=prefix,
            objects=tuple(
                ObjectPointer(
                    provider="s3",
                    container=container,
                    object_name=object_name,
                    content_sha256=digest,
                    size_bytes=10,
                )
                for object_name, digest in objects
            ),
            object_count=len(objects),
            total_bytes=10 * len(objects),
            partial=False,
            created_at=now,
        ),
        created_at=now,
    )
    return FileArtifactStore(tmp_path).create(artifact=manifest, actor=_actor(tenant))


def _make_artifact(
    *,
    store: FileArtifactStore,
    tenant: TenantIdentity,
    artifact_id: str,
) -> Artifact:
    """Insert a minimal Artifact into the store and return it."""
    artifact = Artifact(
        artifact_id=artifact_id,
        tenant=tenant,
        session_id=f"sess-{artifact_id}",
        action_id=None,
        kind="upload_report",
        uri=None,
        payload=UploadReport(
            remote_path="docs/test.txt",
            filename="test.txt",
            size_bytes=512,
            sha256_hex="abc123",
        ),
        created_at=datetime.now(UTC),
    )
    return store.create(artifact=artifact, actor=None)


def _make_proof_receipt(
    *,
    store: FileArtifactStore,
    tenant: TenantIdentity,
    receipt_id: str,
    linked: Artifact,
) -> Artifact:
    """Insert a proof receipt linked to an existing artifact."""
    now = datetime.now(UTC)
    receipt = ProofReceipt(
        receipt_id=receipt_id,
        tenant=tenant,
        subject="upload_report",
        outcome="succeeded",
        summary="Test receipt",
        task_id=None,
        action_id=linked.action_id,
        manifest_artifact_id=None,
        verifier_artifact_id=None,
        linked_artifact_ids=(linked.artifact_id,),
        artifact_digests={linked.artifact_id: linked.payload_digest or ""},
        session_id=linked.session_id,
        event_range_start=None,
        event_range_end=None,
        policy_version="runtime-default-v1",
        idempotency_key=None,
        next_steps=("Inspect linked evidence.",),
        created_at=now,
    )
    return store.create(
        artifact=Artifact(
            artifact_id=receipt_id,
            tenant=tenant,
            session_id=linked.session_id,
            action_id=linked.action_id,
            kind="proof_receipt",
            uri=None,
            payload=receipt,
            created_at=now,
        ),
        actor=None,
    )


# ── nimbus task approve ──────────────────────────────────────────────────────


def test_task_approve_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approve a task in awaiting_approval with a matching pending approval."""
    task_store = FileTaskStore(tmp_path)
    approval_store = FileApprovalStore(tmp_path)
    tenant = _tenant()

    _make_task(
        store=task_store,
        tenant=tenant,
        task_id="task-app-001",
        status=TaskStatus.AWAITING_APPROVAL,
    )
    _make_approval(
        store=approval_store,
        tenant=tenant,
        approval_id="apv-001",
        task_id="task-app-001",
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "approve", "task-app-001"])

    assert result.exit_code == 0, result.output
    assert "Approved" in result.output


def test_task_approve_wrong_status_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving a task that is not awaiting_approval exits non-zero."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store, tenant=tenant, task_id="task-app-done", status=TaskStatus.DONE
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "approve", "task-app-done"])

    assert result.exit_code != 0


def test_task_approve_missing_task_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving a non-existent task exits non-zero."""
    FileTaskStore(tmp_path)  # create empty store

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "approve", "ghost-task"])

    assert result.exit_code != 0


def test_task_approve_no_pending_approval_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approving a task with no pending approval record exits non-zero."""
    task_store = FileTaskStore(tmp_path)
    FileApprovalStore(tmp_path)  # empty approval store
    tenant = _tenant()

    _make_task(
        store=task_store,
        tenant=tenant,
        task_id="task-app-noapv",
        status=TaskStatus.AWAITING_APPROVAL,
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "approve", "task-app-noapv"])

    assert result.exit_code != 0


# ── nimbus task retry ────────────────────────────────────────────────────────


def test_task_retry_failed_task_prints_guidance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying a FAILED task should echo the intent and print the new task ID."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="task-retry-001",
        status=TaskStatus.FAILED,
        intent="Compress old reports",
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "retry", "task-retry-001"])

    assert result.exit_code == 0, result.output
    assert "Compress old reports" in result.output
    # New task ID must appear in output.
    assert "New task:" in result.output


def test_task_retry_creates_pending_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying a FAILED task must persist a new PENDING task in the store."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="task-retry-src",
        status=TaskStatus.FAILED,
        intent="Archive Q1 files",
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "retry", "task-retry-src"])

    assert result.exit_code == 0, result.output
    # Exactly two tasks should now exist: the original (FAILED) + the retry (PENDING).
    all_tasks = store.list_for_tenant(tenant=tenant)
    assert len(all_tasks) == 2
    retry_tasks = [t for t in all_tasks if t.status is TaskStatus.CREATED]
    assert len(retry_tasks) == 1
    new_task = retry_tasks[0]
    assert new_task.intent == "Archive Q1 files"
    assert new_task.metadata.get("retried_from") == "task-retry-src"


def test_task_retry_canceled_task_creates_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying a CANCELED task should also create a new PENDING task."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="task-retry-cancel",
        status=TaskStatus.CANCELED,
        intent="Delete temp files",
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "retry", "task-retry-cancel"])

    assert result.exit_code == 0, result.output
    pending = [
        t
        for t in store.list_for_tenant(tenant=tenant)
        if t.status is TaskStatus.CREATED
    ]
    assert len(pending) == 1
    assert pending[0].intent == "Delete temp files"


def test_task_retry_done_task_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying a DONE task (which is not retryable) exits non-zero."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store, tenant=tenant, task_id="task-retry-done", status=TaskStatus.DONE
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "retry", "task-retry-done"])

    assert result.exit_code != 0


def test_task_retry_missing_task_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retrying a non-existent task exits non-zero."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["task", "retry", "ghost-task"])

    assert result.exit_code != 0


# ── nimbus root / generation ────────────────────────────────────────────────


def test_root_protect_and_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root protect creates a durable protected root visible in CLI listing."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    created = _runner.invoke(
        app,
        ["root", "protect", "--prefix", "docs", "--name", "Docs"],
    )
    listed = _runner.invoke(app, ["root", "list", "--json"])

    assert created.exit_code == 0, created.output
    assert listed.exit_code == 0, listed.output
    assert "root-" in listed.output
    assert "docs/" in listed.output


def test_generation_create_list_and_diff_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generation commands create manifest/proof evidence and stable JSON diffs."""
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )
    monkeypatch.setattr(
        "nimbus_cli.cli._build_storage_for_profile",
        lambda *_args, **_kwargs: _FakeStorage(
            [
                ObjectInfo(
                    object_name="docs/a.txt",
                    size_bytes=1,
                    metadata={"sha256": "a" * 64},
                )
            ]
        ),
    )
    protected = _runner.invoke(app, ["root", "protect", "--prefix", "docs"])
    assert protected.exit_code == 0, protected.output
    root_id = next(
        part for part in protected.output.split() if part.startswith("root-")
    )

    created = _runner.invoke(app, ["generation", "create", root_id, "--json"])
    listed = _runner.invoke(app, ["generation", "list", root_id, "--json"])
    listed_all = _runner.invoke(app, ["generation", "list", "--json"])
    manifest_list = _runner.invoke(app, ["manifest", "list", "--json"])

    assert created.exit_code == 0, created.output
    assert listed.exit_code == 0, listed.output
    assert listed_all.exit_code == 0, listed_all.output
    assert manifest_list.exit_code == 0, manifest_list.output
    assert "proof_artifact" in created.output
    assert "manifest_digest" in listed.output
    assert "manifest_artifact_id" in listed_all.output
    assert "manifest" in manifest_list.output

    generations = FileGenerationStore(tmp_path).list_for_root(
        tenant=_tenant(),
        root_id=root_id,
    )
    assert len(generations) == 1
    verified = _runner.invoke(
        app,
        ["verify", generations[0].manifest_artifact_id],
    )
    blamed = _runner.invoke(app, ["blame", "docs/a.txt", "--json"])
    migration = _runner.invoke(
        app,
        [
            "migration",
            "evaluate",
            root_id,
            "--candidate-container",
            "replica-bucket",
            "--candidate-prefix",
            "replica/docs",
            "--json",
        ],
    )
    healed = _runner.invoke(app, ["heal", "root", root_id, "--json"])
    diff = _runner.invoke(
        app,
        [
            "generation",
            "diff",
            generations[0].generation_id,
            generations[0].generation_id,
            "--json",
        ],
    )

    assert verified.exit_code == 0, verified.output
    assert "no drift detected" in verified.output.lower()
    assert blamed.exit_code == 0, blamed.output
    assert '"object_name": "docs/a.txt"' in blamed.output
    assert migration.exit_code == 0, migration.output
    assert "migration_decision_packet" in migration.output
    assert healed.exit_code == 0, healed.output
    assert '"health_score": 100' in healed.output
    assert diff.exit_code == 0, diff.output
    assert '"unchanged_count": 1' in diff.output


def test_heal_replica_evaluates_missing_replica_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replica healing CLI reports S3-only repair proposals."""
    tenant = _tenant()
    _create_manifest_artifact(
        tmp_path=tmp_path,
        tenant=tenant,
        artifact_id="art-source-manifest",
        container="source-bucket",
        prefix="docs/",
        objects=(("docs/a.txt", "a" * 64),),
    )
    _create_manifest_artifact(
        tmp_path=tmp_path,
        tenant=tenant,
        artifact_id="art-replica-manifest",
        container="replica-bucket",
        prefix="replica/",
        objects=(),
    )
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(
        app,
        [
            "heal",
            "replica",
            "art-source-manifest",
            "--replica-manifest",
            "art-replica-manifest",
            "--root",
            "root-demo",
            "--allow-missing-repair",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"status": "repairable"' in result.output
    assert '"missing_replica_count": 1' in result.output


def test_heal_replica_apply_writes_repair_receipt_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Healing apply copies missing replicas and persists receipt evidence."""
    tenant = _tenant()
    _create_manifest_artifact(
        tmp_path=tmp_path,
        tenant=tenant,
        artifact_id="art-source-apply",
        container="source-bucket",
        prefix="docs/",
        objects=(("docs/a.txt", "a" * 64),),
    )
    _create_manifest_artifact(
        tmp_path=tmp_path,
        tenant=tenant,
        artifact_id="art-replica-apply",
        container="replica-bucket",
        prefix="replica/",
        objects=(),
    )
    storage = _FakeRepairStorage(source_sha256="a" * 64)
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local", str(tmp_path)),
    )
    monkeypatch.setattr(
        "nimbus_cli.cli._build_storage_for_profile",
        lambda *_: storage,
    )

    result = _runner.invoke(
        app,
        [
            "heal",
            "replica",
            "art-source-apply",
            "--replica-manifest",
            "art-replica-apply",
            "--root",
            "root-demo",
            "--allow-missing-repair",
            "--apply",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["applied"] is True
    assert payload["repair_receipts"][0]["outcome"] == "repaired"
    assert payload["artifacts"][0]["kind"] == "repair_receipt"
    assert storage.copied == [
        ("source-bucket", "docs/a.txt", "replica-bucket", "replica/a.txt")
    ]


# ── nimbus plan show ─────────────────────────────────────────────────────────


def test_plan_show_existing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan show displays plan_id, title, and status."""
    store = FilePlanStore(tmp_path)
    tenant = _tenant()
    _make_plan(store=store, tenant=tenant, plan_id="plan-show-001")

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["plan", "show", "plan-show-001"])

    assert result.exit_code == 0, result.output
    assert "plan-show-001" in result.output
    assert "Test Plan" in result.output


def test_plan_list_json_and_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan list/diff expose stable review surfaces for demos and CI."""
    store = FilePlanStore(tmp_path)
    tenant = _tenant()
    _make_plan(store=store, tenant=tenant, plan_id="plan-list-001")

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    listed = _runner.invoke(app, ["plan", "list", "--json"])
    diff = _runner.invoke(app, ["plan", "diff", "plan-list-001", "--json"])

    assert listed.exit_code == 0, listed.output
    assert diff.exit_code == 0, diff.output
    assert "plan-list-001" in listed.output
    assert "approval_binding" in diff.output


def test_plan_reject_json_transitions_proposed_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan reject should fail closed through the store transition."""
    store = FilePlanStore(tmp_path)
    tenant = _tenant()
    _make_plan(store=store, tenant=tenant, plan_id="plan-reject-001")

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["plan", "reject", "plan-reject-001", "--json"])

    assert result.exit_code == 0, result.output
    assert '"status": "rejected"' in result.output
    updated = store.get(tenant=tenant, plan_id="plan-reject-001")
    assert updated is not None
    assert updated.status is PlanStatus.REJECTED


def test_plan_cleanup_approve_supersedes_siblings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup candidates should approve one winner and supersede siblings."""
    tenant = _tenant()
    now = datetime.now(UTC)
    artifact_store = FileArtifactStore(tmp_path)
    manifest = Artifact(
        artifact_id="art-cleanup-manifest",
        tenant=tenant,
        session_id="sess-cleanup",
        action_id=None,
        kind="manifest",
        uri=None,
        payload=GenerationManifest(
            root_id="root-cleanup",
            generation_id="gen-cleanup",
            manifest_digest="sha256:cleanup",
            provider="s3",
            container="bucket",
            prefix="docs/",
            objects=(
                ObjectPointer(
                    provider="s3",
                    container="bucket",
                    object_name="docs/a.txt",
                    content_sha256="a" * 64,
                    size_bytes=10,
                ),
                ObjectPointer(
                    provider="s3",
                    container="bucket",
                    object_name="docs/copy/a.txt",
                    content_sha256="a" * 64,
                    size_bytes=10,
                ),
            ),
            object_count=2,
            total_bytes=20,
            partial=False,
            created_at=now,
        ),
        created_at=now,
    )
    artifact_store.create(artifact=manifest, actor=_actor(tenant))

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    generated = _runner.invoke(
        app,
        ["plan", "cleanup", "art-cleanup-manifest", "--json"],
    )
    assert generated.exit_code == 0, generated.output
    plans = FilePlanStore(tmp_path).list_for_tenant(tenant=tenant)
    assert len(plans) == 3
    selected = next(
        plan
        for plan in plans
        if plan.metadata.get("candidate_strategy") == "archive_before_delete"
    )

    approved = _runner.invoke(app, ["plan", "approve", selected.plan_id, "--json"])

    assert approved.exit_code == 0, approved.output
    updated = FilePlanStore(tmp_path).list_for_tenant(tenant=tenant)
    statuses = {plan.metadata["candidate_strategy"]: plan.status for plan in updated}
    assert statuses["archive_before_delete"] is PlanStatus.APPROVED
    assert statuses["delete_extra_copies"] is PlanStatus.SUPERSEDED
    assert statuses["report_only"] is PlanStatus.SUPERSEDED


def test_stack_cli_propose_approve_diff_and_report_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stack commands expose durable cleanup changes through the CLI."""
    tenant = _tenant()
    manifest = _create_duplicate_manifest(tmp_path=tmp_path, tenant=tenant)
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )
    generated = _runner.invoke(
        app,
        ["plan", "cleanup", manifest.artifact_id, "--json"],
    )
    assert generated.exit_code == 0, generated.output
    plans = FilePlanStore(tmp_path).list_for_tenant(tenant=tenant)
    plan = next(
        item for item in plans if item.metadata["candidate_strategy"] == "report_only"
    )

    proposed = _runner.invoke(app, ["stack", "propose", plan.plan_id, "--json"])
    assert proposed.exit_code == 0, proposed.output
    assert '"operation": "report_duplicate"' in proposed.output
    stack_id = next(
        iter(FileStorageStackStore(tmp_path).list_for_tenant(tenant=tenant))
    ).stack_id

    diff = _runner.invoke(app, ["stack", "diff", stack_id, "--json"])
    approved = _runner.invoke(app, ["stack", "approve", stack_id, "--json"])
    applied = _runner.invoke(app, ["stack", "apply", stack_id, "--json"])

    assert diff.exit_code == 0, diff.output
    assert '"stack_id":' in diff.output
    assert approved.exit_code == 0, approved.output
    assert '"status": "approved"' in approved.output
    assert applied.exit_code == 0, applied.output
    assert '"status": "applied"' in applied.output


def test_policy_patch_cli_propose_show_accept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Policy patch CLI persists learning-derived proposals."""
    tenant = _tenant()
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    proposed = _runner.invoke(
        app,
        [
            "policy",
            "patch",
            "propose",
            "--capability",
            "delete_file",
            "--evidence",
            "artifact:proof-1",
            "--json",
        ],
    )
    assert proposed.exit_code == 0, proposed.output
    proposal = FilePolicyPatchStore(tmp_path).list_for_tenant(tenant=tenant)[0]

    shown = _runner.invoke(
        app,
        ["policy", "patch", "show", proposal.proposal_id, "--json"],
    )
    accepted = _runner.invoke(
        app,
        ["policy", "patch", "accept", proposal.proposal_id, "--json"],
    )

    assert shown.exit_code == 0, shown.output
    assert proposal.proposal_id in shown.output
    assert accepted.exit_code == 0, accepted.output
    assert '"status": "accepted"' in accepted.output


def test_trace_export_cli_includes_events_and_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Trace export gives CI and operators a replayable evidence envelope."""
    tenant = _tenant()
    actor = _actor(tenant)
    FileSessionEventStore(tmp_path).append(
        tenant=tenant,
        session_id="sess-trace",
        event_type="demo_event",
        actor=actor,
        payload={"status": "ok"},
    )
    FileArtifactStore(tmp_path).create(
        artifact=Artifact(
            artifact_id="art-trace",
            tenant=tenant,
            session_id="sess-trace",
            action_id=None,
            kind="upload_report",
            uri=None,
            payload=UploadReport(
                remote_path="docs/test.txt",
                filename="test.txt",
                size_bytes=1,
                sha256_hex="a" * 64,
            ),
            created_at=datetime.now(UTC),
        ),
        actor=actor,
    )
    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["trace", "export", "sess-trace", "--json"])

    assert result.exit_code == 0, result.output
    assert '"content_digest": "sha256:' in result.output
    assert '"event_type": "demo_event"' in result.output
    assert '"artifact_id": "art-trace"' in result.output


def test_spec_check_cli_reports_runtime_status_domains() -> None:
    """The executable status spec is visible as a deterministic CLI check."""
    result = _runner.invoke(app, ["spec", "check", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["checked"] is True
    assert payload["content_digest"].startswith("sha256:")
    assert "action" in payload["spec"]["statuses"]
    assert {
        "formal/tla/NimbusActionLedger.tla",
        "formal/lean/Nimbus/ActionLedger.lean",
    }.issubset({entry["path"] for entry in payload["formal_specs"]})


def test_plan_show_missing_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan show for an unknown plan exits non-zero."""
    FilePlanStore(tmp_path)  # create empty store

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["plan", "show", "ghost-plan"])

    assert result.exit_code != 0


# ── nimbus plan apply ────────────────────────────────────────────────────────


def test_plan_apply_with_yes_flag_approves_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan apply --yes transitions the plan to approved without prompting."""
    plan_store = FilePlanStore(tmp_path)
    tenant = _tenant()
    _make_plan(
        store=plan_store,
        tenant=tenant,
        plan_id="plan-apply-001",
        status=PlanStatus.PROPOSED,
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["plan", "apply", "plan-apply-001", "--yes"])

    assert result.exit_code == 0, result.output
    assert "approved" in result.output.lower()

    updated = plan_store.get(tenant=tenant, plan_id="plan-apply-001")
    assert updated is not None
    assert updated.status is PlanStatus.APPROVED


def test_plan_apply_non_proposed_plan_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan apply on an already-approved plan exits non-zero."""
    plan_store = FilePlanStore(tmp_path)
    tenant = _tenant()
    _make_plan(
        store=plan_store,
        tenant=tenant,
        plan_id="plan-apply-done",
        status=PlanStatus.APPROVED,
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["plan", "apply", "plan-apply-done", "--yes"])

    assert result.exit_code != 0


def test_plan_apply_missing_plan_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plan apply for a non-existent plan exits non-zero."""
    FilePlanStore(tmp_path)  # create empty store

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["plan", "apply", "ghost-plan", "--yes"])

    assert result.exit_code != 0


# ── nimbus artifact show ─────────────────────────────────────────────────────


def test_artifact_show_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Artifact show displays artifact_id, kind, and session."""
    store = FileArtifactStore(tmp_path)
    tenant = _tenant()
    _make_artifact(store=store, tenant=tenant, artifact_id="art-show-001")

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["artifact", "show", "art-show-001"])

    assert result.exit_code == 0, result.output
    assert "art-show-001" in result.output
    assert "upload_report" in result.output
    assert "sha256:" in result.output


def test_artifact_show_missing_exits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact show for an unknown artifact exits non-zero."""
    FileArtifactStore(tmp_path)  # create empty store

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["artifact", "show", "ghost-artifact"])

    assert result.exit_code != 0


# ── nimbus evidence ─────────────────────────────────────────────────────────


def test_evidence_export_preview_and_compact_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Evidence commands export payload bytes, preview, and compact records."""
    store = FileArtifactStore(tmp_path)
    tenant = _tenant()
    _make_artifact(store=store, tenant=tenant, artifact_id="art-evidence-001")
    evidence_root = tmp_path / "evidence-root"

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    before = _runner.invoke(
        app,
        [
            "evidence",
            "preview",
            "art-evidence-001",
            "--root",
            str(evidence_root),
            "--json",
        ],
    )
    export = _runner.invoke(
        app,
        [
            "evidence",
            "export",
            "art-evidence-001",
            "--root",
            str(evidence_root),
            "--json",
        ],
    )
    after = _runner.invoke(
        app,
        [
            "evidence",
            "preview",
            "art-evidence-001",
            "--root",
            str(evidence_root),
            "--json",
        ],
    )
    compact = _runner.invoke(
        app,
        [
            "evidence",
            "compact",
            "art-evidence-001",
            "--root",
            str(evidence_root),
            "--json",
        ],
    )

    assert before.exit_code == 0, before.output
    assert export.exit_code == 0, export.output
    assert after.exit_code == 0, after.output
    assert compact.exit_code == 0, compact.output
    assert json.loads(before.output)["preview"]["evidence_available"] is False
    export_payload = json.loads(export.output)
    assert export_payload["record"]["verification_status"] == "verified"
    assert json.loads(after.output)["preview"]["evidence_available"] is True
    compact_payload = json.loads(compact.output)
    assert compact_payload["bundle"]["artifact_count"] == 1
    assert compact_payload["bundle"]["verification_status"] == "verified"


# ── nimbus proof show ────────────────────────────────────────────────────────


def test_proof_show_latest_json_validates_linked_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proof show latest validates linked artifacts and emits stable JSON."""
    store = FileArtifactStore(tmp_path)
    tenant = _tenant()
    linked = _make_artifact(store=store, tenant=tenant, artifact_id="art-proof-link")
    _make_proof_receipt(
        store=store,
        tenant=tenant,
        receipt_id="rec-proof-001",
        linked=linked,
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["proof", "show", "latest", "--json"])

    assert result.exit_code == 0, result.output
    assert '"valid": true' in result.output
    assert "rec-proof-001" in result.output
    assert "art-proof-link" in result.output


def test_proof_show_missing_link_exits_with_next_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proof validation fails closed when linked evidence is missing."""
    store = FileArtifactStore(tmp_path)
    tenant = _tenant()
    linked = _make_artifact(store=store, tenant=tenant, artifact_id="art-link")
    receipt = _make_proof_receipt(
        store=store,
        tenant=tenant,
        receipt_id="rec-missing-link",
        linked=linked,
    )
    missing_payload = receipt.payload
    assert isinstance(missing_payload, ProofReceipt)
    store.create(
        artifact=Artifact(
            artifact_id="rec-missing-link-2",
            tenant=tenant,
            session_id=receipt.session_id,
            action_id=None,
            kind="proof_receipt",
            uri=None,
            payload=replace(
                missing_payload,
                receipt_id="rec-missing-link-2",
                linked_artifact_ids=("art-does-not-exist",),
            ),
            created_at=datetime.now(UTC),
        ),
        actor=None,
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    result = _runner.invoke(app, ["proof", "show", "rec-missing-link-2"])

    assert result.exit_code == 1
    assert "missing" in result.output
    assert "Re-run the task" in result.output


# ── FileArtifactStore.get ────────────────────────────────────────────────────


def test_artifact_store_get_returns_artifact(tmp_path: Path) -> None:
    """FileArtifactStore.get returns an artifact when it exists."""
    store = FileArtifactStore(tmp_path)
    tenant = _tenant()
    created = _make_artifact(store=store, tenant=tenant, artifact_id="art-get-001")

    found = store.get(tenant=tenant, artifact_id="art-get-001")

    assert found is not None
    assert found.artifact_id == created.artifact_id
    assert found.kind == "upload_report"


def test_artifact_store_get_returns_none_when_missing(tmp_path: Path) -> None:
    """FileArtifactStore.get returns None for an unknown artifact_id."""
    store = FileArtifactStore(tmp_path)
    tenant = _tenant()

    found = store.get(tenant=tenant, artifact_id="no-such-artifact")

    assert found is None


# ── _format_bytes ────────────────────────────────────────────────────────────


# ── nimbus task list --watch ─────────────────────────────────────────────────


def test_task_list_watch_stops_on_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task list --watch should stop cleanly when KeyboardInterrupt is raised."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(store=store, tenant=tenant, task_id="task-watch-list-001")

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )

    call_count = 0

    original_sleep = time.sleep

    def _raise_after_one(seconds: float) -> None:  # noqa: ARG001
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            raise KeyboardInterrupt
        original_sleep(0)

    monkeypatch.setattr(time, "sleep", _raise_after_one)

    result = _runner.invoke(
        app,
        ["task", "list", "--watch", "--interval", "0.01"],
    )

    assert result.exit_code == 0, result.output
    assert (
        "watch stopped" in result.output.lower() or "stopped" in result.output.lower()
    )


def test_task_list_watch_renders_task_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task list --watch should include task IDs in the live display."""
    store = FileTaskStore(tmp_path)
    tenant = _tenant()
    _make_task(
        store=store,
        tenant=tenant,
        task_id="watch-visible-001",
        intent="Watch this task",
    )

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )
    monkeypatch.setattr(
        time, "sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt)
    )

    result = _runner.invoke(
        app,
        ["task", "list", "--watch", "--interval", "0.01"],
    )

    assert result.exit_code == 0, result.output
    assert "watch-visible-001" in result.output


def test_task_list_no_watch_does_not_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task list without --watch should render once and exit."""
    FileTaskStore(tmp_path)  # empty store

    monkeypatch.setattr("nimbus_cli.cli._profile_session_dir", lambda _: tmp_path)
    monkeypatch.setattr(
        "nimbus_cli.cli._resolve_profile",
        lambda _: _fake_profile("local"),
    )
    # If watch mode were active, sleep would be called; this patch ensures it is not.
    sleep_calls: list[float] = []

    def _record_sleep(s: float) -> None:
        sleep_calls.append(s)

    monkeypatch.setattr(time, "sleep", _record_sleep)

    _runner.invoke(app, ["task", "list"])

    assert sleep_calls == []


# ── REPL readline history ────────────────────────────────────────────────────


def test_setup_readline_history_creates_file_on_write(tmp_path: Path) -> None:
    """_setup_readline_history writes a history file via the atexit hook."""
    from nimbus_cli.cli import _setup_readline_history

    history_path = tmp_path / "repl_history"
    assert not history_path.exists()

    _setup_readline_history(history_path=history_path)

    # Flush atexit handlers to simulate process exit and force the write.
    try:
        import readline

        readline.write_history_file(str(history_path))
        assert history_path.exists()
    except ImportError:
        pass  # readline unavailable — skip the assertion.


def test_setup_readline_history_missing_file_is_silent(tmp_path: Path) -> None:
    """_setup_readline_history does not raise when the history file is absent."""
    from nimbus_cli.cli import _setup_readline_history

    history_path = tmp_path / "no_such_dir" / "repl_history"
    # Should not raise — missing parent dirs are created and missing file is ignored.
    _setup_readline_history(history_path=history_path)


# ── _format_bytes ────────────────────────────────────────────────────────────


def test_format_bytes_below_1kb() -> None:
    """Small byte counts should render as bytes."""
    from nimbus_cli.cli import _format_bytes

    result = _format_bytes(512)

    assert "512" in result
    assert "B" in result


def test_format_bytes_kb_range() -> None:
    """Values in the kilobyte range render with 'KB'."""
    from nimbus_cli.cli import _format_bytes

    result = _format_bytes(2048)

    assert "KB" in result


def test_format_bytes_mb_range() -> None:
    """Values in the megabyte range render with 'MB'."""
    from nimbus_cli.cli import _format_bytes

    result = _format_bytes(3 * 1024 * 1024)

    assert "MB" in result
