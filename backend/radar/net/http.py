"""Outbound HTTP client (SPEC §7). Owner: net.

All outbound HTTP goes through :class:`HttpClient` (CONVENTIONS "Python style"). One
:meth:`HttpClient.request` call does, in order:

1. **Strict-mode guard** (SPEC §2.1): ``radar.db.check_not_holding_write`` raises
   ``RuntimeError("holding write transaction: HttpClient.request")`` when ``RADAR_STRICT_TX=1`` and the
   calling thread holds a write transaction (outside strict mode it only logs a warning).
2. **Validation**: ``provider``/``call_type`` from the ledger vocabulary and a plain ``https`` URL whose
   host belongs to the provider (:data:`PROVIDER_HOSTS`), so a provider's secrets can only ever reach
   that provider's host. Violations raise ``ValueError`` (no request is sent).
3. ``RADAR_OFFLINE=1`` and no explicit ``transport`` -> :class:`EgressBlocked`.
4. **Per attempt**: an open Semantic Scholar breaker raises :class:`SourceUnavailable` immediately;
   OpenAlex calls run ``ledger.check_budget('openalex')`` (connectors never do); the per-host minimum
   interval (:func:`min_interval`) is waited for, with at most one request in flight per host; the
   request is sent; a ``cost_ledger`` row is enqueued on the background writer with the endpoint path
   (never the query string), status, unit cost, cost and the job/project from the arguments or
   :func:`call_context`, plus the OpenAlex ``x-ratelimit-*`` headers in ``meta_json``.
5. **Retries**: up to :data:`MAX_RETRIES` on 429/500/502/503/504/529 and connection errors, sleeping
   ``min(30, 2**n) + Retry-After`` seconds before retry ``n`` (2, 4, 8 s without ``Retry-After``); a
   ``Retry-After`` above :data:`MAX_RETRY_AFTER_S` ends the call at once. Exhausted retries raise
   :class:`SourceUnavailable`. OpenAlex 429 with ``x-ratelimit-remaining-usd <= 0`` raises
   :class:`BudgetExceeded` without retry; a Semantic Scholar 429 without ``S2_API_KEY`` raises
   :class:`SourceUnavailable` without retry and opens the breaker for ``RADAR_S2_BREAKER_MINUTES``.
   Every other status is returned to the caller as-is (redirects are not followed).

Secrets are attached here, never by connectors: OpenAlex ``api_key`` (``OPENALEX_API_KEY``), ``mailto``
for OpenAlex plus ``; mailto:<email>`` in the User-Agent comment (``RADAR_CONTACT_EMAIL``), the
Semantic Scholar ``x-api-key`` header (``S2_API_KEY``) and the Anthropic ``x-api-key`` header
(``ANTHROPIC_API_KEY``). A per-user OAuth access token may be passed as ``bearer=`` for GitHub only.
Caller-supplied ``api_key``/``mailto`` query parameters and ``X-Request-ID``, ``User-Agent``,
``x-api-key``, ``Authorization``, ``Cookie``, ``Proxy-Authorization`` headers are dropped, and
cookies set by providers are never stored or sent. Form bodies (``data=``) and JSON bodies are never
logged or recorded.

Exception messages are built from provider, endpoint path and status only (never a URL, a query
string, ``str()`` of an httpx exception or a secret), and are raised without exception chaining.
Importing this module installs the log-record redaction factory (``radar.logging_setup``) and keeps the
``httpx``/``httpcore`` loggers at WARNING unless they were configured explicitly.
"""

from __future__ import annotations

import contextvars
import logging
import math
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Any, NamedTuple

import httpx

from radar import db
from radar.config import Settings, get_settings
from radar.logging_setup import install_record_factory
from radar.net import ledger

log = logging.getLogger(__name__)

USER_AGENT = "Ratiocination/0.1 (local research tool)"

