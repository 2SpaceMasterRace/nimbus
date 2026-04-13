"""File-based conversation persistence for the AI server.

Each session is stored as a single JSON file at
``<session_dir>/<session_id>.json``.  Writes are atomic (rename-from-temp)
so a crash mid-write cannot corrupt the file.

Concurrency note: two simultaneous requests for the same ``session_id`` can
race on the save.  For the MVP this is acceptable — add a per-session
``asyncio.Lock`` when concurrent Slack threads become a concern.
"""

from __future__ import annotations

import json
import re
from pathlib import Path  # noqa: TC003

from openrouter_ai_client_impl.config import DEFAULT_SYSTEM_PROMPT

from ai_client_api import Conversation

# Allow Slack IDs (C0ABC123, U01XYZ, T…), UUIDs, and simple strings.
# Reject anything that could escape the session directory via path traversal.
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:]{1,128}$")


def _validate_session_id(session_id: str) -> None:
    """Raise ``ValueError`` if *session_id* contains unsafe characters."""
    if not _SAFE_SESSION_ID_RE.match(session_id):
        msg = (
            f"session_id {session_id!r} contains unsafe characters or exceeds "
            "128 characters.  Only alphanumerics, hyphens, underscores, dots, "
            "and colons are allowed."
        )
        raise ValueError(msg)


def _session_path(session_dir: Path, session_id: str) -> Path:
    return session_dir / f"{session_id}.json"


def load_session(
    session_dir: Path,
    session_id: str,
    system_prompt: str | None = None,
) -> Conversation:
    """Return the persisted conversation, or a fresh one if not found.

    A corrupted or unreadable session file is silently discarded and a
    fresh ``Conversation`` is returned — better to lose history than to
    crash a live request.

    Args:
        session_dir: Directory that contains ``<session_id>.json`` files.
        session_id: Unique conversation key (e.g. a Slack channel ID).
        system_prompt: System prompt for newly created sessions.  Defaults
            to the Nimbus default prompt.

    Returns:
        Loaded or freshly created ``Conversation`` with ``session_id`` set.

    Raises:
        ValueError: *session_id* contains path-unsafe characters.

    """
    _validate_session_id(session_id)
    path = _session_path(session_dir, session_id)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Conversation.from_json(data)
        except (ValueError, TypeError, KeyError, OSError):
            pass  # fall through to create a fresh conversation
    return Conversation(
        system=system_prompt or DEFAULT_SYSTEM_PROMPT,
        session_id=session_id,
    )


def save_session(session_dir: Path, session_id: str, conv: Conversation) -> None:
    """Persist *conv* to ``<session_dir>/<session_id>.json`` atomically.

    Creates ``session_dir`` (and any parents) if it does not exist.

    Args:
        session_dir: Directory in which to write the session file.
        session_id: Unique conversation key used as the filename stem.
        conv: Conversation state to persist.

    Raises:
        ValueError: *session_id* contains path-unsafe characters.

    """
    _validate_session_id(session_id)
    path = _session_path(session_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(conv.to_json(), indent=2), encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX; near-atomic on Windows (Py 3.3+)
