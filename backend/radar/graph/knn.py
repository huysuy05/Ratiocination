"""SEMANTICALLY_SIMILAR edges from local nearest neighbours (SPEC §12). Owner: graph.

For one paper: its ``K`` nearest papers in the active model's ``VectorIndex`` whose cosine reaches the
model's ``similarity_threshold`` (bge-small 0.80, hashing 0.30) become unverified edges with provenance
``local/knn``, ``confidence`` = cosine and ``model_version`` = the model id. Edges are refreshed when
older than 7 days (or made by another model). Purely local: no network.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.embeddings.index import VectorIndex
from radar.embeddings.registry import get_embedder
from radar.embeddings.vectors import get_vectors
from radar.models import GraphEdge, utcnow
from radar.store.graph import upsert_edge

log = logging.getLogger(__name__)

K = 8
REFRESH_AFTER = timedelta(days=7)
PROVENANCE = "local/knn"


def needs_refresh(session: Session, paper_id: int, model_id: str) -> bool:
    """True when the paper has no current-model kNN edges younger than 7 days."""
    newest = session.scalar(
        select(GraphEdge.updated_at)
        .where(
            GraphEdge.relation == "SEMANTICALLY_SIMILAR",
            GraphEdge.provenance == PROVENANCE,
            GraphEdge.model_version == model_id,
            ((GraphEdge.src_type == "work") & (GraphEdge.src_id == paper_id))
            | ((GraphEdge.dst_type == "work") & (GraphEdge.dst_id == paper_id)),
        )
        .order_by(GraphEdge.updated_at.desc())
        .limit(1)
    )
    return newest is None or newest < utcnow() - REFRESH_AFTER


def link_similar(session: Session, paper_id: int, *, k: int = K) -> int:
    """Create/refresh the paper's similarity edges (write session; vectors must exist). Returns the
    number of edges written."""
    emb = get_embedder()
    matrix, ids = get_vectors(session, "paper", [paper_id], emb.model_id)
    if not ids:
        return 0
    hits = VectorIndex.get(emb.model_id).search(matrix[0], k, exclude_ids={paper_id})
    written = 0
    for other, cosine in hits:
        if cosine < emb.similarity_threshold:
            continue
        upsert_edge(
            session,
            src=("work", paper_id),
            relation="SEMANTICALLY_SIMILAR",
            dst=("work", other),
            provenance=PROVENANCE,
            verified=False,
            confidence=round(float(cosine), 6),
            model_version=emb.model_id,
            evidence={"model_id": emb.model_id, "cosine": round(float(cosine), 6)},
        )
        written += 1
    return written


__all__ = ["K", "PROVENANCE", "REFRESH_AFTER", "link_similar", "needs_refresh"]
