"""Unit tests for session persistence (load / save / validate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ai_server.sessions import (
    _validate_session_id,
    list_sessions,
    load_session,
    save_session,
    session_exists,
)

from ai_client_api import Conversation

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Session ID validation
# ---------------------------------------------------------------------------


class TestValidateSessionId:
    # --- valid inputs ---

    def test_accepts_slack_channel_id(self) -> None:
        _validate_session_id("C08ABCDEF12")

    def test_accepts_slack_thread_timestamp(self) -> None:
        # Slack thread timestamps look like "1234567890.123456"
        _validate_session_id("1234567890.123456")

    def test_accepts_slack_user_id(self) -> None:
        _validate_session_id("U01XYZ789")

    def test_accepts_simple_alphanumeric(self) -> None:
        _validate_session_id("my-session-01")

    def test_accepts_all_allowed_special_chars(self) -> None:
        # Hyphens, underscores, dots, colons are all permitted
        _validate_session_id("chan_A-B.C:D")

    def test_accepts_single_char(self) -> None:
        _validate_session_id("x")

    def test_accepts_exactly_128_chars(self) -> None:
        _validate_session_id("a" * 128)

    def test_accepts_safe_session_id_longer_than_128_chars(self) -> None:
        _validate_session_id("a" * 256)

    # --- invalid inputs ---

    def test_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            _validate_session_id("../../etc/passwd")

    def test_rejects_forward_slash(self) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            _validate_session_id("foo/bar")

    def test_rejects_backslash(self) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            _validate_session_id("foo\\bar")

    def test_rejects_null_byte(self) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            _validate_session_id("foo\x00bar")

    def test_rejects_space(self) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            _validate_session_id("my session")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            _validate_session_id("")


# ---------------------------------------------------------------------------
# load_session
# ---------------------------------------------------------------------------


class TestLoadSession:
    def test_returns_fresh_conversation_when_no_file(self, tmp_path: Path) -> None:
        conv = load_session(tmp_path, "new-session")
        assert conv.session_id == "new-session"
        # Only the system message present — no user/assistant turns yet
        assert len(conv) == 1

    def test_fresh_session_uses_provided_system_prompt(self, tmp_path: Path) -> None:
        conv = load_session(tmp_path, "s1", system_prompt="custom prompt")
        assert conv.system == "custom prompt"

    def test_fresh_session_falls_back_to_default_system_prompt(
        self, tmp_path: Path
    ) -> None:
        from openrouter_ai_client_impl.config import (
            DEFAULT_SYSTEM_PROMPT,
        )

        conv = load_session(tmp_path, "s1")
        assert conv.system == DEFAULT_SYSTEM_PROMPT

    def test_loads_persisted_conversation(self, tmp_path: Path) -> None:
        conv = Conversation(system="test prompt", session_id="chan-123")
        conv.add_user("hi there")
        save_session(tmp_path, "chan-123", conv)

        loaded = load_session(tmp_path, "chan-123")
        contents = [m.content for m in loaded.messages()]
        assert "hi there" in contents

    def test_loaded_session_id_matches(self, tmp_path: Path) -> None:
        conv = Conversation(system="sys", session_id="my-chan")
        save_session(tmp_path, "my-chan", conv)
        loaded = load_session(tmp_path, "my-chan")
        assert loaded.session_id == "my-chan"

    def test_returns_fresh_on_corrupted_json(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad-session.json"
        bad.write_text("not-valid-json{{{{", encoding="utf-8")
        conv = load_session(tmp_path, "bad-session")
        assert len(conv) == 1  # only system message

    def test_returns_fresh_on_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty.json").write_text("", encoding="utf-8")
        conv = load_session(tmp_path, "empty")
        assert len(conv) == 1

    def test_returns_fresh_on_valid_json_wrong_shape(self, tmp_path: Path) -> None:
        wrong = tmp_path / "wrong.json"
        wrong.write_text('{"not": "a conversation"}', encoding="utf-8")
        conv = load_session(tmp_path, "wrong")
        assert len(conv) == 1

    def test_raises_for_unsafe_session_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="unsafe"):
            load_session(tmp_path, "../escape")


# ---------------------------------------------------------------------------
# save_session
# ---------------------------------------------------------------------------


class TestSaveSession:
    def test_creates_json_file(self, tmp_path: Path) -> None:
        conv = Conversation(system="sys", session_id="s1")
        save_session(tmp_path, "s1", conv)
        assert (tmp_path / "s1.json").is_file()

    def test_no_leftover_tmp_file(self, tmp_path: Path) -> None:
        """Atomic rename — the .tmp file must be gone after a successful save."""
        conv = Conversation(system="sys", session_id="s1")
        save_session(tmp_path, "s1", conv)
        assert not (tmp_path / "s1.tmp").exists()

    def test_round_trips_user_and_assistant_turns(self, tmp_path: Path) -> None:
        conv = Conversation(system="sys", session_id="s1")
        conv.add_user("question")
        conv.add_assistant("answer")
        save_session(tmp_path, "s1", conv)

        loaded = load_session(tmp_path, "s1")
        contents = [m.content for m in loaded.messages()]
        assert "question" in contents
        assert "answer" in contents

    def test_second_save_overwrites_first(self, tmp_path: Path) -> None:
        conv = Conversation(system="sys", session_id="s1")
        conv.add_user("turn one")
        save_session(tmp_path, "s1", conv)

        conv.add_user("turn two")
        save_session(tmp_path, "s1", conv)

        loaded = load_session(tmp_path, "s1")
        contents = [m.content for m in loaded.messages()]
        assert "turn one" in contents
        assert "turn two" in contents

    def test_creates_nested_parent_dirs(self, tmp_path: Path) -> None:
        deep_dir = tmp_path / "a" / "b" / "c"
        conv = Conversation(system="sys", session_id="s1")
        save_session(deep_dir, "s1", conv)
        assert (deep_dir / "s1.json").is_file()

    def test_saved_file_is_valid_json(self, tmp_path: Path) -> None:
        conv = Conversation(system="sys", session_id="s1")
        save_session(tmp_path, "s1", conv)
        data = json.loads((tmp_path / "s1.json").read_text())
        assert isinstance(data, dict)

    def test_saved_json_contains_session_id(self, tmp_path: Path) -> None:
        conv = Conversation(system="sys", session_id="chan-42")
        save_session(tmp_path, "chan-42", conv)
        data = json.loads((tmp_path / "chan-42.json").read_text())
        assert data["session_id"] == "chan-42"

    def test_raises_for_unsafe_session_id(self, tmp_path: Path) -> None:
        conv = Conversation(system="sys", session_id="x")
        with pytest.raises(ValueError, match="unsafe"):
            save_session(tmp_path, "../escape", conv)

    def test_session_id_with_max_length(self, tmp_path: Path) -> None:
        long_id = "a" * 128
        conv = Conversation(system="sys", session_id=long_id)
        save_session(tmp_path, long_id, conv)
        assert (tmp_path / f"{long_id}.json").is_file()

    def test_long_session_id_is_persisted_under_a_hashed_filename(
        self, tmp_path: Path
    ) -> None:
        long_id = "a" * 211
        conv = Conversation(system="sys", session_id=long_id)

        save_session(tmp_path, long_id, conv)

        json_files = list(tmp_path.glob("*.json"))
        assert len(json_files) == 1
        assert json_files[0].name != f"{long_id}.json"
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert data["session_id"] == long_id

    def test_long_session_id_round_trips_through_load_session(
        self, tmp_path: Path
    ) -> None:
        long_id = "slack:" + "w" * 64 + ":" + "c" * 64 + ":" + "t" * 64
        conv = Conversation(system="sys", session_id=long_id)
        conv.add_user("hello")

        save_session(tmp_path, long_id, conv)
        loaded = load_session(tmp_path, long_id)

        assert loaded.session_id == long_id
        assert "hello" in [message.content for message in loaded.messages()]

    def test_session_exists_supports_long_session_ids(self, tmp_path: Path) -> None:
        long_id = "x" * 211
        conv = Conversation(system="sys", session_id=long_id)

        save_session(tmp_path, long_id, conv)

        assert session_exists(tmp_path, long_id) is True

    def test_list_sessions_returns_logical_long_session_ids(
        self, tmp_path: Path
    ) -> None:
        short_id = "short-session"
        long_id = "y" * 211

        save_session(
            tmp_path, short_id, Conversation(system="sys", session_id=short_id)
        )
        save_session(tmp_path, long_id, Conversation(system="sys", session_id=long_id))

        assert list_sessions(tmp_path) == sorted([short_id, long_id])


# ---------------------------------------------------------------------------
# Crash-recovery: stale .tmp files from interrupted saves
# ---------------------------------------------------------------------------


class TestCrashRecovery:
    def test_stale_tmp_file_is_invisible_to_load_session(self, tmp_path: Path) -> None:
        """A .tmp left by a process crash is not picked up by load_session.

        load_session only ever opens the committed .json file.  A lingering
        .tmp from a previous failed save must never be returned as session
        data, even if its contents are valid JSON.
        """
        # Write a valid-looking conversation to the .tmp path so the test
        # would catch any accidental fallback to the .tmp file.
        conv = Conversation(system="sys", session_id="ghost")
        conv.add_user("this should not be loaded")
        stale_tmp = tmp_path / "ghost.tmp"
        stale_tmp.write_text(json.dumps(conv.to_json(), indent=2), encoding="utf-8")

        loaded = load_session(tmp_path, "ghost")

        # Only the system message — the .tmp content must be ignored.
        assert len(loaded) == 1

    def test_save_succeeds_and_cleans_up_when_stale_tmp_exists(
        self, tmp_path: Path
    ) -> None:
        """save_session overwrites a lingering .tmp and leaves no .tmp behind.

        If a previous save crashed between write and rename, the stale .tmp
        sits on disk.  The next save must overwrite it, atomically rename it
        to .json, and leave the directory with exactly one .json and zero .tmp
        files.
        """
        stale_tmp = tmp_path / "recover.tmp"
        stale_tmp.write_text("stale garbage from a prior crash", encoding="utf-8")

        conv = Conversation(system="sys", session_id="recover")
        conv.add_user("hello after crash")
        save_session(tmp_path, "recover", conv)

        loaded = load_session(tmp_path, "recover")
        contents = [m.content for m in loaded.messages()]
        assert "hello after crash" in contents
        assert not stale_tmp.exists()  # .tmp was consumed by the atomic rename
