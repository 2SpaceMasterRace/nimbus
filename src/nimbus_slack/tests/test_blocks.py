"""Tests for Slack Block Kit renderers (Feature 3).

These are golden tests: they fix the exact block structure so that Slack
payload regressions are immediately visible in CI.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nimbus_runtime.capabilities import all_capabilities
from nimbus_slack.blocks import (
    _format_size,
    _progress_bar,
    app_home_card,
    approval_request_card,
    blocks_to_fallback_text,
    capability_list_card,
    changed_since_sync_card,
    dedupe_report_card,
    diff_report_card,
    failure_card,
    file_list_card,
    policy_patch_card,
    save_progress_card,
    save_report_card,
    search_results_card,
    storage_stack_card,
    task_status_card,
    workspace_status_card,
)
from nimbus_slack.file_sync import (
    ChangedSinceSyncReport,
    ChannelFileListing,
    DedupeReport,
    DuplicateGroup,
    FileFailure,
    FileSyncReport,
    SlackFileRef,
    StaleSavedFile,
)

pytestmark = pytest.mark.unit


# ── Helpers ──────────────────────────────────────────────────────────────────


def _file(
    *,
    name: str = "report.pdf",
    size_bytes: int = 1024,
    file_id: str = "F001",
    mimetype: str | None = "application/pdf",
) -> SlackFileRef:
    return SlackFileRef(
        file_id=file_id,
        name=name,
        title=name,
        mimetype=mimetype,
        size_bytes=size_bytes,
        url_private_download=None,
        user_id="U123",
        created_ts=None,
    )


def _listing(files: list[SlackFileRef], truncated: bool = False) -> ChannelFileListing:
    return ChannelFileListing(
        channel_id="C123",
        files=tuple(files),
        total_count=len(files),
        truncated=truncated,
    )


def _report(
    *,
    saved_keys: tuple[str, ...] = (),
    missing: tuple[SlackFileRef, ...] = (),
    skipped: tuple[SlackFileRef, ...] = (),
    failures: tuple[FileFailure, ...] = (),
    truncated: bool = False,
) -> FileSyncReport:
    return FileSyncReport(
        channel_id="C123",
        s3_bucket="acme-nimbus",
        s3_prefix="slack/",
        scanned_count=len(missing) + len(skipped) + len(failures),
        total_count=len(missing) + len(skipped) + len(failures),
        truncated=truncated,
        missing_files=missing,
        saved_keys=saved_keys,
        skipped_files=skipped,
        failures=failures,
    )


# ── Block type helpers ───────────────────────────────────────────────────────


def _block_types(blocks: list[dict]) -> list[str]:
    return [b["type"] for b in blocks]


def _header_texts(blocks: list[dict]) -> list[str]:
    return [b["text"]["text"] for b in blocks if b["type"] == "header"]


def _section_texts(blocks: list[dict]) -> list[str]:
    return [b["text"]["text"] for b in blocks if b["type"] == "section" and "text" in b]


def _all_text(blocks: list[dict]) -> str:
    return " ".join(part for block in blocks for part in _block_text_parts(block))


def _block_text_parts(block: dict) -> list[str]:
    parts: list[str] = []
    block_text = block.get("text")
    if isinstance(block_text, dict):
        parts.append(block_text.get("text", ""))
    parts.extend(
        field.get("text", "")
        for field in block.get("fields", [])
        if isinstance(field, dict)
    )
    if block.get("type") == "context":
        parts.extend(
            element.get("text", "")
            for element in block.get("elements", [])
            if isinstance(element, dict)
        )
    if block.get("type") == "actions":
        for element in block.get("elements", []):
            text = element.get("text", {}) if isinstance(element, dict) else {}
            if isinstance(text, dict):
                parts.append(text.get("text", ""))
    return parts


# ── Format helpers ───────────────────────────────────────────────────────────


def test_format_size_bytes() -> None:
    assert _format_size(0) == "0 B"
    assert _format_size(512) == "512 B"
    assert _format_size(1023) == "1023 B"


def test_format_size_kb() -> None:
    assert _format_size(1024) == "1 KB"
    assert _format_size(2048) == "2 KB"
    assert _format_size(1536) == "1.5 KB"


def test_format_size_mb() -> None:
    assert _format_size(1024 * 1024) == "1 MB"
    assert _format_size(int(1.5 * 1024 * 1024)) == "1.5 MB"


def test_format_size_gb() -> None:
    assert _format_size(1024**3) == "1 GB"


def test_format_size_unknown_not_returned_for_valid_int() -> None:
    assert "B" in _format_size(999) or "KB" in _format_size(999)


def test_progress_bar_empty() -> None:
    bar = _progress_bar(0)
    assert "░" in bar
    assert "█" not in bar


def test_progress_bar_full() -> None:
    bar = _progress_bar(100)
    assert "█" in bar
    assert "░" not in bar


def test_progress_bar_half() -> None:
    bar = _progress_bar(50, width=10)
    assert bar.count("█") == 5
    assert bar.count("░") == 5


# ── File list card ───────────────────────────────────────────────────────────


def test_file_list_card_empty_channel() -> None:
    blocks = file_list_card(_listing([]))
    texts = _section_texts(blocks)
    assert any("No files" in t for t in texts)


def test_file_list_card_single_file() -> None:
    blocks = file_list_card(_listing([_file(name="deck.pdf", size_bytes=2048)]))
    assert "header" in _block_types(blocks)
    texts = " ".join(_section_texts(blocks))
    assert "deck.pdf" in texts


def test_file_list_card_shows_size() -> None:
    blocks = file_list_card(_listing([_file(name="big.zip", size_bytes=1024 * 1024)]))
    texts = " ".join(_section_texts(blocks))
    assert "1 MB" in texts


def test_file_list_card_truncation_note() -> None:
    files = [_file(name=f"f{i}.txt", file_id=f"F{i}", size_bytes=100) for i in range(5)]
    listing = ChannelFileListing(
        channel_id="C1",
        files=tuple(files),
        total_count=20,
        truncated=True,
    )
    blocks = file_list_card(listing)
    all_text = " ".join(str(b) for b in blocks)
    assert "20" in all_text or "page" in all_text.lower()


def test_file_list_card_many_files_collapsed() -> None:
    files = [_file(name=f"f{i}.txt", file_id=f"F{i}", size_bytes=50) for i in range(15)]
    blocks = file_list_card(_listing(files))
    texts = _section_texts(blocks)
    assert any("more" in t for t in texts)


def test_file_list_card_respects_max_blocks() -> None:
    files = [_file(name=f"f{i}.txt", file_id=f"F{i}", size_bytes=50) for i in range(60)]
    blocks = file_list_card(_listing(files))
    assert len(blocks) <= 50


# ── Diff report card ─────────────────────────────────────────────────────────


def test_diff_report_card_all_saved() -> None:
    skipped = (_file(name="saved.pdf", size_bytes=512),)
    blocks = diff_report_card(_report(skipped=skipped))
    headers = _header_texts(blocks)
    assert any("Everything in this channel is saved" in h for h in headers)


def test_diff_report_card_missing_files() -> None:
    missing = (_file(name="missing.pdf", size_bytes=1024),)
    blocks = diff_report_card(_report(missing=missing))
    headers = _header_texts(blocks)
    assert any("missing from S3" in h for h in headers)
    texts = _all_text(blocks)
    assert "1" in texts
    assert "missing.pdf" in texts


def test_diff_report_card_truncation_context() -> None:
    missing = (_file(name="x.pdf", file_id="F1", size_bytes=512),)
    blocks = diff_report_card(_report(missing=missing, truncated=True))
    all_text = " ".join(str(b) for b in blocks)
    assert "truncat" in all_text.lower()


# ── Save report card ─────────────────────────────────────────────────────────


def test_save_report_card_all_ok() -> None:
    blocks = save_report_card(_report(saved_keys=("slack/acme/C1/deck.pdf",)))
    headers = _header_texts(blocks)
    assert any("saved the channel files" in h.lower() for h in headers)


def test_save_report_card_with_failures() -> None:
    file = _file(name="broken.pdf", size_bytes=512)
    failures = (FileFailure(file=file, reason="timeout"),)
    blocks = save_report_card(_report(failures=failures))
    texts = " ".join(_section_texts(blocks))
    assert "broken.pdf" in texts or "timeout" in texts


def test_save_report_card_destination_shown() -> None:
    blocks = save_report_card(_report(saved_keys=("slack/k1",)))
    texts = " ".join(_section_texts(blocks))
    assert "acme-nimbus" in texts


# ── Changed since sync card ───────────────────────────────────────────────────


def test_changed_since_sync_card_no_changes() -> None:
    report = ChangedSinceSyncReport(
        channel_id="C1",
        s3_bucket="acme",
        new_files=(),
        resized_files=(),
        last_sync_at=datetime(2024, 1, 1, tzinfo=UTC),
        truncated=False,
    )
    blocks = changed_since_sync_card(report)
    headers = _header_texts(blocks)
    assert any("No files changed" in h for h in headers)


def test_changed_since_sync_card_new_files() -> None:
    report = ChangedSinceSyncReport(
        channel_id="C1",
        s3_bucket="acme",
        new_files=(_file(name="new.pdf"),),
        resized_files=(),
        last_sync_at=None,
        truncated=False,
    )
    blocks = changed_since_sync_card(report)
    texts = " ".join(_section_texts(blocks))
    assert "new.pdf" in texts


# ── Dedupe report card ───────────────────────────────────────────────────────


def test_dedupe_report_card_no_saved_files() -> None:
    report = DedupeReport(
        channel_id="C1",
        s3_bucket="acme",
        saved_count=0,
        duplicate_groups=(),
        stale_files=(),
        truncated=False,
    )
    blocks = dedupe_report_card(report)
    texts = " ".join(_section_texts(blocks))
    assert "No saved" in texts


def test_dedupe_report_card_clean() -> None:
    report = DedupeReport(
        channel_id="C1",
        s3_bucket="acme",
        saved_count=5,
        duplicate_groups=(),
        stale_files=(),
        truncated=False,
    )
    blocks = dedupe_report_card(report)
    headers = _header_texts(blocks)
    assert any("clean" in h.lower() for h in headers)


def test_dedupe_report_card_duplicates() -> None:
    dup = DuplicateGroup(
        content_sha256="abc123def456789012345678901234567890abcdef",
        keys=("k1", "k2"),
    )
    report = DedupeReport(
        channel_id="C1",
        s3_bucket="acme",
        saved_count=5,
        duplicate_groups=(dup,),
        stale_files=(),
        truncated=False,
    )
    blocks = dedupe_report_card(report)
    texts = " ".join(_section_texts(blocks))
    assert "k1" in texts
    assert "k2" in texts
    assert "abc123def456" in texts


def test_dedupe_report_card_stale_entries() -> None:
    stale = StaleSavedFile(file_id="F001", s3_key="slack/acme/old.pdf")
    report = DedupeReport(
        channel_id="C1",
        s3_bucket="acme",
        saved_count=3,
        duplicate_groups=(),
        stale_files=(stale,),
        truncated=False,
    )
    blocks = dedupe_report_card(report)
    texts = " ".join(_section_texts(blocks))
    assert "old.pdf" in texts


# ── Save progress card ───────────────────────────────────────────────────────


def test_save_progress_card_zero_progress() -> None:
    from nimbus_slack.file_sync import SaveProgress

    progress = SaveProgress(total=10, saved=0, skipped=0, failed=0)
    blocks = save_progress_card(progress)
    assert "header" in _block_types(blocks)
    assert "section" in _block_types(blocks)


def test_save_progress_card_partial() -> None:
    from nimbus_slack.file_sync import SaveProgress

    progress = SaveProgress(
        total=10,
        saved=5,
        skipped=2,
        failed=1,
        current_file=_file(name="current.pdf"),
    )
    blocks = save_progress_card(progress)
    all_text = " ".join(str(b) for b in blocks)
    assert "current.pdf" in all_text


# ── Task status card ─────────────────────────────────────────────────────────


def test_task_status_card_basic() -> None:
    blocks = task_status_card(
        task_id="task-abc123",
        status="scanning",
        intent="Save all files in this channel",
    )
    assert "header" in _block_types(blocks)
    texts = " ".join(_section_texts(blocks))
    assert "task-abc123" in texts
    assert "Save all files" in texts


def test_task_status_card_with_stats() -> None:
    blocks = task_status_card(
        task_id="task-xyz",
        status="done",
        intent="Backup",
        scanned_count=23,
        uploaded_count=6,
        skipped_count=17,
        failed_count=0,
        bytes_display="12.4 MB",
    )
    all_text = " ".join(str(b) for b in blocks)
    assert "23" in all_text
    assert "6" in all_text
    assert "12.4 MB" in all_text


def test_task_status_card_awaiting_label() -> None:
    blocks = task_status_card(
        task_id="task-1",
        status="awaiting_approval",
        intent="Delete stale files",
    )
    headers = _header_texts(blocks)
    assert any("awaiting approval" in h for h in headers)


def test_task_status_card_done_label() -> None:
    blocks = task_status_card(task_id="task-2", status="done", intent="Backup")
    headers = _header_texts(blocks)
    assert any("done" in h for h in headers)


def test_task_status_card_failed_label() -> None:
    blocks = task_status_card(task_id="task-3", status="failed", intent="Backup")
    headers = _header_texts(blocks)
    assert any("failed" in h for h in headers)


def test_task_status_card_shows_token_usage() -> None:
    blocks = task_status_card(
        task_id="task-4",
        status="done",
        intent="Backup",
        input_tokens=987,
        output_tokens=247,
    )
    all_text = str(blocks)
    assert "987" in all_text
    assert "247" in all_text


def test_task_status_card_shows_cost_usd() -> None:
    blocks = task_status_card(
        task_id="task-5",
        status="done",
        intent="Backup",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.0015,
    )
    all_text = str(blocks)
    assert "0.0015" in all_text


def test_task_status_card_no_cost_block_when_no_usage() -> None:
    blocks = task_status_card(task_id="task-6", status="done", intent="Backup")
    # There should be no token/cost context block when no usage data is given.
    context_texts = [
        e.get("text", "")
        for b in blocks
        if b.get("type") == "context"
        for e in (b.get("elements") or [])
        if isinstance(e, dict)
    ]
    assert not any("token" in t.lower() or "usd" in t.lower() for t in context_texts)


# ── Approval request card ────────────────────────────────────────────────────


def test_approval_request_card_structure() -> None:
    blocks = approval_request_card(
        action_id="act-deadbeef",
        target_display="slack/acme/C1/document.pdf",
        size_display="2.3 MB",
        sha256="deadbeefcafebabe12345678901234567890abcdef",
        requested_by="U1234",
        expires_at="2026-05-17T12:00:00Z",
    )
    headers = _header_texts(blocks)
    assert any("Approval" in h for h in headers)
    # Should have action buttons
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    assert len(action_blocks) == 1
    buttons = action_blocks[0]["elements"]
    assert len(buttons) == 2
    button_labels = [b["text"]["text"] for b in buttons]
    assert any("Approve" in label for label in button_labels)
    assert any("Reject" in label for label in button_labels)


def test_approval_request_card_action_ids() -> None:
    blocks = approval_request_card(
        action_id="act-test",
        target_display="test/file.pdf",
        size_display=None,
        sha256=None,
        requested_by="U1",
        expires_at="2026-05-18T00:00:00Z",
    )
    action_blocks = [b for b in blocks if b["type"] == "actions"]
    buttons = action_blocks[0]["elements"]
    action_ids = [b["action_id"] for b in buttons]
    assert "approve:act-test" in action_ids
    assert "reject:act-test" in action_ids


# ── Failure card ─────────────────────────────────────────────────────────────


def test_failure_card_basic() -> None:
    blocks = failure_card(
        title="Upload failed",
        detail="Connection timed out.",
        recoverable=True,
    )
    headers = _header_texts(blocks)
    assert any("Upload failed" in h for h in headers)
    texts = " ".join(_section_texts(blocks))
    assert "Connection timed out" in texts


def test_failure_card_terminal() -> None:
    blocks = failure_card(
        title="Policy denied",
        detail="Actor lacks permission.",
        recoverable=False,
    )
    all_text = " ".join(str(b) for b in blocks)
    assert (
        "cannot be retried" in all_text.lower()
        or "terminal" in all_text.lower()
        or "automatically" in all_text.lower()
    )


def test_failure_card_with_retry_hint() -> None:
    blocks = failure_card(
        title="Failed",
        detail="Something went wrong.",
        recoverable=True,
        retry_hint="Try the operation again.",
    )
    all_text = " ".join(str(b) for b in blocks)
    assert "Try the operation" in all_text


# ── Fallback text ────────────────────────────────────────────────────────────


def test_blocks_to_fallback_text_header() -> None:
    blocks = task_status_card(task_id="t1", status="done", intent="Backup files")
    text = blocks_to_fallback_text(blocks)
    assert "done" in text.lower() or "Done" in text
    assert len(text) > 0


def test_blocks_to_fallback_text_strips_mrkdwn() -> None:
    from nimbus_slack.blocks import _section

    blocks = [_section("*Bold text* and _italic text_")]
    text = blocks_to_fallback_text(blocks)
    assert "*" not in text
    assert "_" not in text
    assert "Bold text" in text
    assert "italic text" in text


def test_blocks_to_fallback_text_keeps_identifier_underscores() -> None:
    from nimbus_slack.blocks import _section

    blocks = [_section("Use `candidate_plans` and ask_user_choice.")]
    text = blocks_to_fallback_text(blocks)

    assert "candidate_plans" in text
    assert "ask_user_choice" in text


def test_blocks_to_fallback_text_empty_blocks() -> None:
    text = blocks_to_fallback_text([])
    assert text == ""


def test_blocks_to_fallback_text_divider_skipped() -> None:
    from nimbus_slack.blocks import _divider, _section

    blocks = [_section("Hello"), _divider(), _section("World")]
    text = blocks_to_fallback_text(blocks)
    assert "Hello" in text
    assert "World" in text


# ── Block count safety ────────────────────────────────────────────────────────


def test_file_list_card_never_exceeds_50_blocks() -> None:
    """Slack rejects payloads above 50 blocks."""
    files = [
        _file(name=f"file{i}.pdf", file_id=f"F{i}", size_bytes=1024) for i in range(100)
    ]
    blocks = file_list_card(
        ChannelFileListing(
            channel_id="C1",
            files=tuple(files),
            total_count=100,
            truncated=True,
        )
    )
    assert len(blocks) <= 50


def test_diff_report_card_never_exceeds_50_blocks() -> None:
    missing = tuple(
        _file(name=f"m{i}.pdf", file_id=f"M{i}", size_bytes=100) for i in range(30)
    )
    blocks = diff_report_card(_report(missing=missing))
    assert len(blocks) <= 50


# ── Feature 5: Action buttons on file/save/diff cards ─────────────────────


def test_file_list_card_appends_action_bar() -> None:
    """File list cards include quick-follow-up buttons."""
    listing = ChannelFileListing(
        channel_id="C1",
        files=(_file(name="design.fig", file_id="F1", size_bytes=1024),),
        total_count=1,
        truncated=False,
    )
    blocks = file_list_card(listing)
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) == 1
    action_ids = [
        e["action_id"] for e in action_blocks[0]["elements"] if isinstance(e, dict)
    ]
    assert "cmd:save_channel_files" in action_ids
    assert "cmd:dedupe_report" in action_ids
    assert "cmd:diff_channel_files" in action_ids


def test_empty_file_list_card_has_no_action_bar() -> None:
    """When no files are present, the card is just a 'nothing here' message."""
    listing = ChannelFileListing(
        channel_id="C1",
        files=(),
        total_count=0,
        truncated=False,
    )
    blocks = file_list_card(listing)
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert action_blocks == []


def test_diff_report_card_with_missing_files_offers_save_button() -> None:
    """Diff card with unsaved files should offer a primary 'Save' button."""
    missing = (_file(name="m.pdf", file_id="M1", size_bytes=100),)
    blocks = diff_report_card(_report(missing=missing))
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) == 1
    save_btn = next(
        e
        for e in action_blocks[0]["elements"]
        if isinstance(e, dict) and e["action_id"] == "cmd:save_channel_files"
    )
    assert save_btn.get("style") == "primary"


def test_diff_report_card_with_no_missing_omits_action_bar() -> None:
    """When nothing is unsaved, there's no follow-up action — show clean state."""
    blocks = diff_report_card(_report(missing=()))
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert action_blocks == []


