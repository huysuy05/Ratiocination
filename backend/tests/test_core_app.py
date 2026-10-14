"""Core app tests: boot, health, route table, error envelopes and network hardening (SPEC §5, §16)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from radar.config import get_settings
from radar.errors import ApiError

# Every endpoint of docs/API.md v0.3 (method, path). Path parameter names are normalized to "{}".
API_ENDPOINTS: list[tuple[str, str]] = [
    ("GET", "/api/auth/config"),
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/signin"),
    ("POST", "/api/auth/signout"),
    ("GET", "/api/auth/oauth/{provider}/start"),
    ("GET", "/api/auth/oauth/{provider}/callback"),
    ("POST", "/api/auth/password/forgot"),
    ("POST", "/api/auth/password/reset"),
    ("POST", "/api/auth/email/verify"),
    ("POST", "/api/auth/email/resend"),
    ("GET", "/api/me"),
    ("PATCH", "/api/me"),
    ("DELETE", "/api/me"),
    ("POST", "/api/me/password"),
    ("DELETE", "/api/me/identities/{provider}"),
    ("POST", "/api/me/sessions/revoke-others"),
    ("GET", "/api/projects"),
    ("POST", "/api/projects"),
    ("GET", "/api/projects/{id}"),
    ("PATCH", "/api/projects/{id}"),
    ("DELETE", "/api/projects/{id}"),
    ("GET", "/api/projects/{id}/seeds"),
    ("POST", "/api/projects/{id}/seeds"),
    ("DELETE", "/api/projects/{id}/seeds/{paper_id}"),
    ("GET", "/api/projects/{id}/feed"),
    ("POST", "/api/projects/{id}/feed/rebuild"),
    ("POST", "/api/projects/{id}/feedback"),
    ("POST", "/api/projects/{id}/feedback/undo"),
    ("GET", "/api/papers/{id}"),
    ("GET", "/api/papers"),
    ("GET", "/api/search"),
    ("PUT", "/api/papers/{id}/read"),
    ("POST", "/api/papers/{id}/disruption"),
    ("GET", "/api/exports/bibtex"),
    ("GET", "/api/papers/{id}/neighborhood"),
    ("POST", "/api/papers/{id}/expand"),
    ("GET", "/api/projects/{id}/graph"),
    ("GET", "/api/papers/{id}/breakthroughs"),
    ("POST", "/api/papers/{id}/breakthroughs"),
    ("GET", "/api/scholars/{id}"),
    ("GET", "/api/projects/{id}/documents"),
    ("POST", "/api/projects/{id}/documents"),
    ("GET", "/api/documents/{id}/status"),
    ("DELETE", "/api/documents/{id}"),
    ("GET", "/api/dashboard/activity"),
    ("GET", "/api/dashboard/topics"),
    ("GET", "/api/dashboard/findings"),
    ("GET", "/api/dashboard/work"),
    ("POST", "/api/dashboard/summary"),
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/{id}"),
    ("POST", "/api/jobs/run"),
    ("GET", "/api/settings"),
    ("PATCH", "/api/settings"),
    ("POST", "/api/settings/embedding/download"),
    ("GET", "/api/health"),
]

# Declared success status codes (API.md); the rest are 200.
API_STATUS: dict[tuple[str, str], int] = {
    ("POST", "/api/auth/signup"): 201,
    ("POST", "/api/auth/signout"): 204,
    ("GET", "/api/auth/oauth/{provider}/start"): 302,
    ("GET", "/api/auth/oauth/{provider}/callback"): 302,
    ("POST", "/api/auth/password/forgot"): 202,
    ("POST", "/api/auth/email/resend"): 202,
    ("DELETE", "/api/me"): 204,
    ("POST", "/api/projects"): 201,
    ("DELETE", "/api/projects/{id}"): 204,
    ("POST", "/api/projects/{id}/seeds"): 201,
    ("DELETE", "/api/projects/{id}/seeds/{paper_id}"): 204,
    ("POST", "/api/projects/{id}/feed/rebuild"): 202,
    ("POST", "/api/papers/{id}/expand"): 202,
    ("POST", "/api/papers/{id}/breakthroughs"): 202,
    ("POST", "/api/projects/{id}/documents"): 202,
    ("POST", "/api/dashboard/summary"): 202,
    ("POST", "/api/jobs/run"): 202,
    ("POST", "/api/settings/embedding/download"): 202,
}


def _norm(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def _route_contexts(app: object) -> list[object]:
    """Effective routes (FastAPI >= 0.140 nests included routers)."""
    from fastapi import routing

    if hasattr(routing, "iter_route_contexts"):
        return list(routing.iter_route_contexts(app.routes))  # type: ignore[attr-defined]
    return list(app.routes)  # type: ignore[attr-defined]


def _openapi_operations(app: object) -> dict[tuple[str, str], dict]:
    schema = app.openapi()  # type: ignore[attr-defined]
    ops: dict[tuple[str, str], dict] = {}
    for path, item in schema["paths"].items():
        for method, op in item.items():
            ops[(method.upper(), _norm(path))] = op
    return ops


def _envelope(resp: object) -> dict:
    body = resp.json()  # type: ignore[attr-defined]
    assert set(body) == {"error"}
    err = body["error"]
    assert {"code", "message", "request_id"} <= set(err)
    assert err["request_id"] == resp.headers["x-request-id"]  # type: ignore[attr-defined]
    return err


def _fresh_client(**kwargs: object) -> TestClient:
    from radar.main import create_app

    get_settings.cache_clear()
    base_url = str(kwargs.pop("base_url", "http://127.0.0.1:8000"))
    return TestClient(create_app(), base_url=base_url, headers={"X-Radar-Client": "web"}, **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------
# Boot and health
# ------------------------------------------------------------------------------------------------


def test_app_boots_and_health(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
    assert body["db"] == "sqlite"
    assert body["offline"] is True
    assert body["embedder"] == "local/hashing-v1"
    assert body["auth"] == "local"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", body["time"])


def test_importing_main_has_no_side_effects(tmp_path: Path) -> None:
    data_dir = tmp_path / "never-created"
    env = {**os.environ, "RADAR_DATA_DIR": str(data_dir), "RADAR_DATABASE_URL": ""}
    code = "import radar.main as m; assert not hasattr(m, 'app'); print('ok')"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert not data_dir.exists()


def test_every_api_endpoint_is_registered(client: TestClient) -> None:
    ops = _openapi_operations(client.app)
    missing = [(m, p) for m, p in API_ENDPOINTS if (m, _norm(p)) not in ops]
    assert not missing, f"missing endpoints: {missing}"
    for method, path in API_ENDPOINTS:
        expected = str(API_STATUS.get((method, path), 200))
        assert expected in ops[(method, _norm(path))]["responses"], (method, path)
    # nothing undocumented under /api
    documented = {(m, _norm(p)) for m, p in API_ENDPOINTS} | {("GET", "/api/openapi.json")}
    extra = sorted(k for k in ops if k[1].startswith("/api/") and k not in documented)
    assert not extra, f"undocumented endpoints: {extra}"


def test_api_routes_are_sync_handlers(client: TestClient) -> None:
    import inspect

    checked = 0
    for ctx in _route_contexts(client.app):
        path = getattr(ctx, "path", "") or ""
        endpoint = getattr(ctx, "endpoint", None)
        if not path.startswith("/api/") or endpoint is None or path == "/api/openapi.json":
            continue
        checked += 1
        assert not inspect.iscoroutinefunction(endpoint), f"{path} must be a sync def"
    assert checked >= len(API_ENDPOINTS) - 5


def test_openapi_json_served_and_docs_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import radar.main as main_module

    monkeypatch.setattr(main_module, "WEB_DIST_DIR", tmp_path / "no-dist")
    with _fresh_client() as c:
        r = c.get("/api/openapi.json")
        assert r.status_code == 200
        assert "/api/projects" in r.json()["paths"]
        assert c.get("/docs").status_code == 404
        assert c.get("/redoc").status_code == 404


# ------------------------------------------------------------------------------------------------
# Error envelopes
# ------------------------------------------------------------------------------------------------


def test_unknown_api_route_404_envelope(client: TestClient) -> None:
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert _envelope(r)["code"] == "not_found"
    r = client.post("/api/does-not-exist", json={})
    assert r.status_code == 404
    assert _envelope(r)["code"] == "not_found"


def test_wrong_method_405_envelope(client: TestClient) -> None:
    r = client.put("/api/health")
    assert r.status_code == 405
    err = _envelope(r)
    assert err["code"] == "not_found"
    assert "GET" in r.headers.get("allow", "")


def test_422_envelope_has_dotted_paths_and_no_input_values(client: TestClient) -> None:
    secret = "zqcanary-input-value"
    r = client.post(
        "/api/projects",
        json={
            "name": "",
            "research_question": "q",
            "settings": {"slate_size": secret, "arxiv_categories": [f"Bad {secret}"]},
        },
    )
    assert r.status_code == 422
    err = _envelope(r)
    assert err["code"] == "validation_error"
    paths = {f["path"] for f in err["details"]["fields"]}
    assert {"name", "settings.slate_size", "settings.arxiv_categories.0"} <= paths
    assert secret not in r.text
    for field in err["details"]["fields"]:
        assert set(field) == {"path", "message"}


def test_invalid_json_body_is_a_validation_error(client: TestClient) -> None:
    r = client.post("/api/projects", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    err = _envelope(r)
    assert err["code"] == "validation_error"
    assert err["details"]["fields"][0]["path"] == "body"


def test_query_param_validation_paths(client: TestClient) -> None:
    r = client.get("/api/jobs", params={"limit": "0"})
    assert r.status_code == 422
    assert _envelope(r)["details"]["fields"][0]["path"] == "limit"


def test_api_error_and_internal_error_envelopes() -> None:
    from radar.main import create_app

    app = create_app()

    def boom_api() -> None:
        raise ApiError(409, "conflict", "Already running", {"document_id": 7})

    def boom_internal() -> None:
        raise RuntimeError("zq-secret-detail")

    def boom_stub() -> None:
        raise NotImplementedError("owned by zq-agent")

    app.add_api_route("/api/_test/api-error", boom_api, methods=["GET"])
    app.add_api_route("/api/_test/internal", boom_internal, methods=["GET"])
    app.add_api_route("/api/_test/stub", boom_stub, methods=["GET"])
    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        r = c.get("/api/_test/api-error")
        assert r.status_code == 409
        err = _envelope(r)
        assert err == {
            "code": "conflict",
            "message": "Already running",
            "request_id": r.headers["x-request-id"],
            "details": {"document_id": 7},
        }
        r = c.get("/api/_test/internal")
        assert r.status_code == 500
        err = _envelope(r)
        assert err["code"] == "internal_error"
        assert "zq-secret-detail" not in r.text
        assert r.headers["referrer-policy"] == "no-referrer"
        r = c.get("/api/_test/stub")
        assert r.status_code == 501
        assert _envelope(r)["code"] == "not_implemented"
        assert "zq-agent" not in r.text


# ------------------------------------------------------------------------------------------------
# Request id and Referrer-Policy
# ------------------------------------------------------------------------------------------------


def test_request_id_echo_and_generation(client: TestClient) -> None:
    r = client.get("/api/health", headers={"X-Request-ID": "abc-DEF-123"})
    assert r.headers["x-request-id"] == "abc-DEF-123"
    r = client.get("/api/health", headers={"X-Request-ID": "not valid!"})
    assert r.headers["x-request-id"] != "not valid!"
    assert re.fullmatch(r"[A-Za-z0-9-]{1,64}", r.headers["x-request-id"])
    r = client.get("/api/health", headers={"X-Request-ID": "a" * 65})
    assert r.headers["x-request-id"] != "a" * 65


@pytest.mark.parametrize(
    ("method", "path", "headers"),
    [
        ("GET", "/api/health", {}),
        ("GET", "/api/does-not-exist", {}),
        ("POST", "/api/projects", {"X-Radar-Client": ""}),
        ("GET", "/api/health", {"Host": "evil.example"}),
    ],
)
def test_referrer_policy_on_every_response(client: TestClient, method: str, path: str, headers: dict) -> None:
    r = client.request(method, path, headers=headers, json={} if method == "POST" else None)
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["x-request-id"]


# ------------------------------------------------------------------------------------------------
# X-Radar-Client, Origin, Host, token
# ------------------------------------------------------------------------------------------------

NEW_PROJECT = {"name": "Test project", "research_question": "How are synthetic papers ranked?"}


def test_client_header_required_on_non_get(client: TestClient) -> None:
    r = client.post("/api/projects", json=NEW_PROJECT, headers={"X-Radar-Client": ""})
    assert r.status_code == 403
    assert _envelope(r)["code"] == "unauthorized"
    r = client.post("/api/projects", json=NEW_PROJECT, headers={"X-Radar-Client": "other"})
    assert r.status_code == 403
    assert client.post("/api/projects", json=NEW_PROJECT).status_code == 201
    # GET needs no header
    assert client.get("/api/projects", headers={"X-Radar-Client": ""}).status_code == 200


def test_client_header_required_for_delete_and_patch(client: TestClient) -> None:
    pid = client.post("/api/projects", json=NEW_PROJECT).json()["id"]
    assert (
        client.patch(f"/api/projects/{pid}", json={"name": "x"}, headers={"X-Radar-Client": ""}).status_code
        == 403
    )
    assert client.delete(f"/api/projects/{pid}", headers={"X-Radar-Client": ""}).status_code == 403


@pytest.mark.parametrize(
    "origin",
    ["http://127.0.0.1:8000", "http://localhost:8000", "http://localhost:5173", "http://127.0.0.1:5173"],
)
def test_allowed_origins_accepted(client: TestClient, origin: str) -> None:
    r = client.post("/api/projects", json=NEW_PROJECT, headers={"Origin": origin})
    assert r.status_code == 201


@pytest.mark.parametrize(
    "origin",
    ["http://evil.example", "http://127.0.0.1:9999", "https://localhost:8000", "null", "http://localhost"],
)
def test_foreign_origin_rejected(client: TestClient, origin: str) -> None:
    r = client.post("/api/projects", json=NEW_PROJECT, headers={"Origin": origin})
    assert r.status_code == 403
    assert _envelope(r)["code"] == "unauthorized"


def test_same_origin_on_another_port_is_accepted() -> None:
    """A server started on a port other than RADAR_PORT still accepts its own pages' requests."""
    with _fresh_client(base_url="http://127.0.0.1:5199") as c:
        own = c.post("/api/projects", json=NEW_PROJECT, headers={"Origin": "http://127.0.0.1:5199"})
        assert own.status_code == 201
        other_host = c.post("/api/projects", json=NEW_PROJECT, headers={"Origin": "http://localhost:5199"})
        assert other_host.status_code == 403
        other_scheme = c.post("/api/projects", json=NEW_PROJECT, headers={"Origin": "https://127.0.0.1:5199"})
        assert other_scheme.status_code == 403
        foreign = c.post("/api/projects", json=NEW_PROJECT, headers={"Origin": "http://evil.example"})
        assert foreign.status_code == 403


