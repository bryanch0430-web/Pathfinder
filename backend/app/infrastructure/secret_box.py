"""Encrypts users' own LLM API keys before they are stored."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class UnreadableSecret(Exception):
    """The stored token cannot be decrypted with the current key."""


class SecretBox:
    def __init__(self, settings: Settings) -> None:
        if settings.byok_encryption_key:
            key = settings.byok_encryption_key.encode()
        else:
            # Development and test only: production refuses to start without a key.
            digest = hashlib.sha256(b"pathfinder-byok:" + settings.auth_secret.encode()).digest()
            key = base64.urlsafe_b64encode(digest)
        try:
            self._fernet = Fernet(key)
        except (ValueError, TypeError) as exc:
            raise ValueError("BYOK_ENCRYPTION_KEY is not a valid Fernet key") from exc

    def encrypt(self, plain: str) -> bytes:
        return self._fernet.encrypt(plain.encode())

    def decrypt(self, token: bytes) -> str:
        try:
            return self._fernet.decrypt(token).decode()
        except InvalidToken:
            raise UnreadableSecret("stored key cannot be decrypted") from None


def mask_key(key: str) -> str:
    """First min(3, len//4) characters, an ellipsis, then the last 4."""
    return f"{key[: min(3, len(key) // 4)]}…{key[-4:]}"
