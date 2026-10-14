"""The signed-in user's profile and account (SPEC §5.8, API.md "Accounts")."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response

from radar.api.deps import UserDep
from radar.auth import accounts, passwords, throttle
from radar.auth import sessions as auth_sessions
from radar.config import get_settings
from radar.db import checkpoint_wal, session_scope
from radar.errors import ApiError, validation_error
from radar.models import User
from radar.schemas import AccountDeleteRequest, Me, MeUpdate, PasswordChangeRequest, RevokedSessions

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/me", tags=["me"])


@router.get("", response_model=Me)
def get_me(user: UserDep) -> Me:
    """``200 Me``; 401 ``unauthenticated``."""
    with session_scope() as session:
        return accounts.me_out(session, user.id)


@router.patch("", response_model=Me)
def update_me(body: MeUpdate, user: UserDep) -> Me:
    """``200 Me`` (display name and preferences)."""
    with session_scope(write=True) as session:
        row = session.get(User, user.id)
        if row is None:
            raise ApiError(401, "unauthenticated", "Sign in to continue")
        if body.display_name is not None:
            row.display_name = body.display_name
        if body.preferences is not None:
            patch = body.preferences.model_dump(exclude_unset=True)
            if patch:
                row.preferences_json = {**dict(row.preferences_json or {}), **patch}
    with session_scope() as session:
        return accounts.me_out(session, user.id)


@router.post("/password", response_model=Me)
def change_password(body: PasswordChangeRequest, user: UserDep) -> Me:
    """``200 Me`` (other sessions end); 401 ``invalid_credentials`` for a wrong current password;
    429 ``rate_limited`` after 5 wrong current passwords in 15 minutes."""
    if get_settings().auth != "accounts":
        raise ApiError(409, "conflict", "Passwords are not used in local mode")
    with session_scope() as session:
        row = session.get(User, user.id)
        if row is None:
            raise ApiError(401, "unauthenticated", "Sign in to continue")
        email, stored_hash = row.email, row.password_hash
    if stored_hash is not None:
        throttle.check_password_change(user.id)
        if not passwords.verify_password(stored_hash, body.current_password or ""):
            throttle.record_password_failure(user.id)
            raise ApiError(401, "invalid_credentials", "The current password is incorrect")
        throttle.record_password_success(user.id)
    if (problem := passwords.password_problem(body.new_password, email)) is not None:
        raise validation_error([{"path": "new_password", "message": problem}], problem)
    new_hash = passwords.hash_password(body.new_password)
    with session_scope(write=True) as session:
        row = session.get(User, user.id)
        if row is None or row.password_hash != stored_hash:
            raise ApiError(409, "conflict", "Your password changed meanwhile; try again")
        row.password_hash = new_hash
        auth_sessions.revoke_user_sessions(session, user.id, except_session_id=user.session_id)
    log.info("user %s changed their password", user.id)
    with session_scope() as session:
        return accounts.me_out(session, user.id)


@router.delete("/identities/{provider}", response_model=Me)
def disconnect_identity(provider: str, user: UserDep) -> Me:
    """``200 Me``; 409 ``conflict`` when it is the last sign-in method."""
    with session_scope(write=True) as session:
        row = session.get(User, user.id)
        if row is None:
            raise ApiError(401, "unauthenticated", "Sign in to continue")
        accounts.unlink_identity(session, row, provider)
    with session_scope() as session:
        return accounts.me_out(session, user.id)


@router.post("/sessions/revoke-others", response_model=RevokedSessions)
def revoke_other_sessions(user: UserDep) -> RevokedSessions:
    """``200 { revoked }``."""
    with session_scope(write=True) as session:
        revoked = auth_sessions.revoke_user_sessions(session, user.id, except_session_id=user.session_id)
    return RevokedSessions(revoked=revoked)


@router.delete("", status_code=204, response_class=Response)
def delete_me(body: AccountDeleteRequest, user: UserDep) -> Response:
    """``204``: every project, document, rating and session of the account is deleted."""
    settings = get_settings()
    if settings.auth != "accounts":
        raise ApiError(409, "conflict", "The local user cannot be deleted; delete projects instead")
    with session_scope(write=True) as session:
        row = session.get(User, user.id)
        if row is None:
            raise ApiError(401, "unauthenticated", "Sign in to continue")
        if not row.email or body.confirm_email != row.email:
            raise validation_error(
                [{"path": "confirm_email", "message": "Type your email address to confirm"}],
                "Type your email address to confirm",
            )
        accounts.delete_account(session, row)
    checkpoint_wal()
    response = Response(status_code=204)
    auth_sessions.clear_cookie(response, settings)
    return response
