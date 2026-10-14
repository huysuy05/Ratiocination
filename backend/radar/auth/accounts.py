"""Users, account resolution and account deletion (SPEC §5.6, §5.8). Owner: auth.

Every function here runs inside the caller's session (``write`` where it changes rows) and never
does network calls or password hashing (callers do those outside transactions, SPEC §2.1).

Account resolution for an OAuth sign-in (:func:`resolve_oauth`):

1. a known identity ``(provider, subject)`` signs into its user;
2. else a user with the same (provider-verified) email is linked - when that user has a password but
   never verified the email, the password is removed and its sessions deleted (pre-hijacking
   defense: whoever set it never proved they own the address);
3. else a new user is created (allow-list applies).

The allow-list (``RADAR_ALLOWED_EMAILS``) is checked on every OAuth sign-in, not only when an account
is created.

Admin status and user 1 (local-mode data) need a verified email (SPEC §5.6) - an address whose owner
proved it through a provider, a verification link or a reset link - except on a loopback-only server
(``settings.is_public`` false and verification not forced), where a password sign-up keeps them as
before. User 1 is claimed by the first such account whose email is in ``RADAR_ADMIN_EMAILS``, or, when
that list is empty, by the first such account while no admin exists. An account created unverified
on a public server claims user 1 when it verifies (:func:`confirm_email` moves it into row 1).
Admin is re-evaluated at every sign-in (:func:`mark_signed_in`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, object_session

from radar.auth import sessions as auth_sessions
from radar.auth import tokens as auth_tokens
from radar.config import get_settings
from radar.errors import ApiError
from radar.models import (
    LOCAL_USER_ID,
    AuthSession,
    AuthToken,
    Document,
    FindingsSummary,
    GraphEdge,
    Interaction,
    Job,
    PaperRead,
    Project,
    RecommendationSlate,
    User,
    UserIdentity,
    utcnow,
)
from radar.schemas import Identity, Me, UserPreferences
from radar.schemas import User as UserOut

log = logging.getLogger(__name__)

PROVIDERS = ("google", "github")


class AccountError(Exception):
    """An OAuth sign-in that cannot complete; ``code`` goes to ``/signin?error=<code>``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class OAuthProfile:
    """What a provider told us about the person signing in (email already verified by it)."""

    provider: str
    subject: str
    email: str
    name: str | None
    avatar_url: str | None


# ------------------------------------------------------------------------------------------------
# Serialization
# ------------------------------------------------------------------------------------------------


def _preferences(user: User) -> UserPreferences:
    prefs = dict(user.preferences_json or {})
    timezone = prefs.get("timezone")
    return UserPreferences(
        auto_expand=prefs.get("auto_expand", True) is not False,
        timezone=timezone if isinstance(timezone, str) and timezone else None,
    )


def user_out(session: Session, user: User) -> UserOut:
    identities = session.scalars(
        select(UserIdentity).where(UserIdentity.user_id == user.id).order_by(UserIdentity.id)
    )
    settings = get_settings()
    return UserOut(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified,
        display_name=user.display_name or (user.email.split("@", 1)[0] if user.email else "You"),
        avatar_url=user.avatar_url,
        is_admin=user.is_admin or settings.auth == "local",
        has_password=user.password_hash is not None,
        identities=[
            Identity(provider=i.provider, email=i.email, created_at=i.created_at)
            for i in identities
            if i.provider in PROVIDERS
        ],
        preferences=_preferences(user),
        created_at=user.created_at,
    )


def me_out(session: Session, user_id: int) -> Me:
    user = session.get(User, user_id)
    if user is None:
        raise ApiError(401, "unauthenticated", "Sign in to continue")
    return Me(mode=get_settings().auth, user=user_out(session, user))


# ------------------------------------------------------------------------------------------------
# Creating users
# ------------------------------------------------------------------------------------------------


def find_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))


def _claimable_user1(session: Session) -> User | None:
    user = session.get(User, LOCAL_USER_ID)
    if user is None or user.email is not None or user.password_hash is not None:
        return None
    has_identity = session.scalar(select(UserIdentity.id).where(UserIdentity.user_id == user.id).limit(1))
    return None if has_identity is not None else user


def _is_listed_admin(email: str | None) -> bool:
    return bool(email) and email in get_settings().admin_emails


