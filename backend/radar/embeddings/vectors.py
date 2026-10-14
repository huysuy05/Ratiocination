"""Vector storage helpers (SPEC §9). Owner: emb.

Vectors are float32 little-endian bytes, L2-normalized; a stored vector of another model or dimension
raises ``ValueError``. Vector writes invalidate ``VectorIndex`` after commit.

* One row per ``(entity_type, entity_id, model_id)``; ``content_hash`` is ``content_hash`` of what the
  model saw (``base.model_input``: the model's query/passage prefix + the text), so an entity is
  re-embedded when its text, the model or the model's instruction changes.
* C3: ``ensure_*`` embed with ``get_embedder(model_id)`` and raise ``ValueError`` when that is not
  ``model_id`` itself (e.g. the model is not downloaded and the hashing fallback is active) or when a
  stored vector of ``model_id`` has another dimension; ``get_vectors`` raises on a dimension mismatch.
* Transactions (SPEC §2.1, CONVENTIONS "Database rules"): ``ensure_*`` take the caller's write
  session but never hold its write lock while embedding when they can avoid it:

  - session with no transaction in progress at the call (the normal case): the current state is read
    through a short read session; then, per batch of ``WRITE_BATCH`` (200) items, the texts are
    embedded first and the rows written and **committed** through the caller's session. A crash keeps
    the batches already committed (vectors are derived data and idempotent).
  - session already in a transaction (e.g. it just inserted the chunks to embed): everything goes
    through it so its uncommitted rows are seen; nothing is committed (the caller commits). The write
    lock is then held while embedding - commit before calling to avoid that.
* Writes of paper vectors register an ``after_commit`` hook on the session that calls
  ``VectorIndex.invalidate(model_id)`` (paper vectors are the only indexed entity type).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager

import numpy as np
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from radar.db import session_scope
from radar.embeddings.base import (
    ENTITY_TYPES,
    Embedder,
    content_hash,
    l2_normalize,
    model_input,
    paper_text,
    vector_to_bytes,
    vectors_from_bytes,
)
from radar.embeddings.index import ENTITY_TYPE as INDEXED_ENTITY_TYPE
from radar.embeddings.index import VectorIndex
from radar.embeddings.registry import get_embedder, known_dimension
from radar.models import Embedding, Paper, utcnow

_IN_CHUNK = 500  # ids per IN (...) clause
_EMBED_BATCH = 256  # texts per embedder call
WRITE_BATCH = 200
"""Rows per short write transaction when ``ensure_*`` commit per batch (CONVENTIONS: <=200 rows)."""
_HASH_MAX = 64  # embeddings.content_hash is String(64)

_PENDING_KEY = "radar.embeddings.pending_index_invalidations"
_HOOKED_KEY = "radar.embeddings.index_hook_installed"


def ensure_paper_vectors(session: Session, paper_ids: list[int], model_id: str) -> int:
    """Embed papers lacking a current vector for ``model_id`` (write session). Returns the count.

    The text is ``paper_text(canonical_title, abstract)``; unknown paper ids are skipped. Commits per
    batch when ``session`` has no transaction in progress (see the module docstring).
    """
    ids = _unique_ids(paper_ids)
    if not ids:
        return 0
    _embedder_for(model_id)  # fail fast, before any database work
    items: list[tuple[int, str]] = []
    with _read_session(session) as reader:
        for chunk in _chunks(ids, _IN_CHUNK):
            rows = reader.execute(
                select(Paper.id, Paper.canonical_title, Paper.abstract).where(Paper.id.in_(chunk))
            ).all()
            items.extend((int(pid), paper_text(title, abstract)) for pid, title, abstract in rows)
    return ensure_vectors(session, "paper", items, model_id)


def ensure_vectors(
    session: Session, entity_type: str, items: list[tuple[int, str]], model_id: str, *, query: bool = False
) -> int:
    """Embed ``(entity_id, text)`` items whose content hash or model changed (write session).

    ``query=True`` uses ``embed_queries`` (project questions), else ``embed_passages``. Returns the
    number of vectors written (0 when everything is current). Duplicate ids must carry the same text.
    Commits per batch of ``WRITE_BATCH`` when ``session`` has no transaction in progress; otherwise
    writes within the caller's transaction (see the module docstring).
    """
    _check_entity_type(entity_type)
    texts = _dedupe_items(items)
    if not texts:
        return 0
    emb = _embedder_for(model_id)
    in_caller_transaction = _has_transaction(session)
    with _read_session(session) as reader:
        stored = _stored_hashes(reader, entity_type, list(texts), model_id, emb.dim)
    todo_ids: list[int] = []
    todo_texts: list[str] = []
    todo_hashes: list[str] = []
    for entity_id, text in texts.items():
        digest = content_hash(model_input(emb, text, query=query))
        if stored.get(entity_id) != digest:
            todo_ids.append(entity_id)
            todo_texts.append(text)
            todo_hashes.append(digest)
    if not todo_ids:
        return 0
    if in_caller_transaction:
        matrix = _embed(emb, todo_texts, query=query)
        return _write_rows(session, entity_type, model_id, todo_ids, matrix, todo_hashes)
    written = 0
    for start in range(0, len(todo_ids), WRITE_BATCH):
        end = start + WRITE_BATCH
        matrix = _embed(emb, todo_texts[start:end], query=query)  # no write lock held here
        written += _write_rows(
            session, entity_type, model_id, todo_ids[start:end], matrix, todo_hashes[start:end]
        )
        session.commit()
    return written


def get_vectors(
    session: Session, entity_type: str, entity_ids: list[int], model_id: str
) -> tuple[np.ndarray, list[int]]:
    """``(matrix, ids)`` for the entities that have a ``model_id`` vector (rows follow ``ids``).

    ``ids`` keeps the order of ``entity_ids`` (first occurrence; duplicates and entities without a
    vector are dropped). ``matrix`` is ``(len(ids), dim)`` float32 - ``(0, dim)`` when nothing is
    found (``(0, 0)`` for a model of unknown dimension).
    """
    _check_entity_type(entity_type)
    _check_model_id(model_id)
    ids = _unique_ids(entity_ids)
    expected = known_dimension(model_id)
    found: dict[int, bytes] = {}
    for chunk in _chunks(ids, _IN_CHUNK):
        rows = session.execute(
            select(Embedding.entity_id, Embedding.model_id, Embedding.dimension, Embedding.vector).where(
                Embedding.entity_type == entity_type,
                Embedding.model_id == model_id,
                Embedding.entity_id.in_(chunk),
            )
        ).all()
        for entity_id, stored_model, dimension, blob in rows:
            if stored_model != model_id:
                raise ValueError(f"stored vector of model {stored_model!r} returned for {model_id!r}")
            if expected is None:
                expected = int(dimension)
            if dimension != expected:
                raise ValueError(
                    f"stored {entity_type} vector {entity_id} has dimension {dimension}, "
                    f"but {model_id} vectors have dimension {expected}"
                )
            found[int(entity_id)] = blob
    if expected is None:
        return np.zeros((0, 0), dtype=np.float32), []
    out_ids = [entity_id for entity_id in ids if entity_id in found]
    return vectors_from_bytes([found[entity_id] for entity_id in out_ids], expected), out_ids


def delete_vectors(session: Session, entity_type: str, entity_ids: list[int]) -> int:
    """Delete vectors of every model for the entities (write session). Returns the count."""
    _check_entity_type(entity_type)
    ids = _unique_ids(entity_ids)
    if not ids:
        return 0
    deleted = 0
    models: set[str] = set()
    for chunk in _chunks(ids, _IN_CHUNK):
        criteria = (Embedding.entity_type == entity_type, Embedding.entity_id.in_(chunk))
        if entity_type == INDEXED_ENTITY_TYPE:
            models.update(session.scalars(select(Embedding.model_id).where(*criteria).distinct()))
        result = session.execute(delete(Embedding).where(*criteria))
        deleted += int(getattr(result, "rowcount", 0) or 0)
    if models:
        _invalidate_index_after_commit(session, models)
    return deleted


def store_vectors(
    session: Session,
    entity_type: str,
    entity_ids: Sequence[int],
    vectors: np.ndarray,
    model_id: str,
    content_hashes: Sequence[str],
) -> int:
    """Upsert vectors computed elsewhere (write session): the low-level write that ``ensure_*`` builds on.

    Rows are L2-normalized before storage. ``ValueError`` when the shapes disagree, an id repeats, or
    the dimension differs from the model's (built-in models) or from vectors already stored for
    ``model_id``. Paper vectors invalidate the model's ``VectorIndex`` after commit. Returns the count.
    """
    _check_entity_type(entity_type)
    _check_model_id(model_id)
    ids = [int(entity_id) for entity_id in entity_ids]
    hashes = [str(h) for h in content_hashes]
    if len(set(ids)) != len(ids):
        raise ValueError("entity ids must be unique")
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[0] != len(ids) or len(hashes) != len(ids):
        raise ValueError("expected one vector row and one content hash per entity id")
    if any(not h or len(h) > _HASH_MAX for h in hashes):
        raise ValueError(f"content hashes must be 1-{_HASH_MAX} characters")
    if not ids:
        return 0
    dim = int(matrix.shape[1])
    if dim <= 0:
        raise ValueError("vectors must have a positive dimension")
    expected = known_dimension(model_id)
    if expected is None:
        expected = session.scalar(select(Embedding.dimension).where(Embedding.model_id == model_id).limit(1))
    if expected is not None and dim != expected:
        raise ValueError(f"{model_id} vectors have dimension {expected}, got {dim}")
    return _write_rows(session, entity_type, model_id, ids, l2_normalize(matrix), hashes)


# ------------------------------------------------------------------------------------------------
# Internals
# ------------------------------------------------------------------------------------------------


def _check_entity_type(entity_type: str) -> None:
    if entity_type not in ENTITY_TYPES:
        raise ValueError(f"unknown entity type {entity_type!r} (expected one of {sorted(ENTITY_TYPES)})")


def _check_model_id(model_id: str) -> None:
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id is required")


def _embedder_for(model_id: str) -> Embedder:
    """The loaded embedder of exactly ``model_id`` (``ValueError`` when unknown or unavailable)."""
    _check_model_id(model_id)
    emb = get_embedder(model_id)
    if emb.model_id != model_id:
        raise ValueError(
            f"embedding model {model_id!r} is not available in this process ({emb.model_id!r} is used "
            "instead); embed with get_embedder().model_id"
        )
    return emb


def _unique_ids(entity_ids: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(int(entity_id) for entity_id in entity_ids))


def _chunks(values: Sequence[int], size: int) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _dedupe_items(items: Iterable[tuple[int, str]]) -> dict[int, str]:
    texts: dict[int, str] = {}
    for entity_id, text in items:
        if not isinstance(text, str):
            raise TypeError("item text must be a str")
        key = int(entity_id)
        previous = texts.get(key)
        if previous is not None and previous != text:
            raise ValueError(f"entity {key} is listed twice with different texts")
        texts[key] = text
    return texts


def _has_transaction(session: Session) -> bool:
    """True when ``session`` has a transaction in progress or unflushed changes."""
    return bool(session.in_transaction() or session.new or session.dirty or session.deleted)


@contextmanager
def _read_session(session: Session) -> Iterator[Session]:
    """Where to read current state: the caller's session when it already has a transaction or
    pending changes (so its uncommitted rows are visible), else a short read session so the caller's
    write lock is not taken before the embedding work is done."""
    if _has_transaction(session):
        yield session
        return
    with session_scope() as reader:
        yield reader


def _stored_hashes(
    reader: Session, entity_type: str, ids: list[int], model_id: str, dim: int
) -> dict[int, str]:
    hashes: dict[int, str] = {}
    width = dim * 4
    for chunk in _chunks(ids, _IN_CHUNK):
        rows = reader.execute(
            select(
                Embedding.entity_id,
                Embedding.dimension,
                Embedding.content_hash,
                func.length(Embedding.vector),
            ).where(
                Embedding.entity_type == entity_type,
                Embedding.model_id == model_id,
                Embedding.entity_id.in_(chunk),
            )
        ).all()
        for entity_id, dimension, digest, nbytes in rows:
            if dimension != dim or (nbytes is not None and int(nbytes) != width):
                raise ValueError(
                    f"stored {entity_type} vector {entity_id} of {model_id} has dimension {dimension}, "
                    f"expected {dim}"
                )
            hashes[int(entity_id)] = digest
    return hashes


def _embed(emb: Embedder, texts: list[str], *, query: bool) -> np.ndarray:
    embed = emb.embed_queries if query else emb.embed_passages
    parts: list[np.ndarray] = []
    for start in range(0, len(texts), _EMBED_BATCH):
        batch = texts[start : start + _EMBED_BATCH]
        part = np.asarray(embed(batch))
        if part.shape != (len(batch), emb.dim):
            raise ValueError(
                f"{emb.model_id} returned shape {part.shape}, expected ({len(batch)}, {emb.dim})"
            )
        parts.append(part)
    return l2_normalize(np.vstack(parts))


def _write_rows(
    session: Session,
    entity_type: str,
    model_id: str,
    ids: list[int],
    matrix: np.ndarray,
    hashes: list[str],
) -> int:
    """Upsert rows through ``session`` (re-reading existing rows under its write lock)."""
    dim = int(matrix.shape[1])
    existing: dict[int, Embedding] = {}
    for chunk in _chunks(ids, _IN_CHUNK):
        for row in session.scalars(
            select(Embedding).where(
                Embedding.entity_type == entity_type,
                Embedding.model_id == model_id,
                Embedding.entity_id.in_(chunk),
            )
        ):
            existing[row.entity_id] = row
    now = utcnow()
    for entity_id, vector, digest in zip(ids, matrix, hashes, strict=True):
        blob = vector_to_bytes(vector)
        row = existing.get(entity_id)
        if row is None:
            session.add(
                Embedding(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    model_id=model_id,
                    dimension=dim,
                    vector=blob,
                    content_hash=digest,
                    created_at=now,
                )
            )
        else:
            row.dimension = dim
            row.vector = blob
            row.content_hash = digest
            row.created_at = now
    session.flush()
    if entity_type == INDEXED_ENTITY_TYPE:
        _invalidate_index_after_commit(session, {model_id})
    return len(ids)


def _invalidate_index_after_commit(session: Session, model_ids: Iterable[str]) -> None:
    """Register (once per session) an ``after_commit`` hook that invalidates these models' indexes.

    Pending models are kept until a commit; after a rollback the next commit merely invalidates
    once more (harmless), which keeps savepoint rollbacks from dropping a needed invalidation.
    """
    pending: set[str] | None = session.info.get(_PENDING_KEY)
    if pending is None:
        pending = set()
        session.info[_PENDING_KEY] = pending
    pending.update(model_ids)
    if not session.info.get(_HOOKED_KEY):
        event.listen(session, "after_commit", _on_after_commit)
        session.info[_HOOKED_KEY] = True


def _on_after_commit(session: Session) -> None:
    pending: set[str] | None = session.info.pop(_PENDING_KEY, None)
    for model_id in sorted(pending or ()):
        VectorIndex.invalidate(model_id)
