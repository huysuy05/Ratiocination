"""VectorIndex: brute-force equivalence, restrict/exclude, ties, lazy loading, after-commit invalidation
and thread safety (SPEC §9)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from radar.db import session_scope
from radar.embeddings import registry
from radar.embeddings.index import VectorIndex
from radar.embeddings.vectors import delete_vectors, ensure_paper_vectors, ensure_vectors, store_vectors

MODEL = "test/random-16"
DIM = 16


def _unit(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float64)
    return (m / np.linalg.norm(m, axis=-1, keepdims=True)).astype(np.float32)


def _store(ids: list[int], vectors: np.ndarray, model_id: str = MODEL) -> None:
    with session_scope(write=True) as s:
        store_vectors(s, "paper", ids, vectors, model_id, [f"h{i}" for i in ids])


def _brute_force(
    ids: np.ndarray, matrix: np.ndarray, query: np.ndarray, k: int, allowed: set[int] | None = None
) -> list[tuple[int, float]]:
    scores = matrix @ _unit(query)
    pairs = [
        (int(i), float(sc)) for i, sc in zip(ids, scores, strict=True) if allowed is None or int(i) in allowed
    ]
    pairs.sort(key=lambda p: (-p[1], p[0]))
    return pairs[:k]


@pytest.fixture
def corpus() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(42)
    ids = np.arange(1, 301, dtype=np.int64) * 3  # non-contiguous paper ids
    matrix = _unit(rng.normal(size=(ids.size, DIM)))
    _store([int(i) for i in ids], matrix)
    return ids, matrix


@pytest.fixture
def load_counter(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    loads: list[str] = []
    original = VectorIndex._load

    def counting(self: VectorIndex, epoch: int):  # type: ignore[no-untyped-def]
        loads.append(self.model_id)
        return original(self, epoch)

    monkeypatch.setattr(VectorIndex, "_load", counting)
    yield loads


def _ids(results: list[tuple[int, float]]) -> list[int]:
    return [pid for pid, _ in results]


# ---- search semantics --------------------------------------------------------------------------------


def test_search_equals_brute_force(corpus: tuple[np.ndarray, np.ndarray]) -> None:
    ids, matrix = corpus
    index = VectorIndex.get(MODEL)
    assert VectorIndex.get(MODEL) is index
    assert len(index) == ids.size
    rng = np.random.default_rng(7)
    for k in (1, 5, 50, 300, 1000):
        query = rng.normal(size=DIM) * 5.0  # not normalized: the index normalizes it
        got = index.search(query, k)
        want = _brute_force(ids, matrix, query, k)
        assert _ids(got) == _ids(want)
        np.testing.assert_allclose([s for _, s in got], [s for _, s in want], rtol=1e-5, atol=1e-6)
    assert len(index.search(rng.normal(size=DIM), 1000)) == ids.size
    two_d = index.search(matrix[:1], 3)  # a (1, dim) query works too
    assert two_d[0][0] == int(ids[0]) and abs(two_d[0][1] - 1.0) < 1e-5


def test_restrict_and_exclude(corpus: tuple[np.ndarray, np.ndarray]) -> None:
    ids, matrix = corpus
    index = VectorIndex.get(MODEL)
    query = np.random.default_rng(1).normal(size=DIM)
    restrict = {int(i) for i in ids[::7]} | {999_999}  # unknown ids are ignored
    exclude = {int(i) for i in ids[::14]}
    got = index.search(query, 10, restrict_ids=restrict)
    want = _brute_force(ids, matrix, query, 10, restrict - {999_999})
    assert _ids(got) == _ids(want)
    np.testing.assert_allclose([sc for _, sc in got], [sc for _, sc in want], rtol=1e-5, atol=1e-6)
    got = index.search(query, 10, restrict_ids=restrict, exclude_ids=exclude)
    assert _ids(got) == _ids(_brute_force(ids, matrix, query, 10, restrict - exclude))
    got = index.search(query, 20, exclude_ids=exclude)
    assert _ids(got) == _ids(_brute_force(ids, matrix, query, 20, {int(i) for i in ids} - exclude))
    assert index.search(query, 10, restrict_ids=set()) == []
    assert index.search(query, 10, restrict_ids={int(ids[0])}, exclude_ids={int(ids[0])}) == []
    assert index.search(query, 0) == [] and index.search(query, -1) == []


def test_ties_are_ordered_by_paper_id() -> None:
    v = _unit(np.ones((1, DIM)))[0]
    _store([30, 10, 20], np.vstack([v, v, v]))
    results = VectorIndex.get(MODEL).search(v, 2)
    assert _ids(results) == [10, 20]  # every tie at the boundary is considered
    assert all(abs(score - 1.0) < 1e-6 for _, score in results)


def test_empty_index_and_dimension_mismatch(corpus: tuple[np.ndarray, np.ndarray]) -> None:
    assert VectorIndex.get("test/no-vectors").search(np.ones(DIM), 5) == []
    assert len(VectorIndex.get("test/no-vectors")) == 0
    with pytest.raises(ValueError, match="dimension"):
        VectorIndex.get(MODEL).search(np.ones(DIM + 1), 5)
    with pytest.raises(ValueError):
        VectorIndex.get(MODEL).search(np.full(DIM, np.nan), 5)
    with pytest.raises(ValueError):
        VectorIndex.get(MODEL).search(np.ones((2, DIM)), 5)
    with pytest.raises(ValueError):
        VectorIndex.get("")


def test_one_index_per_model_and_paper_vectors_only() -> None:
    rng = np.random.default_rng(5)
    _store([1, 2], _unit(rng.normal(size=(2, DIM))))
    _store([1, 2, 3], _unit(rng.normal(size=(3, 8))), model_id="test/other-8")
    with session_scope(write=True) as s:  # chunk vectors are never indexed
        store_vectors(s, "chunk", [4, 5], _unit(rng.normal(size=(2, DIM))), MODEL, ["c4", "c5"])
    assert len(VectorIndex.get(MODEL)) == 2
    assert len(VectorIndex.get("test/other-8")) == 3
    assert set(_ids(VectorIndex.get(MODEL).search(rng.normal(size=DIM), 10))) == {1, 2}


def test_index_over_real_paper_embeddings() -> None:
    with session_scope(write=True) as s:
        ensure_vectors(
            s,
            "paper",
            [
                (1, "Test paper A: graph neural networks for citation recommendation"),
                (2, "Test paper B: protein folding with diffusion models"),
                (3, "Test paper C: citation recommendation using graph neural networks"),
            ],
            registry.HASHING_MODEL_ID,
        )
    emb = registry.get_embedder()
    query = emb.embed_queries(["graph neural networks citation recommendation"])[0]
    results = VectorIndex.get(emb.model_id).search(query, 3)
    assert set(_ids(results[:2])) == {1, 3} and results[-1][0] == 2


# ---- laziness and invalidation ---------------------------------------------------------------------


def test_loads_lazily_once_and_reloads_after_commit(
    corpus: tuple[np.ndarray, np.ndarray], load_counter: list[str]
) -> None:
    index = VectorIndex.get(MODEL)
    assert load_counter == []  # lazy
    query = np.ones(DIM)
    index.search(query, 5)
    index.search(query, 5)
    assert load_counter == [MODEL]

    new_vector = _unit(query[None, :])  # the best possible match
    with session_scope(write=True) as s:
        store_vectors(s, "paper", [5000], new_vector, MODEL, ["new"])
        assert 5000 not in _ids(index.search(query, 5))  # not committed yet: the old snapshot is used
    assert load_counter == [MODEL]
    assert index.search(query, 1)[0][0] == 5000  # after commit the hook invalidated the index
    assert load_counter == [MODEL, MODEL]

    with session_scope(write=True) as s:
        assert delete_vectors(s, "paper", [5000]) == 1
    assert 5000 not in _ids(index.search(query, 5))
    assert len(load_counter) == 3


def test_rolled_back_and_non_paper_writes_do_not_invalidate(
    corpus: tuple[np.ndarray, np.ndarray], load_counter: list[str]
) -> None:
    index = VectorIndex.get(MODEL)
    query = np.ones(DIM)
    index.search(query, 5)
    with pytest.raises(RuntimeError), session_scope(write=True) as s:
        store_vectors(s, "paper", [6000], _unit(query[None, :]), MODEL, ["rolled back"])
        raise RuntimeError("abort the transaction")
    with session_scope(write=True) as s:
        store_vectors(s, "chunk", [6000], _unit(query[None, :]), MODEL, ["chunk"])
    assert 6000 not in _ids(index.search(query, 5))
    assert load_counter == [MODEL]


def test_ensure_paper_vectors_invalidates_its_model_only(
    make_paper: Callable[..., int], load_counter: list[str]
) -> None:
    _store([1], _unit(np.ones((1, DIM))))
    other = VectorIndex.get(MODEL)
    other.search(np.ones(DIM), 1)
    hashing = VectorIndex.get(registry.HASHING_MODEL_ID)
    assert hashing.search(np.ones(1024), 5) == []
    pid = make_paper(title="Test paper A")
    with session_scope(write=True) as s:
        ensure_paper_vectors(s, [pid], registry.HASHING_MODEL_ID)
    assert _ids(hashing.search(np.ones(1024), 5)) == [pid]
    other.search(np.ones(DIM), 1)
    assert load_counter == [MODEL, registry.HASHING_MODEL_ID, registry.HASHING_MODEL_ID]


def test_explicit_invalidate_and_clear_all(
    corpus: tuple[np.ndarray, np.ndarray], load_counter: list[str]
) -> None:
    index = VectorIndex.get(MODEL)
    index.search(np.ones(DIM), 1)
    VectorIndex.invalidate("test/some-other-model")
    index.search(np.ones(DIM), 1)
    assert len(load_counter) == 1
    VectorIndex.invalidate(MODEL)
    index.search(np.ones(DIM), 1)
    assert len(load_counter) == 2
    VectorIndex.invalidate()
    index.search(np.ones(DIM), 1)
    assert len(load_counter) == 3
    VectorIndex.clear_all()
    assert VectorIndex.get(MODEL) is not index
    index.search(np.ones(DIM), 1)  # an old reference is also refreshed, never stale
    assert len(load_counter) == 4


def test_an_invalidation_during_a_load_is_not_lost(
    corpus: tuple[np.ndarray, np.ndarray], monkeypatch: pytest.MonkeyPatch
) -> None:
    loads: list[int] = []
    original = VectorIndex._load

    def racing(self: VectorIndex, epoch: int):  # type: ignore[no-untyped-def]
        snapshot = original(self, epoch)
        loads.append(epoch)
        if len(loads) == 1:
            VectorIndex.invalidate(self.model_id)  # a commit lands while the first load runs
        return snapshot

    monkeypatch.setattr(VectorIndex, "_load", racing)
    index = VectorIndex.get(MODEL)
    index.search(np.ones(DIM), 1)
    index.search(np.ones(DIM), 1)  # must reload: the first snapshot may predate the commit
    index.search(np.ones(DIM), 1)
    assert len(loads) == 2 and loads[1] > loads[0]


def test_concurrent_searches_and_invalidations(corpus: tuple[np.ndarray, np.ndarray]) -> None:
    ids, matrix = corpus
    index = VectorIndex.get(MODEL)
    queries = np.random.default_rng(11).normal(size=(40, DIM))
    expected = [_ids(_brute_force(ids, matrix, q, 7)) for q in queries]
    stop = threading.Event()

    def invalidator() -> None:
        while not stop.is_set():
            VectorIndex.invalidate(MODEL)
            stop.wait(0.002)

    thread = threading.Thread(target=invalidator)
    thread.start()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda q: _ids(index.search(q, 7)), list(queries) * 3))
    finally:
        stop.set()
        thread.join()
    assert results == expected * 3