def privileges_need_verified_email() -> bool:
    """True unless the server is loopback-only with verification not forced (SPEC §5.6)."""
    settings = get_settings()
    return settings.is_public or settings.email_verification_required


def _trusted(email_verified: bool) -> bool:
    """May this account's email confer admin status or user 1?"""
    return email_verified or not privileges_need_verified_email()


def _no_admin_yet(session: Session) -> bool:
    return session.scalar(select(User.id).where(User.is_admin.is_(True)).limit(1)) is None


def _may_claim_user1(session: Session, email: str | None) -> bool:
    """Listed in RADAR_ADMIN_EMAILS, or (empty list) the first account while there is no admin."""
    if get_settings().admin_emails:
        return _is_listed_admin(email)
    return _no_admin_yet(session)


def _owns_private_rows(session: Session, user_id: int) -> bool:
    columns = (
        Project.owner_id,
        Document.owner_id,
        Job.owner_id,
        GraphEdge.owner_id,
        Interaction.owner_id,
        RecommendationSlate.owner_id,
        PaperRead.user_id,
        FindingsSummary.user_id,
    )
    return any(
        session.scalar(select(column).where(column == user_id).limit(1)) is not None for column in columns
    )


def _move_into_user1(session: Session, user: User) -> User:
    """Let a newly verified ``user`` claim user 1 when it may (write session); returns the row to use.

    The account's fields, identities, tokens and sessions move to row 1 and its own row is deleted.
    An account that already owns projects or other private rows is never moved (logged instead).
    """
    if user.id == LOCAL_USER_ID:
        return user
    target = _claimable_user1(session)
    if target is None or not _may_claim_user1(session, user.email):
        return user
    if _owns_private_rows(session, user.id):
        log.warning("user %s may claim user 1 but already owns data; not moved", user.id)
        return user
    old_id = user.id
    values = {
        "email": user.email,
        "email_verified": user.email_verified,
        "display_name": user.display_name,
        "avatar_url": user.avatar_url,
        "password_hash": user.password_hash,
        "last_login_at": user.last_login_at,
        "preferences_json": dict(user.preferences_json or {"auto_expand": True}),
    }
    for model in (UserIdentity, AuthToken, AuthSession):
        session.execute(update(model).where(model.user_id == old_id).values(user_id=LOCAL_USER_ID))
    session.execute(delete(User).where(User.id == old_id))  # frees the email for row 1
    for key, value in values.items():
        setattr(target, key, value)
    target.is_admin = True
    session.flush()
    log.info("user %s claimed user 1 after verifying its email", old_id)
    return target


def new_user(
    session: Session,
    *,
    email: str,
    email_verified: bool,
    display_name: str | None,
    avatar_url: str | None,
    password_hash: str | None,
) -> User:
    """Create a user, or claim user 1 (SPEC §5.6). Write session; ``email`` is normalized and allowed.

    An unverified email on a public server gets neither admin nor user 1 here: :func:`confirm_email`
    grants them once the address is proven.
    """
    trusted = _trusted(email_verified)
    listed = trusted and _is_listed_admin(email)
    claim = _claimable_user1(session) if trusted else None
    if claim is not None and _may_claim_user1(session, email):
        user = claim
        user.email = email
        user.email_verified = email_verified
        user.display_name = (display_name or "")[:100]
        user.avatar_url = avatar_url
        user.password_hash = password_hash
        user.is_admin = True
        log.info("user %s claimed by the first account", user.id)
    else:
        user = User(
            email=email,
            email_verified=email_verified,
            display_name=(display_name or "")[:100],
            avatar_url=avatar_url,
            password_hash=password_hash,
            is_admin=listed,
            preferences_json={"auto_expand": True},
        )
        session.add(user)
    session.flush()
    return user


def confirm_email(session: Session, user: User) -> User:
    """Mark ``user``'s email verified - proven by a verification link, a reset link or a provider - and
    apply what a verified email unlocks: user 1 (when it may claim it) and admin when the email is
    listed (SPEC §5.6). Returns the row to sign in: user 1 when the account moved into it. Unused
    verification links of the account stop working (the address is proven now)."""
    user.email_verified = True
    session.execute(
        delete(AuthToken).where(
            AuthToken.user_id == user.id,
            AuthToken.purpose == auth_tokens.VERIFY_EMAIL,
            AuthToken.used_at.is_(None),
        )
    )
    session.flush()
    user = _move_into_user1(session, user)
    if not user.is_admin and _is_listed_admin(user.email):
        user.is_admin = True
    return user


