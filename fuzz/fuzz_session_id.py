#!/usr/bin/env python3
"""Atheris fuzz harness for session-ID validation.

Target: ``_validate_session_id`` and ``_session_file_stem``.

Goal: Prove that no byte sequence, however adversarial, causes any exception
other than ``ValueError`` from the validation gate.  A crash, ``OSError``, or
any other exception type would indicate a path-traversal or filesystem escape
vulnerability — the security boundary this module enforces.

Running
-------
    pip install atheris                      # Linux
    CC=/opt/homebrew/opt/llvm/bin/clang pip install atheris  # macOS

    python fuzz/fuzz_session_id.py
    python fuzz/fuzz_session_id.py -max_total_time=60
    PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_session_id.py   # no Atheris
"""

from __future__ import annotations

import os
import sys

_NO_ATHERIS = os.environ.get("PYTHONFUZZ_NO_ATHERIS", "").strip() == "1"

if _NO_ATHERIS:
    import random

    def _smoke_run() -> None:
        _corpus = [
            b"",
            b"x",
            b"abc123",
            b"a" * 128,
            b"a" * 129,
            b"../../etc/passwd",
            b"foo/bar",
            b"foo\x00bar",
            b"foo bar",
            b"foo\\bar",
            b"C08ABCDEF12",
            b"1234567890.123456",
            b"slack:T123TEAM:event:evt-abc",
        ]
        rng = random.Random(0)
        for _ in range(500):
            size = rng.randint(0, 300)
            _corpus.append(bytes(rng.randint(0, 127) for _ in range(size)))

        for payload in _corpus:
            TestOneInput(payload)
        print(f"Smoke run complete: {len(_corpus)} inputs, no unexpected crashes.")

else:
    import atheris  # type: ignore[import]

if not _NO_ATHERIS:
    with atheris.instrument_imports():
        from ai_server.sessions import _session_file_stem, _validate_session_id
else:
    from ai_server.sessions import _session_file_stem, _validate_session_id


def TestOneInput(data: bytes) -> None:  # noqa: N802
    """Feed raw bytes through the session-ID validation and stem derivation."""
    # Decode as a Python str using latin-1 so every byte sequence is valid.
    # _validate_session_id only allows ASCII-safe chars, so non-ASCII bytes
    # will be rejected — but they must never cause an unexpected exception.
    try:
        session_id = data.decode("latin-1")
    except Exception:  # noqa: BLE001
        return  # should not happen with latin-1, but be safe

    # --- _validate_session_id ---
    # Must raise ValueError for unsafe IDs, nothing else.
    try:
        _validate_session_id(session_id)
    except ValueError:
        return  # expected rejection
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"Unexpected exception from _validate_session_id: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    # If validation passed, _session_file_stem must succeed too.
    try:
        stem = _session_file_stem(session_id)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"_validate_session_id passed but _session_file_stem raised: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    # The stem must be a non-empty string containing no path separators.
    if not stem:
        raise AssertionError("_session_file_stem returned an empty string")
    if "/" in stem or "\\" in stem or "\x00" in stem:
        raise AssertionError(
            f"_session_file_stem returned a stem with a path separator: {stem!r}"
        )


if __name__ == "__main__":
    if _NO_ATHERIS:
        _smoke_run()
    else:
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
