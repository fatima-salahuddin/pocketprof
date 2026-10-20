"""Security primitives (SDS 10, Table 9): Argon2id passwords, signed JWTs, refresh-token hashing,
student pseudonyms, and encryption of third-party credentials at rest."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings

_hasher = PasswordHasher()  # Argon2id with the library's recommended parameters
ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: int, role: str) -> tuple[str, int]:
    s = get_settings()
    ttl = s.access_token_minutes * 60
    payload = {"sub": str(user_id), "role": role, "type": "access",
               "iat": now_utc(), "exp": now_utc() + timedelta(seconds=ttl)}
    return jwt.encode(payload, s.jwt_secret, algorithm=ALGORITHM), ttl


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError on a bad, expired or wrong-type token."""
    payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("not an access token")
    return payload


def new_refresh_token() -> tuple[str, str]:
    """Returns (token for the client, SHA-256 digest to store). The raw token is never stored."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def student_ref(user_id: int, course_id: int) -> str:
    """Pseudonym for analytics tables (SDS 6.3): HMAC-SHA-256(key, user_id || course_id), 16 hex chars.
    Different per course, so a student cannot be linked across courses, and not reversible without the key."""
    msg = f"{user_id}:{course_id}".encode()
    return hmac.new(get_settings().pseudonym_key.encode(), msg, hashlib.sha256).hexdigest()[:16]


def _fernet() -> Fernet:
    key = hashlib.sha256(("credentials:" + get_settings().jwt_secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise ValueError("stored credential cannot be decrypted (was JWT_SECRET changed?)") from e
