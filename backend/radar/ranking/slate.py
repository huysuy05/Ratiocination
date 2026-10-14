"""Deck building (SPEC §10). Owner: rank.

``build_slate(project_id, mode)`` works in short sessions and never holds the write lock while it
embeds:

1. read: the project, its seeds, labels and keywords, the candidate papers (with their titles and
   abstracts when the project has keywords), its documents' chunks;
2. vectors: local vectors for candidates, labeled papers, chunks and the project question (each
   ``ensure_*`` call commits per batch);
3. read: the vectors, anchor titles, chunk metadata and CITES edges touching anchors;
4. compute (pure): features -> score -> maximal marginal relevance -> reasons;
5. write: one short transaction stores the slate and its items.

A slate is always stored - an empty one carries a message saying why.
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from radar.api.serializers import project_keywords, project_settings
from radar.db import session_scope
from radar.embeddings.registry import get_embedder
from radar.embeddings.vectors import ensure_paper_vectors, ensure_vectors, get_vectors
from radar.models import (
    Document,
    DocumentChunk,
    DocumentSection,
    Paper,
    Project,
    ProjectPaperState,
    ProjectSeed,
    RecommendationItem,
    RecommendationSlate,
    utcnow,
)
from radar.ranking import explain, features, mmr, preference
from radar import keywords as kw
from radar.schemas import ProjectSettings
from radar.store.graph import edges_for_works
from radar.store.states import ANCHOR_WEIGHT_BY_STATE, PaperLabel, label_summary

log = logging.getLogger(__name__)

RANKER_VERSION = "radar-0.3"
MODES = ("daily", "more")
MAX_CANDIDATES = 2000
SHOWN_LIMIT = 2
MORE_WINDOW = timedelta(hours=24)
ARXIV_SOURCES = ("arxiv_feed", "arxiv_api", "arxiv_oai")
SEED_WEIGHT = 1.0
SNIPPET_CHARS = 240
KEYWORD_EXTRA = 200
"""Window papers beyond :data:`MAX_CANDIDATES` whose title names a project keyword that still join."""
_IN_CHUNK = 500

Progress = Callable[[float, str], None]


@dataclass(frozen=True)
class Candidate:
    paper_id: int
    category: str | None  # the first project category the paper is listed in (primary first)
    day: date  # publication date, else the day it was first seen


@dataclass(frozen=True)
class ChunkInfo:
    chunk_id: int
    document_id: int
    filename: str
    section: str
    page: int | None
    snippet: str


@dataclass(frozen=True)
class Anchor:
    paper_id: int
    kind: str  # 'seed' | 'relevant' | 'saved'
    weight: float


# ------------------------------------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------------------------------------


def _first_category(primary: str | None, categories: list[str] | None, wanted: set[str]) -> str | None:
    if primary in wanted:
        return primary
    return next((c for c in categories or () if c in wanted), None)


def _title_matches(session: Session, overflow: list[Candidate], keywords: Sequence[str]) -> list[Candidate]:
    """The ``overflow`` candidates (in order) whose title names one of ``keywords``."""
    titles: dict[int, str] = {}
    ids = [c.paper_id for c in overflow]
    for start in range(0, len(ids), _IN_CHUNK):
        chunk = ids[start : start + _IN_CHUNK]
        titles.update(
            session.execute(select(Paper.id, Paper.canonical_title).where(Paper.id.in_(chunk))).all()
        )
    return [
        c
        for c in overflow
        if any(m.field == "title" for m in kw.match_keywords(keywords, titles.get(c.paper_id), None))
    ]


def window_candidates(
    session: Session,
    project_id: int,
    settings: ProjectSettings,
    mode: str,
    today: date,
    keywords: Sequence[str] = (),
) -> tuple[list[Candidate], int]:
    """``(candidates newest first, papers of the window in the project's categories)`` (SPEC §10.1).

    The newest :data:`MAX_CANDIDATES` are kept; with ``keywords``, up to :data:`KEYWORD_EXTRA` older
    window papers whose title names a keyword are added after them, so a busy category cannot push a
    paper that names the user's keyword out of the window.
    """
    wanted = set(settings.arxiv_categories)
    if not wanted:
        return [], 0
    cutoff = today - timedelta(days=settings.time_window_days)
    cutoff_dt = datetime.combine(cutoff, time.min, tzinfo=UTC)
    rows = session.execute(
        select(
            Paper.id,
            Paper.primary_category,
            Paper.categories_json,
            Paper.publication_date,
            Paper.first_seen_at,
        ).where(
            Paper.status == "active",
            or_(
                Paper.publication_date >= cutoff,
                and_(
                    Paper.publication_date.is_(None),
                    Paper.ingested_via.in_(ARXIV_SOURCES),
                    Paper.first_seen_at >= cutoff_dt,
                ),
            ),
        )
    ).all()
    in_window: list[Candidate] = []
    for paper_id, primary, categories, published, first_seen in rows:
        category = _first_category(primary, categories, wanted)
        if category is None:
            continue
        in_window.append(Candidate(paper_id, category, published or first_seen.date()))
    n_window = len(in_window)

    excluded = set(
        session.scalars(
            select(ProjectPaperState.paper_id).where(
                ProjectPaperState.project_id == project_id, ProjectPaperState.state.is_not(None)
            )
        )
    )
    excluded |= set(session.scalars(select(ProjectSeed.paper_id).where(ProjectSeed.project_id == project_id)))
    shown = Counter(
        dict(
            session.execute(
                select(RecommendationItem.paper_id, func.count())
                .join(RecommendationSlate, RecommendationSlate.id == RecommendationItem.slate_id)
                .where(RecommendationSlate.project_id == project_id)
                .group_by(RecommendationItem.paper_id)
            ).all()
        )
    )
    excluded |= {paper_id for paper_id, n in shown.items() if n >= SHOWN_LIMIT}
    if mode == "more":
        since = utcnow() - MORE_WINDOW
        excluded |= set(
            session.scalars(
                select(RecommendationItem.paper_id)
                .join(RecommendationSlate, RecommendationSlate.id == RecommendationItem.slate_id)
                .where(
                    RecommendationSlate.project_id == project_id, RecommendationSlate.generated_at >= since
                )
            )
        )
    candidates = [c for c in in_window if c.paper_id not in excluded]
    candidates.sort(key=lambda c: (c.day, c.paper_id), reverse=True)
    picked, overflow = candidates[:MAX_CANDIDATES], candidates[MAX_CANDIDATES:]
    if keywords and overflow:
        picked += _title_matches(session, overflow, keywords)[:KEYWORD_EXTRA]
    return picked, n_window


def project_chunks(session: Session, project_id: int) -> tuple[list[ChunkInfo], dict[int, str]]:
    """Chunks of the project's ready documents (metadata + snippet) and their texts by id."""
    rows = session.execute(
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            Document.filename,
            DocumentSection.heading,
            DocumentChunk.page,
            DocumentChunk.text,
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
        .where(Document.project_id == project_id, Document.status == "ready")
        .order_by(DocumentChunk.id)
    ).all()
    infos = [
        ChunkInfo(
            chunk_id=chunk_id,
            document_id=document_id,
            filename=filename,
            section=heading,
            page=page,
            snippet=" ".join(text.split())[:SNIPPET_CHARS],
        )
        for chunk_id, document_id, filename, heading, page, text in rows
    ]
    return infos, {row[0]: row[5] for row in rows}


def anchors_of(seed_ids: list[int], labels: dict[int, PaperLabel]) -> dict[int, Anchor]:
    anchors = {pid: Anchor(pid, "seed", SEED_WEIGHT) for pid in seed_ids}
    for pid, label in labels.items():
        weight = ANCHOR_WEIGHT_BY_STATE.get(label.state)
        if weight is not None and pid not in anchors:
            anchors[pid] = Anchor(pid, "relevant" if label.state == "relevant" else "saved", weight)
    return anchors


# ------------------------------------------------------------------------------------------------
# Writing
# ------------------------------------------------------------------------------------------------


def _candidate_set_hash(project_id: int, mode: str, today: date, ids: list[int]) -> str:
    text = f"{project_id}|{mode}|{today.isoformat()}|{','.join(str(i) for i in sorted(ids))}"
    return hashlib.sha256(text.encode()).hexdigest()


def _persist(
    project_id: int,
    owner_id: int,
    mode: str,
    model_id: str,
    candidate_ids: list[int],
    today: date,
    params: dict[str, Any],
    items: list[dict[str, Any]],
    message: str | None,
) -> int:
    with session_scope(write=True) as session:
        slate = RecommendationSlate(
            owner_id=owner_id,
            project_id=project_id,
            generated_at=utcnow(),
            mode=mode,
            ranker_version=RANKER_VERSION,
            embedding_model=model_id,
            candidate_set_hash=_candidate_set_hash(project_id, mode, today, candidate_ids),
            pool_size=len(candidate_ids),
            params_json=params,
            message=message,
        )
        session.add(slate)
        session.flush()
        for item in items:
            session.add(RecommendationItem(slate_id=slate.id, **item))
        session.flush()
        return slate.id


def _empty_message(settings: ProjectSettings, n_window: int) -> str:
    cats = ", ".join(settings.arxiv_categories)
    days = settings.time_window_days
    if n_window == 0:
        return (
            f"No papers from {cats} in the last {days} days yet. The first import can take a few minutes; "
            "try again shortly or widen the time window."
        )
    return f"You have seen every new paper from {cats} in the last {days} days. New papers arrive daily."


# ------------------------------------------------------------------------------------------------
# Building
# ------------------------------------------------------------------------------------------------


def _float(value: Any) -> float | None:
    return None if value is None else round(float(value), 6)


def build_slate(
    project_id: int,
    mode: str = "daily",
    *,
    progress: Progress | None = None,
    today: date | None = None,
) -> int:
    """Build and persist a slate (always persisted, possibly empty); returns its id."""
    if mode not in MODES:
        raise ValueError(f"unknown slate mode {mode!r}")
    report = progress or (lambda fraction, message: None)
    today = today or utcnow().date()
    emb = get_embedder()
    model_id = emb.model_id

    report(0.05, "Collecting candidate papers")
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise LookupError(f"project {project_id} not found")
        settings = project_settings(project)
        owner_id = project.owner_id
        question = "\n".join(t for t in (project.research_question.strip(), project.description.strip()) if t)
        keywords = kw.dedupe_keywords(project_keywords(project))
        seed_ids = list(
            session.scalars(select(ProjectSeed.paper_id).where(ProjectSeed.project_id == project_id))
        )
        labels = label_summary(session, project_id)
        candidates, n_window = window_candidates(session, project_id, settings, mode, today, keywords)
        chunks, chunk_texts = project_chunks(session, project_id)
        paper_texts: dict[int, tuple[str | None, str | None]] = {}
        if keywords:
            ids = [c.paper_id for c in candidates]
            for start in range(0, len(ids), _IN_CHUNK):
                rows = session.execute(
                    select(Paper.id, Paper.canonical_title, Paper.abstract).where(
                        Paper.id.in_(ids[start : start + _IN_CHUNK])
                    )
                ).all()
                paper_texts.update((pid, (title, abstract)) for pid, title, abstract in rows)

    base_params: dict[str, Any] = {
        "slate_size": settings.slate_size,
        "mmr_lambda": mmr.DEFAULT_LAMBDA,
        "weights": dict(features.WEIGHTS),
        "time_window_days": settings.time_window_days,
        "n_candidates": len(candidates),
        "n_labels": len(labels),
        "n_keywords": len(keywords),
    }
    if not settings.arxiv_categories or not candidates:
        message = (
            "Add arXiv categories to this project to build a deck."
            if not settings.arxiv_categories
            else _empty_message(settings, n_window)
        )
        return _persist(project_id, owner_id, mode, model_id, [], today, base_params, [], message)

    anchors = anchors_of(seed_ids, labels)
    labeled_ids = sorted(set(seed_ids) | set(labels))
    candidate_ids = [c.paper_id for c in candidates]

    report(0.2, f"Embedding {len(candidate_ids)} papers locally")
    with session_scope(write=True) as session:
        ensure_paper_vectors(session, candidate_ids + labeled_ids, model_id)
    with session_scope(write=True) as session:
        ensure_vectors(session, "project", [(project_id, question)], model_id, query=True)
    if chunk_texts:
        with session_scope(write=True) as session:
            ensure_vectors(session, "chunk", sorted(chunk_texts.items()), model_id)

    report(0.6, "Scoring candidates")
    with session_scope() as session:
        cand_matrix, cand_ids = get_vectors(session, "paper", candidate_ids, model_id)
        question_matrix, _ = get_vectors(session, "project", [project_id], model_id)
        label_matrix, label_vector_ids = get_vectors(session, "paper", labeled_ids, model_id)
        chunk_matrix, chunk_ids = get_vectors(session, "chunk", [c.chunk_id for c in chunks], model_id)
        titles = dict(
            session.execute(
                select(Paper.id, Paper.canonical_title).where(Paper.id.in_(list(anchors) or [-1]))
            ).all()
        )
        cite_edges = edges_for_works(session, list(anchors), {"CITES"}) if anchors else []

    by_id = {c.paper_id: c for c in candidates}
    cands = [by_id[pid] for pid in cand_ids]
    n = len(cands)
    if n == 0:  # pragma: no cover - ensure_paper_vectors embeds every candidate
        return _persist(
            project_id,
            owner_id,
            mode,
            model_id,
            [],
            today,
            base_params,
            [],
            _empty_message(settings, n_window),
        )
    position = {pid: i for i, pid in enumerate(cand_ids)}
    raw: dict[str, np.ndarray | None] = dict.fromkeys(features.FEATURES)

    # question (always available: the project question is required)
    q = question_matrix[0] if len(question_matrix) else None
    question_cos = cand_matrix @ q if q is not None else np.zeros(n)
    raw["question"] = question_cos

    # keyword (available when the project has keywords; every match is a fact about title/abstract)
    keyword_matches: list[list[kw.KeywordMatch]] | None = None
    if keywords:
        keyword_matches = [
            kw.match_keywords(keywords, *paper_texts.get(c.paper_id, (None, None))) for c in cands
        ]
        raw["keyword"] = np.array([kw.keyword_raw(m) for m in keyword_matches], dtype=np.float64)

    # anchor
    label_rows = {pid: i for i, pid in enumerate(label_vector_ids)}
    anchor_list = [a for a in anchors.values() if a.paper_id in label_rows]
    anchor_best: np.ndarray | None = None
    anchor_cos: np.ndarray | None = None
    if anchor_list:
        anchor_matrix = label_matrix[[label_rows[a.paper_id] for a in anchor_list]]
        weights = np.array([a.weight for a in anchor_list])
        raw["anchor"], anchor_best, anchor_cos = features.anchor_similarity(
            cand_matrix, anchor_matrix, weights
        )

    # draft
    chunk_by_id = {c.chunk_id: c for c in chunks}
    draft_best: np.ndarray | None = None
    if len(chunk_ids):
        raw["draft"], draft_best = features.best_match(cand_matrix, chunk_matrix)

    # citation
    citation: dict[int, dict[str, Any]] = {}
    for edge in cite_edges:
        src, dst = edge.src_id, edge.dst_id
        if dst in anchors and src in position and src not in citation:
            citation[src] = {"paper_id": dst, "kind": anchors[dst].kind, "direction": "cites"}
        elif src in anchors and dst in position and dst not in citation:
            citation[dst] = {"paper_id": src, "kind": anchors[src].kind, "direction": "cited_by"}
    if citation:
        column = np.zeros(n)
        for pid in citation:
            column[position[pid]] = 1.0
        raw["citation"] = column

    # preference
    pos_rows, pos_weights, neg_rows, neg_weights = [], [], [], []
    for pid in seed_ids:
        if pid in label_rows:
            pos_rows.append(label_rows[pid])
            pos_weights.append(1.0)
    for pid, label in labels.items():
        if pid not in label_rows or pid in seed_ids:
            continue
        y, weight = label.train_label
        (pos_rows if y == 1 else neg_rows).append(label_rows[pid])
        (pos_weights if y == 1 else neg_weights).append(weight)
    n_labels = len(pos_rows) + len(neg_rows)
    unlabeled_idx = preference.sample_unlabeled(n, n_labels, seed=project_id)
    dim = cand_matrix.shape[1]
    model = preference.train(
        label_matrix[pos_rows] if pos_rows else np.zeros((0, dim)),
        np.array(pos_weights),
        label_matrix[neg_rows] if neg_rows else np.zeros((0, dim)),
        np.array(neg_weights),
        cand_matrix[unlabeled_idx] if len(unlabeled_idx) else np.zeros((0, dim)),
    )
    probabilities: np.ndarray | None = None
    if model is not None:
        raw["preference"] = model.decision(cand_matrix)
        probabilities = model.probability(cand_matrix)

    # recency
    ages = np.array([(today - c.day).days for c in cands], dtype=np.float64)
    raw["recency"] = features.recency(ages)

    scored = features.score(raw, n)
    report(0.8, "Choosing a diverse deck")
    picked = mmr.mmr_select(scored.score, cand_matrix, settings.slate_size)

    items: list[dict[str, Any]] = []
    for rank, idx in enumerate(picked, start=1):
        cand = cands[idx]
        anchor_ev = None
        if anchor_best is not None and anchor_cos is not None:
            a = anchor_list[int(anchor_best[idx])]
            anchor_ev = {
                "paper_id": a.paper_id,
                "title": titles.get(a.paper_id, ""),
                "kind": a.kind,
                "cosine": float(anchor_cos[idx]),
            }
        draft_ev = None
        if draft_best is not None:
            info = chunk_by_id[chunk_ids[int(draft_best[idx])]]
            draft_ev = {
                "document_id": info.document_id,
                "chunk_id": info.chunk_id,
                "filename": info.filename,
                "section": info.section,
                "page": info.page,
                "snippet": info.snippet,
            }
        cite_ev = citation.get(cand.paper_id)
        if cite_ev is not None:
            cite_ev = {**cite_ev, "title": titles.get(cite_ev["paper_id"], "")}
        pref_ev = (
            {"n_labels": n_labels, "probability": float(probabilities[idx])}
            if probabilities is not None
            else None
        )
        keyword_ev = (
            [{"keyword": m.keyword, "field": m.field, "text": m.text} for m in keyword_matches[idx]]
            if keyword_matches is not None
            else None
        )
        evidence: dict[str, Any] = {
            "question": {"cosine": round(float(question_cos[idx]), 6)},
            "keyword": None if keyword_ev is None else {"matches": keyword_ev},
            "anchor": None
            if anchor_ev is None
            else {
                "paper_id": anchor_ev["paper_id"],
                "kind": anchor_ev["kind"],
                "cosine": round(anchor_ev["cosine"], 6),
            },
            "draft": None
            if draft_ev is None
            else {k: draft_ev[k] for k in ("document_id", "chunk_id", "section", "page")},
            "citation": None
            if cite_ev is None
            else {k: cite_ev[k] for k in ("paper_id", "kind", "direction")},
            "preference": None
            if pref_ev is None
            else {"n_labels": n_labels, "probability": round(pref_ev["probability"], 6)},
            "recency": None,
        }
        feature_json = {
            name: {
                "value": _float(scored.values[name][idx]) if scored.values[name] is not None else None,
                "raw": _float(scored.raw[name][idx]) if scored.raw[name] is not None else None,
                "weight": scored.weights[name],
                "contribution": _float(scored.contributions[name][idx]) or 0.0,
                "available": scored.values[name] is not None,
                "evidence": evidence[name] if scored.values[name] is not None else None,
            }
            for name in features.FEATURES
        }
        reasons = explain.reasons(
            explain.ExplainInput(
                values={
                    name: (None if scored.values[name] is None else float(scored.values[name][idx]))
                    for name in features.FEATURES
                },
                contributions={name: float(scored.contributions[name][idx]) for name in features.FEATURES},
                question_cosine=float(question_cos[idx]),
                anchor=anchor_ev,
                draft=draft_ev,
                citation=cite_ev,
                preference=pref_ev,
                category=cand.category,
                age_days=int(ages[idx]),
                keywords=keyword_ev,
            )
        )
        items.append(
            {
                "paper_id": cand.paper_id,
                "rank": rank,
                "score": round(float(scored.score[idx]), 6),
                "feature_scores_json": feature_json,
                "explanation_json": reasons,
            }
        )

    params = {
        **base_params,
        "norm": scored.norm,
        "n_anchors": len(anchor_list),
        "preference": {
            "trained": model is not None,
            "n_pos": model.n_pos if model else len(pos_rows),
            "n_neg": model.n_neg if model else len(neg_rows),
            "n_unlabeled": model.n_unlabeled if model else 0,
        },
    }
    slate_id = _persist(project_id, owner_id, mode, model_id, cand_ids, today, params, items, None)
    report(1.0, f"Deck ready: {len(items)} papers")
    log.info("project %s: slate %s with %d of %d candidates", project_id, slate_id, len(items), n)
    return slate_id


__all__ = [
    "KEYWORD_EXTRA",
    "MAX_CANDIDATES",
    "MODES",
    "RANKER_VERSION",
    "Anchor",
    "Candidate",
    "build_slate",
    "window_candidates",
]
