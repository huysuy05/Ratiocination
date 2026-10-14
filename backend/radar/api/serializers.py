"""Derived API fields, implemented once (SPEC §4, API.md). Owner: store.

* :func:`paper_summaries` builds ``PaperSummary`` objects for any number of papers in at most six
  SELECTs (papers, identifiers, authors, versions, topics, the user's read marks);
* ``latest_version``: the arXiv ``vN`` label with the highest N, else ``published``, else null;
* ``identifiers.<scheme>``: the primary value per scheme (arXiv DOIs are stored only as ``arxiv``);
* ``links``: ``abs`` = ``https://arxiv.org/abs/<arxiv>`` else the latest landing_url; ``pdf`` =
  ``https://arxiv.org/pdf/<arxiv><latest vN>`` for arXiv papers, else the latest version's pdf_url,
  else ``best_oa_pdf``; ``doi`` = ``https://doi.org/<doi>``; ``oa`` = ``oa_url``. "Latest" orders
  versions by ``coalesce(source_updated_at, observed_at)`` (then id);
* ``topics``: HAS_TOPIC edges by confidence desc, at most 5; ``topics[0]`` is the primary topic;
* recommendation items keep the six features and validate stored reasons against the discriminated
  ``Reason`` union (an invalid stored reason is skipped, never a 500);
* :func:`disruption_detail` renders the stored CD counts with a label and a plain-language split.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import models
from radar.errors import ApiError
from radar.schemas import (
    FEATURE_NAMES,
    Author,
    DisruptionDetail,
    ExpansionStatus,
    FeatureScore,
    FeatureScores,
    Job,
    PaperCounts,
    PaperDetail,
    PaperIdentifiers,
    PaperLinks,
    PaperSummary,
    ProjectSettings,
    ProvenanceEntry,
    Reason,
    RecommendationItem,
    ScholarSummary,
    Slate,
    TopicRef,
)
from radar.store.graph import TopicHit, edges_for_works, paper_topics
from radar.store.identifiers import version_number
from radar.store.papers import latest_version_label
from radar.store.states import get_states

log = logging.getLogger(__name__)

__all__ = [
    "disruption_detail",
    "disruption_label",
    "expansion_status",
    "job_out",
    "paper_detail",
    "paper_summaries",
    "project_settings",
    "recommendation_items",
    "scholar_summary",
    "slate_out",
]

_PAPER_STATUSES = ("active", "stub", "retracted", "withdrawn")
_EXPANSION_STATUSES = ("none", "queued", "running", "done", "failed")
_DOI_SAFE = "/:@!$&'()*+,;=-._~"
_REASON_ADAPTER: TypeAdapter[Any] = TypeAdapter(Reason)
_FEATURE_DEFAULTS: dict[str, Any] = {
    "value": None,
    "raw": None,
    "weight": 0.0,
    "contribution": 0.0,
    "available": False,
}
DISRUPTIVE_SHARE = 0.55
"""A paper is labeled disruptive when at least this share of its citers ignore its references
(n_i / (n_i + n_j)), consolidating at or below 1 − this share, mixed in between (API.md)."""


# ------------------------------------------------------------------------------------------------
# Paper summaries
# ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _VersionRow:
    id: int
    paper_id: int
    version_label: str
    landing_url: str | None
    pdf_url: str | None
    observed_at: datetime
    source_updated_at: datetime | None

    @property
    def sort_key(self) -> tuple[datetime, int]:
        return (self.source_updated_at or self.observed_at, self.id)


@dataclass
class _Loaded:
    papers: dict[int, models.Paper]
    summaries: dict[int, PaperSummary]


def _doi_url(doi: str) -> str:
    return f"https://doi.org/{quote(doi, safe=_DOI_SAFE)}"


def _build_summary(
    paper: models.Paper,
    identifiers: Mapping[str, str],
    authors: list[Author],
    versions: Sequence[_VersionRow],
    topics: Sequence[TopicHit],
    read_at: datetime | None,
) -> PaperSummary:
    doi = identifiers.get("doi")
    arxiv = identifiers.get("arxiv")
    latest = latest_version_label(v.version_label for v in versions)
    newest_first = list(reversed(versions))
    landing = next((v.landing_url for v in newest_first if v.landing_url), None)
    if arxiv:
        suffix = latest if version_number(latest) is not None else ""
        abs_link: str | None = f"https://arxiv.org/abs/{arxiv}"
        pdf_link: str | None = f"https://arxiv.org/pdf/{arxiv}{suffix}"
    else:
        abs_link = landing or (
            f"https://openalex.org/{identifiers['openalex']}" if identifiers.get("openalex") else None
        )
        pdf_link = next((v.pdf_url for v in newest_first if v.pdf_url), None) or paper.best_oa_pdf
    return PaperSummary(
        id=paper.id,
        title=paper.canonical_title,
        authors=authors,
        abstract=paper.abstract,
        publication_date=paper.publication_date,
        year=paper.year,
        primary_category=paper.primary_category,
        categories=[str(c) for c in paper.categories_json or ()],
        venue=paper.venue,
        identifiers=PaperIdentifiers(
            doi=doi, arxiv=arxiv, openalex=identifiers.get("openalex"), s2=identifiers.get("s2")
        ),
        latest_version=latest,
        links=PaperLinks(abs=abs_link, pdf=pdf_link, doi=_doi_url(doi) if doi else None, oa=paper.oa_url),
        topics=[TopicRef(concept_id=t.concept_id, label=t.label, score=t.score) for t in topics],
        status=paper.status if paper.status in _PAPER_STATUSES else "active",
        is_oa=paper.is_oa,
        cited_by_count=paper.cited_by_count,
        cd=paper.disruption_cd,
        read_at=read_at,
    )


def _load(session: Session, paper_ids: Iterable[int], user_id: int | None) -> _Loaded:
    """Papers and summaries for ``paper_ids`` in at most six SELECTs (none for an empty list)."""
    ids = list(dict.fromkeys(int(i) for i in paper_ids))
    if not ids:
        return _Loaded({}, {})
    papers = {p.id: p for p in session.scalars(select(models.Paper).where(models.Paper.id.in_(ids)))}
    if not papers:
        return _Loaded({}, {})
    found = sorted(papers)

    pi = models.PaperIdentifier
    primary: dict[int, dict[str, str]] = defaultdict(dict)
    first_seen: dict[int, dict[str, str]] = defaultdict(dict)
    for paper_id, scheme, value, is_primary in session.execute(
        select(pi.paper_id, pi.scheme, pi.value, pi.is_primary)
        .where(pi.paper_id.in_(found))
        .order_by(pi.paper_id, pi.id)
    ):
        first_seen[paper_id].setdefault(scheme, value)
        if is_primary:
            primary[paper_id].setdefault(scheme, value)
    identifiers = {pid: {**first_seen.get(pid, {}), **primary.get(pid, {})} for pid in found}

    pa = models.PaperAuthor
    authors: dict[int, list[Author]] = defaultdict(list)
    for paper_id, position, raw_name, openalex_id, display_name in session.execute(
        select(pa.paper_id, pa.position, pa.raw_name, pa.openalex_author_id, models.Author.display_name)
        .join(models.Author, models.Author.id == pa.author_id)
        .where(pa.paper_id.in_(found))
        .order_by(pa.paper_id, pa.position)
    ):
        authors[paper_id].append(
            Author(name=display_name or raw_name, position=position, openalex_id=openalex_id)
        )

    pv = models.PaperVersion
    versions: dict[int, list[_VersionRow]] = defaultdict(list)
    for row in session.execute(
        select(
            pv.id,
            pv.paper_id,
            pv.version_label,
            pv.landing_url,
            pv.pdf_url,
            pv.observed_at,
            pv.source_updated_at,
        ).where(pv.paper_id.in_(found))
    ):
        versions[row.paper_id].append(_VersionRow(*row))
    for rows in versions.values():
        rows.sort(key=lambda v: v.sort_key)

    topics = paper_topics(session, found)
    reads: dict[int, datetime] = {}
    if user_id is not None:
        pr = models.PaperRead
        reads = dict(
            session.execute(
                select(pr.paper_id, pr.read_at).where(pr.user_id == user_id, pr.paper_id.in_(found))
            ).all()
        )

    summaries = {
        pid: _build_summary(
            papers[pid],
            identifiers.get(pid, {}),
            authors.get(pid, []),
            versions.get(pid, []),
            topics.get(pid, []),
            reads.get(pid),
        )
        for pid in found
    }
    return _Loaded(papers=papers, summaries=summaries)


def paper_summaries(
    session: Session, paper_ids: list[int], *, user_id: int | None = None
) -> dict[int, PaperSummary]:
    """PaperSummary by id for many papers in at most 6 queries; ``read_at`` is ``user_id``'s mark.

    Unknown ids are omitted. Works in read sessions.
    """
    return _load(session, paper_ids, user_id).summaries


# ------------------------------------------------------------------------------------------------
# Projects
# ------------------------------------------------------------------------------------------------


def project_keywords(project: models.Project) -> list[str]:
    """The project's keywords; a stored value that is not a list of strings reads as no keywords."""
    stored = project.keywords_json
    if not isinstance(stored, list):
        return []
    return [k for k in stored if isinstance(k, str) and k.strip()]


