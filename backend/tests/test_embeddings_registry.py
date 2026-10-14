"""Embedder registry: fallback decided once per process, reset, availability, model cache detection and
the explicit ``download_model`` job (SPEC §9). No test downloads anything unless RADAR_TEST_NETWORK=1."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from radar.embeddings import fastembed_backend, registry
from radar.embeddings.fastembed_backend import FastEmbedEmbedder, ModelNotCached
from radar.embeddings.hashing import HashingEmbedder

FAST = registry.FASTEMBED_MODEL_ID
OTHER = next(model_id for model_id in fastembed_backend.SPECS if model_id != FAST)
OTHER_SPEC = fastembed_backend.SPECS[OTHER]
HASH = registry.HASHING_MODEL_ID
FAST_DIM = fastembed_backend.DEFAULT_SPEC.dim
DIMS = {spec.name: spec.dim for spec in fastembed_backend.SPECS.values()}


class FakeEmbedder:
    """Deterministic stand-in for a model that is not installed in tests."""

    max_tokens = 512
    similarity_threshold = 0.5

    def __init__(self, model_id: str, dim: int) -> None:
        self.model_id = model_id
        self.dim = dim

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return _unit_rows(texts, self.dim)

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        return _unit_rows(texts, self.dim)


def _raw_rows(texts: list[str], dim: int) -> np.ndarray:
    rows = []
    for text in texts:
        seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        rows.append(np.random.default_rng(seed).normal(size=dim) * 3.0)  # deliberately not unit length
    return np.asarray(rows, dtype=np.float32).reshape(len(texts), dim)


def _unit_rows(texts: list[str], dim: int) -> np.ndarray:
    raw = _raw_rows(texts, dim).astype(np.float64)
    return (raw / np.linalg.norm(raw, axis=1, keepdims=True)).astype(np.float32)


class Ctx:
    """Minimal job context: records progress."""

    job_id = None
    project_id = None

    def __init__(self) -> None:
        self.params: dict[str, Any] = {}
        self.progress_calls: list[tuple[float | None, str | None]] = []

    def progress(self, fraction: float | None, message: str | None = None) -> None:
        self.progress_calls.append((fraction, message))


def _fastembed_cache(data_dir: Path) -> Path:
    return data_dir / "models" / "fastembed"


def _write_fake_model_cache(
    cache_dir: Path, *, skip: str | None = None, name: str = fastembed_backend.FASTEMBED_MODEL_NAME
) -> None:
    """The Hugging Face cache layout fastembed reads: refs/main -> snapshots/<rev>/<files>."""
    from huggingface_hub.file_download import repo_folder_name

    spec = fastembed_backend.model_files(name)
    repo = cache_dir / repo_folder_name(repo_id=spec.hf_repo, repo_type="model")
    revision = "0123456789abcdef0123456789abcdef01234567"
    (repo / "refs").mkdir(parents=True, exist_ok=True)
    (repo / "refs" / "main").write_text(revision)
    snapshot = repo / "snapshots" / revision
    snapshot.mkdir(parents=True, exist_ok=True)
    for name in spec.required:
        if name != skip:
            (snapshot / name).write_bytes(b"synthetic test file " + name.encode())


@pytest.fixture
def fake_fastembed(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    """Replace ``fastembed.TextEmbedding`` with a fake that 'downloads' fake files and embeds with
    deterministic, non-normalized vectors. Records every construction (with the HF offline state)."""
    import fastembed
    import huggingface_hub.constants as hf_constants

    real = fastembed.TextEmbedding
    state: dict[str, Any] = {"inits": [], "calls": []}

    class FakeTextEmbedding:
        list_supported_models = real.list_supported_models

        def __init__(
            self,
            model_name: str,
            cache_dir: str | None = None,
            local_files_only: bool = False,
            lazy_load: bool = False,
            **kwargs: Any,
        ) -> None:
            state["inits"].append(
                {
                    "model_name": model_name,
                    "cache_dir": cache_dir,
                    "local_files_only": local_files_only,
                    "lazy_load": lazy_load,
                    "env_offline": os.environ.get("HF_HUB_OFFLINE"),
                    "const_offline": hf_constants.HF_HUB_OFFLINE,
                }
            )
            self.embedding_size = DIMS[model_name]
            if not local_files_only:
                assert cache_dir is not None
                _write_fake_model_cache(Path(cache_dir), name=model_name)

        def embed(self, texts: list[str], batch_size: int = 256) -> Iterator[np.ndarray]:
            state["calls"].append(("embed", list(texts), batch_size))
            yield from _raw_rows(list(texts), self.embedding_size)

        def passage_embed(self, texts: list[str], batch_size: int = 256) -> Iterator[np.ndarray]:
            raise AssertionError("prefixes are applied by FastEmbedEmbedder; use embed()")

        def query_embed(self, texts: list[str], batch_size: int = 256) -> Iterator[np.ndarray]:
            raise AssertionError("prefixes are applied by FastEmbedEmbedder; use embed()")

    monkeypatch.setattr(fastembed, "TextEmbedding", FakeTextEmbedding)
    yield state


# ---- defaults and fallback ---------------------------------------------------------------------


def test_test_default_is_hashing_without_fallback() -> None:
    emb = registry.get_embedder()
    assert isinstance(emb, HashingEmbedder)
    assert emb.model_id == HASH
    assert registry.get_embedder() is emb  # cached instance
    assert registry.get_embedder(HASH) is emb
    assert registry.fallback_active() is False
    assert registry.model_cached() is True  # the hashing model needs no files


def test_fallback_when_fastembed_model_is_not_cached(
    set_env: Callable[..., None], radar_data_dir: Path
) -> None:
    set_env(RADAR_EMBEDDER=FAST)
    emb = registry.get_embedder()
    assert emb.model_id == HASH
    assert registry.fallback_active() is True
    assert registry.model_cached() is False
    assert registry.model_cached(FAST) is False
    assert registry.model_cached(HASH) is True
    infos = {info.model_id: info for info in registry.available_embedders()}
    assert list(infos) == [FAST, OTHER, HASH]
    assert infos[FAST].available is False and infos[FAST].reason == registry.REASON_NOT_DOWNLOADED
    assert infos[OTHER].available is False and infos[OTHER].reason == registry.REASON_NOT_DOWNLOADED
    assert infos[FAST].dim == FAST_DIM and infos[OTHER].dim == OTHER_SPEC.dim
    assert infos[HASH].available is True and infos[HASH].reason is None and infos[HASH].dim == 1024
    # an explicit request for the unavailable model also gets the fallback (callers use emb.model_id)
    assert registry.get_embedder(FAST).model_id == HASH
    # nothing was downloaded or even created
    assert not _fastembed_cache(radar_data_dir).exists()


def test_available_embedders_does_not_load_models(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setitem(registry._FACTORIES, FAST, lambda: calls.append("fast") or FakeEmbedder(FAST, 384))
    infos = registry.available_embedders()
    assert calls == []
    assert {i.model_id: i.available for i in infos} == {FAST: False, OTHER: False, HASH: True}


def test_fallback_is_decided_once_per_process_until_reset(
    set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    set_env(RADAR_EMBEDDER=FAST)
    attempts: list[int] = []
    ready = {"value": False}
    fake = FakeEmbedder(FAST, 384)

    def factory() -> FakeEmbedder:
        attempts.append(1)
        if not ready["value"]:
            raise registry.EmbedderUnavailable(registry.REASON_NOT_DOWNLOADED)
        return fake

    monkeypatch.setitem(registry._FACTORIES, FAST, factory)
    assert registry.get_embedder().model_id == HASH
    ready["value"] = True  # e.g. the files appear later in this process
    for _ in range(3):
        assert registry.get_embedder().model_id == HASH
    assert registry.fallback_active() is True
    assert len(attempts) == 1  # decided once

    registry.reset()
    assert registry.get_embedder() is fake
    assert registry.fallback_active() is False
    assert registry.get_embedder() is fake
    assert len(attempts) == 2
    assert {i.model_id: i.available for i in registry.available_embedders()} == {
        FAST: True,
        OTHER: False,
        HASH: True,
    }


def test_a_broken_model_falls_back_with_a_reason(
    set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    set_env(RADAR_EMBEDDER=FAST)

    def broken() -> FakeEmbedder:
        raise OSError("corrupted onnx file")

    monkeypatch.setitem(registry._FACTORIES, FAST, broken)
    assert registry.get_embedder().model_id == HASH
    info = next(i for i in registry.available_embedders() if i.model_id == FAST)
    assert info.available is False and info.reason == "Model could not be loaded (OSError)"


def test_unknown_models(set_env: Callable[..., None]) -> None:
    with pytest.raises(ValueError):
        registry.get_embedder("nope/model")
    set_env(RADAR_EMBEDDER="nope/model")  # a misconfigured default falls back instead of failing
    assert registry.get_embedder().model_id == HASH
    assert registry.fallback_active() is True
    assert registry.model_cached() is False
    assert registry.known_dimension("nope/model") is None
    assert registry.known_dimension(HASH) == 1024
    assert registry.known_dimension(FAST) == FAST_DIM
    assert registry.known_dimension(OTHER) == OTHER_SPEC.dim


def test_concurrent_first_calls_load_once(
    set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    set_env(RADAR_EMBEDDER=FAST)
    attempts: list[int] = []
    fake = FakeEmbedder(FAST, 384)

    def slow_factory() -> FakeEmbedder:
        attempts.append(1)
        time.sleep(0.05)
        return fake

    monkeypatch.setitem(registry._FACTORIES, FAST, slow_factory)
    results: list[object] = []
    threads = [threading.Thread(target=lambda: results.append(registry.get_embedder())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(attempts) == 1
    assert all(r is fake for r in results) and len(results) == 8


# ---- model cache detection and the fastembed backend (with a fake fastembed) -----------------------


def test_model_cached_reads_the_hugging_face_cache_layout(
    set_env: Callable[..., None], radar_data_dir: Path
) -> None:
    set_env(RADAR_EMBEDDER=FAST)
    cache = _fastembed_cache(radar_data_dir)
    assert registry.model_cached() is False
    _write_fake_model_cache(cache, skip="tokenizer.json")
    assert registry.model_cached() is False  # incomplete
    _write_fake_model_cache(cache)
    assert registry.model_cached() is True
    assert fastembed_backend.cache_bytes(cache) > 0


def test_a_downloaded_model_bridges_until_the_configured_one_is_downloaded(
    set_env: Callable[..., None], radar_data_dir: Path, fake_fastembed: dict[str, Any]
) -> None:
    """A server switched to another model (RADAR_EMBEDDER) keeps using the model it has downloaded
    (never the hashing model) until the new one is downloaded; every vector call uses the id of the
    model actually loaded, so vectors of the two models are never compared."""
    set_env(RADAR_EMBEDDER=OTHER)
    _write_fake_model_cache(_fastembed_cache(radar_data_dir))  # only the default model is downloaded
    emb = registry.get_embedder()
    assert isinstance(emb, FastEmbedEmbedder) and emb.model_id == FAST and emb.dim == FAST_DIM
    assert registry.fallback_active() is True and registry.model_cached() is False
    assert registry.model_cached(FAST) is True
    infos = {i.model_id: i.available for i in registry.available_embedders()}
    assert infos == {FAST: True, OTHER: False, HASH: True}
    # once the configured model is downloaded (reset() after the download job), it takes over
    _write_fake_model_cache(_fastembed_cache(radar_data_dir), name=OTHER_SPEC.name)
    registry.reset()
    emb = registry.get_embedder()
    assert emb.model_id == OTHER and emb.dim == OTHER_SPEC.dim and registry.fallback_active() is False


def test_a_model_downloaded_by_another_process_is_picked_up(
    set_env: Callable[..., None],
    radar_data_dir: Path,
    fake_fastembed: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``radar download-model`` runs in its own process: the server must not keep the fallback until a
    restart. The files are looked for again (rate-limited) and the model loads once they are there."""
    set_env(RADAR_EMBEDDER=FAST)
    assert registry.get_embedder().model_id == HASH
    _write_fake_model_cache(_fastembed_cache(radar_data_dir))  # the CLI download, elsewhere
    assert registry.get_embedder().model_id == HASH  # checked again only after RECHECK_SECONDS
    monkeypatch.setattr(registry, "RECHECK_SECONDS", 0.0)
    emb = registry.get_embedder()
    assert emb.model_id == FAST and registry.fallback_active() is False


