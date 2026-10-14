"""store.graph (SPEC §12): upsert_edge, concepts, merged edge readers, topics."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select

from radar import models
from radar.db import session_scope
from radar.store.graph import (
    MergedEdge,
    concept_external_id,
    edges_for_works,
    get_or_create_concept,
    paper_topics,
    upsert_edge,
)


def edge_rows() -> list[models.GraphEdge]:
    with session_scope() as session:
        return list(session.scalars(select(models.GraphEdge).order_by(models.GraphEdge.id)))


def s2_reference(load_fixture: Callable[[str], Any], index: int = 0) -> dict[str, Any]:
    return load_fixture("s2_references.json")["data"][index]


# ------------------------------------------------------------------------------------------------
# upsert_edge
# ------------------------------------------------------------------------------------------------


def test_cites_edge_direction_and_idempotent_upsert(
    make_paper: Any, load_fixture: Callable[[str], Any]
) -> None:
    citing, cited = make_paper(), make_paper()
    ref = s2_reference(load_fixture)
    evidence = {
        "contexts": ref["contexts"],
        "intents": ref["intents"],
        "is_influential": ref["isInfluential"],
    }
    with session_scope(write=True) as session:
        edge = upsert_edge(
            session,
            src=("work", citing),
            relation="CITES",
            dst=("work", cited),
            provenance="s2",
            verified=True,
            evidence=evidence,
        )
        edge_id = edge.id
    (row,) = edge_rows()
    assert (row.src_type, row.src_id, row.relation, row.dst_type, row.dst_id) == (
        "work",
        citing,
        "CITES",
        "work",
        cited,
    )
    assert row.verified is True and row.provenance == "s2"
    assert row.owner_id == 0 and row.project_id == 0
    assert row.evidence_json == evidence and row.evidence_json["is_influential"] is True
    first_updated = row.updated_at

    with session_scope(write=True) as session:  # identical call: same row, nothing changes
        again = upsert_edge(
            session,
            src=("work", citing),
            relation="CITES",
            dst=("work", cited),
            provenance="s2",
            verified=True,
            evidence=evidence,
        )
        assert again.id == edge_id
    (row,) = edge_rows()
    assert row.updated_at == first_updated

    with session_scope(write=True) as session:  # new values are applied; None never clears
        upsert_edge(
            session,
            src=("work", citing),
            relation="CITES",
            dst=("work", cited),
            provenance="s2",
            verified=True,
            weight=0.5,
        )
    (row,) = edge_rows()
    assert row.weight == 0.5 and row.evidence_json == evidence
    assert row.updated_at >= first_updated

    with session_scope(write=True) as session:  # a different provenance is its own row
        upsert_edge(
            session,
            src=("work", citing),
            relation="CITES",
            dst=("work", cited),
            provenance="openalex",
            verified=True,
        )
    rows = edge_rows()
    assert len(rows) == 2
    assert rows[1].evidence_json == {"contexts": [], "intents": [], "is_influential": None}


def test_symmetric_relations_store_the_smaller_id_first(make_paper: Any) -> None:
    a, b = make_paper(), make_paper()
    high, low = max(a, b), min(a, b)
    with session_scope(write=True) as session:
        first = upsert_edge(
            session,
            src=("work", high),
            relation="SAME_WORK_AS",
            dst=("work", low),
            provenance="title_match",
            verified=False,
            confidence=0.75,
            evidence={"matched_on": "title"},
        )
        second = upsert_edge(
            session,
            src=("work", low),
            relation="SAME_WORK_AS",
            dst=("work", high),
            provenance="title_match",
            verified=False,
            confidence=0.75,
            evidence={"matched_on": "title"},
        )
        assert first.id == second.id
        similar = upsert_edge(
            session,
            src=("work", high),
            relation="SEMANTICALLY_SIMILAR",
            dst=("work", low),
            provenance="local/knn",
            verified=False,
            model_version="local/hashing-v1",
            evidence={"model_id": "local/hashing-v1", "cosine": 0.83},
        )
    rows = edge_rows()
    assert len(rows) == 2
    assert all((r.src_id, r.dst_id) == (low, high) for r in rows)
    assert similar.confidence == pytest.approx(0.83)  # confidence defaults to the cosine
    assert rows[1].evidence_json == {"model_id": "local/hashing-v1", "cosine": 0.83}


def test_cites_keeps_its_direction(make_paper: Any) -> None:
    a, b = sorted((make_paper(), make_paper()))
    with session_scope(write=True) as session:
        upsert_edge(
            session, src=("work", b), relation="CITES", dst=("work", a), provenance="openalex", verified=True
        )
    (row,) = edge_rows()
    assert (row.src_id, row.dst_id) == (b, a)


def test_has_topic_confidence_is_the_score(make_paper: Any, load_fixture: Callable[[str], Any]) -> None:
    work = load_fixture("openalex_work_published.json")
    topic = work["topics"][0]  # T10028 Topic Modeling, score 0.9897...
    pid = make_paper()
    with session_scope(write=True) as session:
        concept = get_or_create_concept(
            session,
            "openalex_topic",
            topic["id"],
            topic["display_name"],
            {"domain": topic["domain"]["display_name"]},
        )
        assert concept.external_id == "T10028"
        edge = upsert_edge(
            session,
            src=("work", pid),
            relation="HAS_TOPIC",
            dst=("concept", concept.id),
            provenance="openalex",
            verified=False,
            confidence=0.1,  # ignored: HAS_TOPIC confidence = evidence score
            evidence={
                "score": topic["score"],
                "subfield": topic["subfield"]["display_name"],
                "field": topic["field"]["display_name"],
            },
        )
        assert edge.confidence == pytest.approx(topic["score"])
        assert edge.evidence_json == {
            "score": topic["score"],
            "subfield": "Artificial Intelligence",
            "field": "Computer Science",
        }
        bare = upsert_edge(
            session,
            src=("work", pid),
            relation="HAS_TOPIC",
            dst=("concept", concept.id),
            provenance="test",
            verified=False,
            confidence=0.4,
        )
        assert bare.evidence_json == {"score": 0.4, "subfield": None, "field": None}


@pytest.mark.parametrize(
    ("src", "dst", "message"),
    [
        (("work", 1), ("work", 1), "two different endpoints"),
        (("paper", 1), ("work", 2), "unknown src node type"),
        (("work", 1), ("galaxy", 2), "unknown dst node type"),
    ],
)
def test_upsert_edge_rejects_bad_endpoints(src: Any, dst: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        with session_scope(write=True) as session:
            upsert_edge(session, src=src, relation="CITES", dst=dst, provenance="s2", verified=True)


def test_private_edges_are_scoped(make_paper: Any, make_project: Any) -> None:
    a, b = make_paper(), make_paper()
    p1, p2 = make_project(), make_project()
    with session_scope(write=True) as session:
        upsert_edge(
            session,
            src=("work", a),
            relation="SEMANTICALLY_SIMILAR",
            dst=("work", b),
            provenance="local/knn",
            verified=False,
            confidence=0.9,
            model_version="local/hashing-v1",
            owner_id=1,
            project_id=p1,
        )
    with session_scope() as session:
        assert len(edges_for_works(session, [a], owner_id=1, project_id=p1)) == 1
        assert edges_for_works(session, [a], owner_id=1, project_id=p2) == []
        assert edges_for_works(session, [a], owner_id=1) == []
        assert edges_for_works(session, [a], project_id=p1) == []  # public readers never see it
        assert edges_for_works(session, [a], project_id=p1, owner_id=2) == []


# ------------------------------------------------------------------------------------------------
# Concepts
# ------------------------------------------------------------------------------------------------


def test_get_or_create_concept_is_idempotent(load_fixture: Callable[[str], Any]) -> None:
    topic = load_fixture("openalex_work_arxiv.json")["topics"][0]  # T10203 Recommender Systems and Techniques
    with session_scope(write=True) as session:
        first = get_or_create_concept(session, "openalex_topic", topic["id"], topic["display_name"])
        second = get_or_create_concept(
            session, "openalex_topic", "T10203", "Another label", {"field": topic["field"]["display_name"]}
        )
        third = get_or_create_concept(
            session, "openalex_topic", "t10203", topic["display_name"], {"field": "x"}
        )
        assert first.id == second.id == third.id
        assert first.canonical_label == "Recommender Systems and Techniques"
        assert first.extra_json == {"field": "Computer Science"}  # added once, existing keys win
        with pytest.raises(ValueError):
            get_or_create_concept(session, "user", "", "Calibrated Recommenders")  # topic editor removed
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(models.Concept)) == 1


@pytest.mark.parametrize(
    ("source", "external_id", "label", "expected"),
    [
        ("openalex_topic", "https://openalex.org/T10203", "x", "T10203"),
        ("openalex_topic", "https://openalex.org/topics/T10286", "x", "T10286"),
        ("openalex_topic", "T13607", "x", "T13607"),
    ],
)
def test_concept_external_id(source: str, external_id: str, label: str, expected: str) -> None:
    assert concept_external_id(source, external_id, label) == expected


@pytest.mark.parametrize(
    ("source", "external_id", "label"),
    [
        ("openalex_topic", "W123", "x"),
        ("openalex_topic", "", "x"),
        ("user", "", "Graph Neural Networks"),
        ("mesh", "D001", "x"),
    ],
)
def test_concept_external_id_rejects(source: str, external_id: str, label: str) -> None:
    with pytest.raises(ValueError):
        concept_external_id(source, external_id, label)


# ------------------------------------------------------------------------------------------------
# edges_for_works
# ------------------------------------------------------------------------------------------------


def _cites_from_two_sources(citing: int, cited: int, ref: dict[str, Any]) -> tuple[int, int]:
    with session_scope(write=True) as session:
        openalex = upsert_edge(
            session,
            src=("work", citing),
            relation="CITES",
            dst=("work", cited),
            provenance="openalex",
            verified=True,
        )
        s2 = upsert_edge(
            session,
            src=("work", citing),
            relation="CITES",
            dst=("work", cited),
            provenance="s2",
            verified=True,
            evidence={
                "contexts": ref["contexts"],
                "intents": ref["intents"],
                "is_influential": ref["isInfluential"],
            },
        )
        return openalex.id, s2.id


def test_edges_merge_across_provenances_preferring_s2(
    make_paper: Any, load_fixture: Callable[[str], Any]
) -> None:
    citing, cited, other = make_paper(), make_paper(), make_paper()
    ref = s2_reference(load_fixture)
    openalex_id, s2_id = _cites_from_two_sources(citing, cited, ref)
    with session_scope(write=True) as session:
        upsert_edge(
            session,
            src=("work", other),
            relation="CITES",
            dst=("work", citing),
            provenance="openalex",
            verified=True,
        )
    with session_scope() as session:
        merged = edges_for_works(session, [citing])
    assert len(merged) == 2
    edge = next(e for e in merged if e.dst_id == cited)
    assert isinstance(edge, MergedEdge)
    assert edge.id == s2_id and edge.provenance == "s2"
    assert edge.provenances == ["s2", "openalex"] and edge.edge_ids == [s2_id, openalex_id]
    assert edge.verified is True
    assert edge.evidence is not None and edge.evidence["contexts"] == ref["contexts"]
    assert edge.evidence["intents"] == ["background"]
    incoming = next(e for e in merged if e.dst_id == citing)
    assert (incoming.src_id, incoming.provenance) == (other, "openalex")
    with session_scope() as session:
        assert [e.dst_id for e in edges_for_works(session, [cited], {"CITES"})] == [cited]
        assert edges_for_works(session, [cited], {"HAS_TOPIC"}) == []
        assert edges_for_works(session, [cited], set()) == []
        assert edges_for_works(session, []) == []


def test_authored_edges_keep_their_position(make_paper: Any) -> None:
    work, other_work = make_paper(), make_paper()  # factories open their own write sessions: call first
    with session_scope(write=True) as session:
        scholar = models.Scholar(openalex_id="A5000000001", display_name="Test Scholar")
        session.add(scholar)
        session.flush()
        edge = upsert_edge(
            session,
            src=("scholar", scholar.id),
            relation="AUTHORED",
            dst=("work", work),
            provenance="openalex",
            verified=True,
            evidence={"position": 2},
        )
        assert edge.evidence_json == {"position": 2}
        odd = upsert_edge(
            session,
            src=("scholar", scholar.id),
            relation="AUTHORED",
            dst=("work", other_work),
            provenance="openalex",
            verified=True,
            evidence={"position": True},
        )
        assert odd.evidence_json == {"position": None}  # a bool is not a position
    with session_scope() as session:
        (merged,) = edges_for_works(session, [work], {"AUTHORED"})
    assert (merged.src_type, merged.dst_id, merged.evidence) == ("scholar", work, {"position": 2})


# ------------------------------------------------------------------------------------------------
# paper_topics
# ------------------------------------------------------------------------------------------------


def test_paper_topics_order_and_limit(make_paper: Any, load_fixture: Callable[[str], Any]) -> None:
    work = load_fixture("openalex_work_published.json")
    pid, other = make_paper(), make_paper()
    with session_scope(write=True) as session:
        concepts = []
        for topic in work["topics"]:
            concept = get_or_create_concept(session, "openalex_topic", topic["id"], topic["display_name"])
            upsert_edge(
                session,
                src=("work", pid),
                relation="HAS_TOPIC",
                dst=("concept", concept.id),
                provenance="openalex",
                verified=False,
                evidence={"score": topic["score"], "subfield": None, "field": None},
            )
            concepts.append(concept.id)
        extra = [
            get_or_create_concept(session, "openalex_topic", f"T9900{i}", f"Test topic {i}") for i in range(4)
        ]
        for i, concept in enumerate(extra):
            upsert_edge(
                session,
                src=("work", pid),
                relation="HAS_TOPIC",
                dst=("concept", concept.id),
                provenance="openalex",
                verified=False,
                confidence=0.0005 if i < 3 else None,
            )
        # a second provenance with a higher score for the last OpenAlex topic keeps the best score
        upsert_edge(
            session,
            src=("work", pid),
            relation="HAS_TOPIC",
            dst=("concept", concepts[2]),
            provenance="test",
            verified=False,
            confidence=0.002,
        )
    with session_scope() as session:
        topics = paper_topics(session, [pid, other])
    assert set(topics) == {pid}
    ranked = topics[pid]
    assert len(ranked) == 5
    assert [t.concept_id for t in ranked[:3]] == [concepts[0], concepts[2], concepts[1]]
    assert ranked[0].label == "Topic Modeling" and ranked[0].score == pytest.approx(
        work["topics"][0]["score"]
    )
    assert ranked[1].score == pytest.approx(0.002)
    assert [t.concept_id for t in ranked[3:]] == [extra[0].id, extra[1].id]  # ties by concept id
    with session_scope() as session:
        assert [t.concept_id for t in paper_topics(session, [pid], limit=1)[pid]] == [concepts[0]]
