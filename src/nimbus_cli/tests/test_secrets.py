"""Tests for Nimbus CLI secret storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from keyring.errors import KeyringError
from nimbus_cli.secrets import (
    NimbusSecrets,
    _try_keyring_delete,
    _try_keyring_get,
    _try_keyring_set,
)

pytestmark = pytest.mark.unit


def test_secret_store_uses_file_fallback_when_keyring_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Headless environments still get durable local onboarding secrets."""
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    store = NimbusSecrets(tmp_path)

    store.set(profile="local", kind="openrouter_api_key", value="sk-test")

    assert store.get(profile="local", kind="openrouter_api_key") == "sk-test"
    assert (tmp_path / "secrets.json").stat().st_mode & 0o777 == 0o600


def test_has_returns_false_when_secret_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    store = NimbusSecrets(tmp_path)
    assert store.has(profile="local", kind="missing") is False


def test_has_returns_true_when_secret_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    store = NimbusSecrets(tmp_path)
    store.set(profile="local", kind="key", value="v")
    assert store.has(profile="local", kind="key") is True


def test_delete_removes_secret_from_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    store = NimbusSecrets(tmp_path)
    store.set(profile="local", kind="key", value="v")
    store.delete(profile="local", kind="key")
    assert store.get(profile="local", kind="key") is None


def test_load_file_raises_on_non_dict_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NIMBUS_DISABLE_KEYRING", "1")
    store = NimbusSecrets(tmp_path)
    (tmp_path / "secrets.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    with pytest.raises(TypeError, match="JSON object"):
        store.get(profile="local", kind="key")


def test_try_keyring_set_returns_false_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keyring
    monkeypatch.setattr(keyring, "set_password", lambda *a: (_ for _ in ()).throw(KeyringError()))
    assert _try_keyring_set("k", "v") is False


def test_try_keyring_get_returns_none_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keyring
    monkeypatch.setattr(keyring, "get_password", lambda *a: (_ for _ in ()).throw(KeyringError()))
    assert _try_keyring_get("k") is None


def test_try_keyring_delete_ignores_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import keyring
    monkeypatch.setattr(keyring, "delete_password", lambda *a: (_ for _ in ()).throw(KeyringError()))
    _try_keyring_delete("k")


def test_set_uses_keyring_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When keyring succeeds, the secret is not written to the fallback file."""
    monkeypatch.delenv("NIMBUS_DISABLE_KEYRING", raising=False)
    import keyring
    stored: dict[str, str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda svc, key, val: stored.__setitem__(key, val))
    monkeypatch.setattr(keyring, "get_password", lambda svc, key: stored.get(key))
    store = NimbusSecrets(tmp_path)
    store.set(profile="p", kind="k", value="secret")
    assert not (tmp_path / "secrets.json").exists()
    assert store.get(profile="p", kind="k") == "secret"