OPENALEX = "openalex"
SEMANTIC_SCHOLAR = "semantic_scholar"
GOOGLE = "google"
GITHUB = "github"
ANTHROPIC = "anthropic"

ARXIV_API_HOST = "export.arxiv.org"
"""The arXiv query API; a 429 here pauses only this host for ``RADAR_ARXIV_BREAKER_MINUTES`` (the RSS
feed and OAI-PMH hosts keep working). Observed 2026-10-12: throttled requests hang ~30 s, then 429
without Retry-After, so retrying quickly only makes things worse."""

PROVIDER_HOSTS: dict[str, frozenset[str]] = {
    "arxiv": frozenset({"rss.arxiv.org", ARXIV_API_HOST, "oaipmh.arxiv.org"}),
    OPENALEX: frozenset({"api.openalex.org"}),
    SEMANTIC_SCHOLAR: frozenset({"api.semanticscholar.org"}),
    GOOGLE: frozenset({"oauth2.googleapis.com"}),
    GITHUB: frozenset({"github.com", "api.github.com"}),
    ANTHROPIC: frozenset({"api.anthropic.com"}),
}
"""The only hosts each provider may be called on (https, default port)."""

HOST_MIN_INTERVALS: dict[str, float] = {
    "rss.arxiv.org": 1.0,
    "export.arxiv.org": 3.0,  # arXiv API terms: one request every 3 s, one connection at a time
    "oaipmh.arxiv.org": 3.0,  # OAI-PMH bulk harvesting (backfill); big pages, few requests
    "api.openalex.org": 0.1,
    # Sign-in and AI-summary calls are user-initiated and rare: no artificial spacing.
    "oauth2.googleapis.com": 0.0,
    "github.com": 0.0,
    "api.github.com": 0.0,
    "api.anthropic.com": 0.0,
}
"""Fixed per-host minimum intervals in seconds; see :func:`min_interval` for S2."""

S2_INTERVAL_S = 3.0
S2_KEYED_INTERVAL_S = 1.05
DEFAULT_MIN_INTERVAL_S = 1.0

METHODS: frozenset[str] = frozenset({"GET", "POST", "HEAD"})
MAX_RETRIES = 3
"""Retries after the first attempt (so at most 4 requests per call)."""
RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})  # 529: Anthropic overloaded
MAX_BACKOFF_S = 30.0
MAX_RETRY_AFTER_S = 60.0
"""A ``Retry-After`` longer than this ends the call with :class:`SourceUnavailable` instead of waiting."""

RESERVED_PARAMS: frozenset[str] = frozenset({"api_key", "mailto"})
RESERVED_HEADERS: frozenset[str] = frozenset(
    {"x-request-id", "user-agent", "x-api-key", "authorization", "proxy-authorization", "cookie"}
)

_MAX_PATH_IN_MESSAGE = 200
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+!#$&'*/=?^`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_bad_email_warned = False


# ------------------------------------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------------------------------------


class EgressBlocked(Exception):
    """Outbound HTTP refused: ``RADAR_OFFLINE=1`` and no explicit transport (SPEC §6)."""


class SourceUnavailable(Exception):
    """A provider failed after bounded retries (or the S2 breaker is open). SPEC §6.

    Attributes: ``provider``, ``status`` (HTTP status or ``None``), ``retry_after`` (seconds or
    ``None``). The message is built from provider, endpoint path and status only.
    """

    def __init__(
        self,
        provider: str,
        status: int | None = None,
        retry_after: float | None = None,
        message: str | None = None,
    ) -> None:
        self.provider = provider
        self.status = status
        self.retry_after = retry_after
        super().__init__(message or f"{provider} unavailable" + (f" (HTTP {status})" if status else ""))


class BudgetExceeded(Exception):
    """A metered provider reached its daily budget stop (SPEC §6, D7). Attribute: ``provider``."""

    def __init__(self, provider: str, message: str | None = None) -> None:
        self.provider = provider
        super().__init__(message or f"{provider} daily budget reached")