def test_dev_origins_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_DEV_ORIGINS", "http://localhost:5174, http://127.0.0.1:5174/")
    with _fresh_client() as c:
        for origin in ("http://localhost:5174", "http://127.0.0.1:5174"):
            assert c.post("/api/projects", json=NEW_PROJECT, headers={"Origin": origin}).status_code == 201
        assert (
            c.post("/api/projects", json=NEW_PROJECT, headers={"Origin": "http://localhost:5175"}).status_code
            == 403
        )


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.nip.io", "", "[::2]:8000"])
def test_foreign_host_rejected(client: TestClient, host: str) -> None:
    r = client.get("/api/health", headers={"Host": host})
    assert r.status_code == 400
    assert _envelope(r)["code"] == "unauthorized"


@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "LOCALHOST", "[::1]:8000", "127.0.0.1"])
def test_loopback_hosts_accepted(client: TestClient, host: str) -> None:
    assert client.get("/api/health", headers={"Host": host}).status_code == 200


def test_allowed_hosts_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_ALLOWED_HOSTS", "radar.local, 10.0.0.5")
    with _fresh_client() as c:
        assert c.get("/api/health", headers={"Host": "radar.local:8000"}).status_code == 200
        assert c.get("/api/health", headers={"Host": "10.0.0.5"}).status_code == 200
        assert c.get("/api/health", headers={"Host": "other.local"}).status_code == 400


