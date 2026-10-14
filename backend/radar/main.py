"""Application factory (SPEC §15 lifespan, §16 network hardening and response headers).

Run with ``uvicorn radar.main:create_app --factory`` (one worker). There is deliberately no
module-level ``app``: importing this module never touches the data directory.

Middleware, outermost first:

0. client IP (only with ``RADAR_CLIENT_IP_HEADER``) - the proxy's client-IP header (Fly.io:
   ``Fly-Client-IP``) replaces the socket peer as ``scope["client"]``;
1. request context - validated ``X-Request-ID`` (``^[A-Za-z0-9-]{1,64}$`` else generated), echoed on
   every response together with the security headers (:func:`security_headers`: ``Referrer-Policy:
   no-referrer``, ``X-Content-Type-Options: nosniff``, ``X-Frame-Options: DENY``, a
   Content-Security-Policy, HSTS when ``RADAR_PUBLIC_URL`` is https, ``Cache-Control: no-store`` on
   ``/api``); requests that arrive on a non-loopback interface are refused in ``RADAR_AUTH=local``
   mode; unhandled exceptions become a 500 ``internal_error`` envelope (traceback logged with the
   request id only);
2. body size limit - 413 ``payload_too_large`` when a request body is larger than its route allows
   (:data:`MAX_BODY_BYTES` for JSON, ``RADAR_MAX_UPLOAD_MB`` + :data:`MULTIPART_OVERHEAD_BYTES` for
   document uploads): a too large ``Content-Length`` is refused before anything is read, a body
   without one (chunked) is counted while it streams. FastAPI reads the whole body before the route's
   dependencies run, so this is what stops an anonymous client from making the server buffer or spool
   large bodies;
3. trusted hosts - Host must be 127.0.0.1, localhost, [::1], RADAR_HOST, RADAR_ALLOWED_HOSTS or the
   host of RADAR_PUBLIC_URL;
4. client guard - every non-GET ``/api`` request needs ``X-Radar-Client: web`` and, when an ``Origin``
   header is present, an allowed origin (RADAR_PUBLIC_URL, own origin on the configured port, or the
   Vite dev origin). With cookie sessions this is the CSRF defense (SPEC §16).

Authentication itself is a route dependency (``radar.api.deps``), not middleware (SPEC §5.9).
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import HTTPConnection
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from radar import __version__
from radar.config import VITE_DEV_PORT, WEB_DIST_DIR, Settings, get_settings, is_loopback
from radar.errors import (
    REQUEST_ID_HEADER,
    error_response,
    http_exception_handler,
    internal_error_response,
    register_exception_handlers,
)
from radar.logging_setup import configure_logging

log = logging.getLogger(__name__)

REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
CLIENT_HEADER = "x-radar-client"
CLIENT_HEADER_VALUE = "web"
SAFE_METHODS = frozenset({"GET", "HEAD"})

# Request bodies (SPEC §16). The largest JSON request is a project with its name (200 characters),
# research question and description (4 000 each): ~8 KB in ASCII, ~33 KB when every character takes
# 4 bytes in UTF-8. 64 KiB leaves about twice that as headroom.
MAX_BODY_BYTES = 64 * 1024
# Multipart framing around an uploaded file (boundaries, part headers, the filename) on top of
# RADAR_MAX_UPLOAD_MB; the route itself still refuses a file above RADAR_MAX_UPLOAD_MB.
MULTIPART_OVERHEAD_BYTES = 64 * 1024
UPLOAD_PATH_RE = re.compile(r"^/api/projects/[^/]+/documents/?$")

# Response headers (SPEC §16).
HSTS_VALUE = "max-age=31536000; includeSubDomains"
API_CACHE_CONTROL = "no-store"
ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"  # /assets/*: content-hashed file names
HTML_CACHE_CONTROL = "no-cache"
FRAME_ANCESTORS_CSP = "frame-ancestors 'none'"

request_id_var: ContextVar[str | None] = ContextVar("radar_request_id", default=None)


def current_request_id() -> str | None:
    """The id of the request being handled (``None`` outside a request)."""
    return request_id_var.get()


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _scope_request_id(scope: Scope) -> str:
    state = scope.get("state")
    rid = state.get("request_id") if isinstance(state, dict) else None
    return str(rid) if rid else uuid.uuid4().hex


async def _send_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    status: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> None:
    response = error_response(status, code, message, _scope_request_id(scope), headers=headers)
    await response(scope, receive, send)


# ------------------------------------------------------------------------------------------------
# Middleware (pure ASGI)
# ------------------------------------------------------------------------------------------------


def spa_csp(script_hashes: Iterable[str] = ()) -> str:
    """The Content-Security-Policy of an HTML page: only same-origin code plus the page's own inline
    scripts by SHA-256 (``script_hashes``: base64 digests). Styles allow ``'unsafe-inline'`` because
    libraries insert ``<style>`` elements at run time (sonner) and React/Motion set style attributes.
    OAuth sign-in is a plain link (a navigation), so ``form-action`` stays ``'self'``."""
    script_src = " ".join(["'self'", *(f"'sha256-{h}'" for h in sorted(set(script_hashes)))])
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {script_src}",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "media-src 'self'",
            "font-src 'self'",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            FRAME_ANCESTORS_CSP,
        ]
    )


_INLINE_SCRIPT_RE = re.compile(rb"<script\b([^>]*)>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_SRC_ATTR_RE = re.compile(rb"\bsrc\s*=", re.IGNORECASE)


def inline_script_hashes(html: bytes) -> list[str]:
    """Base64 SHA-256 digests of the inline ``<script>`` elements of ``html`` (the CSP hash source).

    The browser hashes the script text after HTML input preprocessing (CR LF and CR become LF), so
    the same normalization happens here. Scripts with ``src`` are covered by ``'self'``.
    """
    digests: set[str] = set()
    for attrs, body in _INLINE_SCRIPT_RE.findall(html):
        if _SRC_ATTR_RE.search(attrs) or not body.strip():
            continue
        text = body.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        digests.add(base64.b64encode(hashlib.sha256(text).digest()).decode("ascii"))
    return sorted(digests)


def hsts_enabled(settings: Settings) -> bool:
    """HSTS when the public URL is https and not a loopback host (a developer's https://localhost
    would otherwise pin plain-http localhost out of their browser for a year)."""
    host = urlsplit(settings.public_origin).hostname or ""
    return settings.cookie_secure and not is_loopback(host)


def security_headers(headers: MutableHeaders, path: str, *, hsts: bool) -> None:
    """Add the security headers every response carries (SPEC §16).

    An HTML response without its own Content-Security-Policy gets :func:`spa_csp` without hashes
    (the SPA's ``index.html`` sets its own, with the hashes of its inline scripts); anything else gets
    ``frame-ancestors 'none'``. ``/api`` responses get ``Cache-Control: no-store`` unless the route set
    a Cache-Control itself.
    """
    headers["Referrer-Policy"] = "no-referrer"
    headers["X-Content-Type-Options"] = "nosniff"
    headers["X-Frame-Options"] = "DENY"
    if "content-security-policy" not in headers:
        is_html = headers.get("content-type", "").lower().startswith("text/html")
        headers["Content-Security-Policy"] = spa_csp() if is_html else FRAME_ANCESTORS_CSP
    if hsts:
        headers["Strict-Transport-Security"] = HSTS_VALUE
    if is_api_path(path) and "cache-control" not in headers:
        headers["Cache-Control"] = API_CACHE_CONTROL


class RequestContextMiddleware:
    """Request id, security headers on every response, remote-access refusal and the 500 catch-all."""

    def __init__(self, app: ASGIApp, *, accounts_mode: bool, hsts: bool = False) -> None:
        self.app = app
        self.accounts_mode = accounts_mode
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id")
        rid = incoming if incoming and REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        if isinstance(state, dict):
            state["request_id"] = rid
        token = request_id_var.set(rid)
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = MutableHeaders(scope=message)
                headers[REQUEST_ID_HEADER] = rid
                security_headers(headers, scope["path"], hsts=self.hsts)
            await send(message)

        try:
            if not self.accounts_mode and _arrived_on_remote_interface(scope):
                await _send_error(
                    scope,
                    receive,
                    send_wrapper,
                    403,
                    "unauthorized",
                    "Remote access requires RADAR_AUTH=accounts",
                )
                return
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled error request_id=%s", rid)
            if response_started:
                raise
            await internal_error_response(rid)(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)


class ClientIpHeaderMiddleware:
    """Use a header set by the TLS proxy (``RADAR_CLIENT_IP_HEADER``, e.g. Fly.io's ``Fly-Client-IP``) as
    the request's client address, so the per-IP sign-in throttle (SPEC §5.4) and the access log see the
    real client instead of the proxy.

    The last occurrence of the header wins (a proxy adds its value after anything the client sent); a
    value that is not an IP address leaves the socket peer in place. Enable it only when every request
    reaches the app through a proxy that sets the header.
    """

    def __init__(self, app: ASGIApp, header: str) -> None:
        self.app = app
        self.header = header.strip().lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            raw: bytes | None = None
            for name, value in scope.get("headers") or ():
                if name == self.header:
                    raw = value
            ip = _ip_or_none(raw.decode("latin-1")) if raw is not None else None
            if ip is not None:
                client = scope.get("client")
                scope["client"] = (ip, client[1] if client else 0)
        await self.app(scope, receive, send)


class BodyTooLarge(StarletteHTTPException):
    """Raised from the wrapped ``receive`` once a streamed body passes its limit. An HTTPException so
    FastAPI's body parsing re-raises it instead of turning it into a 400."""

    def __init__(self) -> None:
        super().__init__(413, headers={"Connection": "close"})


class BodySizeLimitMiddleware:
    """413 ``payload_too_large`` for request bodies above the route's limit (pure ASGI, SPEC §16).

    ``upload_limit`` applies to ``POST /api/projects/{id}/documents`` (multipart), ``default_limit`` to
    everything else. A ``Content-Length`` above the limit is answered at once, without reading the body
    (or sending ``100 Continue``); a body without one is counted as it arrives and the request is
    answered with the 413 as soon as it passes the limit (the app's own response, if any, is dropped).
    The 413 closes the connection so the rest of the body is never read.

    An upload without a live session is answered 401 before any of its body is read: the upload limit
    is large (``RADAR_MAX_UPLOAD_MB``), and FastAPI would otherwise spool the whole multipart body to a
    temporary file before ``current_user`` runs, which lets anonymous clients fill the disk.
    """

    def __init__(self, app: ASGIApp, *, default_limit: int, upload_limit: int) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit

    @staticmethod
    def is_upload(scope: Scope) -> bool:
        return scope["method"] == "POST" and UPLOAD_PATH_RE.fullmatch(scope["path"]) is not None

    def limit_for(self, scope: Scope) -> int:
        return self.upload_limit if self.is_upload(scope) else self.default_limit

    @staticmethod
    def _signed_in(scope: Scope) -> bool:
        """Whether the request carries a live session (the check ``current_user`` makes; local mode: yes)."""
        from radar.auth import sessions as auth_sessions

        settings = get_settings()
        if settings.auth == "local":
            return True
        token = HTTPConnection(scope).cookies.get(auth_sessions.cookie_name(settings))
        return token is not None and auth_sessions.resolve(token) is not None

    @staticmethod
    def _message(limit: int) -> str:
        if limit >= 1024 * 1024:
            return f"Request body too large (at most {limit // (1024 * 1024)} MB)"
        return f"Request body too large (at most {limit // 1024} KB)"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if self.is_upload(scope) and not await anyio.to_thread.run_sync(self._signed_in, scope):
            await _send_error(
                scope, receive, send, 401, "unauthenticated", "Sign in to continue", {"Connection": "close"}
            )
            return
        limit = self.limit_for(scope)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.strip().isdigit() and int(declared) > limit:
            await _send_error(
                scope, receive, send, 413, "payload_too_large", self._message(limit), {"Connection": "close"}
            )
            return

        received = 0
        started = False  # a response (the app's or the 413) has started
        rejected = False  # the 413 was sent from here; the app's own response is dropped

        async def limited_receive() -> Message:
            nonlocal received, started, rejected
            message = await receive()
            if message["type"] == "http.request" and not rejected:
                received += len(message.get("body", b""))
                if received > limit:
                    if not started:
                        rejected = started = True
                        await _send_error(
                            scope,
                            receive,
                            send,
                            413,
                            "payload_too_large",
                            self._message(limit),
                            {"Connection": "close"},
                        )
                    raise BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if rejected:
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except BodyTooLarge:
            if not rejected:
                raise


def _ip_or_none(value: str) -> str | None:
    text = value.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        return None


def _arrived_on_remote_interface(scope: Scope) -> bool:
    server = scope.get("server")
    if not server:
        return False
    host = str(server[0])
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False  # not an IP (unix socket, test transport): nothing to check
    return not is_loopback(host)


_HOST_RE = re.compile(r"^(?P<host>\[[0-9A-Fa-f:.]+\]|[^:\[\]/\s]+)(?::(?P<port>\d{1,5}))?$")


def _parse_host(value: str | None) -> str | None:
    if not value:
        return None
    match = _HOST_RE.fullmatch(value.strip())
    if match is None:
        return None
    return match["host"].lower()


class TrustedHostGuard(TrustedHostMiddleware):
    """Starlette's TrustedHostMiddleware semantics (exact host match, IPv6 literals in brackets),
    but a rejected Host gets the standard error envelope (400 ``unauthorized``)."""

    def __init__(self, app: ASGIApp, allowed_hosts: list[str]) -> None:
        super().__init__(app, allowed_hosts=[h.lower() for h in allowed_hosts], www_redirect=False)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        host = _parse_host(Headers(scope=scope).get("host"))
        if host is not None and host in self.allowed_hosts:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":  # pragma: no cover - no websockets in this app
            await send({"type": "websocket.close", "code": 1008})
            return
        await _send_error(scope, receive, send, 400, "unauthorized", "Invalid host header")


class ClientGuard:
    """Non-GET /api requests need ``X-Radar-Client: web`` and, if present, an allowed ``Origin``.

    Allowed: the configured origins (``allowed_origins``) and the request's own origin — its scheme
    plus the ``Host`` it was sent to (also ``https`` behind a TLS proxy). The Host was already checked
    by the TrustedHost guard, so a page on another site still fails; this keeps same-origin requests
    working when the server listens on a port other than ``RADAR_PORT``.
    """

    def __init__(self, app: ASGIApp, allowed_origins: set[str], *, behind_tls_proxy: bool = False) -> None:
        self.app = app
        self.allowed_origins = {o.lower().rstrip("/") for o in allowed_origins}
        self.behind_tls_proxy = behind_tls_proxy

    def _own_origins(self, scope: Scope, headers: Headers) -> set[str]:
        host = headers.get("host", "").strip().lower()
        if not host:
            return set()
        own = {f"{scope.get('scheme', 'http')}://{host}"}
        if self.behind_tls_proxy:
            own.add(f"https://{host}")
        return own

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and is_api_path(scope["path"]) and scope["method"] not in SAFE_METHODS:
            headers = Headers(scope=scope)
            if headers.get(CLIENT_HEADER, "").strip().lower() != CLIENT_HEADER_VALUE:
                await _send_error(
                    scope, receive, send, 403, "unauthorized", "Missing or invalid X-Radar-Client header"
                )
                return
            origin = headers.get("origin")
            if origin is not None:
                value = origin.strip().lower().rstrip("/")
                if value not in self.allowed_origins and value not in self._own_origins(scope, headers):
                    await _send_error(scope, receive, send, 403, "unauthorized", "Origin not allowed")
                    return
        await self.app(scope, receive, send)


def allowed_origins(settings: Settings) -> set[str]:
    """RADAR_PUBLIC_URL, own origins on the configured port, the Vite dev origins and RADAR_DEV_ORIGINS."""
    port = settings.port
    origins = {f"http://localhost:{VITE_DEV_PORT}", f"http://127.0.0.1:{VITE_DEV_PORT}"}
    origins.add(settings.public_origin.lower())
    schemes = ("http", "https") if settings.tls_configured else ("http",)
    for host in settings.trusted_hosts:
        if ":" in host and not host.startswith("["):
            continue  # bare IPv6 literal; the bracketed form is also in the list
        for scheme in schemes:
            origins.add(f"{scheme}://{host}:{port}")
            if (scheme, port) in {("http", 80), ("https", 443)}:
                origins.add(f"{scheme}://{host}")
        if settings.behind_tls_proxy:
            origins.add(f"https://{host}")
    origins.update(o.strip().lower().rstrip("/") for o in settings.dev_origins if o.strip())
    return origins


# ------------------------------------------------------------------------------------------------
# Lifespan, routers, SPA
# ------------------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """SPEC §16: init_db -> recover_interrupted -> runner.start -> scheduler.start_if_enabled;
    shutdown in reverse, then the background writer."""
    from radar import db
    from radar.jobs import runner, scheduler

    db.init_db()
    recovered = runner.recover_interrupted()
    if recovered:
        log.info("marked %s interrupted jobs as failed", recovered)
    runner.start()
    scheduler.start_if_enabled()
    try:
        yield
    finally:
        try:
            scheduler.shutdown()
        finally:
            try:
                runner.shutdown()
            finally:
                db.shutdown_writer()


def _routers() -> list[tuple[Any, bool]]:
    """``(router, needs_session)`` pairs; protected routers get ``Depends(current_user)`` (SPEC §5.9)."""
    from radar.api import (
        auth,
        dashboard,
        documents,
        exports,
        feed,
        graph,
        health,
        jobs,
        me,
        papers,
        projects,
        seeds,
    )
    from radar.api import settings as settings_api

    return [
        (health.router, False),
        (auth.router, False),
        (me.router, False),  # GET /api/me answers 401 itself; every route declares the user
        (projects.router, True),
        (seeds.router, True),
        (feed.router, True),
        (papers.router, True),
        (exports.router, True),
        (settings_api.router, True),
        (jobs.router, True),
        (graph.router, True),
        (documents.router, True),
        (dashboard.router, True),
    ]


def _spa_dir() -> Path | None:
    dist = Path(WEB_DIST_DIR)
    return dist.resolve() if (dist / "index.html").is_file() else None


_html_pages: dict[Path, tuple[int, int, bytes, str]] = {}


def html_page(path: Path) -> tuple[bytes, str]:
    """``(content, Content-Security-Policy)`` of a built HTML page, re-read when the file changes.

    The policy carries the SHA-256 of the page's inline scripts (the pre-paint theme script of
    ``index.html``), computed from the exact bytes that are served, so a rebuilt SPA never needs a
    policy edit and a stale hash cannot block the script.
    """
    stat = path.stat()
    cached = _html_pages.get(path)
    if cached is not None and cached[:2] == (stat.st_mtime_ns, stat.st_size):
        return cached[2], cached[3]
    content = path.read_bytes()
    policy = spa_csp(inline_script_hashes(content))
    _html_pages[path] = (stat.st_mtime_ns, stat.st_size, content, policy)
    return content, policy


def _html_response(page: Path) -> Response:
    content, policy = html_page(page)
    return Response(
        content,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": HTML_CACHE_CONTROL, "Content-Security-Policy": policy},
    )


def _spa_response(dist: Path, path: str) -> Response | None:
    rel = path.lstrip("/")
    if rel:
        candidate = (dist / rel).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file():
            if candidate.suffix.lower() == ".html":
                return _html_response(candidate)
            if candidate.is_relative_to(dist / "assets"):
                return FileResponse(candidate, headers={"Cache-Control": ASSET_CACHE_CONTROL})
            return FileResponse(candidate)
        if "." in rel.rsplit("/", 1)[-1]:
            return None  # a missing asset is a real 404
    return _html_response(dist / "index.html")


def _make_not_found_handler(dist: Path) -> Callable[[Request, Exception], Awaitable[Response]]:
    async def handler(request: Request, exc: Exception) -> Response:
        if (
            isinstance(exc, StarletteHTTPException)
            and exc.status_code == 404
            and request.method in SAFE_METHODS
            and not is_api_path(request.scope["path"])
        ):
            response = _spa_response(dist, request.scope["path"])
            if response is not None:
                return response
        return await http_exception_handler(request, exc)

    return handler


def _check_bind(settings: Settings) -> None:
    """Startup checks (SPEC §3). A public server (``settings.is_public``: a non-loopback bind, a TLS
    proxy in front, or a ``RADAR_PUBLIC_URL`` host that is not loopback, e.g. a loopback bind behind a
    same-host proxy) needs accounts, ``RADAR_ADMIN_EMAILS``, TLS and an https ``RADAR_PUBLIC_URL``."""
    if settings.auth == "accounts" and not (
        settings.password_enabled or settings.google_enabled or settings.github_enabled
    ):
        raise RuntimeError(
            "Refusing to start: RADAR_AUTH=accounts needs a sign-in method (RADAR_PASSWORD_LOGIN=1 or "
            "a complete Google/GitHub client id + secret)"
        )
    if not settings.is_public:
        return
    if not settings.bind_is_loopback:
        what = "a non-loopback RADAR_HOST"
    elif settings.behind_tls_proxy:
        what = "a public server (RADAR_BEHIND_TLS_PROXY=1)"
    else:
        what = "a public server (RADAR_PUBLIC_URL is not a localhost address)"
    if settings.auth != "accounts":
        raise RuntimeError(
            f"Refusing to start: {what} requires RADAR_AUTH=accounts (local mode is loopback only)"
        )
    if not settings.admin_emails:
        raise RuntimeError(
            f"Refusing to start: {what} requires RADAR_ADMIN_EMAILS (otherwise the first stranger to "
            "sign up would become admin)"
        )
    if not settings.tls_configured:
        raise RuntimeError(
            f"Refusing to start: {what} requires RADAR_TLS_CERT and RADAR_TLS_KEY or RADAR_BEHIND_TLS_PROXY=1"
        )
    if not settings.public_url.lower().startswith("https://"):
        raise RuntimeError(
            f"Refusing to start: {what} requires an https RADAR_PUBLIC_URL such as https://radar.example.org "
            "(Secure session cookies, OAuth redirect URIs and email links are built from it)"
        )


def create_app() -> FastAPI:
    """Build the FastAPI application (uvicorn: ``radar.main:create_app --factory``)."""
    settings = get_settings()
    _check_bind(settings)
    configure_logging(settings)

    app = FastAPI(
        title="Ratiocination",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    register_exception_handlers(app)
    dist = _spa_dir()
    if dist is not None:
        app.add_exception_handler(StarletteHTTPException, _make_not_found_handler(dist))
        _, policy = html_page(dist / "index.html")  # read once at startup; re-read if the file changes
        log.info("serving the web build; %d inline script hash(es) in its CSP", policy.count("'sha256-"))
    else:
        log.info("no web build at %s; serving the API only", WEB_DIST_DIR)

    from fastapi import Depends

    from radar.api.deps import current_user

    for router, needs_session in _routers():
        app.include_router(router, dependencies=[Depends(current_user)] if needs_session else [])

    # add_middleware: the last one added is the outermost.
    app.add_middleware(
        ClientGuard, allowed_origins=allowed_origins(settings), behind_tls_proxy=settings.behind_tls_proxy
    )
    app.add_middleware(TrustedHostGuard, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=MAX_BODY_BYTES,
        upload_limit=settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES,
    )
    app.add_middleware(
        RequestContextMiddleware, accounts_mode=settings.auth == "accounts", hsts=hsts_enabled(settings)
    )
    if settings.client_ip_header:
        app.add_middleware(ClientIpHeaderMiddleware, header=settings.client_ip_header)
    return app