def test_save_report_card_appends_action_bar() -> None:
    blocks = save_report_card(_report())
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    assert len(action_blocks) == 1


def test_save_report_card_includes_retry_when_failures_present() -> None:
    from nimbus_slack.file_sync import FileFailure

    report = _report(
        failures=(
            FileFailure(
                file=_file(name="bad.pdf", file_id="B1", size_bytes=10),
                reason="permission denied",
            ),
        ),
    )
    blocks = save_report_card(report)
    action_blocks = [b for b in blocks if b.get("type") == "actions"]
    action_ids = [
        e["action_id"] for e in action_blocks[0]["elements"] if isinstance(e, dict)
    ]
    assert "cmd:retry_save" in action_ids


# ── P8: workspace_status_card ────────────────────────────────────────────────


def _make_status_card(**overrides: int | str) -> list[dict]:
    defaults: dict = {
        "team_id": "T_TEST",
        "tasks_running": 2,
        "tasks_awaiting": 1,
        "tasks_done_today": 5,
        "tasks_failed": 0,
        "pending_approvals": 1,
        "proposed_plans": 3,
    }
    defaults.update(overrides)
    return workspace_status_card(**defaults)  # type: ignore[arg-type]


def test_workspace_status_card_returns_list_of_dicts() -> None:
    """workspace_status_card must return a non-empty list of dicts."""
    blocks = _make_status_card()
    assert isinstance(blocks, list)
    assert len(blocks) >= 1
    assert all(isinstance(b, dict) for b in blocks)


