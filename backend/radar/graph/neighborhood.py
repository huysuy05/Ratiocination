"""A paper's knowledge-graph neighbourhood (SPEC §12, API.md "Knowledge graph"). Owner: graph.

Read-only and local. Groups around the centre paper:

* ``references`` / ``citations`` - CITES edges (≤ 25 each; with a project, closest to its question
  first, else most cited first);
* ``similar`` - SEMANTICALLY_SIMILAR edges of the active embedding model (≤ 8, highest cosine first);
* ``topics`` - HAS_TOPIC classifications (≤ 5);
* ``authors`` - OpenAlex scholars linked by AUTHORED edges, else the byline names as ``author`` nodes;
* ``author_works`` - for analyzed scholars, their analyzed papers (≤ 5 each, most disruptive first).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.api.serializers import expansion_status, paper_summaries, scholar_summary
from radar.embeddings.registry import get_embedder
from radar.embeddings.vectors import get_vectors
from radar.models import Concept, Paper, PaperAuthor, ProjectSeed, Scholar
from radar.models import GraphEdge as EdgeRow
from radar.schemas import GraphEdge, GraphEdgeEvidence, GraphGroup, GraphNode, Neighborhood, PaperSummary
from radar.store.graph import MergedEdge, edges_for_works
from radar.store.states import get_states

LINKS = 25
SIMILAR = 8
TOPICS = 5
AUTHOR_WORKS = 5
RELATIONS = {"CITES", "SEMANTICALLY_SIMILAR", "HAS_TOPIC", "AUTHORED"}
_EVIDENCE: TypeAdapter[Any] = TypeAdapter(GraphEdgeEvidence)


def node_id(kind: str, ident: int | str) -> str:
    return f"{kind}:{ident}"


def edge_out(edge: MergedEdge) -> GraphEdge:
    """API edge from a merged store edge; evidence that does not fit the documented shape is dropped."""
    evidence = None
    if edge.evidence is not None:
        try:
            evidence = _EVIDENCE.validate_python({"relation": edge.relation, **edge.evidence})
        except ValidationError:
            evidence = None
    return GraphEdge(
        id=f"e:{edge.id}",
        source=node_id(edge.src_type, edge.src_id),
        target=node_id(edge.dst_type, edge.dst_id),
        relation=edge.relation,  # type: ignore[arg-type]
        verified=edge.verified,
        confidence=edge.confidence,
        provenance=edge.provenance,
        evidence=evidence,
    )


def work_nodes(
    session: Session,
    paper_ids: Iterable[int],
    *,
    owner_id: int | None,
    project_id: int | None,
    center: int | None = None,
    weights: dict[int, float] | None = None,
) -> dict[int, GraphNode]:
    ids = list(dict.fromkeys(paper_ids))
    summaries = paper_summaries(session, ids, user_id=owner_id)
    states = get_states(session, project_id, ids) if project_id is not None else {}
    seeds = (
        set(session.scalars(select(ProjectSeed.paper_id).where(ProjectSeed.project_id == project_id)))
        if project_id is not None
        else set()
    )
    nodes: dict[int, GraphNode] = {}
    for pid in ids:
        summary = summaries.get(pid)
        if summary is None:
            continue
        state = states.get(pid)
        nodes[pid] = GraphNode(
            id=node_id("work", pid),
            type="work",
            label=summary.title,
            paper=summary,
            state=state.state if state is not None else None,
            seed=pid in seeds,
            is_center=pid == center,
            weight=(weights or {}).get(
                pid, float(summary.cited_by_count) if summary.cited_by_count else None
            ),
        )
    return nodes


def _order(
    session: Session, paper_ids: list[int], summaries: dict[int, PaperSummary], project_id: int | None
) -> tuple[list[int], dict[int, float]]:
    """Closest to the project question first (when there is a question vector), else most cited."""
    cosines: dict[int, float] = {}
    if project_id is not None and paper_ids:
        model_id = get_embedder().model_id
        question, _ = get_vectors(session, "project", [project_id], model_id)
        if len(question):
            matrix, ids = get_vectors(session, "paper", paper_ids, model_id)
            if ids:
                cosines = dict(zip(ids, (matrix @ question[0]).tolist(), strict=True))

    def key(pid: int) -> tuple[int, float, int, int]:
        cited = summaries[pid].cited_by_count if pid in summaries else None
        if cosines:
            return (0 if pid in cosines else 1, -cosines.get(pid, 0.0), -(cited or 0), -pid)
        return (0, 0.0, -(cited or 0), -pid)

    return sorted(paper_ids, key=key), cosines


def build_neighborhood(
    session: Session, paper_id: int, *, project_id: int | None = None, owner_id: int | None = None
) -> Neighborhood:
    """The neighbourhood of ``paper_id`` (the caller checks that the paper exists and owns the project)."""
    model_id = get_embedder().model_id
    merged = edges_for_works(session, [paper_id], RELATIONS)
    references = [e for e in merged if e.relation == "CITES" and (e.src_type, e.src_id) == ("work", paper_id)]
    citations = [e for e in merged if e.relation == "CITES" and (e.dst_type, e.dst_id) == ("work", paper_id)]
    similar = sorted(
        (e for e in merged if e.relation == "SEMANTICALLY_SIMILAR" and e.model_version in (None, model_id)),
        key=lambda e: -(e.confidence or 0.0),
    )[:SIMILAR]
    topics = sorted(
        (e for e in merged if e.relation == "HAS_TOPIC" and e.src_id == paper_id),
        key=lambda e: (e.confidence is None, -(e.confidence or 0.0)),
    )[:TOPICS]
    authored = sorted(
        (e for e in merged if e.relation == "AUTHORED" and e.src_type == "scholar"),
        key=lambda e: ((e.evidence or {}).get("position") is None, (e.evidence or {}).get("position") or 0),
    )

    ref_ids = [e.dst_id for e in references]
    cite_ids = [e.src_id for e in citations]
    linked = list(dict.fromkeys(ref_ids + cite_ids))
    linked_summaries = paper_summaries(session, linked)
    ordered, cosines = _order(session, linked, linked_summaries, project_id)
    rank = {pid: i for i, pid in enumerate(ordered)}
    shown_refs = sorted(ref_ids, key=lambda pid: rank.get(pid, 0))[:LINKS]
    shown_cites = sorted(cite_ids, key=lambda pid: rank.get(pid, 0))[:LINKS]
    similar_ids = [e.dst_id if e.src_id == paper_id else e.src_id for e in similar]

    # authors: analyzed scholars and their papers, else the byline
    scholar_ids = [e.src_id for e in authored]
    scholars = {s.id: s for s in session.scalars(select(Scholar).where(Scholar.id.in_(scholar_ids or [-1])))}
    author_work_edges: list[MergedEdge] = []
    author_work_ids: list[int] = []
    if scholars:
        per_scholar = {
            sid: [e for e in scholar_edges(session, sid) if e.dst_id != paper_id] for sid in scholar_ids
        }
        candidates = sorted({e.dst_id for found in per_scholar.values() for e in found})
        cd = dict(
            session.execute(
                select(Paper.id, Paper.disruption_cd).where(Paper.id.in_(candidates or [-1]))
            ).all()
        )
        for scholar_id in scholar_ids:
            works = [e for e in per_scholar.get(scholar_id, []) if e.dst_id in cd]
            works.sort(key=lambda e: (cd.get(e.dst_id) is None, -(cd.get(e.dst_id) or 0.0), e.dst_id))
            author_work_edges.extend(works[:AUTHOR_WORKS])
            author_work_ids.extend(e.dst_id for e in works[:AUTHOR_WORKS])

    weights = dict(cosines)
    nodes = work_nodes(
        session,
        [paper_id, *shown_refs, *shown_cites, *similar_ids, *author_work_ids],
        owner_id=owner_id,
        project_id=project_id,
        center=paper_id,
        weights=weights,
    )
    if paper_id in nodes:
        nodes[paper_id] = nodes[paper_id].model_copy(update={"is_center": True})
    concept_labels = dict(
        session.execute(
            select(Concept.id, Concept.canonical_label).where(
                Concept.id.in_([e.dst_id for e in topics] or [-1])
            )
        ).all()
    )
    extra_nodes: list[GraphNode] = [
        GraphNode(
            id=node_id("concept", e.dst_id), type="concept", label=concept_labels.get(e.dst_id, "Topic")
        )
        for e in topics
    ]
    author_nodes: list[str] = []
    edges: list[GraphEdge] = []
    for e in authored:
        scholar = scholars.get(e.src_id)
        if scholar is None:
            continue
        extra_nodes.append(
            GraphNode(
                id=node_id("scholar", scholar.id),
                type="scholar",
                label=scholar.display_name,
                scholar=scholar_summary(scholar),
            )
        )
        author_nodes.append(node_id("scholar", scholar.id))
    if not scholars:
        for position, name in session.execute(
            select(PaperAuthor.position, PaperAuthor.raw_name)
            .where(PaperAuthor.paper_id == paper_id)
            .order_by(PaperAuthor.position)
        ).all():
            nid = node_id("author", f"{paper_id}:{position}")
            extra_nodes.append(GraphNode(id=nid, type="author", label=name))
            author_nodes.append(nid)
            edges.append(
                GraphEdge(
                    id=f"a:{paper_id}:{position}",
                    source=nid,
                    target=node_id("work", paper_id),
                    relation="AUTHORED",
                    verified=True,
                    confidence=None,
                    provenance="paper_authors",
                    evidence={"relation": "AUTHORED", "position": position},  # type: ignore[arg-type]
                )
            )

    shown = {node_id("work", pid) for pid in nodes} | {n.id for n in extra_nodes}
    for e in [*references, *citations, *similar, *topics, *authored, *author_work_edges]:
        out = edge_out(e)
        if out.source in shown and out.target in shown:
            edges.append(out)
    edges = list({e.id: e for e in edges}.values())

    groups = [
        GraphGroup(
            key="references",
            label="References",
            node_ids=[node_id("work", p) for p in shown_refs if p in nodes],
            count=len(ref_ids),
        ),
        GraphGroup(
            key="citations",
            label="Cited by",
            node_ids=[node_id("work", p) for p in shown_cites if p in nodes],
            count=len(cite_ids),
        ),
        GraphGroup(
            key="similar",
            label="Similar papers",
            node_ids=[node_id("work", p) for p in similar_ids if p in nodes],
            count=len(similar_ids),
        ),
        GraphGroup(
            key="topics",
            label="Topics",
            node_ids=[node_id("concept", e.dst_id) for e in topics],
            count=len(topics),
        ),
        GraphGroup(key="authors", label="Authors", node_ids=author_nodes, count=len(author_nodes)),
        GraphGroup(
            key="author_works",
            label="Authors' most disruptive papers",
            node_ids=[node_id("work", p) for p in dict.fromkeys(author_work_ids) if p in nodes],
            count=len(set(author_work_ids)),
        ),
    ]
    expansion = expansion_status(session, paper_id)
    notices: list[str] = []
    if not references and not citations:
        if expansion.status in ("none", "failed"):
            notices.append(
                "Citations have not been loaded yet. Expand this paper to see what it cites and who cites it."
            )
        elif expansion.status == "done":
            notices.append("No citation data is known for this paper yet.")
    if expansion.message:
        notices.append(expansion.message)
    if not scholars:
        notices.append("Run a breakthrough analysis to see each author's most disruptive papers.")
    return Neighborhood(
        center=node_id("work", paper_id),
        nodes=[*nodes.values(), *extra_nodes],
        edges=edges,
        groups=groups,
        expansion=expansion,
        notices=notices,
    )


def scholar_edges(session: Session, scholar_id: int) -> list[MergedEdge]:
    """The scholar's public AUTHORED edges (scholar -> work)."""
    rows = session.scalars(
        select(EdgeRow).where(
            EdgeRow.src_type == "scholar",
            EdgeRow.src_id == scholar_id,
            EdgeRow.relation == "AUTHORED",
            EdgeRow.owner_id == 0,
            EdgeRow.project_id == 0,
        )
    )
    return [
        MergedEdge(
            id=row.id,
            src_type=row.src_type,
            src_id=row.src_id,
            relation=row.relation,
            dst_type=row.dst_type,
            dst_id=row.dst_id,
            verified=row.verified,
            provenance=row.provenance,
            confidence=row.confidence,
            weight=row.weight,
            evidence=dict(row.evidence_json) if row.evidence_json else None,
            model_version=row.model_version,
            provenances=[row.provenance],
            edge_ids=[row.id],
        )
        for row in rows
    ]


__all__ = ["build_neighborhood", "edge_out", "node_id", "scholar_edges", "work_nodes"]
