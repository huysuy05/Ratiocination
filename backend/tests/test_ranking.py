"""Ranking (SPEC §10): features, MMR, the preference head, reasons, deck builds, and the feed,
library, search, read-mark and BibTeX endpoints built on them. Vectors come from the deterministic
``local/hashing-v1`` embedder, so word overlap drives similarity."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import select

from radar import models
from radar.api.exports import base_key, bibtex, escape
from radar.api.serializers import paper_summaries, slate_out
from radar.db import session_scope
from radar.ranking import explain, features, mmr, preference
from radar.ranking.slate import build_slate
from radar.schemas import Reason
from radar.store.graph import upsert_edge
from radar.store.states import record_interaction

TODAY = datetime.now(UTC).date()
QUESTION = "graph neural networks for citation recommendation"
ON_TOPIC = (
    "Graph neural networks learn citation recommendation from citation graphs and neural message passing."
)
OFF_TOPIC = "Protein folding kinetics measured with cryogenic electron microscopy in yeast cells."


# ------------------------------------------------------------------------------------------------
# Pure parts
# ------------------------------------------------------------------------------------------------


def test_robust_norm_and_recency() -> None:
    values, bounds = features.robust_norm(np.array([0.0, 1.0, 2.0, 3.0, 100.0]))
    assert values.min() == 0.0 and values.max() == 1.0 and bounds["hi"] > bounds["lo"]
    flat, _ = features.robust_norm(np.array([0.3, 0.3, 0.3]))
    assert flat.tolist() == [0.5, 0.5, 0.5]
    noise, _ = features.robust_norm(np.array([0.104, 0.104, 0.114, 0.114]), floor=0.1)
    assert noise.max() == pytest.approx(0.1, abs=0.02)  # a 0.01 cosine gap stays small
    real, _ = features.robust_norm(np.array([0.0, 0.0, 0.87, 0.87]), floor=0.1)
    assert real.max() == 1.0  # a real gap still uses the full range
    assert features.robust_norm(np.array([]))[0].size == 0
    np.testing.assert_allclose(features.recency(np.array([0, 30, 60, -5])), [1.0, 0.5, 0.25, 1.0])


def test_score_combines_available_features_only() -> None:
    raw = {
        "question": np.array([0.1, 0.5, 0.9]),
        "anchor": None,
        "draft": None,
        "citation": np.array([0.0, 1.0, 0.0]),
        "preference": None,
        "recency": np.array([1.0, 1.0, 1.0]),
    }
    scored = features.score(raw, 3)
    total = features.WEIGHTS["question"] + features.WEIGHTS["citation"] + features.WEIGHTS["recency"]
    assert scored.available("question") and not scored.available("anchor")
    assert scored.values["citation"].tolist() == [0.0, 1.0, 0.0]  # binary: not normalized
    assert scored.values["recency"].tolist() == [0.5, 0.5, 0.5]  # constant: no signal
    np.testing.assert_allclose(sum(scored.contributions.values()), scored.score)
    np.testing.assert_allclose(scored.contributions["citation"][1], features.WEIGHTS["citation"] / total)
    q = scored.values["question"]
    assert q[0] == 0.0 and q[2] == 1.0  # P5..P99 scaling of the question cosine
    expected = (
        features.WEIGHTS["question"] * q
        + features.WEIGHTS["citation"] * np.array([0, 1, 0])
        + features.WEIGHTS["recency"] * 0.5
    ) / total
    np.testing.assert_allclose(scored.score, expected)
    with pytest.raises(ValueError):
        features.score({"question": np.zeros(2)}, 3)


def test_mmr_prefers_relevant_but_diverse_papers() -> None:
    a = np.array([1.0, 0.0, 0.0])
    near_a = np.array([0.99, 0.141, 0.0]) / np.linalg.norm([0.99, 0.141, 0.0])
    b = np.array([0.0, 1.0, 0.0])
    c = np.array([0.0, 0.0, 1.0])
    vectors = np.vstack([a, near_a, b, c])
    scores = np.array([1.0, 0.95, 0.7, 0.0])
    assert mmr.mmr_select(scores, vectors, 2, lam=1.0) == [0, 1]  # relevance only
    assert mmr.mmr_select(scores, vectors, 2, lam=0.7) == [0, 2]  # the near-duplicate waits
    assert mmr.mmr_select(scores, vectors, 10) == [0, 2, 1, 3]
    assert mmr.mmr_select(np.array([0.5, 0.5]), np.eye(2), 1) == [0]  # ties: lower index
    assert mmr.mmr_select(np.array([]), np.zeros((0, 3)), 5) == []


def test_preference_head() -> None:
    rng = np.random.default_rng(1)
    centre = np.zeros(16)
    centre[0] = 1.0
    positives = rng.normal(centre, 0.1, size=(4, 16))
    negatives = rng.normal(-centre, 0.1, size=(3, 16))
    assert preference.train(positives[:2], np.ones(2), negatives, np.ones(3)) is None  # < 3 positives
    assert preference.train(positives, np.ones(4), np.zeros((0, 16)), np.zeros(0)) is None  # no negatives
    model = preference.train(positives, np.ones(4), negatives, np.ones(3), rng.normal(size=(5, 16)))
    assert model is not None and (model.n_pos, model.n_neg, model.n_unlabeled) == (4, 3, 5)
    probe = np.vstack([centre, -centre])
    decision = model.decision(probe)
    assert decision[0] > decision[1]
    assert 0.5 < model.probability(probe)[0] <= 1.0
    sample = preference.sample_unlabeled(100, 3, seed=7)
    assert len(sample) == 30 and list(sample) == sorted(set(sample.tolist()))
    assert preference.sample_unlabeled(100, 3, seed=7).tolist() == sample.tolist()
    assert (
        len(preference.sample_unlabeled(5, 3, seed=7)) == 5 and len(preference.sample_unlabeled(9, 0, 1)) == 0
    )


def _explain_input(**overrides: Any) -> explain.ExplainInput:
    values = {
        "question": 0.9,
        "anchor": 0.8,
        "draft": None,
        "citation": None,
        "preference": 0.2,
        "recency": 0.5,
    }
    contributions = {
        "question": 0.3,
        "anchor": 0.2,
        "draft": 0.0,
        "citation": 0.0,
        "preference": 0.01,
        "recency": 0.04,
    }
    item = explain.ExplainInput(
        values=values,
        contributions=contributions,
        question_cosine=0.42,
        anchor={"paper_id": 3, "title": "Seed paper", "kind": "seed", "cosine": 0.61},
        preference={"n_labels": 4, "probability": 0.7},
        category="cs.IR",
        age_days=2,
    )
    for key, value in overrides.items():
        setattr(item, key, value)
    return item


def test_reasons_are_true_ordered_and_include_a_fact() -> None:
    reasons = explain.reasons(_explain_input())
    codes = [r["code"] for r in reasons]
    assert codes[:2] == ["question", "similar_to_anchor"]
    assert "preference" not in codes  # its value is weak: no claim is made
    assert codes[2] in ("recent", "category")  # the third pill is a plain fact
    assert reasons[1]["text"] == "Similar to “Seed paper” (your seed paper)"
    assert next(r for r in reasons if r["code"] == "recent")["text"] == "Published 2 days ago"
    adapter = TypeAdapter(Reason)
    for reason in reasons:
        adapter.validate_python(reason)  # every stored reason fits the API union
    weak = explain.reasons(_explain_input(values={**_explain_input().values, "question": 0.3, "anchor": 0.1}))
    assert [r["code"] for r in weak] == ["recent", "category"]
    cites = explain.reasons(
        _explain_input(
            values={**_explain_input().values, "citation": 1.0},
            contributions={**_explain_input().contributions, "citation": 0.5},
            citation={"paper_id": 3, "title": "Seed paper", "kind": "relevant", "direction": "cited_by"},
        )
    )
    assert cites[0]["code"] == "cited_by_anchor"
    assert cites[0]["text"] == "Cited by “Seed paper” (which you marked relevant)"
    assert explain.reasons(_explain_input(age_days=0))[-1]["text"] in (
        "Published today",
        "Listed in cs.IR, one of your project's categories",
    )


# ------------------------------------------------------------------------------------------------
# Deck builds
# ------------------------------------------------------------------------------------------------


def _papers(
    make_paper: Any, n: int, text: str, *, category: str = "cs.IR", days_old: int = 1, prefix: str = "On"
) -> list[int]:
    return [
        make_paper(
            title=f"{prefix} topic paper {i} about {text.split()[0].lower()}",
            abstract=f"{text} Variant {i}.",
            categories=[category],
            publication_date=TODAY - timedelta(days=days_old),
        )
        for i in range(n)
    ]


def _deck(project_id: int) -> Any:
    with session_scope() as session:
        slate_id = session.scalar(
            select(models.RecommendationSlate.id)
            .where(models.RecommendationSlate.project_id == project_id)
            .order_by(models.RecommendationSlate.id.desc())
            .limit(1)
        )
        assert slate_id is not None
        return slate_out(session, slate_id, project_id)


def test_projects_without_categories_or_papers_get_an_explained_empty_deck(
    make_project: Any, make_paper: Any
) -> None:
    project = make_project(research_question=QUESTION)
    build_slate(project)
    assert _deck(project).items == [] and "Add arXiv categories" in (_deck(project).message or "")
    other = make_project(research_question=QUESTION, settings={"arxiv_categories": ["cs.DL"]})
    _papers(make_paper, 2, ON_TOPIC, category="cs.IR")  # not in the project's category
    build_slate(other)
    assert "No papers from cs.DL in the last 14 days" in (_deck(other).message or "")


def test_deck_ranks_on_topic_papers_first_with_reasons(make_project: Any, make_paper: Any) -> None:
    project = make_project(
        research_question=QUESTION, settings={"arxiv_categories": ["cs.IR"], "slate_size": 5}
    )
    on = _papers(make_paper, 6, ON_TOPIC)
    _papers(make_paper, 6, OFF_TOPIC, prefix="Off")
    old = _papers(make_paper, 2, ON_TOPIC, days_old=40, prefix="Old")  # outside the 14-day window
    elsewhere = _papers(make_paper, 2, ON_TOPIC, category="q-bio.BM", prefix="Bio")
    build_slate(project)
    deck = _deck(project)
    ids = [item.paper.id for item in deck.items]
    assert len(ids) == 5 and deck.pool_size == 12
    assert set(ids).isdisjoint(set(old) | set(elsewhere))
    assert ids[0] in on and sum(1 for i in ids if i in on) >= 4
    top = deck.items[0]
    assert top.rank == 1 and top.features.question.available and not top.features.anchor.available
    assert top.reasons[0].code == "question" and top.reasons[0].text == "Close to your research question"
    assert any(r.code == "category" for r in top.reasons)
    assert abs(sum(f["contribution"] for f in top.features.model_dump().values()) - top.score) < 1e-4
    with session_scope() as session:
        slate = session.get(models.RecommendationSlate, deck.id)
        assert slate is not None and slate.ranker_version == "radar-0.3"
        assert slate.params_json["preference"]["trained"] is False
        assert slate.params_json["n_candidates"] == 12
        item = session.scalar(
            select(models.RecommendationItem).where(models.RecommendationItem.slate_id == deck.id)
        )
        assert item is not None
        stored = item.feature_scores_json
        assert set(stored) == set(features.FEATURES)
        assert abs(sum(v["contribution"] for v in stored.values()) - item.score) < 1e-4
        assert stored["question"]["evidence"]["cosine"] == pytest.approx(stored["question"]["raw"], abs=1e-6)


def test_seeds_labels_and_citations_shape_the_deck(make_project: Any, make_paper: Any) -> None:
    project = make_project(
        research_question=QUESTION, settings={"arxiv_categories": ["cs.IR"], "slate_size": 8}
    )
    seed = make_paper(
        title="Seed: citation recommendation with graph neural networks",
        abstract=ON_TOPIC,
        categories=["cs.IR"],
    )
    on = _papers(make_paper, 6, ON_TOPIC)
    off = _papers(make_paper, 6, OFF_TOPIC, prefix="Off")
    with session_scope(write=True) as session:
        session.add(models.ProjectSeed(project_id=project, paper_id=seed, added_via="arxiv"))
        upsert_edge(
            session,
            src=("work", off[0]),
            relation="CITES",
            dst=("work", seed),
            provenance="openalex",
            verified=True,
        )
        for pid in on[:3]:
            record_interaction(session, project, pid, "relevant")
        for pid in off[1:3]:
            record_interaction(session, project, pid, "not_relevant")
    build_slate(project)
    deck = _deck(project)
    ids = [item.paper.id for item in deck.items]
    assert seed not in ids and not set(ids) & (
        set(on[:3]) | set(off[1:3])
    )  # seeds and rated papers never return
    with session_scope() as session:
        slate = session.get(models.RecommendationSlate, deck.id)
        assert slate is not None and slate.params_json["preference"]["trained"] is True
        assert slate.params_json["preference"]["n_pos"] == 4 and slate.params_json["preference"]["n_neg"] == 2
        assert slate.params_json["n_anchors"] == 4
    citing = next((item for item in deck.items if item.paper.id == off[0]), None)
    assert citing is not None and citing.features.citation.value == 1.0
    assert any(r.code == "cites_anchor" and r.evidence.anchor_kind == "seed" for r in citing.reasons)
    anchored = [item for item in deck.items if item.features.anchor.available]
    assert anchored and all(item.features.preference.available for item in deck.items)


def test_draft_matches_are_explained_with_the_section(
    make_project: Any, make_paper: Any, client: TestClient
) -> None:
    project = make_project(
        research_question="unrelated words here", settings={"arxiv_categories": ["cs.IR"], "slate_size": 5}
    )
    _papers(make_paper, 3, OFF_TOPIC, prefix="Off")
    on = _papers(make_paper, 3, ON_TOPIC)
    draft = f"# Method\n\n{ON_TOPIC} {ON_TOPIC}\n"
    r = client.post(
        f"/api/projects/{project}/documents", files={"file": ("my-draft.md", draft.encode(), "text/markdown")}
    )
    assert (
        r.status_code == 202
        and client.get(f"/api/documents/{r.json()['document']['id']}/status").json()["status"] == "ready"
    )
    build_slate(project)
    deck = _deck(project)
    top = deck.items[0]
    assert top.paper.id in on and top.features.draft.available
    match = next(reason for reason in top.reasons if reason.code == "draft_match")
    assert match.text == "Matches your document “my-draft.md”, section “Method”"
    assert match.evidence.filename == "my-draft.md" and match.evidence.snippet.startswith(
        "Graph neural networks"
    )


def test_unrated_papers_return_once_and_more_skips_today(make_project: Any, make_paper: Any) -> None:
    project = make_project(
        research_question=QUESTION, settings={"arxiv_categories": ["cs.IR"], "slate_size": 5}
    )
    _papers(make_paper, 12, ON_TOPIC)
    build_slate(project)
    first = {i.paper.id for i in _deck(project).items}
    build_slate(project, "more")
    more = {i.paper.id for i in _deck(project).items}
    assert first.isdisjoint(more) and len(more) == 5  # "more" skips everything shown in the last 24 h
    build_slate(project)  # a new daily deck may show yesterday's unrated papers once more
    third = {i.paper.id for i in _deck(project).items}
    build_slate(project)
    fourth = {i.paper.id for i in _deck(project).items}
    shown_twice = (first & third) | (more & third)
    assert shown_twice.isdisjoint(fourth)  # ...but not a third time


def test_builds_are_deterministic(make_project: Any, make_paper: Any) -> None:
    project = make_project(
        research_question=QUESTION, settings={"arxiv_categories": ["cs.IR"], "slate_size": 6}
    )
    _papers(make_paper, 8, ON_TOPIC)
    _papers(make_paper, 8, OFF_TOPIC, prefix="Off")
    build_slate(project, "more")
    a = [(i.paper.id, round(i.score, 6)) for i in _deck(project).items]
    with session_scope(write=True) as session:  # forget the deck so the same candidates are eligible
        for slate in session.scalars(select(models.RecommendationSlate)):
            session.delete(slate)
    build_slate(project, "more")
    assert [(i.paper.id, round(i.score, 6)) for i in _deck(project).items] == a


# ------------------------------------------------------------------------------------------------
# Feed API
# ------------------------------------------------------------------------------------------------


def test_feed_status_rebuild_feedback_and_undo(
    client: TestClient, make_project: Any, make_paper: Any
) -> None:
    bare = client.post("/api/projects", json={"name": "Bare", "research_question": QUESTION}).json()
    feed = client.get(f"/api/projects/{bare['id']}/feed").json()
    assert feed["status"] == "needs_setup" and feed["slate"] is None and "arXiv categories" in feed["message"]

    _papers(make_paper, 8, ON_TOPIC)
    project = client.post(
        "/api/projects",
        json={
            "name": "Graphs",
            "research_question": QUESTION,
            "settings": {"arxiv_categories": ["cs.IR"], "slate_size": 5},
        },
    ).json()
    feed = client.get(f"/api/projects/{project['id']}/feed").json()  # creation built the first deck inline
    assert feed["status"] == "ready" and feed["job"] is None
    assert feed["last_job"]["kind"] == "build_deck" and feed["last_job"]["status"] == "succeeded"
    assert feed["last_job"]["stats"]["backfill"] == {"error": "EgressBlocked"}  # tests are offline
    slate = feed["slate"]
    assert slate["remaining"] == 5 and [i["rank"] for i in slate["items"]] == [1, 2, 3, 4, 5]
    first = slate["items"][0]

    response = client.post(
        f"/api/projects/{project['id']}/feedback",
        json={
            "paper_id": first["paper"]["id"],
            "action": "relevant",
            "slate_id": slate["id"],
            "context": {"source": "deck"},
        },
    ).json()
    assert response["state"] == "relevant" and response["triggered"]["expansion_job_id"] is not None
    opened = client.post(
        f"/api/projects/{project['id']}/feedback",
        json={"paper_id": slate["items"][1]["paper"]["id"], "action": "open"},
    ).json()
    assert opened["state"] is None and opened["triggered"]["expansion_job_id"] is None
    after = client.get(f"/api/projects/{project['id']}/feed").json()["slate"]
    assert after["remaining"] == 4 and after["items"][0]["state"] == "relevant"
    undone = client.post(f"/api/projects/{project['id']}/feedback/undo", json={}).json()
    assert undone["paper_id"] == first["paper"]["id"] and undone["state"] is None
    assert client.post(f"/api/projects/{project['id']}/feedback/undo", json={}).status_code == 404

    bad = client.post(f"/api/projects/{project['id']}/feedback", json={"paper_id": 987654, "action": "save"})
    assert bad.status_code == 404
    rebuilt = client.post(f"/api/projects/{project['id']}/feed/rebuild", json={"mode": "more"})
    assert rebuilt.status_code == 202 and rebuilt.json()["job"]["params"] == {
        "mode": "more",
        "prepare": False,
    }
    assert client.get(f"/api/projects/{project['id']}/feed").json()["slate"]["mode"] == "more"
    assert client.get("/api/projects/987654/feed").status_code == 404


# ------------------------------------------------------------------------------------------------
# Library, search, read marks, BibTeX
# ------------------------------------------------------------------------------------------------


def _rate(project_id: int, paper_id: int, action: str) -> None:
    with session_scope(write=True) as session:
        record_interaction(session, project_id, paper_id, action)


def test_library_filters_and_pages(client: TestClient, make_project: Any, make_paper: Any) -> None:
    project = make_project()
    papers = [make_paper(title=f"Library paper {i}") for i in range(5)]
    tagged = make_paper(title="A study of zqneedle retrieval")
    for pid in papers[:3]:
        _rate(project, pid, "relevant")
    _rate(project, papers[3], "save")
    _rate(project, papers[4], "not_relevant")
    _rate(project, tagged, "save")
    base = f"/api/papers?project_id={project}"
    everything = client.get(base).json()
    assert (
        everything["total"] == 5 and [i["paper"]["id"] for i in everything["items"]][0] == tagged
    )  # newest first
    assert {i["state"] for i in everything["items"]} == {"relevant", "saved"}
    saved = client.get(base + "&states=saved").json()
    assert {i["paper"]["id"] for i in saved["items"]} == {papers[3], tagged}
    assert client.get(base + "&states=pinned").status_code == 422
    assert [i["paper"]["id"] for i in client.get(base + "&query=ZQNEEDLE").json()["items"]] == [tagged]

    assert client.put(f"/api/papers/{tagged}/read", json={"read": True}).json()["read_at"] is not None
    assert [i["paper"]["id"] for i in client.get(base + "&read=read").json()["items"]] == [tagged]
    assert client.get(base + "&read=unread").json()["total"] == 4
    assert client.get(base + "&read=read").json()["items"][0]["paper"]["read_at"] is not None
    assert client.put(f"/api/papers/{tagged}/read", json={"read": False}).json() == {
        "paper_id": tagged,
        "read_at": None,
    }
    assert client.put("/api/papers/987654/read", json={"read": True}).status_code == 404

    page1 = client.get(base + "&limit=2").json()
    page2 = client.get(base + f"&limit=2&cursor={page1['next_cursor']}").json()
    page3 = client.get(base + f"&limit=2&cursor={page2['next_cursor']}").json()
    ids = [i["paper"]["id"] for p in (page1, page2, page3) for i in p["items"]]
    assert len(ids) == 5 and len(set(ids)) == 5 and page3["next_cursor"] is None
    assert client.get(base + "&cursor=garbage").status_code == 422


def test_paper_detail_and_search(client: TestClient, make_project: Any, make_paper: Any) -> None:
    project = make_project()
    target = make_paper(
        title="Contrastive zqprobe learning for scholarly search", identifiers={"arxiv": "2610.12345"}
    )
    make_paper(title="Unrelated work", abstract="It mentions zqprobe only in passing.")
    _rate(project, target, "save")
    detail = client.get(f"/api/papers/{target}?project_id={project}").json()
    assert detail["state"] == "saved" and detail["disruption"] is None
    assert client.get(f"/api/papers/{target}?project_id=987654").status_code == 404
    hits = client.get("/api/search?query=zqprobe").json()["items"]
    assert [h["id"] for h in hits][0] == target and len(hits) == 2  # title matches come first
    assert [h["id"] for h in client.get("/api/search?query=arXiv:2610.12345v2").json()["items"]] == [target]
    assert client.get("/api/search?query=10.9999/none").json()["items"] == []


def test_bibtex_export(client: TestClient, make_project: Any, make_paper: Any) -> None:
    project = make_project()
    a = make_paper(
        title="The {Tricky} paper: 100% of $costs & more",
        authors=["Ada Lovelace", "Grace Hopper"],
        identifiers={"arxiv": "2610.00001"},
        year=2026,
    )
    b = make_paper(
        title="Tricky follow-up",
        authors=["Ada Lovelace"],
        identifiers={"doi": "10.1000/xyz"},
        year=2026,
        venue="Journal of Tests",
    )
    c = make_paper(title="Tricky again", authors=["Ada Lovelace"], year=2026)
    for pid, action in ((a, "relevant"), (b, "save"), (c, "save")):
        _rate(project, pid, action)
    r = client.get(f"/api/exports/bibtex?project_id={project}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/x-bibtex")
    assert r.headers["content-disposition"] == f'attachment; filename="ratiocination-{project}.bib"'
    text = r.text
    assert text.count("@") == 3
    assert (
        "@misc{lovelace2026tricky," in text
        and "@article{lovelace2026trickya," in text
        or "lovelace2026trickya" in text
    )
    assert r"title = {{The \{Tricky\} paper: 100\% of \$costs \& more}}" in text
    assert "eprint = {2610.00001}" in text and "archivePrefix = {arXiv}" in text
    assert "journal = {Journal of Tests}" in text and "doi = {10.1000/xyz}" in text
    assert "author = {Ada Lovelace and Grace Hopper}" in text
    keys = [line.split("{", 1)[1].rstrip(",") for line in text.splitlines() if line.startswith("@")]
    assert len(keys) == len(set(keys)) == 3
    assert client.get(f"/api/exports/bibtex?project_id={project}&states=saved").text.count("@") == 2
    with session_scope() as session:
        summary = paper_summaries(session, [c])[c]
    assert base_key(summary) == "lovelace2026tricky" and escape("a_b ~ c") == r"a\_b \textasciitilde{} c"
    assert bibtex([]) == ""


def test_tz_aware_today_default() -> None:
    assert isinstance(TODAY, date)
