"""The project graph: what a project's papers have in common (SPEC §12). Owner: graph.

Works: the project's seeds, then relevant and saved papers (most recent first, capped). Around them:
CITES and SEMANTICALLY_SIMILAR edges among those works, the topics that at least two of them share
(else the 15 most frequent), and the authors who appear on at least two of them - as OpenAlex
scholars when known, else by name. Private to the project's owner; read-only and local.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.api.serializers import scholar_summary
from radar.graph.neighborhood import edge_out, node_id, work_nodes
from radar.models import Author, Concept, PaperAuthor, ProjectPaperState, ProjectSeed, Scholar
from radar.schemas import GraphEdge, GraphGroup, GraphNode, ProjectGraph
from radar.store.graph import edges_for_works

MAX_TOPICS = 15


def _name_key(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def build_project_graph(session: Session, project_id: int, owner_id: int, limit: int = 150) -> ProjectGraph:
    """The graph of ``project_id`` (the caller checks ownership)."""
    seeds = list(
        session.scalars(
            select(ProjectSeed.paper_id)
            .where(ProjectSeed.project_id == project_id)
            .order_by(ProjectSeed.created_at)
        )
    )
    liked = session.execute(
        select(ProjectPaperState.paper_id, ProjectPaperState.state)
        .where(ProjectPaperState.project_id == project_id, ProjectPaperState.state.in_(("relevant", "saved")))
        .order_by(ProjectPaperState.updated_at.desc())
    ).all()
    work_ids = list(dict.fromkeys([*seeds, *(pid for pid, _ in liked)]))[:limit]
    works = work_nodes(session, work_ids, owner_id=owner_id, project_id=project_id)
    ids = set(works)

    merged = edges_for_works(session, list(ids), {"CITES", "SEMANTICALLY_SIMILAR", "HAS_TOPIC"})
    edges: list[GraphEdge] = [
        edge_out(e)
        for e in merged
        if e.relation in ("CITES", "SEMANTICALLY_SIMILAR") and e.src_id in ids and e.dst_id in ids
    ]

    topic_works: dict[int, list] = defaultdict(list)
    for e in merged:
        if e.relation == "HAS_TOPIC" and e.src_id in ids:
            topic_works[e.dst_id].append(e)
    shared = [cid for cid, found in topic_works.items() if len(found) >= 2]
    if not shared:
        shared = sorted(topic_works, key=lambda cid: (-len(topic_works[cid]), cid))[:MAX_TOPICS]
    shared = sorted(shared, key=lambda cid: (-len(topic_works[cid]), cid))[:MAX_TOPICS]
    labels = dict(
        session.execute(
            select(Concept.id, Concept.canonical_label).where(Concept.id.in_(shared or [-1]))
        ).all()
    )
    topic_nodes = [
        GraphNode(id=node_id("concept", cid), type="concept", label=labels.get(cid, "Topic"))
        for cid in shared
    ]
    for cid in shared:
        edges.extend(edge_out(e) for e in topic_works[cid])

    # authors shared by at least two works (OpenAlex id when known, else the normalized name)
    rows = session.execute(
        select(
            PaperAuthor.paper_id, PaperAuthor.position, PaperAuthor.openalex_author_id, Author.display_name
        )
        .join(Author, Author.id == PaperAuthor.author_id)
        .where(PaperAuthor.paper_id.in_(list(ids) or [-1]))
    ).all()
    groups: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    for paper_id, position, openalex_id, name in rows:
        key = f"oa:{openalex_id}" if openalex_id else f"n:{_name_key(name)}"
        if key != "n:":
            groups[key].append((paper_id, position, name))
    known = {
        s.openalex_id: s
        for s in session.scalars(
            select(Scholar).where(
                Scholar.openalex_id.in_([k[3:] for k in groups if k.startswith("oa:")] or ["-"])
            )
        )
    }
    author_nodes: list[GraphNode] = []
    for key, found in sorted(groups.items(), key=lambda kv: (-len({p for p, _, _ in kv[1]}), kv[0])):
        papers = {p for p, _, _ in found}
        if len(papers) < 2:
            continue
        scholar = known.get(key[3:]) if key.startswith("oa:") else None
        nid = node_id("scholar", scholar.id) if scholar else node_id("author", key)
        author_nodes.append(
            GraphNode(
                id=nid,
                type="scholar" if scholar else "author",
                label=scholar.display_name if scholar else found[0][2],
                scholar=scholar_summary(scholar) if scholar else None,
                weight=float(len(papers)),
            )
        )
        for paper_id, position, _ in found:
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

    state_of = {pid: state for pid, state in liked}
    seed_set = set(seeds)
    graph_groups = [
        GraphGroup(
            key="seeds",
            label="Seeds",
            node_ids=[node_id("work", p) for p in works if p in seed_set],
            count=len(seed_set),
        ),
        GraphGroup(
            key="relevant",
            label="Relevant",
            node_ids=[
                node_id("work", p) for p in works if state_of.get(p) == "relevant" and p not in seed_set
            ],
            count=sum(1 for s in state_of.values() if s == "relevant"),
        ),
        GraphGroup(
            key="saved",
            label="Saved",
            node_ids=[node_id("work", p) for p in works if state_of.get(p) == "saved" and p not in seed_set],
            count=sum(1 for s in state_of.values() if s == "saved"),
        ),
        GraphGroup(
            key="topics", label="Shared topics", node_ids=[n.id for n in topic_nodes], count=len(topic_nodes)
        ),
        GraphGroup(
            key="authors",
            label="Shared authors",
            node_ids=[n.id for n in author_nodes],
            count=len(author_nodes),
        ),
    ]
    notices: list[str] = []
    if len(works) < 2:
        notices.append("Add seeds or mark papers relevant to grow this project's graph.")
    elif not any(e.relation == "CITES" for e in edges):
        notices.append("No citations link these papers yet. Expanding a paper loads its citations.")
    shown = {node_id("work", p) for p in works} | {n.id for n in topic_nodes} | {n.id for n in author_nodes}
    edges = list({e.id: e for e in edges if e.source in shown and e.target in shown}.values())
    return ProjectGraph(
        nodes=[*works.values(), *topic_nodes, *author_nodes],
        edges=edges,
        groups=graph_groups,
        notices=notices,
    )


__all__ = ["build_project_graph"]