# ------------------------------------------------------------------------------------------------
# Pure helpers
# ------------------------------------------------------------------------------------------------


def contact_email(settings: Settings | None = None) -> str | None:
    """``RADAR_CONTACT_EMAIL`` when it is a plain address (anything else is never sent)."""
    global _bad_email_warned
    email = (settings or get_settings()).contact_email
    if not email:
        return None
    if _EMAIL_RE.fullmatch(email):
        return email
    if not _bad_email_warned:
        _bad_email_warned = True
        log.warning("RADAR_CONTACT_EMAIL is not a plain e-mail address; it is not sent to providers")
    return None


def user_agent(settings: Settings | None = None) -> str:
    """``Ratiocination/0.1 (local research tool)``, with ``; mailto:<email>`` inside the comment when
    ``RADAR_CONTACT_EMAIL`` is set."""
    email = contact_email(settings)
    if email:
        return f"{USER_AGENT[:-1]}; mailto:{email})"
    return USER_AGENT


def min_interval(host: str, settings: Settings | None = None) -> float:
    """Minimum seconds between request starts to ``host`` (SPEC §6)."""
    s = settings or get_settings()
    name = host.lower()
    if name == "api.semanticscholar.org":
        return S2_KEYED_INTERVAL_S if s.s2_api_key else S2_INTERVAL_S
    return HOST_MIN_INTERVALS.get(name, DEFAULT_MIN_INTERVAL_S)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds from a ``Retry-After`` value (delta-seconds or HTTP-date); ``None`` when absent/invalid."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if not math.isfinite(seconds):
        return None
    return max(0.0, seconds)


def _header_number(value: str | None, *, integral: bool = False) -> float | int | None:
    if value is None:
        return None
    try:
        number = float(value.strip())
    except ValueError:
        return None
    if not math.isfinite(number):
        return None
    if integral and number.is_integer():
        return int(number)
    return number


def openalex_rate_limit_meta(headers: Mapping[str, str]) -> dict[str, float | int | None]:
    """``meta_json`` for an OpenAlex response: ``{limit_usd, remaining_usd, remaining_requests,
    reset_seconds}`` from ``x-ratelimit-limit-usd``, ``x-ratelimit-remaining-usd``,
    ``x-ratelimit-remaining`` and ``x-ratelimit-reset`` (missing values are ``None``); ``{}`` when the
    response carries none of them."""
    meta: dict[str, float | int | None] = {
        "limit_usd": _header_number(headers.get("x-ratelimit-limit-usd")),
        "remaining_usd": _header_number(headers.get("x-ratelimit-remaining-usd")),
        "remaining_requests": _header_number(headers.get("x-ratelimit-remaining"), integral=True),
        "reset_seconds": _header_number(headers.get("x-ratelimit-reset"), integral=True),
    }
    if all(value is None for value in meta.values()):
        return {}
    return meta


def _short_path(path: str) -> str:
    if len(path) <= _MAX_PATH_IN_MESSAGE:
        return path
    return path[: _MAX_PATH_IN_MESSAGE - 3] + "..."


def _unavailable_message(provider: str, path: str, status: int | None) -> str:
    detail = f"HTTP {status}" if status is not None else "no response"
    return f"{provider} unavailable: {_short_path(path)} ({detail})"


