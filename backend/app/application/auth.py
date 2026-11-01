"""Small, dependency-free password hashing and HS256 bearer tokens.

This is intentionally standard-library only: the course deployment should not
gain a JWT/passlib dependency just to authenticate a local demo. The token is a
real JWT (header.payload.signature) signed with HMAC-SHA256.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_b64, digest_b64 = encoded.split("$", 2)
        if algorithm != "scrypt":
            return False
        salt = _unb64(salt_b64)
        expected = _unb64(digest_b64)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_token(user_id: str, email: str, secret: str, hours: int = 168) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": user_id,
                "email": email,
                "iat": int(time.time()),
                "exp": int(time.time()) + hours * 3600,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _b64(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_token(token: str, secret: str) -> dict | None:
    try:
        header, payload, signature = token.split(".")
        signing_input = f"{header}.{payload}".encode("ascii")
        expected = _b64(hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except Exception:
        return None
