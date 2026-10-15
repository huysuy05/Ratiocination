"""arXiv feed + API connectors (SPEC §7) against the real 2026-10-12 fixtures."""

from __future__ import annotations

from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from radar.connectors._atom import collapse, detex, split_creators
from radar.connectors.arxiv_api import parse_api_response, query_by_ids, query_recent
from radar.connectors.arxiv_feed import fetch_feed, parse_feed
from radar.db import flush_writes, session_scope
from radar.models import CostLedger, Paper, PaperVersion, SourceRecord
from radar.net.http import SourceUnavailable
from radar.store.papers import payload_hash, upsert_paper

FEED_URL = "https://rss.arxiv.org/atom/cs.IR+cs.DL+cs.CL"
API_URL = "https://export.arxiv.org/api/query"


# ------------------------------------------------------------------------------------------------ feed


def test_parse_feed_reads_every_announce_type(load_fixture):
    result = parse_feed(load_fixture("arxiv_feed_multi.xml"))
    assert result.raw_count == 7 and len(result.records) == 7
    assert result.updated == "2026-10-12T21:02:12.365840+00:00"
    by_id = {r.source_id: r for r in result.records}
    assert [r.announce_type for r in result.records] == [
        "new",
        "new",
        "cross",
        "cross",
        "replace",
        "replace",
        "replace-cross",
    ]
    new = by_id["2610.12545v1"]
    assert new.source == "arxiv_feed"
    assert new.identifiers == {"arxiv": "2610.12545"}
    assert new.version_label == "v1"
    assert new.title.startswith("AutoIndexer")
    assert new.abstract.startswith("Autoregressive decoding")  # "arXiv:… Announce Type … Abstract:" stripped
    assert "Announce Type" not in new.abstract
    assert new.primary_category == "cs.CL" and new.categories[:2] == ["cs.CL", "cs.AI"]
    assert new.authors[0] == "Shu Ishida" and len(new.authors) == 6
    assert new.publication_date == date(2026, 10, 12)
    assert new.license == "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"
    assert new.landing_url == "https://arxiv.org/abs/2610.12545"
    assert new.pdf_url == "https://arxiv.org/pdf/2610.12545v1"
    replaced = by_id["2405.01930v3"]
    assert replaced.announce_type == "replace" and replaced.version_label == "v3"


def test_parse_empty_feed_is_valid(load_fixture):
    result = parse_feed(load_fixture("arxiv_feed_empty.xml"))
    assert result.records == [] and result.raw_count == 0
    assert result.updated is not None


def test_feed_payload_is_stable_across_regenerations(load_fixture):
    """The stored payload excludes feed-generation timestamps, so re-reading is idempotent."""
    xml = load_fixture("arxiv_feed_multi.xml")
    regenerated = xml.replace(b"2026-10-12T21:02:12", b"2026-10-13T04:00:00")
    first = parse_feed(xml).records
    second = parse_feed(regenerated).records
    assert [payload_hash(r.raw) for r in first] == [payload_hash(r.raw) for r in second]


def test_malformed_feed_raises_value_error():
    with pytest.raises(ValueError):
        parse_feed(b"<feed><entry>")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Han Luo, Zhan Zhuang, Xu He", ["Han Luo", "Zhan Zhuang", "Xu He"]),
        ("Alice Smith and Bob Jones", ["Alice Smith", "Bob Jones"]),
        ("A. Author, B. Author, and C. Author", ["A. Author", "B. Author", "C. Author"]),
        ("John Smith, Jr., Mary Major", ["John Smith, Jr.", "Mary Major"]),
        ("Grace Anderson", ["Grace Anderson"]),
        ("", []),
    ],
)
def test_split_creators(raw, expected):
    assert split_creators(raw) == expected


