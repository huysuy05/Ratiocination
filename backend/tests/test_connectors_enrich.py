"""OpenAlex and Semantic Scholar connectors (SPEC §6) against the 2026-10-12 fixtures."""

from __future__ import annotations

import json
import re
from datetime import date
from urllib.parse import parse_qs, urlsplit

import pytest

from radar.connectors import openalex, s2_papers
from radar.db import flush_writes, session_scope
from radar.models import CostLedger
from radar.net.http import SourceUnavailable

OA_WORKS = "https://api.openalex.org/works"
S2_BATCH = "https://api.semanticscholar.org/graph/v1/paper/batch"


def _query(request) -> dict[str, list[str]]:
    return parse_qs(urlsplit(str(request.url)).query)


# ------------------------------------------------------------------------------------------- OpenAlex


def test_parse_arxiv_work(load_fixture):
    work = openalex.parse_work(load_fixture("openalex_work_arxiv.json"))
    assert work.openalex_id == "W4414828500"
    assert work.doi is None  # the arXiv DOI is not a DOI identifier (SPEC §5)
    assert work.arxiv_id == "2504.08385"  # found in the locations
    assert work.type == "preprint" and work.venue is None  # arXiv is a repository, not a venue
    assert work.abstract.startswith("Scholar Inbox is a new open-access platform")
    assert [t.external_id for t in work.topics][0] == "T10203"
    assert work.topics[0].label == "Recommender Systems and Techniques"
    assert work.topics[0].subfield == "Information Systems" and work.topics[0].field == "Computer Science"
    assert len(work.author_ids) == len(work.authors)
    assert work.author_ids[:2] == ["A5104435539", "A5119180993"]  # raw-name authors have no id
    record = work.to_record()
    assert record.source == "openalex" and record.version_label is None
    assert record.identifiers == {"openalex": "W4414828500", "arxiv": "2504.08385"}
    assert record.publication_date == date(2025, 4, 11)


def test_parse_published_work_links_preprint_and_drops_placeholder_date(load_fixture):
    work = openalex.parse_work(load_fixture("openalex_work_published.json"))
    assert work.doi == "10.18653/v1/2020.acl-main.207" and work.arxiv_id == "2004.07180"
    assert work.venue == "Annual Meeting of the Association for Computational Linguistics (ACL)"
    assert len(work.referenced_works) == 54 and work.referenced_works[0] == "W90362830"
    assert work.publication_date == date(2020, 1, 1)  # what OpenAlex says…
    record = work.to_record()
    assert record.publication_date is None and record.year == 2020  # …is a placeholder, not a date
    assert record.best_oa_pdf == "https://www.aclweb.org/anthology/2020.acl-main.207.pdf"


def test_parse_retracted_work(load_fixture):
    work = openalex.parse_work(load_fixture("openalex_work_retracted.json"))
    assert work.is_retracted and work.to_record().status == "retracted"
    assert work.abstract is None


def test_reconstruct_abstract():
    assert (
        openalex.reconstruct_abstract({"world": [1], "Hello": [0], "again": [3], "!": [2]})
        == "Hello world ! again"
    )
    assert openalex.reconstruct_abstract(None) is None
    assert openalex.reconstruct_abstract({}) is None


@pytest.mark.parametrize(
    ("identifier", "path"),
    [
        ("doi:10.18653/V1/2020.ACL-MAIN.207", "/works/doi:10.18653/v1/2020.acl-main.207"),
        ("arxiv:2504.08385v2", "/works/doi:10.48550/arxiv.2504.08385"),
        ("https://openalex.org/W3016913119", "/works/W3016913119"),
        ("W3016913119", "/works/W3016913119"),
    ],
)
def test_get_work_paths(http_mock, load_fixture, identifier, path):
    http_mock.add(
        "GET", re.compile(r"^https://api\.openalex\.org/works/"), load_fixture("openalex_work_published.json")
    )
    work = openalex.get_work(identifier)
    assert work is not None and work.openalex_id == "W3016913119"
    request = http_mock.requests[0]
    assert request.url.path == path
    assert "abstract_inverted_index" in _query(request)["select"][0]


def test_get_work_404_and_errors(http_mock):
    # Compiled patterns are searched in the full URL, which includes "?select=…".
    http_mock.add("GET", re.compile(r"/works/W1\?"), {"error": "not found"}, status=404)
    http_mock.add("GET", re.compile(r"/works/W2\?"), {"error": "bad"}, status=400)
    assert openalex.get_work("W1") is None
    with pytest.raises(SourceUnavailable):
        openalex.get_work("W2")
    with pytest.raises(ValueError):
        openalex.get_work("not an id")


def test_lookup_is_free_and_list_calls_are_metered(http_mock, load_fixture):
    http_mock.add("GET", re.compile(r"/works/W3016913119"), load_fixture("openalex_work_published.json"))
    http_mock.add("GET", OA_WORKS, load_fixture("openalex_list_cites.json"))
    openalex.get_work("W3016913119")
    citing = openalex.get_citing_works("W3016913119", limit=500)
    assert len(citing) == 3
    query = _query(http_mock.requests[1])
    assert query["filter"] == ["cites:W3016913119"]
    assert query["per-page"] == ["200"] and query["sort"] == ["publication_date:desc"]
    flush_writes()
    with session_scope() as s:
        rows = [(r.call_type, r.cost_usd) for r in s.query(CostLedger).order_by(CostLedger.id)]
    assert rows == [("lookup", 0.0), ("list", 0.0001)]


def test_get_works_by_ids_chunks_by_50(http_mock, load_fixture):
    http_mock.add("GET", OA_WORKS, load_fixture("openalex_list_cites.json"))
    ids = [f"W{n}" for n in range(1, 61)] + ["https://openalex.org/W1", "junk"]
    openalex.get_works_by_ids(ids)
    filters = [_query(r)["filter"][0] for r in http_mock.requests]
    assert len(filters) == 2 and filters[0].startswith("openalex:W1|W2|")
    assert filters[0].count("|") == 49 and filters[1].count("|") == 9


# ------------------------------------------------------------------------------------- Semantic Scholar


def test_batch_keeps_positions_and_nulls(http_mock, load_fixture):
    http_mock.add("POST", S2_BATCH, load_fixture("s2_batch.json"))
    ids = ["ARXIV:2004.07180", "ARXIV:2504.08385", "DOI:10.0000/does-not-exist"]
    items = s2_papers.get_papers_batch(ids, ["paperId", "externalIds", "title"])
    assert [item is None for item in items] == [False, False, True]
    request = http_mock.requests[0]
    assert json.loads(request.content) == {"ids": ids}
    assert _query(request)["fields"] == ["paperId,externalIds,title"]


def test_batch_rejects_misaligned_answers(http_mock):
    http_mock.add("POST", S2_BATCH, [None])
    with pytest.raises(SourceUnavailable):
        s2_papers.get_papers_batch(["ARXIV:1", "ARXIV:2"], "title")


def test_paper_record_from_s2(load_fixture):
    items = load_fixture("s2_batch.json")
    record = s2_papers.paper_record(items[0], stub=True)
    assert record.source == "s2" and record.status == "stub"
    assert record.identifiers["s2"] == "a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f031"
    assert record.identifiers["arxiv"] == "2004.07180"
    assert s2_papers.paper_record(None) is None
    placeholder = dict(items[0], publicationDate="2020-01-01", year=2020)
    assert s2_papers.paper_record(placeholder).publication_date is None
