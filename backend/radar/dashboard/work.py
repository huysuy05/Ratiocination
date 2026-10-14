"""Relation to your work (SPEC §14.5). Owner: dashboard.

"Your work" in a project is its question vector (question + description, embedded as a query when a
deck is built) and the vectors of its ready documents' chunks. A paper's work similarity is

    s(p) = max(cos(p, question), max over chunks cos(p, chunk))

together with the part that matched. Per project in scope:

* ``closest`` - the 5 liked papers (the library, all time) with the highest s;
* ``quartiles`` - the period's swiped papers in 4 groups by s, with the share marked relevant or saved
  in each (needs 8 papers);
* ``correlation`` - Pearson r between s and the decision (1 relevant/saved, 0 not relevant) over the
  period's swiped papers, when there are at least 10 and both decisions occur;
* ``sections`` - per document section (not the references), the liked papers whose best chunk is in
  it with cosine >= the model's similarity threshold, and the closest one; empty sections are gaps;
* ``drift`` - the weekly mean cos(liked papers, question) over the last 12 weeks.

A paper's decision is its latest swipe in the period. Only vectors already stored for the active model
are used (every deck build embeds the project's labeled papers, its question and its chunks), so this
view never embeds anything; papers without a vector are left out of the counts.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.api.serializers import paper_summaries
from radar.dashboard.common import POSITIVE, SWIPES, Scope
from radar.embeddings.registry import get_embedder
from radar.embeddings.vectors import get_vectors
from radar.models import Document, DocumentChunk, DocumentSection, Interaction, Project, ProjectPaperState
from radar.schemas import (
    DashboardWork,
    DocumentMatch,
    QuestionMatch,
    WorkCorrelation,
    WorkDriftPoint,
    WorkMatch,
    WorkPaper,
    WorkProject,
    WorkQuartile,
    WorkSection,
)

CLOSEST = 5
QUARTILE_MIN = 8
CORRELATION_MIN = 10
DRIFT_WEEKS = 12
SNIPPET_CHARS = 240
LIBRARY_STATES = ("relevant", "saved")
REFERENCES = re.compile(
    r"^\W*(?:\d+(?:\.\d+)*\.?\s+)?(?:references|bibliography|works cited|literature cited)\b", re.I
)
STRENGTHS: tuple[tuple[float, str], ...] = ((0.1, "none"), (0.3, "weak"), (0.5, "moderate"))


@dataclass(frozen=True)
class Part:
    """A document chunk of the user's work."""

    chunk_id: int
    document_id: int
    filename: str
    section_id: int
    heading: str
    page: int | None
    snippet: str


@dataclass(frozen=True)
class Scores:
    """Per paper: cosine to the question and to the closest chunk (``None`` when there is none)."""

    question: np.ndarray | None
    chunk: np.ndarray | None
    chunk_row: np.ndarray | None  # index into ``Work.parts``

    def work(self, i: int) -> tuple[float, int]:
        """``(s, part)`` of row ``i``: the work similarity and the matching part (-1 = the question)."""
        q = float(self.question[i]) if self.question is not None else None
        c = float(self.chunk[i]) if self.chunk is not None else None
        if c is not None and (q is None or c > q):
            return c, int(self.chunk_row[i])  # type: ignore[index]
        return (q if q is not None else 0.0), -1


@dataclass(frozen=True)
class Work:
    question: np.ndarray | None  # (dim,)
    chunks: np.ndarray  # (len(parts), dim)
    parts: list[Part]

    @property
    def empty(self) -> bool:
        return self.question is None and not self.parts

    def score(self, matrix: np.ndarray) -> Scores:
        if len(matrix) == 0:
            return Scores(None, None, None)
        question = matrix @ self.question if self.question is not None else None
        if not self.parts:
            return Scores(question, None, None)
        cos = matrix @ self.chunks.T
        rows = cos.argmax(axis=1)
        return Scores(question, cos[np.arange(len(matrix)), rows], rows)

    def match(self, part: int) -> WorkMatch:
        if part < 0:
            return QuestionMatch()
        p = self.parts[part]
        return DocumentMatch(
            document_id=p.document_id, filename=p.filename, section=p.heading, page=p.page, snippet=p.snippet
        )


