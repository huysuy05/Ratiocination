"""Dashboard (SPEC §14): activity, topics, quoted findings, relation to the user's work, the AI summary
job (with the privacy check on the exact Anthropic request body), scope validation and user isolation.

Interactions are recorded through ``record_interaction`` and then back-dated; vectors are stored by
hand (unit vectors of the hashing model's 1024 dimensions) so every similarity is known exactly."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from radar import models
from radar.connectors import anthropic
from radar.dashboard import summary
from radar.dashboard.activity import streak
from radar.dashboard.common import category_label, make_scope
from radar.dashboard.findings import best_finding, score_sentence, split_sentences
from radar.dashboard.work import correlation, quartiles, strength
from radar.db import session_scope
from radar.embeddings.vectors import store_vectors
from radar.errors import ApiError
from radar.net.http import SourceUnavailable
from radar.store.graph import get_or_create_concept, upsert_edge
from radar.store.states import record_interaction, undo_interaction

MODEL = "local/hashing-v1"
DIM = 1024
MESSAGES = "https://api.anthropic.com/v1/messages"
TODAY = datetime.now(UTC).date()


# ------------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------------


def at(days_ago: int, hour: int = 12) -> datetime:
    return datetime.combine(TODAY - timedelta(days=days_ago), time(hour), tzinfo=UTC)


def swipe(project_id: int, paper_id: int, action: str, when: datetime | None = None) -> int:
    """Record an interaction (and its state), back-dated to ``when``."""
    with session_scope(write=True) as s:
        interaction = record_interaction(s, project_id, paper_id, action)
        if when is not None:
            interaction.timestamp = when
        s.flush()
        return interaction.id


def unit(*parts: tuple[int, float]) -> np.ndarray:
    v = np.zeros(DIM)
    for index, value in parts:
        v[index] = value
    return v


def with_cos(axis: int, cosine: float, spare: int) -> np.ndarray:
    """A unit vector whose cosine with axis ``axis`` is ``cosine`` (the rest goes to axis ``spare``)."""
    return unit((axis, cosine), (spare, math.sqrt(max(0.0, 1 - cosine**2))))


def put_vectors(entity_type: str, vectors: dict[int, np.ndarray]) -> None:
    ids = list(vectors)
    with session_scope(write=True) as s:
        store_vectors(s, entity_type, ids, np.array([vectors[i] for i in ids]), MODEL, [f"h{i}" for i in ids])


def add_document(project_id: int, sections: list[tuple[str, np.ndarray | None]]) -> tuple[int, list[int]]:
    """A ready document whose sections each have one chunk (with the given vector, if any)."""
    chunk_vectors: dict[int, np.ndarray] = {}
    section_ids: list[int] = []
    with session_scope(write=True) as s:
        doc = models.Document(
            owner_id=1,
            project_id=project_id,
            filename="thesis.md",
            mime_type="text/markdown",
            size_bytes=10,
            checksum=f"{project_id:064d}",
            storage_uri=f"uploads/{project_id}/thesis.md",
            status="ready",
        )
        s.add(doc)
        s.flush()
        for ordinal, (heading, vector) in enumerate(sections):
            text = f"Private draft text ZQX-DOC of section {heading}."
            section = models.DocumentSection(
                document_id=doc.id,
                ordinal=ordinal,
                heading=heading,
                page_start=ordinal + 1,
                page_end=ordinal + 1,
                char_start=0,
                char_end=len(text),
                text=text,
                text_hash="x",
                extraction_version="1",
            )
            s.add(section)
            s.flush()
            section_ids.append(section.id)
            chunk = models.DocumentChunk(
                document_id=doc.id,
                section_id=section.id,
                chunk_index=ordinal,
                text=text,
                token_count=8,
                char_start=0,
                char_end=len(text),
                page=ordinal + 1,
                text_hash="x",
            )
            s.add(chunk)
            s.flush()
            if vector is not None:
                chunk_vectors[chunk.id] = vector
        document_id = doc.id
    if chunk_vectors:
        put_vectors("chunk", chunk_vectors)
    return document_id, section_ids


def add_topic(paper_id: int, external_id: str, label: str, confidence: float) -> int:
    with session_scope(write=True) as s:
        concept = get_or_create_concept(s, "openalex_topic", external_id, label)
        upsert_edge(
            s,
            src=("work", paper_id),
            relation="HAS_TOPIC",
            dst=("concept", concept.id),
            provenance="openalex",
            verified=True,
            confidence=confidence,
        )
        return concept.id


def by_key(stats: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {s["key"]: s for s in stats}


RESULT_ABSTRACT = (
    "Retrieval models are widely used. We propose a sparse retriever for long documents. "
    "Experiments show that it outperforms dense baselines by 12% on three benchmarks."
)
BACKGROUND_ABSTRACT = (
    "Recently, large models have attracted attention. This remains challenging. We study it."
)


# ------------------------------------------------------------------------------------------------
# Pure pieces
# ------------------------------------------------------------------------------------------------


def test_sentence_splitter_keeps_abbreviations_and_decimals() -> None:
    text = (
        "Prior work (e.g. Smith et al. 2020) used Fig. 3 settings, i.e. a 0.5 dropout vs. none. "
        "We find a 2.5x speedup! Does it hold? Yes."
    )
    assert split_sentences(text) == [
        "Prior work (e.g. Smith et al. 2020) used Fig. 3 settings, i.e. a 0.5 dropout vs. none.",
        "We find a 2.5x speedup!",
        "Does it hold?",
        "Yes.",
    ]
    assert split_sentences("  ") == []


def test_finding_scores_follow_the_cues() -> None:
    result = score_sentence("Experiments show that our model outperforms the baselines by 12% overall.", 2, 3)
    # results show (+2) outperforms (+2) capped at +4, a number (+1), one of the last two (+1)
    assert result.score == 6 and result.cues == ["results indicate", "outperforms"]
    background = score_sentence(
        "Recently, retrieval has attracted a lot of attention from the community.", 0, 3
    )
    assert background.score == -2 and background.cues == []  # first sentence, background cue
    short = score_sentence("We show it works.", 1, 3)
    assert short.score == 1  # we show (+2), last two (+1), fewer than 8 words (-2)

    finding = best_finding(RESULT_ABSTRACT)
    assert finding is not None
    assert (
        finding.sentence == "Experiments show that it outperforms dense baselines by 12% on three benchmarks."
    )
    assert best_finding(BACKGROUND_ABSTRACT) is None
    assert best_finding(None) is None


def test_streak_counts_back_from_today_or_yesterday() -> None:
    today = date(2026, 10, 12)
    days = {today, today - timedelta(days=1), today - timedelta(days=3)}
    assert streak(days, today) == 2
    assert streak({today - timedelta(days=1), today - timedelta(days=2)}, today) == 2  # none today yet
    assert streak({today - timedelta(days=2)}, today) == 0


def test_quartiles_and_correlation() -> None:
    pairs = [(0.1, 0)] * 6 + [(0.4, 1), (0.5, 1), (0.8, 1), (0.9, 1)]
    groups = quartiles(pairs)
    assert [(g.n, g.positive_rate) for g in groups] == [(3, 0.0), (3, 0.0), (2, 1.0), (2, 1.0)]
    assert (groups[2].lo, groups[2].hi) == (0.4, 0.5)
    assert quartiles(pairs[:7]) == []

    r = correlation(pairs)
    assert r is not None and r.strength == "strong" and r.n == 10 and r.r > 0.8
    assert r.text.startswith("Papers closer to your work were more likely to be marked relevant (r = 0.")
    negative = correlation([(s, 1 - y) for s, y in pairs])
    assert negative is not None and negative.r < 0 and "−" in negative.text
    assert "further from your work" in negative.text
    assert correlation(pairs[:9]) is None
    assert correlation([(0.1 * i, 1) for i in range(12)]) is None  # one label only
    assert [strength(x) for x in (0.05, -0.2, 0.45, -0.7)] == ["none", "weak", "moderate", "strong"]


def test_category_labels() -> None:
    assert category_label("cs.LG") == "Machine Learning (cs.LG)"
    assert category_label("xx.YY") == "xx.YY"


def test_summary_validation_keeps_only_cited_points() -> None:
    reply = {
        "themes": [
            {
                "title": "Sparse retrieval",
                "points": [
                    {"text": "Sparse models beat dense ones [1, 2] on long inputs.", "cites": [1, 2, 9]},
                    {"text": "An uncited claim.", "cites": []},
                    {"text": "Only an out-of-range cite.", "cites": [0, 4]},
                    {"text": "A boolean is not a cite.", "cites": [True]},
                ],
            },
            {"title": "Empty", "points": [{"text": "x", "cites": "1"}]},
            {"title": "Evaluation " * 20, "points": [{"text": "Benchmarks disagree.", "cites": [3, 1]}]},
        ]
    }
    themes, dropped = summary.validate(reply, [101, 102, 103])
    assert dropped == 4
    assert themes[0] == {
        "title": "Sparse retrieval",
        "points": [{"text": "Sparse models beat dense ones on long inputs.", "cites": [101, 102]}],
    }
    assert len(themes) == 2 and themes[1]["points"] == [{"text": "Benchmarks disagree.", "cites": [101, 103]}]
    assert len(themes[1]["title"]) <= 80 and themes[1]["title"].endswith("…")
    assert summary.validate("not a dict", [1]) == ([], 0)

    many = {"themes": [{"title": f"T{i}", "points": [{"text": "p", "cites": [1]}] * 6} for i in range(8)]}
    themes, dropped = summary.validate(many, [1])
    assert len(themes) == 6 and all(len(t["points"]) == 4 for t in themes) and dropped == 0


def test_clip_cuts_at_a_word_boundary() -> None:
    assert summary.clip("a  b\n c", 10) == "a b c"
    clipped = summary.clip("word " * 400, 1500)
    assert len(clipped) <= 1500 and clipped.endswith("word…")
    assert summary.clip(None, 5) == ""


def test_anthropic_reply_parsing() -> None:
    payload = {
        "model": "claude-x",
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "Here you go"},
            {"type": "tool_use", "id": "t1", "name": "report_findings", "input": {"themes": []}},
        ],
        "usage": {"input_tokens": 900, "output_tokens": 120},
    }
    call = anthropic.parse_tool_call(payload, "report_findings")
    assert call == anthropic.ToolCall(
        input={"themes": []}, model="claude-x", stop_reason="tool_use", input_tokens=900, output_tokens=120
    )
    assert anthropic.parse_tool_call(payload, "other_tool") is None
    assert anthropic.parse_tool_call([], "report_findings") is None


# ------------------------------------------------------------------------------------------------
# Scope
# ------------------------------------------------------------------------------------------------


def test_scope_periods_and_time_zones(make_project: Callable[..., int]) -> None:
    project_id = make_project()
    with session_scope() as s:
        scope = make_scope(s, 1, 7, None, "Asia/Tokyo")
        assert scope.project_ids == [project_id] and scope.project_filter is None
        assert (scope.end - scope.start).days == 6
        assert scope.previous_end == scope.start - timedelta(days=1)
        assert (scope.previous_end - scope.previous_start).days == 6
        moment = datetime(2026, 3, 1, 20, 0, tzinfo=UTC)  # already Mar 2 in Tokyo
        assert scope.local_day(moment) == date(2026, 3, 2)
        assert scope.utc(date(2026, 3, 2)) == datetime(2026, 3, 1, 15, 0, tzinfo=UTC)
        assert scope.bucket == "day" and make_scope(s, 1, 365, None, "UTC").bucket == "week"
        weeks = scope.week_starts(12)
        assert len(weeks) == 12 and all(w.weekday() == 0 for w in weeks) and weeks[-1] <= scope.end

        with pytest.raises(ApiError) as bad_zone:
            make_scope(s, 1, 7, None, "Mars/Olympus")
        assert bad_zone.value.status == 422
        with pytest.raises(ApiError) as bad_days:
            make_scope(s, 1, 14, None, None)
        assert bad_days.value.status == 422
        with pytest.raises(ApiError) as missing:
            make_scope(s, 1, 7, 9999, None)
        assert missing.value.status == 404


def test_dashboard_endpoints_validate_their_query(client: TestClient) -> None:
    for name in ("activity", "topics", "findings", "work"):
        assert client.get(f"/api/dashboard/{name}?days=14").status_code == 422
        r = client.get(f"/api/dashboard/{name}?tz=Mars/Olympus")
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"
        assert client.get(f"/api/dashboard/{name}?project_id=9999").status_code == 404
        empty = client.get(f"/api/dashboard/{name}?tz=UTC")
        assert empty.status_code == 200, empty.text


# ------------------------------------------------------------------------------------------------
# Activity
# ------------------------------------------------------------------------------------------------


def test_activity_counts_the_period_and_the_one_before(
    client: TestClient, make_project: Callable[..., int], make_paper: Callable[..., int]
) -> None:
    project_id = make_project(name="Retrieval")
    other_project = make_project(name="Other")
    a, b, c, d, e, f, g, h = (make_paper() for _ in range(8))
    swipe(project_id, a, "relevant", at(1))
    swipe(project_id, b, "not_relevant", at(1))
    swipe(project_id, c, "save", at(0, hour=0))
    swipe(project_id, d, "relevant", at(3))
    swipe(other_project, h, "relevant", at(2))
    swipe(project_id, a, "open", at(1))
    swipe(project_id, a, "open", at(1))
    swipe(project_id, e, "relevant", at(10))
    swipe(project_id, f, "not_relevant", at(9))
    undone = swipe(project_id, g, "relevant", at(2))
    with session_scope(write=True) as s:
        undo_interaction(s, project_id, undone)
        s.add(models.PaperRead(user_id=1, paper_id=a, read_at=at(0, hour=0)))
        s.add(models.PaperRead(user_id=1, paper_id=e, read_at=at(11)))
        for generated in (at(0, hour=0), at(12)):
            s.add(
                models.RecommendationSlate(
                    owner_id=1,
                    project_id=project_id,
                    generated_at=generated,
                    mode="daily",
                    ranker_version="t",
                    embedding_model=MODEL,
                    candidate_set_hash="h",
                    pool_size=0,
                )
            )

    r = client.get("/api/dashboard/activity", params={"days": 7, "tz": "UTC"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["period"] == {
        "start": (TODAY - timedelta(days=6)).isoformat(),
        "end": TODAY.isoformat(),
        "days": 7,
        "bucket": "day",
        "tz": "UTC",
    }
    assert body["totals"] == {
        "swiped": 5,
        "relevant": 3,
        "not_relevant": 1,
        "saved": 1,
        "opened": 1,
        "read": 1,
        "decks": 1,
    }
    assert body["previous"] == {
        "swiped": 2,
        "relevant": 1,
        "not_relevant": 1,
        "saved": 0,
        "opened": 0,
        "read": 1,
        "decks": 1,
    }
    assert body["positive_rate"] == 0.8 and body["positive_rate_before"] == 0.5
    assert body["streak_days"] == 4  # today, 1, 2 and 3 days ago (the undone swipe does not count)
    assert body["all_time"] == {"swiped": 7, "read": 2, "library": 5}
    assert len(body["series"]) == 7 and sum(p["swiped"] for p in body["series"]) == 5
    assert body["series"][-2] == {"date": (TODAY - timedelta(days=1)).isoformat(), "swiped": 2, "positive": 1}
    assert body["by_project"] == [
        {"project_id": project_id, "name": "Retrieval", "swiped": 4, "positive": 3},
        {"project_id": other_project, "name": "Other", "swiped": 1, "positive": 1},
    ]

    one = client.get("/api/dashboard/activity", params={"days": 7, "tz": "UTC", "project_id": other_project})
    assert one.json()["totals"]["swiped"] == 1 and one.json()["totals"]["read"] == 0

    year = client.get("/api/dashboard/activity", params={"days": 365, "tz": "UTC"}).json()
    assert year["period"]["bucket"] == "week"
    assert all(date.fromisoformat(p["date"]).weekday() == 0 for p in year["series"])
    assert sum(p["swiped"] for p in year["series"]) == 7


# ------------------------------------------------------------------------------------------------
# Topics
# ------------------------------------------------------------------------------------------------


def test_topics_lean_in_fade_and_skip(
    client: TestClient, make_project: Callable[..., int], make_paper: Callable[..., int]
) -> None:
    project_id = make_project()
    lg = [make_paper(categories=["cs.LG"]) for _ in range(3)]
    ir_now = make_paper(categories=["cs.IR"])
    ir_before = [make_paper(categories=["cs.IR"]) for _ in range(3)]
    cv = [make_paper(categories=["cs.CV"]) for _ in range(4)]
    for pid in lg:
        swipe(project_id, pid, "relevant", at(1))
    swipe(project_id, ir_now, "save", at(2))
    for pid in ir_before:
        swipe(project_id, pid, "relevant", at(10))
    for pid in cv:
        swipe(project_id, pid, "not_relevant", at(1))

    body = client.get("/api/dashboard/topics", params={"days": 7, "tz": "UTC"}).json()
    assert (body["n_positive"], body["n_positive_before"]) == (4, 3)
    assert [t["key"] for t in body["leaning_in"]] == ["cat:cs.LG"]
    lean = body["leaning_in"][0]
    assert lean["label"] == "Machine Learning (cs.LG)" and lean["kind"] == "category"
    assert (lean["count"], lean["share"], lean["count_before"], lean["delta"]) == (3, 0.75, 0, 0.75)
    assert [t["key"] for t in body["fading"]] == ["cat:cs.IR"]
    assert body["fading"][0]["delta"] == -0.75
    assert [t["key"] for t in body["top"]] == ["cat:cs.LG", "cat:cs.IR"]
    assert [(t["key"], t["swiped"], t["skip_rate"]) for t in body["skipping"]] == [("cat:cs.CV", 4, 1.0)]
    assert body["coverage"] == 0.0
    assert len(body["weeks"]) == 12 and sum(lean["weekly"]) == 3


def test_openalex_topic_wins_over_the_category(
    client: TestClient, make_project: Callable[..., int], make_paper: Callable[..., int]
) -> None:
    project_id = make_project()
    papers = [make_paper(categories=["cs.IR"]) for _ in range(3)]
    for pid in papers[:2]:
        concept_id = add_topic(pid, "T10215", "Recommender Systems", 0.98)
        add_topic(pid, "T999", "Something weaker", 0.4)
        swipe(project_id, pid, "relevant", at(0, hour=0))
    swipe(project_id, papers[2], "relevant", at(0, hour=0))
    body = client.get("/api/dashboard/topics", params={"days": 7, "tz": "UTC"}).json()
    top = by_key(body["top"])
    assert top[f"concept:{concept_id}"]["label"] == "Recommender Systems"
    assert (
        top[f"concept:{concept_id}"]["count"] == 2
        and top[f"concept:{concept_id}"]["concept_id"] == concept_id
    )
    assert top["cat:cs.IR"]["count"] == 1
    assert body["coverage"] == round(2 / 3, 4)


# ------------------------------------------------------------------------------------------------
# Findings
# ------------------------------------------------------------------------------------------------


def test_findings_quote_abstracts_by_topic(
    client: TestClient, make_project: Callable[..., int], make_paper: Callable[..., int]
) -> None:
    project_id = make_project()
    strong = make_paper(categories=["cs.LG"], abstract=RESULT_ABSTRACT, title="Sparse long retrieval")
    close = make_paper(
        categories=["cs.LG"],
        abstract="We introduce a benchmark. Results show that rerankers improve recall by 4 points on average.",
    )
    far = make_paper(
        categories=["cs.LG"],
        abstract="We introduce a corpus. Results show that rerankers improve recall by 3 points on average.",
    )
    none = make_paper(categories=["cs.IR"], abstract=BACKGROUND_ABSTRACT)
    missing = make_paper(categories=["cs.IR"], abstract=None)
    rejected = make_paper(categories=["cs.LG"], abstract=RESULT_ABSTRACT)
    put_vectors("project", {project_id: unit((0, 1.0))})
    put_vectors(
        "paper", {close: with_cos(0, 0.9, 10), far: with_cos(0, 0.2, 11), strong: with_cos(0, 0.5, 12)}
    )
    swipe(project_id, strong, "relevant", at(2))
    swipe(project_id, close, "save", at(3))
    swipe(project_id, far, "relevant", at(1))
    swipe(project_id, none, "relevant", at(1))
    swipe(project_id, missing, "relevant", at(1))
    swipe(project_id, rejected, "not_relevant", at(1))

    body = client.get("/api/dashboard/findings", params={"days": 7, "tz": "UTC"}).json()
    assert (body["n_papers"], body["n_with_findings"]) == (5, 3)
    assert [g["topic"]["key"] for g in body["groups"]] == ["cat:cs.LG"]
    findings = body["groups"][0]["findings"]
    assert [f["paper"]["id"] for f in findings] == [strong, close, far]  # score, then similarity
    assert findings[0]["sentence"] == (
        "Experiments show that it outperforms dense baselines by 12% on three benchmarks."
    )
    assert findings[0]["cues"] == ["results indicate", "outperforms"]
    assert findings[0]["paper"]["title"] == "Sparse long retrieval"
    assert (findings[1]["similarity"], findings[2]["similarity"]) == (0.9, 0.2)
    assert findings[1]["action"] == "save" and findings[1]["acted_at"].startswith(
        (TODAY - timedelta(days=3)).isoformat()
    )
    assert body["summary"] is None
    assert body["ai"] == {"available": False, "model": None, "remaining_today": 0, "job": None}


# ------------------------------------------------------------------------------------------------
# Relation to your work
# ------------------------------------------------------------------------------------------------


def test_work_relation_closest_quartiles_correlation_sections_and_drift(
    client: TestClient, make_project: Callable[..., int], make_paper: Callable[..., int]
) -> None:
    project_id = make_project(name="Thesis")
    _, (intro, methods, refs, discussion) = add_document(
        project_id,
        [
            ("Introduction", unit((1, 1.0))),
            ("Methods", unit((2, 1.0))),
            ("7 References", unit((3, 1.0))),
            ("Discussion", unit((4, 1.0))),
        ],
    )
    put_vectors("project", {project_id: unit((0, 1.0))})
    l1, l2, l3, l4 = (make_paper() for _ in range(4))
    negatives = [make_paper() for _ in range(6)]
    vectors = {
        l1: with_cos(1, 0.9, 20),  # Introduction
        l2: with_cos(0, 0.8, 21),  # the question
        l3: with_cos(2, 0.5, 22),  # Methods
        l4: with_cos(3, 0.4, 23),  # References (not a section)
    }
    vectors |= {pid: with_cos(0, 0.1, 30 + i) for i, pid in enumerate(negatives)}
    put_vectors("paper", vectors)
    for pid in (l1, l2, l4):
        swipe(project_id, pid, "relevant")
    swipe(project_id, l3, "save")
    for pid in negatives:
        swipe(project_id, pid, "not_relevant")
    no_vector = make_paper()
    swipe(project_id, no_vector, "relevant")

    body = client.get("/api/dashboard/work", params={"days": 30, "tz": "UTC"}).json()
    (project,) = body["projects"]
    assert (project["project_id"], project["name"], project["has_documents"]) == (project_id, "Thesis", True)
    assert project["threshold"] == 0.3
    assert [(p["paper"]["id"], p["similarity"]) for p in project["closest"]] == [
        (l1, 0.9),
        (l2, 0.8),
        (l3, 0.5),
        (l4, 0.4),
    ]
    assert project["closest"][0]["match"] == {
        "kind": "document",
        "document_id": project["sections"][0]["document_id"],
        "filename": "thesis.md",
        "section": "Introduction",
        "page": 1,
        "snippet": "Private draft text ZQX-DOC of section Introduction.",
    }
    assert project["closest"][1]["match"] == {"kind": "question"}

    assert [(q["n"], q["positive_rate"]) for q in project["quartiles"]] == [
        (3, 0.0),
        (3, 0.0),
        (2, 1.0),
        (2, 1.0),
    ]
    corr = project["correlation"]
    assert corr["n"] == 10 and corr["strength"] == "strong" and corr["r"] > 0.8
    assert corr["text"].startswith("Papers closer to your work were more likely to be marked relevant")

    sections = {s["section_id"]: s for s in project["sections"]}
    assert refs not in sections
    assert sections[intro]["n_supporting"] == 1 and sections[intro]["best"]["paper"]["id"] == l1
    assert sections[methods]["n_supporting"] == 1 and sections[methods]["best"]["paper"]["id"] == l3
    assert sections[methods]["best"]["match"]["section"] == "Methods"
    assert sections[discussion] == {
        "document_id": sections[intro]["document_id"],
        "filename": "thesis.md",
        "section_id": discussion,
        "heading": "Discussion",
        "n_supporting": 0,
        "best": None,
    }

    assert len(project["drift"]) == 12
    this_week = project["drift"][-1]
    assert this_week["n"] == 4 and this_week["mean_similarity"] == 0.2  # (0 + 0.8 + 0 + 0) / 4
    assert all(p["mean_similarity"] is None and p["n"] == 0 for p in project["drift"][:-1])


def test_work_without_vectors_or_documents(client: TestClient, make_project: Callable[..., int]) -> None:
    project_id = make_project(name="Fresh")
    body = client.get("/api/dashboard/work", params={"tz": "UTC"}).json()
    assert body["projects"] == [
        {
            "project_id": project_id,
            "name": "Fresh",
            "has_documents": False,
            "threshold": 0.3,
            "closest": [],
            "quartiles": [],
            "correlation": None,
            "sections": [],
            "drift": body["projects"][0]["drift"],
        }
    ]
    assert all(p["mean_similarity"] is None for p in body["projects"][0]["drift"])


# ------------------------------------------------------------------------------------------------
# AI summary
# ------------------------------------------------------------------------------------------------


def tool_reply(themes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "stop_reason": "tool_use",
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "report_findings", "input": {"themes": themes}}
        ],
        "usage": {"input_tokens": 1200, "output_tokens": 150},
    }


@pytest.fixture
def ai_on(set_env: Callable[..., None]) -> None:
    set_env(ANTHROPIC_API_KEY="sk-ant-test-ZQX-KEY")


@pytest.fixture
def liked_papers(make_project: Callable[..., int], make_paper: Callable[..., int]) -> tuple[int, list[int]]:
    """A project full of private canaries and four liked papers (one without an abstract)."""
    with session_scope(write=True) as s:
        user = s.get(models.User, 1)
        assert user is not None
        user.email = "ada-zqx@example.org"
        user.display_name = "Ada ZQX-NAME"
    project_id = make_project(
        name="Secret ZQX-PROJECT",
        research_question="Private ZQX-QUESTION about canaries",
        description="ZQX-DESC",
        keywords_json=["ZQX-KEYWORD"],
    )
    add_document(project_id, [("Introduction", None)])
    papers = [
        make_paper(title=f"Public title {i}", abstract=f"Public abstract {i}. We show result {i}.")
        for i in range(3)
    ]
    papers.append(make_paper(title="No abstract", abstract=None))
    for days_ago, pid in zip((3, 2, 1, 1), papers, strict=True):
        swipe(project_id, pid, "relevant", at(days_ago))
    return project_id, papers


def test_summary_job_sends_only_public_abstracts_and_stores_cited_points(
    ai_on: None, client: TestClient, http_mock: Any, liked_papers: tuple[int, list[int]]
) -> None:
    project_id, papers = liked_papers
    http_mock.add(
        "POST",
        MESSAGES,
        tool_reply(
            [
                {
                    "title": "Results",
                    "points": [
                        {"text": "Results hold [1] across settings.", "cites": [1, 3, 7]},
                        {"text": "Unsupported.", "cites": [8]},
                    ],
                }
            ]
        ),
    )
    r = client.post("/api/dashboard/summary", json={"days": 30, "tz": "UTC"})
    assert r.status_code == 202, r.text
    job = r.json()["job"]
    assert job["kind"] == "summarize_findings" and job["status"] == "succeeded", job
    assert job["params"] == {"user_id": 1, "days": 30, "tz": "UTC"}
    assert job["stats"]["n_papers"] == 3 and job["stats"]["dropped_points"] == 1

    (request,) = http_mock.requests
    assert request.headers["x-api-key"] == "sk-ant-test-ZQX-KEY"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert set(body) == {"model", "max_tokens", "system", "messages", "tools", "tool_choice"}
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 1500
    assert body["tool_choice"] == {"type": "tool", "name": "report_findings"}
    assert body["tools"][0]["input_schema"] == summary.INPUT_SCHEMA
    assert body["messages"] == [
        {
            "role": "user",
            "content": (
                "[1] Public title 2\nPublic abstract 2. We show result 2.\n\n"
                "[2] Public title 1\nPublic abstract 1. We show result 1.\n\n"
                "[3] Public title 0\nPublic abstract 0. We show result 0."
            ),
        }
    ]
    raw = request.content.decode()
    for canary in (
        "ZQX-PROJECT",
        "ZQX-QUESTION",
        "ZQX-DESC",
        "ZQX-KEYWORD",
        "ZQX-DOC",
        "ZQX-NAME",
        "ada-zqx",
        "ZQX-KEY",
    ):
        assert canary not in raw, canary

    found = client.get("/api/dashboard/findings", params={"days": 30, "tz": "UTC"}).json()
    stored = found["summary"]
    assert stored["n_papers"] == 3 and stored["dropped_points"] == 1 and stored["stale"] is False
    assert stored["model"] == "claude-opus-5-5"
    assert stored["themes"] == [
        {
            "title": "Results",
            "points": [
                {
                    "text": "Results hold across settings.",
                    "papers": [
                        {"id": papers[2], "title": "Public title 2"},
                        {"id": papers[0], "title": "Public title 0"},
                    ],
                }
            ],
        }
    ]
    assert found["ai"]["available"] is True and found["ai"]["remaining_today"] == 4
    assert found["ai"]["job"] is None  # the job finished before the summary it wrote
    with session_scope() as s:
        row = s.query(models.FindingsSummary).one()
        assert (row.user_id, row.project_id, row.days, row.input_tokens, row.output_tokens) == (
            1,
            0,
            30,
            1200,
            150,
        )

    one_project = client.get(
        "/api/dashboard/findings", params={"days": 30, "tz": "UTC", "project_id": project_id}
    )
    assert one_project.json()["summary"] is None  # summaries are per (user, project, days)


def test_summary_becomes_stale_when_liked_papers_change(
    ai_on: None,
    client: TestClient,
    http_mock: Any,
    liked_papers: tuple[int, list[int]],
    make_paper: Callable[..., int],
) -> None:
    project_id, _ = liked_papers
    http_mock.add("POST", MESSAGES, tool_reply([{"title": "T", "points": [{"text": "p", "cites": [1]}]}]))
    assert client.post("/api/dashboard/summary", json={"days": 30, "tz": "UTC"}).status_code == 202
    swipe(project_id, make_paper(abstract="A new public abstract. We find something."), "save")
    found = client.get("/api/dashboard/findings", params={"days": 30, "tz": "UTC"}).json()
    assert found["summary"]["stale"] is True


def test_summary_request_errors(
    set_env: Callable[..., None],
    client: TestClient,
    http_mock: Any,
    make_project: Callable[..., int],
    make_paper: Callable[..., int],
) -> None:
    r = client.post("/api/dashboard/summary", json={"days": 30})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_configured"

    set_env(ANTHROPIC_API_KEY="sk-ant-test", RADAR_SUMMARIES_PER_DAY="1")
    project_id = make_project()
    papers = [make_paper(abstract=f"Abstract {i}. We show {i}.") for i in range(2)]
    for pid in papers:
        swipe(project_id, pid, "relevant")
    r = client.post("/api/dashboard/summary", json={"days": 30, "tz": "UTC"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_enough_papers"
    assert "found 2" in r.json()["error"]["message"]
    assert client.post("/api/dashboard/summary", json={"days": 30, "project_id": 9999}).status_code == 404
    assert client.post("/api/dashboard/summary", json={"days": 14}).status_code == 422

    swipe(project_id, make_paper(abstract="Third. We show three."), "relevant")
    http_mock.add(
        "POST",
        MESSAGES,
        {"type": "error", "error": {"type": "not_found_error", "message": "model"}},
        status=404,
    )
    failed = client.post("/api/dashboard/summary", json={"days": 30, "tz": "UTC"}).json()["job"]
    assert failed["status"] == "failed" and failed["error_code"] == "source_unavailable"
    assert "RADAR_SUMMARY_MODEL" in failed["error_message"]
    status = client.get("/api/dashboard/findings", params={"days": 30, "tz": "UTC"}).json()["ai"]
    assert status["remaining_today"] == 0 and status["job"]["id"] == failed["id"]  # the failure is shown

    r = client.post("/api/dashboard/summary", json={"days": 30, "tz": "UTC"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"


def _other_users_summaries(n: int) -> None:
    """``n`` AI summaries requested today by other users (failed ones count too)."""
    with session_scope(write=True) as s:
        for i in range(n):
            s.add(
                models.Job(
                    kind="summarize_findings",
                    status="failed",
                    owner_id=100 + i,
                    params_json={"user_id": 100 + i, "days": 30},
                    dedupe_key=f"zq-other-user-{i}",
                )
            )


ONE_POINT = [{"title": "T", "points": [{"text": "p", "cites": [1]}]}]


def test_summary_global_daily_cap(
    ai_on: None,
    set_env: Callable[..., None],
    client: TestClient,
    http_mock: Any,
    liked_papers: tuple[int, list[int]],
) -> None:
    set_env(RADAR_SUMMARIES_GLOBAL_PER_DAY="3")  # per user: the default 5
    _other_users_summaries(2)
    status = client.get("/api/dashboard/findings", params={"days": 30, "tz": "UTC"}).json()["ai"]
    assert status["remaining_today"] == 1  # min(5 left for the user, 3 - 2 left for the server)
    http_mock.add("POST", MESSAGES, tool_reply(ONE_POINT))
    assert client.post("/api/dashboard/summary", json={"days": 30, "tz": "UTC"}).status_code == 202
    r = client.post("/api/dashboard/summary", json={"days": 7, "tz": "UTC"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
    assert "This server has used all of today's AI summaries" in r.json()["error"]["message"]
    retry = r.json()["error"]["details"]["retry_after_seconds"]
    assert 1 <= retry <= 86400 and r.headers["retry-after"] == str(retry)  # until the next UTC midnight
    status = client.get("/api/dashboard/findings", params={"days": 7, "tz": "UTC"}).json()["ai"]
    assert status["remaining_today"] == 0
    assert len(http_mock.requests) == 1  # the refused request never reached Anthropic

    with pytest.raises(ValueError):  # there is no "unlimited": at least one per day
        set_env(RADAR_SUMMARIES_GLOBAL_PER_DAY="0")
        from radar.config import get_settings

        get_settings()


@pytest.mark.parametrize("cap", ["RADAR_SUMMARIES_PER_DAY", "RADAR_SUMMARIES_GLOBAL_PER_DAY"])
def test_parallel_summary_requests_cannot_pass_a_cap(
    ai_on: None,
    set_env: Callable[..., None],
    client: TestClient,
    http_mock: Any,
    liked_papers: tuple[int, list[int]],
    monkeypatch: pytest.MonkeyPatch,
    cap: str,
) -> None:
    import threading
    import time as time_module

    set_env(**{"RADAR_SUMMARIES_PER_DAY": "10", "RADAR_SUMMARIES_GLOBAL_PER_DAY": "10", cap: "2"})
    http_mock.add("POST", MESSAGES, tool_reply(ONE_POINT))
    for name in ("jobs_today", "jobs_today_all"):  # widen the gap between counting and inserting
        real = getattr(summary, name)

        def slow(*args: Any, _real: Callable[..., int] = real) -> int:
            n = _real(*args)
            time_module.sleep(0.05)
            return n

        monkeypatch.setattr(summary, name, slow)
    barrier = threading.Barrier(4)
    codes: list[int] = []

    def request(days: int) -> None:
        barrier.wait()
        codes.append(client.post("/api/dashboard/summary", json={"days": days, "tz": "UTC"}).status_code)

    threads = [threading.Thread(target=request, args=(days,)) for days in (7, 30, 90, 365)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert sorted(codes) == [202, 202, 429, 429]
    with session_scope() as s:
        assert s.query(models.Job).filter(models.Job.kind == "summarize_findings").count() == 2


def test_rejected_key_and_missing_tool_block(ai_on: None, http_mock: Any) -> None:
    http_mock.add(
        "POST", MESSAGES, {"type": "error", "error": {"type": "authentication_error"}}, status=401, times=1
    )
    kwargs = dict(
        model="m", system="s", user="u", tool_name="report_findings", tool_description="d", input_schema={}
    )
    with pytest.raises(ApiError) as rejected:
        anthropic.call_tool(**kwargs)  # type: ignore[arg-type]
    assert rejected.value.code == "not_configured" and "ZQX" not in rejected.value.message
    http_mock.add("POST", MESSAGES, {"model": "m", "content": [], "stop_reason": "max_tokens"}, times=1)
    with pytest.raises(SourceUnavailable) as empty:
        anthropic.call_tool(**kwargs)  # type: ignore[arg-type]
    assert "stop reason: max_tokens" in str(empty.value)


def test_summary_job_refuses_another_users_papers(ai_on: None) -> None:
    from radar.jobs.runner import JobContext

    with pytest.raises(ApiError) as denied:
        summary.run_summarize_findings(JobContext(owner_id=2), user_id=1)
    assert denied.value.status == 403


# ------------------------------------------------------------------------------------------------
# Isolation and boundaries
# ------------------------------------------------------------------------------------------------

BASE = "http://localhost:8000"


@pytest.fixture
def accounts_app(set_env: Callable[..., None]) -> Iterator[FastAPI]:
    set_env(RADAR_AUTH="accounts", RADAR_PUBLIC_URL=BASE, ANTHROPIC_API_KEY="sk-ant-test")
    from radar.main import create_app

    app = create_app()
    with TestClient(app, base_url=BASE, headers={"X-Radar-Client": "web"}):
        yield app


def test_users_only_see_their_own_dashboard(
    accounts_app: FastAPI, make_project: Callable[..., int], make_paper: Callable[..., int]
) -> None:
    ada = TestClient(accounts_app, base_url=BASE, headers={"X-Radar-Client": "web"})
    bob = TestClient(accounts_app, base_url=BASE, headers={"X-Radar-Client": "web"})
    ada_id = ada.post(
        "/api/auth/signup", json={"email": "ada@example.org", "password": "correct horse 1"}
    ).json()
    bob_id = bob.post(
        "/api/auth/signup", json={"email": "bob@example.org", "password": "correct horse 2"}
    ).json()
    ada_project = make_project(owner_id=ada_id["user"]["id"])
    make_project(owner_id=bob_id["user"]["id"])
    swipe(ada_project, make_paper(abstract=RESULT_ABSTRACT), "relevant")

    assert ada.get("/api/dashboard/activity").json()["totals"]["swiped"] == 1
    assert bob.get("/api/dashboard/activity").json()["totals"]["swiped"] == 0
    assert bob.get("/api/dashboard/findings").json()["n_papers"] == 0
    for name in ("activity", "topics", "findings", "work"):
        r = bob.get(f"/api/dashboard/{name}", params={"project_id": ada_project})
        assert r.status_code == 404, name
    assert bob.post("/api/dashboard/summary", json={"project_id": ada_project}).status_code == 404
    anonymous = TestClient(accounts_app, base_url=BASE, headers={"X-Radar-Client": "web"})
    assert anonymous.get("/api/dashboard/activity").status_code == 401


def test_dashboard_package_never_loads_network_modules() -> None:
    code = (
        "import sys, importlib\n"
        "for m in ('activity', 'topics', 'findings', 'work', 'common'):\n"
        "    importlib.import_module('radar.dashboard.' + m)\n"
        "bad = [m for m in sys.modules if m.startswith(('radar.net', 'radar.connectors', 'radar.ingestion',"
        " 'radar.jobs', 'radar.graph.expand', 'radar.graph.breakthroughs', 'radar.auth.oauth'))]\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ})
    assert result.returncode == 0, result.stdout + result.stderr