def test_fastembed_embedder_requires_the_cached_model(radar_data_dir: Path) -> None:
    with pytest.raises(ModelNotCached):
        FastEmbedEmbedder()
    assert not _fastembed_cache(radar_data_dir).exists()


def test_fastembed_embedder_loads_offline_and_keeps_input_order(
    radar_data_dir: Path, fake_fastembed: dict[str, Any]
) -> None:
    _write_fake_model_cache(_fastembed_cache(radar_data_dir))
    emb = FastEmbedEmbedder()
    init = fake_fastembed["inits"][-1]
    assert init["local_files_only"] is True and init["cache_dir"] == str(_fastembed_cache(radar_data_dir))
    assert init["env_offline"] == "1" and init["const_offline"] is True
    rng = np.random.default_rng(7)
    texts = [f"Test chunk {i} " + "word " * int(rng.integers(0, 60)) for i in range(75)]  # > 2 batches
    out = emb.embed_passages(texts)
    dim = fastembed_backend.DEFAULT_SPEC.dim
    assert out.shape == (75, dim) and out.dtype == np.float32
    passage_prefix = fastembed_backend.DEFAULT_SPEC.passage_prefix
    expected = _unit_rows([passage_prefix + t for t in texts], dim)
    np.testing.assert_allclose(out, expected, rtol=1e-5, atol=1e-6)  # row i belongs to text i
    passage_calls = fake_fastembed["calls"]
    assert len(passage_calls) >= 3 and all(len(c[1]) <= fastembed_backend.BATCH_SIZE for c in passage_calls)
    assert sum(len(c[1]) for c in passage_calls) == 75
    q = emb.embed_queries(["Test question"])
    query_prefix = fastembed_backend.DEFAULT_SPEC.query_prefix
    assert query_prefix and fake_fastembed["calls"][-1][1] == [query_prefix + "Test question"]
    np.testing.assert_allclose(np.linalg.norm(q, axis=1), 1.0, rtol=1e-5)
    assert emb.embed_passages([]).shape == (0, dim)


