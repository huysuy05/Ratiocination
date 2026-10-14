"""Text helpers, the deterministic ``local/hashing-v1`` embedder and the package import rules (SPEC §9, §2)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import numpy as np
import pytest

from radar.embeddings.base import (
    Embedder,
    check_texts,
    content_hash,
    l2_normalize,
    paper_text,
    vector_to_bytes,
    vectors_from_bytes,
)
from radar.embeddings.hashing import MODEL_ID, HashingEmbedder

BACKEND_DIR = Path(__file__).resolve().parents[1]

TEXTS = [
    "Test paper A: graph neural networks for citation recommendation",
    "Test paper B: dense retrieval with contrastive pre-training",
    "Test paper C: graph neural networks for citation recommendation",
]


# ---- text helpers --------------------------------------------------------------------------------


def test_paper_text_follows_the_spec_formula() -> None:
    assert paper_text("Title", "Abstract text.") == "Title\n\nAbstract text."
    assert paper_text("Title", None) == "Title"
    assert paper_text("Title", "") == "Title"
    assert paper_text("  Title  ", "  Abstract  ") == "Title  \n\n  Abstract"


def test_content_hash_is_sha256_hex_and_sensitive_to_any_change() -> None:
    h = content_hash("Test paper A\n\nabstract")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)
    assert h == content_hash("Test paper A\n\nabstract")
    assert h != content_hash("Test paper A\n\nabstract.")
    assert content_hash("bad \ud800 surrogate")  # damaged extractions never make hashing fail


def test_l2_normalize_and_byte_round_trip() -> None:
    m = np.array([[3.0, 4.0], [0.0, 0.0]])
    out = l2_normalize(m)
    assert out.dtype == np.float32 and out.flags.c_contiguous
    np.testing.assert_allclose(out, [[0.6, 0.8], [0.0, 0.0]], rtol=1e-6)
    with pytest.raises(ValueError):
        l2_normalize(np.array([[np.nan, 1.0]]))
    blob = vector_to_bytes(out[0])
    assert blob == np.array([0.6, 0.8], dtype="<f4").tobytes()
    back = vectors_from_bytes([blob, blob], 2)
    assert back.shape == (2, 2) and back.dtype == np.float32
    np.testing.assert_array_equal(back[0], out[0])
    with pytest.raises(ValueError):
        vectors_from_bytes([blob], 3)  # wrong dimension for the stored bytes


def test_check_texts_rejects_a_bare_string_and_non_strings() -> None:
    assert check_texts(("a", "b")) == ["a", "b"]
    with pytest.raises(TypeError):
        check_texts("not a list")
    with pytest.raises(TypeError):
        check_texts(["ok", None])  # type: ignore[list-item]


# ---- hashing embedder -------------------------------------------------------------------------------


def test_hashing_embedder_satisfies_the_protocol() -> None:
    emb = HashingEmbedder()
    assert isinstance(emb, Embedder)
    assert (emb.model_id, emb.dim, emb.similarity_threshold) == (MODEL_ID, 1024, 0.30)
    assert emb.max_tokens > 0


def test_hashing_is_deterministic_and_unit_norm() -> None:
    a = HashingEmbedder().embed_passages(TEXTS)
    b = HashingEmbedder().embed_passages(list(reversed(TEXTS)))[::-1]
    assert a.shape == (3, 1024) and a.dtype == np.float32
    np.testing.assert_array_equal(a, b)  # independent of instance and batch composition
    np.testing.assert_allclose(np.linalg.norm(a, axis=1), 1.0, rtol=1e-6)
    assert np.all(a >= 0.0)  # alternate_sign=False
    np.testing.assert_array_equal(a[0], a[2])  # same text, same vector
    assert float(a[0] @ a[1]) < 0.5 < float(a[0] @ a[2])


def test_hashing_matches_the_specified_pipeline() -> None:
    from sklearn.feature_extraction.text import HashingVectorizer

    vec = HashingVectorizer(
        n_features=1024, alternate_sign=False, ngram_range=(1, 2), norm=None, stop_words="english"
    )
    counts = vec.transform(TEXTS).toarray()
    expected = np.log1p(counts)
    expected /= np.linalg.norm(expected, axis=1, keepdims=True)
    np.testing.assert_allclose(HashingEmbedder().embed_passages(TEXTS), expected, rtol=1e-6, atol=1e-7)


def test_hashing_queries_equal_passages_and_edge_inputs() -> None:
    emb = HashingEmbedder()
    np.testing.assert_array_equal(emb.embed_queries(TEXTS), emb.embed_passages(TEXTS))
    empty = emb.embed_passages([])
    assert empty.shape == (0, 1024) and empty.dtype == np.float32
    zero = emb.embed_passages(["", "the and of"])  # no tokens left after stop words
    assert zero.shape == (2, 1024) and not zero.any()
    with pytest.raises(TypeError):
        emb.embed_passages("a single string")  # type: ignore[arg-type]


def test_hashing_is_deterministic_across_processes_and_package_imports_stay_local() -> None:
    """Fresh interpreter: same vector; importing every radar.embeddings module loads no network/jobs
    module, does not import fastembed, and forces Hugging Face offline mode even when
    huggingface_hub was imported first (SPEC §2 import rule, §9)."""
    script = textwrap.dedent(
        """
        import importlib, json, os, pkgutil, sys
        import huggingface_hub.constants as hf_constants
        offline_before = hf_constants.HF_HUB_OFFLINE
        import radar.embeddings
        for info in pkgutil.walk_packages(radar.embeddings.__path__, "radar.embeddings."):
            importlib.import_module(info.name)
        heavy = sorted(m for m in ("fastembed", "onnxruntime", "sklearn") if m in sys.modules)
        from radar.embeddings.hashing import HashingEmbedder
        vec = HashingEmbedder().embed_passages([sys.argv[1]])[0]
        prefixes = ("radar.net", "radar.connectors", "radar.ingestion", "radar.jobs", "radar.graph.expand")
        print(json.dumps({
            "forbidden": sorted(m for m in sys.modules if m.startswith(prefixes)),
            "heavy_at_import": heavy,
            "offline_before": offline_before,
            "offline_after": hf_constants.HF_HUB_OFFLINE,
            "env_offline": os.environ.get("HF_HUB_OFFLINE"),
            "env_telemetry": os.environ.get("HF_HUB_DISABLE_TELEMETRY"),
            "vector": vec.tolist(),
        }))
        """
    )
    hf_vars = {"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"}
    env = {k: v for k, v in os.environ.items() if k not in hf_vars}
    proc = subprocess.run(
        [sys.executable, "-c", script, TEXTS[0]],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["forbidden"] == []
    assert out["heavy_at_import"] == []
    assert out["offline_before"] is False and out["offline_after"] is True
    assert out["env_offline"] == "1" and out["env_telemetry"] == "1"
    np.testing.assert_array_equal(
        np.asarray(out["vector"], dtype=np.float32), HashingEmbedder().embed_passages(TEXTS[:1])[0]
    )