def test_fetch_feed_builds_url_and_records_ledger(http_mock, load_fixture):
    http_mock.add("GET", FEED_URL, load_fixture("arxiv_feed_multi.xml"))
    result = fetch_feed(["cs.IR", "cs.DL", "cs.CL", "cs.IR"], job_id=None)
    assert len(result.records) == 7
    assert str(http_mock.requests[0].url) == FEED_URL  # duplicates dropped, order kept
    flush_writes()
    with session_scope() as s:
        row = s.query(CostLedger).one()
        assert (row.provider, row.call_type, row.endpoint, row.cost_usd) == (
            "arxiv",
            "feed",
            "/atom/cs.IR+cs.DL+cs.CL",
            0,
        )


def test_fetch_feed_rejects_bad_input():
    with pytest.raises(ValueError):
        fetch_feed([])
    with pytest.raises(ValueError):
        fetch_feed([f"cs.X{i}" for i in range(11)])
    with pytest.raises(ValueError):
        fetch_feed(["cs.IR; DROP"])


def test_fetch_feed_non_200_is_source_unavailable(http_mock):
    http_mock.add("GET", "https://rss.arxiv.org/atom/cs.IR", "not found", status=404)
    with pytest.raises(SourceUnavailable) as info:
        fetch_feed(["cs.IR"])
    assert info.value.provider == "arxiv" and info.value.status == 404


# ------------------------------------------------------------------------------------------------ API


def test_parse_api_response(load_fixture):
    records = {r.source_id: r for r in parse_api_response(load_fixture("arxiv_api_query.xml"))}
    assert set(records) == {"1706.03762v7", "2504.08385v2", "hep-th/9901001v3"}
    attention = records["1706.03762v7"]
    assert attention.source == "arxiv_api"
    assert attention.title == "Attention Is All You Need"
    assert attention.publication_date == date(2017, 6, 12)  # v1 date
    assert attention.source_updated_at.isoformat() == "2023-08-02T00:41:18+00:00"  # this version
    assert attention.primary_category == "cs.CL" and attention.categories[:2] == ["cs.CL", "cs.LG"]
    assert len(attention.authors) == 8 and attention.authors[0] == "Ashish Vaswani"
    assert attention.pdf_url == "https://arxiv.org/pdf/1706.03762v7"
    assert attention.landing_url == "https://arxiv.org/abs/1706.03762v7"
    old = records["hep-th/9901001v3"]
    assert old.identifiers == {"arxiv": "hep-th/9901001", "doi": "10.1143/PTP.101.1155"}
    assert old.venue == "Prog.Theor.Phys.101:1155-1164,1999"
    assert old.abstract.startswith("We explicitly give")  # leading whitespace collapsed


def test_parse_api_skips_error_entries():
    xml = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_1234</id><title>Error</title>
      <summary>incorrect id format for 1234</summary></entry></feed>"""
    assert parse_api_response(xml) == []


def test_withdrawn_comment_sets_status():
    xml = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/2101.00001v2</id><title>Test paper A</title><summary>x</summary>
      <published>2021-01-01T00:00:00Z</published><arxiv:comment>This paper has been withdrawn by the authors</arxiv:comment>
      </entry></feed>"""
    (record,) = parse_api_response(xml)
    assert record.status == "withdrawn"


def test_query_by_ids_drops_versions_and_chunks(http_mock, load_fixture):
    http_mock.add("GET", API_URL, load_fixture("arxiv_api_query.xml"))
    ids = ["https://arxiv.org/abs/1706.03762v7", "arXiv:2504.08385", "not an id"] + [
        f"2101.{n:05d}" for n in range(60)
    ]
    records = query_by_ids(ids)
    assert len(http_mock.requests) == 2  # 62 ids -> chunks of 50 + 12
    first = parse_qs(urlsplit(str(http_mock.requests[0].url)).query)
    assert first["id_list"][0].split(",")[:2] == ["1706.03762", "2504.08385"]
    assert first["max_results"] == ["50"]
    assert len(records) == 6  # the fixture answered both requests


