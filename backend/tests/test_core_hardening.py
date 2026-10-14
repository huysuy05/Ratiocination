"""Launch hardening (SPEC §3 startup checks, §16): request body limits, security and cache headers, the
SPA's Content-Security-Policy with inline-script hashes, and the public-server startup matrix."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import tomllib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import MutableHeaders

from radar.config import REPO_ROOT, get_settings

KB = 1024
MB = 1024 * 1024


def _app() -> Any:
    from radar.main import create_app

    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """``env(NAME=value, OTHER=None)`` for this test only (restored afterwards), settings cache cleared."""

    def _set(**values: str | None) -> None:
        for name, value in values.items():
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        get_settings.cache_clear()

    return _set


def _client(app: Any, base_url: str = "http://127.0.0.1:8000") -> TestClient:
    return TestClient(app, base_url=base_url, headers={"X-Radar-Client": "web"})


def _sha256(text: str) -> str:
    return base64.b64encode(hashlib.sha256(text.encode()).digest()).decode()


# ------------------------------------------------------------------------------------------------
# Raw ASGI harness: counts how much of the body the app pulled
# ------------------------------------------------------------------------------------------------


def _raw_request(
    app: Any, method: str, path: str, headers: dict[str, str], chunks: list[bytes]
) -> tuple[list[dict[str, Any]], int]:
    """Run one request through ``app``; returns (sent messages, number of body chunks read)."""
    pending = list(chunks)
    reads = 0
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal reads
        if pending:
            reads += 1
            body = pending.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(pending)}
        await asyncio.sleep(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in {"host": "127.0.0.1:8000", **headers}.items()
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
        "state": {},
    }
    asyncio.run(app(scope, receive, send))
    return sent, reads


def _status_and_body(sent: list[dict[str, Any]]) -> tuple[int, dict[str, str], dict[str, Any]]:
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1, sent  # exactly one response, never the app's own on top of the 413
    headers = {k.decode().lower(): v.decode() for k, v in starts[0]["headers"]}
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return starts[0]["status"], headers, json.loads(body)


# ------------------------------------------------------------------------------------------------
# Request body limits
# ------------------------------------------------------------------------------------------------


def test_oversized_content_length_is_refused_before_the_body_is_read(env: Callable[..., None]) -> None:
    from radar.main import MAX_BODY_BYTES

    env(RADAR_AUTH="accounts")  # the route would answer 401 if authentication ran first
    app = _app()
    headers = {"content-type": "application/json", "x-radar-client": "web", "content-length": str(10 * MB)}
    sent, reads = _raw_request(app, "POST", "/api/projects", headers, [b"{" + b" " * 1000])
    status, out_headers, body = _status_and_body(sent)
    assert (status, body["error"]["code"]) == (413, "payload_too_large")
    assert reads == 0  # nothing of the body was read: no handler, no authentication, no parsing
    assert out_headers["connection"] == "close"
    assert body["error"]["request_id"] == out_headers["x-request-id"]
    assert out_headers["x-content-type-options"] == "nosniff"
    assert "64 KB" in body["error"]["message"] and MAX_BODY_BYTES == 64 * KB

    # an unauthenticated sign-in with a huge declared body gets the 413, not the throttle or a 401
    with _client(app) as c:
        r = c.post(
            "/api/auth/signin",
            content=b"x" * (MAX_BODY_BYTES + 1),
            headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:8000"},
        )
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
        assert r.headers["cache-control"] == "no-store"


def test_chunked_body_is_counted_while_it_streams(env: Callable[..., None]) -> None:
    env(RADAR_AUTH="accounts")
    app = _app()
    headers = {"content-type": "application/json", "x-radar-client": "web", "transfer-encoding": "chunked"}
    chunks = [b" " * (16 * KB) for _ in range(10)]  # 160 KB, no Content-Length
    sent, reads = _raw_request(app, "POST", "/api/auth/signin", headers, chunks)
    status, out_headers, body = _status_and_body(sent)
    assert (status, body["error"]["code"]) == (413, "payload_too_large")
    assert reads == 5  # 5 x 16 KB passes 64 KB: reading stops there
    assert out_headers["connection"] == "close"

    # the same through the test client (httpx sends a generator body chunked)
    with _client(app) as c:
        r = c.post(
            "/api/projects",
            content=(b" " * (16 * KB) for _ in range(10)),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_small_chunked_body_and_the_largest_project_pass(client: TestClient) -> None:
    wide = "\U0001d538"  # 4 bytes in UTF-8: the worst case of what a browser sends
    project = {"name": wide * 200, "research_question": wide * 4000, "description": wide * 4000}
    payload = json.dumps(project, ensure_ascii=False).encode()
    assert 32 * KB < len(payload) < 64 * KB
    r = client.post("/api/projects", content=payload, headers={"Content-Type": "application/json"})
    assert r.status_code == 201, r.text
    chunked = client.post(
        "/api/projects",
        content=(part for part in (b'{"name": "Chunked", ', b'"research_question": "Does it pass?"}')),
        headers={"Content-Type": "application/json"},
    )
    assert chunked.status_code == 201, chunked.text
    assert client.get("/api/projects").status_code == 200  # GET without a body is untouched


def test_upload_route_allows_its_own_limit(
    env: Callable[..., None], make_project: Callable[..., int]
) -> None:
    env(RADAR_MAX_UPLOAD_MB="1")
    app = _app()
    project_id = make_project()
    url = f"/api/projects/{project_id}/documents"
    with _client(app) as c:
        exact = c.post(url, files={"file": ("big.txt", b"x" * (MB - 1) + b"\n", "text/plain")})
        assert exact.status_code == 202, exact.text  # 1 MB file + multipart framing > 64 KB: allowed here
        route = c.post(url, files={"file": ("over.txt", b"y" * (MB + 1), "text/plain")})
        assert route.status_code == 413 and "Files can be at most 1 MB" in route.json()["error"]["message"]
        huge = c.post(url, files={"file": ("huge.txt", b"z" * (2 * MB), "text/plain")})
        assert huge.status_code == 413 and huge.json()["error"]["code"] == "payload_too_large"
        assert "Request body too large" in huge.json()["error"]["message"]  # the middleware, unread
        # the upload limit is for that route only
        other = c.post(
            f"/api/projects/{project_id}/seeds",
            content=b'{"identifier": "' + b"1" * (100 * KB) + b'"}',
            headers={"Content-Type": "application/json"},
        )
        assert other.status_code == 413


def test_anonymous_upload_is_refused_before_its_body_is_read(env: Callable[..., None]) -> None:
    env(
        RADAR_AUTH="accounts", RADAR_PUBLIC_URL="http://127.0.0.1:8000", RADAR_REQUIRE_EMAIL_VERIFICATION=None
    )
    app = _app()
    headers = {
        "content-type": "multipart/form-data; boundary=x",
        "x-radar-client": "web",
        "content-length": str(40 * MB),
    }
    for extra in ({}, {"cookie": "radar_session=forged-token"}):
        sent, reads = _raw_request(
            app, "POST", "/api/projects/1/documents", {**headers, **extra}, [b"--x\r\n" + b"z" * 1000]
        )
        status, out_headers, body = _status_and_body(sent)
        assert (status, body["error"]["code"]) == (401, "unauthenticated")
        assert reads == 0  # nothing spooled: the session is checked before the body
        assert out_headers["connection"] == "close"

    with _client(app) as c:  # a signed-in owner still uploads
        assert (
            c.post(
                "/api/auth/signup", json={"email": "ada@example.org", "password": "correct horse battery"}
            ).status_code
            == 201
        )
        project = c.post("/api/projects", json={"name": "Mine", "research_question": "Does the upload pass?"})
        assert project.status_code == 201, project.text
        r = c.post(
            f"/api/projects/{project.json()['id']}/documents",
            files={"file": ("draft.txt", b"A short draft.\n", "text/plain")},
        )
        assert r.status_code == 202, r.text


# ------------------------------------------------------------------------------------------------
# Security and cache headers
# ------------------------------------------------------------------------------------------------


def test_api_responses_carry_security_headers_and_no_store(
    client: TestClient, make_project: Callable[..., int]
) -> None:
    bibtex = client.get("/api/exports/bibtex", params={"project_id": make_project()})
    assert bibtex.status_code == 200 and "attachment" in bibtex.headers["content-disposition"]
    for r in (
        client.get("/api/health"),
        client.get("/api/does-not-exist"),
        client.post("/api/projects", json={}, headers={"X-Radar-Client": ""}),
        bibtex,  # private library data: not cached either
    ):
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["content-security-policy"] == "frame-ancestors 'none'"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["cache-control"] == "no-store"
        assert "strict-transport-security" not in r.headers  # http public URL


def test_route_cache_control_is_kept() -> None:
    from radar.main import security_headers

    headers = MutableHeaders(raw=[(b"cache-control", b"private, max-age=60"), (b"content-type", b"text/csv")])
    security_headers(headers, "/api/exports/x", hsts=True)
    assert headers["cache-control"] == "private, max-age=60"
    assert headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
    html = MutableHeaders(raw=[(b"content-type", b"text/html; charset=utf-8")])
    security_headers(html, "/somewhere", hsts=False)
    assert html["content-security-policy"].startswith("default-src 'self'; script-src 'self';")
    assert "cache-control" not in html


@pytest.mark.parametrize(
    ("public_url", "hsts"),
    [
        ("https://radar.example.org", True),
        ("https://localhost:8443", False),
        ("http://localhost:8000", False),
    ],
)
def test_hsts_only_for_an_https_public_url(env: Callable[..., None], public_url: str, hsts: bool) -> None:
    env(
        RADAR_AUTH="accounts",
        RADAR_ADMIN_EMAILS="admin@example.org",
        RADAR_BEHIND_TLS_PROXY="1" if public_url.startswith("https") else None,
        RADAR_PUBLIC_URL=public_url,
    )
    with _client(_app(), base_url=public_url) as c:
        r = c.get("/api/health")
        assert r.status_code == 200
        assert r.headers.get("strict-transport-security") == (
            "max-age=31536000; includeSubDomains" if hsts else None
        )


THEME_SCRIPT = (
    '\n      (function () { document.documentElement.setAttribute("data-theme", "dark"); })();\n    '
)


@pytest.fixture
def dist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    import radar.main as main_module

    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        "<!doctype html><html><head><title>Ratiocination</title>"
        f"<script>{THEME_SCRIPT}</script>"
        '<script type="module" crossorigin src="/assets/index-AbC123.js"></script>'
        "</head><body><div id=root></div></body></html>",
        encoding="utf-8",
    )
    (root / "assets" / "index-AbC123.js").write_text("console.log('ok')", encoding="utf-8")
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    monkeypatch.setattr(main_module, "WEB_DIST_DIR", root)
    main_module._html_pages.clear()
    yield root
    main_module._html_pages.clear()


def test_spa_csp_hashes_the_inline_script(dist: Path) -> None:
    with _client(_app()) as c:
        for path in ("/", "/projects/1/feed", "/index.html"):
            r = c.get(path)
            assert r.status_code == 200 and "Ratiocination" in r.text, path
            csp = r.headers["content-security-policy"]
            directives = dict(d.strip().split(" ", 1) for d in csp.split(";"))
            assert directives["script-src"] == f"'self' 'sha256-{_sha256(THEME_SCRIPT)}'"
            assert "unsafe-inline" not in directives["script-src"]
            assert directives["default-src"] == "'self'"
            assert directives["object-src"] == "'none'" and directives["base-uri"] == "'self'"
            assert directives["frame-ancestors"] == "'none'" and directives["form-action"] == "'self'"
            assert r.headers["cache-control"] == "no-cache"
            assert r.headers["x-frame-options"] == "DENY"
        asset = c.get("/assets/index-AbC123.js")
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert asset.headers["content-security-policy"] == "frame-ancestors 'none'"
        missing = c.get("/assets/index-Missing.js")
        assert missing.status_code == 404 and "immutable" not in missing.headers.get("cache-control", "")
        icon = c.get("/favicon.svg")
        assert icon.status_code == 200 and "immutable" not in icon.headers.get("cache-control", "")

        # a rebuilt index.html is picked up without a restart
        newer = "\n      window.rebuilt = true;\n    "
        (dist / "index.html").write_text(
            f"<!doctype html><html><head><script>{newer}</script></head><body>rebuilt page</body></html>",
            encoding="utf-8",
        )
        r = c.get("/")
        assert "rebuilt page" in r.text
        assert f"'sha256-{_sha256(newer)}'" in r.headers["content-security-policy"]
        assert _sha256(THEME_SCRIPT) not in r.headers["content-security-policy"]


def test_inline_script_hashes() -> None:
    from radar.main import inline_script_hashes, spa_csp

    html = (
        b"<script>a()</script><SCRIPT type='text/javascript'>b()\r\nc()</SCRIPT>"
        b'<script src="/x.js"></script><script type="module" src=/y.js>ignored()</script><script> </script>'
    )
    assert inline_script_hashes(html) == sorted({_sha256("a()"), _sha256("b()\nc()")})
    assert "'sha256-" not in spa_csp()


def test_real_web_build_has_exactly_its_inline_scripts_hashed() -> None:
    from radar.config import WEB_DIST_DIR
    from radar.main import inline_script_hashes

    index = Path(WEB_DIST_DIR) / "index.html"
    if not index.is_file():
        pytest.skip("web/dist is not built")
    html = index.read_text(encoding="utf-8")
    inline = [
        body
        for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I)
        if "src=" not in attrs and body.strip()
    ]
    assert inline_script_hashes(index.read_bytes()) == sorted({_sha256(b) for b in inline})
    # no inline event handler attributes: they would need 'unsafe-hashes'
    assert re.search(r"<[^>]*\son[a-z]+\s*=", html, re.I) is None


# ------------------------------------------------------------------------------------------------
# Startup checks for a public server
# ------------------------------------------------------------------------------------------------

ADMIN = {"RADAR_ADMIN_EMAILS": "admin@example.org"}
PROXY = {"RADAR_BEHIND_TLS_PROXY": "1"}


@pytest.mark.parametrize(
    ("settings_env", "error"),
    [
        ({}, None),  # local mode on loopback (the e2e and single-user setup)
        ({"RADAR_AUTH": "accounts"}, None),  # accounts on loopback (developer)
        ({"RADAR_AUTH": "accounts", "RADAR_PUBLIC_URL": "http://localhost:5173"}, None),  # Vite
        ({"RADAR_AUTH": "accounts", "RADAR_PUBLIC_URL": "http://127.0.0.1:8000"}, None),
        # loopback bind behind a TLS proxy on the same host: the same rules as a public bind
        ({**PROXY}, "RADAR_AUTH=accounts"),
        ({"RADAR_AUTH": "accounts", **PROXY}, "RADAR_ADMIN_EMAILS"),
        ({"RADAR_AUTH": "accounts", **PROXY, **ADMIN}, "https RADAR_PUBLIC_URL"),
        (
            {"RADAR_AUTH": "accounts", **PROXY, **ADMIN, "RADAR_PUBLIC_URL": "http://radar.example.org"},
            "https RADAR_PUBLIC_URL",
        ),
        ({"RADAR_AUTH": "accounts", **PROXY, **ADMIN, "RADAR_PUBLIC_URL": "https://radar.example.org"}, None),
        # loopback bind, a public host name in RADAR_PUBLIC_URL (a same-host reverse proxy)
        ({"RADAR_PUBLIC_URL": "https://radar.example.org"}, "RADAR_AUTH=accounts"),
        ({"RADAR_AUTH": "accounts", **ADMIN, "RADAR_PUBLIC_URL": "https://radar.example.org"}, "TLS"),
        ({"RADAR_AUTH": "accounts", **ADMIN, **PROXY, "RADAR_PUBLIC_URL": "https://radar.example.org"}, None),
        # non-loopback bind
        ({"RADAR_HOST": "0.0.0.0", "RADAR_AUTH": "accounts", **ADMIN, **PROXY}, "https RADAR_PUBLIC_URL"),
        (
            {
                "RADAR_HOST": "0.0.0.0",
                "RADAR_AUTH": "accounts",
                **ADMIN,
                "RADAR_TLS_CERT": "/tmp/cert.pem",
                "RADAR_TLS_KEY": "/tmp/key.pem",
                "RADAR_PUBLIC_URL": "https://radar.example.org",
            },
            None,
        ),
    ],
)
def test_public_server_startup_matrix(
    env: Callable[..., None], settings_env: dict[str, str], error: str | None
) -> None:
    from radar.main import create_app

    env(**settings_env)
    if error is None:
        assert create_app() is not None
    else:
        with pytest.raises(RuntimeError, match=re.escape(error)):
            create_app()


def test_fly_toml_env_passes_the_startup_checks(env: Callable[..., None]) -> None:
    from radar.main import create_app

    fly = tomllib.loads((REPO_ROOT / "fly.toml").read_text(encoding="utf-8"))
    values = {k: str(v) for k, v in fly["env"].items() if k != "RADAR_DATA_DIR"}  # keep the test's data dir
    assert values["RADAR_PUBLIC_URL"].startswith("https://") and values["RADAR_BEHIND_TLS_PROXY"] == "1"
    env(**{**values, "RADAR_SCHEDULER": "0"})
    with pytest.raises(RuntimeError, match="RADAR_ADMIN_EMAILS"):  # a secret on Fly, not in fly.toml
        create_app()
    env(RADAR_ADMIN_EMAILS="admin@example.org")
    app = create_app()
    public = values["RADAR_PUBLIC_URL"]
    with TestClient(app, base_url=public, headers={"X-Radar-Client": "web"}) as c:
        r = c.get("/api/health", headers={"Fly-Client-IP": "203.0.113.9"})
        assert r.status_code == 200
        assert r.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"
        assert c.get("/api/health", headers={"Host": "localhost"}).status_code == 200  # Fly's health check
