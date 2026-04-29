"""Tests for nimbus_slack.crypto."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from nimbus_slack.crypto import SecretCodec, SecretCodecError

pytestmark = pytest.mark.unit


def _valid_key() -> str:
    return Fernet.generate_key().decode("utf-8")


class TestSecretCodecFromKey:
    def test_valid_key_returns_codec(self) -> None:
        codec = SecretCodec.from_key(_valid_key())
        assert isinstance(codec, SecretCodec)

    def test_empty_key_raises(self) -> None:
        with pytest.raises(SecretCodecError, match="must not be empty"):
            SecretCodec.from_key("   ")

    def test_invalid_base64_key_raises(self) -> None:
        with pytest.raises(SecretCodecError, match="urlsafe base64"):
            SecretCodec.from_key("not-a-valid-fernet-key")


class TestSecretCodecFromEnv:
    def test_missing_env_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NIMBUS_SLACK_SECRET_KEY", raising=False)
        with pytest.raises(SecretCodecError, match="is not set"):
            SecretCodec.from_env()

    def test_valid_env_returns_codec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_SLACK_SECRET_KEY", _valid_key())
        codec = SecretCodec.from_env()
        assert isinstance(codec, SecretCodec)


class TestSecretCodecEncryptDecrypt:
    def test_round_trip(self) -> None:
        codec = SecretCodec.from_key(_valid_key())
        assert codec.decrypt(codec.encrypt("hello")) == "hello"

    def test_encrypt_empty_raises(self) -> None:
        codec = SecretCodec.from_key(_valid_key())
        with pytest.raises(SecretCodecError, match="empty secret"):
            codec.encrypt("")

    def test_decrypt_empty_raises(self) -> None:
        codec = SecretCodec.from_key(_valid_key())
        with pytest.raises(SecretCodecError, match="empty ciphertext"):
            codec.decrypt("")

    def test_decrypt_bad_ciphertext_raises(self) -> None:
        codec = SecretCodec.from_key(_valid_key())
        with pytest.raises(SecretCodecError, match="could not be decrypted"):
            codec.decrypt("notvalidciphertext")