def test_workspace_status_card_has_header_block() -> None:
    """The first block should be a plain health header; team ID lives in the body."""
    blocks = _make_status_card(team_id="T_MYTEAM")
    header = blocks[0]
    assert header["type"] == "header"
    assert header["text"]["text"] == "Nimbus workspace health"
    assert "T_MYTEAM" in _all_text(blocks)


def test_workspace_status_card_green_when_no_failures() -> None:
    """When no failures and no pending approvals the body says the workspace is healthy."""
    blocks = _make_status_card(tasks_failed=0, pending_approvals=0)
    assert "Everything looks healthy" in _all_text(blocks)


def test_workspace_status_card_warning_when_failed() -> None:
    """When tasks have failed the body says something needs attention."""
    blocks = _make_status_card(tasks_failed=2)
    assert "need attention" in _all_text(blocks)


def test_workspace_status_card_warning_when_pending_approvals() -> None:
    """Pending approvals alone should also say something needs attention."""
    blocks = _make_status_card(tasks_failed=0, pending_approvals=3)
    assert "need attention" in _all_text(blocks)


def test_workspace_status_card_section_contains_all_counters() -> None:
    """The conversational status body must include all six metric counts."""
    blocks = _make_status_card(
        tasks_running=3,
        tasks_awaiting=1,
        tasks_done_today=7,
        tasks_failed=2,
        pending_approvals=4,
        proposed_plans=5,
    )
    text = _all_text(blocks)
    assert "3 running" in text
    assert "1 awaiting approval" in text
    assert "7 done today" in text
    assert "2 failed" in text
    assert "4 pending approval" in text
    assert "5 proposed plan" in text


