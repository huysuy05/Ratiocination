"""Per-project logistic preference head (SPEC §10). Owner: rank.

The approach Scholar Inbox reports: a logistic regression over paper embeddings, trained in every
deck build (milliseconds). Positives: seeds and relevant papers (weight 1.0), saved papers (0.5).
Negatives: not-relevant papers (1.0) plus a seeded sample of unlabeled window papers (0.1 each) so
the model also learns what an ordinary paper of the window looks like. Trained only with at least
three positives and one negative row.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.linear_model import LogisticRegression

MIN_POSITIVES = 3
UNLABELED_WEIGHT = 0.1
UNLABELED_FACTOR = 10


@dataclass(frozen=True)
class PreferenceModel:
    coef: np.ndarray
    intercept: float
    n_pos: int
    n_neg: int
    n_unlabeled: int

    def decision(self, vectors: np.ndarray) -> np.ndarray:
        return np.asarray(vectors, dtype=np.float64) @ self.coef + self.intercept

    def probability(self, vectors: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-self.decision(vectors)))


def sample_unlabeled(n_available: int, n_labels: int, seed: int) -> np.ndarray:
    """Indices of up to ``UNLABELED_FACTOR × n_labels`` unlabeled rows, drawn without replacement."""
    size = min(n_available, UNLABELED_FACTOR * max(n_labels, 0))
    if size <= 0:
        return np.zeros(0, dtype=np.int64)
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(n_available, size=size, replace=False))


def train(
    positives: np.ndarray,
    positive_weights: np.ndarray,
    negatives: np.ndarray,
    negative_weights: np.ndarray,
    unlabeled: np.ndarray | None = None,
) -> PreferenceModel | None:
    """Fit the head, or ``None`` when there are fewer than three positives or no negative row."""
    dim = positives.shape[1] if positives.ndim == 2 else 0
    unlabeled = np.zeros((0, dim)) if unlabeled is None else unlabeled
    if len(positives) < MIN_POSITIVES or len(negatives) + len(unlabeled) == 0:
        return None
    parts = [p for p in (positives, negatives, unlabeled) if len(p)]
    x = np.vstack(parts).astype(np.float64)
    y = np.concatenate([np.ones(len(positives)), np.zeros(len(negatives) + len(unlabeled))])
    w = np.concatenate(
        [
            np.asarray(positive_weights, dtype=np.float64),
            np.asarray(negative_weights, dtype=np.float64),
            np.full(len(unlabeled), UNLABELED_WEIGHT),
        ]
    )
    model = LogisticRegression(C=1.0, max_iter=1000)
    model.fit(x, y, sample_weight=w)
    return PreferenceModel(
        coef=model.coef_[0].astype(np.float64),
        intercept=float(model.intercept_[0]),
        n_pos=len(positives),
        n_neg=len(negatives),
        n_unlabeled=len(unlabeled),
    )


__all__ = [
    "MIN_POSITIVES",
    "UNLABELED_FACTOR",
    "UNLABELED_WEIGHT",
    "PreferenceModel",
    "sample_unlabeled",
    "train",
]
