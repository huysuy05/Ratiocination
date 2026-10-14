"""Project keywords (SPEC §10): validation, matching rules, the saturating ``keyword`` feature, its
reasons and evidence, candidate widening, and the projects API. Synthetic texts only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from radar import models
from radar.db import session_scope
from radar.ranking import explain, features, slate
from radar import keywords as kw
from radar.ranking.slate import build_slate, window_candidates
from radar.schemas import ProjectCreate, ProjectSettings, ProjectUpdate, Reason

TODAY = datetime.now(UTC).date()


# ------------------------------------------------------------------------------------------------
# Matching rules
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keyword", "text", "found"),
    [
        ("conformal prediction", "Conformal Prediction for retrieval", "Conformal Prediction"),
        ("Conformal Prediction", "a CONFORMAL PREDICTION study", "CONFORMAL PREDICTION"),
        ("self-supervised", "Self supervised pretraining", "Self supervised"),
        ("self supervised", "a self-supervised objective", "self-supervised"),
        ("self supervised", "a self–supervised objective", "self–supervised"),  # en dash
        ("LLM", "Evaluating LLMs at scale", "LLMs"),
        ("neural networks", "one neural network", "neural network"),
        ("query", "several queries", "queries"),
        ("class", "imbalanced classes", "classes"),
        ("C++", "fast C++ kernels", "C++"),
        ("ＲＡＧ", "RAG pipelines", "RAG"),  # NFKC: full-width letters
        ("graph", "a paragraph about graphing", None),  # whole words only
        ("neural network", "neural ranking network", None),  # phrases are contiguous
        ("C++", "C kernels", None),
        ("analysis", "two analyses", None),  # light plural handling only
    ],
)
def test_whole_word_phrase_matching(keyword: str, text: str, found: str | None) -> None:
    matches = kw.match_keywords([keyword], text, None)
    assert [m.text for m in matches] == ([found] if found else [])
    assert all(m.field == "title" and m.keyword == keyword for m in matches)


def test_title_wins_over_abstract_and_each_keyword_counts_once() -> None:
    matches = kw.match_keywords(
        ["retrieval", "conformal prediction", "graphs", "absent"],
        "Conformal prediction for graph learning",
        "We study retrieval. Retrieval again, and conformal prediction in the abstract too.",
    )
    assert [(m.keyword, m.field, m.text) for m in matches] == [
        ("conformal prediction", "title", "Conformal prediction"),
        ("graphs", "title", "graph"),
        ("retrieval", "abstract", "retrieval"),
    ]
    assert kw.keyword_raw(matches) == pytest.approx(2 * kw.TITLE_WEIGHT + kw.ABSTRACT_WEIGHT)
    assert kw.match_keywords([], "anything", "anything") == []
    assert kw.match_keywords(["x y"], None, None) == []
    long = "word " * 40
    (hit,) = kw.match_keywords([long.strip()], long, None)
    assert len(hit.text) <= kw.MATCH_TEXT_CHARS and hit.text.endswith("…")


def test_keys_and_dedupe_are_case_hyphen_and_plural_insensitive() -> None:
    assert (
        kw.keyword_key("Self-Supervised  LLMs")
        == kw.keyword_key("self supervised llm")
        == "self supervised llm"
    )
    assert kw.dedupe_keywords(["LLM", "llms", "Self-Supervised", "self supervised", "RAG"]) == [
        "LLM",
        "Self-Supervised",
        "RAG",
    ]
    assert kw.keyword_key("--") == "" and kw.dedupe_keywords(["--", "ok"]) == ["ok"]
    assert kw.clean_keyword("  conformal \n  prediction ") == "conformal prediction"


def test_saturation() -> None:
    assert kw.keyword_value(0) == 0.0
    one, two, three = (kw.keyword_value(r) for r in (1.0, 2.0, 3.0))
    assert one == pytest.approx(0.632, abs=1e-3) and two == pytest.approx(0.865, abs=1e-3)
    assert three < 0.96 and kw.keyword_value(12.0) < 1.0  # twelve title matches never exceed 1
    assert two - one < one  # each further match adds less
    assert kw.keyword_value(kw.ABSTRACT_WEIGHT) == pytest.approx(0.393, abs=1e-3)
    np.testing.assert_allclose(features.saturate(np.array([0.0, 1.0, -2.0])), [0.0, one, 0.0])


# ------------------------------------------------------------------------------------------------
# Score math
# ------------------------------------------------------------------------------------------------


def _raw(**columns: np.ndarray | None) -> dict[str, np.ndarray | None]:
    raw: dict[str, np.ndarray | None] = dict.fromkeys(features.FEATURES)
    raw.update(columns)
    return raw


def test_projects_without_keywords_score_exactly_as_before() -> None:
    question = np.array([0.20, 0.50, 0.90, 0.40])
    recency = np.array([1.0, 0.9, 0.8, 0.95])
    without = features.score(_raw(question=question, recency=recency), 4)
    assert not without.available("keyword")
    assert without.contributions["keyword"].tolist() == [0.0] * 4
    total = features.WEIGHTS["question"] + features.WEIGHTS["recency"]
    expected = (
        features.WEIGHTS["question"] * without.values["question"]
        + features.WEIGHTS["recency"] * without.values["recency"]
    ) / total
    np.testing.assert_allclose(without.score, expected)
    # keywords that match nothing scale every score by the same factor: the order cannot change
    none_match = features.score(_raw(question=question, recency=recency, keyword=np.zeros(4)), 4)
    assert none_match.available("keyword")
    assert np.argsort(none_match.score).tolist() == np.argsort(without.score).tolist()


def test_keywords_upweight_without_overriding_the_question() -> None:
    # papers: the best question match, a good one (~60% of the window's P5..P99 range) with one title
    # keyword, an off-topic one with three title keywords
    question = np.array([0.90, 0.66, 0.10] + [0.3] * 20)
    keyword = np.array([0.0, kw.TITLE_WEIGHT, 3 * kw.TITLE_WEIGHT] + [0.0] * 20)
    recency = np.ones(23)
    scored = features.score(_raw(question=question, recency=recency, keyword=keyword), 23)
    v = scored.values["keyword"]
    assert v[0] == 0.0 and v[1] == pytest.approx(kw.keyword_value(1.0)) and v[2] < 1.0
    s = scored.score
    assert s[1] > s[0]  # a title keyword lifts a decent question match above the best non-matching one
    assert s[0] > s[2]  # ...but piling keywords on an off-topic paper does not beat a strong question match
    assert scored.raw["keyword"] is not None and scored.raw["keyword"][2] == 3.0  # raw = Σ match weights
    np.testing.assert_allclose(sum(scored.contributions.values()), s)
    share = features.WEIGHTS["keyword"] / sum(features.WEIGHTS[n] for n in ("question", "keyword", "recency"))
    assert scored.contributions["keyword"][1] == pytest.approx(share * kw.keyword_value(1.0))


def test_recency_is_a_tie_breaker_inside_a_short_window() -> None:
    """Recency keeps its absolute scale (floor 1.0): two weeks of age move a paper by at most 0.28 of
    the recency weight, so a few days never outweigh a clearly closer question match."""
    ages = np.array([0.0, 3.0, 7.0, 13.0, 13.0])
    rec = features.recency(ages)
    question = np.array([0.55, 0.60, 0.65, 0.70, 0.50])
    scored = features.score(_raw(question=question, recency=rec), 5)
    assert scored.values["recency"].max() - scored.values["recency"].min() < 0.3
    assert scored.score[3] > scored.score[0]  # 13 days old but closer to the question wins


# ------------------------------------------------------------------------------------------------
# Reasons
# ------------------------------------------------------------------------------------------------


def _match(keyword: str, field: str, text: str | None = None) -> dict[str, Any]:
    return {"keyword": keyword, "field": field, "text": text or keyword}


def test_keyword_reason_names_each_keyword_and_where() -> None:
    assert explain.keyword_text([_match("conformal prediction", "title")]) == (
        "Mentions your keyword ‘conformal prediction’ in the title"
    )
    assert explain.keyword_text([_match("a", "title"), _match("b", "title")]) == (
        "Mentions your keywords ‘a’ and ‘b’ in the title"
    )
    assert explain.keyword_text([_match("a", "title"), _match("b", "abstract")]) == (
        "Mentions your keywords ‘a’ in the title and ‘b’ in the abstract"
    )
    assert explain.keyword_text([_match("RAG", "abstract")]) == "Mentions your keyword ‘RAG’ in the abstract"
    many = [_match(k, "title") for k in "abcd"] + [_match("e", "abstract")]
    assert explain.keyword_text(many) == "Mentions your keywords ‘a’, ‘b’ and ‘c’ in the title (+2 more)"


def test_keyword_reason_is_a_metadata_fact_with_evidence() -> None:
    matches = [_match("conformal prediction", "title", "Conformal Predictions"), _match("RAG", "abstract")]
    item = explain.ExplainInput(
        values={"question": 0.3, "anchor": None, "keyword": 0.78, "recency": 0.2},
        contributions={"question": 0.15, "keyword": 0.27, "recency": 0.01},
        question_cosine=0.4,
        keywords=matches,
        category="cs.IR",
        age_days=3,
    )
    reasons = explain.reasons(item)
    assert reasons[0]["code"] == "keyword" and reasons[0]["kind"] == "metadata"
    assert reasons[0]["contribution"] == pytest.approx(0.27)
    assert reasons[0]["evidence"] == {
        "matches": [
            {"keyword": "conformal prediction", "field": "title", "text": "Conformal Predictions"},
            {"keyword": "RAG", "field": "abstract", "text": "RAG"},
        ]
    }
    TypeAdapter(Reason).validate_python(reasons[0])
    no_match = explain.reasons(explain.ExplainInput(values={"keyword": 0.0}, contributions={}, keywords=[]))
    assert all(r["code"] != "keyword" for r in no_match)


# ------------------------------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------------------------


def test_keyword_validation() -> None:
    body = ProjectCreate(
        name="Test", research_question="Test question?", keywords=["  conformal   prediction ", "LLMs", "llm"]
    )
    assert body.keywords == ["conformal prediction", "LLMs"]
    assert ProjectCreate(name="Test", research_question="Test question?").keywords == []
    for bad, path in (
        (["a"], ("keywords", 0)),
        (["x" * 61], ("keywords", 0)),
        (["--"], ("keywords", 0)),
        ([f"keyword {i}" for i in range(13)], ("keywords",)),
        ([3], ("keywords", 0)),
    ):
        with pytest.raises(ValidationError) as info:
            ProjectCreate(name="Test", research_question="Test question?", keywords=bad)
        assert info.value.errors()[0]["loc"] == path
    with pytest.raises(ValidationError):
        ProjectUpdate.model_validate({"keywords": None})
    assert ProjectUpdate.model_validate({"keywords": []}).keywords == []


# ------------------------------------------------------------------------------------------------
# Deck builds and the API
# ------------------------------------------------------------------------------------------------


def _deck_items(project_id: int) -> list[models.RecommendationItem]:
    with session_scope() as session:
        slate_id = session.scalar(
            select(models.RecommendationSlate.id)
            .where(models.RecommendationSlate.project_id == project_id)
            .order_by(models.RecommendationSlate.id.desc())
            .limit(1)
        )
        items = list(
            session.scalars(
                select(models.RecommendationItem)
                .where(models.RecommendationItem.slate_id == slate_id)
                .order_by(models.RecommendationItem.rank)
            )
        )
        session.expunge_all()
        return items


def test_deck_boosts_keyword_papers_and_explains_them(make_project: Any, make_paper: Any) -> None:
    question = "test question about synthetic ranking signals"
    topic = "synthetic ranking signals for test questions"
    plain = [make_paper(title=f"Test paper {i} on {topic}", abstract=f"About {topic}.") for i in range(5)]
    titled = make_paper(
        title=f"Test paper T on {topic} with zqconformal zqprediction", abstract=f"About {topic}."
    )
    twin = make_paper(title=f"Test paper U on {topic} with other extra words", abstract=f"About {topic}.")
    in_abstract = make_paper(
        title=f"Test paper V on {topic}", abstract=f"About {topic}: zqconformal zqprediction."
    )
    off_topic = make_paper(
        title="Test paper W with zqconformal zqprediction", abstract="Unrelated words only."
    )
    project = make_project(
        research_question=question,
        keywords_json=["zqconformal zqprediction"],
        settings={"arxiv_categories": ["cs.IR"], "slate_size": 10},
    )
    build_slate(project)
    items = {item.paper_id: item for item in _deck_items(project)}
    assert set(items) == {*plain, titled, twin, in_abstract, off_topic}
    title_item, abstract_item = items[titled], items[in_abstract]
    feat = title_item.feature_scores_json["keyword"]
    assert feat["available"] and feat["raw"] == 1.0 and feat["value"] == pytest.approx(0.632, abs=1e-3)
    assert feat["weight"] == features.WEIGHTS["keyword"]
    assert feat["evidence"] == {
        "matches": [
            {"keyword": "zqconformal zqprediction", "field": "title", "text": "zqconformal zqprediction"}
        ]
    }
    assert abstract_item.feature_scores_json["keyword"]["raw"] == kw.ABSTRACT_WEIGHT
    assert items[plain[0]].feature_scores_json["keyword"] == {
        "value": 0.0,
        "raw": 0.0,
        "weight": features.WEIGHTS["keyword"],
        "contribution": 0.0,
        "available": True,
        "evidence": {"matches": []},
    }
    reason = next(r for r in title_item.explanation_json if r["code"] == "keyword")
    assert reason["text"] == "Mentions your keyword ‘zqconformal zqprediction’ in the title"
    assert "keyword" in [r["code"] for r in title_item.explanation_json[: explain.PILLS]]  # a card pill
    other = next(r for r in abstract_item.explanation_json if r["code"] == "keyword")
    assert other["text"] == "Mentions your keyword ‘zqconformal zqprediction’ in the abstract"
    assert all(r["code"] != "keyword" for r in items[plain[0]].explanation_json)
    # the keyword lifts a paper above its keyword-less twin and to the top of the deck...
    assert title_item.score > items[twin].score and title_item.rank == 1
    assert abstract_item.score > max(items[p].score for p in plain)
    # ...but an off-topic paper that only names the keyword stays below the on-topic papers
    assert items[off_topic].score < min(items[p].score for p in plain)
    for item in items.values():
        total = sum(v["contribution"] for v in item.feature_scores_json.values())
        assert total == pytest.approx(item.score, abs=1e-4)
    with session_scope() as session:
        params = session.scalar(
            select(models.RecommendationSlate.params_json).where(
                models.RecommendationSlate.project_id == project
            )
        )
    assert params is not None and params["n_keywords"] == 1 and "zqconformal" not in str(params)


def test_decks_without_keywords_mark_the_feature_unavailable(make_project: Any, make_paper: Any) -> None:
    for i in range(3):
        make_paper(title=f"Test paper {i}", abstract="Synthetic abstract.")
    project = make_project(settings={"arxiv_categories": ["cs.IR"], "slate_size": 5})
    build_slate(project)
    for item in _deck_items(project):
        entry = item.feature_scores_json["keyword"]
        assert entry["available"] is False and entry["contribution"] == 0.0 and entry["evidence"] is None
        assert all(r["code"] != "keyword" for r in item.explanation_json)


def test_keyword_titles_beyond_the_candidate_cap_still_join(
    make_project: Any, make_paper: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(slate, "MAX_CANDIDATES", 3)
    newest = [make_paper(title=f"Test paper new {i}", publication_date=TODAY) for i in range(3)]
    older = [
        make_paper(title=f"Test paper old {i}", publication_date=TODAY - timedelta(days=3 + i))
        for i in range(4)
    ]
    match = make_paper(title="Test paper on zqkeyword retrieval", publication_date=TODAY - timedelta(days=9))
    in_abstract_only = make_paper(
        title="Test paper old abstract", abstract="zqkeyword here", publication_date=TODAY - timedelta(days=9)
    )
    project = make_project(settings={"arxiv_categories": ["cs.IR"]})
    settings = ProjectSettings(arxiv_categories=["cs.IR"])
    with session_scope() as session:
        plain, n_window = window_candidates(session, project, settings, "daily", TODAY)
        widened, _ = window_candidates(session, project, settings, "daily", TODAY, ["zqkeyword"])
    assert n_window == 9 and [c.paper_id for c in plain] == list(reversed(newest))
    assert [c.paper_id for c in widened] == [*reversed(newest), match]
    assert in_abstract_only not in {c.paper_id for c in widened} and not set(older) & {
        c.paper_id for c in widened
    }
    monkeypatch.setattr(slate, "KEYWORD_EXTRA", 0)
    with session_scope() as session:
        capped, _ = window_candidates(session, project, settings, "daily", TODAY, ["zqkeyword"])
    assert len(capped) == 3


def test_projects_api_stores_and_validates_keywords(client: TestClient) -> None:
    created = client.post(
        "/api/projects",
        json={
            "name": "Test keywords",
            "research_question": "Test question?",
            "keywords": [" Conformal  prediction ", "LLMs", "LLM", "self-supervised"],
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["keywords"] == ["Conformal prediction", "LLMs", "self-supervised"]
    pid = project["id"]
    assert client.get(f"/api/projects/{pid}").json()["keywords"] == project["keywords"]
    assert client.get("/api/projects").json()[0]["keywords"] == project["keywords"]

    renamed = client.patch(f"/api/projects/{pid}", json={"name": "Renamed"}).json()
    assert renamed["keywords"] == project["keywords"]  # untouched unless sent
    replaced = client.patch(f"/api/projects/{pid}", json={"keywords": ["RAG"]}).json()
    assert replaced["keywords"] == ["RAG"]
    cleared = client.patch(f"/api/projects/{pid}", json={"keywords": []}).json()
    assert cleared["keywords"] == []

    bad = client.patch(f"/api/projects/{pid}", json={"keywords": ["ok", "x"]})
    assert bad.status_code == 422
    fields = bad.json()["error"]["details"]["fields"]
    assert fields[0]["path"] == "keywords.1" and "x" not in fields[0]["message"].split()
    too_many = client.post(
        "/api/projects",
        json={"name": "Test", "research_question": "Test?", "keywords": [f"keyword {i}" for i in range(13)]},
    )
    assert (
        too_many.status_code == 422 and too_many.json()["error"]["details"]["fields"][0]["path"] == "keywords"
    )
    assert client.patch(f"/api/projects/{pid}", json={"keywords": None}).status_code == 422
    assert client.get(f"/api/projects/{pid}").json()["keywords"] == []