def test_workspace_status_card_context_mentions_commands() -> None:
    """The context block should hint at follow-up CLI/Slack commands."""
    blocks = _make_status_card()
    context_texts = [
        el["text"]
        for b in blocks
        if b.get("type") == "context"
        for el in b.get("elements", [])
        if isinstance(el, dict)
    ]
    combined = " ".join(context_texts)
    assert "task list" in combined or "plan show" in combined


def test_workspace_status_card_empty_team_id_omits_id_from_header() -> None:
    """Passing an empty team_id should omit the ID from the card."""
    blocks = _make_status_card(team_id="")
    assert "for ``" not in _all_text(blocks)


def test_workspace_status_card_fallback_text_is_readable() -> None:
    """blocks_to_fallback_text must extract meaningful text from the card."""
    blocks = _make_status_card()
    text = blocks_to_fallback_text(blocks)
    assert "Nimbus" in text


# ── P2: app_home_card ────────────────────────────────────────────────────────


def _make_home_card(**overrides: int | str) -> list[dict]:
    defaults: dict = {
        "team_id": "T_HOME",
        "tasks_running": 1,
        "tasks_awaiting": 0,
        "tasks_done_today": 3,
        "tasks_failed": 0,
        "pending_approvals": 0,
        "proposed_plans": 2,
    }
    defaults.update(overrides)
    return app_home_card(**defaults)  # type: ignore[arg-type]


