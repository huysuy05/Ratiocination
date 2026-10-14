"""Maximal marginal relevance selection (SPEC §10). Owner: rank. Pure numpy.

Greedy: repeatedly pick ``argmax λ·rel(p) − (1−λ)·max cos(p, picked)``, where ``rel`` is the score
min-max scaled to [0, 1] over the candidates (1.0 for every candidate when the score is constant) and
cosines come from L2-normalized vectors. Ties go to the lower index, so results are deterministic.
"""

from __future__ import annotations

import numpy as np

DEFAULT_LAMBDA = 0.7


def minmax(values: np.ndarray) -> np.ndarray:
    x = np.asarray(values, dtype=np.float64)
    if x.size == 0:
        return x.copy()
    lo, hi = float(x.min()), float(x.max())
    if hi - lo < 1e-12:
        return np.ones_like(x)
    return (x - lo) / (hi - lo)


def mmr_select(scores: np.ndarray, vectors: np.ndarray, k: int, lam: float = DEFAULT_LAMBDA) -> list[int]:
    """Indices of up to ``k`` candidates in pick order."""
    n = len(scores)
    if n == 0 or k <= 0:
        return []
    if vectors.shape[0] != n:
        raise ValueError("one vector per score is required")
    rel = minmax(scores)
    max_sim = np.zeros(n, dtype=np.float64)
    picked: list[int] = []
    available = np.ones(n, dtype=bool)
    for _ in range(min(k, n)):
        gain = lam * rel - (1.0 - lam) * max_sim
        gain[~available] = -np.inf
        best = int(np.argmax(gain))  # first maximum: ties go to the lower index
        picked.append(best)
        available[best] = False
        max_sim = np.maximum(max_sim, vectors @ vectors[best])
    return picked


__all__ = ["DEFAULT_LAMBDA", "minmax", "mmr_select"]
