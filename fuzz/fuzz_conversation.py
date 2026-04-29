#!/usr/bin/env python3
"""Atheris fuzz harness for Conversation deserialisation.

Target: ``Conversation.from_json`` and the inner ``_message_from_dict`` parser.

Goal: Prove that no byte sequence reachable through the on-disk JSON format
can cause an unhandled exception in the deserialisation path.  The only
exceptions permitted are ``ValueError``, ``TypeError``, and ``KeyError``,
which are the ones ``load_session`` already catches.  Any other exception
(e.g. ``AttributeError``, ``IndexError``, ``UnicodeDecodeError``) would
surface as a 500 in production.

Running
-------
Install Atheris (Linux preferred; macOS requires a libFuzzer-enabled Clang):

    pip install atheris          # Linux (ships with a compatible Clang wheel)
    # macOS:
    CC=/opt/homebrew/opt/llvm/bin/clang pip install atheris

Run the harness::

    python fuzz/fuzz_conversation.py          # unlimited, runs until crash or Ctrl-C
    python fuzz/fuzz_conversation.py -max_total_time=60   # 60-second CI budget

Supply a corpus directory to guide mutation::

    mkdir -p fuzz/corpus/conversation
    python fuzz/fuzz_conversation.py fuzz/corpus/conversation -max_total_time=60

Run without Atheris (plain Python, no coverage guidance) for smoke-testing on
platforms where Atheris is unavailable::

    PYTHONFUZZ_NO_ATHERIS=1 python fuzz/fuzz_conversation.py
"""

from __future__ import annotations

import json
import os
import sys

# ---------------------------------------------------------------------------
# Allow running without Atheris for smoke-testing on macOS without libFuzzer.
# ---------------------------------------------------------------------------

_NO_ATHERIS = os.environ.get("PYTHONFUZZ_NO_ATHERIS", "").strip() == "1"

if _NO_ATHERIS:
    import random

    def _smoke_run() -> None:
        """Run a small set of hand-crafted and random inputs without Atheris."""
        _corpus = [
            b"",
            b"null",
            b"[]",
            b"{}",
            b'{"schema_version": 1, "system": "s", "session_id": "x", '
            b'"max_messages": 20, "max_total_tokens": 8000, "messages": []}',
            b'{"schema_version": 1, "system": "s", "session_id": "x", '
            b'"max_messages": 20, "max_total_tokens": 8000, "messages": '
            b'[{"role": "user", "content": "hi", "tool_calls": [], '
            b'"tool_call_id": null, "name": null}]}',
            b'{"schema_version": 2}',  # wrong version
            b'{"schema_version": true}',  # bool version
            b'{"schema_version": 1, "messages": "not-a-list"}',
        ]
        # Add random noise entries.
        rng = random.Random(42)
        for _ in range(200):
            size = rng.randint(0, 512)
            _corpus.append(bytes(rng.randint(0, 255) for _ in range(size)))

        for payload in _corpus:
            TestOneInput(payload)
        print(f"Smoke run complete: {len(_corpus)} inputs, no unexpected crashes.")

else:
    import atheris  # type: ignore[import]

# ---------------------------------------------------------------------------
# The import target — instrument for coverage if Atheris is available.
# ---------------------------------------------------------------------------

if not _NO_ATHERIS:
    with atheris.instrument_imports():
        import json as _json  # re-import so Atheris instruments json internals too

        from ai_client_api.conversation import Conversation, _message_from_dict
else:
    from ai_client_api.conversation import Conversation, _message_from_dict

# ---------------------------------------------------------------------------
# Fuzz target
# ---------------------------------------------------------------------------

_ALLOWED_EXCEPTIONS = (ValueError, TypeError, KeyError, UnicodeDecodeError)


def TestOneInput(data: bytes) -> None:  # noqa: N802 — Atheris expects this exact name
    """Feed ``data`` through the full deserialisation pipeline.

    Any exception other than the expected ones is a finding.
    """
    # Step 1: attempt JSON parse.
    try:
        decoded = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return  # invalid JSON is expected; not a finding

    # Step 2: attempt full Conversation deserialisation.
    if isinstance(decoded, dict):
        try:
            Conversation.from_json(decoded)
        except _ALLOWED_EXCEPTIONS:
            return
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"Unexpected exception from Conversation.from_json: "
                f"{type(exc).__name__}: {exc}"
            ) from exc

    # Step 3: attempt single-message deserialisation (exercises _message_from_dict).
    try:
        _message_from_dict(decoded)
    except _ALLOWED_EXCEPTIONS:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"Unexpected exception from _message_from_dict: "
            f"{type(exc).__name__}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if _NO_ATHERIS:
        _smoke_run()
    else:
        atheris.Setup(sys.argv, TestOneInput)
        atheris.Fuzz()