def test_app_home_card_returns_non_empty_list() -> None:
    """app_home_card must return a non-empty list of block dicts."""
    blocks = _make_home_card()
    assert isinstance(blocks, list)
    assert len(blocks) >= 1
    assert all(isinstance(b, dict) for b in blocks)


def test_app_home_card_has_welcome_header() -> None:
    """First block must be a header containing 'Nimbus'."""
    blocks = _make_home_card(team_id="T_WELCOME")
    header = blocks[0]
    assert header["type"] == "header"
    assert "Nimbus" in header["text"]["text"]


def test_app_home_card_includes_team_id_in_header_when_present() -> None:
    """When a non-empty team_id is given it appears in the header text."""
    blocks = _make_home_card(team_id="MYTEAM123")
    header_text = blocks[0]["text"]["text"]
    assert "MYTEAM123" in header_text


def test_app_home_card_healthy_status_copy() -> None:
    """No failures and no pending approvals should say the workspace is healthy."""
    blocks = _make_home_card(tasks_failed=0, pending_approvals=0)
    assert "Everything looks healthy" in _all_text(blocks)


def test_app_home_card_attention_status_copy_when_failed() -> None:
    """Failed tasks should say something needs attention."""
    blocks = _make_home_card(tasks_failed=1)
    assert "need attention" in _all_text(blocks)


