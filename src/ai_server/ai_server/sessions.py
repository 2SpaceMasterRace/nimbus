"""File-based conversation persistence for the AI server.

Each session is stored as a single JSON file under ``session_dir``. Safe,
short session IDs are used directly as the filename stem. Longer logical
session IDs are mapped to a deterministic SHA-256-based stem so wrapper-facing
conversation identities can exceed filesystem-friendly name limits without
changing the public session identity stored in the JSON payload.

Writes are atomic (rename-from-temp) so a crash mid-write cannot corrupt the
file.

Concurrency is handled at the router layer via per-session ``asyncio.Lock``
objects (see ``router._get_session_lock``), which serialise concurrent
requests that share the same ``session_id`` before they touch the
filesystem.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path  # noqa: TC003

from openrouter_ai_client_impl.config import DEFAULT_SYSTEM_PROMPT

from ai_client_api import Conversation

# Allow Slack IDs (C0ABC123, U01XYZ, T…), UUIDs, and simple strings.
# Reject anything that could escape the session directory via path traversal.
_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-.:]+$")
_MAX_SESSION_FILENAME_STEM_LENGTH = 128
_HASHED_SESSION_STEM_PREFIX = "sha256-"


def _validate_session_id(session_id: str) -> None:
    """Raise ``ValueError`` if *session_id* contains unsafe characters."""
    if not session_id or not _SAFE_SESSION_ID_RE.match(session_id):
        msg = (
            f"session_id {session_id!r} contains unsafe characters. Only "
            "alphanumerics, hyphens, underscores, dots, and colons are allowed."
        )
        raise ValueError(msg)


def _session_file_stem(session_id: str) -> str:
    """Return a filesystem-safe filename stem for a logical session ID."""
    _validate_session_id(session_id)
    if len(session_id) <= _MAX_SESSION_FILENAME_STEM_LENGTH:
        return session_id
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"{_HASHED_SESSION_STEM_PREFIX}{digest}"


def _session_path(session_dir: Path, session_id: str) -> Path:
    return session_dir / f"{_session_file_stem(session_id)}.json"


def session_exists(session_dir: Path, session_id: str) -> bool:
    """Return whether a persisted session file exists for *session_id*."""
    _validate_session_id(session_id)
    return _session_path(session_dir, session_id).is_file()


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


def delete_session(session_dir: Path, session_id: str) -> bool:
    """Delete the session file for *session_id*, if it exists.

    Args:
        session_dir: Directory that contains ``<session_id>.json`` files.
        session_id: Unique conversation key to delete.

    Returns:
        ``True`` if a file was found and removed; ``False`` if it did not
        exist (idempotent — safe to call multiple times).

    Raises:
        ValueError: *session_id* contains path-unsafe characters.

    """
    _validate_session_id(session_id)
    path = _session_path(session_dir, session_id)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    else:
        return True


def list_sessions(session_dir: Path) -> list[str]:
    """Return the session IDs of all persisted conversations.

    Scans *session_dir* for ``*.json`` files and returns their stems.
    Temporary ``.tmp`` files written during atomic saves are ignored.
    Returns an empty list if the directory does not exist.

    Args:
        session_dir: Directory that contains ``<session_id>.json`` files.

    Returns:
        Sorted list of session ID strings.

    """
    if not session_dir.is_dir():
        return []

    session_ids: list[str] = []
    for path in session_dir.iterdir():
        if path.suffix != ".json":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError, TypeError):
            session_ids.append(path.stem)
            continue
        session_id = data.get("session_id")
        session_ids.append(
            str(session_id) if isinstance(session_id, str) else path.stem
        )
    return sorted(session_ids)