# ---- the download job ------------------------------------------------------------------------------


def test_download_is_refused_offline(radar_data_dir: Path) -> None:
    assert os.environ["RADAR_OFFLINE"] == "1"
    with pytest.raises(Exception) as info:
        registry.run_download_model(Ctx(), FAST)
    assert type(info.value).__name__ in {"EgressBlocked", "ModelDownloadBlocked"}
    assert "RADAR_OFFLINE" in str(info.value)
    assert not _fastembed_cache(radar_data_dir).exists()


def test_download_of_hashing_or_unknown_models(set_env: Callable[..., None]) -> None:
    stats = registry.run_download_model(Ctx())  # default is local/hashing-v1 in tests: nothing to fetch
    assert stats == {"model_id": HASH, "dim": 1024, "downloaded": False, "download_bytes": 0}
    set_env(RADAR_OFFLINE="0")
    with pytest.raises(ValueError):
        registry.run_download_model(Ctx(), "nope/model")


def test_download_lifts_offline_mode_only_for_the_download_then_resets(
    set_env: Callable[..., None], radar_data_dir: Path, fake_fastembed: dict[str, Any]
) -> None:
    import huggingface_hub.constants as hf_constants

    set_env(RADAR_OFFLINE="0", RADAR_EMBEDDER=FAST)
    assert registry.get_embedder().model_id == HASH  # fallback decided before the download
    ctx = Ctx()
    stats = registry.run_download_model(ctx)

    download, load = fake_fastembed["inits"]
    assert download["local_files_only"] is False and download["lazy_load"] is True
    assert download["env_offline"] == "0" and download["const_offline"] is False
    assert download["cache_dir"] == str(_fastembed_cache(radar_data_dir))
    assert load["local_files_only"] is True and load["env_offline"] == "1" and load["const_offline"] is True
    assert os.environ["HF_HUB_OFFLINE"] == "1" and hf_constants.HF_HUB_OFFLINE is True

    assert stats["model_id"] == FAST and stats["dim"] == FAST_DIM
    assert stats["downloaded"] is True and stats["download_bytes"] > 0
    assert stats["cache_bytes"] >= stats["download_bytes"] and stats["seconds"] >= 0
    assert ctx.progress_calls[0][0] == 0.05 and ctx.progress_calls[-1] == (1.0, "Model ready")
    # reset() ran: the model is now the active default
    emb = registry.get_embedder()
    assert isinstance(emb, FastEmbedEmbedder) and emb.model_id == FAST
    assert registry.fallback_active() is False and registry.model_cached() is True


