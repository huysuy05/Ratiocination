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
from radar.net.http import SourceUnavailable

FEED_RE = re.compile(r"^https://rss\.arxiv\.org/atom/")
OAI_URL = "https://oaipmh.arxiv.org/oai"
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


def test_backfill_needs_a_project_with_categories(make_project):
    with pytest.raises(ValueError):
        run_backfill(JobContext())
    project_id = make_project()
    assert run_backfill(JobContext(project_id=project_id))["skipped"]
