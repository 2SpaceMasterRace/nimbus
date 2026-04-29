"""Persistent Nimbus CLI profile and session configuration."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

DEFAULT_PROFILE = "local"
DEFAULT_MODEL = "openai/gpt-oss-120b:free"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_REMOTE_PATH = "/ai/chat/turn"
CONFIG_SCHEMA_VERSION = 1

ProfileMode = Literal["local", "remote"]
RemoteAuthKind = Literal["bearer", "hmac"]


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """External and internal identity for one CLI conversation."""

    internal_id: str
    external_id: str
    created_at: str

    @classmethod
    def create(cls, *, external_id: str | None = None) -> SessionRecord:
        """Create a fresh session record."""
        now = datetime.now(UTC)
        internal_id = str(uuid.uuid4())
        return cls(
            internal_id=internal_id,
            external_id=external_id or f"cli-{now.strftime('%Y%m%d-%H%M%S')}",
            created_at=now.isoformat(),
        )

    def to_json(self) -> dict[str, object]:
        """Encode this record as JSON-compatible data."""
        return {
            "internal_id": self.internal_id,
            "external_id": self.external_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, data: object) -> SessionRecord:
        """Decode a session record from JSON-compatible data."""
        if not isinstance(data, dict):
            msg = "session record must be an object"
            raise TypeError(msg)
        return cls(
            internal_id=_required_str(data, "internal_id"),
            external_id=_required_str(data, "external_id"),
            created_at=_required_str(data, "created_at"),
        )


@dataclass(frozen=True, slots=True)
class NimbusProfile:
    """One local or remote Nimbus CLI profile."""

    name: str
    mode: ProfileMode
    model: str = DEFAULT_MODEL
    fallback_model: str | None = None
    openrouter_base_url: str = DEFAULT_OPENROUTER_BASE_URL
    remote_base_url: str | None = None
    remote_auth: RemoteAuthKind | None = None
    storage_container: str | None = None
    session_dir: str | None = None

    def to_json(self) -> dict[str, object]:
        """Encode this profile as JSON-compatible data."""
        return {
            "name": self.name,
            "mode": self.mode,
            "model": self.model,
            "fallback_model": self.fallback_model,
            "openrouter_base_url": self.openrouter_base_url,
            "remote_base_url": self.remote_base_url,
            "remote_auth": self.remote_auth,
            "storage_container": self.storage_container,
            "session_dir": self.session_dir,
        }

    @classmethod
    def from_json(cls, data: object) -> NimbusProfile:
        """Decode one profile from JSON-compatible data."""
        if not isinstance(data, dict):
            msg = "profile must be an object"
            raise TypeError(msg)
        mode = _required_literal(data, "mode", ("local", "remote"))
        remote_auth_raw = data.get("remote_auth")
        remote_auth = None
        if remote_auth_raw is not None:
            remote_auth = cast(
                "RemoteAuthKind",
                _literal_value(remote_auth_raw, "remote_auth", ("bearer", "hmac")),
            )
        profile = cls(
            name=_required_str(data, "name"),
            mode=mode,
            model=_optional_str(data, "model") or DEFAULT_MODEL,
            fallback_model=_optional_str(data, "fallback_model"),
            openrouter_base_url=(
                _optional_str(data, "openrouter_base_url")
                or DEFAULT_OPENROUTER_BASE_URL
            ),
            remote_base_url=_optional_str(data, "remote_base_url"),
            remote_auth=remote_auth,
            storage_container=_optional_str(data, "storage_container"),
            session_dir=_optional_str(data, "session_dir"),
        )
        profile.validate()
        return profile

    def validate(self) -> None:
        """Validate profile invariants."""
        if not self.name:
            msg = "profile name cannot be empty"
            raise ValueError(msg)
        if self.mode == "remote":
            if not self.remote_base_url:
                msg = "remote profiles require remote_base_url"
                raise ValueError(msg)
            if self.remote_auth is None:
                msg = "remote profiles require remote_auth"
                raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class NimbusConfig:
    """Complete persisted Nimbus CLI configuration."""

    active_profile: str = DEFAULT_PROFILE
    profiles: dict[str, NimbusProfile] = field(default_factory=dict)
    sessions: dict[str, SessionRecord] = field(default_factory=dict)
    last_session_by_profile: dict[str, str] = field(default_factory=dict)

    def with_profile(self, profile: NimbusProfile) -> NimbusConfig:
        """Return a config with ``profile`` inserted and activated."""
        profiles = dict(self.profiles)
        profiles[profile.name] = profile
        return NimbusConfig(
            active_profile=profile.name,
            profiles=profiles,
            sessions=dict(self.sessions),
            last_session_by_profile=dict(self.last_session_by_profile),
        )

    def profile(self, name: str | None = None) -> NimbusProfile:
        """Return the requested profile or the active profile."""
        profile_name = name or self.active_profile
        try:
            return self.profiles[profile_name]
        except KeyError as exc:
            msg = f"Nimbus profile {profile_name!r} is not configured"
            raise KeyError(msg) from exc

    def resolve_session(
        self,
        *,
        profile_name: str,
        external_id: str | None,
        resume_last: bool,
    ) -> tuple[NimbusConfig, SessionRecord]:
        """Resolve or create the session requested by a chat command."""
        if resume_last:
            last_external_id = self.last_session_by_profile.get(profile_name)
            if last_external_id is None:
                msg = f"profile {profile_name!r} has no previous session"
                raise KeyError(msg)
            try:
                return self, self.sessions[last_external_id]
            except KeyError as exc:
                msg = f"last session {last_external_id!r} is missing"
                raise KeyError(msg) from exc

        if external_id is not None and external_id in self.sessions:
            record = self.sessions[external_id]
        else:
            record = SessionRecord.create(external_id=external_id)
        sessions = dict(self.sessions)
        sessions[record.external_id] = record
        last = dict(self.last_session_by_profile)
        last[profile_name] = record.external_id
        return (
            NimbusConfig(
                active_profile=self.active_profile,
                profiles=dict(self.profiles),
                sessions=sessions,
                last_session_by_profile=last,
            ),
            record,
        )

    def to_json(self) -> dict[str, object]:
        """Encode the config as JSON-compatible data."""
        return {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "active_profile": self.active_profile,
            "profiles": {
                name: profile.to_json() for name, profile in self.profiles.items()
            },
            "sessions": {
                external_id: session.to_json()
                for external_id, session in self.sessions.items()
            },
            "last_session_by_profile": dict(self.last_session_by_profile),
        }

    @classmethod
    def from_json(cls, data: object) -> NimbusConfig:
        """Decode the config from JSON-compatible data."""
        if not isinstance(data, dict):
            msg = "Nimbus config must be an object"
            raise TypeError(msg)
        if data.get("schema_version") != CONFIG_SCHEMA_VERSION:
            msg = "unsupported Nimbus config schema version"
            raise ValueError(msg)
        profiles_raw = data.get("profiles", {})
        sessions_raw = data.get("sessions", {})
        if not isinstance(profiles_raw, dict) or not isinstance(sessions_raw, dict):
            msg = "profiles and sessions must be objects"
            raise TypeError(msg)
        last_raw = data.get("last_session_by_profile", {})
        if not isinstance(last_raw, dict):
            msg = "last_session_by_profile must be an object"
            raise TypeError(msg)
        return cls(
            active_profile=_optional_str(data, "active_profile") or DEFAULT_PROFILE,
            profiles={
                str(name): NimbusProfile.from_json(value)
                for name, value in profiles_raw.items()
            },
            sessions={
                str(name): SessionRecord.from_json(value)
                for name, value in sessions_raw.items()
            },
            last_session_by_profile={
                str(name): str(value)
                for name, value in last_raw.items()
                if isinstance(value, str)
            },
        )


class ConfigStore:
    """File-backed Nimbus CLI config store."""

    def __init__(self, home: Path | None = None) -> None:
        """Create a store rooted at ``home`` or ``NIMBUS_HOME``."""
        self.home = home or default_nimbus_home()
        self.path = self.home / "config.json"

    def load(self) -> NimbusConfig:
        """Load the config, returning an empty default if it does not exist."""
        if not self.path.exists():
            return NimbusConfig()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return NimbusConfig.from_json(data)

    def save(self, config: NimbusConfig) -> None:
        """Atomically persist a Nimbus config file."""
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(config.to_json(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)


def default_nimbus_home() -> Path:
    """Return the Nimbus CLI home directory."""
    raw = os.environ.get("NIMBUS_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".nimbus"


def default_session_dir() -> Path:
    """Return the default local CLI session directory."""
    raw = os.environ.get("NIMBUS_SESSION_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return default_nimbus_home() / "sessions" / "cli"


def _required_str(data: dict[object, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        msg = f"{key!r} must be a non-empty string"
        raise TypeError(msg)
    return value


def _optional_str(data: dict[object, object], key: str) -> str | None:
    value = data.get(key)
    if value is None or isinstance(value, str):
        return value
    msg = f"{key!r} must be a string or null"
    raise TypeError(msg)


def _required_literal(
    data: dict[object, object],
    key: str,
    allowed: tuple[str, ...],
) -> ProfileMode:
    return cast("ProfileMode", _literal_value(data.get(key), key, allowed))


def _literal_value(value: object, key: str, allowed: tuple[str, ...]) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    msg = f"{key!r} must be one of {', '.join(allowed)}"
    raise ValueError(msg)
