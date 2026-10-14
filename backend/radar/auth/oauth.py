"""Google (OpenID Connect) and GitHub (OAuth 2) sign-in (SPEC §5.5). Owner: auth.

Flow: :func:`begin` stores a pending sign-in (state hashed, PKCE verifier, Google nonce, return path)
and returns the provider URL; the browser gets the state in an HttpOnly cookie too. On the callback,
:func:`take_state` checks the state against the cookie and the stored row (single use, 10 minutes),
then :func:`fetch_profile` exchanges the code through ``radar.net.http.HttpClient`` (never inside a
write transaction) and returns an :class:`~radar.auth.accounts.OAuthProfile` with a verified email.

Google's ID token comes straight from Google's token endpoint over TLS, authenticated with our client
secret, so (OpenID Connect Core §3.1.3.7) its claims are validated without a signature check: issuer,
audience, expiry (60 s leeway), nonce and ``email_verified``. GitHub's email is the primary verified
address from ``/user/emails``. Provider access tokens are used once and discarded.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar.auth.accounts import AccountError, OAuthProfile
from radar.auth.passwords import email_problem, normalize_email
from radar.auth.sessions import OAUTH_STATE_TTL, token_hash
from radar.config import get_settings
from radar.models import OAuthState, utcnow
from radar.net.http import EgressBlocked, SourceUnavailable, get_http

log = logging.getLogger(__name__)

STATE_COOKIE = "radar_oauth"
STATE_COOKIE_PATH = "/api/auth/oauth"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
GITHUB_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
CLOCK_LEEWAY = timedelta(seconds=60)
PROVIDERS = ("google", "github")


@dataclass(frozen=True)
class PendingSignIn:
    provider: str
    code_verifier: str
    nonce: str | None
    next_path: str
    link_user_id: int | None


# ------------------------------------------------------------------------------------------------
# Pure helpers
# ------------------------------------------------------------------------------------------------


def enabled(provider: str) -> bool:
    settings = get_settings()
    if provider == "google":
        return settings.google_enabled
    if provider == "github":
        return settings.github_enabled
    return False


def redirect_uri(provider: str) -> str:
    return f"{get_settings().public_origin}/api/auth/oauth/{provider}/callback"


def safe_next(next_path: str | None) -> str:
    """A same-site path to return to after sign-in; anything else becomes ``/``."""
    path = str(next_path or "").strip()
    if (
        not path.startswith("/")
        or path.startswith("//")
        or path.startswith("/\\")
        or path.startswith("/api/")
        or "\n" in path
        or "\r" in path
        or len(path) > 500
    ):
        return "/"
    return path


def pkce_challenge(verifier: str) -> str:
    """S256 code challenge: base64url(sha256(verifier)) without padding (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _b64url_json(segment: str) -> dict[str, Any]:
    padded = segment + "=" * (-len(segment) % 4)
    data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JWT segment is not an object")
    return data


def google_claims(id_token: str, *, client_id: str, nonce: str | None, now: datetime) -> dict[str, Any]:
    """Validated claims of a Google ID token received from the token endpoint (see module docstring).

    Raises ``ValueError`` with a short reason when a check fails.
    """
    parts = str(id_token or "").split(".")
    if len(parts) != 3:
        raise ValueError("malformed ID token")
    try:
        claims = _b64url_json(parts[1])
    except (ValueError, UnicodeError) as exc:
        raise ValueError("unreadable ID token") from exc
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise ValueError("wrong issuer")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences:
        raise ValueError("wrong audience")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        raise ValueError("missing expiry")
    if datetime.fromtimestamp(exp, tz=now.tzinfo) + CLOCK_LEEWAY < now:
        raise ValueError("expired ID token")
    if nonce is not None and claims.get("nonce") != nonce:
        raise ValueError("nonce mismatch")
    if claims.get("email_verified") not in (True, "true"):
        raise ValueError("email not verified")
    if not claims.get("sub") or not claims.get("email"):
        raise ValueError("missing subject or email")
    return claims


def github_primary_email(emails: Any) -> str | None:
    """The primary verified address from ``GET /user/emails`` (else any verified one), or ``None``."""
    if not isinstance(emails, list):
        return None
    verified = [
        e
        for e in emails
        if isinstance(e, dict) and e.get("verified") is True and isinstance(e.get("email"), str)
    ]
    primary = next((e for e in verified if e.get("primary") is True), None)
    chosen = primary or (verified[0] if verified else None)
    return normalize_email(chosen["email"]) if chosen else None


# ------------------------------------------------------------------------------------------------
# Start and state
# ------------------------------------------------------------------------------------------------


def begin(
    session: Session, provider: str, next_path: str | None, *, link_user_id: int | None = None
) -> tuple[str, str]:
    """Store a pending sign-in (write session) and return ``(provider_url, state)``."""
    settings = get_settings()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(24) if provider == "google" else None
    session.add(
        OAuthState(
            state_hash=token_hash(state),
            provider=provider,
            code_verifier=verifier,
            nonce=nonce,
            next_path=safe_next(next_path),
            link_user_id=link_user_id,
        )
    )
    session.flush()
    common = {
        "redirect_uri": redirect_uri(provider),
        "state": state,
        "code_challenge": pkce_challenge(verifier),
        "code_challenge_method": "S256",
    }
    if provider == "google":
        params = {
            "client_id": settings.google_client_id,
            "response_type": "code",
            "scope": "openid email profile",
            "nonce": nonce,
            "prompt": "select_account",
            **common,
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", state
    params = {
        "client_id": settings.github_client_id,
        "scope": "read:user user:email",
        "allow_signup": "true",
        **common,
    }
    return f"{GITHUB_AUTH_URL}?{urlencode(params)}", state


def take_state(
    session: Session, provider: str, state: str | None, cookie_state: str | None
) -> PendingSignIn | None:
    """Consume the pending sign-in for ``state`` (write session; single use).

    Returns ``None`` when the state is missing, does not match the browser's cookie, is unknown,
    expired, or belongs to another provider. A found row is deleted in every case, so the caller can
    commit and then report ``oauth_state``.
    """
    if not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        return None
    row = session.scalar(select(OAuthState).where(OAuthState.state_hash == token_hash(state)))
    if row is None:
        return None
    pending = PendingSignIn(
        provider=row.provider,
        code_verifier=row.code_verifier,
        nonce=row.nonce,
        next_path=row.next_path,
        link_user_id=row.link_user_id,
    )
    expired = row.created_at < utcnow() - OAUTH_STATE_TTL
    session.execute(delete(OAuthState).where(OAuthState.id == row.id))
    if expired or row.provider != provider:
        return None
    return pending


# ------------------------------------------------------------------------------------------------
# Code exchange (network)
# ------------------------------------------------------------------------------------------------


def _json(response: Any) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _google_profile(code: str, pending: PendingSignIn) -> OAuthProfile:
    settings = get_settings()
    response = get_http().request(
        "POST",
        GOOGLE_TOKEN_URL,
        provider="google",
        call_type="auth",
        data={
            "code": code,
            "client_id": settings.google_client_id or "",
            "client_secret": settings.google_client_secret or "",
            "redirect_uri": redirect_uri("google"),
            "grant_type": "authorization_code",
            "code_verifier": pending.code_verifier,
        },
        headers={"Accept": "application/json"},
    )
    payload = _json(response)
    if response.status_code != 200 or not isinstance(payload, dict) or not payload.get("id_token"):
        log.warning("google sign-in: token exchange failed (HTTP %s)", response.status_code)
        raise AccountError("oauth_failed")
    try:
        claims = google_claims(
            str(payload["id_token"]),
            client_id=settings.google_client_id or "",
            nonce=pending.nonce,
            now=utcnow(),
        )
    except ValueError as exc:
        log.warning("google sign-in: ID token rejected (%s)", exc)
        raise AccountError("no_verified_email" if "verified" in str(exc) else "oauth_failed") from None
    email = normalize_email(str(claims["email"]))
    if email_problem(email):
        raise AccountError("no_verified_email")
    name = claims.get("name")
    picture = claims.get("picture")
    return OAuthProfile(
        provider="google",
        subject=str(claims["sub"]),
        email=email,
        name=str(name)[:100] if name else None,
        avatar_url=str(picture) if isinstance(picture, str) and picture.startswith("https://") else None,
    )


def _github_profile(code: str, pending: PendingSignIn) -> OAuthProfile:
    settings = get_settings()
    http = get_http()
    response = http.request(
        "POST",
        GITHUB_TOKEN_URL,
        provider="github",
        call_type="auth",
        data={
            "client_id": settings.github_client_id or "",
            "client_secret": settings.github_client_secret or "",
            "code": code,
            "redirect_uri": redirect_uri("github"),
            "code_verifier": pending.code_verifier,
        },
        headers={"Accept": "application/json"},
    )
    payload = _json(response)
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if response.status_code != 200 or not isinstance(token, str) or not token:
        log.warning("github sign-in: token exchange failed (HTTP %s)", response.status_code)
        raise AccountError("oauth_failed")
    user_response = http.request(
        "GET", GITHUB_USER_URL, provider="github", call_type="auth", headers=GITHUB_HEADERS, bearer=token
    )
    emails_response = http.request(
        "GET", GITHUB_EMAILS_URL, provider="github", call_type="auth", headers=GITHUB_HEADERS, bearer=token
    )
    user = _json(user_response)
    if user_response.status_code != 200 or not isinstance(user, dict) or not user.get("id"):
        raise AccountError("oauth_failed")
    email = github_primary_email(_json(emails_response)) if emails_response.status_code == 200 else None
    if email is None or email_problem(email):
        raise AccountError("no_verified_email")
    name = user.get("name") or user.get("login")
    avatar = user.get("avatar_url")
    return OAuthProfile(
        provider="github",
        subject=str(user["id"]),
        email=email,
        name=str(name)[:100] if name else None,
        avatar_url=str(avatar) if isinstance(avatar, str) and avatar.startswith("https://") else None,
    )


def fetch_profile(provider: str, code: str, pending: PendingSignIn) -> OAuthProfile:
    """Exchange ``code`` and return the verified profile; raises :class:`AccountError`."""
    if not code or len(code) > 2000:
        raise AccountError("oauth_failed")
    try:
        if provider == "google":
            return _google_profile(code, pending)
        if provider == "github":
            return _github_profile(code, pending)
    except (SourceUnavailable, EgressBlocked) as exc:
        log.warning("%s sign-in: provider unreachable (%s)", provider, type(exc).__name__)
        raise AccountError("oauth_failed") from None
    raise AccountError("oauth_failed")


__all__ = [
    "PROVIDERS",
    "STATE_COOKIE",
    "STATE_COOKIE_PATH",
    "PendingSignIn",
    "begin",
    "enabled",
    "fetch_profile",
    "github_primary_email",
    "google_claims",
    "pkce_challenge",
    "redirect_uri",
    "safe_next",
    "take_state",
]
