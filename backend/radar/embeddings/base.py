"""Embedder protocol, text helpers and the vector storage format (SPEC §9). Owner: emb.

Every embedder returns ``(n, dim)`` float32 matrices whose rows are L2-normalized. A text with no
content at all (e.g. an empty text, or only stop words for ``local/hashing-v1``) maps to the zero
vector, whose cosine with anything is 0. Stored vectors are float32 little-endian bytes
(:data:`VECTOR_DTYPE`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from typing import Protocol, runtime_checkable

import numpy as np

VECTOR_DTYPE = np.dtype("<f4")
"""Storage format of ``embeddings.vector``: float32, little-endian."""

ENTITY_TYPES = frozenset({"paper", "chunk", "project"})
"""Values of ``embeddings.entity_type`` (SPEC §4)."""


@runtime_checkable
class Embedder(Protocol):
    """A local text embedder; outputs are ``(n, dim)`` float32, L2-normalized.

    Embedders may also expose ``query_prefix``/``passage_prefix`` (the instruction they put before
    queries/passages; ``""`` when absent): :func:`model_input` uses them so that the stored content
    hash covers exactly what the model saw.
    """

    model_id: str
    dim: int
    max_tokens: int
    similarity_threshold: float

    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    def embed_queries(self, texts: list[str]) -> np.ndarray: ...


def paper_text(title: str, abstract: str | None) -> str:
    """``f"{title}\\n\\n{abstract or ''}".strip()``."""
    return f"{title or ''}\n\n{abstract or ''}".strip()


def model_input(emb: object, text: str, *, query: bool) -> str:
    """The text as the model sees it: the embedder's query or passage prefix + ``text``."""
    prefix = getattr(emb, "query_prefix" if query else "passage_prefix", "") or ""
    return f"{prefix}{text}" if isinstance(prefix, str) else text


def content_hash(text: str) -> str:
    """Hash of the embedded text (re-embed only when it or the model changes).

    SHA-256 hex digest (64 chars) of the UTF-8 bytes; lone surrogates from damaged extractions are
    encoded with ``surrogatepass`` so hashing never fails.
    """
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


# ------------------------------------------------------------------------------------------------
# Helpers shared by the embedders, the vector store and the index
# ------------------------------------------------------------------------------------------------


def check_texts(texts: Iterable[str]) -> list[str]:
    """Validate embedder input: an iterable of ``str`` (never a bare string)."""
    if isinstance(texts, (str, bytes)):
        raise TypeError("texts must be a list of strings, not a single string")
    out = list(texts)
    for text in out:
        if not isinstance(text, str):
            raise TypeError("every text must be a str")
    return out


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """Rows scaled to unit L2 norm as a C-contiguous float32 array; all-zero rows stay zero.

    Norms are computed in float64. Non-finite values raise ``ValueError``.
    """
    m = np.asarray(matrix, dtype=np.float64)
    if m.ndim != 2:
        raise ValueError("expected a 2-d matrix")
    if not np.all(np.isfinite(m)):
        raise ValueError("vectors must be finite")
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    safe = np.where(norms > 0.0, norms, 1.0)
    return np.ascontiguousarray(m / safe, dtype=np.float32)


def vector_to_bytes(vector: np.ndarray) -> bytes:
    """One vector as float32 little-endian bytes (the ``embeddings.vector`` format)."""
    v = np.asarray(vector)
    if v.ndim != 1:
        raise ValueError("expected a 1-d vector")
    return v.astype(VECTOR_DTYPE, copy=False).tobytes()


def vectors_from_bytes(blobs: Sequence[bytes], dim: int) -> np.ndarray:
    """Stack stored vectors into an ``(n, dim)`` float32 matrix; ``ValueError`` on a wrong length."""
    if dim <= 0:
        raise ValueError("dimension must be positive")
    width = dim * VECTOR_DTYPE.itemsize
    for blob in blobs:
        if len(blob) != width:
            raise ValueError(f"stored vector has {len(blob)} bytes, expected {width} (dimension {dim})")
    if not blobs:
        return np.zeros((0, dim), dtype=np.float32)
    flat = np.frombuffer(b"".join(blobs), dtype=VECTOR_DTYPE)
    return np.ascontiguousarray(flat.reshape(len(blobs), dim), dtype=np.float32)