def test_app_home_card_section_contains_counters() -> None:
    """The workspace health section must contain all six metric counts."""
    blocks = _make_home_card(
        tasks_running=7,
        tasks_awaiting=2,
        tasks_done_today=10,
        tasks_failed=3,
        pending_approvals=1,
        proposed_plans=4,
    )
    text = _all_text(blocks)
    assert "7 running" in text
    assert "2 awaiting approval" in text
    assert "10 done today" in text
    assert "3 failed" in text
    assert "1 pending approval" in text
    assert "4 proposed plan" in text


def test_app_home_card_quick_commands_section_present() -> None:
    """Card must include a section listing quick `@Nimbus` commands."""
    blocks = _make_home_card()
    section_texts = [
        b["text"]["text"]
        for b in blocks
        if b.get("type") == "section" and isinstance(b.get("text"), dict)
    ]
    combined = " ".join(section_texts)
    assert "save" in combined.lower()
    assert "@Nimbus" in combined or "@nimbus" in combined.lower()
    assert "nimbus stack diff" in combined
    assert "nimbus proof show latest" in combined


def test_storage_stack_card_exposes_stack_and_plan_ids() -> None:
    """Storage stack Slack cards should carry CLI-inspectable IDs."""
    blocks = storage_stack_card(
        stack_id="stk-123",
        status="conflicted",
        plan_id="plan-123",
        change_count=2,
        conflict_count=1,
        next_step="run restack",
    )
    text = _all_text(blocks)
    assert "stk-123" in text
    assert "plan-123" in text
    assert "nimbus stack diff stk-123" in text


