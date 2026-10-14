"""fastembed backends (SPEC §9, D4): the local ONNX models in :data:`SPECS`. Owner: emb.

Every model is used with its own query/passage instructions (:class:`ModelSpec`): fastembed 0.9's
``query_embed``/``passage_embed`` add no prefix for these models, so the prefixes are applied here and
the texts go through ``TextEmbedding.embed``. Texts are embedded in length-sorted batches capped by
both :data:`BATCH_SIZE` texts and :data:`TOKEN_BUDGET` estimated tokens, so long abstracts are embedded
a few at a time (the attention working memory grows with batch × length², and ONNX Runtime keeps the
peak).

Importing this module sets ``HF_HUB_OFFLINE=1`` and ``HF_HUB_DISABLE_TELEMETRY=1`` (and patches
``huggingface_hub.constants`` when that library is already loaded, because it reads them only once at
import), so normal runs never contact Hugging Face. The model is loaded from
``<data>/models/fastembed`` with ``local_files_only=True`` and only after the cache check passed.

The ONLY download path is :func:`download_model_files` (used by
``radar.embeddings.registry.run_download_model``), which lifts offline mode just for the download
(:func:`hf_online`). Public model files only; no private data is sent.

``fastembed`` (onnxruntime, tokenizers, huggingface_hub) is imported lazily.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import stat
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

from radar.config import get_settings
from radar.embeddings.base import check_texts, l2_normalize

log = logging.getLogger(__name__)

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@dataclass(frozen=True)
class ModelSpec:
    """One supported fastembed model and how to use it."""

    model_id: str  # the id stored with every vector (``embeddings.model_id``)
    name: str  # fastembed's model name
    dim: int
    query_prefix: str  # prepended to queries (research question + description)
    passage_prefix: str  # prepended to passages (papers, document chunks)
    similarity_threshold: float  # SEMANTICALLY_SIMILAR edges and "supports this section" (SPEC §12, §14.5)
    max_tokens: int = 512


BGE_SMALL = ModelSpec(
    model_id="fastembed/BAAI/bge-small-en-v1.5",
    name="BAAI/bge-small-en-v1.5",
    dim=384,
    query_prefix=BGE_QUERY_PREFIX,
    passage_prefix="",
    similarity_threshold=0.80,
)
BGE_BASE = ModelSpec(
    model_id="fastembed/BAAI/bge-base-en-v1.5",
    name="BAAI/bge-base-en-v1.5",
    dim=768,
    query_prefix=BGE_QUERY_PREFIX,
    passage_prefix="",
    # cosines run lower than bge-small's: 0.76 keeps the share of top-8 neighbour pairs that 0.80 keeps
    # for bge-small (83 % over 1,000 recent cs.AI papers, measured 2026-10-13)
    similarity_threshold=0.76,
)
SPECS: dict[str, ModelSpec] = {spec.model_id: spec for spec in (BGE_SMALL, BGE_BASE)}
"""Supported fastembed models, the default first (also the order of the interim fallback, SPEC §9)."""
DEFAULT_SPEC = BGE_SMALL

# Backwards-compatible names for the default model.
MODEL_ID = DEFAULT_SPEC.model_id
FASTEMBED_MODEL_NAME = DEFAULT_SPEC.name
DIM = DEFAULT_SPEC.dim

BATCH_SIZE = 32
"""At most this many texts per ONNX call."""
TOKEN_BUDGET = 4096
"""At most this many (estimated, padded) tokens per ONNX call: 32 titles, but 8 full-length abstracts."""
CHARS_PER_TOKEN = 4.0

# fastembed's tokenizer loader requires these two besides the model file(s); config.json and
# special_tokens_map.json are optional.
_TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json")


def _force_hf_offline() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    constants = sys.modules.get("huggingface_hub.constants")
    if constants is not None:  # already imported: it captured the environment at import time
        constants.HF_HUB_OFFLINE = True  # type: ignore[attr-defined]
        constants.HF_HUB_DISABLE_TELEMETRY = True  # type: ignore[attr-defined]


_force_hf_offline()


class ModelNotCached(RuntimeError):
    """The model files are not in the local cache (download them explicitly first)."""


@dataclass(frozen=True)
class ModelFiles:
    """Where fastembed keeps a model in its Hugging Face cache and which files loading needs."""

    name: str
    hf_repo: str
    required: tuple[str, ...]
    dim: int
    approx_bytes: int


def default_cache_dir() -> Path:
    """``<RADAR_DATA_DIR>/models/fastembed``."""
    return get_settings().models_dir / "fastembed"


def fastembed_installed() -> bool:
    """True when the ``fastembed`` package can be imported (checked without importing it)."""
    return importlib.util.find_spec("fastembed") is not None


@lru_cache(maxsize=8)
def spec_for(model_id: str) -> ModelSpec:
    """The :class:`ModelSpec` of ``model_id`` (``KeyError`` when unsupported)."""
    return SPECS[model_id]


def model_files(name: str = FASTEMBED_MODEL_NAME) -> ModelFiles:
    """fastembed's description of ``name`` (``ImportError`` without fastembed, ``ValueError`` if unknown)."""
    from fastembed import TextEmbedding

    for desc in TextEmbedding.list_supported_models():
        if str(desc.get("model", "")).lower() != name.lower():
            continue
        hf_repo = (desc.get("sources") or {}).get("hf")
        if not hf_repo:
            raise ValueError(f"fastembed has no Hugging Face source for {name}")
        files = (
            str(desc["model_file"]),
            *(str(f) for f in desc.get("additional_files") or ()),
            *_TOKENIZER_FILES,
        )
        return ModelFiles(
            name=str(desc["model"]),
            hf_repo=str(hf_repo),
            required=tuple(dict.fromkeys(files)),
            dim=int(desc["dim"]),
            approx_bytes=int(float(desc.get("size_in_GB") or 0.0) * 1e9),
        )
    raise ValueError(f"fastembed does not support {name}")