def test_sessions_required_in_accounts_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_AUTH", "accounts")
    with _fresh_client() as c:
        assert c.get("/api/health").status_code == 200  # health needs no session
        assert c.get("/api/auth/config").json()["mode"] == "accounts"
        for method, path in (("GET", "/api/projects"), ("GET", "/api/me"), ("GET", "/api/jobs")):
            r = c.request(method, path)
            assert r.status_code == 401, path
            assert _envelope(r)["code"] == "unauthenticated"
            assert "www-authenticate" not in r.headers


def test_no_session_required_in_local_mode(client: TestClient) -> None:
    assert client.get("/api/projects").status_code == 200
    me = client.get("/api/me").json()
    assert me["mode"] == "local" and me["user"]["id"] == 1 and me["user"]["is_admin"] is True


def test_non_loopback_bind_requires_accounts_admins_and_tls(monkeypatch: pytest.MonkeyPatch) -> None:
    from radar.main import create_app

    monkeypatch.setenv("RADAR_HOST", "0.0.0.0")
    monkeypatch.setenv("RADAR_AUTH", "local")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="RADAR_AUTH"):
        create_app()
    monkeypatch.setenv("RADAR_AUTH", "accounts")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="RADAR_ADMIN_EMAILS"):
        create_app()
    monkeypatch.setenv("RADAR_ADMIN_EMAILS", "admin@example.invalid")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="TLS"):
        create_app()
    monkeypatch.setenv("RADAR_BEHIND_TLS_PROXY", "1")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="https RADAR_PUBLIC_URL"):
        create_app()
    monkeypatch.setenv("RADAR_PUBLIC_URL", "https://radar.example.org")
    get_settings.cache_clear()
    assert create_app() is not None
    monkeypatch.delenv("RADAR_BEHIND_TLS_PROXY")
    monkeypatch.setenv("RADAR_TLS_CERT", "/tmp/cert.pem")
    monkeypatch.setenv("RADAR_TLS_KEY", "/tmp/key.pem")
    get_settings.cache_clear()
    assert create_app() is not None


