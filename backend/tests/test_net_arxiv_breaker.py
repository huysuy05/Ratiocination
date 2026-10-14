"""arXiv API cool-down after HTTP 429 (2026-10-12 live finding; DECISIONS.md)."""

from __future__ import annotations

import pytest

from radar.net.http import ARXIV_API_HOST, HttpClient, SourceUnavailable

API = f"https://{ARXIV_API_HOST}/api/query"
FEED = "https://rss.arxiv.org/atom/cs.IR"
OAI = "https://oaipmh.arxiv.org/oai"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def client(http_mock):
    clock = Clock()
    http_mock.add("GET", API, "<feed/>", status=429)
    http_mock.add("GET", FEED, "<feed/>")
    http_mock.add("GET", OAI, "<OAI-PMH/>")
    return HttpClient(transport=http_mock.transport, sleep=lambda s: None, monotonic=clock), clock


def test_api_429_fails_fast_and_pauses_only_the_api_host(client, http_mock):
    http, clock = client
    with pytest.raises(SourceUnavailable) as info:
        http.request("GET", API, provider="arxiv", call_type="query")
    assert info.value.status == 429
    assert "arXiv API rate limit" in str(info.value) and "http" not in str(info.value)
    assert len(http_mock.requests) == 1  # no retries against a throttling server

    with pytest.raises(SourceUnavailable) as again:
        http.request("GET", API, provider="arxiv", call_type="query")
    assert "skipped" in str(again.value)
    assert len(http_mock.requests) == 1  # blocked before sending

    assert http.request("GET", FEED, provider="arxiv", call_type="feed").status_code == 200
    assert http.request("GET", OAI, provider="arxiv", call_type="list").status_code == 200

    clock.now += 10 * 60 + 1  # default RADAR_ARXIV_BREAKER_MINUTES = 10
    with pytest.raises(SourceUnavailable):
        http.request("GET", API, provider="arxiv", call_type="query")
    assert sum(1 for r in http_mock.requests if r.url.host == ARXIV_API_HOST) == 2


def test_breaker_minutes_is_configurable(http_mock, monkeypatch):
    from radar.config import get_settings

    monkeypatch.setenv("RADAR_ARXIV_BREAKER_MINUTES", "0")
    get_settings.cache_clear()
    http_mock.add("GET", API, "<feed/>", status=429)
    http = HttpClient(transport=http_mock.transport, sleep=lambda s: None)
    for _ in range(2):
        with pytest.raises(SourceUnavailable):
            http.request("GET", API, provider="arxiv", call_type="query")
    assert len(http_mock.requests) == 2  # a zero-minute pause never blocks