def is_cached(name: str = FASTEMBED_MODEL_NAME, cache_dir: Path | None = None) -> bool:
    """True when every file fastembed needs to load ``name`` is in ``cache_dir`` (no network, no writes)."""
    cache = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    if not cache.is_dir() or not fastembed_installed():
        return False
    try:
        spec = model_files(name)
    except (ImportError, ValueError):
        return False
    from huggingface_hub import try_to_load_from_cache

    for filename in spec.required:
        path = try_to_load_from_cache(spec.hf_repo, filename, cache_dir=str(cache))
        if not isinstance(path, str) or not os.path.isfile(path):
            return False
    return True


def cache_bytes(cache_dir: Path | None = None) -> int:
    """Total size of the regular files under ``cache_dir`` (symlinks are not followed or counted)."""
    cache = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    total = 0
    for root, _dirs, files in os.walk(cache):
        for filename in files:
            try:
                st = os.lstat(os.path.join(root, filename))
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size
    return total


def load_text_embedding(name: str = FASTEMBED_MODEL_NAME, cache_dir: Path | None = None) -> Any:
    """A ``fastembed.TextEmbedding`` loaded from the local cache only (``ModelNotCached`` otherwise)."""
    cache = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    if not is_cached(name, cache):
        raise ModelNotCached(f"embedding model {name} is not downloaded")
    from fastembed import TextEmbedding

    try:
        return TextEmbedding(model_name=name, cache_dir=str(cache), local_files_only=True)
    except TypeError as exc:  # a fastembed without local_files_only; HF_HUB_OFFLINE=1 still applies
        if "local_files_only" not in str(exc):
            raise
        return TextEmbedding(model_name=name, cache_dir=str(cache))


_hf_env_lock = threading.Lock()


