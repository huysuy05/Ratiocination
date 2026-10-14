"""api.serializers (SPEC §4, API.md): derived paper fields, items, slates, jobs, expansions, disruption."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event

from radar import models
from radar.api.serializers import (
    disruption_detail,
    disruption_label,
    expansion_status,
    job_out,
    paper_detail,
    paper_summaries,
    recommendation_items,
    slate_out,
)
from radar.db import get_engine, session_scope
from radar.errors import ApiError
from radar.schemas import (
    FEATURE_NAMES,
    AnchorEdgeReason,
    CategoryReason,
    DeletedDocumentEvidence,
    DraftMatchReason,
    PaperSummary,
    QuestionReason,
)
from radar.store.graph import get_or_create_concept, upsert_edge
from radar.store.papers import PaperRecord, upsert_paper
from radar.store.states import record_interaction

# ------------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------------


@contextmanager
def count_selects() -> Iterator[list[str]]:
    """Collect the SELECT statements the read engine executes inside the block."""
    statements: list[str] = []
    engine = get_engine(write=False)

    def before(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before)


def upsert(record: PaperRecord) -> int:
    with session_scope(write=True) as session:
        return upsert_paper(session, record).paper_id


def add_topic(paper_id: int, external_id: str, label: str, score: float | None) -> int:
    with session_scope(write=True) as session:
        concept = get_or_create_concept(session, "openalex_topic", external_id, label)
        upsert_edge(
            session,
            src=("work", paper_id),
            relation="HAS_TOPIC",
            dst=("concept", concept.id),
            provenance="openalex",
            verified=False,
            confidence=score,
            evidence={"score": score, "subfield": None, "field": None} if score is not None else None,
        )
        return concept.id


def scholar_inbox(load_fixture: Callable[[str], Any]) -> int:
    """Scholar Inbox via its arXiv API entry (2504.08385v2) and its OpenAlex work (with topics)."""
    pid = upsert(
        PaperRecord(
            source="arxiv_api",
            source_id="http://arxiv.org/abs/2504.08385v2",
            title="Scholar Inbox: Personalized Paper Recommendations for Scientists",
            abstract="Scholar Inbox is a new open-access platform designed to address the challenges researchers face.",
            authors=["Markus Flicke", "Glenn Angrabeit", "Madhav Iyengar"],
            publication_date=date(2025, 4, 11),
            identifiers={"arxiv": "2504.08385v2"},
            version_label="v2",
            categories=["cs.CL", "cs.AI", "cs.IR"],
            landing_url="https://arxiv.org/abs/2504.08385v2",
            pdf_url="https://arxiv.org/pdf/2504.08385v2",
            source_updated_at=datetime(2025, 6, 14, 16, 27, 19, tzinfo=UTC),
        )
    )
    work = load_fixture("openalex_work_arxiv.json")
    assert (
        upsert(
            PaperRecord(
                source="openalex",
                source_id=work["id"],
                title=work["title"],
                identifiers={"openalex": work["id"], "doi": work["doi"]},
                is_oa=work["open_access"]["is_oa"],
                oa_url=work["open_access"]["oa_url"],
                best_oa_pdf=work["best_oa_location"]["pdf_url"],
                raw=work,
            )
        )
        == pid
    )
    for topic in work["topics"]:
        add_topic(pid, topic["id"], topic["display_name"], topic["score"])
    return pid


def specter_published(load_fixture: Callable[[str], Any]) -> int:
    """SPECTER through its OpenAlex record only (no arXiv identifier)."""
    work = load_fixture("openalex_work_published.json")
    loc = work["primary_location"]
    return upsert(
        PaperRecord(
            source="openalex",
            source_id=work["id"],
            title=work["title"],
            authors=["Arman Cohan", "Sergey Feldman"],
            year=2020,
            identifiers={"openalex": work["id"], "doi": work["doi"]},
            version_label="published",
            venue="Annual Meeting of the Association for Computational Linguistics (ACL)",
            landing_url=loc["landing_page_url"],
            pdf_url=loc["pdf_url"],
            license=loc["license"],
            is_oa=True,
            oa_url=work["open_access"]["oa_url"],
            best_oa_pdf=work["best_oa_location"]["pdf_url"],
            cited_by_count=work["cited_by_count"],
        )
    )


def feature_scores(draft_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    scores: dict[str, Any] = {
        name: {
            "value": 0.5,
            "raw": 0.25,
            "weight": 0.1,
            "contribution": 0.05,
            "available": True,
            "evidence": None,
        }
        for name in FEATURE_NAMES
    }
    scores["draft"]["evidence"] = draft_evidence
    scores["preference"]["available"] = False
    return scores


def make_slate(
    project_id: int,
    items: list[dict[str, Any]],
    *,
    generated_at: datetime | None = None,
    message: str | None = None,
    mode: str = "daily",
) -> int:
    with session_scope(write=True) as session:
        slate = models.RecommendationSlate(
            project_id=project_id,
            mode=mode,
            ranker_version="test-ranker-1",
            embedding_model="local/hashing-v1",
            candidate_set_hash="0" * 64,
            pool_size=120,
            message=message,
            generated_at=generated_at or datetime.now(UTC),
        )
        session.add(slate)
        session.flush()
        for rank, spec in enumerate(items, start=1):
            session.add(
                models.RecommendationItem(
                    slate_id=slate.id,
                    paper_id=spec["paper_id"],
                    rank=rank,
                    score=spec.get("score", 0.4),
                    feature_scores_json=spec.get("features", feature_scores()),
                    explanation_json=spec.get("reasons", []),
                )
            )
        return slate.id


def act(project_id: int, paper_id: int, action: str, slate_id: int | None = None) -> int:
    with session_scope(write=True) as session:
        return record_interaction(session, project_id, paper_id, action, slate_id=slate_id).id


# ------------------------------------------------------------------------------------------------
# Paper summaries
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("n", [3, 40])
def test_paper_summaries_use_a_fixed_number_of_queries(make_paper: Any, n: int) -> None:
    ids = []
    for i in range(n):
        pid = make_paper(
            identifiers={"arxiv": f"2610.{i:05d}", "doi": f"10.0000/test.{i}"},
            authors=["Ada Test", "Grace Example"],
            versions=["v1", "v2", "published"],
        )
        add_topic(pid, f"T{100 + i}", f"Test topic {i}", 0.5)
        ids.append(pid)
    with session_scope() as session:
        session.connection()  # BEGIN happens outside the counted block
        with count_selects() as statements:
            summaries = paper_summaries(session, ids + [987654])
        assert len(statements) == 5  # papers, identifiers, authors, versions, topics
        with count_selects() as statements:
            paper_summaries(session, ids, user_id=models.LOCAL_USER_ID)
        assert len(statements) == 6  # ... plus the user's read marks
    assert set(summaries) == set(ids)
    one = summaries[ids[-1]]
    assert isinstance(one, PaperSummary)
    assert one.latest_version == "v2" and len(one.authors) == 2 and len(one.topics) == 1
    with session_scope() as session, count_selects() as statements:
        assert paper_summaries(session, []) == {}
        assert statements == []


def test_summary_of_an_arxiv_paper(load_fixture: Callable[[str], Any]) -> None:
    pid = scholar_inbox(load_fixture)
    with session_scope() as session:
        summary = paper_summaries(session, [pid])[pid]
    assert summary.title == "Scholar Inbox: Personalized Paper Recommendations for Scientists"
    assert [(a.name, a.position, a.openalex_id) for a in summary.authors] == [
        ("Markus Flicke", 0, None),
        ("Glenn Angrabeit", 1, None),
        ("Madhav Iyengar", 2, None),
    ]
    assert summary.publication_date == date(2025, 4, 11) and summary.year == 2025
    assert summary.primary_category == "cs.CL" and summary.categories == ["cs.CL", "cs.AI", "cs.IR"]
    # the arXiv DOI appears only as `arxiv`
    assert summary.identifiers.model_dump() == {
        "doi": None,
        "arxiv": "2504.08385",
        "openalex": "W4414828500",
        "s2": None,
    }
    assert summary.latest_version == "v2"
    assert summary.links.model_dump() == {
        "abs": "https://arxiv.org/abs/2504.08385",
        "pdf": "https://arxiv.org/pdf/2504.08385v2",
        "doi": None,
        "oa": "https://arxiv.org/pdf/2504.08385",
    }
    assert [(t.label, t.score) for t in summary.topics] == [
        ("Recommender Systems and Techniques", 1.0),
        ("Information Retrieval and Search Behavior", 0.0),
        ("Academic Publishing and Open Access", 0.0),
    ]
    assert summary.status == "active" and summary.is_oa is True
    assert (summary.cited_by_count, summary.cd, summary.read_at) == (None, None, None)
    payload = summary.model_dump(mode="json")
    assert payload["publication_date"] == "2025-04-11"


def test_summary_of_a_published_paper(load_fixture: Callable[[str], Any]) -> None:
    pid = specter_published(load_fixture)
    with session_scope() as session:
        summary = paper_summaries(session, [pid])[pid]
    assert summary.identifiers.doi == "10.18653/v1/2020.acl-main.207" and summary.identifiers.arxiv is None
    assert summary.latest_version == "published"
    assert summary.links.abs == "https://doi.org/10.18653/v1/2020.acl-main.207"  # latest landing_url
    assert summary.links.pdf == "https://www.aclweb.org/anthology/2020.acl-main.207.pdf"
    assert summary.links.doi == "https://doi.org/10.18653/v1/2020.acl-main.207"
    assert summary.links.oa == "https://www.aclweb.org/anthology/2020.acl-main.207.pdf"
    assert summary.topics == [] and summary.cited_by_count == 471


def test_summary_links_fallbacks(make_paper: Any) -> None:
    stub = make_paper(identifiers={"arxiv": "hep-th/9901001"}, status="stub")
    retracted = make_paper(
        identifiers={"doi": "10.1016/s0140-6736(97)11096-0"}, status="retracted", best_oa_pdf=None
    )
    oa_only = make_paper(best_oa_pdf="https://example.invalid/test-paper.pdf")
    openalex_only = make_paper(identifiers={"openalex": "W123"})
    with session_scope() as session:
        summaries = paper_summaries(session, [stub, retracted, oa_only, openalex_only])
    assert summaries[stub].links.abs == "https://arxiv.org/abs/hep-th/9901001"
    assert summaries[stub].links.pdf == "https://arxiv.org/pdf/hep-th/9901001"  # no vN known
    assert summaries[stub].latest_version is None and summaries[stub].status == "stub"
    assert summaries[retracted].links.doi == "https://doi.org/10.1016/s0140-6736(97)11096-0"
    assert summaries[retracted].links.abs is None and summaries[retracted].links.pdf is None
    assert summaries[retracted].status == "retracted"
    assert summaries[oa_only].links.pdf == "https://example.invalid/test-paper.pdf"
    assert summaries[openalex_only].links.abs == "https://openalex.org/W123"


def test_topics_are_capped_and_ordered(make_paper: Any) -> None:
    pid = make_paper()
    scores = [0.2, 0.9, None, 0.5, 0.1, 0.7, 0.3]
    for i, score in enumerate(scores):
        add_topic(pid, f"T{900 + i}", f"Test topic {i}", score)
    with session_scope() as session:
        topics = paper_summaries(session, [pid])[pid].topics
    assert [t.score for t in topics] == [0.9, 0.7, 0.5, 0.3, 0.2]


def test_read_marks_disruption_and_author_ids_in_summaries(make_paper: Any) -> None:
    read, unread = make_paper(disruption_cd=0.25), make_paper()
    when = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    with session_scope(write=True) as session:
        other = models.User(email="zq-other@example.invalid", display_name="Other")
        session.add(other)
        session.flush()
        session.add(models.PaperRead(user_id=models.LOCAL_USER_ID, paper_id=read, read_at=when))
        session.add(models.PaperRead(user_id=other.id, paper_id=unread, read_at=when))
        author = session.query(models.PaperAuthor).filter_by(paper_id=read).one()
        author.openalex_author_id = "A42"
    with session_scope() as session:
        mine = paper_summaries(session, [read, unread], user_id=models.LOCAL_USER_ID)
        anonymous = paper_summaries(session, [read, unread])
    assert mine[read].read_at == when and mine[unread].read_at is None  # another user's mark is private
    assert anonymous[read].read_at is None
    assert mine[read].cd == 0.25 and mine[unread].cd is None
    assert mine[read].authors[0].openalex_id == "A42"


# ------------------------------------------------------------------------------------------------
# Paper detail
# ------------------------------------------------------------------------------------------------


def test_paper_detail_without_project(load_fixture: Callable[[str], Any]) -> None:
    pid = specter_published(load_fixture)
    with session_scope() as session:
        detail = paper_detail(session, pid, None)
    assert detail.id == pid and detail.title.startswith("SPECTER")
    assert set(detail.provenance) >= {"title", "authors", "venue", "cited_by_count"}
    assert detail.provenance["venue"].source == "openalex"
    assert detail.counts.model_dump() == {"references": None, "citations": None}
    assert (detail.state, detail.last_interaction_id, detail.recommendation) == (None, None, None)
    assert detail.expansion.status == "none" and detail.disruption is None
    payload = detail.model_dump(mode="json")
    assert payload["provenance"]["title"]["observed_at"].endswith("Z")
    assert "versions" not in payload and "flags" not in payload
    with session_scope() as session, pytest.raises(ApiError) as info:
        paper_detail(session, 123456, None)
    assert (info.value.status, info.value.code) == (404, "not_found")


def test_paper_detail_counts(make_paper: Any) -> None:
    pid, a, b, c = make_paper(), make_paper(), make_paper(), make_paper()
    with session_scope(write=True) as session:
        for provenance in ("s2", "openalex"):
            upsert_edge(
                session,
                src=("work", pid),
                relation="CITES",
                dst=("work", a),
                provenance=provenance,
                verified=True,
            )
        upsert_edge(
            session,
            src=("work", pid),
            relation="CITES",
            dst=("work", b),
            provenance="openalex",
            verified=True,
        )
        upsert_edge(
            session, src=("work", c), relation="CITES", dst=("work", pid), provenance="s2", verified=True
        )
        upsert_edge(
            session,
            src=("work", pid),
            relation="SEMANTICALLY_SIMILAR",
            dst=("work", c),
            provenance="local/knn",
            verified=False,
            confidence=0.9,
            model_version="local/hashing-v1",
        )
    with session_scope() as session:
        assert paper_detail(session, pid, None).counts.model_dump() == {"references": 2, "citations": 1}
    expanded = make_paper()
    with session_scope(write=True) as session:
        session.add(
            models.PaperExpansion(
                paper_id=expanded, status="done", last_expanded_at=datetime.now(UTC), sources_json={}
            )
        )
    with session_scope() as session:
        counts = paper_detail(session, expanded, None).counts
    assert (counts.references, counts.citations) == (0, 0)


def test_paper_detail_with_project(make_project: Any, make_paper: Any) -> None:
    project, other_project = make_project(), make_project()
    pid = make_paper(versions=["v1"])
    old = make_slate(project, [{"paper_id": pid}], generated_at=datetime.now(UTC) - timedelta(days=2))
    latest = make_slate(project, [{"paper_id": make_paper()}, {"paper_id": pid}])
    make_slate(other_project, [{"paper_id": pid}])
    interaction = act(project, pid, "relevant", slate_id=latest)
    with session_scope() as session:
        detail = paper_detail(session, pid, project, user_id=models.LOCAL_USER_ID)
        other = paper_detail(session, pid, other_project)
    assert (detail.state, detail.last_interaction_id) == ("relevant", interaction)
    assert detail.recommendation is not None
    assert detail.recommendation.slate_id == latest != old
    assert detail.recommendation.rank == 2 and detail.recommendation.state == "relevant"
    assert other.state is None
    assert other.recommendation is not None and other.recommendation.slate_id not in (old, latest)


# ------------------------------------------------------------------------------------------------
# Recommendation items and slates
# ------------------------------------------------------------------------------------------------

REASONS = [
    {
        "code": "question",
        "kind": "model",
        "text": "Close to your research question",
        "evidence": {"cosine": 0.71},
        "contribution": 0.2,
    },
    {
        "code": "cites_anchor",
        "kind": "metadata",
        "text": "Cites “Test paper A” (your seed paper)",
        "evidence": {
            "anchor_paper_id": 1,
            "anchor_title": "Test paper A",
            "anchor_kind": "seed",
            "direction": "cites",
        },
        "contribution": 0.1,
    },
    {
        "code": "draft_match",
        "kind": "model",
        "text": "Matched a private document that has since been deleted",
        "evidence": {"deleted_document": True},
        "contribution": 0.08,
    },
    {
        "code": "category",
        "kind": "metadata",
        "text": "Listed in cs.IR, one of your project's categories",
        "evidence": {"category": "cs.IR"},
        "contribution": None,
    },
    {
        "code": "calibration_sample",
        "kind": "system",
        "text": "dropped",
        "evidence": None,
        "contribution": None,
    },
]


def deck(make_project: Any, make_paper: Any) -> tuple[int, int, list[int]]:
    project = make_project()
    papers = [make_paper() for _ in range(3)]
    slate = make_slate(
        project,
        [
            {"paper_id": papers[0], "score": 0.61, "reasons": REASONS},
            {"paper_id": papers[1], "score": 0.42},
            {"paper_id": papers[2], "score": 0.3},
        ],
    )
    return project, slate, papers


def test_recommendation_items(make_project: Any, make_paper: Any) -> None:
    project, slate, papers = deck(make_project, make_paper)
    marked = act(project, papers[0], "relevant", slate_id=slate)
    act(project, papers[2], "open", slate_id=slate)  # passive: no state
    with session_scope() as session:
        items = recommendation_items(session, slate, project)
    assert [i.paper.id for i in items] == papers and [i.rank for i in items] == [1, 2, 3]
    first, second, third = items
    assert (first.state, first.last_interaction_id) == ("relevant", marked)
    assert (second.state, third.state) == (None, None)
    assert first.slate_id == slate and first.score == 0.61
    assert [type(r) for r in first.reasons] == [
        QuestionReason,
        AnchorEdgeReason,
        DraftMatchReason,
        CategoryReason,
    ]  # the removed v0.2 reason code is skipped
    assert isinstance(first.reasons[2].evidence, DeletedDocumentEvidence)
    assert first.reasons[1].evidence.anchor_kind == "seed"  # type: ignore[union-attr]
    features = first.features.model_dump()
    assert list(features) == list(FEATURE_NAMES)
    assert features["preference"]["available"] is False and "evidence" not in features["draft"]
    payload = [i.model_dump(mode="json") for i in items]
    assert all(
        set(p)
        == {"slate_id", "paper", "rank", "score", "features", "reasons", "state", "last_interaction_id"}
        for p in payload
    )


def test_slate_out(make_project: Any, make_paper: Any) -> None:
    project, slate, papers = deck(make_project, make_paper)
    act(project, papers[1], "save", slate_id=slate)
    with session_scope() as session:
        out = slate_out(session, slate, project)
    assert out.id == slate and out.project_id == project and out.mode == "daily"
    assert (out.ranker_version, out.embedding_model, out.pool_size) == (
        "test-ranker-1",
        "local/hashing-v1",
        120,
    )
    assert out.remaining == 2 and len(out.items) == 3 and out.message is None
    assert out.model_dump(mode="json")["generated_at"].endswith("Z")
    other_project = make_project()
    with session_scope() as session:
        for bad_project, bad_slate in ((other_project, slate), (project, 999_999)):
            with pytest.raises(ApiError) as info:
                slate_out(session, bad_slate, bad_project)
            assert info.value.status == 404
            with pytest.raises(ApiError):
                recommendation_items(session, bad_slate, bad_project)


def test_empty_slate_keeps_its_message(make_project: Any) -> None:
    project = make_project()
    empty = make_slate(
        project, [], message="No new papers in your categories in the last 14 days", mode="more"
    )
    with session_scope() as session:
        out = slate_out(session, empty, project)
    assert out.items == [] and out.remaining == 0 and out.mode == "more"
    assert out.message == "No new papers in your categories in the last 14 days"


# ------------------------------------------------------------------------------------------------
# Disruption
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("cd", "n_i", "n_j", "label"),
    [
        (-0.0005, 174, 294, "consolidating"),  # SPECTER, 2026-10-12: 37% eclipse its references
        (0.02, 60, 40, "disruptive"),
        (0.001, 51, 49, "mixed"),  # a 51/49 split is not a breakthrough
        (-0.001, 49, 51, "mixed"),
        (None, 0, 0, "undefined"),
        (0.0, 0, 0, "undefined"),
    ],
)
def test_disruption_label(cd: float | None, n_i: int, n_j: int, label: str) -> None:
    assert disruption_label(cd, n_i, n_j) == label


def test_disruption_detail(make_paper: Any) -> None:
    never = make_paper()
    measured = make_paper(
        disruption_cd=-0.0005,
        disruption_ni=174,
        disruption_nj=294,
        disruption_nk=245_711,
        disruption_refs=54,
        disruption_refs_total=54,
        disruption_at=datetime(2026, 10, 12, tzinfo=UTC),
    )
    no_refs = make_paper(disruption_refs=0, disruption_at=datetime(2026, 10, 12, tzinfo=UTC))
    with session_scope() as session:
        assert disruption_detail(session.get(models.Paper, never)) is None
        detail = disruption_detail(session.get(models.Paper, measured))
        empty = disruption_detail(session.get(models.Paper, no_refs))
    assert detail is not None and (detail.n_citing, detail.label, detail.refs_used) == (
        468,
        "consolidating",
        54,
    )
    assert detail.text == (
        "Of 468 citing papers, 174 (37%) cite it without citing its references and 294 cite both; "
        "245,711 other papers cite its references but not it."
    )
    assert empty is not None and empty.cd is None and empty.label == "undefined"
    assert empty.text.startswith("No reference list is known")


# ------------------------------------------------------------------------------------------------
# Jobs and expansions
# ------------------------------------------------------------------------------------------------


def test_job_out(make_project: Any) -> None:
    project = make_project()
    started = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)
    with session_scope(write=True) as session:
        job = models.Job(
            kind="build_deck",
            status="failed",
            project_id=project,
            params_json={"mode": "daily"},
            dedupe_key="test-job-out",
            stats_json={"pool": 12},
            progress=0.5,
            message="Embedding 412 papers",
            error_code="internal_error",
            error_message="internal error (job 1)",
            started_at=started,
            finished_at=started + timedelta(minutes=1),
        )
        session.add(job)
        session.flush()
        job_id = job.id
    with session_scope() as session:
        row = session.get(models.Job, job_id)
        assert row is not None
        out = job_out(row)
        hidden = job_out(row, hide_project=True)
    assert out.id == job_id and out.kind == "build_deck" and out.status == "failed"
    assert out.project_id == project and out.params == {"mode": "daily"} and out.stats == {"pool": 12}
    assert hidden.project_id is None and hidden.id == job_id
    assert (out.progress, out.message, out.error_code, out.error_message) == (
        0.5,
        "Embedding 412 papers",
        "internal_error",
        "internal error (job 1)",
    )
    payload = out.model_dump(mode="json")
    assert (
        payload["started_at"] == "2026-10-12T08:00:00Z" and payload["finished_at"] == "2026-10-12T08:01:00Z"
    )
    assert payload["created_at"].endswith("Z")


def test_expansion_status(make_paper: Any) -> None:
    absent, done, stale, orphan = make_paper(), make_paper(), make_paper(), make_paper()
    finished = datetime(2026, 10, 11, 9, 30, tzinfo=UTC)
    with session_scope(write=True) as session:
        failed_job = models.Job(
            kind="expand",
            status="failed",
            dedupe_key="test-expand-failed",
            error_code="interrupted",
            error_message="Interrupted by a restart",
        )
        session.add(failed_job)
        session.flush()
        session.add_all(
            [
                models.PaperExpansion(
                    paper_id=done, status="done", last_expanded_at=finished, sources_json={"s2": 3}
                ),
                models.PaperExpansion(
                    paper_id=stale, status="running", job_id=failed_job.id, sources_json={}
                ),
                models.PaperExpansion(paper_id=orphan, status="queued", job_id=987654, sources_json={}),
            ]
        )
        failed_job_id = failed_job.id
    with session_scope() as session:
        assert expansion_status(session, absent).model_dump() == {
            "status": "none",
            "job_id": None,
            "last_expanded_at": None,
            "message": None,
        }
        assert expansion_status(session, done).model_dump() == {
            "status": "done",
            "job_id": None,
            "last_expanded_at": finished,
            "message": None,
        }
        stale_status = expansion_status(session, stale)
        assert (stale_status.status, stale_status.job_id, stale_status.message) == (
            "failed",
            failed_job_id,
            "Interrupted by a restart",
        )
        orphan_status = expansion_status(session, orphan)
        assert (orphan_status.status, orphan_status.message) == ("failed", "Expansion was interrupted")