def test_download_failure_is_reported_and_offline_mode_restored(
    set_env: Callable[..., None], monkeypatch: pytest.MonkeyPatch
) -> None:
    import fastembed
    import huggingface_hub.constants as hf_constants

    class Unreachable:
        list_supported_models = fastembed.TextEmbedding.list_supported_models

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ValueError("Could not load model BAAI/bge-small-en-v1.5 from any source.")

    monkeypatch.setattr(fastembed, "TextEmbedding", Unreachable)
    set_env(RADAR_OFFLINE="0", RADAR_EMBEDDER=FAST)
    with pytest.raises(registry.ModelDownloadFailed) as info:
        registry.run_download_model(Ctx())
    assert info.value.code == "source_unavailable"  # the job runner stores ApiError code + message
    assert "http" not in info.value.message.lower()
    assert os.environ["HF_HUB_OFFLINE"] == "1" and hf_constants.HF_HUB_OFFLINE is True
    assert registry.get_embedder().model_id == HASH


def test_download_job_through_the_runner_offline_is_egress_blocked(radar_data_dir: Path) -> None:
    """SPEC §16 maps EgressBlocked to ``egress_blocked``; the refusal must reach the job row that way."""
    from radar.jobs import runner

    try:
        with pytest.raises(runner.JobFailed) as info:
            runner.run_job_sync("download_model", params={"model_id": FAST})
    except NotImplementedError:  # pragma: no cover - runner still a stub
        pytest.skip("job runner not implemented yet")
    assert info.value.error_code == "egress_blocked"
    assert "RADAR_OFFLINE" in info.value.error_message
    assert not _fastembed_cache(radar_data_dir).exists()