def test_query_recent_params(http_mock, load_fixture):
    http_mock.add("GET", API_URL, load_fixture("arxiv_api_query.xml"))
    query_recent("cs.IR", date(2026, 9, 28), date(2026, 10, 12), max_results=5000)
    query = parse_qs(urlsplit(str(http_mock.requests[0].url)).query)
    assert query["search_query"] == ["cat:cs.IR AND submittedDate:[202609280000 TO 202610122359]"]
    assert query["sortBy"] == ["submittedDate"] and query["sortOrder"] == ["descending"]
    assert query["max_results"] == ["2000"]  # clamped to arXiv's slice limit
    assert query["start"] == ["0"]
    with pytest.raises(ValueError):
        query_recent("cs.IR", date(2026, 10, 12), date(2026, 9, 28))


def test_query_recent_pages_with_start(http_mock, load_fixture):
    """The backfill fallback pages through a busy category (start 0, 200, 400, ...)."""
    http_mock.add("GET", API_URL, load_fixture("arxiv_api_query.xml"))
    query_recent("cs.IR", date(2026, 9, 28), date(2026, 10, 12), 200, start=200)
    query = parse_qs(urlsplit(str(http_mock.requests[0].url)).query)
    assert query["start"] == ["200"] and query["max_results"] == ["200"]
    query_recent("cs.IR", date(2026, 9, 28), date(2026, 10, 12), 200, start=-5)
    assert parse_qs(urlsplit(str(http_mock.requests[1].url)).query)["start"] == ["0"]


# ------------------------------------------------------------------------------------------------ store


def test_ingesting_the_feed_twice_creates_no_duplicates(load_fixture):
    """SPEC §19 test 1: papers, versions and source records are created once."""
    records = parse_feed(load_fixture("arxiv_feed_multi.xml")).records
    for _ in range(2):
        with session_scope(write=True) as s:
            for record in records:
                upsert_paper(s, record)
    with session_scope() as s:
        assert s.query(Paper).count() == 7
        assert s.query(PaperVersion).count() == 7
        assert s.query(SourceRecord).count() == 7
        replaced = s.query(PaperVersion).filter_by(version_label="v3", announce_type="replace").one()
        assert replaced.source == "arxiv"


def test_api_then_feed_replace_marks_new_version(load_fixture):
    """An arXiv replacement announcement adds a version to the existing paper (no new paper)."""
    feed = {r.source_id: r for r in parse_feed(load_fixture("arxiv_feed_multi.xml")).records}
    replace = feed["2405.01930v3"]
    older = parse_feed(load_fixture("arxiv_feed_multi.xml")).records[4]
    older.version_label, older.source_id, older.announce_type = "v2", "2405.01930v2", "new"
    with session_scope(write=True) as s:
        first = upsert_paper(s, older)
        second = upsert_paper(s, replace)
    assert second.paper_id == first.paper_id
    assert second.new_version is True


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ('Juhani Kivim\\"aki', "Juhani Kivimäki"),
        ("Paul Erd\\H{o}s", "Paul Erdős"),
        ("{\\'E}cole {\\`a} Gen{\\`e}ve", "École à Genève"),
        ("Fran\\c{c}ois", "François"),
        ("Ji\\v{r}\\'i", "Jiří"),
        ("\\'{\\i}ndice", "índice"),
        ("Stra\\ss e and Gro\\ss{}mann", "Straße and Großmann"),
        ("{\\L}ukasiewicz and S{\\o}ren", "Łukasiewicz and Søren"),
        ('\\AA ngstr\\"om', "Ångström"),
    ],
)
def test_tex_accents_become_unicode(raw: str, clean: str) -> None:
    assert detex(raw) == clean


def test_math_and_other_commands_are_left_alone() -> None:
    text = (
        "$\\varphi$ and $\\hat{x}$ in $\\mathbb{R}^n$, \\rho, \\cite{x}, \\url{y}, \\vec{v}, \\kappa, \\t{oo}"
    )
    assert detex(text) == text


def test_collapse_unescapes_detexes_and_collapses() -> None:
    assert collapse('  Kivim\\"aki  &amp;\n Z\\"urich ') == "Kivimäki & Zürich"
    assert split_creators('Juhani Kivim\\"aki, Paul Erd\\H{o}s and Fran\\c{c}ois Chollet') == [
        "Juhani Kivimäki",
        "Paul Erdős",
        "François Chollet",
    ]
