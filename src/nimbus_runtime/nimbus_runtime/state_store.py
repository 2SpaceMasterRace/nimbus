"""Persistent expiring runtime state for confirmation flows."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_SESSION_DIR = Path.home() / ".nimbus" / "sessions" / "ai_server"
_REQUEST_STATE_DIRNAME = "_request_state"
_CLEANUP_INTERVAL_SECONDS = 60.0

_namespace_locks: dict[str, threading.Lock] = {}
_namespace_locks_guard = threading.Lock()
_last_cleanup_at: dict[str, float] = {}


@dataclass(frozen=True, slots=True)
class StateReadResult:
    """Result of reading one expiring runtime-state entry."""

    value: dict[str, object] | None
    cleaned_entries: int = 0


def _session_dir() -> Path:
    raw = os.environ.get("AI_SESSION_DIR", "").strip()
    return Path(raw) if raw else _DEFAULT_SESSION_DIR


def _namespace_dir(namespace: str) -> Path:
    return _session_dir() / _REQUEST_STATE_DIRNAME / namespace


def _namespace_lock(namespace: str) -> threading.Lock:
    with _namespace_locks_guard:
        lock = _namespace_locks.get(namespace)
        if lock is None:
            lock = threading.Lock()
            _namespace_locks[namespace] = lock
        return lock


def _entry_path(namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return _namespace_dir(namespace) / f"sha256-{digest}.json"


def _write_entry(
    *, namespace: str, key: str, value: dict[str, object], expires_at: float
) -> None:
    path = _entry_path(namespace, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"key": key, "expires_at": expires_at, "value": value}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_live_value(path: Path, *, now: float) -> tuple[dict[str, object] | None, int]:
    if not path.is_file():
        return None, 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        expires_at = float(data["expires_at"])
        value = data["value"]
    except (KeyError, OSError, TypeError, ValueError):
        path.unlink(missing_ok=True)
        return None, 1
    if not isinstance(value, dict):
        path.unlink(missing_ok=True)
        return None, 1
    if expires_at <= now:
        path.unlink(missing_ok=True)
        return None, 1
    return value, 0


def _cleanup_namespace_locked(namespace: str, *, now: float) -> int:
    last_cleanup = _last_cleanup_at.get(namespace)
    if last_cleanup is not None and (now - last_cleanup) < _CLEANUP_INTERVAL_SECONDS:
        return 0

    namespace_dir = _namespace_dir(namespace)
    _last_cleanup_at[namespace] = now
    if not namespace_dir.is_dir():
        return 0

    cleaned_entries = 0
    for path in namespace_dir.iterdir():
        if path.suffix != ".json":
            continue
        _, cleaned = _read_live_value(path, now=now)
        cleaned_entries += cleaned
    return cleaned_entries


def get_state(namespace: str, key: str) -> StateReadResult:
    """Return an unexpired runtime-state entry for *key*, if present."""
    lock = _namespace_lock(namespace)
    with lock:
        now = time.time()
        cleaned_entries = _cleanup_namespace_locked(namespace, now=now)
        value, cleaned_target = _read_live_value(_entry_path(namespace, key), now=now)
        return StateReadResult(
            value=value,
            cleaned_entries=cleaned_entries + cleaned_target,
        )


def put_state(
    namespace: str,
    key: str,
    *,
    value: dict[str, object],
    expires_at: float,
) -> int:
    """Store runtime state for *key* until *expires_at*."""
    lock = _namespace_lock(namespace)
    with lock:
        now = time.time()
        cleaned_entries = _cleanup_namespace_locked(namespace, now=now)
        _write_entry(namespace=namespace, key=key, value=value, expires_at=expires_at)
        return cleaned_entries


def delete_state(namespace: str, key: str) -> None:
    """Delete any persisted runtime-state entry for *key*."""
    lock = _namespace_lock(namespace)
    with lock:
        _entry_path(namespace, key).unlink(missing_ok=True)