def test_policy_patch_card_exposes_review_command() -> None:
    """Policy patch Slack cards should make review authority visible."""
    blocks = policy_patch_card(
        proposal_id="pprop-123",
        status="proposed",
        base_policy="runtime-default-v1",
        proposed_policy="runtime-default-v2",
        capability="delete_file",
        reviewer="U123",
    )
    text = _all_text(blocks)
    assert "pprop-123" in text
    assert "delete_file" in text
    assert "nimbus policy patch accept pprop-123" in text


def test_app_home_card_respects_max_blocks_limit() -> None:
    """app_home_card must never return more than 50 blocks."""
    blocks = _make_home_card()
    assert len(blocks) <= 50


# ── search_results_card ──────────────────────────────────────────────────────


def _make_search_result(
    *,
    title: str = "Budget Report Q3",
    source_uri: str = "slack://T123/C999/F001",
    score: float = 3.0,
    snippet: str = "quarterly budget figures",
    channel_id: str | None = "C999",
) -> object:
    """Return a minimal SearchResult-like object for block rendering tests."""
    from datetime import UTC, datetime

    from nimbus_runtime.domain import TenantIdentity
    from nimbus_runtime.search import (
        SearchChunkHit,
        SearchDocument,
        SearchDocumentStatus,
        SearchResult,
    )

    tenant = TenantIdentity(platform="slack", workspace_id="T123")
    doc = SearchDocument(
        tenant=tenant,
        document_id="doc-001",
        source_uri=source_uri,
        object_key="slack/T123/C999/budget.pdf",
        title=title,
        content_type="application/pdf",
        size_bytes=10240,
        status=SearchDocumentStatus.SEARCHABLE,
        channel_id=channel_id,
        indexed_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    chunk_hit = SearchChunkHit(
        chunk_id="chunk-0",
        chunk_index=0,
        snippet=snippet,
        score=score,
    )
    return SearchResult(
        document=doc,
        score=score,
        chunk_hits=(chunk_hit,),
        citations=(f"{source_uri}:chunk:0",),
        indexed_at=doc.indexed_at,
    )


def test_search_results_card_empty_returns_no_results_message() -> None:
    """An empty result set should explain that no documents matched."""
    blocks = search_results_card(query="budget", results=[])

    block_texts = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict) else ""
        for b in blocks
    )
    assert "No indexed documents" in block_texts


