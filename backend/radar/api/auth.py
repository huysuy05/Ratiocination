"""Sign-up, sign-in, sign-out, OAuth, password reset, email verification (SPEC §5, API.md "Accounts").

Order of work in every handler: validation and throttling -> reads -> slow work outside any
transaction (password hashing, provider calls) -> one short write transaction -> cookies. Messages
never reveal whether an email has an account, except sign-up's documented 409 ``email_taken``;
sign-in's 403 ``email_unverified`` / ``not_allowed`` come only after the password matched.

Email verification (``settings.email_verification_required``: on for a public server): a password
sign-up gets no session; it gets a verification link, and following it signs in. The link signs in
without the password only in the browser that asked for it - the ``radar_verify`` cookie holds a hash
of the link's token - so a stranger who signed up with someone else's address cannot have that
person verify the stranger's password by clicking the email (the verify page then asks for the
password; the owner of the address uses "forgot password" instead).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Body, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from radar.api.deps import current_user
from radar.auth import accounts, mailer, oauth, passwords, throttle, tokens
from radar.auth import sessions as auth_sessions
from radar.config import get_settings
from radar.db import session_scope
from radar.auth.sessions import token_hash
from radar.errors import ApiError, validation_error
from radar.models import AuthToken, User, utcnow
from radar.schemas import (
    AuthConfig,
    AuthProviders,
    ForgotPasswordRequest,
    Me,
    ResendVerificationRequest,
    ResetPasswordRequest,
    SignInRequest,
    SignUpRequest,
    VerificationPending,
    VerifyEmailRequest,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

VERIFY_COOKIE = "radar_verify"
VERIFY_COOKIE_PATH = "/api/auth/email"


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _require_password_login() -> None:
    if not get_settings().password_enabled:
        raise ApiError(404, "not_found", "Email and password sign-in is not enabled")


def _require_accounts() -> None:
    if get_settings().auth != "accounts":
        raise ApiError(404, "not_found", "Accounts are not enabled on this server")


def password_signup_open() -> bool:
    """Email + password sign-up: on unless it would need a verification email that cannot be sent."""
    settings = get_settings()
    return settings.password_enabled and (not settings.email_verification_required or mailer.can_send())


def _not_allowed() -> ApiError:
    return ApiError(403, "not_allowed", "This email address cannot use this server")


def _browser_proof(raw_token: str) -> str:
    """The ``radar_verify`` cookie value for a verification token (a hash: the cookie is not the link)."""
    return hashlib.sha256(f"radar-verify-browser:{raw_token}".encode()).hexdigest()


def _set_verify_cookie(response: Response, raw_token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        VERIFY_COOKIE,
        _browser_proof(raw_token),
        max_age=int(tokens.TTL[tokens.VERIFY_EMAIL].total_seconds()),
        path=VERIFY_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_verify_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        VERIFY_COOKIE, path=VERIFY_COOKIE_PATH, secure=settings.cookie_secure, httponly=True, samesite="lax"
    )


def _field_error(path: str, message: str) -> ApiError:
    return validation_error([{"path": path, "message": message}], message)


def _send_verification(user_id: int, email: str, next_path: str = "/") -> str | None:
    """Issue a verification token and email the link; returns the raw token (``None`` without mail)."""
    if not mailer.can_send():
        return None
    with session_scope(write=True) as session:
        raw = tokens.issue(session, user_id, tokens.VERIFY_EMAIL, email)
    link = tokens.link(tokens.VERIFY_EMAIL, raw)
    if next_path != "/":
        link += "&next=" + quote(next_path, safe="/")
    mailer.send_link_email(
        email,
        "Confirm your email for Ratiocination",
        "Open this link within 48 hours to confirm that this address belongs to you and sign in:",
        link,
    )
    return raw


@router.get("/config", response_model=AuthConfig)
def auth_config() -> AuthConfig:
    """``200 AuthConfig``."""
    settings = get_settings()
    return AuthConfig(
        mode=settings.auth,
        providers=AuthProviders(
            password=settings.password_enabled, google=settings.google_enabled, github=settings.github_enabled
        ),
        allowlist=bool(settings.allowed_emails),
        email_delivery=mailer.can_send(),
        password_signup=password_signup_open(),
        email_verification=settings.auth == "accounts" and settings.email_verification_required,
    )


@router.post("/signup", response_model=Me | VerificationPending, status_code=201)
def sign_up(body: SignUpRequest, request: Request, response: Response) -> Me | VerificationPending:
    """``201 Me`` + session cookie, or ``202 VerificationPending`` (no session; a verification link was
    emailed) while email verification is required; 409 ``email_taken``; 403 ``not_allowed``; 409
    ``not_configured`` when sign-up needs a verification email but SMTP is not set up; 429."""
    _require_password_login()
    verify_first = get_settings().email_verification_required
    if not password_signup_open():
        raise ApiError(
            409, "not_configured", "Sign-up with email and password is not available on this server"
        )
    throttle.check_attempt(_client_ip(request))
    email = passwords.normalize_email(body.email)
    if (problem := passwords.email_problem(email)) is not None:
        raise _field_error("email", problem)
    if (problem := passwords.password_problem(body.password, email)) is not None:
        raise _field_error("password", problem)
    if not get_settings().email_allowed(email):
        raise ApiError(403, "not_allowed", "This email address cannot create an account here")
    with session_scope() as session:
        if accounts.find_by_email(session, email) is not None:
            raise ApiError(409, "email_taken", "An account with this email already exists. Sign in instead.")
    password_hash = passwords.hash_password(body.password)
    token: str | None = None
    try:
        with session_scope(write=True) as session:
            user = accounts.new_user(
                session,
                email=email,
                email_verified=False,
                display_name=body.display_name,
                avatar_url=None,
                password_hash=password_hash,
            )
            if not verify_first:
                accounts.mark_signed_in(user)
                token = auth_sessions.create_session(session, user.id)
            user_id = user.id
    except IntegrityError:
        raise ApiError(
            409, "email_taken", "An account with this email already exists. Sign in instead."
        ) from None
    log.info("user %s signed up with a password", user_id)
    raw = _send_verification(user_id, email, oauth.safe_next(body.next))
    if token is None:
        response.status_code = 202
        if raw is not None:
            _set_verify_cookie(response, raw)
        return VerificationPending(email=email)
    auth_sessions.set_cookie(response, token)
    with session_scope() as session:
        return accounts.me_out(session, user_id)


@router.post("/signin", response_model=Me)
def sign_in(body: SignInRequest, request: Request, response: Response) -> Me:
    """``200 Me`` + session cookie; 401 ``invalid_credentials``; after a correct password, 403
    ``not_allowed`` (allow-list) or 403 ``email_unverified`` (verification required); 429."""
    _require_password_login()
    email = passwords.normalize_email(body.email)
    throttle.check_attempt(_client_ip(request))
    throttle.check_signin(email)
    with session_scope() as session:
        row = session.execute(
            select(User.id, User.password_hash, User.email_verified).where(User.email == email)
        ).first()
    user_id, stored_hash, verified = (
        (row[0], row[1], bool(row[2])) if row is not None else (None, None, False)
    )
    if not passwords.verify_password(stored_hash, body.password) or user_id is None:
        throttle.record_failure(email)
        raise ApiError(401, "invalid_credentials", "Email or password is incorrect")
    throttle.record_success(email)
    settings = get_settings()
    if not settings.email_allowed(email):
        raise _not_allowed()
    if not verified and settings.email_verification_required:
        raise ApiError(
            403,
            "email_unverified",
            "Confirm your email address first: open the link we emailed you, or ask for a new one.",
        )
    new_hash = passwords.hash_password(body.password) if passwords.needs_rehash(stored_hash or "") else None
    with session_scope(write=True) as session:
        user = session.get(User, user_id)
        if user is None or user.password_hash != stored_hash:
            raise ApiError(401, "invalid_credentials", "Email or password is incorrect")
        if new_hash is not None:
            user.password_hash = new_hash
        accounts.mark_signed_in(user)
        token = auth_sessions.create_session(session, user.id)
    auth_sessions.set_cookie(response, token)
    with session_scope() as session:
        return accounts.me_out(session, user_id)


@router.post("/signout", status_code=204, response_class=Response)
def sign_out(request: Request) -> Response:
    """``204``; deletes this session and clears the cookie."""
    response = Response(status_code=204)
    settings = get_settings()
    if settings.auth != "accounts":
        return response
    token = request.cookies.get(auth_sessions.cookie_name(settings))
    if token:
        with session_scope(write=True) as session:
            auth_sessions.revoke(session, token)
    auth_sessions.clear_cookie(response, settings)
    return response


# ------------------------------------------------------------------------------------------------
# OAuth
# ------------------------------------------------------------------------------------------------


def _signin_error(code: str) -> RedirectResponse:
    response = RedirectResponse(f"/signin?error={quote(code)}", status_code=302)
    _clear_state_cookie(response)
    return response


def _clear_state_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        oauth.STATE_COOKIE,
        path=oauth.STATE_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _signed_in_user_id(request: Request) -> int | None:
    settings = get_settings()
    info = auth_sessions.resolve(request.cookies.get(auth_sessions.cookie_name(settings)))
    return info.user_id if info is not None else None


@router.get("/oauth/{provider}/start", status_code=302, response_class=RedirectResponse)
def oauth_start(
    provider: str, request: Request, next: str = Query("/", max_length=500), link: int = Query(0, ge=0, le=1)
) -> RedirectResponse:
    """``302`` to Google/GitHub (404 when the provider is not configured)."""
    if provider not in oauth.PROVIDERS or not oauth.enabled(provider):
        raise ApiError(404, "not_found", "This sign-in method is not enabled")
    link_user_id: int | None = None
    if link:
        link_user_id = _signed_in_user_id(request)
        if link_user_id is None:
            return _signin_error("oauth_state")
    with session_scope(write=True) as session:
        url, state = oauth.begin(session, provider, next, link_user_id=link_user_id)
    response = RedirectResponse(url, status_code=302)
    settings = get_settings()
    response.set_cookie(
        oauth.STATE_COOKIE,
        state,
        max_age=600,
        path=oauth.STATE_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/oauth/{provider}/callback", status_code=302, response_class=RedirectResponse)
def oauth_callback(
    provider: str,
    request: Request,
    code: str | None = Query(None, max_length=2000),
    state: str | None = Query(None, max_length=200),
    error: str | None = Query(None, max_length=200),
) -> RedirectResponse:
    """``302`` to the saved return path, or to ``/signin?error=<code>``."""
    if provider not in oauth.PROVIDERS or not oauth.enabled(provider):
        raise ApiError(404, "not_found", "This sign-in method is not enabled")
    with session_scope(write=True) as session:
        pending = oauth.take_state(session, provider, state, request.cookies.get(oauth.STATE_COOKIE))
    if pending is None:
        return _signin_error("oauth_state")
    if error or not code:
        return _signin_error("oauth_denied")
    if pending.link_user_id is not None and _signed_in_user_id(request) != pending.link_user_id:
        return _signin_error("oauth_state")
    try:
        profile = oauth.fetch_profile(provider, code, pending)
        with session_scope(write=True) as session:
            user_id = accounts.resolve_oauth(session, profile, link_user_id=pending.link_user_id)
            token = (
                None if pending.link_user_id is not None else auth_sessions.create_session(session, user_id)
            )
    except accounts.AccountError as exc:
        log.info("%s sign-in refused: %s", provider, exc.code)
        return _signin_error(exc.code)
    log.info("user %s signed in with %s", user_id, provider)
    response = RedirectResponse(pending.next_path, status_code=302)
    _clear_state_cookie(response)
    if token is not None:
        auth_sessions.set_cookie(response, token)
    return response


# ------------------------------------------------------------------------------------------------
# Password reset and email verification
# ------------------------------------------------------------------------------------------------


@router.post("/password/forgot", status_code=202, response_class=Response)
def forgot_password(body: ForgotPasswordRequest, request: Request) -> Response:
    """``202`` always; emails a reset link when the address belongs to a password account."""
    _require_password_login()
    throttle.check_attempt(_client_ip(request))
    email = passwords.normalize_email(body.email)
    if passwords.email_problem(email) is None and mailer.can_send():
        throttle.check_email_send(email)
        with session_scope() as session:
            row = session.execute(select(User.id, User.password_hash).where(User.email == email)).first()
        if row is not None and row[1] is not None:
            with session_scope(write=True) as session:
                raw = tokens.issue(session, row[0], tokens.RESET_PASSWORD, email)
            mailer.send_link_email(
                email,
                "Reset your Ratiocination password",
                "Use this link within 30 minutes to choose a new password:",
                tokens.link(tokens.RESET_PASSWORD, raw),
            )
    return Response(status_code=202)


@router.post("/password/reset", response_model=Me)
def reset_password(body: ResetPasswordRequest, response: Response) -> Me:
    """``200 Me`` + session cookie (every other session ends); 400 ``invalid_token``."""
    _require_password_login()
    invalid = ApiError(400, "invalid_token", "This link is invalid or has expired. Ask for a new one.")
    with session_scope() as session:
        row = session.execute(
            select(
                AuthToken.user_id, AuthToken.email, AuthToken.purpose, AuthToken.used_at, AuthToken.expires_at
            ).where(AuthToken.token_hash == token_hash(body.token))
        ).first()
    if row is None or row[2] != tokens.RESET_PASSWORD or row[3] is not None or row[4] <= utcnow():
        raise invalid
    if not get_settings().email_allowed(row[1]):
        raise _not_allowed()
    if (problem := passwords.password_problem(body.password, row[1])) is not None:
        raise _field_error("password", problem)
    password_hash = passwords.hash_password(body.password)
    with session_scope(write=True) as session:
        token_row = tokens.consume(session, body.token, tokens.RESET_PASSWORD)
        user = session.get(User, token_row.user_id) if token_row is not None else None
        if token_row is None or user is None or user.email != token_row.email:
            raise invalid
        user.password_hash = password_hash
        auth_sessions.revoke_user_sessions(session, user.id)
        if not user.email_verified:
            user = accounts.confirm_email(session, user)  # receiving the link proves the address
        accounts.mark_signed_in(user)
        token = auth_sessions.create_session(session, user.id)
        user_id = user.id
    log.info("user %s reset their password", user_id)
    auth_sessions.set_cookie(response, token)
    with session_scope() as session:
        return accounts.me_out(session, user_id)


@router.post("/email/verify", response_model=Me)
def verify_email(body: VerifyEmailRequest, request: Request, response: Response) -> Me:
    """``200 Me`` + session cookie (works signed in or out); 400 ``invalid_token``; 401
    ``password_required`` (details ``{email}``) when the link is opened outside the browser that asked
    for it while verification is required, 401 ``invalid_credentials`` for a wrong password; 403
    ``not_allowed``; 429 ``rate_limited``."""
    _require_accounts()
    settings = get_settings()
    invalid = ApiError(400, "invalid_token", "This link is invalid or has expired. Ask for a new one.")
    with session_scope() as session:
        row = session.execute(
            select(
                AuthToken.purpose,
                AuthToken.used_at,
                AuthToken.expires_at,
                AuthToken.email,
                User.email,
                User.email_verified,
                User.password_hash,
            )
            .join(User, User.id == AuthToken.user_id)
            .where(AuthToken.token_hash == token_hash(body.token))
        ).first()
    if (
        row is None
        or row[0] != tokens.VERIFY_EMAIL
        or row[1] is not None
        or row[2] <= utcnow()
        or row[3] != row[4]
    ):
        raise invalid
    email, verified, stored_hash = str(row[4]), bool(row[5]), row[6]
    same_browser = hmac.compare_digest(request.cookies.get(VERIFY_COOKIE, ""), _browser_proof(body.token))
    # Whoever set the password never proved they own the address: unless this is their browser, the
    # person holding the link (who does) must know the password before it becomes a verified account.
    needs_password = (
        settings.email_verification_required and stored_hash is not None and not verified and not same_browser
    )
    if needs_password:
        if body.password is None:
            raise ApiError(
                401,
                "password_required",
                "Enter your password to confirm this email address",
                details={"email": email},
            )
        throttle.check_attempt(_client_ip(request))
        throttle.check_signin(email)
        if not passwords.verify_password(stored_hash, body.password):
            throttle.record_failure(email)
            raise ApiError(401, "invalid_credentials", "The password is incorrect")
        throttle.record_success(email)
    if not settings.email_allowed(email):
        raise _not_allowed()
    signed_in_as = _signed_in_user_id(request)
    with session_scope(write=True) as session:
        token_row = tokens.consume(session, body.token, tokens.VERIFY_EMAIL)
        user = session.get(User, token_row.user_id) if token_row is not None else None
        if token_row is None or user is None or user.email != token_row.email:
            raise invalid
        if needs_password and user.password_hash != stored_hash:
            raise invalid  # the password changed meanwhile
        if not user.email_verified:
            user = accounts.confirm_email(session, user)
        accounts.mark_signed_in(user)
        user_id = user.id
        session_token = None if signed_in_as == user_id else auth_sessions.create_session(session, user_id)
    log.info("user %s verified their email", user_id)
    if session_token is not None:
        auth_sessions.set_cookie(response, session_token)
    _clear_verify_cookie(response)
    with session_scope() as session:
        return accounts.me_out(session, user_id)


@router.post("/email/resend", status_code=202, response_class=Response)
def resend_verification(
    request: Request, body: Annotated[ResendVerificationRequest | None, Body()] = None
) -> Response:
    """``202``. With ``{email}`` (signed out): always 202 whether or not the address has an account
    waiting for verification (429 ``rate_limited`` per IP and per address). Without a body (signed in):
    409 ``conflict`` when already verified. Both: 409 ``not_configured`` without mail."""
    response = Response(status_code=202)
    if body is not None and body.email is not None:
        _require_password_login()
        throttle.check_attempt(_client_ip(request))
        if not mailer.can_send():
            raise ApiError(409, "not_configured", "Email delivery is not set up on this server")
        email = passwords.normalize_email(body.email)
        if passwords.email_problem(email) is not None:
            return response
        throttle.check_email_send(email)  # counted for every address, so 429 reveals nothing
        with session_scope() as session:
            row = session.execute(
                select(User.id, User.email_verified, User.password_hash).where(User.email == email)
            ).first()
        raw: str | None = None
        if row is not None and not row[1] and row[2] is not None and get_settings().email_allowed(email):
            raw = _send_verification(row[0], email, oauth.safe_next(body.next))
        _set_verify_cookie(response, raw or secrets.token_urlsafe(32))  # same answer either way
        return response
    user = current_user(request, response)
    with session_scope() as session:
        row_user = session.get(User, user.id)
        email_or_none, verified = (
            (row_user.email, row_user.email_verified) if row_user is not None else (None, True)
        )
    if verified or not email_or_none:
        raise ApiError(409, "conflict", "This email address is already confirmed")
    if not mailer.can_send():
        raise ApiError(409, "not_configured", "Email delivery is not set up on this server")
    throttle.check_email_send(email_or_none)
    _send_verification(user.id, email_or_none)
    return response
