#!/usr/bin/env python3
"""Atheris fuzz harness for the persistent request-state reader.

Target: ``_read_live_value`` — the function that parses a JSON record from
a state file and decides whether the entry is alive or expired.

Goal: Prove that no byte sequence simulating a corrupted on-disk state file
causes an unhandled exception.  The function is expected to return
``(None, 1)`` for any unreadable or malformed content by deleting the file
and returning gracefully.  A crash here would mean corrupted nonce-state or
idempotency-state files can take down the server.

Strategy: Write a temporary file containing the fuzz input, then call
``_read_live_value`` on it.  Clean up regardless.

Running
-------
    pip install atheris
    python fuzz/fuzz_request_state.py
    python fuzz/fuzz_request_state.py -max_total_time=60
    PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_request_state.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_NO_ATHERIS = os.environ.get("PYTHONFUZZ_NO_ATHERIS", "").strip() == "1"

if _NO_ATHERIS:
    import random

    def _smoke_run() -> None:
        _corpus = [
            b"",
            b"null",
            b"{}",
            b"[]",
            b'{"key": "k", "expires_at": 9999999999.0, "value": {}}',
            b'{"key": "k", "expires_at": 0.0, "value": {}}',  # already expired
            b'{"expires_at": "not-a-float", "value": {}}',
            b'{"expires_at": 9999999999.0, "value": "not-a-dict"}',
            b'{"expires_at": 9999999999.0}',  # missing "value"
            b"\x00\x01\x02\x03",  # binary noise
            b"not-valid-json{{{{",
        ]
        rng = random.Random(7)
        for _ in range(500):
            size = rng.randint(0, 256)
            _corpus.append(bytes(rng.randint(0, 255) for _ in range(size)))

        for payload in _corpus:
            TestOneInput(payload)
        print(f"Smoke run complete: {len(_corpus)} inputs, no unexpected crashes.")

else:
    import atheris  # type: ignore[import]

if not _NO_ATHERIS:
    with atheris.instrument_imports():
        from ai_server.request_state import _read_live_value
else:
    from ai_server.request_state import _read_live_value


def TestOneInput(data: bytes) -> None:  # noqa: N802
    """Write ``data`` to a temp file and parse it through ``_read_live_value``."""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "state.json"
        p.write_bytes(data)
        now = time.time()
        try:
            value, cleaned = _read_live_value(p, now=now)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"Unexpected exception from _read_live_value: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        # Invariants on the return value.
        if value is not None and not isinstance(value, dict):
            raise AssertionError(
                f"_read_live_value returned non-dict value: {type(value)}"
            )
        if cleaned not in (0, 1):
            raise AssertionError(
                f"_read_live_value returned unexpected cleaned count: {cleaned}"
            )


if __name__ == "__main__":
    if _NO_ATHERIS:
        _smoke_run()
    else:
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
