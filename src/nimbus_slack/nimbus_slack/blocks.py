"""Slack Block Kit renderers for Nimbus UI cards.

Ownership:
  nimbus_runtime  — owns structured response objects
  nimbus_slack    — owns Block Kit rendering (this module)
  LLM             — may provide short summary text only

Every public function returns a list of Slack Block Kit block dicts.
None of these functions make network calls.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from nimbus_runtime.capabilities import CapabilitySpec
    from nimbus_runtime.search import SearchResult

    from nimbus_slack.file_sync import (
        ChangedSinceSyncReport,
        ChannelFileListing,
        DedupeReport,
        FileSyncReport,
        SaveProgress,
        SlackFileRef,
    )

# Slack Block Kit block size limit (Slack rejects payloads above 50 blocks)
_MAX_BLOCKS = 50
# File rows shown inline before collapsing to a count
_MAX_FILE_ROWS_INLINE = 10
# Max characters for a file name displayed inline
_MAX_NAME_CHARS = 60
# Preview row counts for inline lists — keeps cards scannable
_PREVIEW_SHORT = 5
_PREVIEW_LONG = 8
# Duplicate group key preview before collapsing.
_MAX_DUPLICATE_KEYS_INLINE = 3
# Default consequence shown in approval cards
_DEFAULT_CONSEQUENCE = (
    "This action cannot be undone unless a restore plan is available."
)

# Statuses where a task has finished — used to switch the CLI hint context.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "done",
        "failed",
        "canceled",
        "expired",
        "rejected",
    }
)

# Status labels matching TaskStatus strings.
_STATUS_LABELS: dict[str, str] = {
    "created": "Created",
    "planning": "Planning",
    "scanning": "Scanning",
    "diffing": "Diffing",
    "awaiting_approval": "Awaiting approval",
    "applying": "Applying",
    "verifying": "Verifying",
    "done": "Done",
    "failed": "Failed",
    "canceled": "Canceled",
    "expired": "Expired",
    "rejected": "Rejected",
}


# ── Primitive builders ──────────────────────────────────────────────────────


def _header(text: str) -> dict[str, Any]:
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": text, "emoji": False},
    }


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _section_with_fields(*fields: str) -> dict[str, Any]:
    return {
        "type": "section",
        "fields": [{"type": "mrkdwn", "text": f} for f in fields],
    }


def _context(*elements: str) -> dict[str, Any]:
    return {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": el} for el in elements],
    }


def _divider() -> dict[str, Any]:
    return {"type": "divider"}


def _button(
    text: str, action_id: str, value: str, style: str = "default"
) -> dict[str, Any]:
    btn: dict[str, Any] = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": False},
        "action_id": action_id,
        "value": value,
    }
    if style in {"primary", "danger"}:
        btn["style"] = style
    return btn


def _actions(*buttons: dict[str, Any]) -> dict[str, Any]:
    return {"type": "actions", "elements": list(buttons)}


# ── Formatting helpers ──────────────────────────────────────────────────────


def _truncate(text: str, max_chars: int = _MAX_NAME_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status.replace("_", " ").title())


def _safe_mrkdwn(text: str) -> str:
    """Escape characters that would break mrkdwn field boundaries."""
    return re.sub(r"[`*_~<>&]", lambda m: f"\\{m.group()}", text)


# ── Feature 1 / Feature 3: File list card ───────────────────────────────────


def file_list_card(listing: ChannelFileListing) -> list[dict[str, Any]]:
    """Render a file-list Slack card from a :class:`ChannelFileListing`.

    Appends an action bar with quick follow-up commands (save, dedupe, diff)
    so a listing becomes a workflow entry point instead of a terminal reply.
    """
    from nimbus_slack import design  # noqa: PLC0415

    blocks: list[dict[str, Any]] = []

    if not listing.files:
        blocks.append(_section("No files are visible to Nimbus in this channel."))
        return blocks

    count = listing.total_count or len(listing.files)
    shown = len(listing.files)
    truncated = listing.truncated or shown < count

    header_text = "I found files in this channel"
    blocks.append(_header(header_text))
    if truncated:
        blocks.append(_section(f"I can see {shown} of {count} files right now."))
    else:
        blocks.append(_section(f"I found {_plural(count, 'file')} in this channel."))

    blocks.extend(_file_row(file) for file in listing.files[:_MAX_FILE_ROWS_INLINE])

    if len(listing.files) > _MAX_FILE_ROWS_INLINE:
        remaining = len(listing.files) - _MAX_FILE_ROWS_INLINE
        blocks.append(
            _section(
                f"_Showing the first {_MAX_FILE_ROWS_INLINE}; "
                f"{remaining} more are hidden._"
            )
        )

    if truncated:
        blocks.append(
            _context(
                "Scan reached its page bound. "
                f"{_plural(count - shown, 'file')} not shown."
            )
        )

    blocks.append(design.file_list_actions())

    return blocks[:_MAX_BLOCKS]


def _file_row(file: SlackFileRef) -> dict[str, Any]:
    name = _truncate(file.name)
    size = _format_size(file.size_bytes)
    mime = file.mimetype or "unknown type"
    return _section(f"• *{_safe_mrkdwn(name)}* - {size} - `{mime}`")


# ── Feature 3: Save report card ─────────────────────────────────────────────


def save_report_card(report: FileSyncReport) -> list[dict[str, Any]]:
    """Render a save-result card from a :class:`FileSyncReport`."""
    saved = len(report.saved_keys)
    skipped = len(report.skipped_files)
    failed = len(report.failures)
    scanned = report.scanned_count

    if failed:
        header_text = "I saved what I could"
        summary = (
            f"I scanned {_plural(scanned, 'file')}, saved {saved}, "
            f"skipped {skipped}, and hit {_plural(failed, 'failure')}."
        )
    else:
        header_text = "I saved the channel files to S3"
        summary = (
            f"I scanned {_plural(scanned, 'file')}, saved {saved}, "
            f"and skipped {skipped} "
            "that were already recorded."
        )
    blocks: list[dict[str, Any]] = [_header(header_text), _section(summary)]
    target = _target_text(report)
    blocks.append(_section(f"*Destination:* `{target}`"))

    if report.saved_keys:
        preview = [
            f"`{_truncate(k, 50)}`" for k in list(report.saved_keys)[:_PREVIEW_SHORT]
        ]
        suffix = (
            f"\n_…and {len(report.saved_keys) - _PREVIEW_SHORT} more_"
            if len(report.saved_keys) > _PREVIEW_SHORT
            else ""
        )
        blocks.append(_section("*Saved files:*\n" + "\n".join(preview) + suffix))

    if report.failures:
        failure_lines = [
            f"• `{_truncate(f.file.name, 40)}` — {f.reason}"
            for f in report.failures[:_PREVIEW_SHORT]
        ]
        if len(report.failures) > _PREVIEW_SHORT:
            failure_lines.append(f"_…and {len(report.failures) - _PREVIEW_SHORT} more_")
        blocks.append(_section("*Failures:*\n" + "\n".join(failure_lines)))

    if report.truncated:
        total = report.total_count or scanned
        blocks.append(
            _context(
                f"Scan truncated at {scanned} of {total} files."
                " Raise scan bounds to see more."
            )
        )

    # Action bar — quick follow-ups (view diff, find duplicates, retry).
    from nimbus_slack import design  # noqa: PLC0415

    blocks.append(design.save_report_actions(has_failures=failed > 0))

    return blocks[:_MAX_BLOCKS]


# ── Feature 3: Diff report card ─────────────────────────────────────────────


def diff_report_card(report: FileSyncReport) -> list[dict[str, Any]]:
    """Render a diff-result card from a :class:`FileSyncReport`.

    When unsaved files exist, appends a primary "Save unsaved files" button
    so the user can resolve the diff with one click.
    """
    from nimbus_slack import design  # noqa: PLC0415

    target = _target_text(report)
    missing = len(report.missing_files)
    scanned = report.scanned_count

    if missing == 0:
        blocks: list[dict[str, Any]] = [
            _header("Everything in this channel is saved"),
            _section(
                f"I checked {_plural(scanned, 'file')}. "
                f"They are all recorded in `{target}`."
            ),
        ]
        if report.truncated:
            blocks.append(_context("Scan was truncated — some files were not checked."))
        return blocks

    blocks = [
        _header("Some channel files are missing from S3"),
        _section(
            f"I checked {_plural(scanned, 'file')}. "
            f"{_plural(missing, 'file').capitalize()} {_is_are(missing)} "
            "in Slack but not saved "
            f"to `{target}` yet."
        ),
        _section(f"*Destination:* `{target}`"),
    ]
    preview = report.missing_files[:_MAX_FILE_ROWS_INLINE]
    lines = [
        f"• *{_truncate(f.name)}*  _{_format_size(f.size_bytes)}_" for f in preview
    ]
    if len(report.missing_files) > _MAX_FILE_ROWS_INLINE:
        lines.append(f"_…and {len(report.missing_files) - _MAX_FILE_ROWS_INLINE} more_")
    blocks.append(_section("*Unsaved files:*\n" + "\n".join(lines)))

    if report.truncated:
        blocks.append(_context("Scan was truncated — full list may be larger."))

    blocks.append(design.diff_report_actions())

    return blocks[:_MAX_BLOCKS]


# ── Feature 3: Changed-since-sync card ──────────────────────────────────────


def changed_since_sync_card(report: ChangedSinceSyncReport) -> list[dict[str, Any]]:
    """Render a changed-since-last-sync card."""
    new = len(report.new_files)
    resized = len(report.resized_files)

    if new == 0 and resized == 0:
        anchor = (
            f"Last sync: {report.last_sync_at.isoformat()}"
            if report.last_sync_at
            else "No prior syncs recorded."
        )
        return [
            _header("No files changed since the last save"),
            _section(f"I checked the saved manifest. {anchor}"),
        ]

    blocks: list[dict[str, Any]] = [
        _header("Some files changed since the last save"),
        _section(
            f"I found {_plural(new + resized, 'changed file')}: "
            f"{new} new and {resized} "
            "with a different size."
        ),
    ]

    if new > 0:
        lines = [
            f"• *{_truncate(f.name)}*  _{_format_size(f.size_bytes)}_"
            for f in report.new_files[:_PREVIEW_LONG]
        ]
        if new > _PREVIEW_LONG:
            lines.append(f"_…and {new - _PREVIEW_LONG} more_")
        blocks.append(_section(f"*New ({new}):*\n" + "\n".join(lines)))

    if resized > 0:
        lines = [
            f"• *{_truncate(f.name)}*  _{_format_size(f.size_bytes)}_"
            for f in report.resized_files[:_PREVIEW_SHORT]
        ]
        if resized > _PREVIEW_SHORT:
            lines.append(f"_…and {resized - _PREVIEW_SHORT} more_")
        blocks.append(_section(f"*Resized ({resized}):*\n" + "\n".join(lines)))

    if report.last_sync_at:
        blocks.append(_context(f"Last sync: {report.last_sync_at.isoformat()}"))

    return blocks[:_MAX_BLOCKS]


# ── Feature 3: Dedupe report card ───────────────────────────────────────────


def dedupe_report_card(report: DedupeReport) -> list[dict[str, Any]]:
    """Render a dedupe report card from a :class:`DedupeReport`."""
    if report.saved_count == 0:
        return [_section("No saved files to deduplicate yet.")]

    dup_count = len(report.duplicate_groups)
    stale_count = len(report.stale_files)

    if dup_count == 0 and stale_count == 0:
        if report.stale_checked:
            detail = (
                f"I checked {_plural(report.saved_count, 'saved file')} in "
                f"{report.scope_label}. They are unique and still visible in Slack."
            )
        else:
            detail = (
                f"I checked {_plural(report.saved_count, 'saved file')} in "
                f"{report.scope_label}. They have unique recorded content hashes."
            )
        return [
            _header("The saved files look clean"),
            _section(detail),
        ]

    header = "I found duplicate or stale saved files"
    stale_summary = _plural(stale_count, "stale saved entry", "stale saved entries")
    blocks: list[dict[str, Any]] = [
        _header(header),
        _section(
            f"I checked {_plural(report.saved_count, 'saved file')} in "
            f"{report.scope_label}. I found {_plural(dup_count, 'duplicate set')} "
            f"and {stale_summary}."
        ),
    ]

    if report.duplicate_groups:
        lines: list[str] = []
        for group in report.duplicate_groups[:_PREVIEW_LONG]:
            keys = ", ".join(
                f"`{_truncate(key, 44)}`"
                for key in group.keys[:_MAX_DUPLICATE_KEYS_INLINE]
            )
            hidden_key_count = len(group.keys) - _MAX_DUPLICATE_KEYS_INLINE
            extra = f" and {hidden_key_count} more" if hidden_key_count > 0 else ""
            lines.append(
                f"• {len(group.keys)} copies: {keys}{extra} "
                f"(hash `{group.content_sha256[:12]}...`)"
            )
        if dup_count > _PREVIEW_LONG:
            lines.append(f"_…and {dup_count - _PREVIEW_LONG} more_")
        blocks.append(_section("*Duplicate groups:*\n" + "\n".join(lines)))

    if report.stale_files:
        lines = [
            f"• `{_truncate(f.s3_key, 50)}`" for f in report.stale_files[:_PREVIEW_LONG]
        ]
        if stale_count > _PREVIEW_LONG:
            lines.append(f"_…and {stale_count - _PREVIEW_LONG} more_")
        blocks.append(_section("*Stale S3 entries:*\n" + "\n".join(lines)))

    if report.truncated:
        blocks.append(_context("Results may be incomplete — scan was truncated."))

    return blocks[:_MAX_BLOCKS]


# ── Feature 3: Save progress card ───────────────────────────────────────────


def save_progress_card(
    progress: SaveProgress, *, channel_label: str = ""
) -> list[dict[str, Any]]:
    """Render a live-progress card for an in-flight save operation."""
    processed = progress.saved + progress.skipped + progress.failed
    pct = int(processed / progress.total * 100) if progress.total else 0
    bar = _progress_bar(pct)

    title = f"I'm saving Slack files{' in ' + channel_label if channel_label else ''}"
    blocks: list[dict[str, Any]] = [
        _header(title),
        _section(f"{bar}  {processed}/{progress.total} ({pct}%)"),
        _section_with_fields(
            f"*Saved*\n{progress.saved}",
            f"*Skipped*\n{progress.skipped}",
            f"*Failed*\n{progress.failed}",
        ),
    ]
    if progress.current_file:
        blocks.append(
            _context(f"Current: `{_truncate(progress.current_file.name, 40)}`")
        )

    return blocks


def _progress_bar(pct: int, width: int = 12) -> str:
    filled = int(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


# ── Feature 3: Task status card ─────────────────────────────────────────────


def task_status_card(  # noqa: PLR0913, C901
    *,
    task_id: str,
    status: str,
    intent: str,
    scanned_count: int | None = None,
    uploaded_count: int | None = None,
    skipped_count: int | None = None,
    failed_count: int | None = None,
    bytes_display: str | None = None,
    artifact_links: list[str] | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
) -> list[dict[str, Any]]:
    """Render a task-status card visible from Slack and CLI."""
    status_label = _status_label(status)
    blocks: list[dict[str, Any]] = [
        _header(f"Task {status_label.lower()}"),
        _section(f"I'm tracking `{task_id}` for: {_truncate(intent, 80)}"),
    ]

    stats: list[str] = []
    if scanned_count is not None:
        stats.append(f"*Scanned*\n{scanned_count}")
    if uploaded_count is not None:
        stats.append(f"*Uploaded*\n{uploaded_count}")
    if skipped_count is not None:
        stats.append(f"*Skipped*\n{skipped_count}")
    if failed_count is not None:
        stats.append(f"*Failed*\n{failed_count}")
    if stats:
        blocks.append(_section_with_fields(*stats[:4]))

    if bytes_display:
        blocks.append(_context(f"Total size: {bytes_display}"))

    if artifact_links:
        links = "\n".join(f"• `{a}`" for a in artifact_links[:5])
        blocks.append(_section(f"*Artifacts:*\n{links}"))

    # Cost / token usage context block (only when data is available).
    usage_parts: list[str] = []
    if input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
        usage_parts.append(
            f"{total:,} tokens ({input_tokens:,} in / {output_tokens:,} out)"
        )
    if cost_usd is not None and cost_usd > 0:
        usage_parts.append(f"~${cost_usd:.4f} USD")
    if usage_parts:
        blocks.append(_context("  •  ".join(usage_parts)))

    if status in _TERMINAL_STATUSES:
        blocks.append(
            _context(
                f"Artifacts: `nimbus task artifacts {task_id}`  •  "
                f"Proof: `nimbus proof show latest`  •  "
                f"Events: `nimbus task events {task_id}`"
            )
        )
    else:
        blocks.append(
            _context(
                f"Watch: `nimbus task watch {task_id}`  •  "
                f"Events: `nimbus task events {task_id}`"
            )
        )

    # Add interactive action bar (approve/reject, cancel, view artifacts).
    from nimbus_slack import design as _design  # noqa: PLC0415

    blocks.append(_design.task_action_bar(task_id=task_id, status=status))

    return blocks[:_MAX_BLOCKS]


# ── Feature 4: Approval request card ────────────────────────────────────────


def approval_request_card(  # noqa: PLR0913
    *,
    action_id: str,
    target_display: str,
    size_display: str | None,
    sha256: str | None,
    requested_by: str,
    expires_at: str,
    risk_level: str = "destructive",
    consequence: str = _DEFAULT_CONSEQUENCE,
) -> list[dict[str, Any]]:
    """Render an approval-request card for destructive actions."""
    blocks: list[dict[str, Any]] = [
        _header("Approval required"),
        _section(
            "I need approval before I run this storage action."
            f"\n\n*Target:* `{_truncate(target_display, 80)}`"
        ),
        _section_with_fields(
            f"*Risk*\n{risk_level.replace('_', ' ').title()}",
            f"*Requested by*\n<@{requested_by}>",
            *([f"*Size*\n{size_display}"] if size_display else []),
        ),
        _section(consequence),
    ]
    if sha256:
        blocks.append(_context(f"SHA-256: `{sha256[:16]}…`"))
    blocks.append(_context(f"Expires: {expires_at}  •  Action ID: `{action_id}`"))
    blocks.append(
        _actions(
            _button("Approve", f"approve:{action_id}", action_id, style="primary"),
            _button("Reject", f"reject:{action_id}", action_id, style="danger"),
        )
    )
    return blocks[:_MAX_BLOCKS]


# ── Feature 3: Failure card ──────────────────────────────────────────────────


def failure_card(
    *,
    title: str = "Operation failed",
    detail: str,
    recoverable: bool = True,
    retry_hint: str | None = None,
) -> list[dict[str, Any]]:
    """Render a failure card for any Nimbus error condition."""
    blocks: list[dict[str, Any]] = [
        _header(title),
        _section(detail),
    ]
    if recoverable and retry_hint:
        blocks.append(_context(f"Try: {retry_hint}"))
    elif not recoverable:
        blocks.append(_context("This failure cannot be retried automatically."))
    return blocks


# ── Feature 6: Manifest summary card ────────────────────────────────────────


def manifest_summary_card(  # noqa: PLR0913 — all fields are required for evidence reporting.
    *,
    task_id: str,
    scanned_count: int,
    saved_count: int,
    skipped_count: int,
    failed_count: int,
    total_bytes: int,
    artifact_id: str | None = None,
    restore_available: bool = True,
) -> list[dict[str, Any]]:
    """Render an evidence manifest summary for a completed backup operation.

    This is the canonical "proof" card — every important action leaves a
    manifest artifact, and this card surfaces it to Slack users.
    """
    blocks: list[dict[str, Any]] = [
        _header("Backup manifest"),
        _section(
            f"I scanned {_plural(scanned_count, 'file')}, saved {saved_count}, skipped "
            f"{skipped_count}, and failed on {failed_count}."
        ),
        _section_with_fields(
            f"*Saved*\n{saved_count}",
            f"*Skipped*\n{skipped_count}",
            f"*Failed*\n{failed_count}",
            f"*Total size*\n{_format_size(total_bytes)}",
        ),
    ]

    if artifact_id:
        blocks.append(
            _context(
                f"Manifest artifact: `{artifact_id}`  •  "
                f"View: `nimbus task artifacts {task_id}`  •  "
                "Proof: `nimbus proof show latest`"
            )
        )

    if not restore_available:
        blocks.append(
            _context(
                "Bucket versioning is off"
                " - true restore is unavailable for deleted files."
            )
        )

    from nimbus_slack import design  # noqa: PLC0415

    blocks.append(design.manifest_summary_actions())

    return blocks[:_MAX_BLOCKS]


# ── Formatting helpers ──────────────────────────────────────────────────────


_BYTES_PER_UNIT = 1024
_DISPLAY_UNITS = ("KB", "MB", "GB")


def _format_size(size_bytes: int) -> str:
    if size_bytes < _BYTES_PER_UNIT:
        return f"{size_bytes} B"
    value = float(size_bytes)
    for unit in _DISPLAY_UNITS:
        value /= _BYTES_PER_UNIT
        if value < _BYTES_PER_UNIT:
            return f"{value:.1f}".rstrip("0").rstrip(".") + f" {unit}"
    value /= _BYTES_PER_UNIT
    return f"{value:.1f}".rstrip("0").rstrip(".") + " TB"


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    word = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {word}"


def _is_are(count: int) -> str:
    return "is" if count == 1 else "are"


def _target_text(report: FileSyncReport) -> str:
    prefix = report.s3_prefix.strip("/")
    if prefix:
        return f"s3://{report.s3_bucket}/{prefix}/"
    return f"s3://{report.s3_bucket}/"


# ── Serialization helper ─────────────────────────────────────────────────────


def workspace_status_card(  # noqa: PLR0913
    *,
    team_id: str,
    tasks_running: int,
    tasks_awaiting: int,
    tasks_done_today: int,
    tasks_failed: int,
    pending_approvals: int,
    proposed_plans: int,
) -> list[dict[str, Any]]:
    """Build a Block Kit health card for ``@Nimbus status``."""
    healthy = tasks_failed == 0 and pending_approvals == 0
    workspace = f" for `{team_id}`" if team_id else ""
    summary = (
        f"Everything looks healthy{workspace}."
        if healthy
        else f"I found workspace items that need attention{workspace}."
    )
    return [
        _header("Nimbus workspace health"),
        _section(summary),
        _section(
            "Right now: "
            f"{tasks_running} running, {tasks_awaiting} awaiting approval, "
            f"{tasks_done_today} done today, {tasks_failed} failed, "
            f"{_plural(pending_approvals, 'pending approval')}, and "
            f"{_plural(proposed_plans, 'proposed plan')}."
        ),
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        "Use `@Nimbus task list`, `@Nimbus plan show`,"
                        " or the Nimbus CLI for details."
                    ),
                }
            ],
        },
    ]


def app_home_card(  # noqa: PLR0913
    *,
    team_id: str,
    tasks_running: int = 0,
    tasks_awaiting: int = 0,
    tasks_done_today: int = 0,
    tasks_failed: int = 0,
    pending_approvals: int = 0,
    proposed_plans: int = 0,
) -> list[dict[str, Any]]:
    """Build the Block Kit blocks for the Nimbus App Home tab.

    The view is built from the same live counters as the status card so the
    Home tab always reflects current workspace health.  Blocks are returned as
    a flat list; callers wrap them in ``{"type": "home", "blocks": blocks}``.
    """
    healthy = tasks_failed == 0 and pending_approvals == 0
    health_line = (
        "Everything looks healthy."
        if healthy
        else "Some workspace items need attention."
    )
    blocks: list[dict[str, Any]] = [
        _header(f"Welcome to Nimbus{' - ' + team_id if team_id else ''}"),
        _section(
            "*Nimbus* keeps your Slack files backed up to S3, answers questions "
            "about your data, and surfaces cloud-storage insights right in chat.\n\n"
            "Mention `@Nimbus` in any channel to get started."
        ),
        _header("Workspace health"),
        _section(
            f"{health_line} Current counts: {tasks_running} running, "
            f"{tasks_awaiting} awaiting approval, {tasks_done_today} done today, "
            f"{tasks_failed} failed, {_plural(pending_approvals, 'pending approval')}, "
            f"and {_plural(proposed_plans, 'proposed plan')}."
        ),
        _header("Quick commands"),
        _section(
            "Say these to `@Nimbus` in any channel:\n\n"
            "• `save all files in this channel` — back up channel files to S3\n"
            "• `what files are in this channel?` — list Slack channel files\n"
            "• `what files are not saved in S3?` — find unsaved files\n"
            "• `status` — see this workspace health summary\n"
            "• `find duplicate files` — detect duplicates and stale S3 entries\n"
            "• `what files changed since last sync?` — recent changes since backup\n"
            "• CLI: `nimbus stack diff <stack-id>` and "
            "`nimbus proof show latest` for proof-backed storage reviews"
        ),
    ]
    from nimbus_slack import design as _design  # noqa: PLC0415

    blocks.append(_design.footer())
    return blocks[:_MAX_BLOCKS]


def storage_stack_card(  # noqa: PLR0913
    *,
    stack_id: str,
    status: str,
    plan_id: str | None,
    change_count: int,
    conflict_count: int,
    next_step: str,
) -> list[dict[str, Any]]:
    """Render a Slack review card for a Nimbus storage stack."""
    blocks = [
        _header("Storage stack review"),
        _section(
            f"Stack `{stack_id}` is `{status}` with "
            f"{_plural(change_count, 'change')} and "
            f"{_plural(conflict_count, 'conflict')}."
        ),
        _section_with_fields(
            f"*Stack ID:*\n`{stack_id}`",
            f"*Plan ID:*\n`{plan_id or 'none'}`",
            f"*Status:*\n`{status}`",
            f"*Next step:*\n{_safe_mrkdwn(next_step)}",
        ),
        _context(
            f"CLI: `nimbus stack show {stack_id}`  •  `nimbus stack diff {stack_id}`"
        ),
    ]
    return blocks[:_MAX_BLOCKS]


def policy_patch_card(  # noqa: PLR0913
    *,
    proposal_id: str,
    status: str,
    base_policy: str,
    proposed_policy: str,
    capability: str,
    reviewer: str,
) -> list[dict[str, Any]]:
    """Render a Slack review card for a learning-derived policy patch."""
    blocks = [
        _header("Policy patch review"),
        _section(
            f"Proposal `{proposal_id}` is `{status}` for capability `{capability}`."
        ),
        _section_with_fields(
            f"*Base:*\n`{base_policy}`",
            f"*Proposed:*\n`{proposed_policy}`",
            f"*Reviewer:*\n`{reviewer}`",
            f"*Status:*\n`{status}`",
        ),
        _context(
            f"CLI: `nimbus policy patch show {proposal_id}`  •  "
            f"`nimbus policy patch accept {proposal_id}`"
        ),
    ]
    return blocks[:_MAX_BLOCKS]


def search_results_card(
    *,
    query: str,
    results: Sequence[SearchResult],
) -> list[dict[str, Any]]:
    """Render indexed-document search results as a Slack Block Kit card.

    Args:
        query:   The original search query string.
        results: Ranked ``SearchResult`` objects from ``FileSearchIndexStore``.

    Returns:
        A list of Slack Block Kit block dicts, capped at ``_MAX_BLOCKS``.

    """
    blocks: list[dict[str, Any]] = [
        _header(f"Search results for {_truncate(query, 60)}"),
    ]
    if not results:
        blocks.append(_section("No indexed documents matched your query."))
        blocks.append(
            _context(
                "Tip: save channel files to S3 first with `save files here`.",
            )
        )
        return blocks[:_MAX_BLOCKS]

    shown = tuple(results[:_PREVIEW_LONG])
    for result in shown:
        doc = result.document
        snippet = result.chunk_hits[0].snippet if result.chunk_hits else ""
        if len(snippet) > 200:  # noqa: PLR2004
            snippet = snippet[:197] + "…"
        title_text = f"*{_safe_mrkdwn(doc.title)}*"
        if snippet:
            title_text += f"\n{_safe_mrkdwn(snippet)}"
        blocks.append(_section(title_text))
        meta_parts: list[str] = []
        if doc.source_uri:
            meta_parts.append(f"<{doc.source_uri}|{_truncate(doc.source_uri, 50)}>")
        if doc.channel_id:
            meta_parts.append(f"channel: {doc.channel_id}")
        meta_parts.append(f"score: {result.score:.1f}")
        blocks.append(_context(*meta_parts))

    remaining = len(results) - len(shown)
    if remaining > 0:
        blocks.append(_context(f"…and {_plural(remaining, 'more result')}."))

    blocks.append(
        _context(f"Found {_plural(len(results), 'result')} for: {_safe_mrkdwn(query)}")
    )
    return blocks[:_MAX_BLOCKS]


def capability_list_card(
    capabilities: Sequence[CapabilitySpec],
) -> list[dict[str, Any]]:
    """Render the shared Nimbus capability catalog for Slack."""
    from nimbus_runtime.capabilities import CapabilityStatus  # noqa: PLC0415

    live = [
        capability
        for capability in capabilities
        if capability.status is not CapabilityStatus.ROADMAP
    ]
    roadmap = [
        capability
        for capability in capabilities
        if capability.status is CapabilityStatus.ROADMAP
    ]
    blocks: list[dict[str, Any]] = [
        _header("Nimbus tools"),
        _section(
            "I use a shared runtime tool catalog across Slack and CLI. "
            "Live tools run through the task/action system; roadmap tools show "
            "what plugs into the same contract next."
        ),
    ]
    if live:
        blocks.append(_section("*Available now:*\n" + _capability_lines(live, limit=8)))
    if roadmap:
        blocks.append(
            _section("*Coming next:*\n" + _capability_lines(roadmap, limit=6))
        )
    blocks.append(
        _context(
            "Use `nimbus tools list` or `nimbus tools inspect <name>` for the CLI view."
        )
    )
    return blocks[:_MAX_BLOCKS]


def _capability_lines(capabilities: Sequence[CapabilitySpec], *, limit: int) -> str:
    shown = capabilities[:limit]
    lines = [
        f"• `{capability.name}` - {capability.title} ({capability.status.value})"
        for capability in shown
    ]
    remaining = len(capabilities) - len(shown)
    if remaining > 0:
        lines.append(f"_...and {remaining} more_")
    return "\n".join(lines)


def blocks_to_fallback_text(blocks: list[dict[str, Any]]) -> str:
    """Return a plain-text fallback for notifications and accessibility."""
    parts: list[str] = []
    for block in blocks:
        btype = block.get("type")
        if btype == "header":
            text = block.get("text", {})
            if isinstance(text, dict):
                parts.append(text.get("text", ""))
        elif btype == "section":
            text = block.get("text", {})
            if isinstance(text, dict):
                raw = text.get("text", "")
                # Strip mrkdwn bold/italic markers for plain-text readability
                raw = re.sub(r"\*([^*]+)\*", r"\1", raw)
                raw = re.sub(r"(?<![\w`])_([^_\n]+)_(?![\w`])", r"\1", raw)
                parts.append(raw)
        elif btype == "context":
            parts.extend(
                el.get("text", "")
                for el in block.get("elements", [])
                if isinstance(el, dict)
            )
    return "\n".join(p for p in parts if p).strip()