def _validate_target(method: str, url: str, provider: str, call_type: str) -> tuple[str, httpx.URL, str, str]:
    """``(METHOD, url, host, path)``; ``ValueError`` (never echoing the URL) when the call is not allowed."""
    if provider not in PROVIDER_HOSTS:
        raise ValueError(f"unknown provider {provider!r}")
    if call_type not in ledger.CALL_TYPES:
        raise ValueError(f"unknown call_type {call_type!r}")
    verb = str(method).upper()
    if verb not in METHODS:
        raise ValueError(f"unsupported HTTP method for {provider}")
    target: httpx.URL | None
    try:
        target = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        target = None
    if target is None:
        raise ValueError(f"{provider}: invalid request URL")
    if target.scheme != "https" or target.userinfo or target.port not in (None, 443):
        raise ValueError(f"{provider}: only plain https URLs are allowed")
    host = target.host.lower()
    if host not in PROVIDER_HOSTS[provider]:
        raise ValueError(f"{provider}: the URL host is not an allowed host for this provider")
    return verb, target, host, target.path or "/"


def _query_items(
    target: httpx.URL, params: Mapping[str, Any] | None, provider: str, settings: Settings
) -> list[tuple[str, str]]:
    items = [(k, v) for k, v in target.params.multi_items() if k.lower() not in RESERVED_PARAMS]
    if params:
        items.extend(
            (k, v) for k, v in httpx.QueryParams(params).multi_items() if k.lower() not in RESERVED_PARAMS
        )
    email = contact_email(settings)
    if provider == OPENALEX:
        if settings.openalex_api_key:
            items.append(("api_key", settings.openalex_api_key))
        if email:
            items.append(("mailto", email))
    return items


def _request_headers(
    headers: Mapping[str, str] | None, provider: str, settings: Settings, bearer: str | None = None
) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if str(name).strip().lower() in RESERVED_HEADERS:
            continue
        out[str(name)] = value
    # Sign-in and AI calls identify the app, not a contact address.
    out["User-Agent"] = user_agent(settings) if provider not in (GOOGLE, GITHUB, ANTHROPIC) else USER_AGENT
    if provider == SEMANTIC_SCHOLAR and settings.s2_api_key:
        out["x-api-key"] = settings.s2_api_key
    if provider == ANTHROPIC and settings.anthropic_api_key:
        out["x-api-key"] = settings.anthropic_api_key
    if bearer:
        out["Authorization"] = f"Bearer {bearer}"
    return out


def _budget_exhausted(meta: Mapping[str, Any]) -> bool:
    remaining = meta.get("remaining_usd")
    return isinstance(remaining, (int, float)) and remaining <= 0


