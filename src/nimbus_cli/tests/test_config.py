"""Tests for Nimbus CLI profile configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from nimbus_cli.config import (
    DEFAULT_MODEL,
    ConfigStore,
    NimbusConfig,
    NimbusProfile,
    SessionRecord,
    _literal_value,
    _optional_str,
    _required_str,
    default_session_dir,
)

pytestmark = pytest.mark.unit


def test_config_round_trips_profiles_and_sessions(tmp_path: Path) -> None:
    """Profiles and external/internal session identities persist together."""
    store = ConfigStore(tmp_path)
    config = NimbusConfig().with_profile(
        NimbusProfile(name="local", mode="local", model=DEFAULT_MODEL)
    )
    config, session = config.resolve_session(
        profile_name="local",
        external_id="work-session",
        resume_last=False,
    )
    store.save(config)

    loaded = store.load()

    assert loaded.profile("local").model == DEFAULT_MODEL
    assert loaded.sessions["work-session"].internal_id == session.internal_id
    assert loaded.last_session_by_profile["local"] == "work-session"


def test_resume_last_requires_existing_session() -> None:
    """The CLI should not guess when a profile has no prior session."""
    with pytest.raises(KeyError, match="no previous session"):
        NimbusConfig().resolve_session(
            profile_name="local",
            external_id=None,
            resume_last=True,
        )


def test_remote_profile_requires_auth_and_base_url() -> None:
    """Remote profiles must name the server and auth scheme."""
    with pytest.raises(ValueError, match="remote_base_url"):
        NimbusProfile(name="bad", mode="remote", remote_auth="hmac").validate()


def test_session_record_from_json_rejects_non_dict() -> None:
    with pytest.raises(TypeError, match="session record must be an object"):
        SessionRecord.from_json("not-a-dict")


def test_profile_from_json_rejects_non_dict() -> None:
    with pytest.raises(TypeError, match="profile must be an object"):
        NimbusProfile.from_json(["not", "a", "dict"])


def test_remote_profile_requires_remote_auth() -> None:
    with pytest.raises(ValueError, match="remote_auth"):
        NimbusProfile(
            name="r", mode="remote", remote_base_url="https://example.com"
        ).validate()


def test_profile_lookup_raises_on_unknown_name() -> None:
    config = NimbusConfig().with_profile(NimbusProfile(name="local", mode="local"))
    with pytest.raises(KeyError, match="not configured"):
        config.profile("nonexistent")


def test_resume_last_raises_when_session_missing_from_store() -> None:
    config = NimbusConfig(
        active_profile="local",
        profiles={},
        sessions={},
        last_session_by_profile={"local": "ghost-session"},
    )
    with pytest.raises(KeyError, match="missing"):
        config.resolve_session(
            profile_name="local", external_id=None, resume_last=True
        )


def test_resolve_session_reuses_existing_external_id() -> None:
    config = NimbusConfig().with_profile(NimbusProfile(name="local", mode="local"))
    config, session1 = config.resolve_session(
        profile_name="local", external_id="s1", resume_last=False
    )
    config2, session2 = config.resolve_session(
        profile_name="local", external_id="s1", resume_last=False
    )
    assert session1.internal_id == session2.internal_id


def test_nimbus_config_from_json_rejects_non_dict() -> None:
    with pytest.raises(TypeError, match="must be an object"):
        NimbusConfig.from_json("bad")


def test_nimbus_config_from_json_rejects_wrong_schema_version() -> None:
    with pytest.raises(ValueError, match="schema version"):
        NimbusConfig.from_json({"schema_version": 99})


def test_nimbus_config_from_json_rejects_non_dict_profiles() -> None:
    with pytest.raises(TypeError, match="profiles and sessions"):
        NimbusConfig.from_json({"schema_version": 1, "profiles": "bad"})


def test_nimbus_config_from_json_rejects_non_dict_last_session() -> None:
    with pytest.raises(TypeError, match="last_session_by_profile"):
        NimbusConfig.from_json(
            {"schema_version": 1, "profiles": {}, "sessions": {}, "last_session_by_profile": "bad"}
        )


def test_default_session_dir_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NIMBUS_SESSION_DIR", str(tmp_path))
    assert default_session_dir() == tmp_path


def test_required_str_raises_on_missing_key() -> None:
    with pytest.raises(TypeError, match="non-empty string"):
        _required_str({}, "missing_key")


def test_optional_str_raises_on_non_string_value() -> None:
    with pytest.raises(TypeError, match="must be a string"):
        _optional_str({"key": 42}, "key")


def test_literal_value_raises_on_invalid_choice() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        _literal_value("bad", "mode", ("local", "remote"))
