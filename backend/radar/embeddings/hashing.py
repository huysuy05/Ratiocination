"""Deterministic fallback embedder ``local/hashing-v1`` (SPEC §9). Owner: emb.

``HashingVectorizer(n_features=1024, alternate_sign=False, ngram_range=(1, 2), norm=None,
stop_words='english')`` -> ``log1p`` of the counts -> L2 normalize. No model files, no download, no
fitted state: the same text gives the same vector in every process (murmurhash3 with a fixed seed).
Queries and passages are embedded the same way. A text without any token (empty, or only stop
words) maps to the zero vector.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np

from radar.embeddings.base import check_texts, l2_normalize

MODEL_ID = "local/hashing-v1"
N_FEATURES = 1024


class HashingEmbedder:
    """HashingVectorizer(n_features=1024, ngram (1, 2), english stop words) -> log1p -> L2 normalize."""

    model_id: str = MODEL_ID
    dim: int = N_FEATURES
    max_tokens: int = 100_000
    similarity_threshold: float = 0.30
    query_prefix: str = ""
    passage_prefix: str = ""

    def __init__(self) -> None:
        self._vectorizer: Any = None
        self._lock = threading.Lock()

    def _get_vectorizer(self) -> Any:
        # scikit-learn is imported lazily so importing radar.embeddings stays cheap.
        with self._lock:
            if self._vectorizer is None:
                from sklearn.feature_extraction.text import HashingVectorizer

                self._vectorizer = HashingVectorizer(
                    n_features=N_FEATURES,
                    alternate_sign=False,
                    ngram_range=(1, 2),
                    norm=None,
                    stop_words="english",
                )
            return self._vectorizer

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        """``(len(texts), 1024)`` float32, rows L2-normalized (zero rows for token-less texts)."""
        items = check_texts(texts)
        if not items:
            return np.zeros((0, self.dim), dtype=np.float32)
        # transform() is stateless (nothing is fitted), so concurrent calls are safe.
        counts = self._get_vectorizer().transform(items).tocsr()
        counts.data = np.log1p(counts.data)
        return l2_normalize(counts.toarray())

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        """Same as :meth:`embed_passages` (the hashing model is symmetric)."""
        return self.embed_passages(texts)