def load_work(session: Session, project_id: int, model_id: str) -> Work:
    """The project's question vector and ready chunk vectors for ``model_id``."""
    question, _ = get_vectors(session, "project", [project_id], model_id)
    rows = session.execute(
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            Document.filename,
            DocumentSection.id,
            DocumentSection.heading,
            DocumentChunk.page,
            func.substr(DocumentChunk.text, 1, SNIPPET_CHARS * 2),
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .join(DocumentSection, DocumentSection.id == DocumentChunk.section_id)
        .where(Document.project_id == project_id, Document.status == "ready")
        .order_by(DocumentChunk.id)
    ).all()
    matrix, ids = get_vectors(session, "chunk", [row[0] for row in rows], model_id)
    by_id = {row[0]: row for row in rows}
    parts = [
        Part(
            chunk_id=cid,
            document_id=by_id[cid][1],
            filename=by_id[cid][2],
            section_id=by_id[cid][3],
            heading=by_id[cid][4],
            page=by_id[cid][5],
            snippet=" ".join(str(by_id[cid][6]).split())[:SNIPPET_CHARS],
        )
        for cid in ids
    ]
    return Work(question[0] if len(question) else None, matrix, parts)


def work_similarity(
    session: Session, papers_by_project: dict[int, list[int]], model_id: str
) -> dict[tuple[int, int], float]:
    """``s`` of each ``(project_id, paper_id)`` (pairs without vectors are omitted)."""
    out: dict[tuple[int, int], float] = {}
    for project_id, paper_ids in papers_by_project.items():
        work = load_work(session, project_id, model_id)
        if work.empty:
            continue
        matrix, found = get_vectors(session, "paper", paper_ids, model_id)
        scores = work.score(matrix)
        for i, pid in enumerate(found):
            out[(project_id, pid)] = round(scores.work(i)[0], 4)
    return out


# ------------------------------------------------------------------------------------------------
# Statistics
# ------------------------------------------------------------------------------------------------


def quartiles(pairs: list[tuple[float, int]]) -> list[WorkQuartile]:
    """``(s, decision)`` pairs in 4 groups of increasing s (``[]`` below ``QUARTILE_MIN``)."""
    if len(pairs) < QUARTILE_MIN:
        return []
    ordered = sorted(pairs)
    out = []
    for group in np.array_split(np.array(ordered, dtype=float), 4):
        out.append(
            WorkQuartile(
                lo=round(float(group[:, 0].min()), 4),
                hi=round(float(group[:, 0].max()), 4),
                n=len(group),
                positive_rate=round(float(group[:, 1].mean()), 4),
            )
        )
    return out


def strength(r: float) -> str:
    size = abs(r)
    return next((word for bound, word in STRENGTHS if size < bound), "strong")


def correlation(pairs: list[tuple[float, int]]) -> WorkCorrelation | None:
    """Pearson r between s and the decision, or ``None`` when it is not meaningful."""
    n = len(pairs)
    if n < CORRELATION_MIN:
        return None
    s = np.array([p[0] for p in pairs], dtype=float)
    y = np.array([p[1] for p in pairs], dtype=float)
    if y.min() == y.max() or float(s.std()) < 1e-9:
        return None
    r = round(float(np.corrcoef(s, y)[0, 1]), 2)
    word = strength(r)
    stats = f"(r = {r:.2f}, n = {n})".replace("-", "−")
    if word == "none":
        text = f"How close a paper was to your work made little difference to your decisions {stats}"
    elif r > 0:
        text = f"Papers closer to your work were more likely to be marked relevant {stats}"
    else:
        text = f"Papers further from your work were more likely to be marked relevant {stats}"
    return WorkCorrelation(r=r, n=n, strength=word, text=text)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------------
# Per project
# ------------------------------------------------------------------------------------------------


def _sections(session: Session, project_id: int) -> list[tuple[int, int, str, str]]:
    """``(section_id, document_id, filename, heading)`` of ready documents, references excluded."""
    rows = session.execute(
        select(DocumentSection.id, Document.id, Document.filename, DocumentSection.heading)
        .join(Document, Document.id == DocumentSection.document_id)
        .where(Document.project_id == project_id, Document.status == "ready")
        .order_by(Document.id, DocumentSection.ordinal)
    ).all()
    return [(sid, did, name, heading) for sid, did, name, heading in rows if not REFERENCES.match(heading)]


