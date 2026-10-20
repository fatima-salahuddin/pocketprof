"""Account operations: login, refresh-token rotation, sign-up and user creation."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import Problem, conflict
from ..core.security import (
    create_access_token,
    hash_password,
    hash_token,
    needs_rehash,
    new_refresh_token,
    now_utc,
    verify_password,
)
from ..db.models import RefreshToken, User

# Verified against when the email is unknown, so a login takes the same time either way.
_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


@dataclass
class TokenPair:
    access_token: str
    expires_in: int
    refresh_token: str
    user: User


def _invalid() -> Problem:
    return Problem(401, "Unauthorized", "Email or password is incorrect.", "/problems/bad-credentials")


def normalise_email(email: str) -> str:
    return email.strip().lower()


def create_user(db: Session, *, email: str, name: str, role: str, password: str | None) -> User:
    email = normalise_email(email)
    if db.scalar(select(User).where(User.email == email)):
        raise conflict(f"An account with {email} already exists.")
    user = User(email=email, name=name.strip(), role=role,
                password_hash=hash_password(password) if password else None)
    db.add(user)
    db.flush()
    return user


def _issue(db: Session, user: User, family: str | None = None) -> TokenPair:
    raw, digest = new_refresh_token()
    db.add(RefreshToken(user_id=user.id, token_hash=digest, family=family or secrets.token_hex(16),
                        expires_at=now_utc() + timedelta(days=get_settings().refresh_token_days)))
    access, ttl = create_access_token(user.id, user.role)
    return TokenPair(access, ttl, raw, user)


def login(db: Session, email: str, password: str) -> TokenPair:
    user = db.scalar(select(User).where(User.email == normalise_email(email)))
    if user is None or not user.password_hash:
        verify_password(password, _DUMMY_HASH)
        raise _invalid()
    if not verify_password(password, user.password_hash) or not user.is_active:
        raise _invalid()
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = now_utc()
    return _issue(db, user)


def refresh(db: Session, raw_token: str) -> TokenPair:
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    expired = row is not None and _aware(row.expires_at) < now_utc()
    if row is None or expired:
        raise Problem(401, "Unauthorized", "Please sign in again.", "/problems/session-ended")
    if row.revoked_at is not None:
        # A rotated token was presented again: someone holds a copy. End every session in the family.
        db.execute(update(RefreshToken).where(RefreshToken.family == row.family, RefreshToken.revoked_at.is_(None))
                   .values(revoked_at=now_utc()))
        db.commit()
        raise Problem(401, "Unauthorized", "Please sign in again.", "/problems/session-ended")
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise Problem(401, "Unauthorized", "This account is not active.", "/problems/session-ended")
    row.revoked_at = now_utc()
    return _issue(db, user, family=row.family)


def logout(db: Session, raw_token: str) -> None:
    row = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw_token)))
    if row is not None:
        db.execute(update(RefreshToken).where(RefreshToken.family == row.family, RefreshToken.revoked_at.is_(None))
                   .values(revoked_at=now_utc()))


def _aware(dt):
    """SQLite returns naive datetimes; treat them as UTC."""
    from datetime import timezone
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
