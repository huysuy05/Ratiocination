"""Job ``embed`` (SPEC §9, §15). Owner: enrich.

Computes local vectors (the default model, or the hashing fallback) for papers that lack one, newest
first, in chunks so progress is visible; ``ensure_paper_vectors`` embeds without holding the write
lock when given a fresh session.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import exists, select

from radar.embeddings.registry import fallback_active, get_embedder
from radar.embeddings.vectors import ensure_paper_vectors
from radar.models import Embedding, Paper

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

EMBED_CHUNK = 100


def _missing(model_id: str, paper_ids: list[int] | None) -> Any:
    has_vector = exists().where(
        Embedding.entity_type == "paper", Embedding.entity_id == Paper.id, Embedding.model_id == model_id
    )
    query = select(Paper.id).where(~has_vector, Paper.status.not_in(("retracted", "withdrawn")))
    if paper_ids is not None:
        query = query.where(Paper.id.in_(paper_ids))
    return query.order_by(Paper.first_seen_at.desc(), Paper.id.desc())


def run_embed_papers(ctx: JobContext, paper_ids: list[int] | None = None) -> dict[str, Any]:
    """Local embeddings (default model) for papers lacking them (all, or ``paper_ids``)."""
    emb = get_embedder()
    ids_filter = [int(p) for p in paper_ids] if paper_ids is not None else None
    with ctx.session(write=False) as session:
        todo = list(session.scalars(_missing(emb.model_id, ids_filter)))
    computed = 0
    for start in range(0, len(todo), EMBED_CHUNK):
        ctx.progress(start / max(1, len(todo)), f"Embedding papers locally ({start}/{len(todo)})")
        with ctx.session(write=True) as session:
            computed += ensure_paper_vectors(session, todo[start : start + EMBED_CHUNK], emb.model_id)
    ctx.progress(1.0, f"Embedded {computed} papers with {emb.model_id}")
    return {
        "model_id": emb.model_id,
        "fallback": fallback_active(),
        "candidates": len(todo),
        "computed": computed,
    }
