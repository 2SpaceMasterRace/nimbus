"""Secret storage for Nimbus CLI onboarding."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from nimbus_cli.config import default_nimbus_home

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_SERVICE_NAME = "nimbus-cli"
_SECRET_FILE_MODE = 0o600


class NimbusSecrets:
    """Store profile secrets in keyring, with a local file fallback."""

    def __init__(self, home: Path | None = None) -> None:
        """Create a secret store rooted at ``home`` or ``NIMBUS_HOME``."""
        self.home = home or default_nimbus_home()
        self._fallback_path = self.home / "secrets.json"

    def set(self, *, profile: str, kind: str, value: str) -> None:
        """Store one profile secret."""
        key = _secret_key(profile=profile, kind=kind)
        if not _keyring_disabled() and _try_keyring_set(key, value):
            self._delete_from_file(key)
            return
        secrets = self._load_file()
        secrets[key] = value
        self._save_file(secrets)

    def get(self, *, profile: str, kind: str) -> str | None:
        """Return a profile secret if it exists."""
        key = _secret_key(profile=profile, kind=kind)
        if not _keyring_disabled():
            value = _try_keyring_get(key)
            if value:
                return value
        return self._load_file().get(key)

    def delete(self, *, profile: str, kind: str) -> None:
        """Delete a profile secret from both backends."""
        key = _secret_key(profile=profile, kind=kind)
        if not _keyring_disabled():
            _try_keyring_delete(key)
        self._delete_from_file(key)

    def has(self, *, profile: str, kind: str) -> bool:
        """Return whether a profile secret exists."""
        return bool(self.get(profile=profile, kind=kind))

    def _load_file(self) -> dict[str, str]:
        if not self._fallback_path.exists():
            return {}
        data = json.loads(self._fallback_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            msg = "Nimbus secret file must contain a JSON object"
            raise TypeError(msg)
        return {
            str(key): value for key, value in data.items() if isinstance(value, str)
        }

    def _save_file(self, secrets: Mapping[str, str]) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        tmp = self._fallback_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(dict(secrets), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.chmod(_SECRET_FILE_MODE)
        tmp.replace(self._fallback_path)
        self._fallback_path.chmod(_SECRET_FILE_MODE)

    def _delete_from_file(self, key: str) -> None:
        secrets = self._load_file()
        if key not in secrets:
            return
        del secrets[key]
        self._save_file(secrets)


def _secret_key(*, profile: str, kind: str) -> str:
    """Return the stable keyring username for one profile secret."""
    return f"profile:{profile}:{kind}"


def _keyring_disabled() -> bool:
    return os.environ.get("NIMBUS_DISABLE_KEYRING", "").lower() in {
        "1",
        "true",
        "yes",
    }


def _try_keyring_set(key: str, value: str) -> bool:
    try:
        keyring.set_password(_SERVICE_NAME, key, value)
    except (KeyringError, RuntimeError):
        return False
    return True


def _try_keyring_get(key: str) -> str | None:
    try:
        return keyring.get_password(_SERVICE_NAME, key)
    except (KeyringError, RuntimeError):
        return None


def _try_keyring_delete(key: str) -> None:
    try:
        keyring.delete_password(_SERVICE_NAME, key)
    except (KeyringError, PasswordDeleteError, RuntimeError):
        return