def project_settings(project: models.Project) -> ProjectSettings:
    """The project's validated settings; invalid stored keys fall back to their defaults."""
    stored = dict(project.settings_json or {})
    try:
        return ProjectSettings.from_stored(stored)
    except ValidationError:
        merged = ProjectSettings().model_dump()
        for key, value in stored.items():
            if key not in merged:
                continue
            candidate = {**merged, key: value}
            try:
                ProjectSettings.model_validate(candidate)
            except ValidationError:
                continue
            merged = candidate
        return ProjectSettings.model_validate(merged)


# ------------------------------------------------------------------------------------------------
# Recommendation items and slates
# ------------------------------------------------------------------------------------------------


def _features(raw: Any) -> FeatureScores:
    data = raw if isinstance(raw, dict) else {}
    values: dict[str, FeatureScore] = {}
    for name in FEATURE_NAMES:
        entry = data.get(name)
        score: FeatureScore | None = None
        if isinstance(entry, dict):
            try:
                score = FeatureScore.model_validate({**_FEATURE_DEFAULTS, **entry})
            except ValidationError:
                score = None
        values[name] = score if score is not None else FeatureScore.model_validate(_FEATURE_DEFAULTS)
    return FeatureScores(**values)


def _reasons(raw: Any, slate_id: int, paper_id: int) -> list[Any]:
    out: list[Any] = []
    for entry in raw if isinstance(raw, list) else ():
        try:
            out.append(_REASON_ADAPTER.validate_python(entry))
        except ValidationError:
            log.warning("slate %s paper %s: skipped an invalid stored reason", slate_id, paper_id)
    return out