def _cookie_jar() -> CookieJar:
    """A jar that accepts and returns no cookies (providers cannot correlate requests by cookie)."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


@dataclass(frozen=True)
class _Call:
    """Everything about one :meth:`HttpClient.request` call that stays fixed across attempts."""

    provider: str
    call_type: str
    method: str
    url: httpx.URL  # without query string (the query travels in ``query``)
    host: str
    path: str
    query: list[tuple[str, str]]
    body: object | None  # JSON body
    form: dict[str, str] | None  # form-encoded body
    headers: dict[str, str]
    timeout: float
    interval: float
    unit_cost: float
    keyless_s2: bool
    breaker_key: str | None  # provider or host whose breaker a 429 opens (keyless S2, arXiv API)
    breaker_minutes: float
    job_id: int | None
    project_id: int | None


class _Outcome(NamedTuple):
    response: httpx.Response | None  # None: request error (connection, timeout, protocol, decoding)
    error_name: str | None  # exception class name of a request error (never its text)
    meta: dict[str, Any]  # OpenAlex rate-limit values recorded in the ledger row
    breaker_seconds: float | None  # set when this attempt opened the keyless S2 breaker


def _send(client: httpx.Client, call: _Call) -> tuple[httpx.Response | None, str | None]:
    """Send once: ``(response, None)``, or ``(None, exception class name)`` on a request error."""
    try:
        response = client.request(
            call.method,
            call.url,
            params=call.query,
            json=call.body,
            data=call.form,
            headers=call.headers,
            timeout=call.timeout,
        )
    except httpx.RequestError as exc:  # connection errors, timeouts, protocol/decoding errors
        return None, type(exc).__name__
    return response, None


# ------------------------------------------------------------------------------------------------
# Client
# ------------------------------------------------------------------------------------------------


class HttpClient:
    """Rate-limited, retrying, ledger-recording HTTP client (SPEC §6).

    ``transport`` replaces the network (tests pass ``httpx.MockTransport``; it also allows requests while
    ``RADAR_OFFLINE=1``). ``sleep`` and ``monotonic`` drive every wait (per-host intervals, backoff) and
    the Semantic Scholar breaker, so tests can use a fake clock. Thread-safe; one instance is shared
    process-wide through :func:`get_http`.
    """

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.transport = transport
        self.sleep = sleep
        self.monotonic = monotonic
        self._lock = threading.Lock()
        self._client: httpx.Client | None = None
        self._host_locks: dict[str, threading.Lock] = {}
        self._next_slot: dict[str, float] = {}
        self._breakers: dict[str, tuple[float, datetime]] = {}
        self._last_status: dict[str, int | None] = {}

    # -- public state (settings page) --------------------------------------------------------------

    def breaker_until(self, provider: str = SEMANTIC_SCHOLAR) -> datetime | None:
        """When the provider's breaker closes (aware UTC), or ``None`` when it is closed."""
        with self._lock:
            entry = self._breakers.get(provider)
            if entry is None or entry[0] <= self.monotonic():
                return None
            return entry[1]

    def last_status(self, provider: str) -> int | None:
        """HTTP status of the provider's latest attempt (``None``: none yet or no response)."""
        with self._lock:
            return self._last_status.get(provider)

    def close(self) -> None:
        """Close pooled connections (a later request opens new ones)."""
        with self._lock:
            client, self._client = self._client, None
        if client is not None:
            client.close()

    # -- request -----------------------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        provider: str,
        call_type: str,
        params: dict[str, Any] | None = None,
        json: object | None = None,
        data: Mapping[str, str] | None = None,
        headers: dict[str, str] | None = None,
        bearer: str | None = None,
        timeout: float = 30.0,
        job_id: int | None = None,
        project_id: int | None = None,
    ) -> httpx.Response:
        """Send one request (with retries) and record it in the cost ledger (SPEC §7).

        ``json`` and ``data`` (form fields) are mutually exclusive; ``bearer`` (an OAuth access token)
        is accepted for GitHub only. Returns the response for any status other than the retried ones.
        Raises ``RuntimeError`` (strict mode, holding a write transaction), ``ValueError`` (call not
        allowed), :class:`EgressBlocked`, :class:`BudgetExceeded` or :class:`SourceUnavailable`.
        """
        db.check_not_holding_write("HttpClient.request")
        if db.holding_write_transaction():
            log.warning("HTTP request while holding a write transaction (provider=%s)", provider)
        verb, target, host, path = _validate_target(method, url, provider, call_type)
        if json is not None and data is not None:
            raise ValueError(f"{provider}: pass either json or data, not both")
        if bearer is not None and provider != GITHUB:
            raise ValueError(f"{provider}: bearer tokens are only sent to GitHub")
        settings = get_settings()
        if settings.offline and self.transport is None:
            raise EgressBlocked(f"{provider} {_short_path(path)}: outbound HTTP is blocked (RADAR_OFFLINE=1)")

        ctx_job_id, ctx_project_id = _call_ctx.get()
        keyless_s2 = provider == SEMANTIC_SCHOLAR and not settings.s2_api_key
        call = _Call(
            provider=provider,
            call_type=call_type,
            method=verb,
            url=target.copy_with(query=None, fragment=None),
            host=host,
            path=path,
            query=_query_items(target, params, provider, settings),
            body=json,
            form={str(k): str(v) for k, v in data.items()} if data is not None else None,
            headers=_request_headers(headers, provider, settings, bearer),
            timeout=timeout,
            interval=min_interval(host, settings),
            unit_cost=ledger.unit_cost_for(provider, call_type),
            keyless_s2=keyless_s2,
            breaker_key=SEMANTIC_SCHOLAR if keyless_s2 else (host if host == ARXIV_API_HOST else None),
            breaker_minutes=float(
                settings.s2_breaker_minutes if keyless_s2 else settings.arxiv_breaker_minutes
            ),
            job_id=job_id if job_id is not None else ctx_job_id,
            project_id=project_id if project_id is not None else ctx_project_id,
        )

        retries = 0
        while True:
            outcome = self._attempt(call)
            response = outcome.response
            status = response.status_code if response is not None else None
            retry_after: float | None = None
            if response is not None:
                if provider == OPENALEX and status == 429 and _budget_exhausted(outcome.meta):
                    log.warning("openalex %s: HTTP 429 with no budget left; OpenAlex stopped for today", path)
                    raise BudgetExceeded(
                        provider,
                        f"{provider} daily budget exhausted ({_short_path(path)}: HTTP 429); "
                        "calls resume after 00:00 UTC",
                    )
                if outcome.breaker_seconds is not None:
                    seconds = outcome.breaker_seconds
                    reason = "no API key" if call.keyless_s2 else "arXiv API rate limit"
                    log.warning("%s %s: HTTP 429 (%s); paused for %.0f s", provider, path, reason, seconds)
                    raise SourceUnavailable(
                        provider,
                        429,
                        seconds,
                        message=f"{_unavailable_message(provider, path, 429)}; "
                        f"paused for {seconds / 60:g} min ({reason})",
                    )
                if status not in RETRY_STATUSES:
                    return response
                retry_after = parse_retry_after(response.headers.get("retry-after"))

            failure = f"HTTP {status}" if status is not None else (outcome.error_name or "no response")
            if retries >= MAX_RETRIES or (retry_after is not None and retry_after > MAX_RETRY_AFTER_S):
                log.warning("%s %s: %s; giving up after %d attempt(s)", provider, path, failure, retries + 1)
                raise SourceUnavailable(
                    provider, status, retry_after, message=_unavailable_message(provider, path, status)
                )
            retries += 1
            delay = min(MAX_BACKOFF_S, 2.0**retries) + (retry_after or 0.0)
            if retry_after:
                self._defer_host(host, retry_after)
            log.warning(
                "%s %s: %s; retry %d/%d in %.2f s", provider, path, failure, retries, MAX_RETRIES, delay
            )
            self.sleep(delay)

    # -- internals ---------------------------------------------------------------------------------

    def _attempt(self, call: _Call) -> _Outcome:
        """One attempt under the host lock (one request in flight per host): breaker and budget checks,
        the interval wait, the request, its ledger row and, for a keyless S2 429, opening the breaker.
        Doing all of it under the lock means a waiting thread sees the previous attempt's ledger row
        (rate-limit headers) and breaker state before it sends anything."""
        client = self._http_client()
        with self._host_lock(call.host):
            self._raise_if_breaker_open(call.provider, call.path, call.host)
            if call.provider == OPENALEX:
                ledger.check_budget(OPENALEX)
            self._wait_for_slot(call.host, call.interval)
            response, error_name = _send(client, call)
            status = response.status_code if response is not None else None
            meta = (
                openalex_rate_limit_meta(response.headers)
                if response is not None and call.provider == OPENALEX
                else {}
            )
            charged = status is not None and status != 429 and status < 500
            ledger.record_call(
                provider=call.provider,
                call_type=call.call_type,
                endpoint=call.path,
                status_code=status,
                units=1.0,
                unit_cost=call.unit_cost,
                cost_usd=call.unit_cost if charged else 0.0,
                job_id=call.job_id,
                project_id=call.project_id,
                meta=meta,
            )
            with self._lock:
                self._last_status[call.provider] = status
            breaker_seconds = None
            if status == 429 and call.breaker_key is not None:
                breaker_seconds = self._open_breaker(call.breaker_key, call.breaker_minutes)
        return _Outcome(response, error_name, meta, breaker_seconds)

    def _http_client(self) -> httpx.Client:
        with self._lock:
            if self._client is None:
                self._client = httpx.Client(
                    transport=self.transport, follow_redirects=False, cookies=_cookie_jar()
                )
            return self._client

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            lock = self._host_locks.get(host)
            if lock is None:
                lock = self._host_locks[host] = threading.Lock()
            return lock

    def _wait_for_slot(self, host: str, interval: float) -> None:
        """Reserve the next start slot for ``host`` and sleep until it (caller holds the host lock)."""
        with self._lock:
            now = self.monotonic()
            slot = max(now, self._next_slot.get(host, now))
            self._next_slot[host] = slot + interval
        delay = slot - now
        if delay > 0:
            self.sleep(delay)

    def _defer_host(self, host: str, seconds: float) -> None:
        """Honour ``Retry-After`` for every thread: no request to ``host`` starts before it elapses."""
        with self._lock:
            until = self.monotonic() + seconds
            self._next_slot[host] = max(self._next_slot.get(host, until), until)

    def _open_breaker(self, key: str, minutes: float) -> float:
        """Pause ``key`` (a provider, or a host such as ``export.arxiv.org``) for ``minutes``."""
        seconds = max(0.0, float(minutes) * 60.0)
        with self._lock:
            self._breakers[key] = (
                self.monotonic() + seconds,
                datetime.now(UTC) + timedelta(seconds=seconds),
            )
        return seconds

    def _raise_if_breaker_open(self, provider: str, path: str, host: str | None = None) -> None:
        """Raise when the provider's breaker or this host's breaker is open."""
        for key in (provider, host):
            if key is None:
                continue
            with self._lock:
                entry = self._breakers.get(key)
                if entry is None:
                    continue
                remaining = entry[0] - self.monotonic()
                if remaining <= 0:
                    del self._breakers[key]
                    continue
            reason = "without an API key" if key == SEMANTIC_SCHOLAR else "from the arXiv API"
            raise SourceUnavailable(
                provider,
                None,
                remaining,
                message=f"{provider} unavailable: {_short_path(path)} skipped (paused after HTTP 429 {reason})",
            )


