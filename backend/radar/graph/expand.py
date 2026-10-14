"""Job ``expand``: load a paper's citation neighbourhood (SPEC §12). Owner: graph.

1. Skip when expanded within 7 days (unless ``force``).
2. Semantic Scholar references + citations (≤100 each, with citation contexts/intents) when S2 knows
   the paper and is reachable; otherwise OpenAlex: the work's ``referenced_works`` (looked up in
   batches of 50) and its newest citing works (one list call).
3. Neighbours are upserted as ``stub`` papers (an existing paper keeps its status) with verified CITES
   edges (citing → cited), written in short batches.
4. New neighbours get local vectors; the paper gets fresh SEMANTICALLY_SIMILAR edges.
5. ``paper_expansions`` records the outcome (``done`` / ``failed`` with a message).
Never recursive: neighbours are not expanded themselves.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from radar.connectors import openalex, s2_graph
from radar.embeddings.registry import get_embedder
from radar.embeddings.vectors import ensure_paper_vectors
from radar.graph.knn import link_similar
from radar.models import Paper, PaperExpansion, PaperIdentifier, utcnow
from radar.net.http import BudgetExceeded, SourceUnavailable
from radar.store.graph import upsert_edge
from radar.store.papers import PaperRecord, upsert_paper

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

LIMIT = 100
RECENT = timedelta(days=7)
WRITE_BATCH = 50


def _identifiers(ctx: JobContext, paper_id: int) -> dict[str, str]:
    with ctx.session(write=False) as session:
        if session.get(Paper, paper_id) is None:
            raise LookupError(f"paper {paper_id} not found")
        rows = session.execute(
            select(PaperIdentifier.scheme, PaperIdentifier.value).where(
                PaperIdentifier.paper_id == paper_id, PaperIdentifier.is_primary.is_(True)
            )
        )
        return dict(rows.all())


def s2_key(identifiers: dict[str, str]) -> str | None:
    if identifiers.get("s2"):
        return identifiers["s2"]
    if identifiers.get("arxiv"):
        return f"ARXIV:{identifiers['arxiv']}"
    if identifiers.get("doi"):
        return f"DOI:{identifiers['doi']}"
    return None


def openalex_key(identifiers: dict[str, str]) -> str | None:
    if identifiers.get("openalex"):
        return identifiers["openalex"]
    if identifiers.get("doi"):
        return f"doi:{identifiers['doi']}"
    if identifiers.get("arxiv"):
        return f"arxiv:{identifiers['arxiv']}"
    return None


def _set_status(ctx: JobContext, paper_id: int, **values: Any) -> None:
    with ctx.session(write=True) as session:
        row = session.scalar(select(PaperExpansion).where(PaperExpansion.paper_id == paper_id))
        if row is None:
            row = PaperExpansion(paper_id=paper_id, status="none", sources_json={})
            session.add(row)
        for key, value in values.items():
            setattr(row, key, value)


def _stub(record: PaperRecord) -> PaperRecord:
    record.status = "stub"  # an existing active paper stays active (stub never downgrades)
    return record


def _store_links(
    ctx: JobContext,
    paper_id: int,
    links: list[tuple[PaperRecord, dict[str, Any] | None, bool]],
    provenance: str,
) -> list[int]:
    """Upsert neighbours and CITES edges in short batches; ``outgoing`` = the paper cites the neighbour."""
    neighbour_ids: list[int] = []
    for start in range(0, len(links), WRITE_BATCH):
        with ctx.session(write=True) as session:
            for record, evidence, outgoing in links[start : start + WRITE_BATCH]:
                other = upsert_paper(session, record).paper_id
                if other == paper_id:
                    continue
                src, dst = (paper_id, other) if outgoing else (other, paper_id)
                upsert_edge(
                    session,
                    src=("work", src),
                    relation="CITES",
                    dst=("work", dst),
                    provenance=provenance,
                    verified=True,
                    evidence=evidence or {"contexts": [], "intents": [], "is_influential": None},
                )
                neighbour_ids.append(other)
    return list(dict.fromkeys(neighbour_ids))


def _from_s2(ctx: JobContext, key: str) -> tuple[list[tuple[PaperRecord, dict[str, Any], bool]], bool]:
    """``(links, known)``; raises SourceUnavailable when S2 cannot be reached."""
    references = s2_graph.get_references(key, LIMIT, job_id=ctx.job_id, project_id=ctx.project_id)
    citations = s2_graph.get_citations(key, LIMIT, job_id=ctx.job_id, project_id=ctx.project_id)
    if references is None and citations is None:
        return [], False
    links: list[tuple[PaperRecord, dict[str, Any], bool]] = []
    for group, outgoing in ((references or [], True), (citations or [], False)):
        for link in group:
            evidence = {
                "contexts": link.contexts,
                "intents": link.intents,
                "is_influential": link.is_influential,
            }
            links.append((_stub(link.record), evidence, outgoing))
    return links, True


def _from_openalex(ctx: JobContext, key: str) -> tuple[list[tuple[PaperRecord, None, bool]], str | None]:
    work = openalex.get_work(key, job_id=ctx.job_id, project_id=ctx.project_id)
    if work is None:
        return [], None
    references = openalex.get_works_by_ids(
        work.referenced_works[:LIMIT], job_id=ctx.job_id, project_id=ctx.project_id
    )
    citing = openalex.get_citing_works(work.openalex_id, LIMIT, job_id=ctx.job_id, project_id=ctx.project_id)
    links = [(_stub(w.to_record()), None, True) for w in references]
    links += [(_stub(w.to_record()), None, False) for w in citing]
    return links, work.openalex_id


def run_expand(ctx: JobContext, paper_id: int, force: bool = False, auto: bool = False) -> dict[str, Any]:
    """References + citations (S2, else OpenAlex), stubs, CITES edges, embeddings, kNN edges."""
    with ctx.session(write=False) as session:
        last = session.scalar(
            select(PaperExpansion.last_expanded_at).where(PaperExpansion.paper_id == paper_id)
        )
    if not force and last is not None and last > utcnow() - RECENT:
        return {"skipped": "expanded in the last 7 days"}
    identifiers = _identifiers(ctx, paper_id)
    _set_status(ctx, paper_id, status="running", job_id=ctx.job_id, message=None)
    stats: dict[str, Any] = {"auto": auto, "source": None, "neighbours": 0}
    message: str | None = None
    try:
        links: list[tuple[PaperRecord, dict[str, Any] | None, bool]] = []
        key = s2_key(identifiers)
        if key is not None:
            ctx.progress(0.1, "Asking Semantic Scholar for references and citations")
            try:
                s2_links, known = _from_s2(ctx, key)
                if known:
                    links, stats["source"] = list(s2_links), "s2"
            except SourceUnavailable:
                message = "Semantic Scholar was unavailable; citations come from OpenAlex"
        if stats["source"] is None:
            oa_key = openalex_key(identifiers)
            if oa_key is not None:
                ctx.progress(0.3, "Asking OpenAlex for references and citations")
                oa_links, oa_id = _from_openalex(ctx, oa_key)
                if oa_id is not None:
                    links, stats["source"] = list(oa_links), "openalex"
        if stats["source"] is None:
            message = "Neither Semantic Scholar nor OpenAlex knows this paper yet"
        ctx.progress(0.6, f"Linking {len(links)} citing and cited papers")
        neighbours = _store_links(ctx, paper_id, links, stats["source"] or "openalex") if links else []
        stats["neighbours"] = len(neighbours)
        stats["references"] = sum(1 for _, _, outgoing in links if outgoing)
        stats["citations"] = sum(1 for _, _, outgoing in links if not outgoing)
        ctx.progress(0.8, "Embedding new papers locally")
        model_id = get_embedder().model_id
        with ctx.session(write=True) as session:
            ensure_paper_vectors(session, [paper_id, *neighbours], model_id)
        with ctx.session(write=True) as session:
            stats["similar"] = link_similar(session, paper_id)
    except BudgetExceeded:
        _set_status(
            ctx, paper_id, status="failed", message="OpenAlex daily budget reached; try again tomorrow"
        )
        raise
    except Exception:
        _set_status(ctx, paper_id, status="failed", message="Expansion failed; try again later")
        raise
    _set_status(
        ctx,
        paper_id,
        status="done",
        last_expanded_at=utcnow(),
        message=message,
        sources_json={stats["source"] or "none": stats["neighbours"]},
    )
    ctx.progress(
        1.0,
        f"Linked {stats['references']} references, {stats['citations']} citing papers "
        f"and {stats['similar']} similar papers",
    )
    return stats


__all__ = ["LIMIT", "openalex_key", "run_expand", "s2_key"]
