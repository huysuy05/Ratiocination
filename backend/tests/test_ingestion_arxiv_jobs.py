"""Jobs ``ingest_feeds`` and ``backfill`` (SPEC §8) with mocked arXiv responses."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from radar.db import session_scope
from radar.ingestion import backfill as backfill_module
from radar.ingestion.backfill import run_backfill
from radar.ingestion.feeds import run_ingest_feeds
from radar.jobs.runner import JobContext, JobFailed, run_job_sync
from radar.models import IngestionWatermark, Job, Paper, SourceRecord
from radar.net.http import EgressBlocked, SourceUnavailable

FEED_RE = re.compile(r"^https://rss\.arxiv\.org/atom/")
OAI_URL = "https://oaipmh.arxiv.org/oai"
API_URL = "https://export.arxiv.org/api/query"
FIXED_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)  # the fixture day; keeps window tests stable


def _counts() -> tuple[int, int]:
    with session_scope() as s:
        return s.query(Paper).count(), s.query(SourceRecord).count()


def test_ingest_feeds_upserts_and_skips_unchanged_feed(http_mock, load_fixture, make_project):
    make_project(settings={"arxiv_categories": ["cs.IR", "cs.DL", "cs.CL"]})
    make_project(settings={"arxiv_categories": ["cs.IR"]}, active=False)  # inactive: not read
    xml = load_fixture("arxiv_feed_multi.xml")
    http_mock.add("GET", FEED_RE, xml)

    stats = run_job_sync("ingest_feeds")  # through the registry and the runner
    assert stats["feeds"] == 1 and stats["created"] == 7 and stats["entries"] == 7
    assert str(http_mock.requests[0].url) == "https://rss.arxiv.org/atom/cs.CL+cs.DL+cs.IR"
    assert _counts() == (7, 7)
    with session_scope() as s:
        mark = s.query(IngestionWatermark).one()
        assert (mark.source, mark.query_key) == ("arxiv_feed", "cs.CL+cs.DL+cs.IR")

    again = run_job_sync("ingest_feeds")
    assert again["unchanged_feeds"] == 1 and again["records"] == 0
    assert _counts() == (7, 7)

    http_mock.routes.clear()
    http_mock.add("GET", FEED_RE, xml.replace(b"2026-10-12T21:02:12", b"2026-10-13T04:00:00"))
    regenerated = run_job_sync("ingest_feeds")
    assert regenerated["records"] == 7 and regenerated["created"] == 0
    assert _counts() == (7, 7)  # idempotent: same entries, same payload hashes


def test_ingest_feeds_chunks_more_than_ten_categories(http_mock, load_fixture, make_project):
    cats = [f"cs.{c}" for c in ("AI", "CL", "CV", "DB", "DL", "DS", "GT", "HC", "IR", "LG", "NE", "RO")]
    make_project(settings={"arxiv_categories": cats})
    http_mock.add("GET", FEED_RE, load_fixture("arxiv_feed_empty.xml"))
    stats = run_ingest_feeds(JobContext())
    assert stats["feeds"] == 2 and len(http_mock.requests) == 2
    assert [len(str(r.url).rsplit("/", 1)[1].split("+")) for r in http_mock.requests] == [10, 2]


def test_ingest_feeds_without_categories_is_skipped(make_project):
    make_project()
    assert run_ingest_feeds(JobContext())["skipped"]


def test_one_failed_feed_does_not_stop_the_others(http_mock, load_fixture, make_project):
    cats = [f"cs.{c}" for c in ("AI", "CL", "CV", "DB", "DL", "DS", "GT", "HC", "IR", "LG", "NE")]
    make_project(settings={"arxiv_categories": cats})
    http_mock.add("GET", re.compile(r"cs\.NE$"), "boom", status=503)
    http_mock.add("GET", FEED_RE, load_fixture("arxiv_feed_multi.xml"))
    stats = run_ingest_feeds(JobContext())
    assert stats["failed_feeds"] == 1 and stats["created"] == 7


def test_all_feeds_failing_fails_the_job(http_mock, make_project):
    make_project(settings={"arxiv_categories": ["cs.IR"]})
    http_mock.add("GET", FEED_RE, "boom", status=503)
    with pytest.raises(SourceUnavailable):
        run_ingest_feeds(JobContext())
    with pytest.raises(JobFailed) as info:
        run_job_sync("ingest_feeds")
    assert info.value.error_code == "source_unavailable"
    with session_scope() as s:
        job = s.query(Job).order_by(Job.id.desc()).first()
        assert job.status == "failed" and job.error_code == "source_unavailable"
        assert "http" not in (job.error_message or "")  # no URLs in stored messages


def _last_page(load_fixture) -> bytes:
    """The real OAI page without its resumption token (so a harvest stops after one page)."""
    xml = load_fixture("arxiv_oai_raw_page.xml")
    return xml.split(b"<resumptionToken")[0] + b"</ListRecords>\n</OAI-PMH>\n"


def test_backfill_harvests_sets_and_keeps_recent_papers_in_project_categories(
    http_mock, load_fixture, make_project, monkeypatch
):
    monkeypatch.setattr(backfill_module, "utcnow", lambda: FIXED_NOW)
    project_id = make_project(settings={"arxiv_categories": ["cs.SD", "cs.HC"], "time_window_days": 14})
    http_mock.add("GET", OAI_URL, _last_page(load_fixture))
    stats = run_job_sync("backfill", project_id=project_id)
    # One OAI set (cs) covers both categories. Of the 4 records only 2610.11099 (cs.SD, v1 2026-10-08)
    # is both recent and in the project's categories; 2601.00765 (cs.HC) has a January v1 date.
    assert (stats["sets"], stats["pages"], stats["scanned"]) == (1, 1, 4)
    assert stats["records"] == 1 and stats["created"] == 1
    assert (stats["since"], stats["until"]) == ("2026-09-28", "2026-10-12")
    query = parse_qs(urlsplit(str(http_mock.requests[0].url)).query)
    assert query["set"] == ["cs"] and query["from"] == ["2026-09-28"] and query["until"] == ["2026-10-12"]
    with session_scope() as s:
        paper = s.query(Paper).one()
        assert paper.canonical_title.startswith("Cross-Lingual Speaker Verification")
        assert paper.ingested_via == "arxiv_oai"


def test_backfill_harvests_one_set_per_group(http_mock, load_fixture, make_project, monkeypatch):
    monkeypatch.setattr(backfill_module, "utcnow", lambda: FIXED_NOW)
    project_id = make_project(settings={"arxiv_categories": ["cs.SD", "eess.AS", "hep-th"]})
    http_mock.add("GET", OAI_URL, _last_page(load_fixture))
    stats = run_backfill(JobContext(project_id=project_id))
    sets = [parse_qs(urlsplit(str(r.url)).query)["set"][0] for r in http_mock.requests]
    assert sets == ["cs", "eess", "physics:hep-th"] and stats["sets"] == 3


def test_backfill_fails_only_when_every_set_fails(http_mock, load_fixture, make_project, monkeypatch):
    monkeypatch.setattr(backfill_module, "utcnow", lambda: FIXED_NOW)
    project_id = make_project(settings={"arxiv_categories": ["cs.SD", "eess.AS"]})
    http_mock.add("GET", re.compile(r"set=eess"), "busy", status=404)
    http_mock.add("GET", OAI_URL, _last_page(load_fixture))
    stats = run_backfill(JobContext(project_id=project_id))
    assert stats["failed_sets"] == 1 and stats["created"] == 1
    http_mock.routes.clear()
    http_mock.add("GET", OAI_URL, "busy", status=404)
    with pytest.raises(SourceUnavailable):
        run_backfill(JobContext(project_id=project_id))


def _api_query(request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(str(request.url)).query)


def test_backfill_falls_back_to_the_arxiv_api_when_oai_is_throttled(
    http_mock, load_fixture, make_project, monkeypatch, caplog
):
    """Production 2026-10-14: OAI-PMH hangs from the Fly machine while the query API answers in 3.6 s."""
    monkeypatch.setattr(backfill_module, "utcnow", lambda: FIXED_NOW)
    project_id = make_project(settings={"arxiv_categories": ["cs.SD"], "time_window_days": 14})
    http_mock.add("GET", OAI_URL, "throttled", status=503)
    http_mock.add("GET", API_URL, load_fixture("arxiv_api_query.xml"))

    with caplog.at_level("WARNING"):
        stats = run_job_sync("backfill", project_id=project_id)

    assert stats["source"] == "arxiv_api" and stats["failed_sets"] == 1 and stats["pages"] == 0
    assert stats["api_categories"] == 1 and stats["api_queries"] == 1
    assert stats["records"] == 3 and stats["api_records"] == 3 and stats["created"] == 3
    assert any("falling back to the arXiv query API" in r.getMessage() for r in caplog.records)
    assert len(http_mock.calls(url_or_regex=OAI_URL)) == 2  # one attempt and one retry, not four
    query = _api_query(http_mock.calls(url_or_regex=API_URL)[0])
    assert query["search_query"] == ["cat:cs.SD AND submittedDate:[202609280000 TO 202610122359]"]
    assert query["max_results"] == ["200"] and query["start"] == ["0"]
    with session_scope() as s:
        job = s.query(Job).order_by(Job.id.desc()).first()
        assert job.status == "succeeded" and "arXiv API" in (job.message or "")
    assert _counts()[0] == 3


def test_backfill_uses_the_api_only_for_the_sets_that_failed(
    http_mock, load_fixture, make_project, monkeypatch
):
    monkeypatch.setattr(backfill_module, "utcnow", lambda: FIXED_NOW)
    project_id = make_project(settings={"arxiv_categories": ["cs.SD", "eess.AS"]})
    http_mock.add("GET", re.compile(r"set=eess"), "throttled", status=503)
    http_mock.add("GET", OAI_URL, _last_page(load_fixture))
    http_mock.add("GET", API_URL, load_fixture("arxiv_api_query.xml"))

    stats = run_backfill(JobContext(project_id=project_id))
    assert stats["source"] == "arxiv_oai+arxiv_api" and stats["failed_sets"] == 1
    assert stats["api_categories"] == 1 and stats["records"] == 4  # 1 from OAI (cs) + 3 from the API
    queries = [_api_query(r)["search_query"][0] for r in http_mock.calls(url_or_regex=API_URL)]
    assert queries == ["cat:eess.AS AND submittedDate:[202609280000 TO 202610122359]"]


def test_backfill_fails_source_unavailable_when_both_sources_fail(http_mock, make_project, monkeypatch):
    monkeypatch.setattr(backfill_module, "utcnow", lambda: FIXED_NOW)
    project_id = make_project(settings={"arxiv_categories": ["cs.SD"]})
    http_mock.add("GET", OAI_URL, "throttled", status=503)
    http_mock.add("GET", API_URL, "busy", status=503)

    with pytest.raises(SourceUnavailable):
        run_backfill(JobContext(project_id=project_id))
    with pytest.raises(JobFailed) as info:
        run_job_sync("backfill", project_id=project_id)
    assert info.value.error_code == "source_unavailable"
    assert "arXiv API" in info.value.error_message and "http" not in info.value.error_message
    with session_scope() as s:
        job = s.query(Job).order_by(Job.id.desc()).first()
        assert job.stats_json["api_failed"] is True and job.stats_json["failed_sets"] == 1


def test_backfill_in_offline_mode_reaches_neither_source(make_project, monkeypatch):
    """RADAR_OFFLINE=1 (the test default) blocks the harvest before any fallback is considered."""
    monkeypatch.setattr(backfill_module, "utcnow", lambda: FIXED_NOW)

    def never(*args, **kwargs):
        raise AssertionError("the arXiv API must not be called while offline")

    monkeypatch.setattr(backfill_module, "query_recent", never)
    project_id = make_project(settings={"arxiv_categories": ["cs.SD"]})
    with pytest.raises(EgressBlocked):
        run_backfill(JobContext(project_id=project_id))


def test_backfill_needs_a_project_with_categories(make_project):
    with pytest.raises(ValueError):
        run_backfill(JobContext())
    project_id = make_project()
    assert run_backfill(JobContext(project_id=project_id))["skipped"]
