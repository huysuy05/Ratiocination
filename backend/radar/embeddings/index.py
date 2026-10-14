"""In-memory brute-force index over paper vectors (SPEC §9). Owner: emb.

One index per model id over the ``embeddings`` rows with entity type ``paper``. The vectors are
loaded lazily (one short read session) on the first search after creation or invalidation and
searched with numpy: stored vectors are L2-normalized, the query is normalized, so the score is the
cosine. Results are ordered by cosine (descending), ties by paper id (ascending).

Invalidation uses monotonically increasing epochs (per model, plus a global one for
``invalidate(None)``/``clear_all()``): a snapshot remembers the epoch it was loaded at and is reused
only while that epoch is current, so an invalidation that lands while a load is running can never
leave a stale snapshot cached - and invalidating never waits for a running load. Loads are
serialized per index; searches run on immutable snapshots, so the class is thread-safe.
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select

from radar.db import session_scope
from radar.embeddings.base import vectors_from_bytes
from radar.embeddings.registry import known_dimension
from radar.models import Embedding

log = logging.getLogger(__name__)

ENTITY_TYPE = "paper"


@dataclass(frozen=True)
class _Snapshot:
    epoch: int
    ids: np.ndarray  # int64, ascending, read-only
    matrix: np.ndarray  # (n, dim) float32, read-only
    dim: int | None  # None when the index is empty


class VectorIndex:
    """Entity type ``paper`` only; one index per model id; lazy; thread-safe."""

    _registry_lock = threading.Lock()
    _indexes: dict[str, VectorIndex] = {}
    _epochs = itertools.count(1)
    _model_epoch: dict[str, int] = {}
    _global_epoch: int = 0

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self._load_lock = threading.Lock()
        self._snapshot: _Snapshot | None = None

    # ---- registry ----------------------------------------------------------------------------

    @classmethod
    def get(cls, model_id: str) -> VectorIndex:
        """The (lazily loaded) index of ``model_id``'s paper vectors."""
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("model_id is required")
        with cls._registry_lock:
            index = cls._indexes.get(model_id)
            if index is None:
                index = cls(model_id)
                cls._indexes[model_id] = index
            return index

    @classmethod
    def invalidate(cls, model_id: str | None = None) -> None:
        """Drop the index for ``model_id`` (all models when ``None``); it reloads lazily."""
        with cls._registry_lock:
            epoch = next(cls._epochs)
            if model_id is None:
                cls._global_epoch = epoch
                targets = list(cls._indexes.values())
            else:
                cls._model_epoch[model_id] = epoch
                index = cls._indexes.get(model_id)
                targets = [index] if index is not None else []
        for index in targets:
            index._snapshot = None  # free the memory now; the epoch check alone guarantees freshness

    @classmethod
    def clear_all(cls) -> None:
        """Drop every index (tests)."""
        with cls._registry_lock:
            cls._global_epoch = next(cls._epochs)
            targets = list(cls._indexes.values())
            cls._indexes.clear()
        for index in targets:
            index._snapshot = None

    @classmethod
    def _current_epoch(cls, model_id: str) -> int:
        with cls._registry_lock:
            return max(cls._global_epoch, cls._model_epoch.get(model_id, 0))

    # ---- loading -----------------------------------------------------------------------------

    def _get_snapshot(self) -> _Snapshot:
        snap = self._snapshot
        if snap is not None and snap.epoch == self._current_epoch(self.model_id):
            return snap
        with self._load_lock:
            epoch = self._current_epoch(self.model_id)
            snap = self._snapshot
            if snap is not None and snap.epoch == epoch:
                return snap
            snap = self._load(epoch)
            self._snapshot = snap
            return snap

    def _load(self, epoch: int) -> _Snapshot:
        with session_scope() as session:
            rows = session.execute(
                select(Embedding.entity_id, Embedding.dimension, Embedding.vector)
                .where(Embedding.entity_type == ENTITY_TYPE, Embedding.model_id == self.model_id)
                .order_by(Embedding.entity_id)
            ).all()
        if not rows:
            empty_ids = np.zeros(0, dtype=np.int64)
            empty_ids.setflags(write=False)
            return _Snapshot(epoch, empty_ids, np.zeros((0, 0), dtype=np.float32), None)
        dim = known_dimension(self.model_id) or int(rows[0].dimension)
        bad = sum(1 for row in rows if row.dimension != dim)
        if bad:
            raise ValueError(f"{bad} stored paper vectors of {self.model_id} do not have dimension {dim}")
        ids = np.fromiter((row.entity_id for row in rows), dtype=np.int64, count=len(rows))
        matrix = vectors_from_bytes([row.vector for row in rows], dim)
        ids.setflags(write=False)
        matrix.setflags(write=False)
        log.debug("vector index loaded: model=%s papers=%d", self.model_id, len(rows))
        return _Snapshot(epoch, ids, matrix, dim)

    def __len__(self) -> int:
        """Number of paper vectors of this model (loads the index when needed)."""
        return int(self._get_snapshot().ids.size)

    # ---- search ------------------------------------------------------------------------------

    def search(
        self,
        query: np.ndarray,
        k: int,
        restrict_ids: set[int] | None = None,
        exclude_ids: set[int] | None = None,
    ) -> list[tuple[int, float]]:
        """Top-``k`` ``(paper_id, cosine)`` pairs.

        ``restrict_ids`` limits the search to those papers (``None`` = all; empty = nothing);
        ``exclude_ids`` removes papers. A query whose dimension differs from the index's raises
        ``ValueError``. An empty index returns ``[]``.
        """
        q = np.asarray(query, dtype=np.float64)
        if q.ndim == 2 and q.shape[0] == 1:
            q = q[0]
        if q.ndim != 1:
            raise ValueError("query must be a single vector")
        if not np.all(np.isfinite(q)):
            raise ValueError("query vector must be finite")
        k = int(k)
        if k <= 0 or (restrict_ids is not None and len(restrict_ids) == 0):
            return []
        snap = self._get_snapshot()
        if snap.dim is None:
            return []
        if q.shape[0] != snap.dim:
            raise ValueError(
                f"query has dimension {q.shape[0]} but {self.model_id} paper vectors have dimension {snap.dim}"
            )
        norm = float(np.linalg.norm(q))
        qn = (q / norm if norm > 0.0 else q).astype(np.float32)

        ids = snap.ids
        scores = snap.matrix @ qn
        keep: np.ndarray | None = None
        if restrict_ids is not None:
            keep = np.isin(ids, _id_array(restrict_ids))
        if exclude_ids:
            dropped = np.isin(ids, _id_array(exclude_ids))
            keep = ~dropped if keep is None else keep & ~dropped
        if keep is not None:
            ids = ids[keep]
            scores = scores[keep]
        n = int(scores.shape[0])
        if n == 0:
            return []
        kk = min(k, n)
        if kk < n:
            kth = np.partition(scores, n - kk)[n - kk]  # the kk-th largest score
            candidates = np.nonzero(scores >= kth)[0]  # every tie at the boundary stays in
        else:
            candidates = np.arange(n)
        order = candidates[np.lexsort((ids[candidates], -scores[candidates]))][:kk]
        return [(int(ids[i]), float(scores[i])) for i in order]


def _id_array(values: Iterable[int]) -> np.ndarray:
    return np.fromiter((int(v) for v in values), dtype=np.int64)