@contextmanager
def hf_online() -> Iterator[None]:
    """Lift Hugging Face offline mode for an explicit download, then restore the previous state.

    Both the environment variable (read by fastembed per call) and ``huggingface_hub.constants``
    (read by the hub client) are switched. Downloads are serialized by a lock.
    """
    import huggingface_hub.constants as hf_constants

    with _hf_env_lock:
        saved_env = os.environ.get("HF_HUB_OFFLINE")
        saved_const = hf_constants.HF_HUB_OFFLINE
        os.environ["HF_HUB_OFFLINE"] = "0"
        hf_constants.HF_HUB_OFFLINE = False
        try:
            yield
        finally:
            if saved_env is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = saved_env
            hf_constants.HF_HUB_OFFLINE = saved_const


def download_model_files(name: str = FASTEMBED_MODEL_NAME, cache_dir: Path | None = None) -> None:
    """Download ``name`` into ``cache_dir`` from Hugging Face (the explicit egress of SPEC §9).

    fastembed first checks the local cache (no request when it is complete and intact) and only
    fetches what is missing. The ONNX session is not created here (``lazy_load=True``).
    """
    from fastembed import TextEmbedding

    cache = Path(cache_dir) if cache_dir is not None else default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    log.info("fetching embedding model %s from Hugging Face (explicit download)", name)
    with hf_online():
        TextEmbedding(model_name=name, cache_dir=str(cache), lazy_load=True)


def batches(lengths: list[int], max_tokens: int = 512) -> list[list[int]]:
    """Indices grouped into ONNX calls: sorted by length, ≤ :data:`BATCH_SIZE` texts and ≤
    :data:`TOKEN_BUDGET` padded tokens (estimated as characters / 4, capped at ``max_tokens``)."""
    order = sorted(range(len(lengths)), key=lambda i: lengths[i])
    out: list[list[int]] = []
    current: list[int] = []
    for i in order:
        tokens = min(max_tokens, max(1, int(lengths[i] / CHARS_PER_TOKEN) + 2))
        # sorted ascending, so this text is the longest of the batch: padded size = count × tokens
        if current and (len(current) >= BATCH_SIZE or (len(current) + 1) * tokens > TOKEN_BUDGET):
            out.append(current)
            current = []
        current.append(i)
    if current:
        out.append(current)
    return out


class FastEmbedEmbedder:
    """Local ONNX model loaded from ``<data>/models/fastembed`` (never downloads here).

    Construction loads the model (``ModelNotCached`` when the files are missing). Queries (the
    project question) get the model's query prefix and passages (papers, chunks) its passage prefix;
    both go through ``TextEmbedding.embed``. Calls are serialized per instance.
    """

    def __init__(self, spec: ModelSpec | None = None, cache_dir: Path | None = None) -> None:
        self.spec = spec or DEFAULT_SPEC
        self.model_id: str = self.spec.model_id
        self.dim: int = self.spec.dim
        self.max_tokens: int = self.spec.max_tokens
        self.similarity_threshold: float = self.spec.similarity_threshold
        self.query_prefix: str = self.spec.query_prefix
        self.passage_prefix: str = self.spec.passage_prefix
        self.cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir()
        self._lock = threading.Lock()
        self._model = load_text_embedding(self.spec.name, self.cache_dir)
        size = int(getattr(self._model, "embedding_size", self.dim))
        if size != self.dim:
            raise RuntimeError(f"{self.spec.name} reports dimension {size}, expected {self.dim}")

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, self.passage_prefix)

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        return self._embed(texts, self.query_prefix)

    def _embed(self, texts: list[str], prefix: str) -> np.ndarray:
        items = [prefix + text for text in check_texts(texts)]
        out = np.zeros((len(items), self.dim), dtype=np.float32)
        if not items:
            return out
        with self._lock:
            for idx in batches([len(text) for text in items], self.max_tokens):
                vectors = np.asarray(
                    list(self._model.embed([items[i] for i in idx], batch_size=len(idx))), dtype=np.float32
                )
                if vectors.shape != (len(idx), self.dim):
                    raise RuntimeError(
                        f"fastembed returned shape {vectors.shape}, expected ({len(idx)}, {self.dim})"
                    )
                out[idx] = vectors
        return l2_normalize(out)
