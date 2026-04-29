"""Encryption helpers for Slack workspace and tenant secrets."""

from __future__ import annotations

import os
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

NIMBUS_SLACK_SECRET_KEY = "NIMBUS_SLACK_SECRET_KEY"  # noqa: S105


class SecretCodecError(RuntimeError):
    """Raised when a Slack secret cannot be encrypted or decrypted."""


@dataclass(frozen=True, slots=True)
class SecretCodec:
    """Small authenticated-encryption boundary for persisted Slack secrets."""

    _fernet: Fernet

    @classmethod
    def from_key(cls, key: str) -> SecretCodec:
        """Build a codec from a Fernet key string."""
        normalized = key.strip()
        if not normalized:
            msg = "Nimbus Slack encryption key must not be empty."
            raise SecretCodecError(msg)
        try:
            return cls(Fernet(normalized.encode("utf-8")))
        except ValueError as exc:
            msg = (
                "Nimbus Slack encryption key must be a urlsafe base64-encoded "
                "32-byte Fernet key."
            )
            raise SecretCodecError(msg) from exc

    @classmethod
    def from_env(cls) -> SecretCodec:
        """Build a codec from ``NIMBUS_SLACK_SECRET_KEY``."""
        key = os.environ.get(NIMBUS_SLACK_SECRET_KEY, "")
        if not key.strip():
            msg = f"{NIMBUS_SLACK_SECRET_KEY} is not set."
            raise SecretCodecError(msg)
        return cls.from_key(key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a non-empty secret and return a text-safe ciphertext."""
        if not plaintext:
            msg = "Cannot encrypt an empty secret."
            raise SecretCodecError(msg)
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext previously produced by :meth:`encrypt`."""
        if not ciphertext:
            msg = "Cannot decrypt an empty ciphertext."
            raise SecretCodecError(msg)
        try:
            return self._fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            msg = "Stored Slack secret could not be decrypted."
            raise SecretCodecError(msg) from exc