def mark_signed_in(user: User) -> None:
    """Stamp ``last_login_at`` and re-evaluate admin from ``RADAR_ADMIN_EMAILS`` (SPEC §5.6).

    A listed email gains admin (a verified one, except on a loopback-only server). When the list is
    set, an admin whose email is not listed (or not verified) loses admin - unless no other listed
    admin account exists yet, so the server is never left without an admin; that case is logged and
    resolves at the admin's first sign-in after a listed admin has signed in. An empty list keeps the
    first-account rule and revokes nothing.
    """
    user.last_login_at = utcnow()
    settings = get_settings()
    entitled = _is_listed_admin(user.email) and _trusted(user.email_verified)
    if entitled and not user.is_admin:
        user.is_admin = True
        return
    if entitled or not user.is_admin or not settings.admin_emails:
        return
    session = object_session(user)
    other_listed_admin = (
        session.scalar(
            select(User.id)
            .where(User.is_admin.is_(True), User.id != user.id, User.email.in_(settings.admin_emails))
            .limit(1)
        )
        if session is not None
        else None
    )
    if other_listed_admin is None:
        log.warning(
            "user %s keeps admin although its email is not listed: no listed admin exists yet", user.id
        )
        return
    user.is_admin = False
    log.info("user %s is no longer an admin (email not in RADAR_ADMIN_EMAILS)", user.id)


def invite(session: Session, email: str) -> User:
    """The account for ``email``, created without a password when missing (write session).

    For operators without SMTP: issue a reset token for it and hand over the link; choosing a password
    there proves the address (admin and user 1 then follow the usual rules). ``email`` is normalized
    and allowed.
    """
    user = find_by_email(session, email)
    if user is None:
        user = new_user(
            session, email=email, email_verified=False, display_name=None, avatar_url=None, password_hash=None
        )
    return user


# ------------------------------------------------------------------------------------------------
# OAuth
# ------------------------------------------------------------------------------------------------


def resolve_oauth(session: Session, profile: OAuthProfile, *, link_user_id: int | None = None) -> int:
    """The user id to sign in (or link) for ``profile`` (write session); raises :class:`AccountError`."""
    identity = session.scalar(
        select(UserIdentity).where(
            UserIdentity.provider == profile.provider, UserIdentity.subject == profile.subject
        )
    )
    now = utcnow()
    if link_user_id is not None:
        if identity is not None and identity.user_id != link_user_id:
            raise AccountError("identity_in_use")
        owner = session.scalar(select(User).where(User.email == profile.email))
        if identity is None and owner is not None and owner.id != link_user_id:
            raise AccountError("identity_in_use")  # that email belongs to another account
        if identity is None:
            session.add(
                UserIdentity(
                    user_id=link_user_id,
                    provider=profile.provider,
                    subject=profile.subject,
                    email=profile.email,
                    last_login_at=now,
                )
            )
        else:
            identity.last_login_at = now
            identity.email = profile.email
        session.flush()
        return link_user_id

    settings = get_settings()
    if identity is not None:
        user = session.get(User, identity.user_id)
        if user is None:  # pragma: no cover - FK cascade
            raise AccountError("oauth_failed")
        if user.email and not settings.email_allowed(user.email):
            raise AccountError("not_allowed")
        identity.last_login_at = now
        identity.email = profile.email
    else:
        user = find_by_email(session, profile.email)
        if user is not None:
            if not settings.email_allowed(profile.email):
                raise AccountError("not_allowed")
            if not user.email_verified:
                if user.password_hash is not None:
                    user.password_hash = None
                    removed = auth_sessions.revoke_user_sessions(session, user.id)
                    log.warning(
                        "user %s: unverified password removed after a verified %s sign-in (%d sessions ended)",
                        user.id,
                        profile.provider,
                        removed,
                    )
                user = confirm_email(session, user)
        else:
            if not settings.email_allowed(profile.email):
                raise AccountError("not_allowed")
            user = new_user(
                session,
                email=profile.email,
                email_verified=True,
                display_name=profile.name,
                avatar_url=profile.avatar_url,
                password_hash=None,
            )
        session.add(
            UserIdentity(
                user_id=user.id,
                provider=profile.provider,
                subject=profile.subject,
                email=profile.email,
                last_login_at=now,
            )
        )
    if not user.display_name and profile.name:
        user.display_name = profile.name[:100]
    if profile.avatar_url and not user.avatar_url:
        user.avatar_url = profile.avatar_url
    mark_signed_in(user)
    session.flush()
    return user.id