# ------------------------------------------------------------------------------------------------
# Process-wide client and call attribution
# ------------------------------------------------------------------------------------------------

_client: HttpClient | None = None
_client_lock = threading.Lock()


def get_http() -> HttpClient:
    """The process-wide client (a default one is created lazily)."""
    global _client
    with _client_lock:
        if _client is None:
            _client = HttpClient()
        return _client


def set_http(client: HttpClient | None) -> None:
    """Install ``client`` (tests); ``None`` resets to a fresh default client."""
    global _client
    with _client_lock:
        _client = client


_call_ctx: contextvars.ContextVar[tuple[int | None, int | None]] = contextvars.ContextVar(
    "radar_http_call_context", default=(None, None)
)


@contextmanager
def call_context(job_id: int | None, project_id: int | None) -> Iterator[None]:
    """Attribute requests made inside the block to ``job_id``/``project_id`` (the runner enters it).

    Explicit ``job_id``/``project_id`` arguments of :meth:`HttpClient.request` win. Context variables
    are per thread: a worker thread must enter its own ``call_context``.
    """
    token = _call_ctx.set((job_id, project_id))
    try:
        yield
    finally:
        _call_ctx.reset(token)


# Secrets never reach logs: redact every log record (api_key=, mailto, x-api-key, configured values)
# and keep httpx's per-request INFO lines (full URLs) off unless a logger level was set explicitly.
install_record_factory()
for _logger_name in ("httpx", "httpcore"):
    if logging.getLogger(_logger_name).level == logging.NOTSET:
        logging.getLogger(_logger_name).setLevel(logging.WARNING)