def _items_out(
    session: Session,
    slate: models.RecommendationSlate,
    items: Sequence[models.RecommendationItem],
    project: models.Project,
    summaries: Mapping[int, PaperSummary] | None = None,
) -> list[RecommendationItem]:
    paper_ids = [item.paper_id for item in items]
    if summaries is None:
        summaries = paper_summaries(session, paper_ids, user_id=project.owner_id)
    states = get_states(session, project.id, paper_ids)
    out: list[RecommendationItem] = []
    for item in items:
        summary = summaries.get(item.paper_id)
        if summary is None:
            log.warning("slate %s: paper %s no longer exists; item skipped", slate.id, item.paper_id)
            continue
        state_row = states.get(item.paper_id)
        out.append(
            RecommendationItem(
                slate_id=slate.id,
                paper=summary,
                rank=item.rank,
                score=item.score,
                features=_features(item.feature_scores_json),
                reasons=_reasons(item.explanation_json, slate.id, item.paper_id),
                state=state_row.state if state_row is not None else None,
                last_interaction_id=state_row.last_interaction_id if state_row is not None else None,
            )
        )
    return out


def _slate_and_project(
    session: Session, slate_id: int, project_id: int
) -> tuple[models.RecommendationSlate, models.Project]:
    slate = session.get(models.RecommendationSlate, slate_id)
    project = session.get(models.Project, project_id)
    if slate is None or project is None or slate.project_id != project_id:
        raise ApiError(404, "not_found", "Slate not found")
    return slate, project


def _slate_items(session: Session, slate_id: int) -> list[models.RecommendationItem]:
    ri = models.RecommendationItem
    return list(session.scalars(select(ri).where(ri.slate_id == slate_id).order_by(ri.rank, ri.id)))


