"""Property-based tests for session-ID validation and file-stem derivation.

Three contracts are checked:

1. Validation oracle — any string whose characters are all in the safe
   alphabet is accepted; anything else (empty or containing unsafe chars)
   is rejected with ValueError("unsafe …").

2. File-stem determinism — for any valid ID the stem function must be pure
   (same input → same output every time) and must route correctly between
   the two code paths: short IDs ≤ 128 chars are returned verbatim; long
   IDs produce a ``sha256-<hex>`` stem.

3. Round-trip persistence — save_session followed by load_session must
   preserve the logical session_id for any valid identifier, regardless of
   whether the on-disk filename is a direct stem or a hash.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest
from ai_server.sessions import (
    _session_file_stem,
    _validate_session_id,
    load_session,
    save_session,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from ai_client_api import Conversation

pytestmark = pytest.mark.property

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

_SAFE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-.:"

_valid_id = st.text(alphabet=_SAFE_ALPHABET, min_size=1, max_size=300)

# Generate strings that contain at least one character outside the safe set.
_invalid_id = st.one_of(
    st.just(""),  # empty string is always invalid
    # Valid prefix with a single injected bad character somewhere inside.
    st.builds(
        lambda prefix, bad, suffix: prefix + bad + suffix,
        prefix=st.text(alphabet=_SAFE_ALPHABET, min_size=0, max_size=20),
        bad=st.sampled_from("/\\ \x00!@#$%^&*()[]{}|=+,<>?\"'`~;"),
        suffix=st.text(alphabet=_SAFE_ALPHABET, min_size=0, max_size=20),
    ),
)

_short_valid_id = _valid_id.filter(lambda s: len(s) <= 128)
_long_valid_id = _valid_id.filter(lambda s: len(s) > 128)

# ---------------------------------------------------------------------------
# 1. Validation oracle
# ---------------------------------------------------------------------------


@given(_valid_id)
def test_safe_id_never_raises(session_id: str) -> None:
    """Any character-safe non-empty ID must pass validation without raising."""
    _validate_session_id(session_id)  # must not raise


@given(_invalid_id)
def test_unsafe_id_always_raises(session_id: str) -> None:
    """Any ID that is empty or contains an unsafe character must raise ValueError."""
    with pytest.raises(ValueError, match="unsafe"):
        _validate_session_id(session_id)


@given(
    st.text(
        alphabet=st.characters(
            blacklist_characters=_SAFE_ALPHABET,
            blacklist_categories=("Cs",),
        ),
        min_size=1,
    )
)
def test_string_of_only_unsafe_chars_raises(session_id: str) -> None:
    """A non-empty string composed entirely of unsafe characters is always rejected."""
    with pytest.raises(ValueError, match="unsafe"):
        _validate_session_id(session_id)


# ---------------------------------------------------------------------------
# 2. File-stem derivation
# ---------------------------------------------------------------------------


@given(_valid_id)
def test_stem_is_pure(session_id: str) -> None:
    """_session_file_stem must be deterministic: same input → same output."""
    assert _session_file_stem(session_id) == _session_file_stem(session_id)


@given(_short_valid_id)
def test_short_id_is_returned_as_its_own_stem(session_id: str) -> None:
    """IDs ≤ 128 characters must be returned verbatim as the filename stem."""
    assert _session_file_stem(session_id) == session_id


@given(_long_valid_id)
def test_long_id_produces_sha256_stem(session_id: str) -> None:
    """IDs > 128 characters must produce a sha256-<64-char hex> stem."""
    stem = _session_file_stem(session_id)
    assert stem.startswith("sha256-")
    hex_part = stem[len("sha256-") :]
    assert len(hex_part) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", hex_part)


@given(_long_valid_id, _long_valid_id)
def test_distinct_long_ids_produce_distinct_stems(id_a: str, id_b: str) -> None:
    """Two different long IDs must not collide on a SHA-256 stem.

    SHA-256 collision resistance holds at any input size Hypothesis can explore.
    """
    if id_a == id_b:
        return  # same input → same stem; not a collision
    assert _session_file_stem(id_a) != _session_file_stem(id_b)


# ---------------------------------------------------------------------------
# 3. Round-trip persistence
# ---------------------------------------------------------------------------


@given(_short_valid_id, st.text(min_size=1, max_size=200))
@settings(max_examples=50)
def test_short_id_round_trips_through_save_load(
    session_id: str, system_prompt: str
) -> None:
    """save_session → load_session must recover the original session_id.

    Short IDs (≤ 128 chars) are stored verbatim as the filename stem.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        conv = Conversation(system=system_prompt, session_id=session_id)
        save_session(path, session_id, conv)
        loaded = load_session(path, session_id)
        assert loaded.session_id == session_id


@given(_long_valid_id, st.text(min_size=1, max_size=200))
@settings(max_examples=50)
def test_long_id_round_trips_through_save_load(
    session_id: str, system_prompt: str
) -> None:
    """save_session → load_session must recover the original logical session_id.

    Long IDs (> 128 chars) are stored under a sha256 hash stem.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        conv = Conversation(system=system_prompt, session_id=session_id)
        save_session(path, session_id, conv)
        loaded = load_session(path, session_id)
        assert loaded.session_id == session_id


@given(
    _valid_id,
    st.text(min_size=1, max_size=200),
    _text := st.text(min_size=1, max_size=200),
)
@settings(max_examples=50)
def test_user_turn_survives_round_trip(
    session_id: str, system_prompt: str, user_text: str
) -> None:
    """A user turn added before saving must appear in the loaded conversation."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)
        conv = Conversation(system=system_prompt, session_id=session_id)
        conv.add_user(user_text)
        save_session(path, session_id, conv)
        loaded = load_session(path, session_id)
        contents = [m.content for m in loaded.messages()]
        assert user_text in contents
