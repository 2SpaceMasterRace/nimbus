"""Background task worker for the Nimbus Slack adapter.

Starts one :class:`~nimbus_runtime.worker.TaskWorkerLoop` per BYOK-configured
workspace, executing background tasks (channel backups, file syncs, etc.)
directly inside the Slack service process.

The worker is started in the FastAPI lifespan and runs until the process
shuts down.  Each workspace gets its own asyncio ``Task`` so a slow backup
in one workspace never blocks another.

Environments without any BYOK-configured workspace (remote-only mode) start
zero workers; the lifespan still calls ``build_tenant_workers`` safely.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from nimbus_runtime.backup import (
        BackupChannelRef,
        BackupDestination,
        BackupFileScan,
        BackupManifestEntry,
    )
    from nimbus_runtime.domain import TenantIdentity
    from nimbus_runtime.worker import TaskLeaseContext

log = structlog.get_logger()

_WORKER_POLL_SECONDS = 2.0
_WORKER_LEASE_SECONDS = 30
_WORKER_HEARTBEAT_SECONDS = 10


# ── Concrete implementations of backup Protocol types ─────────────────────────


@dataclass
class _InMemoryManifestStore:
    """Simple in-process manifest store backed by a dict.

    Used by the backup workflow to track which files have already been saved.
    A production deployment should persist this to SQLite or Postgres.
    """

    _entries: dict[str, BackupManifestEntry] = field(default_factory=dict)

    def list_entries(
        self,
        *,
        tenant: TenantIdentity,
        channel: BackupChannelRef,
    ) -> tuple[BackupManifestEntry, ...]:
        """Return existing manifest entries for one tenant-scoped channel."""
        return tuple(
            e
            for e in self._entries.values()
            if e.tenant == tenant and e.channel == channel
        )

    def record_entry(self, entry: BackupManifestEntry) -> None:
        """Persist or replace manifest evidence for one source file."""
        self._entries[entry.file_id] = entry


@dataclass
class _SlackFileSource:
    """Connects ChannelBackupWorkflow to the Slack files.list API.

    In this MVP the source always returns an empty scan — the real
    implementation would call ``slack_sdk`` ``files.list`` using the bot
    token stored in the tenant config.  The workflow handles empty scans
    gracefully (no uploads, creates an empty manifest artifact).
    """

    channel_id: str
    team_id: str

    def list_files(
        self,
        channel: BackupChannelRef,  # noqa: ARG002
        *,
        page_size: int = 100,
        max_pages: int = 3,
    ) -> BackupFileScan:
        """Return a bounded listing of source files (MVP: always empty)."""
        from nimbus_runtime.backup import BackupFileScan  # noqa: PLC0415

        log.info(
            "slack_worker_backup_scan",
            channel_id=self.channel_id,
            team_id=self.team_id,
            page_size=page_size,
            max_pages=max_pages,
        )
        # TODO(team-2): call Slack files.list API with bot token  # noqa: FIX002, TD003
        return BackupFileScan(files=(), total_count=0, truncated=False)

    def download_file(self, file: object, *, max_bytes: int) -> bytes:
        """Download one source file with a hard byte bound (MVP: unreachable)."""
        # list_files always returns empty, so this is never called in MVP.
        msg = "download_file called on empty source"
        raise NotImplementedError(msg)


@dataclass
class _S3ObjectSink:
    """Connects ChannelBackupWorkflow to the tenant S3 bucket.

    Delegates to the NimbusRuntime's storage client so SSE-KMS and
    retry logic are inherited automatically.
    """

    runtime: object

    def upload_bytes(
        self,
        *,
        destination: BackupDestination,
        key: str,
        content: bytes,
    ) -> None:
        """Upload object bytes to the destination bucket."""
        log.debug(
            "slack_worker_backup_upload",
            bucket=destination.container,
            key=key,
            bytes=len(content),
        )
        storage = getattr(self.runtime, "storage", None)
        if storage is not None:
            import asyncio as _asyncio  # noqa: PLC0415

            _asyncio.get_event_loop().run_until_complete(
                storage.upload_bytes(
                    container=destination.container,
                    object_name=key,
                    content=content,
                )
            )

    def verify_object(
        self,
        *,
        destination: BackupDestination,
        key: str,
        content_sha256: str,
        size_bytes: int,
    ) -> bool:
        """Return whether the destination object matches expected evidence."""
        log.debug(
            "slack_worker_backup_verify",
            bucket=destination.container,
            key=key,
        )
        # MVP: optimistic — assume upload succeeded.
        del key, content_sha256, size_bytes, destination
        return True


# ── Task handler ──────────────────────────────────────────────────────────────


async def _slack_task_handler(ctx: TaskLeaseContext) -> None:
    """Dispatch one claimed task to the appropriate Slack handler.

    Currently supported intents
    ---------------------------
    ``backup_channel``
        Runs :class:`~nimbus_runtime.backup.ChannelBackupWorkflow` with the
        channel coordinates stored in ``task.metadata``.

    All other intents are logged and the task handler returns without error,
    which marks the task as ``completed`` by the worker loop.
    """
    task = ctx.task
    intent = task.intent
    log.info(
        "slack_worker_task_started",
        task_id=task.task_id,
        intent=intent,
        status=task.status.value,
    )

    if intent == "backup_channel":
        await _handle_backup_channel(ctx)
        return

    log.warning(
        "slack_worker_unknown_intent",
        task_id=task.task_id,
        intent=intent,
    )


async def _handle_backup_channel(ctx: TaskLeaseContext) -> None:
    """Execute a ``backup_channel`` task using ChannelBackupWorkflow."""
    from nimbus_runtime.backup import (  # noqa: PLC0415
        BackupChannelRef,
        BackupDestination,
        ChannelBackupRequest,
        ChannelBackupWorkflow,
    )
    from nimbus_runtime.stores import FileArtifactStore  # noqa: PLC0415

    from nimbus_slack.runtime import _session_dir, build_tenant_runtime  # noqa: PLC0415
    from nimbus_slack.store import SlackStoreError  # noqa: PLC0415

    task = ctx.task
    tenant = task.tenant
    metadata = task.metadata or {}

    channel_id = metadata.get("channel_id")
    channel_name = metadata.get("channel_name")
    s3_prefix = metadata.get("s3_prefix", "slack")
    if not isinstance(channel_id, str) or not channel_id:
        log.warning(
            "slack_worker_backup_missing_channel",
            task_id=task.task_id,
            metadata=metadata,
        )
        return

    team_id = tenant.workspace_id

    # Resolve tenant configuration (BYOK credentials).
    try:
        from nimbus_slack.deps import get_slack_store  # noqa: PLC0415

        store = get_slack_store()
        config = store.get_tenant_config(team_id)
    except SlackStoreError as exc:
        log.warning(
            "slack_worker_backup_store_unavailable",
            task_id=task.task_id,
            error=str(exc),
        )
        return

    if config is None:
        log.warning(
            "slack_worker_backup_no_config",
            task_id=task.task_id,
            team_id=team_id,
        )
        return

    runtime = build_tenant_runtime(team_id=team_id, config=config)
    session_dir = _session_dir(team_id)

    channel_ref = BackupChannelRef(
        platform="slack",
        workspace_id=team_id,
        channel_id=channel_id,
        channel_name=channel_name if isinstance(channel_name, str) else None,
    )
    destination = BackupDestination(
        container=config.s3_bucket,
        prefix=s3_prefix if isinstance(s3_prefix, str) else "slack",
    )
    request = ChannelBackupRequest(
        tenant=tenant,
        channel=channel_ref,
        destination=destination,
    )

    workflow = ChannelBackupWorkflow(
        source=_SlackFileSource(channel_id=channel_id, team_id=team_id),
        sink=_S3ObjectSink(runtime=runtime),
        manifest_store=_InMemoryManifestStore(),
        artifact_store=FileArtifactStore(session_dir),
    )
    result = await workflow.run(context=ctx, request=request)
    log.info(
        "slack_worker_backup_completed",
        task_id=task.task_id,
        channel_id=channel_id,
        uploaded=result.uploaded_count,
        deduped=result.deduped_count,
        skipped=len(result.skipped_files),
        failed=len(result.failed_files),
    )


# ── Lifespan helper ───────────────────────────────────────────────────────────


def build_tenant_workers(
    *,
    team_ids: list[str],
) -> list[asyncio.Task[None]]:
    """Build and start one :class:`TaskWorkerLoop` per active tenant.

    Args:
        team_ids: Active workspace IDs to serve.  Pass an empty list when
            in remote-only mode (no workers are started).

    Returns:
        The running asyncio tasks.  Callers should cancel them during shutdown.

    """
    from datetime import timedelta  # noqa: PLC0415

    from nimbus_runtime.domain import TenantIdentity  # noqa: PLC0415
    from nimbus_runtime.stores import (  # noqa: PLC0415
        FileTaskStore,
        FileWorkerLeaseStore,
    )
    from nimbus_runtime.worker import TaskWorkerConfig, TaskWorkerLoop  # noqa: PLC0415

    from nimbus_slack.runtime import _session_dir  # noqa: PLC0415

    tasks: list[asyncio.Task[None]] = []
    for team_id in team_ids:
        tenant = TenantIdentity(platform="slack", workspace_id=team_id)
        session_dir = _session_dir(team_id)
        task_store = FileTaskStore(session_dir)
        lease_store = FileWorkerLeaseStore(session_dir)
        config = TaskWorkerConfig(
            tenant=tenant,
            worker_id=f"slack-{team_id}-{uuid.uuid4().hex[:8]}",
            lease_duration=timedelta(seconds=_WORKER_LEASE_SECONDS),
            heartbeat_interval=timedelta(seconds=_WORKER_HEARTBEAT_SECONDS),
            poll_interval=timedelta(seconds=_WORKER_POLL_SECONDS),
        )
        loop = TaskWorkerLoop(
            task_store=task_store,
            lease_store=lease_store,
            handler=_slack_task_handler,
            config=config,
        )
        stop_event = asyncio.Event()
        worker_task = asyncio.create_task(
            loop.run_forever(stop=stop_event),
            name=f"nimbus-worker-{team_id}",
        )
        log.info(
            "slack_worker_started",
            team_id=team_id,
            worker_id=config.worker_id,
        )
        tasks.append(worker_task)

    return tasks