def recommendation_items(session: Session, slate_id: int, project_id: int) -> list[RecommendationItem]:
    """Items of a slate ordered by rank, each with its current project state.

    Raises ``ApiError`` 404 when the slate does not exist or belongs to another project.
    """
    slate, project = _slate_and_project(session, slate_id, project_id)
    return _items_out(session, slate, _slate_items(session, slate.id), project)


def slate_out(session: Session, slate_id: int, project_id: int) -> Slate:
    """API ``Slate`` (items with current states, ``remaining``)."""
    slate, project = _slate_and_project(session, slate_id, project_id)
    items = _items_out(session, slate, _slate_items(session, slate.id), project)
    return Slate(
        id=slate.id,
        project_id=slate.project_id,
        generated_at=slate.generated_at,
        mode=slate.mode if slate.mode in ("daily", "more") else "daily",
        ranker_version=slate.ranker_version,
        embedding_model=slate.embedding_model,
        pool_size=slate.pool_size,
        items=items,
        remaining=sum(1 for item in items if item.state is None),
        message=slate.message,
    )


# ------------------------------------------------------------------------------------------------
# Jobs, expansions, scholars, disruption
# ------------------------------------------------------------------------------------------------


def job_out(job: models.Job, *, hide_project: bool = False) -> Job:
    """API ``Job`` from a ``jobs`` row; ``hide_project`` blanks another user's project id."""
    return Job(
        id=job.id,
        kind=job.kind,
        status=job.status,
        project_id=None if hide_project else job.project_id,
        params=dict(job.params_json or {}),
        stats=dict(job.stats_json or {}),
        progress=job.progress,
        message=job.message,
        error_code=job.error_code,
        error_message=job.error_message,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


def expansion_status(session: Session, paper_id: int) -> ExpansionStatus:
    """``paper_expansions`` row as ``ExpansionStatus`` (status ``none`` when absent).

    A row still ``queued``/``running`` whose job failed (e.g. interrupted by a restart) or no longer
    exists is reported as ``failed`` with the job's error message (read-side reconciliation; nothing
    is written).
    """
    row = session.scalar(select(models.PaperExpansion).where(models.PaperExpansion.paper_id == paper_id))
    if row is None:
        return ExpansionStatus(status="none", job_id=None, last_expanded_at=None, message=None)
    status = row.status if row.status in _EXPANSION_STATUSES else "none"
    message = row.message
    if status in ("queued", "running") and row.job_id is not None:
        job = session.get(models.Job, row.job_id)
        if job is None or job.status == "failed":
            status = "failed"
            message = (job.error_message if job is not None else None) or "Expansion was interrupted"
    return ExpansionStatus(
        status=status, job_id=row.job_id, last_expanded_at=row.last_expanded_at, message=message
    )


def scholar_summary(scholar: models.Scholar) -> ScholarSummary:
    return ScholarSummary(
        id=scholar.id,
        openalex_id=scholar.openalex_id,
        display_name=scholar.display_name,
        orcid=scholar.orcid,
        works_count=scholar.works_count,
        cited_by_count=scholar.cited_by_count,
        h_index=scholar.h_index,
        institution=scholar.last_known_institution,
        analyzed_at=scholar.analyzed_at,
    )


def disruption_label(cd: float | None, n_i: int, n_j: int) -> str:
    """``disruptive`` / ``consolidating`` / ``mixed`` from the citer split; ``undefined`` without CD.

    The share n_i / (n_i + n_j) has the same sign as CD around 0.5 (CD > 0 iff n_i > n_j), so the label
    never contradicts the index; the dead zone keeps 51/49 splits from being called breakthroughs.
    """
    if cd is None or n_i + n_j <= 0:
        return "undefined"
    share = n_i / (n_i + n_j)
    if cd > 0 and share >= DISRUPTIVE_SHARE:
        return "disruptive"
    if cd < 0 and share <= 1 - DISRUPTIVE_SHARE:
        return "consolidating"
    return "mixed"


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}" + ("" if n == 1 else "s")