# ---- real model (opt-in) -------------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("RADAR_TEST_NETWORK") != "1",
    reason="downloads the real model from Hugging Face; set RADAR_TEST_NETWORK=1 to run",
)
def test_real_fastembed_model_download_and_embed(set_env: Callable[..., None]) -> None:
    set_env(RADAR_OFFLINE="0", RADAR_EMBEDDER=FAST)
    stats = registry.run_download_model(Ctx())
    assert stats["model_id"] == FAST and stats["dim"] == FAST_DIM
    emb = registry.get_embedder()
    assert emb.model_id == FAST and not registry.fallback_active()
    vecs = emb.embed_passages(
        [
            "Graph neural networks for citation recommendation",
            "Recommending citations with graph neural networks",
            "A field guide to sourdough bread baking",
        ]
    )
    assert vecs.shape == (3, FAST_DIM)
    np.testing.assert_allclose(np.linalg.norm(vecs, axis=1), 1.0, rtol=1e-5)
    assert float(vecs[0] @ vecs[1]) > float(vecs[0] @ vecs[2])
    query = emb.embed_queries(["citation recommendation"])[0]
    assert float(query @ vecs[0]) > float(query @ vecs[2])


def test_batches_cap_texts_and_estimated_tokens() -> None:
    """Short texts go 32 per call; full-length abstracts only a few, so attention memory stays bounded."""
    short = fastembed_backend.batches([40] * 70)
    assert [len(b) for b in short] == [32, 32, 6]
    long = fastembed_backend.batches([4000] * 20)  # ~1000 tokens each, capped at 512
    assert all(len(b) * 512 <= fastembed_backend.TOKEN_BUDGET for b in long) and sum(map(len, long)) == 20
    mixed = fastembed_backend.batches([3000, 20, 2500, 30])
    assert [i for b in mixed for i in b] == [1, 3, 2, 0]  # sorted by length
    assert fastembed_backend.batches([]) == []


def test_every_supported_model_has_its_instructions() -> None:
    """bge models search with their query instruction; the stored model ids name the model."""
    for model_id, spec in fastembed_backend.SPECS.items():
        assert model_id == f"fastembed/{spec.name}" and spec.dim > 0
        assert 0 < spec.similarity_threshold < 1
    assert (
        fastembed_backend.BGE_SMALL.query_prefix
        == "Represent this sentence for searching relevant passages: "
    )
    assert fastembed_backend.BGE_SMALL.passage_prefix == ""
