"""Graph edges and concepts (SPEC §12). Owner: store.

Rules:

* ``CITES`` points citing -> cited; symmetric relations (``SAME_WORK_AS``, ``SEMANTICALLY_SIMILAR``)
  are stored with ``(src_type, src_id) < (dst_type, dst_id)`` (``src_id < dst_id`` for work pairs);
* ``HAS_TOPIC`` sets ``confidence = evidence.score``; ``SEMANTICALLY_SIMILAR`` defaults
  ``confidence`` to ``evidence.cosine``;
* evidence is stored in the documented shape per relation (SPEC §4): CITES
  ``{contexts, intents, is_influential}``, HAS_TOPIC ``{score, subfield, field}``,
  SEMANTICALLY_SIMILAR ``{model_id, cosine}``, SAME_WORK_AS ``{matched_on}``, AUTHORED ``{position}``;
* ``upsert_edge`` is idempotent: the identity is (src, relation, dst, provenance, owner, project); a
  repeated call updates weight/confidence/evidence/verified/model_version when they differ (``None``
  arguments never clear stored values) and ``updated_at`` moves only when something changed;
* readers (:func:`edges_for_works`) merge rows with the same (src, relation, dst) across provenances
  into one :class:`MergedEdge` (S2 evidence preferred).

Write helpers (``upsert_edge``, ``get_or_create_concept``) run inside a write session.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from radar.models import Concept, GraphEdge

log = logging.getLogger(__name__)

__all__ = [
    "NODE_TYPES",
    "ONTOLOGY_SOURCES",
    "PUBLIC_RELATIONS",
    "SYMMETRIC_RELATIONS",
    "MergedEdge",
    "TopicHit",
    "concept_external_id",
    "edges_for_works",
    "get_or_create_concept",
    "paper_topics",
    "upsert_edge",
]

NODE_TYPES = frozenset({"work", "concept", "scholar", "project", "document", "chunk"})
SYMMETRIC_RELATIONS = frozenset({"SAME_WORK_AS", "SEMANTICALLY_SIMILAR"})
PUBLIC_RELATIONS: tuple[str, ...] = ("CITES", "SAME_WORK_AS", "SEMANTICALLY_SIMILAR", "HAS_TOPIC", "AUTHORED")
ONTOLOGY_SOURCES: tuple[str, ...] = ("openalex_topic",)
# Preferred row when merging provenances: S2 first (richest CITES evidence), then OpenAlex, ...
_PROVENANCE_ORDER = {"s2": 0, "openalex": 1, "identifier": 2, "title_match": 3, "local/knn": 4}


@dataclass
class MergedEdge:
    """Rows with the same (src, relation, dst) merged across provenances (S2 evidence preferred)."""

    id: int  # db id of the preferred row
    src_type: str
    src_id: int
    relation: str
    dst_type: str
    dst_id: int
    verified: bool
    provenance: str  # of the preferred row
    confidence: float | None = None
    weight: float | None = None
    evidence: dict[str, Any] | None = None
    model_version: str | None = None
    owner_id: int = 0
    project_id: int = 0
    provenances: list[str] = field(default_factory=list)
    edge_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class TopicHit:
    """One HAS_TOPIC classification of a work (see :func:`paper_topics`)."""

    concept_id: int
    label: str
    score: float | None


# ------------------------------------------------------------------------------------------------
# Edges
# ------------------------------------------------------------------------------------------------


def _node(value: tuple[str, int], role: str) -> tuple[str, int]:
    try:
        node_type, node_id = value
    except (TypeError, ValueError):
        raise ValueError(f"{role} must be a (type, id) tuple") from None
    node_type = str(node_type)
    if node_type not in NODE_TYPES:
        raise ValueError(f"unknown {role} node type {node_type!r}")
    return node_type, int(node_id)


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _shape_evidence(
    relation: str, evidence: dict[str, Any] | None, confidence: float | None, model_version: str | None
) -> dict[str, Any] | None:
    """Evidence in the documented shape for ``relation`` (SPEC §4.1); ``None`` when not derivable."""
    data = dict(evidence) if evidence else {}
    if relation == "CITES":
        influential = data.get("is_influential")
        data["contexts"] = [str(c) for c in data.get("contexts") or () if c is not None]
        data["intents"] = [str(i) for i in data.get("intents") or () if i is not None]
        data["is_influential"] = influential if isinstance(influential, bool) else None
        return data
    if relation == "HAS_TOPIC":
        data.setdefault("score", confidence)
        data.setdefault("subfield", None)
        data.setdefault("field", None)
        return data
    if relation == "SEMANTICALLY_SIMILAR":
        data.setdefault("model_id", model_version)
        data.setdefault("cosine", confidence)
        if data["model_id"] is None or data["cosine"] is None:
            return data if evidence else None
        return data
    if relation == "AUTHORED":
        position = data.get("position")
        data["position"] = position if isinstance(position, int) and not isinstance(position, bool) else None
        return data
    return data if evidence else None


def upsert_edge(
    session: Session,
    *,
    src: tuple[str, int],
    relation: str,
    dst: tuple[str, int],
    provenance: str,
    verified: bool,
    confidence: float | None = None,
    weight: float | None = None,
    evidence: dict[str, Any] | None = None,
    model_version: str | None = None,
    owner_id: int = 0,
    project_id: int = 0,
) -> GraphEdge:
    """Insert or update one edge (symmetric relations store src_id < dst_id). Write session.

    Raises ``ValueError`` for an unknown node type, an empty relation/provenance or a self-loop
    (``src == dst``; callers skip self-references).
    """
    src_type, src_id = _node(src, "src")
    dst_type, dst_id = _node(dst, "dst")
    relation = str(relation or "").strip()
    provenance = str(provenance or "").strip()
    if not relation or not provenance:
        raise ValueError("relation and provenance are required")
    if (src_type, src_id) == (dst_type, dst_id):
        raise ValueError("an edge needs two different endpoints")
    if relation in SYMMETRIC_RELATIONS and (src_type, src_id) > (dst_type, dst_id):
        src_type, src_id, dst_type, dst_id = dst_type, dst_id, src_type, src_id

    confidence = _float_or_none(confidence)
    weight = _float_or_none(weight)
    if relation == "HAS_TOPIC" and evidence and _float_or_none(evidence.get("score")) is not None:
        confidence = _float_or_none(evidence.get("score"))
    elif relation == "SEMANTICALLY_SIMILAR" and confidence is None and evidence:
        confidence = _float_or_none(evidence.get("cosine"))
    shaped = _shape_evidence(relation, evidence, confidence, model_version) if evidence is not None else None

    edge = session.scalar(
        select(GraphEdge).where(
            GraphEdge.src_type == src_type,
            GraphEdge.src_id == src_id,
            GraphEdge.relation == relation,
            GraphEdge.dst_type == dst_type,
            GraphEdge.dst_id == dst_id,
            GraphEdge.provenance == provenance,
            GraphEdge.owner_id == int(owner_id),
            GraphEdge.project_id == int(project_id),
        )
    )
    if edge is None:
        if shaped is None:
            shaped = _shape_evidence(relation, None, confidence, model_version)
        edge = GraphEdge(
            src_type=src_type,
            src_id=src_id,
            relation=relation,
            dst_type=dst_type,
            dst_id=dst_id,
            weight=weight,
            confidence=confidence,
            verified=bool(verified),
            provenance=provenance,
            evidence_json=shaped,
            model_version=model_version,
            owner_id=int(owner_id),
            project_id=int(project_id),
        )
        session.add(edge)
        session.flush()
        return edge

    if edge.verified != bool(verified):
        edge.verified = bool(verified)
    if weight is not None and edge.weight != weight:
        edge.weight = weight
    if confidence is not None and edge.confidence != confidence:
        edge.confidence = confidence
    if shaped is not None and edge.evidence_json != shaped:
        edge.evidence_json = shaped
    if model_version is not None and edge.model_version != model_version:
        edge.model_version = model_version
    return edge


def _preference(edge: GraphEdge) -> tuple[int, int, int, int]:
    return (
        0 if edge.provenance == "s2" else 1,
        0 if edge.verified else 1,
        _PROVENANCE_ORDER.get(edge.provenance, len(_PROVENANCE_ORDER)),
        edge.id,
    )


def edges_for_works(
    session: Session,
    work_ids: list[int],
    relations: set[str] | None = None,
    *,
    owner_id: int = 0,
    project_id: int = 0,
) -> list[MergedEdge]:
    """Merged edges touching ``work_ids``.

    Visible rows are public (owner 0, project 0) or belong to ``owner_id`` and ``project_id``; with
    ``project_id=0`` no project-scoped row is returned. Rows with the same (src, relation, dst) merge
    into one :class:`MergedEdge` whose preferred row is the S2 one when present (then verified rows,
    then OpenAlex, identifier, title_match, local/knn, others; lowest id last): ``id``, ``provenance``,
    ``owner_id``/``project_id`` come from it; ``evidence``/``confidence``/``weight``/``model_version``
    are the first non-null values in preference order; ``verified`` is true when any row is verified.
    Results are sorted by (relation, src, dst).
    """
    ids = sorted({int(i) for i in work_ids})
    if not ids or (relations is not None and not relations):
        return []
    query = select(GraphEdge).where(
        or_(
            and_(GraphEdge.src_type == "work", GraphEdge.src_id.in_(ids)),
            and_(GraphEdge.dst_type == "work", GraphEdge.dst_id.in_(ids)),
        ),
        GraphEdge.owner_id.in_(sorted({0, int(owner_id)})),
        GraphEdge.project_id.in_(sorted({0, int(project_id)})),
    )
    if relations is not None:
        query = query.where(GraphEdge.relation.in_(sorted(relations)))
    rows = list(session.scalars(query.order_by(GraphEdge.id)))
    if not rows:
        return []

    groups: dict[tuple[str, int, str, str, int], list[GraphEdge]] = defaultdict(list)
    for row in rows:
        groups[(row.src_type, row.src_id, row.relation, row.dst_type, row.dst_id)].append(row)

    merged: list[MergedEdge] = []
    for (src_type, src_id, relation, dst_type, dst_id), members in groups.items():
        members.sort(key=_preference)
        best = members[0]

        def first(attr: str, members: list[GraphEdge] = members) -> Any:
            return next((getattr(m, attr) for m in members if getattr(m, attr) is not None), None)

        provenances: list[str] = []
        for member in members:
            if member.provenance not in provenances:
                provenances.append(member.provenance)
        evidence = first("evidence_json")
        merged.append(
            MergedEdge(
                id=best.id,
                src_type=src_type,
                src_id=src_id,
                relation=relation,
                dst_type=dst_type,
                dst_id=dst_id,
                verified=any(m.verified for m in members),
                provenance=best.provenance,
                confidence=first("confidence"),
                weight=first("weight"),
                evidence=dict(evidence) if evidence is not None else None,
                model_version=first("model_version"),
                owner_id=best.owner_id,
                project_id=best.project_id,
                provenances=provenances,
                edge_ids=[m.id for m in members],
            )
        )
    merged.sort(key=lambda e: (e.relation, e.src_type, e.src_id, e.dst_type, e.dst_id))
    return merged


def paper_topics(session: Session, paper_ids: Iterable[int], *, limit: int = 5) -> dict[int, list[TopicHit]]:
    """HAS_TOPIC classifications per work in ONE query: public edges, highest confidence first (ties
    by concept id), at most ``limit`` per work; a concept reached through several rows keeps its best
    score. ``topics[0]`` is the paper's primary topic (SPEC §14.2)."""
    ids = sorted({int(i) for i in paper_ids})
    if not ids:
        return {}
    rows = session.execute(
        select(GraphEdge.src_id, Concept.id, Concept.canonical_label, GraphEdge.confidence)
        .join(Concept, Concept.id == GraphEdge.dst_id)
        .where(
            GraphEdge.src_type == "work",
            GraphEdge.src_id.in_(ids),
            GraphEdge.relation == "HAS_TOPIC",
            GraphEdge.dst_type == "concept",
            GraphEdge.owner_id == 0,
            GraphEdge.project_id == 0,
        )
    )
    best: dict[int, dict[int, TopicHit]] = defaultdict(dict)
    for paper_id, concept_id, label, score in rows:
        current = best[paper_id].get(concept_id)
        if current is None or (score is not None and (current.score is None or score > current.score)):
            best[paper_id][concept_id] = TopicHit(concept_id=concept_id, label=label, score=score)
    out: dict[int, list[TopicHit]] = {}
    for paper_id, hits in best.items():
        ordered = sorted(hits.values(), key=lambda h: (h.score is None, -(h.score or 0.0), h.concept_id))
        out[paper_id] = ordered[: max(0, limit)]
    return out


