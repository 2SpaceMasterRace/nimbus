"""Tests for nimbus_slack.crypto."""

from __future__ import annotations

import hashlib
import json

import pytest
from cryptography.fernet import Fernet
from nimbus_slack.crypto import SecretCodec, SecretCodecError

pytestmark = pytest.mark.unit


def _valid_key() -> str:
    return Fernet.generate_key().decode("utf-8")


class _FakeKmsClient:
    """Small KMS double that enforces encryption-context equality."""

    def __init__(self) -> None:
        self._wrapped: dict[bytes, tuple[bytes, dict[str, str]]] = {}
        self.generate_calls: list[dict[str, object]] = []
        self.decrypt_calls: list[dict[str, object]] = []

    def generate_data_key(
        self,
        *,
        KeyId: str,
        KeySpec: str,
        EncryptionContext: dict[str, str],
    ) -> dict[str, object]:
        assert KeySpec == "AES_256"
        self.generate_calls.append(
            {
                "KeyId": KeyId,
                "EncryptionContext": dict(EncryptionContext),
            }
        )
        seed = json.dumps(
            {
                "key_id": KeyId,
                "context": EncryptionContext,
                "n": len(self.generate_calls),
            },
            sort_keys=True,
        ).encode("utf-8")
        plaintext = hashlib.sha256(seed).digest()
        encrypted = b"wrapped:" + hashlib.sha256(plaintext).digest()
        self._wrapped[encrypted] = (plaintext, dict(EncryptionContext))
        return {
            "KeyId": KeyId,
            "Plaintext": plaintext,
            "CiphertextBlob": encrypted,
        }

    def decrypt(
        self,
        *,
        CiphertextBlob: bytes,
        EncryptionContext: dict[str, str],
    ) -> dict[str, object]:
        self.decrypt_calls.append(
            {
                "CiphertextBlob": CiphertextBlob,
                "EncryptionContext": dict(EncryptionContext),
            }
        )
        plaintext, expected_context = self._wrapped[CiphertextBlob]
        if expected_context != EncryptionContext:
            msg = "encryption context mismatch"
            raise ValueError(msg)
        return {"Plaintext": plaintext}


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
        monkeypatch.delenv("NIMBUS_SLACK_KMS_KEY_ID", raising=False)
        with pytest.raises(SecretCodecError, match="is not set"):
            SecretCodec.from_env()

    def test_valid_env_returns_codec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NIMBUS_SLACK_KMS_KEY_ID", raising=False)
        monkeypatch.setenv("NIMBUS_SLACK_SECRET_KEY", _valid_key())
        codec = SecretCodec.from_env()
        assert isinstance(codec, SecretCodec)


class TestSecretCodecEncryptDecrypt:
    def test_round_trip(self) -> None:
        codec = SecretCodec.from_key(_valid_key())
        assert codec.decrypt(codec.encrypt("hello")) == "hello"

    def test_envelope_binds_expected_aad(self) -> None:
        codec = SecretCodec.from_key(_valid_key())
        ciphertext = codec.encrypt(
            "secret",
            tenant_id="T123",
            field_name="openrouter_api_key",
            record_id="T123",
            purpose="tenant_config",
        )

        assert (
            codec.decrypt(
                ciphertext,
                tenant_id="T123",
                field_name="openrouter_api_key",
                record_id="T123",
                purpose="tenant_config",
            )
            == "secret"
        )
        with pytest.raises(SecretCodecError, match="AAD"):
            codec.decrypt(
                ciphertext,
                tenant_id="T999",
                field_name="openrouter_api_key",
                record_id="T999",
                purpose="tenant_config",
            )

    def test_legacy_fernet_ciphertext_still_decrypts(self) -> None:
        key = _valid_key()
        legacy = Fernet(key.encode("utf-8")).encrypt(b"old-secret").decode("utf-8")

        assert SecretCodec.from_key(key).decrypt(legacy) == "old-secret"

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

    def test_kms_envelope_wraps_dek_and_binds_tenant_context(self) -> None:
        kms = _FakeKmsClient()
        codec = SecretCodec.from_kms_client(
            kms_client=kms,
            kms_key_id="arn:aws:kms:us-east-1:123:key/slack",
            dek_version="7",
        )

        ciphertext = codec.encrypt(
            "kms-secret",
            tenant_id="T123",
            field_name="openrouter_api_key",
            record_id="T123",
            purpose="tenant_config",
        )
        envelope = json.loads(ciphertext)

        assert envelope["version"] == 2
        assert envelope["algorithm"] == "aws-kms-fernet-dek-v1"
        assert envelope["dek_version"] == "7"
        assert envelope["encrypted_dek"]
        assert "kms-secret" not in ciphertext
        assert (
            codec.decrypt(
                ciphertext,
                tenant_id="T123",
                field_name="openrouter_api_key",
                record_id="T123",
                purpose="tenant_config",
            )
            == "kms-secret"
        )
        assert kms.generate_calls[0]["EncryptionContext"] == {
            "tenant_id": "T123",
            "field_name": "openrouter_api_key",
            "record_id": "T123",
            "purpose": "tenant_config",
        }
        with pytest.raises(SecretCodecError, match="AAD"):
            codec.decrypt(
                ciphertext,
                tenant_id="T999",
                field_name="openrouter_api_key",
                record_id="T999",
                purpose="tenant_config",
            )

    def test_kms_codec_can_read_legacy_fernet_during_migration(self) -> None:
        legacy_key = _valid_key()
        legacy = (
            Fernet(legacy_key.encode("utf-8")).encrypt(b"old-secret").decode("utf-8")
        )
        codec = SecretCodec.from_kms_client(
            kms_client=_FakeKmsClient(),
            kms_key_id="alias/nimbus-slack",
            legacy_fernet_key=legacy_key,
        )

        assert codec.decrypt(legacy) == "old-secret"