def project_work(
    session: Session, scope: Scope, project: Project, model_id: str, threshold: float
) -> WorkProject:
    work = load_work(session, project.id, model_id)
    weeks = scope.week_starts(DRIFT_WEEKS)
    library = list(
        session.scalars(
            select(ProjectPaperState.paper_id).where(
                ProjectPaperState.project_id == project.id, ProjectPaperState.state.in_(LIBRARY_STATES)
            )
        )
    )
    rows = session.execute(
        select(Interaction.paper_id, Interaction.action, Interaction.timestamp)
        .where(
            Interaction.project_id == project.id,
            Interaction.undone_at.is_(None),
            Interaction.action.in_(SWIPES),
            Interaction.timestamp >= scope.utc(min(scope.start, weeks[0])),
            Interaction.timestamp < scope.utc(scope.end + timedelta(days=1)),
        )
        .order_by(Interaction.timestamp, Interaction.id)
    ).all()
    decisions: dict[int, int] = {}  # latest swipe in the period wins
    weekly: dict[date, set[int]] = defaultdict(set)
    for paper_id, action, timestamp in rows:
        day = scope.local_day(timestamp)
        if scope.start <= day <= scope.end:
            decisions[paper_id] = int(action in POSITIVE)
        if action in POSITIVE:
            weekly[day - timedelta(days=day.weekday())].add(paper_id)

    ids = sorted(set(library) | set(decisions) | {pid for papers in weekly.values() for pid in papers})
    matrix, found = get_vectors(session, "paper", ids, model_id) if not work.empty else (np.zeros((0, 0)), [])
    scores = work.score(matrix)
    row = {pid: i for i, pid in enumerate(found)}

    closest_ids = sorted((pid for pid in library if pid in row), key=lambda pid: -scores.work(row[pid])[0])
    closest_ids = closest_ids[:CLOSEST]

    supporting: dict[int, int] = defaultdict(int)
    best_in_section: dict[int, tuple[float, int, int]] = {}  # section -> (cosine, paper, part)
    if scores.chunk is not None:
        for pid in library:
            if pid not in row:
                continue
            cosine, part = float(scores.chunk[row[pid]]), int(scores.chunk_row[row[pid]])  # type: ignore[index]
            if cosine < threshold:
                continue
            section_id = work.parts[part].section_id
            supporting[section_id] += 1
            if section_id not in best_in_section or cosine > best_in_section[section_id][0]:
                best_in_section[section_id] = (cosine, pid, part)

    summaries = paper_summaries(
        session, closest_ids + [b[1] for b in best_in_section.values()], user_id=scope.user_id
    )

    def work_paper(pid: int, similarity: float, part: int) -> WorkPaper | None:
        summary = summaries.get(pid)
        if summary is None:
            return None
        return WorkPaper(paper=summary, similarity=round(similarity, 4), match=work.match(part))

    closest = [p for p in (work_paper(pid, *scores.work(row[pid])) for pid in closest_ids) if p is not None]
    sections = []
    for section_id, document_id, filename, heading in _sections(session, project.id):
        best = best_in_section.get(section_id)
        sections.append(
            WorkSection(
                document_id=document_id,
                filename=filename,
                section_id=section_id,
                heading=heading,
                n_supporting=supporting.get(section_id, 0),
                best=work_paper(best[1], best[0], best[2]) if best else None,
            )
        )

    pairs = [(scores.work(row[pid])[0], y) for pid, y in decisions.items() if pid in row]
    drift = []
    for week in weeks:
        values = [
            float(scores.question[row[pid]])  # type: ignore[index]
            for pid in weekly.get(week, ())
            if pid in row and scores.question is not None
        ]
        drift.append(
            WorkDriftPoint(
                week=week, mean_similarity=round(float(np.mean(values)), 4) if values else None, n=len(values)
            )
        )
    has_documents = bool(
        session.scalar(
            select(func.count())
            .select_from(Document)
            .where(Document.project_id == project.id, Document.status == "ready")
        )
    )
    return WorkProject(
        project_id=project.id,
        name=project.name,
        has_documents=has_documents,
        threshold=threshold,
        closest=closest,
        quartiles=quartiles(pairs),
        correlation=correlation(pairs),
        sections=sections,
        drift=drift,
    )


def work(session: Session, scope: Scope) -> DashboardWork:
    emb = get_embedder()
    projects = session.scalars(
        select(Project).where(Project.id.in_(scope.project_ids or [-1])).order_by(Project.id)
    ).all()
    return DashboardWork(
        period=scope.period(),
        projects=[project_work(session, scope, p, emb.model_id, emb.similarity_threshold) for p in projects],
    )


__all__ = [
    "Work",
    "correlation",
    "load_work",
    "project_work",
    "quartiles",
    "strength",
    "work",
    "work_similarity",
]