def disruption_detail(paper: models.Paper) -> DisruptionDetail | None:
    """The stored CD analysis of ``paper`` (``None`` when it was never computed)."""
    if paper.disruption_at is None:
        return None
    n_i, n_j, n_k = (paper.disruption_ni or 0), (paper.disruption_nj or 0), (paper.disruption_nk or 0)
    n_citing = n_i + n_j
    cd = paper.disruption_cd
    label = disruption_label(cd, n_i, n_j)
    if cd is None:
        if (paper.disruption_refs or 0) == 0:
            text = "No reference list is known for this paper, so its disruption cannot be measured."
        else:
            text = "No paper cites this one yet, so its disruption cannot be measured."
    else:
        share = round(100 * n_i / n_citing) if n_citing else 0
        text = (
            f"Of {_plural(n_citing, 'citing paper')}, {n_i:,} ({share}%) cite it without citing its "
            f"references and {n_j:,} cite both; {n_k:,} other papers cite its references but not it."
        )
    return DisruptionDetail(
        cd=cd,
        n_citing=n_citing,
        n_i=n_i,
        n_j=n_j,
        n_k=n_k,
        refs_used=paper.disruption_refs or 0,
        refs_total=paper.disruption_refs_total,
        computed_at=paper.disruption_at,
        label=label,
        text=text,
    )


# ------------------------------------------------------------------------------------------------
# Paper detail
# ------------------------------------------------------------------------------------------------


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _provenance(raw: Any) -> dict[str, ProvenanceEntry]:
    out: dict[str, ProvenanceEntry] = {}
    for name, entry in raw.items() if isinstance(raw, dict) else ():
        if not isinstance(entry, dict) or not entry.get("source"):
            continue
        observed = _parse_datetime(entry.get("observed_at"))
        if observed is None:
            continue
        out[str(name)] = ProvenanceEntry(source=str(entry["source"]), observed_at=observed)
    return out


def _counts(session: Session, paper_id: int, expanded: bool) -> PaperCounts:
    edges = edges_for_works(session, [paper_id], {"CITES"})
    references = {(e.dst_type, e.dst_id) for e in edges if (e.src_type, e.src_id) == ("work", paper_id)}
    citations = {(e.src_type, e.src_id) for e in edges if (e.dst_type, e.dst_id) == ("work", paper_id)}
    if not edges and not expanded:
        return PaperCounts(references=None, citations=None)
    return PaperCounts(references=len(references), citations=len(citations))


def _latest_recommendation(
    session: Session, project: models.Project, paper_id: int, summary: PaperSummary
) -> RecommendationItem | None:
    ri, rs = models.RecommendationItem, models.RecommendationSlate
    row = session.execute(
        select(ri, rs)
        .join(rs, rs.id == ri.slate_id)
        .where(rs.project_id == project.id, ri.paper_id == paper_id)
        .order_by(rs.generated_at.desc(), rs.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    item, slate = row
    out = _items_out(session, slate, [item], project, summaries={paper_id: summary})
    return out[0] if out else None


def paper_detail(
    session: Session, paper_id: int, project_id: int | None, *, user_id: int | None = None
) -> PaperDetail:
    """PaperDetail; project fields (state, recommendation) only with ``project_id``.

    The caller checks that ``project_id`` belongs to the requesting user. Raises ``ApiError`` 404 for
    an unknown paper. ``counts.references/citations`` are distinct CITES pairs known locally (null
    when the paper was never expanded and has no CITES edges).
    """
    loaded = _load(session, [paper_id], user_id)
    summary = loaded.summaries.get(paper_id)
    if summary is None:
        raise ApiError(404, "not_found", "Paper not found")
    paper = loaded.papers[paper_id]
    expansion = expansion_status(session, paper_id)
    expanded = expansion.last_expanded_at is not None or expansion.status == "done"

    state: str | None = None
    last_interaction_id: int | None = None
    recommendation: RecommendationItem | None = None
    if project_id is not None:
        state_row = get_states(session, project_id, [paper_id]).get(paper_id)
        if state_row is not None:
            state = state_row.state
            last_interaction_id = state_row.last_interaction_id
        project = session.get(models.Project, project_id)
        if project is not None:
            recommendation = _latest_recommendation(session, project, paper_id, summary)

    return PaperDetail(
        **summary.model_dump(),
        provenance=_provenance(paper.field_provenance_json),
        counts=_counts(session, paper_id, expanded),
        state=state,
        last_interaction_id=last_interaction_id,
        recommendation=recommendation,
        expansion=expansion,
        disruption=disruption_detail(paper),
    )