def sign_in_methods(session: Session, user: User) -> int:
    identities = session.scalar(select(func.count()).where(UserIdentity.user_id == user.id)) or 0
    return int(identities) + (1 if user.password_hash is not None else 0)


def unlink_identity(session: Session, user: User, provider: str) -> None:
    """Remove the user's identity for ``provider``; 409 when it is the last sign-in method."""
    identity = session.scalar(
        select(UserIdentity).where(UserIdentity.user_id == user.id, UserIdentity.provider == provider)
    )
    if identity is None:
        raise ApiError(404, "not_found", "That account is not connected")
    if sign_in_methods(session, user) <= 1:
        raise ApiError(
            409, "conflict", "Add a password or connect another account before disconnecting this one"
        )
    session.delete(identity)
    session.flush()


# ------------------------------------------------------------------------------------------------
# Deleting an account
# ------------------------------------------------------------------------------------------------


def delete_account(session: Session, user: User) -> dict[str, int]:
    """Delete every project (with private-data scrubbing) and every row of ``user`` (write session).

    The caller checkpoints the WAL after commit. 409 for the last admin while other users exist.
    """
    from radar.documents import service as documents_service

    if user.is_admin:
        other_admins = session.scalar(select(func.count()).where(User.is_admin.is_(True), User.id != user.id))
        others = session.scalar(select(func.count()).where(User.id != user.id, User.email.is_not(None)))
        if not other_admins and others:
            raise ApiError(
                409, "conflict", "Make another user an admin before deleting the last admin account"
            )
    project_ids = list(session.scalars(select(Project.id).where(Project.owner_id == user.id)))
    for project_id in project_ids:
        documents_service.delete_project_private_data(session, project_id)
    if project_ids:
        session.execute(delete(Project).where(Project.id.in_(project_ids)))
    jobs = session.execute(
        delete(Job).where((Job.owner_id == user.id) | (Job.project_id.in_(project_ids or [-1])))
    ).rowcount
    edges = session.execute(delete(GraphEdge).where(GraphEdge.owner_id == user.id)).rowcount
    if user.id == LOCAL_USER_ID:
        # User 1 must exist (SPEC §4): reset it to an unclaimed local user instead of deleting it.
        session.execute(
            update(User)
            .where(User.id == LOCAL_USER_ID)
            .values(
                email=None,
                email_verified=False,
                display_name="You",
                avatar_url=None,
                password_hash=None,
                is_admin=False,
                last_login_at=None,
                preferences_json={"auto_expand": True},
            )
        )
        auth_sessions.revoke_user_sessions(session, LOCAL_USER_ID)
        session.execute(delete(UserIdentity).where(UserIdentity.user_id == LOCAL_USER_ID))
        from radar.models import AuthToken, FindingsSummary, PaperRead

        for model in (AuthToken, FindingsSummary, PaperRead):
            session.execute(delete(model).where(model.user_id == LOCAL_USER_ID))
    else:
        session.delete(user)  # FK cascades: identities, sessions, tokens, reads, summaries
    session.flush()
    log.info(
        "user %s deleted (%d projects, %d jobs, %d edges)", user.id, len(project_ids), jobs or 0, edges or 0
    )
    return {"projects": len(project_ids), "jobs": int(jobs or 0), "edges": int(edges or 0)}


__all__ = [
    "AccountError",
    "OAuthProfile",
    "confirm_email",
    "delete_account",
    "find_by_email",
    "invite",
    "mark_signed_in",
    "me_out",
    "new_user",
    "privileges_need_verified_email",
    "resolve_oauth",
    "sign_in_methods",
    "unlink_identity",
    "user_out",
]
