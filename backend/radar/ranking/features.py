"""Interpretable features and the score (SPEC §10). Owner: rank. Pure numpy.

Seven features, each a raw value per candidate (``None`` = unavailable for this build):

* ``question`` - cosine to the project question vector (always available);
* ``anchor`` - max over anchors of weight × cosine (seeds 1.0, relevant 1.0, saved 0.8);
* ``keyword`` - the project's keywords found in the title (1.0 each) or only in the abstract (0.5
  each), ``radar.keywords``; available when the project has keywords;
* ``draft`` - max cosine to the project's document chunks;
* ``citation`` - 1 when a CITES edge links the candidate and an anchor (binary, not normalized);
* ``preference`` - the logistic-regression decision value;
* ``recency`` - 0.5 ** (age_days / 30).

``citation`` is used as is and ``keyword`` saturates as ``1 − exp(−raw)`` (one title match 0.63, two
0.86): both are facts on an absolute scale, so they are not stretched over the candidates. The other
raw values are normalized robustly over the candidates: ``clip((x − P5) / max(P99 − P5, floor), 0,
1)``, or 0.5 for every candidate when ``P99 − P5 < 1e-6`` (a constant feature carries no ranking
signal). The per-feature ``floor`` (:data:`SPREAD_FLOORS`: 0.1 for cosines, 1.0 logit for the
preference head, 1.0 for recency) keeps noise from being stretched to the full range: without it a
0.01 cosine difference on a vague question would outweigh a strong draft match, and a few days of age
inside a two-week window would count as much as the whole recency weight (recency's raw value is
already in [0, 1], so its floor of 1.0 keeps it on that absolute scale: papers 0 and 14 days old differ
by 0.28, not by 1).
Score ``S = Σ wᵢ·vᵢ / Σ wᵢ`` over available features; contribution ``cᵢ = wᵢ·vᵢ / Σ w`` so ``S = Σ cᵢ``.
A project without keywords (or a deck without a draft, anchors or ratings) leaves that weight out of
``Σ w``, so its score is exactly what the remaining features give.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

FEATURES: tuple[str, ...] = ("question", "anchor", "keyword", "draft", "citation", "preference", "recency")
WEIGHTS: dict[str, float] = {
    "question": 0.30,
    "anchor": 0.25,
    "keyword": 0.20,
    "draft": 0.15,
    "citation": 0.07,
    "preference": 0.15,
    "recency": 0.08,
}
BINARY_FEATURES: frozenset[str] = frozenset({"citation"})
SATURATING_FEATURES: frozenset[str] = frozenset({"keyword"})
"""Raw value r ≥ 0 becomes ``1 − exp(−r)`` (absolute, not normalized over the candidates)."""
SPREAD_FLOORS: dict[str, float] = {
    "question": 0.1,
    "anchor": 0.1,
    "draft": 0.1,
    "preference": 1.0,
    "recency": 1.0,
}
RECENCY_HALF_LIFE_DAYS = 30.0
_FLAT = 1e-6


def robust_norm(raw: np.ndarray, floor: float = 0.0) -> tuple[np.ndarray, dict[str, float]]:
    """``(values, {"lo": P5, "hi": P99})``: ``clip((x − P5) / max(P99 − P5, floor), 0, 1)``; every
    value 0.5 when the spread is below 1e-6."""
    x = np.asarray(raw, dtype=np.float64)
    if x.size == 0:
        return x.copy(), {"lo": 0.0, "hi": 0.0}
    lo, hi = (float(v) for v in np.percentile(x, [5, 99]))
    if hi - lo < _FLAT:
        return np.full_like(x, 0.5), {"lo": lo, "hi": hi}
    return np.clip((x - lo) / max(hi - lo, floor), 0.0, 1.0), {"lo": lo, "hi": hi}


def saturate(raw: np.ndarray) -> np.ndarray:
    """``1 − exp(−r)`` for ``r ≥ 0`` (negative values count as 0)."""
    return -np.expm1(-np.maximum(np.asarray(raw, dtype=np.float64), 0.0))


def recency(age_days: np.ndarray, half_life_days: float = RECENCY_HALF_LIFE_DAYS) -> np.ndarray:
    """``0.5 ** (age / half_life)``; negative ages (dates in the future) count as 0."""
    age = np.maximum(np.asarray(age_days, dtype=np.float64), 0.0)
    return np.power(0.5, age / half_life_days)


def anchor_similarity(
    candidates: np.ndarray, anchors: np.ndarray, weights: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each candidate: ``(best weighted cosine, index of that anchor, its raw cosine)``.

    Rows of ``candidates`` (n×d) and ``anchors`` (m×d) are L2-normalized; ``weights`` has m entries.
    """
    sims = candidates @ anchors.T  # (n, m) cosines
    weighted = sims * np.asarray(weights, dtype=np.float64)[None, :]
    best = np.argmax(weighted, axis=1)
    rows = np.arange(len(candidates))
    return weighted[rows, best], best, sims[rows, best]


def best_match(candidates: np.ndarray, targets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(max cosine, argmax index)`` of each candidate against ``targets`` (chunks, questions)."""
    sims = candidates @ targets.T
    best = np.argmax(sims, axis=1)
    return sims[np.arange(len(candidates)), best], best


@dataclass
class Scored:
    """The score of every candidate and the per-feature numbers behind it."""

    score: np.ndarray
    raw: dict[str, np.ndarray | None]
    values: dict[str, np.ndarray | None]
    contributions: dict[str, np.ndarray]
    norm: dict[str, dict[str, float]] = field(default_factory=dict)
    weights: dict[str, float] = field(default_factory=dict)

    def available(self, name: str) -> bool:
        return self.values.get(name) is not None


def score(raw: Mapping[str, np.ndarray | None], n: int, weights: Mapping[str, float] | None = None) -> Scored:
    """Normalize the available features and combine them (see the module docstring)."""
    w = dict(WEIGHTS if weights is None else weights)
    values: dict[str, np.ndarray | None] = {}
    raws: dict[str, np.ndarray | None] = {}
    norm: dict[str, dict[str, float]] = {}
    for name in FEATURES:
        column = raw.get(name)
        if column is None:
            values[name] = raws[name] = None
            continue
        column = np.asarray(column, dtype=np.float64)
        if column.shape != (n,):
            raise ValueError(f"feature {name} has shape {column.shape}, expected ({n},)")
        raws[name] = column
        if name in BINARY_FEATURES:
            values[name] = np.clip(column, 0.0, 1.0)
        elif name in SATURATING_FEATURES:
            values[name] = saturate(column)
        else:
            values[name], norm[name] = robust_norm(column, SPREAD_FLOORS.get(name, 0.0))
    total = sum(w[name] for name in FEATURES if values[name] is not None)
    contributions: dict[str, np.ndarray] = {}
    result = np.zeros(n, dtype=np.float64)
    for name in FEATURES:
        column = values[name]
        if column is None or total <= 0:
            contributions[name] = np.zeros(n, dtype=np.float64)
            continue
        contributions[name] = w[name] * column / total
        result += contributions[name]
    return Scored(score=result, raw=raws, values=values, contributions=contributions, norm=norm, weights=w)


__all__ = [
    "BINARY_FEATURES",
    "FEATURES",
    "RECENCY_HALF_LIFE_DAYS",
    "SATURATING_FEATURES",
    "SPREAD_FLOORS",
    "WEIGHTS",
    "Scored",
    "anchor_similarity",
    "best_match",
    "recency",
    "robust_norm",
    "saturate",
    "score",
]
