"""radar.net.http (SPEC §7): per-host intervals, retries, budgets, the S2 breaker, guards, ledger rows,
call attribution, form bodies, bearer tokens and secret hygiene. Everything runs on
httpx.MockTransport with a fake clock."""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from radar import db, models
from radar.net import http as net_http
from radar.net import ledger
from radar.net.http import (
    BudgetExceeded,
    EgressBlocked,
    HttpClient,
    SourceUnavailable,
    call_context,
    get_http,
    set_http,
)

OPENALEX_WORK = "https://api.openalex.org/works/W2741809807"
OPENALEX_LIST = "https://api.openalex.org/works"
FEED = "https://rss.arxiv.org/atom/cs.IR"  # a plain unmetered host (no breaker, no key)
GITHUB_USER = "https://api.github.com/user"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
ANTHROPIC_MESSAGES = "https://api.anthropic.com/v1/messages"
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_FEED = "https://rss.arxiv.org/atom/cs.IR+cs.DL"
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"
S2_REFS = "https://api.semanticscholar.org/graph/v1/paper/ARXIV:2004.07180/references"

SECRET_KEY = "zqsecretkey9"
SECRET_EMAIL = "zq@example.invalid"
SECRET_S2_KEY = "zqs2secret7"


# ------------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------------


class FakeClock:
    """Thread-safe fake monotonic clock: ``sleep`` records the delay and advances the time."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.now += seconds

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


Answer = httpx.Response | Callable[[httpx.Request], httpx.Response]


class Server:
    """MockTransport handler answering from a script (the last answer repeats); records each request
    and the fake time at which it arrived."""

    def __init__(self, clock: FakeClock, *answers: Answer) -> None:
        self.clock = clock
        self.answers: list[Answer] = list(answers) or [httpx.Response(200, json={})]
        self.requests: list[httpx.Request] = []
        self.times: list[float] = []
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            self.requests.append(request)
            self.times.append(self.clock.monotonic())
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, httpx.Response):
            return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)
        return answer(request)


def resp(status: int = 200, *, json: Any = None, headers: dict[str, str] | None = None) -> httpx.Response:
    if json is None:
        return httpx.Response(status, headers=headers)
    return httpx.Response(status, json=json, headers=headers)


def refuse(request: httpx.Request) -> httpx.Response:
    """A connection error whose text contains the full URL and User-Agent (it must never be echoed)."""
    raise httpx.ConnectError(
        f"cannot reach {request.url} as {request.headers['user-agent']}", request=request
    )


def make_client(clock: FakeClock, server: Callable[[httpx.Request], httpx.Response]) -> HttpClient:
    return HttpClient(transport=httpx.MockTransport(server), sleep=clock.sleep, monotonic=clock.monotonic)


def ledger_rows() -> list[models.CostLedger]:
    ledger.flush()
    with db.session_scope() as session:
        return list(session.scalars(select(models.CostLedger).order_by(models.CostLedger.id)))


def lookup(
    client: HttpClient, url: str = OPENALEX_WORK, provider: str = "openalex", **kwargs: Any
) -> httpx.Response:
    return client.request("GET", url, provider=provider, call_type="lookup", **kwargs)


def openalex_list(client: HttpClient, **kwargs: Any) -> httpx.Response:
    params = {"filter": "cites:W2741809807", "per-page": 3}
    return client.request(
        "GET", OPENALEX_LIST, provider="openalex", call_type="list", params=params, **kwargs
    )


def leak_text(exc: BaseException) -> str:
    return (str(exc) + repr(exc) + "".join(traceback.format_exception(exc))).lower()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


# ------------------------------------------------------------------------------------------------
# Offline, strict mode, validation
# ------------------------------------------------------------------------------------------------


def test_offline_blocks_the_default_client_but_not_an_explicit_transport(clock: FakeClock) -> None:
    client = HttpClient(sleep=clock.sleep, monotonic=clock.monotonic)
    with pytest.raises(EgressBlocked) as info:
        lookup(client, params={"select": "id"})
    assert str(info.value) == "openalex /works/W2741809807: outbound HTTP is blocked (RADAR_OFFLINE=1)"
    with pytest.raises(EgressBlocked):
        get_http().request(
            "GET", ARXIV_API, provider="arxiv", call_type="query", params={"id_list": "2504.08385"}
        )
    assert ledger_rows() == []

    server = Server(clock, resp(200, json={"id": "W2741809807"}))
    assert lookup(make_client(clock, server)).json() == {"id": "W2741809807"}
    assert len(server.requests) == 1


def test_strict_guard_raises_inside_an_open_write_transaction(
    clock: FakeClock, assert_no_open_write: Callable[[], None]
) -> None:
    server = Server(clock)
    client = make_client(clock, server)
    with db.session_scope(write=True) as session:
        session.execute(select(models.User.id)).all()
        assert db.holding_write_transaction()
        with pytest.raises(RuntimeError, match="holding write transaction"):
            lookup(client)
        with pytest.raises(RuntimeError, match="holding write transaction"):
            client.request("GET", ARXIV_API, provider="arxiv", call_type="query")
    assert server.requests == []
    assert_no_open_write()
    assert lookup(client).status_code == 200
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("method", "url", "provider", "call_type"),
    [
        ("GET", OPENALEX_WORK, "s2", "lookup"),
        ("GET", OPENALEX_WORK, "openalex", "fetch"),
        ("DELETE", OPENALEX_WORK, "openalex", "lookup"),
        ("GET", FEED, "openalex", "lookup"),
        ("GET", OPENALEX_WORK, "crossref", "lookup"),  # Crossref was removed in v0.3
        ("GET", "https://accounts.google.com/o/oauth2/v2/auth", "google", "auth"),  # never server-side
        ("GET", OPENALEX_WORK, "github", "auth"),
        ("GET", "http://api.openalex.org/works/W1", "openalex", "lookup"),
        ("GET", "https://user:pw@api.openalex.org/works/W1", "openalex", "lookup"),
        ("GET", "https://api.openalex.org:8443/works/W1", "openalex", "lookup"),
        ("GET", "https://example.org/collect?api_key=1", "arxiv", "query"),
        ("GET", "not a url", "arxiv", "query"),
    ],
)
def test_calls_outside_the_provider_allowlist_raise_value_error(
    clock: FakeClock, method: str, url: str, provider: str, call_type: str
) -> None:
    server = Server(clock)
    with pytest.raises(ValueError) as info:
        make_client(clock, server).request(method, url, provider=provider, call_type=call_type)
    assert "://" not in str(info.value) and "example.org" not in str(info.value)
    assert server.requests == []
    assert ledger_rows() == []


# ------------------------------------------------------------------------------------------------
# Per-host minimum intervals
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "provider", "call_type", "env", "interval"),
    [
        (ARXIV_FEED, "arxiv", "feed", {}, 1.0),
        (ARXIV_API, "arxiv", "query", {}, 3.0),
        (OPENALEX_WORK, "openalex", "lookup", {}, 0.1),
        (GITHUB_USER, "github", "auth", {}, 0.0),
        (ANTHROPIC_MESSAGES, "anthropic", "generate", {}, 0.0),
        (S2_BATCH, "semantic_scholar", "batch", {}, 3.0),
        (S2_BATCH, "semantic_scholar", "batch", {"S2_API_KEY": SECRET_S2_KEY}, 1.05),
    ],
)
def test_minimum_interval_between_requests_to_one_host(
    clock: FakeClock,
    set_env: Callable[..., None],
    url: str,
    provider: str,
    call_type: str,
    env: dict[str, str],
    interval: float,
) -> None:
    if env:
        set_env(**env)
    host = httpx.URL(url).host
    assert net_http.min_interval(host) == interval
    server = Server(clock)
    client = make_client(clock, server)
    method = "POST" if call_type in ("batch", "generate") else "GET"
    for _ in range(3):
        client.request(method, url, provider=provider, call_type=call_type)
    assert clock.sleeps == (pytest.approx([interval, interval]) if interval else [])
    gaps = [later - earlier for earlier, later in zip(server.times, server.times[1:], strict=False)]
    assert gaps == pytest.approx([interval, interval])

    clock.advance(interval * 3)  # idle for longer than the interval: no wait
    client.request(method, url, provider=provider, call_type=call_type)
    assert len(clock.sleeps) == (2 if interval else 0)


def test_intervals_are_tracked_per_host(clock: FakeClock) -> None:
    client = make_client(clock, Server(clock))
    client.request("GET", ARXIV_API, provider="arxiv", call_type="query")
    lookup(client)
    client.request("GET", GITHUB_USER, provider="github", call_type="auth")
    client.request("GET", ARXIV_FEED, provider="arxiv", call_type="feed")
    assert clock.sleeps == []
    client.request("GET", ARXIV_API, provider="arxiv", call_type="query")
    assert clock.sleeps == [3.0]


def test_interval_holds_for_concurrent_threads(clock: FakeClock) -> None:
    server = Server(clock)
    client = make_client(clock, server)

    def call(_: int) -> int:
        return client.request("GET", ARXIV_API, provider="arxiv", call_type="query").status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        assert list(pool.map(call, range(12))) == [200] * 12
    times = sorted(server.times)
    gaps = [later - earlier for earlier, later in zip(times, times[1:], strict=False)]
    assert min(gaps) >= 3.0 - 1e-9
    assert times[-1] - times[0] == pytest.approx(33.0)
    assert clock.sleeps == pytest.approx([3.0] * 11)


def test_one_request_in_flight_per_host_without_blocking_other_hosts(clock: FakeClock) -> None:
    release = threading.Event()
    first_started = threading.Event()
    arxiv_entries: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "export.arxiv.org":
            arxiv_entries.append(clock.monotonic())
            first_started.set()
            release.wait(5)
        return httpx.Response(200, json={})

    client = make_client(clock, handler)
    kwargs = {"provider": "arxiv", "call_type": "query"}
    first = threading.Thread(target=client.request, args=("GET", ARXIV_API), kwargs=kwargs)
    second = threading.Thread(target=client.request, args=("GET", ARXIV_API), kwargs=kwargs)
    first.start()
    try:
        assert first_started.wait(5)
        assert lookup(client).status_code == 200  # another host is not blocked
        second.start()
        second.join(0.2)
        assert len(arxiv_entries) == 1  # the second arXiv request waits for the first to finish
    finally:
        release.set()
        first.join(5)
    second.join(5)
    assert len(arxiv_entries) == 2
    assert arxiv_entries[1] - arxiv_entries[0] == pytest.approx(3.0)


def run_queued_pair(
    clock: FakeClock, answer: httpx.Response, call: Callable[[HttpClient], object]
) -> tuple[list[httpx.Request], list[object]]:
    """Start ``call`` twice: the first request is held in flight until the second thread is queued
    behind it on the same host; every request gets ``answer``. Returns (requests sent, outcomes)."""
    release = threading.Event()
    started = threading.Event()
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        started.set()
        release.wait(5)
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)

    client = make_client(clock, handler)
    outcomes: list[object] = [None, None]

    def run(slot: int) -> None:
        try:
            outcomes[slot] = call(client)
        except (SourceUnavailable, BudgetExceeded) as exc:
            outcomes[slot] = exc

    first = threading.Thread(target=run, args=(0,))
    second = threading.Thread(target=run, args=(1,))
    first.start()
    try:
        assert started.wait(5)
        second.start()
        second.join(0.2)  # now waiting for the host
    finally:
        release.set()
        first.join(5)
    second.join(5)
    return sent, outcomes


def test_a_request_queued_behind_a_keyless_s2_429_is_never_sent(clock: FakeClock) -> None:
    sent, (first, second) = run_queued_pair(
        clock,
        resp(429),
        lambda c: c.request("GET", S2_REFS, provider="semantic_scholar", call_type="query"),
    )
    assert len(sent) == 1
    assert isinstance(first, SourceUnavailable) and first.status == 429
    assert isinstance(second, SourceUnavailable) and second.status is None  # the breaker was already open


def test_a_request_queued_behind_an_openalex_call_sees_its_budget_headers(clock: FakeClock) -> None:
    nearly_spent = {"x-ratelimit-limit-usd": "0.1", "x-ratelimit-remaining-usd": "0.01"}
    sent, (first, second) = run_queued_pair(clock, resp(200, json={}, headers=nearly_spent), lookup)
    assert len(sent) == 1
    assert isinstance(first, httpx.Response) and first.status_code == 200
    assert isinstance(second, BudgetExceeded)


# ------------------------------------------------------------------------------------------------
# Retries and Retry-After
# ------------------------------------------------------------------------------------------------


def test_5xx_then_success_retries_with_exponential_backoff(clock: FakeClock) -> None:
    server = Server(clock, resp(503), resp(502), resp(500), resp(200, json={"ok": True}))
    response = lookup(make_client(clock, server), FEED, "arxiv")
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert clock.sleeps == [2.0, 4.0, 8.0]
    assert [row.status_code for row in ledger_rows()] == [503, 502, 500, 200]


def test_retry_after_is_added_to_the_backoff(clock: FakeClock) -> None:
    server = Server(
        clock,
        resp(429, headers={"Retry-After": "3"}),
        resp(503, headers={"Retry-After": "1.5"}),
        resp(504),
        resp(200),
    )
    response = lookup(make_client(clock, server), FEED, "arxiv")
    assert response.status_code == 200
    assert clock.sleeps == [5.0, 5.5, 8.0]


def test_exhausted_retries_raise_source_unavailable_without_url(
    clock: FakeClock, set_env: Callable[..., None]
) -> None:
    set_env(OPENALEX_API_KEY=SECRET_KEY)
    server = Server(clock, resp(503))
    with pytest.raises(SourceUnavailable) as info:
        openalex_list(make_client(clock, server))
    exc = info.value
    assert (exc.provider, exc.status, exc.retry_after) == ("openalex", 503, None)
    assert str(exc) == "openalex unavailable: /works (HTTP 503)"
    assert exc.__cause__ is None and exc.__context__ is None
    assert len(server.requests) == 4  # first attempt + 3 retries
    assert clock.sleeps == [2.0, 4.0, 8.0]
    text = leak_text(exc)
    for forbidden in ("https://", "api.openalex.org", "api_key", SECRET_KEY, "cites%3a", "per-page="):
        assert forbidden not in text
    assert [row.status_code for row in ledger_rows()] == [503] * 4


def test_connection_errors_are_retried_and_never_echoed(clock: FakeClock) -> None:
    server = Server(clock, refuse, refuse, resp(200, json={"ok": True}))
    client = make_client(clock, server)
    assert lookup(client, FEED, "arxiv").json() == {"ok": True}
    assert clock.sleeps == [2.0, 4.0]
    assert client.last_status("arxiv") == 200

    failing = make_client(clock, Server(clock, refuse))
    with pytest.raises(SourceUnavailable) as info:
        lookup(failing, FEED, "arxiv")
    assert info.value.status is None
    assert str(info.value) == "arxiv unavailable: /atom/cs.IR (no response)"
    assert "cannot reach" not in leak_text(info.value) and "https://" not in leak_text(info.value)
    assert failing.last_status("arxiv") is None
    assert [row.status_code for row in ledger_rows()] == [None, None, 200, None, None, None, None]


def test_a_long_retry_after_ends_the_call_without_waiting(clock: FakeClock) -> None:
    server = Server(clock, resp(503, headers={"Retry-After": "3600"}))
    with pytest.raises(SourceUnavailable) as info:
        lookup(make_client(clock, server), FEED, "arxiv")
    assert (info.value.status, info.value.retry_after) == (503, 3600.0)
    assert len(server.requests) == 1 and clock.sleeps == []


def test_max_retries_lowers_the_budget_for_one_call(clock: FakeClock) -> None:
    """The OAI-PMH harvest uses this: a throttled endpoint must not cost four long timeouts."""
    server = Server(clock, resp(503))
    with pytest.raises(SourceUnavailable):
        lookup(make_client(clock, server), FEED, "arxiv", max_retries=1)
    assert len(server.requests) == 2 and clock.sleeps == [2.0]

    once = Server(clock, resp(503))
    with pytest.raises(SourceUnavailable):
        lookup(make_client(clock, once), FEED, "arxiv", max_retries=0)
    assert len(once.requests) == 1 and clock.sleeps == [2.0]  # no further sleep

    # It can only lower the limit, never raise it above MAX_RETRIES.
    many = Server(clock, resp(503))
    with pytest.raises(SourceUnavailable):
        lookup(make_client(clock, many), FEED, "arxiv", max_retries=99)
    assert len(many.requests) == net_http.MAX_RETRIES + 1


@pytest.mark.parametrize("status", [200, 204, 302, 304, 400, 401, 404, 501, 599])
def test_other_statuses_are_returned_without_retry(clock: FakeClock, status: int) -> None:
    server = Server(clock, resp(status, headers={"Location": "https://example.org/elsewhere"}))
    response = lookup(make_client(clock, server), FEED, "arxiv")
    assert response.status_code == status
    assert len(server.requests) == 1 and clock.sleeps == []  # redirects are never followed
    assert [row.status_code for row in ledger_rows()] == [status]


def test_retry_after_parsing() -> None:
    now = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    later = (now + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert net_http.parse_retry_after("7") == 7.0
    assert net_http.parse_retry_after(" 1.5 ") == 1.5
    assert net_http.parse_retry_after("-4") == 0.0
    assert net_http.parse_retry_after(later, now=now) == pytest.approx(90.0)
    assert net_http.parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=now) == 0.0
    for invalid in (None, "", "soon", "nan", "inf"):
        assert net_http.parse_retry_after(invalid) is None


# ------------------------------------------------------------------------------------------------
# OpenAlex budget
# ------------------------------------------------------------------------------------------------


def test_openalex_429_without_budget_left_raises_budget_exceeded_without_retry(clock: FakeClock) -> None:
    exhausted = {"x-ratelimit-limit-usd": "0.1", "x-ratelimit-remaining-usd": "0", "Retry-After": "1"}
    server = Server(clock, resp(429, headers=exhausted), resp(200))
    client = make_client(clock, server)
    with pytest.raises(BudgetExceeded) as info:
        openalex_list(client)
    assert info.value.provider == "openalex"
    assert "https://" not in leak_text(info.value)
    assert len(server.requests) == 1 and clock.sleeps == []
    (row,) = ledger_rows()
    assert (row.status_code, row.cost_usd) == (429, 0.0)
    assert row.meta_json["remaining_usd"] == 0 and row.meta_json["limit_usd"] == 0.1

    with pytest.raises(BudgetExceeded):  # stopped for the rest of the UTC day: nothing is sent
        lookup(client)
    assert len(server.requests) == 1
    assert lookup(client, FEED, "arxiv").status_code == 200  # other providers continue


def test_openalex_429_with_budget_left_is_retried(clock: FakeClock) -> None:
    limited = {"x-ratelimit-limit-usd": "0.1", "x-ratelimit-remaining-usd": "0.09", "Retry-After": "1"}
    server = Server(clock, resp(429, headers=limited), resp(200, json={"id": "W2741809807"}))
    assert lookup(make_client(clock, server)).status_code == 200
    assert clock.sleeps == [3.0]


def test_openalex_budget_stop_from_rate_limit_headers(clock: FakeClock) -> None:
    headers = {
        "x-ratelimit-limit-usd": "0.1",
        "x-ratelimit-remaining-usd": "0.03",
        "x-ratelimit-remaining": "300",
        "x-ratelimit-reset": "3600",
    }
    server = Server(
        clock,
        resp(200, json={"results": []}, headers=headers),
        resp(200, json={"results": []}, headers={**headers, "x-ratelimit-remaining-usd": "0.02"}),
        resp(200),
    )
    client = make_client(clock, server)
    openalex_list(client)  # 70 % of the provider's limit used: below the 80 % stop
    status = ledger.budget_status("openalex")
    assert (status.source, status.stopped) == ("headers", False)
    assert status.spent_today_usd == pytest.approx(0.07)

    openalex_list(client)  # now exactly 80 %
    with pytest.raises(BudgetExceeded):
        lookup(client)
    assert len(server.requests) == 2
    status = ledger.budget_status("openalex")
    assert (status.source, status.stopped) == ("headers", True)
    assert status.remaining_today_usd == pytest.approx(0.02)


def test_openalex_budget_stop_from_ledger_sums(clock: FakeClock, set_env: Callable[..., None]) -> None:
    set_env(RADAR_OPENALEX_DAILY_BUDGET_USD="0.0005")  # stop at 80 % = $0.0004 = four list calls
    server = Server(clock, resp(200, json={"results": []}))
    client = make_client(clock, server)
    for _ in range(3):
        lookup(client)  # single-work lookups cost $0
    for _ in range(4):
        openalex_list(client)
    with pytest.raises(BudgetExceeded):
        lookup(client)
    assert len(server.requests) == 7
    status = ledger.budget_status("openalex")
    assert (status.source, status.stopped) == ("ledger", True)
    assert status.spent_today_usd == pytest.approx(0.0004)
    assert status.remaining_today_usd == pytest.approx(0.0001)


def test_openalex_stops_when_few_requests_remain(clock: FakeClock) -> None:
    headers = {
        "x-ratelimit-limit-usd": "1",
        "x-ratelimit-remaining-usd": "0.9",
        "x-ratelimit-remaining": "19",
    }
    server = Server(clock, resp(200, json={}, headers=headers))
    client = make_client(clock, server)
    lookup(client)
    with pytest.raises(BudgetExceeded):
        lookup(client)
    assert len(server.requests) == 1


def test_check_budget_runs_before_every_openalex_attempt(
    clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    checked: list[str] = []
    monkeypatch.setattr(ledger, "check_budget", checked.append)
    client = make_client(clock, Server(clock, resp(503), resp(200)))
    lookup(client)
    lookup(client, FEED, "arxiv")
    client.request("GET", ARXIV_API, provider="arxiv", call_type="query")
    assert checked == ["openalex", "openalex"]


# ------------------------------------------------------------------------------------------------
# Semantic Scholar breaker
# ------------------------------------------------------------------------------------------------


def test_keyless_s2_429_opens_the_breaker_without_retry(
    clock: FakeClock, set_env: Callable[..., None]
) -> None:
    set_env(RADAR_S2_BREAKER_MINUTES="2")
    server = Server(clock, resp(429, headers={"Retry-After": "1"}), resp(200, json=[None]))
    client = make_client(clock, server)
    body = {"ids": ["ARXIV:2504.08385"]}
    with pytest.raises(SourceUnavailable) as info:
        client.request("POST", S2_BATCH, provider="semantic_scholar", call_type="batch", json=body)
    assert (info.value.provider, info.value.status, info.value.retry_after) == (
        "semantic_scholar",
        429,
        120.0,
    )
    assert len(server.requests) == 1 and clock.sleeps == []
    assert client.last_status("semantic_scholar") == 429
    until = client.breaker_until()
    assert until is not None and until.tzinfo is not None
    assert timedelta(seconds=110) < until - datetime.now(UTC) <= timedelta(seconds=120)

    clock.advance(119)
    with pytest.raises(SourceUnavailable) as blocked:  # breaker open: raised immediately, nothing sent
        client.request("GET", S2_REFS, provider="semantic_scholar", call_type="query", params={"limit": 100})
    assert blocked.value.status is None and blocked.value.retry_after == pytest.approx(1.0)
    assert "https://" not in leak_text(blocked.value)
    assert len(server.requests) == 1
    assert lookup(client, FEED, "arxiv").status_code == 200  # other providers are unaffected

    clock.advance(1.5)
    response = client.request("POST", S2_BATCH, provider="semantic_scholar", call_type="batch", json=body)
    assert response.json() == [None]
    assert client.breaker_until() is None
    assert [row.status_code for row in ledger_rows() if row.provider == "semantic_scholar"] == [429, 200]


def test_s2_breaker_defaults_to_15_minutes(clock: FakeClock) -> None:
    client = make_client(clock, Server(clock, resp(429)))
    with pytest.raises(SourceUnavailable) as info:
        client.request("GET", S2_REFS, provider="semantic_scholar", call_type="query")
    assert info.value.retry_after == 900.0
    clock.advance(899)
    with pytest.raises(SourceUnavailable):
        client.request("GET", S2_REFS, provider="semantic_scholar", call_type="query")
    assert client.breaker_until() is not None


def test_keyed_s2_429_is_retried_normally(clock: FakeClock, set_env: Callable[..., None]) -> None:
    set_env(S2_API_KEY=SECRET_S2_KEY)
    server = Server(clock, resp(429), resp(429, headers={"Retry-After": "2"}), resp(200, json=[None]))
    client = make_client(clock, server)
    response = client.request(
        "POST", S2_BATCH, provider="semantic_scholar", call_type="batch", json={"ids": ["ARXIV:2504.08385"]}
    )
    assert response.status_code == 200
    assert clock.sleeps == [2.0, 6.0]
    assert client.breaker_until() is None
    assert [request.headers["x-api-key"] for request in server.requests] == [SECRET_S2_KEY] * 3


# ------------------------------------------------------------------------------------------------
# Ledger rows and call attribution
# ------------------------------------------------------------------------------------------------


def test_every_attempt_is_recorded_with_path_only_and_its_cost(clock: FakeClock) -> None:
    oa_headers = {
        "x-ratelimit-limit-usd": "0.1",
        "x-ratelimit-remaining-usd": "0.0999",
        "x-ratelimit-remaining": "999",
        "x-ratelimit-reset": "43200",
        "x-ratelimit-cost-usd": "0",
    }
    server = Server(
        clock,
        resp(200, json={}, headers=oa_headers),
        resp(200, json={}, headers=oa_headers),
        resp(404),
        resp(200),
        resp(200),
    )
    client = make_client(clock, server)
    lookup(client, params={"select": "id,title"})
    client.request(
        "GET",
        OPENALEX_LIST + "?filter=cites:W2741809807",
        provider="openalex",
        call_type="list",
        params={"per-page": 3},
    )
    lookup(client, GITHUB_USER, "github")
    client.request("GET", ARXIV_API, provider="arxiv", call_type="query", params={"id_list": "2504.08385"})
    client.request("GET", ARXIV_FEED, provider="arxiv", call_type="feed")
    rows = ledger_rows()
    assert [(r.provider, r.call_type, r.endpoint, r.status_code) for r in rows] == [
        ("openalex", "lookup", "/works/W2741809807", 200),
        ("openalex", "list", "/works", 200),
        ("github", "lookup", "/user", 404),
        ("arxiv", "query", "/api/query", 200),
        ("arxiv", "feed", "/atom/cs.IR+cs.DL", 200),
    ]
    assert [(r.unit_cost, r.cost_usd) for r in rows] == [
        (0.0, 0.0),
        (0.0001, 0.0001),
        (0.0, 0.0),
        (0.0, 0.0),
        (0.0, 0.0),
    ]
    for row in rows:
        assert "?" not in row.endpoint and "select" not in row.endpoint and "id_list" not in row.endpoint
        assert (row.units, row.job_id, row.project_id) == (1.0, None, None)
        assert row.timestamp.tzinfo is not None
    expected_meta = {
        "limit_usd": 0.1,
        "remaining_usd": 0.0999,
        "remaining_requests": 999,
        "reset_seconds": 43200,
    }
    assert rows[0].meta_json == expected_meta and rows[1].meta_json == expected_meta
    assert all(row.meta_json == {} for row in rows[2:])
    assert ledger.spent_today("openalex") == pytest.approx(0.0001)


def test_failed_attempts_are_recorded_but_not_charged(clock: FakeClock) -> None:
    client = make_client(clock, Server(clock, resp(500), resp(429), refuse, resp(200, json={})))
    openalex_list(client)
    rows = ledger_rows()
    assert [(r.status_code, r.unit_cost, r.cost_usd) for r in rows] == [
        (500, 0.0001, 0.0),
        (429, 0.0001, 0.0),
        (None, 0.0001, 0.0),
        (200, 0.0001, 0.0001),
    ]


def test_call_context_attributes_requests_made_in_a_worker_thread(clock: FakeClock) -> None:
    client = make_client(clock, Server(clock))

    def job() -> None:
        with call_context(41, 7):
            lookup(client, FEED, "arxiv")
            lookup(client, FEED, "arxiv", job_id=99)
            with call_context(42, None):
                lookup(client, FEED, "arxiv")
            lookup(client, FEED, "arxiv", project_id=8)
        lookup(client, FEED, "arxiv")

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(job).result()
    with call_context(5, 6):
        lookup(client, FEED, "arxiv")
        other = threading.Thread(target=lookup, args=(client, FEED, "arxiv"))
        other.start()  # context variables do not leak into other threads
        other.join()
    lookup(client, FEED, "arxiv")
    assert [(row.job_id, row.project_id) for row in ledger_rows()] == [
        (41, 7),
        (99, 7),
        (42, None),
        (41, 8),
        (None, None),
        (5, 6),
        (None, None),
        (None, None),
    ]


# ------------------------------------------------------------------------------------------------
# Headers, credentials and secrets
# ------------------------------------------------------------------------------------------------


def test_default_requests_carry_no_credentials(clock: FakeClock) -> None:
    server = Server(clock)
    client = make_client(clock, server)
    lookup(client, params={"select": "id"})
    lookup(client, FEED, "arxiv")
    client.request("POST", S2_BATCH, provider="semantic_scholar", call_type="batch", json={"ids": []})
    for request in server.requests:
        assert request.headers["user-agent"] == "Ratiocination/0.1 (local research tool)"
        assert "x-api-key" not in request.headers
        assert "api_key" not in request.url.params and "mailto" not in request.url.params
    assert server.requests[0].url.params.multi_items() == [("select", "id")]


def test_credentials_are_attached_per_provider_and_caller_values_dropped(
    clock: FakeClock, set_env: Callable[..., None]
) -> None:
    set_env(OPENALEX_API_KEY=SECRET_KEY, S2_API_KEY=SECRET_S2_KEY, RADAR_CONTACT_EMAIL=SECRET_EMAIL)
    server = Server(clock)
    client = make_client(clock, server)
    caller_headers = {
        "X-Request-ID": "req-123",
        "User-Agent": "spoofed",
        "x-api-key": "caller-key",
        "Authorization": "Bearer caller",
        "Cookie": "a=b",
        "Accept": "application/json",
    }
    caller_params = {"select": "id", "api_key": "caller-key", "MAILTO": "caller@example.org"}
    lookup(client, OPENALEX_WORK + "?mailto=url@example.org", params=caller_params, headers=caller_headers)
    lookup(client, FEED, "arxiv", params=caller_params, headers=caller_headers)
    client.request(
        "GET",
        ARXIV_API,
        provider="arxiv",
        call_type="query",
        params={"id_list": "2504.08385", "api_key": "caller-key"},
        headers=caller_headers,
    )
    client.request(
        "POST",
        S2_BATCH,
        provider="semantic_scholar",
        call_type="batch",
        params={"fields": "title"},
        json={"ids": ["ARXIV:2504.08385"]},
        headers=caller_headers,
    )
    openalex, feed, arxiv, s2 = server.requests
    for request in server.requests:
        assert (
            request.headers["user-agent"] == f"Ratiocination/0.1 (local research tool; mailto:{SECRET_EMAIL})"
        )
        assert request.headers["accept"] == "application/json"
        for dropped in ("x-request-id", "authorization", "cookie"):
            assert dropped not in request.headers
    assert openalex.url.params.multi_items() == [
        ("select", "id"),
        ("api_key", SECRET_KEY),
        ("mailto", SECRET_EMAIL),
    ]
    assert feed.url.params.multi_items() == [("select", "id")]  # only OpenAlex gets mailto
    assert arxiv.url.params.multi_items() == [("id_list", "2504.08385")]
    assert s2.url.params.multi_items() == [("fields", "title")]
    assert s2.headers["x-api-key"] == SECRET_S2_KEY
    assert all("x-api-key" not in request.headers for request in (openalex, feed, arxiv))
    assert s2.read() == b'{"ids":["ARXIV:2504.08385"]}'


def test_a_malformed_contact_email_is_never_sent(clock: FakeClock, set_env: Callable[..., None]) -> None:
    set_env(RADAR_CONTACT_EMAIL="someone at example dot org\r\nX-Injected: 1")
    server = Server(clock)
    lookup(make_client(clock, server), OPENALEX_WORK, "openalex")
    (request,) = server.requests
    assert request.headers["user-agent"] == "Ratiocination/0.1 (local research tool)"
    assert "mailto" not in request.url.params and "x-injected" not in request.headers


def test_provider_cookies_are_never_stored_or_sent(clock: FakeClock) -> None:
    server = Server(clock, resp(200, json={}, headers={"Set-Cookie": "track=1; Path=/"}))
    client = make_client(clock, server)
    lookup(client)
    lookup(client)
    assert all("cookie" not in request.headers for request in server.requests)


def test_secrets_never_reach_exceptions_ledger_rows_or_logs(
    clock: FakeClock, set_env: Callable[..., None], caplog: pytest.LogCaptureFixture
) -> None:
    set_env(OPENALEX_API_KEY=SECRET_KEY, RADAR_CONTACT_EMAIL=SECRET_EMAIL, S2_API_KEY=SECRET_S2_KEY)
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="httpx")  # even httpx's own request lines (full URLs)
    caplog.set_level(logging.DEBUG, logger="httpcore")

    def overloaded(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text=f"overloaded while serving {request.url}")

    exhausted = resp(429, headers={"x-ratelimit-limit-usd": "1", "x-ratelimit-remaining-usd": "0"})
    sent: list[httpx.Request] = []
    errors: list[BaseException] = []
    scenarios: list[tuple[Server, Callable[[HttpClient], object]]] = [
        (Server(clock, overloaded), lambda c: lookup(c, params={"select": "id"})),
        (Server(clock, refuse), openalex_list),
        (Server(clock, refuse), lambda c: lookup(c, FEED, "arxiv")),
        (
            Server(clock, overloaded),
            lambda c: c.request("GET", S2_REFS, provider="semantic_scholar", call_type="query"),
        ),
        (Server(clock, refuse, exhausted), openalex_list),
        (Server(clock), lookup),  # stopped by the budget check: nothing is sent
    ]
    for server, call in scenarios:
        with pytest.raises((SourceUnavailable, BudgetExceeded)) as info:
            call(make_client(clock, server))
        errors.append(info.value)
        sent.extend(server.requests)
    assert [type(e).__name__ for e in errors] == ["SourceUnavailable"] * 4 + ["BudgetExceeded"] * 2

    # The credentials really were sent to the providers (so the checks below are meaningful) ...
    openalex_requests = [r for r in sent if r.url.host == "api.openalex.org"]
    assert openalex_requests and all(r.url.params["api_key"] == SECRET_KEY for r in openalex_requests)
    assert all(r.url.params["mailto"] == SECRET_EMAIL for r in openalex_requests)
    assert all(SECRET_EMAIL in r.headers["user-agent"] for r in sent)
    assert all(
        r.headers["x-api-key"] == SECRET_S2_KEY for r in sent if r.url.host == "api.semanticscholar.org"
    )

    # ... but never reach exception text, ledger rows or logs.
    secrets = [s.lower() for s in (SECRET_KEY, SECRET_EMAIL, "zq%40example.invalid", SECRET_S2_KEY)]
    for exc in errors:
        text = leak_text(exc)
        assert not [s for s in secrets if s in text], text
        assert "https://" not in text
    rows = ledger_rows()
    assert len(rows) == 4 + 4 + 4 + 4 + 2
    columns = [column.name for column in models.CostLedger.__table__.columns]
    for row in rows:
        row_text = " ".join(str(getattr(row, name)) for name in columns).lower()
        assert not [s for s in secrets if s in row_text], row_text
    assert any(record.name == "radar.net.http" for record in caplog.records)
    assert any(record.name == "httpx" for record in caplog.records)
    log_text = (caplog.text + " ".join(record.getMessage() for record in caplog.records)).lower()
    assert not [s for s in secrets if s in log_text]


# ------------------------------------------------------------------------------------------------
# Process-wide client
# ------------------------------------------------------------------------------------------------


def test_get_http_shares_one_client_and_set_http_replaces_it() -> None:
    first = get_http()
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert set(map(id, pool.map(lambda _: get_http(), range(16)))) == {id(first)}
    custom = HttpClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    set_http(custom)
    assert get_http() is custom
    set_http(None)
    fresh = get_http()
    assert fresh is not custom and fresh is not first and fresh.transport is None


def test_rate_limit_meta_parsing() -> None:
    full = httpx.Headers(
        {
            "x-ratelimit-limit-usd": "0.1",
            "x-ratelimit-remaining-usd": "0.0371",
            "x-ratelimit-remaining": "371",
            "x-ratelimit-reset": "5021.5",
            "x-ratelimit-limit": "1000",
        }
    )
    assert net_http.openalex_rate_limit_meta(full) == {
        "limit_usd": 0.1,
        "remaining_usd": 0.0371,
        "remaining_requests": 371,
        "reset_seconds": 5021.5,
    }
    partial = httpx.Headers({"x-ratelimit-remaining-usd": "0", "x-ratelimit-remaining": "lots"})
    assert net_http.openalex_rate_limit_meta(partial) == {
        "limit_usd": None,
        "remaining_usd": 0.0,
        "remaining_requests": None,
        "reset_seconds": None,
    }
    assert net_http.openalex_rate_limit_meta(httpx.Headers({"content-type": "application/json"})) == {}


# ------------------------------------------------------------------------------------------------
# v0.3: sign-in and AI providers (form bodies, bearer tokens, the Anthropic key, 529)
# ------------------------------------------------------------------------------------------------


def test_form_bodies_are_sent_form_encoded_and_never_recorded(
    clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    server = Server(clock, resp(200, json={"id_token": "x.y.z"}))
    client = make_client(clock, server)
    form = {"code": "zqcode123", "client_secret": "zqclientsecret", "grant_type": "authorization_code"}
    client.request("POST", GOOGLE_TOKEN, provider="google", call_type="auth", data=form)
    (request,) = server.requests
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert request.read() == b"code=zqcode123&client_secret=zqclientsecret&grant_type=authorization_code"
    assert request.headers["user-agent"] == "Ratiocination/0.1 (local research tool)"
    (row,) = ledger_rows()
    assert (row.provider, row.call_type, row.endpoint, row.cost_usd) == ("google", "auth", "/token", 0.0)
    assert "zqcode123" not in caplog.text and "zqclientsecret" not in caplog.text
    with pytest.raises(ValueError):
        client.request("POST", GOOGLE_TOKEN, provider="google", call_type="auth", data=form, json={})


def test_bearer_tokens_go_to_github_only(clock: FakeClock, set_env: Callable[..., None]) -> None:
    set_env(RADAR_CONTACT_EMAIL=SECRET_EMAIL)
    server = Server(clock)
    client = make_client(clock, server)
    client.request(
        "GET",
        GITHUB_USER,
        provider="github",
        call_type="auth",
        bearer="gho_zqtoken",
        headers={"Authorization": "Bearer spoofed", "Accept": "application/vnd.github+json"},
    )
    (request,) = server.requests
    assert request.headers["authorization"] == "Bearer gho_zqtoken"  # the caller's header is dropped
    assert request.headers["accept"] == "application/vnd.github+json"
    assert SECRET_EMAIL not in request.headers["user-agent"]  # sign-in calls carry no contact address
    for provider, url in (
        ("openalex", OPENALEX_WORK),
        ("google", GOOGLE_TOKEN),
        ("anthropic", ANTHROPIC_MESSAGES),
    ):
        with pytest.raises(ValueError):
            client.request("GET", url, provider=provider, call_type="lookup", bearer="gho_zqtoken")
    assert len(server.requests) == 1


def test_the_anthropic_key_is_attached_by_the_client(clock: FakeClock, set_env: Callable[..., None]) -> None:
    set_env(ANTHROPIC_API_KEY="sk-ant-zqkey")
    server = Server(clock, resp(529), resp(200, json={"content": []}))
    client = make_client(clock, server)
    response = client.request(
        "POST",
        ANTHROPIC_MESSAGES,
        provider="anthropic",
        call_type="generate",
        json={"model": "claude-opus-5-5"},
        headers={"x-api-key": "caller", "anthropic-version": "2023-06-01"},
        timeout=120.0,
    )
    assert response.status_code == 200 and clock.sleeps == [2.0]  # 529 (overloaded) is retried
    assert [r.headers["x-api-key"] for r in server.requests] == ["sk-ant-zqkey", "sk-ant-zqkey"]
    assert server.requests[0].headers["anthropic-version"] == "2023-06-01"
    rows = ledger_rows()
    assert [(r.provider, r.call_type, r.status_code) for r in rows] == [
        ("anthropic", "generate", 529),
        ("anthropic", "generate", 200),
    ]
