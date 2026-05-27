"""Encryption helpers for Slack workspace and tenant secrets."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

NIMBUS_SLACK_SECRET_KEY = "NIMBUS_SLACK_SECRET_KEY"  # noqa: S105
NIMBUS_SLACK_KMS_KEY_ID = "NIMBUS_SLACK_KMS_KEY_ID"
NIMBUS_SLACK_DEK_VERSION = "NIMBUS_SLACK_DEK_VERSION"

_ENVELOPE_VERSION_KMS = 2
_ENVELOPE_VERSION_FERNET = 1


class _KmsClient(Protocol):
    """Structural type for the subset of boto3 KMS client used here."""

    def generate_data_key(self, **kwargs: object) -> dict[str, object]: ...

    def decrypt(self, **kwargs: object) -> dict[str, object]: ...


class SecretCodecError(RuntimeError):
    """Raised when a Slack secret cannot be encrypted or decrypted."""


@dataclass(frozen=True, slots=True)
class SecretCodec:
    """Small authenticated-encryption boundary for persisted Slack secrets."""

    _fernet: Fernet | None
    _key_id: str
    _kms_key_id: str | None = None
    _kms_client: _KmsClient | None = None
    _dek_version: str = "1"

    @classmethod
    def from_key(cls, key: str) -> SecretCodec:
        """Build a codec from a Fernet key string."""
        normalized = key.strip()
        if not normalized:
            msg = "Nimbus Slack encryption key must not be empty."
            raise SecretCodecError(msg)
        try:
            key_id = os.environ.get("NIMBUS_SLACK_SECRET_KEY_ID", "").strip()
            if not key_id:
                key_id = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
            return cls(Fernet(normalized.encode("utf-8")), key_id)
        except ValueError as exc:
            msg = (
                "Nimbus Slack encryption key must be a urlsafe base64-encoded "
                "32-byte Fernet key."
            )
            raise SecretCodecError(msg) from exc

    @classmethod
    def from_kms_client(
        cls,
        *,
        kms_client: _KmsClient,
        kms_key_id: str,
        legacy_fernet_key: str | None = None,
        dek_version: str = "1",
    ) -> SecretCodec:
        """Build a KMS-backed envelope codec with optional legacy Fernet reads."""
        normalized_key_id = kms_key_id.strip()
        if not normalized_key_id:
            msg = "Nimbus Slack KMS key id must not be empty."
            raise SecretCodecError(msg)
        legacy_fernet = None
        if legacy_fernet_key and legacy_fernet_key.strip():
            try:
                legacy_fernet = Fernet(legacy_fernet_key.strip().encode("utf-8"))
            except ValueError as exc:
                msg = "legacy_fernet_key must be a valid Fernet key."
                raise SecretCodecError(msg) from exc
        key_id = os.environ.get("NIMBUS_SLACK_SECRET_KEY_ID", "").strip()
        if not key_id:
            key_id = hashlib.sha256(normalized_key_id.encode("utf-8")).hexdigest()[:16]
        return cls(
            legacy_fernet,
            key_id,
            _kms_key_id=normalized_key_id,
            _kms_client=kms_client,
            _dek_version=dek_version.strip() or "1",
        )

    @classmethod
    def from_env(cls) -> SecretCodec:
        """Build a codec from KMS env when configured, else Fernet env."""
        kms_key_id = os.environ.get(NIMBUS_SLACK_KMS_KEY_ID, "").strip()
        if kms_key_id:
            import boto3  # noqa: PLC0415

            return cls.from_kms_client(
                kms_client=boto3.client("kms"),
                kms_key_id=kms_key_id,
                legacy_fernet_key=os.environ.get(NIMBUS_SLACK_SECRET_KEY),
                dek_version=os.environ.get(NIMBUS_SLACK_DEK_VERSION, "1"),
            )
        key = os.environ.get(NIMBUS_SLACK_SECRET_KEY, "")
        if not key.strip():
            msg = f"{NIMBUS_SLACK_SECRET_KEY} is not set."
            raise SecretCodecError(msg)
        return cls.from_key(key)

    def encrypt(
        self,
        plaintext: str,
        *,
        tenant_id: str = "",
        field_name: str = "",
        record_id: str = "",
        purpose: str = "slack_secret",
    ) -> str:
        """Encrypt a non-empty secret and return a text-safe ciphertext."""
        if not plaintext:
            msg = "Cannot encrypt an empty secret."
            raise SecretCodecError(msg)
        aad = _aad(
            tenant_id=tenant_id,
            field_name=field_name,
            record_id=record_id,
            purpose=purpose,
        )
        if self._kms_client is not None and self._kms_key_id is not None:
            return self._encrypt_kms(plaintext, aad=aad, tenant_id=tenant_id)
        if self._fernet is None:
            msg = "No legacy Fernet key is configured for encryption."
            raise SecretCodecError(msg)
        inner = json.dumps(
            {"plaintext": plaintext, "aad": aad},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        envelope = {
            "version": 1,
            "algorithm": "fernet-envelope-v1",
            "key_id": self._key_id,
            "tenant_id": tenant_id,
            "aad": aad,
            "ciphertext": self._fernet.encrypt(inner).decode("utf-8"),
        }
        return json.dumps(envelope, sort_keys=True, separators=(",", ":"))

    def decrypt(
        self,
        ciphertext: str,
        *,
        tenant_id: str = "",
        field_name: str = "",
        record_id: str = "",
        purpose: str = "slack_secret",
    ) -> str:
        """Decrypt a ciphertext previously produced by :meth:`encrypt`."""
        if not ciphertext:
            msg = "Cannot decrypt an empty ciphertext."
            raise SecretCodecError(msg)
        expected_aad = _aad(
            tenant_id=tenant_id,
            field_name=field_name,
            record_id=record_id,
            purpose=purpose,
        )
        try:
            parsed = json.loads(ciphertext)
        except json.JSONDecodeError:
            parsed = None
        try:
            version = parsed.get("version") if isinstance(parsed, dict) else None
            if isinstance(parsed, dict) and version == _ENVELOPE_VERSION_KMS:
                return self._decrypt_kms_envelope(parsed, expected_aad=expected_aad)
            if isinstance(parsed, dict) and version == _ENVELOPE_VERSION_FERNET:
                return self._decrypt_envelope(parsed, expected_aad=expected_aad)
            if self._fernet is None:
                msg = "Legacy Fernet key is unavailable for this Slack secret."
                raise SecretCodecError(msg)
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except (InvalidToken, KeyError, TypeError, ValueError) as exc:
            msg = "Stored Slack secret could not be decrypted."
            raise SecretCodecError(msg) from exc

    def _encrypt_kms(
        self,
        plaintext: str,
        *,
        aad: dict[str, str],
        tenant_id: str,
    ) -> str:
        if self._kms_client is None or self._kms_key_id is None:
            msg = "KMS codec is not configured."
            raise SecretCodecError(msg)
        context = _kms_context(aad)
        response = self._kms_client.generate_data_key(
            KeyId=self._kms_key_id,
            KeySpec="AES_256",
            EncryptionContext=context,
        )
        dek = response.get("Plaintext")
        encrypted_dek = response.get("CiphertextBlob")
        if not isinstance(dek, bytes) or not isinstance(encrypted_dek, bytes):
            msg = "KMS GenerateDataKey response was malformed."
            raise SecretCodecError(msg)
        fernet = Fernet(base64.urlsafe_b64encode(dek))
        inner = json.dumps(
            {"plaintext": plaintext, "aad": aad},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        envelope = {
            "version": 2,
            "algorithm": "aws-kms-fernet-dek-v1",
            "key_id": str(response.get("KeyId") or self._kms_key_id),
            "configured_key_id": self._kms_key_id,
            "dek_version": self._dek_version,
            "tenant_id": tenant_id,
            "aad": aad,
            "encrypted_dek": base64.b64encode(encrypted_dek).decode("ascii"),
            "ciphertext": fernet.encrypt(inner).decode("utf-8"),
        }
        return json.dumps(envelope, sort_keys=True, separators=(",", ":"))

    def _decrypt_kms_envelope(
        self,
        envelope: dict[str, object],
        *,
        expected_aad: dict[str, str],
    ) -> str:
        if self._kms_client is None:
            msg = "KMS client is unavailable for this Slack secret."
            raise SecretCodecError(msg)
        if envelope.get("algorithm") != "aws-kms-fernet-dek-v1":
            msg = "Unsupported Slack KMS secret envelope algorithm."
            raise SecretCodecError(msg)
        if envelope.get("aad") != expected_aad:
            msg = "Stored Slack secret AAD does not match this record."
            raise SecretCodecError(msg)
        encrypted_dek = envelope.get("encrypted_dek")
        ciphertext = envelope.get("ciphertext")
        if not isinstance(encrypted_dek, str) or not isinstance(ciphertext, str):
            msg = "Stored Slack KMS envelope is malformed."
            raise SecretCodecError(msg)
        response = self._kms_client.decrypt(
            CiphertextBlob=base64.b64decode(encrypted_dek.encode("ascii")),
            EncryptionContext=_kms_context(expected_aad),
        )
        dek = response.get("Plaintext")
        if not isinstance(dek, bytes):
            msg = "KMS Decrypt response was malformed."
            raise SecretCodecError(msg)
        fernet = Fernet(base64.urlsafe_b64encode(dek))
        inner_raw = fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        inner = json.loads(inner_raw)
        if not isinstance(inner, dict) or inner.get("aad") != expected_aad:
            msg = "Stored Slack secret AAD does not match encrypted payload."
            raise SecretCodecError(msg)
        plaintext = inner.get("plaintext")
        if not isinstance(plaintext, str) or not plaintext:
            msg = "Stored Slack secret payload is malformed."
            raise SecretCodecError(msg)
        return plaintext

    def _decrypt_envelope(
        self,
        envelope: dict[str, object],
        *,
        expected_aad: dict[str, str],
    ) -> str:
        if envelope.get("algorithm") != "fernet-envelope-v1":
            msg = "Unsupported Slack secret envelope algorithm."
            raise SecretCodecError(msg)
        if envelope.get("aad") != expected_aad:
            msg = "Stored Slack secret AAD does not match this record."
            raise SecretCodecError(msg)
        ciphertext = envelope["ciphertext"]
        if not isinstance(ciphertext, str):
            msg = "Stored Slack secret envelope is malformed."
            raise SecretCodecError(msg)
        if self._fernet is None:
            msg = "Legacy Fernet key is unavailable for this Slack secret."
            raise SecretCodecError(msg)
        inner_raw = self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        inner = json.loads(inner_raw)
        if not isinstance(inner, dict) or inner.get("aad") != expected_aad:
            msg = "Stored Slack secret AAD does not match encrypted payload."
            raise SecretCodecError(msg)
        plaintext = inner.get("plaintext")
        if not isinstance(plaintext, str) or not plaintext:
            msg = "Stored Slack secret payload is malformed."
            raise SecretCodecError(msg)
        return plaintext


def _aad(
    *,
    tenant_id: str,
    field_name: str,
    record_id: str,
    purpose: str,
) -> dict[str, str]:
    return {
        "tenant_id": tenant_id,
        "field_name": field_name,
        "record_id": record_id,
        "purpose": purpose,
    }


def _kms_context(aad: dict[str, str]) -> dict[str, str]:
    """Return the KMS encryption context for a tenant-bound secret field."""
    return {
        "tenant_id": aad["tenant_id"],
        "field_name": aad["field_name"],
        "record_id": aad["record_id"],
        "purpose": aad["purpose"],
    }
