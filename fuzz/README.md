# Fuzz Harnesses

Coverage-guided and smoke-mode fuzz harnesses for security-relevant parsing
paths in Nimbus.

These harnesses target code that reads untrusted or corrupted data: persisted
conversation JSON, session IDs, and request-state files. The requirement is
simple: malformed input may be rejected, but it must not crash with an
unexpected exception or escape the intended filesystem boundary.

## Harness Map

| Harness | Target | Protects against |
| --- | --- | --- |
| `fuzz_conversation.py` | `Conversation.from_json`, `_message_from_dict` | Corrupt session files crashing restore |
| `fuzz_session_id.py` | `_validate_session_id`, `_session_file_stem` | Path traversal and invalid session IDs |
| `fuzz_request_state.py` | `_read_live_value` | Corrupt replay/idempotency state crashing reads |

## Smoke Mode

Smoke mode runs without Atheris or libFuzzer. It is the easiest local check and
the right default for CI environments without a fuzzing toolchain:

```bash
PYTHONFUZZ_NO_ATHERIS=1 uv run python fuzz/fuzz_conversation.py
PYTHONFUZZ_NO_ATHERIS=1 uv run python fuzz/fuzz_session_id.py
PYTHONFUZZ_NO_ATHERIS=1 uv run python fuzz/fuzz_request_state.py
```

Smoke mode uses deterministic hand-crafted and random inputs. It does not
provide coverage-guided mutation, but it exercises the same invariants as the
full harness.

## Full Fuzzing Setup

### Linux

```bash
uv pip install atheris
```

The Linux Atheris wheel includes libFuzzer support, so no extra compiler setup
is usually needed.

### macOS

Apple's system `clang` does not include libFuzzer. Install LLVM and use its
compiler while installing Atheris:

```bash
brew install llvm
CC="$(brew --prefix llvm)/bin/clang" uv pip install atheris
```

## Running Atheris

```bash
# Run until interrupted
uv run python fuzz/fuzz_conversation.py

# Run for a fixed 60-second budget
uv run python fuzz/fuzz_conversation.py -max_total_time=60

# Run with a seed corpus
mkdir -p fuzz/corpus/conversation
uv run python fuzz/fuzz_conversation.py fuzz/corpus/conversation -max_total_time=60
```

The same flags work for all harnesses.

## Reproducing Findings

Atheris prints a reproducing input and writes a `crash-*` file when it finds a
failure. Re-run the harness with that file after fixing the bug:

```bash
uv run python fuzz/fuzz_conversation.py crash-<hash>
```

Keep any minimized regression input if it documents a lasting boundary case, and
add a normal pytest regression test when the bug belongs in the standard suite.

## Invariants

The harnesses allow only exception types already handled by the production
boundary. Any unexpected exception is raised as `AssertionError`, which Atheris
treats as a crash.

`fuzz_session_id.py` also asserts that accepted session file stems contain no
path separators, backslashes, or NUL bytes.

## Related Docs

- `tests/README.md`
- `docs/source/testing.md`
