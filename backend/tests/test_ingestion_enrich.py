"""Jobs ``enrich``, ``embed`` and seed resolution (SPEC §6, §9) with mocked sources."""

from __future__ import annotations

import re

import pytest

from radar.db import session_scope
from radar.errors import UnresolvableIdentifier
from radar.ingestion.enrich import run_enrich
from radar.ingestion.seeds import resolve_seed
from radar.ingestion.vectors import run_embed_papers
from radar.jobs.runner import JobContext
from radar.models import Concept, GraphEdge, Paper, PaperAuthor, PaperIdentifier, PaperVersion
from radar.net.http import SourceUnavailable

OA_ARXIV = re.compile(r"^https://api\.openalex\.org/works/doi:10\.48550/arxiv\.2504\.08385\?")
OA_SPECTER = re.compile(r"^https://api\.openalex\.org/works/doi:10\.18653/v1/2020\.acl-main\.207\?")
ARXIV_API = "https://export.arxiv.org/api/query"


def _identifiers(paper_id: int) -> dict[str, str]:
    with session_scope() as s:
        rows = s.query(PaperIdentifier).filter_by(paper_id=paper_id).all()
        return {r.scheme: r.value for r in rows}


# ------------------------------------------------------------------------------------------- enrich


def test_enrich_links_openalex_topics_and_author_ids(http_mock, load_fixture, make_paper):
    paper_id = make_paper(identifiers={"arxiv": "2504.08385"})
    http_mock.add("GET", OA_ARXIV, load_fixture("openalex_work_arxiv.json"))

    stats = run_enrich(JobContext())
    assert stats["found"] == 1 and stats["topics_linked"] == 1  # secondary topics score 0.0
    assert stats["author_ids_linked"] >= 2
    assert _identifiers(paper_id)["openalex"] == "W4414828500"
    with session_scope() as s:
        concept = s.query(Concept).filter_by(external_id="T10203").one()
        edge = s.query(GraphEdge).filter_by(relation="HAS_TOPIC").one()
        assert (edge.src_id, edge.dst_type, edge.dst_id) == (paper_id, "concept", concept.id)
        assert edge.confidence == 1.0 and edge.verified is False and edge.provenance == "openalex"
        assert edge.evidence_json == {
            "score": 1.0,
            "subfield": "Information Systems",
            "field": "Computer Science",
        }
        authors = s.query(PaperAuthor).filter_by(paper_id=paper_id).order_by(PaperAuthor.position).all()
        assert [a.openalex_author_id for a in authors[:2]] == ["A5104435539", "A5119180993"]
        assert s.get(Paper, paper_id).openalex_checked_at is not None


def test_enrich_not_found_is_retried_later_not_every_run(http_mock, make_paper):
    paper_id = make_paper(identifiers={"arxiv": "2610.99999"})
    http_mock.add("GET", re.compile(r"api\.openalex\.org/works/"), {"error": "not found"}, status=404)
    assert run_enrich(JobContext())["not_found"] == 1
    assert run_enrich(JobContext())["openalex_candidates"] == 0  # checked < 2 days ago
    assert run_enrich(JobContext(), paper_ids=[paper_id])["openalex_candidates"] == 1  # explicit request


def test_enrich_stops_at_the_openalex_budget(http_mock, make_paper):
    make_paper(identifiers={"arxiv": "2610.00001"})
    make_paper(identifiers={"arxiv": "2610.00002"})
    http_mock.add(
        "GET",
        re.compile(r"api\.openalex\.org"),
        {"error": "budget"},
        status=429,
        headers={"x-ratelimit-limit-usd": "0.1", "x-ratelimit-remaining-usd": "0"},
    )
    stats = run_enrich(JobContext())
    assert stats["budget_stopped"] is True and stats["found"] == 0
    assert len(http_mock.requests) == 1  # no retries, no second paper


