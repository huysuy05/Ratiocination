"""Single-use tokens for email verification and password reset links (SPEC §5.7). Owner: auth.

Tokens are ``secrets.token_urlsafe(32)``; only their SHA-256 is stored. Issuing a token deletes the
user's earlier unused tokens of the same purpose, so only the newest link works. Verification links
last 48 hours, reset links 30 minutes.
"""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar.auth.sessions import token_hash
from radar.config import get_settings
from radar.models import AuthToken, utcnow

VERIFY_EMAIL = "verify_email"
RESET_PASSWORD = "reset_password"
TTL = {VERIFY_EMAIL: timedelta(hours=48), RESET_PASSWORD: timedelta(minutes=30)}
PAGES = {VERIFY_EMAIL: "/verify", RESET_PASSWORD: "/reset"}


def issue(session: Session, user_id: int, purpose: str, email: str) -> str:
    """Store a new token (write session) and return the raw value for the link."""
    if purpose not in TTL:
        raise ValueError(f"unknown token purpose {purpose!r}")
    session.execute(
        delete(AuthToken).where(
            AuthToken.user_id == user_id, AuthToken.purpose == purpose, AuthToken.used_at.is_(None)
        )
    )
    raw = secrets.token_urlsafe(32)
    now = utcnow()
    session.add(
        AuthToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=token_hash(raw),
            email=email,
            created_at=now,
            expires_at=now + TTL[purpose],
        )
    )
    session.flush()
    return raw


def consume(session: Session, raw: str, purpose: str) -> AuthToken | None:
    """Mark the token used and return it; ``None`` when unknown, used, expired or of another purpose."""
    row = session.scalar(select(AuthToken).where(AuthToken.token_hash == token_hash(raw)))
    now = utcnow()
    if row is None or row.purpose != purpose or row.used_at is not None or row.expires_at <= now:
        return None
    row.used_at = now
    session.flush()
    return row


def link(purpose: str, raw: str) -> str:
    """The page URL carrying the token (``/verify?token=…`` or ``/reset?token=…``)."""
    return f"{get_settings().public_origin}{PAGES[purpose]}?token={raw}"


__all__ = ["RESET_PASSWORD", "VERIFY_EMAIL", "consume", "issue", "link"]