def test_accounts_mode_needs_a_sign_in_method(monkeypatch: pytest.MonkeyPatch) -> None:
    from radar.main import create_app

    monkeypatch.setenv("RADAR_AUTH", "accounts")
    monkeypatch.setenv("RADAR_PASSWORD_LOGIN", "0")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="sign-in method"):
        create_app()
    monkeypatch.setenv("RADAR_GITHUB_CLIENT_ID", "zq-client")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="sign-in method"):  # an id without a secret is not enough
        create_app()
    monkeypatch.setenv("RADAR_GITHUB_CLIENT_SECRET", "zq-secret")
    get_settings.cache_clear()
    assert create_app() is not None


def test_request_on_remote_interface_refused_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RADAR_ALLOWED_HOSTS", "192.168.1.10")
    with _fresh_client(base_url="http://192.168.1.10:8000") as c:
        r = c.get("/api/health")
        assert r.status_code == 403
        assert _envelope(r)["code"] == "unauthorized"


# ------------------------------------------------------------------------------------------------
# SPA fallback
# ------------------------------------------------------------------------------------------------


def test_spa_served_with_fallback_but_never_for_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import radar.main as main_module

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Ratiocination</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(main_module, "WEB_DIST_DIR", dist)
    with _fresh_client() as c:
        for path in ("/", "/projects/1/feed", "/settings"):
            r = c.get(path)
            assert r.status_code == 200, path
            assert "Ratiocination" in r.text
        r = c.get("/assets/app.js")
        assert r.status_code == 200
        assert "console.log" in r.text
        assert c.get("/assets/missing.js").status_code == 404
        r = c.get("/api/not-a-route")
        assert r.status_code == 404
        assert _envelope(r)["code"] == "not_found"
        assert c.get("/%2e%2e/secret.txt").status_code in (200, 404)
        assert "outside" not in c.get("/%2e%2e/secret.txt").text
        assert c.get("/api/health").json()["status"] == "ok"


def test_no_spa_when_dist_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import radar.main as main_module

    monkeypatch.setattr(main_module, "WEB_DIST_DIR", tmp_path / "nope")
    with _fresh_client() as c:
        r = c.get("/")
        assert r.status_code == 404
        assert _envelope(r)["code"] == "not_found"


def test_register_exception_handlers_alone_gives_envelopes() -> None:
    from fastapi import FastAPI

    from radar.errors import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

    def boom() -> None:
        raise RuntimeError("zq-secret-detail")

    def bad(n: int) -> None:
        return None

    app.add_api_route("/boom", boom, methods=["GET"])
    app.add_api_route("/bad", bad, methods=["GET"])
    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/boom")
        assert r.status_code == 500
        assert r.json()["error"]["code"] == "internal_error"
        assert "zq-secret-detail" not in r.text
        r = c.get("/bad", params={"n": "x"})
        assert r.status_code == 422
        assert r.json()["error"]["details"]["fields"][0]["path"] == "n"
        r = c.get("/nope")
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
