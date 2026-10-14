"""Knowledge graph (SPEC §12, §13): the CD index, disruption counts, expansion (S2 and the OpenAlex
fallback), kNN similarity, the neighbourhood and project graph, breakthrough analyses and quotas.
All outbound calls are answered by ``http_mock`` from real 2026-10-12 payloads."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from radar import models
from radar.connectors import openalex, s2_graph
from radar.db import session_scope
from radar.graph import disruption
from radar.graph.breakthroughs import chosen_authors
from radar.graph.knn import link_similar
from radar.jobs.runner import JobContext
from radar.store.graph import upsert_edge
from radar.store.states import record_interaction

SPECTER_DOI = "10.18653/v1/2020.acl-main.207"
OA_LOOKUP = re.compile(r"^https://api\.openalex\.org/works/(doi:10\.18653|W3016913119)")
OA_WORKS = re.compile(r"^https://api\.openalex\.org/works\?")
OA_AUTHORS = re.compile(r"^https://api\.openalex\.org/authors\?")
S2_REFS = re.compile(r"/graph/v1/paper/[^/]+/references")
S2_CITES = re.compile(r"/graph/v1/paper/[^/]+/citations")


def query(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}


def counts_route(n_f: int = 468, n_b: int = 294, n_r: int = 246_005) -> Any:
    """Answer ``/works?filter=…`` count queries by the shape of the filter (SPEC §13)."""

    def answer(request: httpx.Request) -> dict[str, Any]:
        f = query(request)["filter"]
        if f.startswith("author.id:"):
            raise AssertionError("author works must be routed before counts")
        if "," in f:
            count = n_b
        elif "|" in f or f.count("cites:") == 1 and not f.startswith("cites:W3016913119"):
            count = n_r
        else:
            count = n_f
        return {"meta": {"count": count, "cost_usd": 0.0001}, "results": [{"id": "https://openalex.org/W1"}]}

    return answer


# ------------------------------------------------------------------------------------------------
# CD index
# ------------------------------------------------------------------------------------------------


def test_cd_index_matches_a_hand_computation() -> None:
    # SPECTER (W3016913119), live counts of 2026-10-12: N_F 468, N_B 294, N_R 246 005
    result = disruption.cd_index(468, 294, 246_005, refs_used=54)
    assert (result.n_i, result.n_j, result.n_k, result.n_citing) == (174, 294, 245_711, 468)
    assert result.cd == pytest.approx((174 - 294) / (174 + 294 + 245_711))
    assert result.cd == pytest.approx(-0.000487, abs=1e-6)
    disruptive = disruption.cd_index(100, 10, 50)  # 90 citers ignore the references
    assert disruptive.cd == pytest.approx((90 - 10) / (90 + 10 + 40))
    assert disruption.cd_index(10, 10, 10).cd == -1.0  # every citer also cites the references
    assert disruption.cd_index(10, 0, 0).cd == 1.0  # nobody connects it to its references
    assert disruption.cd_index(0, 0, 50).cd is None  # no citing works
    assert disruption.cd_index(5, 0, 0, refs_used=0).cd is None  # no references known
    clamped = disruption.cd_index(10, 15, 3)  # inconsistent counts are clamped, never negative
    assert (clamped.n_i, clamped.n_j, clamped.n_k) == (0, 10, 0)


def test_disruption_counts_use_three_filtered_list_calls(http_mock: Any) -> None:
    http_mock.add("GET", OA_WORKS, counts_route())
    refs = ["W90362830", "https://openalex.org/W2133564696"]
    assert openalex.disruption_counts("W3016913119", refs) == (468, 294, 246_005)
    filters = [query(r)["filter"] for r in http_mock.requests]
    assert filters == [
        "cites:W3016913119",
        "cites:W3016913119,cites:W90362830|W2133564696",
        "cites:W90362830|W2133564696",
    ]
    assert all(query(r)["per-page"] == "1" and query(r)["select"] == "id" for r in http_mock.requests)
    with pytest.raises(ValueError):
        openalex.disruption_counts("W1", [])
    with pytest.raises(ValueError):
        openalex.disruption_counts("W1", [f"W{i}" for i in range(1, 102)])


def test_disruption_endpoint(client: TestClient, http_mock: Any, make_paper: Any, load_fixture: Any) -> None:
    paper = make_paper(identifiers={"doi": SPECTER_DOI})
    http_mock.add("GET", OA_LOOKUP, load_fixture("openalex_work_published.json"))
    http_mock.add("GET", OA_WORKS, counts_route())
    r = client.post(f"/api/papers/{paper}/disruption")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["n_citing"], body["n_i"], body["n_j"], body["n_k"]) == (468, 174, 294, 245_711)
    assert body["refs_used"] == 54 and body["refs_total"] == 54 and body["label"] == "consolidating"
    assert body["text"].startswith("Of 468 citing papers, 174 (37%) cite it without citing its references")
    count_calls = len(http_mock.calls("GET", OA_WORKS))
    assert count_calls == 3
    assert client.post(f"/api/papers/{paper}/disruption").json() == body  # cached for 30 days
    assert len(http_mock.calls("GET", OA_WORKS)) == 3
    client.post(f"/api/papers/{paper}/disruption?force=true")
    assert len(http_mock.calls("GET", OA_WORKS)) == 6
    detail = client.get(f"/api/papers/{paper}").json()
    assert detail["disruption"]["label"] == "consolidating" and detail["cd"] == pytest.approx(body["cd"])
    assert detail["identifiers"]["openalex"] == "W3016913119"  # the lookup attached the OpenAlex id
    bare = make_paper()
    assert client.post(f"/api/papers/{bare}/disruption").status_code == 422
    assert client.post("/api/papers/987654/disruption").status_code == 404


def test_disruption_outage_then_budget_errors(
    client: TestClient, http_mock: Any, make_paper: Any, load_fixture: Any
) -> None:
    other = make_paper(identifiers={"doi": "10.1000/other"})
    http_mock.add("GET", re.compile(r"api\.openalex\.org/works/doi:10\.1000"), {"error": "down"}, status=503)
    r = client.post(f"/api/papers/{other}/disruption")
    assert r.status_code == 503 and r.json()["error"]["code"] == "source_unavailable"
    paper = make_paper(identifiers={"doi": SPECTER_DOI})
    http_mock.add("GET", OA_LOOKUP, load_fixture("openalex_work_published.json"))
    http_mock.add(
        "GET",
        OA_WORKS,
        {"error": "budget"},
        status=429,
        headers={"x-ratelimit-limit-usd": "0.1", "x-ratelimit-remaining-usd": "0"},
    )
    r = client.post(f"/api/papers/{paper}/disruption")
    assert r.status_code == 429 and r.json()["error"]["code"] == "budget_exceeded"
    again = client.post(f"/api/papers/{other}/disruption")  # stopped for the rest of the UTC day
    assert again.status_code == 429


# ------------------------------------------------------------------------------------------------
# Expansion and similarity
# ------------------------------------------------------------------------------------------------


def test_expand_from_semantic_scholar(
    client: TestClient, http_mock: Any, make_paper: Any, load_fixture: Any
) -> None:
    paper = make_paper(title="SPECTER", identifiers={"arxiv": "2004.07180"})
    http_mock.add("GET", S2_REFS, load_fixture("s2_references.json"))
    http_mock.add("GET", S2_CITES, load_fixture("s2_citations.json"))
    r = client.post(f"/api/papers/{paper}/expand")
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["kind"] == "expand" and job["status"] == "succeeded"
    assert (
        job["stats"]["source"] == "s2" and job["stats"]["references"] == 3 and job["stats"]["citations"] == 3
    )
    assert (
        job["message"] == f"Linked 3 references, 3 citing papers and {job['stats']['similar']} similar papers"
    )
    s2_request = http_mock.calls("GET", S2_REFS)[0]
    assert "/paper/ARXIV:2004.07180/references" in str(s2_request.url) and query(s2_request)["limit"] == "100"
    with session_scope() as session:
        cites = session.scalars(select(models.GraphEdge).where(models.GraphEdge.relation == "CITES")).all()
        assert len(cites) == 6 and all(e.verified and e.provenance == "s2" for e in cites)
        outgoing = [e for e in cites if e.src_id == paper]
        assert len(outgoing) == 3 and any(e.evidence_json["contexts"] for e in outgoing)
        stubs = session.scalars(select(models.Paper).where(models.Paper.id != paper)).all()
        assert stubs and all(p.status == "stub" for p in stubs)
        row = session.scalar(select(models.PaperExpansion).where(models.PaperExpansion.paper_id == paper))
        assert row is not None and row.status == "done" and row.last_expanded_at is not None
    again = client.post(f"/api/papers/{paper}/expand")
    assert again.status_code == 200 and again.json()["job"] is None  # expanded within 7 days

    hood = client.get(f"/api/papers/{paper}/neighborhood").json()
    groups = {g["key"]: g for g in hood["groups"]}
    assert groups["references"]["count"] == 3 and groups["citations"]["count"] == 3
    assert hood["center"] == f"work:{paper}"
    center = next(n for n in hood["nodes"] if n["id"] == hood["center"])
    assert center["is_center"] and center["type"] == "work"
    cite_edges = [e for e in hood["edges"] if e["relation"] == "CITES"]
    assert len(cite_edges) == 6
    influential = [e for e in cite_edges if e["evidence"]["is_influential"]]
    assert influential and influential[0]["evidence"]["relation"] == "CITES"
    assert groups["authors"]["count"] == 1  # byline names until an analysis finds scholars
    assert any(n["type"] == "author" for n in hood["nodes"])
    assert any("breakthrough analysis" in notice for notice in hood["notices"])


def test_expand_falls_back_to_openalex(
    client: TestClient, http_mock: Any, make_paper: Any, load_fixture: Any
) -> None:
    paper = make_paper(title="SPECTER", identifiers={"doi": SPECTER_DOI})
    http_mock.add("GET", S2_REFS, {"message": "Too Many Requests"}, status=429)
    work = load_fixture("openalex_work_published.json")
    http_mock.add("GET", OA_LOOKUP, work)
    listed = load_fixture("openalex_list_cites.json")
    http_mock.add("GET", OA_WORKS, listed)
    r = client.post(f"/api/papers/{paper}/expand")
    assert r.status_code == 202, r.text
    stats = r.json()["job"]["stats"]
    assert stats["source"] == "openalex" and stats["neighbours"] > 0
    filters = [query(req)["filter"] for req in http_mock.calls("GET", OA_WORKS)]
    assert filters[0].startswith("openalex:W") and filters[-1] == "cites:W3016913119"
    hood = client.get(f"/api/papers/{paper}/neighborhood").json()
    assert "Semantic Scholar was unavailable; citations come from OpenAlex" in hood["notices"]
    assert hood["expansion"]["status"] == "done"


def test_similarity_edges_use_the_model_threshold(make_paper: Any) -> None:
    text = "Contrastive learning of scientific document embeddings from citation graphs."
    a = make_paper(title="Paper A", abstract=text)
    b = make_paper(title="Paper B", abstract=text + " A small variation.")
    c = make_paper(title="Paper C", abstract="Weather radar observations of convective storms over plains.")
    from radar.embeddings.registry import get_embedder
    from radar.embeddings.vectors import ensure_paper_vectors

    with session_scope(write=True) as session:
        ensure_paper_vectors(session, [a, b, c], get_embedder().model_id)
    with session_scope(write=True) as session:
        assert link_similar(session, a) == 1
    with session_scope() as session:
        (edge,) = session.scalars(
            select(models.GraphEdge).where(models.GraphEdge.relation == "SEMANTICALLY_SIMILAR")
        ).all()
        assert {edge.src_id, edge.dst_id} == {a, b} and edge.src_id < edge.dst_id
        assert edge.provenance == "local/knn" and not edge.verified and edge.confidence > 0.3
        assert edge.evidence_json["model_id"] == "local/hashing-v1"


# ------------------------------------------------------------------------------------------------
# Breakthroughs
# ------------------------------------------------------------------------------------------------


def _breakthrough_routes(http_mock: Any, load_fixture: Any, *, budget_after: int | None = None) -> None:
    http_mock.add("GET", OA_LOOKUP, load_fixture("openalex_work_published.json"))
    http_mock.add("GET", OA_AUTHORS, load_fixture("openalex_authors.json"))
    http_mock.add(
        "GET",
        re.compile(r"^https://api\.openalex\.org/works\?.*filter=author\.id"),
        load_fixture("openalex_author_works.json"),
    )
    if budget_after is not None:
        http_mock.add("GET", OA_WORKS, counts_route(), times=budget_after)
        http_mock.add(
            "GET",
            OA_WORKS,
            {"error": "budget"},
            status=429,
            headers={"x-ratelimit-limit-usd": "0.1", "x-ratelimit-remaining-usd": "0"},
        )
    else:
        http_mock.add("GET", OA_WORKS, counts_route())


def test_chosen_authors_are_the_first_six_plus_the_last() -> None:
    ids = [f"A{i}" for i in range(10)]
    assert chosen_authors(ids) == [
        (0, "A0"),
        (1, "A1"),
        (2, "A2"),
        (3, "A3"),
        (4, "A4"),
        (5, "A5"),
        (9, "A9"),
    ]
    assert chosen_authors(["A1", None, "A2"]) == [(0, "A1"), (2, "A2")]
    assert chosen_authors([None]) == []


def test_breakthrough_analysis(
    client: TestClient, http_mock: Any, make_paper: Any, load_fixture: Any
) -> None:
    paper = make_paper(title="SPECTER", identifiers={"doi": SPECTER_DOI})
    assert client.get(f"/api/papers/{paper}/breakthroughs").json()["status"] == "none"
    _breakthrough_routes(http_mock, load_fixture)
    r = client.post(f"/api/papers/{paper}/breakthroughs")
    assert r.status_code == 202, r.text
    stats = r.json()["job"]["stats"]
    assert stats["authors"] == 5 and stats["authors_done"] == 5 and stats["stopped"] is None
    assert stats["works"] == 15  # 3 top works per author in the fixture, for 5 authors
    body = client.get(f"/api/papers/{paper}/breakthroughs").json()
    assert body["status"] == "done" and body["paper"]["label"] == "consolidating"
    names = [g["scholar"]["display_name"] for g in body["scholars"]]
    assert names == ["Arman Cohan", "Sergey Feldman", "Iz Beltagy", "Doug Downey", "Daniel S. Weld"]
    cohan = body["scholars"][0]
    assert (
        cohan["position"] == 0
        and cohan["scholar"]["h_index"] == 34
        and cohan["scholar"]["orcid"] == "0000-0002-8954-2724"
    )
    titles = [w["paper"]["title"] for w in cohan["works"]]
    assert {t.split(":")[0] for t in titles} >= {"SciBERT", "Longformer"}
    assert all(w["cd"] is not None for w in cohan["works"])
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(models.Scholar)) == 5
        authored = session.scalar(
            select(func.count()).select_from(models.GraphEdge).where(models.GraphEdge.relation == "AUTHORED")
        )
        # the paper's five authors + each author's three top works (the mock answers every author
        # with Cohan's three most-cited papers)
        assert authored == 5 + 5 * 3
    hood = client.get(f"/api/papers/{paper}/neighborhood").json()
    groups = {g["key"]: g for g in hood["groups"]}
    assert groups["authors"]["count"] == 5 and groups["author_works"]["count"] == 3
    scholar_id = cohan["scholar"]["id"]
    scholar = client.get(f"/api/scholars/{scholar_id}").json()
    assert scholar["scholar"]["display_name"] == "Arman Cohan" and len(scholar["works"]) == 4  # incl. SPECTER
    assert client.get("/api/scholars/987654").status_code == 404


def test_breakthroughs_stop_cleanly_at_the_budget(
    client: TestClient, http_mock: Any, make_paper: Any, load_fixture: Any
) -> None:
    paper = make_paper(title="SPECTER", identifiers={"doi": SPECTER_DOI})
    _breakthrough_routes(http_mock, load_fixture, budget_after=6)  # the paper (3) + one more work (3)
    r = client.post(f"/api/papers/{paper}/breakthroughs")
    assert r.status_code == 202
    job = r.json()["job"]
    assert job["status"] == "succeeded"  # a budget stop is not a failure: results so far are kept
    assert job["stats"]["stopped"].startswith("Stopped early: the OpenAlex daily budget was reached (0 of 5")
    body = client.get(f"/api/papers/{paper}/breakthroughs").json()
    assert body["paper"]["cd"] is not None and body["message"].startswith("Stopped early")
    again = client.post(f"/api/papers/{paper}/breakthroughs")
    assert again.status_code == 409 and again.json()["error"]["code"] == "conflict"  # budget spent today


def test_daily_quotas(
    client: TestClient, http_mock: Any, make_paper: Any, load_fixture: Any, set_env: Any
) -> None:
    set_env(RADAR_BREAKTHROUGHS_PER_DAY="1", RADAR_EXPANSIONS_PER_USER_PER_DAY="1")
    _breakthrough_routes(http_mock, load_fixture)
    first = make_paper(identifiers={"doi": SPECTER_DOI})
    second = make_paper(identifiers={"doi": "10.1000/second"})
    assert client.post(f"/api/papers/{first}/breakthroughs").status_code == 202
    limited = client.post(f"/api/papers/{second}/breakthroughs")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "quota_exceeded"
    http_mock.add("GET", S2_REFS, load_fixture("s2_references.json"))
    http_mock.add("GET", S2_CITES, load_fixture("s2_citations.json"))
    a = make_paper(identifiers={"arxiv": "2610.00001"})
    b = make_paper(identifiers={"arxiv": "2610.00002"})
    assert client.post(f"/api/papers/{a}/expand").status_code == 202
    assert client.post(f"/api/papers/{b}/expand").status_code == 429


# ------------------------------------------------------------------------------------------------
# Project graph
# ------------------------------------------------------------------------------------------------


def test_project_graph(client: TestClient, make_project: Any, make_paper: Any) -> None:
    project = make_project()
    seed = make_paper(title="Seed", authors=["Ada Lovelace", "Grace Hopper"])
    liked = make_paper(title="Liked", authors=["Ada Lovelace"])
    saved = make_paper(title="Saved", authors=["Alan Turing"])
    outsider = make_paper(title="Not in the project", authors=["Ada Lovelace"])
    with session_scope(write=True) as session:
        session.add(models.ProjectSeed(project_id=project, paper_id=seed, added_via="doi"))
        record_interaction(session, project, liked, "relevant")
        record_interaction(session, project, saved, "save")
        topic = models.Concept(
            canonical_label="Graph learning", ontology_source="openalex_topic", external_id="T1"
        )
        session.add(topic)
        session.flush()
        for pid in (seed, liked):
            upsert_edge(
                session,
                src=("work", pid),
                relation="HAS_TOPIC",
                dst=("concept", topic.id),
                provenance="openalex",
                verified=False,
                confidence=0.9,
            )
        upsert_edge(
            session,
            src=("work", liked),
            relation="CITES",
            dst=("work", seed),
            provenance="openalex",
            verified=True,
        )
        upsert_edge(
            session,
            src=("work", outsider),
            relation="CITES",
            dst=("work", seed),
            provenance="openalex",
            verified=True,
        )
    graph = client.get(f"/api/projects/{project}/graph").json()
    works = {n["id"] for n in graph["nodes"] if n["type"] == "work"}
    assert works == {f"work:{seed}", f"work:{liked}", f"work:{saved}"}
    seed_node = next(n for n in graph["nodes"] if n["id"] == f"work:{seed}")
    assert seed_node["seed"] is True
    groups = {g["key"]: g for g in graph["groups"]}
    assert groups["seeds"]["node_ids"] == [f"work:{seed}"] and groups["relevant"]["count"] == 1
    assert groups["topics"]["node_ids"] == [f"concept:{topic.id}"]
    (author,) = [n for n in graph["nodes"] if n["type"] == "author"]
    assert author["label"] == "Ada Lovelace" and author["weight"] == 2.0  # shared by two project papers
    relations = sorted(e["relation"] for e in graph["edges"])
    assert relations.count("CITES") == 1  # the outsider's citation is not part of the project graph
    assert relations.count("HAS_TOPIC") == 2 and relations.count("AUTHORED") == 2
    assert client.get("/api/projects/987654/graph").status_code == 404


def test_s2_link_parsing(load_fixture: Any) -> None:
    refs = s2_graph.parse_links(load_fixture("s2_references.json"), "citedPaper")
    cites = s2_graph.parse_links(load_fixture("s2_citations.json"), "citingPaper")
    assert len(refs) == 3 and len(cites) == 3
    assert refs[0].is_influential is True and refs[0].intents == ["background"] and refs[0].contexts
    assert refs[0].record.status == "stub" and refs[0].record.identifiers["arxiv"] == "1909.13456"
    assert s2_graph.parse_links({"data": [{"citedPaper": None}]}, "citedPaper") == []
    assert s2_graph.parse_links(None, "citedPaper") == []


def test_run_breakthroughs_without_identifiers(make_paper: Any) -> None:
    from radar.graph.breakthroughs import run_breakthroughs

    paper = make_paper()
    assert (
        run_breakthroughs(JobContext(), paper)["stopped"] == "This paper has no DOI, arXiv id or OpenAlex id"
    )
