"""密码哈希与 JWT 凭证工具（PBKDF2-HMAC-SHA256 + 手写 HS256）。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

PBKDF2_ROUNDS = 210_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2${PBKDF2_ROUNDS}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = encoded.split("$", 3)
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.urlsafe_b64decode(salt), int(rounds)
        )
        return hmac.compare_digest(base64.urlsafe_b64encode(digest).decode(), expected)
    except (ValueError, TypeError):
        return False


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_token(user: dict[str, Any], secret: str, token_type: str = "access", ttl_seconds: int = 3600) -> str:
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(
        json.dumps(
            {"sub": str(user["id"]), "tenant_id": user["tenant_id"], "type": token_type, "exp": int(time.time()) + ttl_seconds},
            separators=(",", ":"),
        ).encode()
    )
    signature = _b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
    return f"{header}.{payload}.{signature}"


def decode_token(token: str, secret: str) -> dict[str, Any] | None:
    try:
        header, payload, signature = token.split(".", 2)
        expected = _b64(hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