def test_enrich_links_known_references_and_merges_preprint(http_mock, load_fixture, make_paper):
    published = make_paper(identifiers={"doi": "10.18653/v1/2020.acl-main.207"})
    cited = make_paper(identifiers={"openalex": "W90362830"})
    http_mock.add("GET", OA_SPECTER, load_fixture("openalex_work_published.json"))
    stats = run_enrich(JobContext(), paper_ids=[published])
    assert stats["citations_linked"] == 1
    assert _identifiers(published) == {
        "doi": "10.18653/v1/2020.acl-main.207",
        "openalex": "W3016913119",
        "arxiv": "2004.07180",  # the preprint is now the same canonical work (SPEC §19 test 2)
    }
    with session_scope() as s:
        edge = s.query(GraphEdge).filter_by(relation="CITES").one()
        assert (edge.src_id, edge.dst_id, edge.verified, edge.provenance) == (
            published,
            cited,
            True,
            "openalex",
        )
        assert s.query(PaperVersion).filter_by(paper_id=published).count() == 0  # OpenAlex adds none
        paper = s.get(Paper, published)
        assert paper.venue == "Annual Meeting of the Association for Computational Linguistics (ACL)"


def test_enrich_marks_retractions(http_mock, load_fixture, make_paper):
    paper_id = make_paper(identifiers={"doi": "10.1016/s0140-6736(97)11096-0"})
    http_mock.add(
        "GET",
        re.compile(r"api\.openalex\.org/works/doi:10\.1016"),
        load_fixture("openalex_work_retracted.json"),
    )
    stats = run_enrich(JobContext())
    assert stats["retracted"] >= 1
    with session_scope() as s:
        assert s.get(Paper, paper_id).status == "retracted"


# -------------------------------------------------------------------------------------------- seeds


def test_known_seed_resolves_without_network(make_paper):
    paper_id = make_paper(identifiers={"arxiv": "2504.08385"})
    # No http_mock is installed and RADAR_OFFLINE=1, so any network call would raise EgressBlocked.
    assert resolve_seed("https://arxiv.org/abs/2504.08385v2") == (paper_id, "arxiv")
    assert resolve_seed("10.48550/arXiv.2504.08385") == (paper_id, "arxiv")


def test_seed_from_arxiv_api(http_mock, load_fixture):
    http_mock.add("GET", ARXIV_API, load_fixture("arxiv_api_query.xml"))
    paper_id, scheme = resolve_seed("arXiv:1706.03762")
    assert scheme == "arxiv"
    with session_scope() as s:
        assert s.get(Paper, paper_id).canonical_title == "Attention Is All You Need"


def test_seed_falls_back_to_openalex_when_arxiv_is_throttled(http_mock, load_fixture):
    http_mock.add("GET", ARXIV_API, "slow down", status=429)
    http_mock.add("GET", OA_ARXIV, load_fixture("openalex_work_arxiv.json"))
    paper_id, _ = resolve_seed("2504.08385")
    assert _identifiers(paper_id) == {"arxiv": "2504.08385", "openalex": "W4414828500"}


def test_seed_reports_unavailable_rather_than_unknown_when_arxiv_failed(http_mock):
    http_mock.add("GET", ARXIV_API, "slow down", status=429)
    http_mock.add("GET", re.compile(r"api\.openalex\.org"), {"error": "x"}, status=404)
    with pytest.raises(SourceUnavailable):
        resolve_seed("2610.12345")


def test_seed_doi_via_openalex(http_mock, load_fixture):
    http_mock.add("GET", OA_SPECTER, load_fixture("openalex_work_published.json"))
    paper_id, scheme = resolve_seed("https://doi.org/10.18653/v1/2020.acl-main.207")
    assert scheme == "doi" and _identifiers(paper_id)["openalex"] == "W3016913119"


def test_unknown_or_invalid_seed(http_mock):
    with pytest.raises(UnresolvableIdentifier):
        resolve_seed("definitely not an identifier")
    http_mock.add("GET", re.compile(r"api\.openalex\.org"), {"error": "x"}, status=404)
    with pytest.raises(UnresolvableIdentifier):
        resolve_seed("10.1234/does-not-exist")


# ------------------------------------------------------------------------------------------ vectors


def test_embed_papers_is_incremental(make_paper):
    ids = [make_paper() for _ in range(3)]
    first = run_embed_papers(JobContext())
    assert first["model_id"] == "local/hashing-v1" and first["computed"] == 3
    assert run_embed_papers(JobContext())["computed"] == 0
    new_id = make_paper()
    assert run_embed_papers(JobContext(), paper_ids=[new_id, ids[0]])["computed"] == 1
