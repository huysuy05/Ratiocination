"""Sign-in sessions in HttpOnly cookies (SPEC §5.2). Owner: auth.

* The cookie holds a random token (``secrets.token_urlsafe(32)``); the database stores only its
  SHA-256 hex, so a leaked database cannot be replayed as cookies.
* Cookie: ``radar_session`` (``__Host-radar_session`` when Secure), ``HttpOnly; SameSite=Lax;
  Path=/``, ``Max-Age`` = ``RADAR_SESSION_DAYS`` days, ``Secure`` when ``RADAR_PUBLIC_URL`` is https.
* :func:`resolve` maps a cookie to a :class:`SessionInfo` (one read query); while email verification
  is required (``settings.email_verification_required``), a session of an account whose email is
  not verified does not count (sessions created before the rule applied). :func:`touch` records
  ``last_seen_at`` at most every 5 minutes through the background writer and slides ``expires_at``
  once a day; when it slides, the caller re-issues the cookie.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar import db
from radar.config import Settings, get_settings
from radar.models import AuthSession, AuthToken, OAuthState, User, utcnow

COOKIE_NAME = "radar_session"
SECURE_COOKIE_NAME = "__Host-radar_session"
TOUCH_INTERVAL = timedelta(minutes=5)
SLIDE_AFTER = timedelta(days=1)
OAUTH_STATE_TTL = timedelta(minutes=10)


@dataclass(frozen=True)
class SessionInfo:
    session_id: int
    user_id: int
    is_admin: bool
    expires_at: datetime
    last_seen_at: datetime


def cookie_name(settings: Settings | None = None) -> str:
    return SECURE_COOKIE_NAME if (settings or get_settings()).cookie_secure else COOKIE_NAME


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_lifetime(settings: Settings | None = None) -> timedelta:
    return timedelta(days=(settings or get_settings()).session_days)


def create_session(session: Session, user_id: int, *, now: datetime | None = None) -> str:
    """Insert a new session for ``user_id`` (write session) and return the raw cookie token."""
    now = now or utcnow()
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            user_id=user_id,
            token_hash=token_hash(token),
            created_at=now,
            last_seen_at=now,
            expires_at=now + session_lifetime(),
        )
    )
    session.flush()
    return token


def resolve(token: str | None, *, now: datetime | None = None) -> SessionInfo | None:
    """The live session for a cookie token, or ``None`` (missing, unknown, expired, or an unverified
    account's session while email verification is required)."""
    if not token or len(token) > 200:
        return None
    now = now or utcnow()
    with db.session_scope() as session:
        row = session.execute(
            select(
                AuthSession.id,
                AuthSession.user_id,
                User.is_admin,
                AuthSession.expires_at,
                AuthSession.last_seen_at,
                User.email_verified,
            )
            .join(User, User.id == AuthSession.user_id)
            .where(AuthSession.token_hash == token_hash(token), AuthSession.expires_at > now)
        ).first()
    if row is None:
        return None
    if not row[5] and get_settings().email_verification_required:
        return None
    return SessionInfo(
        session_id=row[0], user_id=row[1], is_admin=bool(row[2]), expires_at=row[3], last_seen_at=row[4]
    )


def touch(info: SessionInfo, *, now: datetime | None = None) -> bool:
    """Record activity (at most every 5 minutes) and slide the expiry (at most daily).

    Returns True when the expiry slid, i.e. the cookie should be re-issued with a fresh Max-Age.
    """
    now = now or utcnow()
    lifetime = session_lifetime()
    slide = info.expires_at < now + lifetime - SLIDE_AFTER
    if not slide and now - info.last_seen_at < TOUCH_INTERVAL:
        return False
    new_expiry = now + lifetime if slide else None

    def apply(session: Session) -> None:
        row = session.get(AuthSession, info.session_id)
        if row is None:
            return
        row.last_seen_at = now
        if new_expiry is not None and new_expiry > row.expires_at:
            row.expires_at = new_expiry

    db.enqueue_write(apply)
    return slide


def revoke(session: Session, token: str | None) -> int:
    """Delete the session of ``token`` (write session); returns the number of rows deleted."""
    if not token:
        return 0
    result = session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
    return int(result.rowcount or 0)


def revoke_user_sessions(session: Session, user_id: int, *, except_session_id: int | None = None) -> int:
    """Delete every session of ``user_id`` except ``except_session_id`` (write session)."""
    stmt = delete(AuthSession).where(AuthSession.user_id == user_id)
    if except_session_id is not None:
        stmt = stmt.where(AuthSession.id != except_session_id)
    return int(session.execute(stmt).rowcount or 0)


def purge_expired(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Delete expired sessions, used/expired one-time tokens and stale OAuth states (write session)."""
    now = now or utcnow()
    sessions = session.execute(delete(AuthSession).where(AuthSession.expires_at <= now)).rowcount or 0
    tokens = (
        session.execute(
            delete(AuthToken).where((AuthToken.expires_at <= now) | (AuthToken.used_at.is_not(None)))
        ).rowcount
        or 0
    )
    states = (
        session.execute(delete(OAuthState).where(OAuthState.created_at <= now - OAUTH_STATE_TTL)).rowcount
        or 0
    )
    return {"sessions": int(sessions), "tokens": int(tokens), "oauth_states": int(states)}


def set_cookie(response: Response, token: str, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    response.set_cookie(
        cookie_name(s),
        token,
        max_age=int(session_lifetime(s).total_seconds()),
        path="/",
        secure=s.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def clear_cookie(response: Response, settings: Settings | None = None) -> None:
    s = settings or get_settings()
    response.delete_cookie(cookie_name(s), path="/", secure=s.cookie_secure, httponly=True, samesite="lax")


__all__ = [
    "COOKIE_NAME",
    "SECURE_COOKIE_NAME",
    "SessionInfo",
    "clear_cookie",
    "cookie_name",
    "create_session",
    "purge_expired",
    "resolve",
    "revoke",
    "revoke_user_sessions",
    "set_cookie",
    "token_hash",
    "touch",
]