def test_search_results_card_contains_query_in_header() -> None:
    """The card header should include the search query text."""
    blocks = search_results_card(query="quarterly budget", results=[])

    header_block = next(b for b in blocks if b["type"] == "header")
    assert "quarterly budget" in header_block["text"]["text"]


def test_search_results_card_single_result_contains_title() -> None:
    """A result's document title should appear in the rendered card."""
    result = _make_search_result(title="Security Policy v2")
    blocks = search_results_card(query="security", results=[result])

    all_text = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict) else ""
        for b in blocks
    )
    assert "Security Policy v2" in all_text


def test_search_results_card_shows_snippet() -> None:
    """Chunk snippets should be visible in the rendered card."""
    result = _make_search_result(snippet="the quarterly revenue exceeded targets")
    blocks = search_results_card(query="revenue", results=[result])

    all_text = " ".join(
        b.get("text", {}).get("text", "") if isinstance(b.get("text"), dict) else ""
        for b in blocks
    )
    assert "quarterly revenue exceeded targets" in all_text


def test_search_results_card_shows_score_in_context() -> None:
    """The result score should appear in a context block."""
    result = _make_search_result(score=4.5)
    blocks = search_results_card(query="test", results=[result])

    context_texts: list[str] = []
    for block in blocks:
        if block.get("type") == "context":
            context_texts.extend(
                el.get("text", "")
                for el in block.get("elements", [])
                if isinstance(el, dict)
            )
    combined = " ".join(context_texts)
    assert "4.5" in combined


def test_search_results_card_respects_max_blocks_limit() -> None:
    """search_results_card must never return more than 50 blocks."""
    results = [_make_search_result(title=f"Doc {i}") for i in range(30)]
    blocks = search_results_card(query="docs", results=results)
    assert len(blocks) <= 50


def test_search_results_card_shows_result_count() -> None:
    """A summary line mentioning total result count should appear."""
    results = [_make_search_result()]
    blocks = search_results_card(query="budget", results=results)

    context_texts: list[str] = []
    for block in blocks:
        if block.get("type") == "context":
            context_texts.extend(
                el.get("text", "")
                for el in block.get("elements", [])
                if isinstance(el, dict)
            )
    combined = " ".join(context_texts)
    assert "1" in combined and "result" in combined


# ── Capability list card ────────────────────────────────────────────────────


def test_capability_list_card_shows_current_and_roadmap_tools() -> None:
    """Slack capability discovery should use the shared runtime catalog."""
    blocks = capability_list_card(all_capabilities())
    text = _all_text(blocks)

    assert "Nimbus tools" in text
    assert "list_files" in text
    assert "candidate_plans" in text
    assert "nimbus tools list" in text
