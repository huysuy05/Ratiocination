"""Accounts (SPEC §5): password sign-up/sign-in, sessions, throttling, reset and verification links,
Google/GitHub OAuth with account resolution (incl. the pre-hijacking defense), user isolation, admin
rules and account deletion. Every outbound call goes to ``http_mock``; mail goes to ``mailer.capture``."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from radar import models
from radar.auth import mailer, oauth
from radar.db import session_scope

BASE = "http://localhost:8000"
PUBLIC_BASE = "https://localhost:8000"  # a public server must have an https RADAR_PUBLIC_URL
PASSWORD = "correct horse battery"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_USER = "https://api.github.com/user"
GITHUB_EMAILS = "https://api.github.com/user/emails"


# ------------------------------------------------------------------------------------------------
# Fixtures and helpers
# ------------------------------------------------------------------------------------------------


PROVIDER_ENV = {
    "RADAR_GOOGLE_CLIENT_ID": "google-client",
    "RADAR_GOOGLE_CLIENT_SECRET": "google-secret",
    "RADAR_GITHUB_CLIENT_ID": "github-client",
    "RADAR_GITHUB_CLIENT_SECRET": "github-secret",
}


@pytest.fixture
def accounts_app(set_env: Callable[..., None]) -> Iterator[FastAPI]:
    """Accounts mode on a loopback-only server (the developer setup: no email verification gate)."""
    set_env(
        RADAR_AUTH="accounts", RADAR_PUBLIC_URL=BASE, RADAR_REQUIRE_EMAIL_VERIFICATION=None, **PROVIDER_ENV
    )
    from radar.main import create_app

    app = create_app()
    with TestClient(app, base_url=BASE, headers={"X-Radar-Client": "web"}):  # runs the lifespan once
        yield app


@pytest.fixture
def browser(accounts_app: FastAPI) -> Callable[[], TestClient]:
    """A new browser (its own cookie jar) for the accounts-mode app."""
    return lambda: TestClient(accounts_app, base_url=BASE, headers={"X-Radar-Client": "web"})


@pytest.fixture
def public_app(set_env: Callable[..., None]) -> Iterator[FastAPI]:
    """Accounts mode on a public server: non-loopback bind behind a TLS proxy, RADAR_ADMIN_EMAILS set."""
    set_env(
        RADAR_AUTH="accounts",
        RADAR_HOST="0.0.0.0",
        RADAR_BEHIND_TLS_PROXY="1",
        RADAR_ADMIN_EMAILS="boss@example.org",
        RADAR_PUBLIC_URL=PUBLIC_BASE,
        RADAR_REQUIRE_EMAIL_VERIFICATION=None,
        **PROVIDER_ENV,
    )
    from radar.main import create_app

    app = create_app()
    with TestClient(app, base_url=PUBLIC_BASE, headers={"X-Radar-Client": "web"}):
        yield app


@pytest.fixture
def public(public_app: FastAPI) -> Callable[[], TestClient]:
    """A new browser for the public server (https: Secure ``__Host-`` session cookies)."""
    return lambda: TestClient(public_app, base_url=PUBLIC_BASE, headers={"X-Radar-Client": "web"})


@pytest.fixture
def outbox() -> Iterator[list[mailer.OutgoingMail]]:
    """SMTP stand-in: mail is collected instead of sent (and counts as configured)."""
    with mailer.capture() as box:
        yield box


def sign_up(client: TestClient, email: str, password: str = PASSWORD, **extra: Any) -> dict[str, Any]:
    r = client.post("/api/auth/signup", json={"email": email, "password": password, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def error_code(response: httpx.Response) -> str:
    return response.json()["error"]["code"]


def jwt(claims: dict[str, Any]) -> str:
    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256', 'kid': 'k'})}.{part(claims)}.c2lnbmF0dXJl"


def google_claims(nonce: str | None, **overrides: Any) -> dict[str, Any]:
    claims = {
        "iss": "https://accounts.google.com",
        "aud": "google-client",
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        "nonce": nonce,
        "sub": "google-sub-1",
        "email": "ada@example.org",
        "email_verified": True,
        "name": "Ada Lovelace",
        "picture": "https://lh3.googleusercontent.com/ada",
    }
    claims.update(overrides)
    return claims


def start(client: TestClient, provider: str, **params: Any) -> dict[str, str]:
    r = client.get(f"/api/auth/oauth/{provider}/start", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    query = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    assert "radar_oauth=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    return query


def callback(client: TestClient, provider: str, **params: Any) -> httpx.Response:
    return client.get(f"/api/auth/oauth/{provider}/callback", params=params, follow_redirects=False)


def google_sign_in(client: TestClient, http_mock: Any, **claim_overrides: Any) -> httpx.Response:
    query = start(client, "google", next="/projects")
    seen: dict[str, Any] = {}

    def token_endpoint(request: httpx.Request) -> dict[str, Any]:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        seen.update(form)
        claims = {**google_claims(query["nonce"]), **claim_overrides}
        return {"access_token": "ya29.x", "id_token": jwt(claims)}

    http_mock.add("POST", GOOGLE_TOKEN, token_endpoint, times=1)
    response = callback(client, "google", code="google-code", state=query["state"])
    if seen:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(seen["code_verifier"].encode()).digest()).rstrip(
            b"="
        )
        assert challenge.decode() == query["code_challenge"]  # PKCE verifier matches the challenge
        assert (seen["code"], seen["client_secret"], seen["grant_type"]) == (
            "google-code",
            "google-secret",
            "authorization_code",
        )
    return response


def github_routes(http_mock: Any, *, user_id: int = 4242, emails: Any = None) -> None:
    http_mock.add("POST", GITHUB_TOKEN, {"access_token": "gho_zqtoken", "token_type": "bearer"}, times=1)
    http_mock.add(
        "GET",
        GITHUB_USER,
        {
            "id": user_id,
            "login": "ada",
            "name": None,
            "avatar_url": "https://avatars.githubusercontent.com/u/1",
        },
        times=1,
    )
    http_mock.add(
        "GET",
        GITHUB_EMAILS,
        emails
        if emails is not None
        else [
            {"email": "old@example.org", "primary": False, "verified": True},
            {"email": "Ada@Example.org", "primary": True, "verified": True},
        ],
        times=1,
    )


def github_sign_in(client: TestClient, http_mock: Any, **kwargs: Any) -> httpx.Response:
    query = start(client, "github")
    github_routes(http_mock, **kwargs)
    return callback(client, "github", code="github-code", state=query["state"])


# ------------------------------------------------------------------------------------------------
# Config, sign-up, sign-in, sessions
# ------------------------------------------------------------------------------------------------


def test_auth_config(browser: Callable[[], TestClient], set_env: Callable[..., None]) -> None:
    assert browser().get("/api/auth/config").json() == {
        "mode": "accounts",
        "providers": {"password": True, "google": True, "github": True},
        "allowlist": False,
        "email_delivery": False,
        "password_signup": True,  # loopback-only: sign-up needs no verification email
        "email_verification": False,
    }


def test_sign_up_sets_a_session_cookie_and_claims_user_one(browser: Callable[[], TestClient]) -> None:
    client = browser()
    assert client.get("/api/me").status_code == 401
    r = client.post("/api/auth/signup", json={"email": " Ada@Example.ORG ", "password": PASSWORD})
    assert r.status_code == 201
    cookie = r.headers["set-cookie"]
    for part in ("radar_session=", "HttpOnly", "Path=/", "SameSite=lax", "Max-Age=2592000"):
        assert part in cookie, part
    assert "Secure" not in cookie  # http public URL
    me = r.json()
    assert me["mode"] == "accounts"
    user = me["user"]
    assert (user["id"], user["email"], user["email_verified"], user["is_admin"]) == (
        1,
        "ada@example.org",
        False,
        True,
    )
    assert user["has_password"] is True and user["identities"] == [] and user["display_name"] == "ada"
    assert client.get("/api/me").json()["user"]["id"] == 1
    other = sign_up(browser(), "bob@example.org", display_name="Bob")
    assert (other["user"]["id"], other["user"]["is_admin"], other["user"]["display_name"]) != (1, True, "Bob")
    assert other["user"]["display_name"] == "Bob" and other["user"]["is_admin"] is False
    with session_scope() as s:
        stored = s.scalar(select(models.AuthSession.token_hash))
        assert stored is not None and len(stored) == 64
        token = cookie.split("radar_session=", 1)[1].split(";", 1)[0]
        assert token not in {
            row for row in s.scalars(select(models.AuthSession.token_hash))
        }  # hashed at rest


def test_sign_up_rules(browser: Callable[[], TestClient], set_env: Callable[..., None]) -> None:
    client = browser()
    sign_up(client, "ada@example.org")
    taken = client.post("/api/auth/signup", json={"email": "ADA@example.org", "password": PASSWORD})
    assert taken.status_code == 409 and error_code(taken) == "email_taken"
    short = client.post("/api/auth/signup", json={"email": "bob@example.org", "password": "short"})
    assert short.status_code == 422 and short.json()["error"]["details"]["fields"][0]["path"] == "password"
    same = client.post(
        "/api/auth/signup", json={"email": "carol@example.org", "password": "carol@example.org"}
    )
    assert same.status_code == 422 and "email" in same.json()["error"]["message"].lower()
    bad = client.post("/api/auth/signup", json={"email": "not-an-email", "password": PASSWORD})
    assert bad.status_code == 422
    set_env(RADAR_ALLOWED_EMAILS="@lab.example.org")
    refused = client.post("/api/auth/signup", json={"email": "eve@example.org", "password": PASSWORD})
    assert refused.status_code == 403 and error_code(refused) == "not_allowed"
    assert (
        browser()
        .post("/api/auth/signup", json={"email": "dan@lab.example.org", "password": PASSWORD})
        .status_code
        == 201
    )


def test_sign_in_and_sign_out(browser: Callable[[], TestClient]) -> None:
    sign_up(browser(), "ada@example.org")
    client = browser()
    wrong = client.post("/api/auth/signin", json={"email": "ada@example.org", "password": "wrong password!"})
    unknown = client.post("/api/auth/signin", json={"email": "nobody@example.org", "password": PASSWORD})
    for r in (wrong, unknown):  # identical answers: no account enumeration
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "invalid_credentials"
        assert r.json()["error"]["message"] == "Email or password is incorrect"
    ok = client.post("/api/auth/signin", json={"email": "ADA@example.org", "password": PASSWORD})
    assert ok.status_code == 200 and ok.json()["user"]["email"] == "ada@example.org"
    assert client.get("/api/projects").status_code == 200
    out = client.post("/api/auth/signout")
    assert out.status_code == 204 and 'radar_session=""' in out.headers["set-cookie"]
    assert client.get("/api/projects").status_code == 401


def test_failed_sign_ins_are_throttled_per_email(browser: Callable[[], TestClient]) -> None:
    sign_up(browser(), "ada@example.org")
    client = browser()
    for _ in range(5):
        assert (
            client.post(
                "/api/auth/signin", json={"email": "ada@example.org", "password": "nope nope!"}
            ).status_code
            == 401
        )
    blocked = client.post("/api/auth/signin", json={"email": "ada@example.org", "password": PASSWORD})
    assert blocked.status_code == 429 and error_code(blocked) == "rate_limited"
    assert int(blocked.headers["retry-after"]) > 0
    assert blocked.json()["error"]["details"]["retry_after_seconds"] > 0
    # another account is unaffected
    sign_up(browser(), "bob@example.org")
    assert (
        client.post("/api/auth/signin", json={"email": "bob@example.org", "password": PASSWORD}).status_code
        == 200
    )


def test_expired_and_unknown_sessions_are_rejected(browser: Callable[[], TestClient]) -> None:
    client = browser()
    sign_up(client, "ada@example.org")
    with session_scope(write=True) as s:
        for row in s.scalars(select(models.AuthSession)):
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get("/api/me").status_code == 401
    forged = browser()
    forged.cookies.set("radar_session", "x" * 43)
    assert forged.get("/api/projects").status_code == 401


def test_sessions_slide_and_record_activity(browser: Callable[[], TestClient]) -> None:
    client = browser()
    sign_up(client, "ada@example.org")
    old = datetime.now(UTC) - timedelta(days=3)
    with session_scope(write=True) as s:
        row = s.scalar(select(models.AuthSession))
        assert row is not None
        row.last_seen_at = old
        row.expires_at = old + timedelta(days=30)
    r = client.get("/api/me")
    assert r.status_code == 200 and "radar_session=" in r.headers.get("set-cookie", "")  # re-issued
    from radar.db import flush_writes

    flush_writes()
    with session_scope() as s:
        row = s.scalar(select(models.AuthSession))
        assert row is not None and row.last_seen_at > old + timedelta(days=2)
        assert row.expires_at > datetime.now(UTC) + timedelta(days=29)


# ------------------------------------------------------------------------------------------------
# Isolation and admin rules
# ------------------------------------------------------------------------------------------------


def test_users_never_see_each_others_projects_documents_or_jobs(browser: Callable[[], TestClient]) -> None:
    ada, bob = browser(), browser()
    sign_up(ada, "ada@example.org")
    sign_up(bob, "bob@example.org")
    project = ada.post(
        "/api/projects",
        json={
            "name": "Ada's project",
            "research_question": "q?",
            "settings": {"arxiv_categories": ["cs.IR"]},
        },
    ).json()
    pid = project["id"]
    doc = ada.post(
        f"/api/projects/{pid}/documents",
        files={"file": ("draft.md", b"# Draft\n\nPrivate words.", "text/markdown")},
    )
    assert doc.status_code == 202, doc.text
    document_id = doc.json()["document"]["id"]

    assert bob.get("/api/projects").json() == []
    for method, path in (
        ("GET", f"/api/projects/{pid}"),
        ("PATCH", f"/api/projects/{pid}"),
        ("DELETE", f"/api/projects/{pid}"),
        ("GET", f"/api/projects/{pid}/seeds"),
        ("GET", f"/api/projects/{pid}/documents"),
        ("GET", f"/api/documents/{document_id}/status"),
        ("DELETE", f"/api/documents/{document_id}"),
    ):
        kwargs = {"json": {"name": "x"}} if method == "PATCH" else {}
        r = bob.request(method, path, **kwargs)
        assert r.status_code == 404, (method, path, r.text)
    upload = bob.post(
        f"/api/projects/{pid}/documents", files={"file": ("x.md", b"# X\n\nwords", "text/markdown")}
    )
    assert upload.status_code == 404
    seed = bob.post(f"/api/projects/{pid}/seeds", json={"paper_id": 1})
    assert seed.status_code == 404

    ada_jobs = ada.get("/api/jobs").json()
    assert {j["kind"] for j in ada_jobs} >= {"build_deck", "process_document"}
    assert bob.get("/api/jobs").json() == []
    assert bob.get(f"/api/jobs/{ada_jobs[0]['id']}").status_code == 404
    # Ada is the admin (first account): she sees every job anyway
    assert ada.get(f"/api/jobs/{ada_jobs[0]['id']}").status_code == 200


def test_admin_only_routes(browser: Callable[[], TestClient]) -> None:
    ada, bob = browser(), browser()
    sign_up(ada, "ada@example.org")
    sign_up(bob, "bob@example.org")
    assert ada.get("/api/settings").json()["admin"] is not None
    assert bob.get("/api/settings").json()["admin"] is None
    for r in (
        bob.patch("/api/settings", json={"scheduler_enabled": True}),
        bob.post("/api/settings/embedding/download"),
        bob.post("/api/jobs/run", json={"kind": "daily"}),
        bob.post("/api/jobs/run", json={"kind": "expand", "params": {"paper_id": 1}}),
    ):
        assert r.status_code == 403 and error_code(r) == "forbidden"
    assert ada.patch("/api/settings", json={"scheduler_enabled": False}).status_code == 200


def test_admin_emails_decide_who_claims_user_one(
    browser: Callable[[], TestClient], set_env: Callable[..., None]
) -> None:
    """Loopback-only server: an unverified password sign-up may still claim user 1 (developer setup).
    The public-server rules are in test_public_verification_grants_admin_and_user_one."""
    set_env(RADAR_ADMIN_EMAILS="boss@example.org")
    with session_scope(write=True) as s:  # data created in local mode belongs to user 1
        s.add(models.Project(owner_id=1, name="Local project", research_question="q?"))
    first = sign_up(browser(), "early@example.org")
    assert first["user"]["id"] != 1 and first["user"]["is_admin"] is False
    boss_client = browser()
    boss = sign_up(boss_client, "boss@example.org")
    assert boss["user"]["id"] == 1 and boss["user"]["is_admin"] is True
    assert [p["name"] for p in boss_client.get("/api/projects").json()] == ["Local project"]


# ------------------------------------------------------------------------------------------------
# Profile, password, account deletion
# ------------------------------------------------------------------------------------------------


def test_profile_and_password_change(browser: Callable[[], TestClient]) -> None:
    client, other_device = browser(), browser()
    sign_up(client, "ada@example.org")
    other_device.post("/api/auth/signin", json={"email": "ada@example.org", "password": PASSWORD})
    me = client.patch(
        "/api/me", json={"display_name": "Ada L.", "preferences": {"timezone": "Europe/London"}}
    ).json()
    assert me["user"]["display_name"] == "Ada L."
    assert me["user"]["preferences"] == {"auto_expand": True, "timezone": "Europe/London"}
    wrong = client.post(
        "/api/me/password", json={"current_password": "not it at all", "new_password": "a new password"}
    )
    assert wrong.status_code == 401 and error_code(wrong) == "invalid_credentials"
    ok = client.post(
        "/api/me/password", json={"current_password": PASSWORD, "new_password": "a new password"}
    )
    assert ok.status_code == 200
    assert client.get("/api/me").status_code == 200  # this session survives
    assert other_device.get("/api/me").status_code == 401  # every other session ends
    assert (
        browser()
        .post("/api/auth/signin", json={"email": "ada@example.org", "password": "a new password"})
        .status_code
        == 200
    )


def test_revoke_other_sessions(browser: Callable[[], TestClient]) -> None:
    client, laptop = browser(), browser()
    sign_up(client, "ada@example.org")
    laptop.post("/api/auth/signin", json={"email": "ada@example.org", "password": PASSWORD})
    assert client.post("/api/me/sessions/revoke-others").json() == {"revoked": 1}
    assert laptop.get("/api/me").status_code == 401 and client.get("/api/me").status_code == 200


def test_delete_account(browser: Callable[[], TestClient]) -> None:
    ada, bob = browser(), browser()
    sign_up(ada, "ada@example.org")
    sign_up(bob, "bob@example.org")
    pid = bob.post("/api/projects", json={"name": "Bob's project", "research_question": "q?"}).json()["id"]
    bob.post(
        f"/api/projects/{pid}/documents", files={"file": ("d.md", b"# D\n\nzqprivate words", "text/markdown")}
    )
    wrong = bob.request("DELETE", "/api/me", json={"confirm_email": "ada@example.org"})
    assert wrong.status_code == 422
    last_admin = ada.request("DELETE", "/api/me", json={"confirm_email": "ada@example.org"})
    assert last_admin.status_code == 409  # Bob still exists
    gone = bob.request("DELETE", "/api/me", json={"confirm_email": "bob@example.org"})
    assert gone.status_code == 204
    assert bob.get("/api/me").status_code == 401
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.Project)) == 0
        assert s.scalar(select(func.count()).select_from(models.Document)) == 0
        assert s.scalar(select(func.count()).where(models.User.email == "bob@example.org")) == 0
    assert (
        browser()
        .post("/api/auth/signin", json={"email": "bob@example.org", "password": PASSWORD})
        .status_code
        == 401
    )
    # now Ada is alone: she may delete her account (user 1 is reset, not removed)
    assert ada.request("DELETE", "/api/me", json={"confirm_email": "ada@example.org"}).status_code == 204
    with session_scope() as s:
        user = s.get(models.User, 1)
        assert user is not None and user.email is None and user.password_hash is None


# ------------------------------------------------------------------------------------------------
# Reset and verification links
# ------------------------------------------------------------------------------------------------


def _token(mail: mailer.OutgoingMail) -> str:
    match = re.search(r"token=([A-Za-z0-9_-]+)", mail.body)
    assert match is not None
    return match.group(1)


def test_password_reset_flow(browser: Callable[[], TestClient]) -> None:
    client, laptop = browser(), browser()
    with mailer.capture() as outbox:
        sign_up(client, "ada@example.org")
        laptop.post("/api/auth/signin", json={"email": "ada@example.org", "password": PASSWORD})
        outbox.clear()  # the verification mail
        assert (
            browser().post("/api/auth/password/forgot", json={"email": "nobody@example.org"}).status_code
            == 202
        )
        assert outbox == []  # unknown address: same answer, no mail
        assert (
            browser().post("/api/auth/password/forgot", json={"email": "ADA@example.org"}).status_code == 202
        )
    (mail,) = outbox
    assert mail.to == "ada@example.org" and f"{BASE}/reset?token=" in mail.body
    token = _token(mail)
    fresh = browser()
    weak = fresh.post("/api/auth/password/reset", json={"token": token, "password": "short"})
    assert weak.status_code == 422
    reset = fresh.post("/api/auth/password/reset", json={"token": token, "password": "brand new password"})
    assert reset.status_code == 200 and reset.json()["user"]["email_verified"] is True
    assert fresh.get("/api/me").status_code == 200
    assert client.get("/api/me").status_code == 401 and laptop.get("/api/me").status_code == 401
    again = browser().post("/api/auth/password/reset", json={"token": token, "password": "another new pass"})
    assert again.status_code == 400 and error_code(again) == "invalid_token"
    assert (
        browser()
        .post("/api/auth/signin", json={"email": "ada@example.org", "password": "brand new password"})
        .status_code
        == 200
    )


def test_email_verification_flow(browser: Callable[[], TestClient]) -> None:
    client = browser()
    with mailer.capture() as outbox:
        sign_up(client, "ada@example.org")
        (mail,) = outbox
        assert f"{BASE}/verify?token=" in mail.body
        token = _token(mail)
        elsewhere = browser()  # signed out, another browser: the link signs in there too
        verified = elsewhere.post("/api/auth/email/verify", json={"token": token})
        assert verified.status_code == 200 and verified.json()["user"]["email_verified"] is True
        assert "radar_session=" in verified.headers["set-cookie"]
        assert elsewhere.get("/api/me").json()["user"]["email"] == "ada@example.org"
        assert client.get("/api/me").json()["user"]["email_verified"] is True
        reused = browser().post("/api/auth/email/verify", json={"token": token})
        assert reused.status_code == 400 and error_code(reused) == "invalid_token"
        resend = client.post("/api/auth/email/resend")
        assert resend.status_code == 409 and error_code(resend) == "conflict"
    unconfigured = browser()
    sign_up(unconfigured, "bob@example.org")  # no SMTP and no capture: no mail, still works
    assert unconfigured.post("/api/auth/email/resend").json()["error"]["code"] == "not_configured"


# ------------------------------------------------------------------------------------------------
# OAuth
# ------------------------------------------------------------------------------------------------


def test_google_start_redirect(browser: Callable[[], TestClient]) -> None:
    query = start(browser(), "google", next="/dashboard")
    assert query["client_id"] == "google-client" and query["response_type"] == "code"
    assert query["redirect_uri"] == f"{BASE}/api/auth/oauth/google/callback"
    assert query["scope"] == "openid email profile" and query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 40 and len(query["nonce"]) >= 30 and len(query["code_challenge"]) == 43
    with session_scope() as s:
        row = s.scalar(select(models.OAuthState))
        assert row is not None and row.next_path == "/dashboard" and row.state_hash != query["state"]
    assert browser().get("/api/auth/oauth/twitter/start", follow_redirects=False).status_code == 404


def test_google_sign_in_creates_a_verified_account(browser: Callable[[], TestClient], http_mock: Any) -> None:
    client = browser()
    r = google_sign_in(client, http_mock)
    assert r.status_code == 302 and r.headers["location"] == "/projects"
    assert "radar_session=" in r.headers["set-cookie"]
    me = client.get("/api/me").json()["user"]
    assert (me["email"], me["email_verified"], me["has_password"], me["display_name"]) == (
        "ada@example.org",
        True,
        False,
        "Ada Lovelace",
    )
    assert [i["provider"] for i in me["identities"]] == ["google"]
    assert me["avatar_url"] == "https://lh3.googleusercontent.com/ada"
    # the same Google account signs into the same user again
    again = browser()
    google_sign_in(again, http_mock)
    assert again.get("/api/me").json()["user"]["id"] == me["id"]
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(models.UserIdentity)) == 1
        assert s.scalar(select(func.count()).select_from(models.OAuthState)) == 0  # single use


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"aud": "someone-else"}, "oauth_failed"),
        ({"iss": "https://evil.example.org"}, "oauth_failed"),
        ({"exp": 1}, "oauth_failed"),
        ({"nonce": "replayed"}, "oauth_failed"),
        ({"email_verified": False}, "no_verified_email"),
    ],
)
def test_google_claims_are_checked(
    browser: Callable[[], TestClient], http_mock: Any, overrides: dict[str, Any], code: str
) -> None:
    client = browser()
    r = google_sign_in(client, http_mock, **overrides)
    assert r.status_code == 302 and r.headers["location"] == f"/signin?error={code}"
    assert client.get("/api/me").status_code == 401


def test_state_must_match_the_browsers_cookie(browser: Callable[[], TestClient], http_mock: Any) -> None:
    victim, attacker = browser(), browser()
    query = start(attacker, "google")
    r = callback(victim, "google", code="attacker-code", state=query["state"])  # login CSRF attempt
    assert r.headers["location"] == "/signin?error=oauth_state"
    assert http_mock.requests == []  # the code was never exchanged
    assert callback(attacker, "google", error="access_denied", state=query["state"]).headers["location"] == (
        "/signin?error=oauth_denied"
    )
    unknown = browser()
    unknown.cookies.set("radar_oauth", "made-up", path="/api/auth/oauth")
    assert (
        callback(unknown, "google", code="c", state="made-up").headers["location"]
        == "/signin?error=oauth_state"
    )


def test_github_sign_in_uses_the_primary_verified_email(
    browser: Callable[[], TestClient], http_mock: Any
) -> None:
    client = browser()
    r = github_sign_in(client, http_mock)
    assert r.status_code == 302 and r.headers["location"] == "/"
    me = client.get("/api/me").json()["user"]
    assert me["email"] == "ada@example.org" and me["display_name"] == "ada"  # login when name is empty
    user_call = http_mock.calls("GET", GITHUB_USER)[0]
    assert user_call.headers["authorization"] == "Bearer gho_zqtoken"
    token_call = http_mock.calls("POST", GITHUB_TOKEN)[0]
    assert token_call.headers["accept"] == "application/json" and b"code_verifier=" in token_call.content
    unverified = browser()
    r = github_sign_in(
        unverified,
        http_mock,
        user_id=99,
        emails=[{"email": "x@example.org", "primary": True, "verified": False}],
    )
    assert r.headers["location"] == "/signin?error=no_verified_email"


def test_oauth_links_to_an_existing_verified_account(
    browser: Callable[[], TestClient], http_mock: Any
) -> None:
    password_user = browser()
    with mailer.capture() as outbox:
        sign_up(password_user, "ada@example.org")
        browser().post("/api/auth/email/verify", json={"token": _token(outbox[0])})
    google = browser()
    google_sign_in(google, http_mock)
    me = google.get("/api/me").json()["user"]
    assert (
        me["id"] == 1
        and me["has_password"] is True
        and [i["provider"] for i in me["identities"]] == ["google"]
    )
    assert password_user.get("/api/me").status_code == 200  # a verified owner keeps their sessions


def test_pre_hijacked_password_is_removed(browser: Callable[[], TestClient], http_mock: Any) -> None:
    attacker = browser()
    sign_up(attacker, "ada@example.org", password="attacker password")  # never verified
    victim = browser()
    google_sign_in(victim, http_mock)
    me = victim.get("/api/me").json()["user"]
    assert me["email_verified"] is True and me["has_password"] is False
    assert attacker.get("/api/me").status_code == 401  # the attacker's session ended
    retry = browser().post(
        "/api/auth/signin", json={"email": "ada@example.org", "password": "attacker password"}
    )
    assert retry.status_code == 401


def test_linking_and_unlinking_identities(browser: Callable[[], TestClient], http_mock: Any) -> None:
    client = browser()
    google_sign_in(client, http_mock)
    query = start(client, "github", link="1")
    github_routes(http_mock, emails=[{"email": "ada@work.example.org", "primary": True, "verified": True}])
    r = callback(client, "github", code="c", state=query["state"])
    assert r.status_code == 302 and "radar_session=" not in r.headers.get("set-cookie", "")  # no new session
    me = client.get("/api/me").json()["user"]
    assert sorted(i["provider"] for i in me["identities"]) == ["github", "google"]
    assert client.delete("/api/me/identities/github").status_code == 200
    last = client.delete("/api/me/identities/google")
    assert last.status_code == 409  # the only way left to sign in
    assert client.post("/api/me/password", json={"new_password": "now a password"}).status_code == 200
    assert client.delete("/api/me/identities/google").status_code == 200
    # a GitHub account verified for Ada's address cannot be linked to Bob's account
    other = browser()
    sign_up(other, "bob@example.org")
    query = start(other, "github", link="1")
    github_routes(http_mock, user_id=7777)  # primary verified email: ada@example.org
    refused = callback(other, "github", code="c", state=query["state"])
    assert refused.headers["location"] == "/signin?error=identity_in_use"
    # Bob links his own GitHub account (4242, now free); Ada can no longer link it
    query = start(other, "github", link="1")
    github_routes(
        http_mock, user_id=4242, emails=[{"email": "bob@example.org", "primary": True, "verified": True}]
    )
    assert callback(other, "github", code="c", state=query["state"]).headers["location"] == "/"
    query = start(client, "github", link="1")
    github_routes(
        http_mock, user_id=4242, emails=[{"email": "ada@work.example.org", "primary": True, "verified": True}]
    )
    taken = callback(client, "github", code="c", state=query["state"])
    assert taken.headers["location"] == "/signin?error=identity_in_use"
    with session_scope() as s:
        owners = dict(s.execute(select(models.UserIdentity.provider, models.UserIdentity.user_id)).all())
    assert owners == {"github": other.get("/api/me").json()["user"]["id"]}


def test_oauth_respects_the_allow_list(
    browser: Callable[[], TestClient], http_mock: Any, set_env: Callable[..., None]
) -> None:
    set_env(RADAR_ALLOWED_EMAILS="@lab.example.org")
    r = google_sign_in(browser(), http_mock)
    assert r.headers["location"] == "/signin?error=not_allowed"
    with session_scope() as s:
        assert s.scalar(select(func.count()).where(models.User.email.is_not(None))) == 0


# ------------------------------------------------------------------------------------------------
# Public server: email verification gates sessions, the allow-list and admin
# ------------------------------------------------------------------------------------------------


def _user_row(email: str) -> models.User:
    with session_scope() as s:
        user = s.scalar(select(models.User).where(models.User.email == email))
        assert user is not None
        s.expunge(user)
        return user


def _pending_sign_up(
    client: TestClient, email: str, password: str = PASSWORD, **extra: Any
) -> httpx.Response:
    r = client.post("/api/auth/signup", json={"email": email, "password": password, **extra})
    assert r.status_code == 202, r.text
    assert r.json() == {"verification_required": True, "email": email.strip().lower()}
    assert "radar_session" not in r.headers.get("set-cookie", "")
    return r


def test_public_sign_up_waits_for_email_verification(
    public: Callable[[], TestClient], outbox: list[mailer.OutgoingMail]
) -> None:
    config = public().get("/api/auth/config").json()
    assert (config["password_signup"], config["email_verification"]) == (True, True)
    client = public()
    r = _pending_sign_up(client, "Ada@Example.org", next="/projects/3/feed")
    cookie = r.headers["set-cookie"]
    assert "radar_verify=" in cookie and "HttpOnly" in cookie and "Path=/api/auth/email" in cookie
    assert client.get("/api/me").status_code == 401 and client.get("/api/projects").status_code == 401
    row = _user_row("ada@example.org")
    assert (row.email_verified, row.is_admin) == (False, False) and row.id != 1
    (mail,) = outbox
    assert f"{PUBLIC_BASE}/verify?token=" in mail.body and "&next=/projects/3/feed" in mail.body
    token = _token(mail)
    assert token not in cookie  # the cookie proves the browser, it is not the link

    # sign-in: the unverified answer comes only after the right password (no enumeration oracle)
    other = public()
    wrong = other.post("/api/auth/signin", json={"email": "ada@example.org", "password": "wrong password!"})
    unknown = other.post("/api/auth/signin", json={"email": "nobody@example.org", "password": PASSWORD})
    assert [(x.status_code, error_code(x)) for x in (wrong, unknown)] == [(401, "invalid_credentials")] * 2
    pending = other.post("/api/auth/signin", json={"email": "ada@example.org", "password": PASSWORD})
    assert pending.status_code == 403 and error_code(pending) == "email_unverified"
    assert "radar_session" not in pending.headers.get("set-cookie", "")

    verified = client.post("/api/auth/email/verify", json={"token": token})  # the browser that signed up
    assert verified.status_code == 200, verified.text
    me = verified.json()["user"]
    assert (me["email"], me["email_verified"], me["is_admin"]) == ("ada@example.org", True, False)
    assert me["id"] != 1  # not listed in RADAR_ADMIN_EMAILS: no admin, no local data
    assert "radar_session=" in verified.headers["set-cookie"]
    assert client.get("/api/projects").status_code == 200
    again = client.post("/api/auth/email/verify", json={"token": token})
    assert again.status_code == 400 and error_code(again) == "invalid_token"
    assert (
        other.post("/api/auth/signin", json={"email": "ada@example.org", "password": PASSWORD}).status_code
        == 200
    )


def test_public_verification_grants_admin_and_user_one(
    public: Callable[[], TestClient], outbox: list[mailer.OutgoingMail]
) -> None:
    with session_scope(write=True) as s:  # data created in local mode belongs to user 1
        s.add(models.Project(owner_id=1, name="Local project", research_question="q?"))
    early = public()
    _pending_sign_up(early, "early@example.org")
    assert early.post("/api/auth/email/verify", json={"token": _token(outbox[-1])}).json()["user"]["id"] != 1
    boss = public()
    _pending_sign_up(boss, "boss@example.org")
    row = _user_row("boss@example.org")
    assert (row.id, row.is_admin) != (1, True) and row.is_admin is False  # nothing before verification
    r = boss.post("/api/auth/email/verify", json={"token": _token(outbox[-1])})
    me = r.json()["user"]
    assert (me["id"], me["is_admin"], me["email"], me["has_password"]) == (1, True, "boss@example.org", True)
    assert [p["name"] for p in boss.get("/api/projects").json()] == ["Local project"]
    assert boss.get("/api/settings").json()["admin"] is not None
    with session_scope() as s:
        assert s.scalar(select(func.count()).where(models.User.email == "boss@example.org")) == 1
        assert s.scalar(select(func.count()).select_from(models.User)) == 2  # user 1 + early
    signed_in = public().post("/api/auth/signin", json={"email": "boss@example.org", "password": PASSWORD})
    assert signed_in.status_code == 200 and signed_in.json()["user"]["id"] == 1


def test_public_stranger_cannot_take_over_the_admin_email(
    public: Callable[[], TestClient], outbox: list[mailer.OutgoingMail]
) -> None:
    attacker = public()
    _pending_sign_up(attacker, "boss@example.org", password="attacker password")
    row = _user_row("boss@example.org")
    assert (row.is_admin, row.email_verified) == (False, False) and row.id != 1
    blocked = attacker.post(
        "/api/auth/signin", json={"email": "boss@example.org", "password": "attacker password"}
    )
    assert blocked.status_code == 403 and error_code(blocked) == "email_unverified"
    # The confirmation email reaches the real owner, who clicks it in their own browser: the link
    # alone does not verify a password someone else chose.
    token = _token(outbox[-1])
    owner = public()
    asked = owner.post("/api/auth/email/verify", json={"token": token})
    assert asked.status_code == 401 and error_code(asked) == "password_required"
    assert asked.json()["error"]["details"] == {"email": "boss@example.org"}
    guessed = owner.post("/api/auth/email/verify", json={"token": token, "password": "my own password"})
    assert guessed.status_code == 401 and error_code(guessed) == "invalid_credentials"
    assert _user_row("boss@example.org").email_verified is False
    # the owner resets the password instead: the reset link proves the address
    assert owner.post("/api/auth/password/forgot", json={"email": "boss@example.org"}).status_code == 202
    reset = owner.post(
        "/api/auth/password/reset", json={"token": _token(outbox[-1]), "password": "owner password!"}
    )
    assert reset.status_code == 200
    me = reset.json()["user"]
    assert (me["id"], me["is_admin"], me["email_verified"]) == (1, True, True)
    retry = public().post(
        "/api/auth/signin", json={"email": "boss@example.org", "password": "attacker password"}
    )
    assert retry.status_code == 401
    assert public().post("/api/auth/email/verify", json={"token": token}).status_code == 400  # superseded


def test_public_oauth_proves_the_admin_email_and_ends_the_squatter(
    public: Callable[[], TestClient], outbox: list[mailer.OutgoingMail], http_mock: Any
) -> None:
    _pending_sign_up(public(), "boss@example.org", password="attacker password")
    owner = public()
    r = google_sign_in(owner, http_mock, email="boss@example.org")
    assert r.status_code == 302 and r.headers["location"] == "/projects"
    me = owner.get("/api/me").json()["user"]
    assert (me["id"], me["is_admin"], me["has_password"], me["email_verified"]) == (1, True, False, True)
    assert [i["provider"] for i in me["identities"]] == ["google"]
    with session_scope() as s:
        assert (
            s.scalar(select(func.count()).select_from(models.User)) == 1
        )  # the squatter's row moved into user 1


def test_public_allow_list_needs_a_proven_address(
    public: Callable[[], TestClient],
    outbox: list[mailer.OutgoingMail],
    set_env: Callable[..., None],
    http_mock: Any,
) -> None:
    set_env(RADAR_ALLOWED_EMAILS="@lab.example.org")
    refused = public().post("/api/auth/signup", json={"email": "eve@example.org", "password": PASSWORD})
    assert refused.status_code == 403 and error_code(refused) == "not_allowed"
    # typing an allowed address is not enough: no session until the mailbox answers
    squatter = public()
    _pending_sign_up(squatter, "x@lab.example.org")
    assert squatter.get("/api/projects").status_code == 401
    assert (
        squatter.post(
            "/api/auth/signin", json={"email": "x@lab.example.org", "password": PASSWORD}
        ).status_code
        == 403
    )
    member = public()
    _pending_sign_up(member, "ada@lab.example.org")
    verify_token = _token(outbox[-1])
    member.post("/api/auth/password/forgot", json={"email": "ada@lab.example.org"})
    reset_token = _token(outbox[-1])
    # the lab leaves the allow-list: no path ends in a session or a verified account any more
    set_env(RADAR_ALLOWED_EMAILS="@other.example.org")
    for r in (
        member.post("/api/auth/email/verify", json={"token": verify_token}),
        member.post("/api/auth/password/reset", json={"token": reset_token, "password": "a new password!"}),
        member.post("/api/auth/signin", json={"email": "ada@lab.example.org", "password": PASSWORD}),
    ):
        assert r.status_code == 403 and error_code(r) == "not_allowed", r.text
    assert _user_row("ada@lab.example.org").email_verified is False
    assert google_sign_in(public(), http_mock, email="ada@lab.example.org").headers["location"] == (
        "/signin?error=not_allowed"
    )
    set_env(RADAR_ALLOWED_EMAILS="@lab.example.org")
    assert (
        member.post("/api/auth/email/verify", json={"token": verify_token}).status_code == 200
    )  # not consumed


def test_resend_while_signed_out_answers_the_same_for_every_address(
    public: Callable[[], TestClient], outbox: list[mailer.OutgoingMail], http_mock: Any
) -> None:
    _pending_sign_up(public(), "ada@example.org")
    google_sign_in(public(), http_mock, email="carol@example.org")  # verified account
    outbox.clear()
    answers = []
    for email in ("ada@example.org", "nobody@example.org", "carol@example.org"):
        browser_ = public()
        r = browser_.post("/api/auth/email/resend", json={"email": email})
        answers.append(
            (r.status_code, r.content, sorted(c.split("=", 1)[0] for c in r.headers.get_list("set-cookie")))
        )
    assert answers == [(202, b"", ["radar_verify"])] * 3
    assert [m.to for m in outbox] == ["ada@example.org"]  # only the account waiting for verification
    # the resent link works in the browser that asked for it, without the password
    asker = public()
    asker.post("/api/auth/email/resend", json={"email": "ada@example.org", "next": "/dashboard"})
    assert "&next=/dashboard" in outbox[-1].body
    assert asker.post("/api/auth/email/verify", json={"token": _token(outbox[-1])}).status_code == 200
    # per-address limit (5 an hour) applies to unknown addresses as well
    for _ in range(5):
        assert public().post("/api/auth/email/resend", json={"email": "ghost@example.org"}).status_code == 202
    limited = public().post("/api/auth/email/resend", json={"email": "ghost@example.org"})
    assert limited.status_code == 429 and error_code(limited) == "rate_limited"


def test_public_server_without_smtp_closes_password_sign_up(
    public: Callable[[], TestClient], set_env: Callable[..., None]
) -> None:
    from radar.auth import accounts, passwords, tokens

    config = public().get("/api/auth/config").json()
    assert config["providers"]["password"] is True  # existing password accounts still sign in
    assert (config["password_signup"], config["email_verification"], config["email_delivery"]) == (
        False,
        True,
        False,
    )
    closed = public().post("/api/auth/signup", json={"email": "ada@example.org", "password": PASSWORD})
    assert closed.status_code == 409 and error_code(closed) == "not_configured"
    resend = public().post("/api/auth/email/resend", json={"email": "ada@example.org"})
    assert resend.status_code == 409 and error_code(resend) == "not_configured"
    # admin bootstrap without SMTP: the operator invites the listed address and hands over a reset link
    with session_scope(write=True) as s:
        user = accounts.invite(s, "boss@example.org")
        assert (user.is_admin, user.email_verified, user.password_hash) == (False, False, None)
        raw = tokens.issue(s, user.id, tokens.RESET_PASSWORD, "boss@example.org")
    boss = public()
    reset = boss.post("/api/auth/password/reset", json={"token": raw, "password": "owner password!"})
    me = reset.json()["user"]
    assert (me["id"], me["is_admin"], me["email_verified"]) == (1, True, True)
    with session_scope(write=True) as s:  # a verified password account made earlier keeps working
        s.add(
            models.User(
                email="dan@example.org",
                email_verified=True,
                password_hash=passwords.hash_password(PASSWORD),
                preferences_json={},
            )
        )
    assert (
        public().post("/api/auth/signin", json={"email": "dan@example.org", "password": PASSWORD}).status_code
        == 200
    )


def test_cli_invite_link_bootstraps_the_admin_without_smtp(
    public: Callable[[], TestClient], capsys: pytest.CaptureFixture[str]
) -> None:
    from radar import cli

    assert public().get("/api/auth/config").json()["password_signup"] is False  # no SMTP
    assert cli.main(["invite-link", "--email", "boss@example.org"]) == 0
    link = capsys.readouterr().out.strip()
    assert link.startswith(f"{PUBLIC_BASE}/reset?token=")
    token = parse_qs(urlsplit(link).query)["token"][0]
    boss = public()
    reset = boss.post("/api/auth/password/reset", json={"token": token, "password": "owner password!"})
    assert reset.status_code == 200, reset.text
    me = reset.json()["user"]
    assert (me["id"], me["is_admin"], me["email_verified"], me["has_password"]) == (1, True, True, True)
    assert "__Host-radar_session=" in reset.headers["set-cookie"] and "Secure" in reset.headers["set-cookie"]
    assert boss.get("/api/settings").json()["admin"] is not None
    again = public().post("/api/auth/password/reset", json={"token": token, "password": "other password!"})
    assert again.status_code == 400 and error_code(again) == "invalid_token"  # works once
    signed_in = public().post(
        "/api/auth/signin", json={"email": "boss@example.org", "password": "owner password!"}
    )
    assert signed_in.status_code == 200 and signed_in.json()["user"]["id"] == 1


def test_loopback_keeps_immediate_sessions_unless_verification_is_forced(
    browser: Callable[[], TestClient], set_env: Callable[..., None], outbox: list[mailer.OutgoingMail]
) -> None:
    client = browser()
    sign_up(client, "ada@example.org")  # 201 + session, as before
    assert client.get("/api/me").status_code == 200
    set_env(RADAR_REQUIRE_EMAIL_VERIFICATION="1")
    assert browser().get("/api/auth/config").json()["email_verification"] is True
    assert client.get("/api/me").status_code == 401  # an unverified account's session no longer counts
    _pending_sign_up(browser(), "bob@example.org")
    blocked = browser().post("/api/auth/signin", json={"email": "ada@example.org", "password": PASSWORD})
    assert blocked.status_code == 403 and error_code(blocked) == "email_unverified"
    set_env(RADAR_REQUIRE_EMAIL_VERIFICATION="0")
    assert client.get("/api/me").status_code == 200


def test_admin_follows_the_admin_list_at_sign_in(
    browser: Callable[[], TestClient], set_env: Callable[..., None]
) -> None:
    def admin_after_sign_in(email: str) -> bool:
        r = browser().post("/api/auth/signin", json={"email": email, "password": PASSWORD})
        assert r.status_code == 200, r.text
        return bool(r.json()["user"]["is_admin"])

    sign_up(browser(), "ada@example.org")  # empty list: the first account is admin
    sign_up(browser(), "bob@example.org")
    assert (admin_after_sign_in("ada@example.org"), admin_after_sign_in("bob@example.org")) == (True, False)
    set_env(RADAR_ADMIN_EMAILS="carol@example.org")
    # Ada is not listed, but no listed admin exists yet: she keeps admin (never zero admins)
    assert admin_after_sign_in("ada@example.org") is True
    sign_up(browser(), "carol@example.org")
    assert admin_after_sign_in("carol@example.org") is True
    assert admin_after_sign_in("ada@example.org") is False  # revoked now that Carol is an admin
    set_env(RADAR_ADMIN_EMAILS="carol@example.org,bob@example.org")
    assert admin_after_sign_in("bob@example.org") is True
    set_env(RADAR_ADMIN_EMAILS=None)  # empty list: nothing is revoked
    assert (admin_after_sign_in("bob@example.org"), admin_after_sign_in("carol@example.org")) == (True, True)


def test_password_change_is_rate_limited(browser: Callable[[], TestClient]) -> None:
    ada, bob = browser(), browser()
    sign_up(ada, "ada@example.org")
    sign_up(bob, "bob@example.org")
    for _ in range(5):
        r = ada.post(
            "/api/me/password", json={"current_password": "not it at all", "new_password": "a new password"}
        )
        assert r.status_code == 401 and error_code(r) == "invalid_credentials"
    blocked = ada.post(
        "/api/me/password", json={"current_password": PASSWORD, "new_password": "a new password"}
    )
    assert blocked.status_code == 429 and error_code(blocked) == "rate_limited"
    assert int(blocked.headers["retry-after"]) > 0
    ok = bob.post("/api/me/password", json={"current_password": PASSWORD, "new_password": "a new password"})
    assert ok.status_code == 200  # per user


def test_password_hashing_runs_at_most_two_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading
    import time

    from radar.auth import passwords

    lock = threading.Lock()
    release = threading.Event()
    state = {"active": 0, "peak": 0, "done": 0}

    def blocking(result: Any) -> Callable[..., Any]:
        def run(*args: Any) -> Any:
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            release.wait(10)
            with lock:
                state["active"] -= 1
                state["done"] += 1
            return result

        return run

    class FakeHasher:
        hash = staticmethod(blocking("$argon2id$fake"))
        verify = staticmethod(blocking(True))

    monkeypatch.setattr(passwords, "_hasher", FakeHasher())
    monkeypatch.setattr(passwords, "_dummy_hash", None)  # the dummy-hash path is bounded too
    calls: list[Callable[[], Any]] = [
        lambda: passwords.hash_password("one password"),
        lambda: passwords.hash_password("two password"),
        lambda: passwords.verify_password("$argon2id$stored", "three password"),
        lambda: passwords.verify_password(None, "four password"),  # unknown email: dummy hash + verify
        lambda: passwords.verify_password("$argon2id$stored", "five password"),
    ]
    threads = [threading.Thread(target=call, daemon=True) for call in calls]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    while state["active"] < passwords.HASH_CONCURRENCY and time.monotonic() < deadline:
        time.sleep(0.005)
    time.sleep(0.1)  # give the other three threads every chance to (wrongly) start hashing
    assert state["active"] == passwords.HASH_CONCURRENCY == 2
    release.set()
    for thread in threads:
        thread.join(5)
    assert state["peak"] == 2 and state["done"] == 6  # five calls; the unknown email hashed the dummy too


# ------------------------------------------------------------------------------------------------
# Pure helpers
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "public", "required"),
    [
        ({}, False, False),
        ({"RADAR_PUBLIC_URL": "http://localhost:5173"}, False, False),
        ({"RADAR_HOST": "0.0.0.0"}, True, True),
        ({"RADAR_BEHIND_TLS_PROXY": "1"}, True, True),
        (
            {"RADAR_PUBLIC_URL": "https://radar.example.org"},
            True,
            True,
        ),  # loopback bind behind a same-host proxy
        ({"RADAR_REQUIRE_EMAIL_VERIFICATION": "1"}, False, True),
        ({"RADAR_HOST": "0.0.0.0", "RADAR_REQUIRE_EMAIL_VERIFICATION": "0"}, True, False),
        ({"RADAR_REQUIRE_EMAIL_VERIFICATION": ""}, False, False),
    ],
)
def test_public_server_and_verification_settings(
    set_env: Callable[..., None], env: dict[str, str], public: bool, required: bool
) -> None:
    from radar.auth import accounts
    from radar.config import get_settings

    set_env(**{"RADAR_REQUIRE_EMAIL_VERIFICATION": None, **env})
    settings = get_settings()
    assert (settings.is_public, settings.email_verification_required) == (public, required)
    assert accounts.privileges_need_verified_email() is (public or required)


def test_pkce_challenge_rfc7636_vector() -> None:
    assert oauth.pkce_challenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == (
        "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    )


@pytest.mark.parametrize(
    ("raw", "safe"),
    [
        ("/projects/3?tab=deck", "/projects/3?tab=deck"),
        ("//evil.example.org", "/"),
        ("/\\evil.example.org", "/"),
        ("https://evil.example.org", "/"),
        ("/api/me", "/"),
        ("/x\r\nSet-Cookie: a=b", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_safe_next(raw: str | None, safe: str) -> None:
    assert oauth.safe_next(raw) == safe


def test_google_claims_validation() -> None:
    now = datetime.now(UTC)
    good = google_claims("n1")
    assert (
        oauth.google_claims(jwt(good), client_id="google-client", nonce="n1", now=now)["sub"]
        == "google-sub-1"
    )
    assert oauth.google_claims(
        jwt({**good, "aud": ["x", "google-client"]}), client_id="google-client", nonce="n1", now=now
    )
    assert oauth.google_claims(
        jwt({**good, "iss": "accounts.google.com", "email_verified": "true"}),
        client_id="google-client",
        nonce="n1",
        now=now,
    )
    for bad in ("not.a.jwt.at.all", "a.b", jwt({**good, "sub": ""}), "x.!!!.y"):
        with pytest.raises(ValueError):
            oauth.google_claims(bad, client_id="google-client", nonce="n1", now=now)


def test_github_primary_email() -> None:
    assert oauth.github_primary_email([{"email": "B@x.org", "primary": True, "verified": True}]) == "b@x.org"
    assert (
        oauth.github_primary_email(
            [
                {"email": "a@x.org", "primary": True, "verified": False},
                {"email": "c@x.org", "primary": False, "verified": True},
            ]
        )
        == "c@x.org"
    )
    assert oauth.github_primary_email([{"email": "a@x.org", "primary": True, "verified": False}]) is None
    assert oauth.github_primary_email({"message": "Requires authentication"}) is None