# ------------------------------------------------------------------------------------------------
# Concepts
# ------------------------------------------------------------------------------------------------

_OPENALEX_TOPIC_RE = re.compile(r"(?:(?:https?://)?(?:api\.)?openalex\.org/(?:topics/)?)?[Tt](\d+)/?")


def concept_external_id(ontology_source: str, external_id: str | None, label: str | None = None) -> str:
    """Normalized ``external_id``: OpenAlex ``T\\d+`` (URL forms accepted).

    Raises ``ValueError`` for an unknown ontology source or an unusable id.
    """
    if ontology_source == "openalex_topic":
        match = _OPENALEX_TOPIC_RE.fullmatch(str(external_id or "").strip())
        if match is None:
            raise ValueError("OpenAlex topic ids look like T12345")
        return f"T{match.group(1)}"
    raise ValueError(f"unknown ontology source {ontology_source!r}")


def get_or_create_concept(
    session: Session, ontology_source: str, external_id: str, label: str, extra: dict[str, Any] | None = None
) -> Concept:
    """The concept ``(ontology_source, external_id)``, created when missing. Write session.

    ``external_id`` is normalized by :func:`concept_external_id`. An existing concept keeps its label
    (an empty one is filled) and gains ``extra`` keys it does not have yet (existing keys win).
    """
    ext = concept_external_id(ontology_source, external_id, label)
    clean_label = " ".join(str(label or "").split())
    concept = session.scalar(
        select(Concept).where(Concept.ontology_source == ontology_source, Concept.external_id == ext)
    )
    if concept is None:
        concept = Concept(
            canonical_label=clean_label or ext,
            ontology_source=ontology_source,
            external_id=ext,
            extra_json=dict(extra or {}),
        )
        session.add(concept)
        session.flush()
        return concept
    if not concept.canonical_label and clean_label:
        concept.canonical_label = clean_label
    if extra:
        merged = {**dict(extra), **dict(concept.extra_json or {})}
        if merged != (concept.extra_json or {}):
            concept.extra_json = merged
    return concept
