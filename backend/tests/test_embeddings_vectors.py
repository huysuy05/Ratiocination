"""Vector storage: ensure_* idempotency and re-embedding, C3 mismatch errors, get_vectors, delete_vectors,
and the transaction rule that embedding never holds the write lock (SPEC §9, §2.1)."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

import numpy as np
import pytest
from sqlalchemy import select

from radar import models
from radar.db import holding_write_transaction, session_scope
from radar.embeddings import registry
from radar.embeddings import vectors as vectors_module
from radar.embeddings.base import content_hash, paper_text
from radar.embeddings.hashing import HashingEmbedder
from radar.embeddings.vectors import (
    delete_vectors,
    ensure_paper_vectors,
    ensure_vectors,
    get_vectors,
)

HASH = registry.HASHING_MODEL_ID
FAKE = "test/fake-8"


class SpyEmbedder:
    """8-d deterministic embedder that records each call and whether a write lock was held."""

    max_tokens = 512
    similarity_threshold = 0.5

    def __init__(self, model_id: str = FAKE, dim: int = 8) -> None:
        self.model_id = model_id
        self.dim = dim
        self.calls: list[tuple[str, list[str], bool]] = []

    def _rows(self, texts: list[str]) -> np.ndarray:
        rows = [
            np.random.default_rng(int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)).normal(size=self.dim)
            for t in texts
        ]
        m = np.asarray(rows, dtype=np.float64).reshape(len(texts), self.dim)
        return (m / np.linalg.norm(m, axis=1, keepdims=True)).astype(np.float32)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        self.calls.append(("passages", list(texts), holding_write_transaction()))
        return self._rows(texts)

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        self.calls.append(("queries", list(texts), holding_write_transaction()))
        return self._rows(texts)


@pytest.fixture
def spy(monkeypatch: pytest.MonkeyPatch) -> SpyEmbedder:
    emb = SpyEmbedder()
    monkeypatch.setitem(registry._FACTORIES, FAKE, lambda: emb)
    return emb


def _rows(entity_type: str = "paper", model_id: str | None = None) -> dict[int, models.Embedding]:
    with session_scope() as s:
        stmt = select(models.Embedding).where(models.Embedding.entity_type == entity_type)
        if model_id is not None:
            stmt = stmt.where(models.Embedding.model_id == model_id)
        return {row.entity_id: row for row in s.scalars(stmt)}


# ---- ensure_paper_vectors / ensure_vectors ---------------------------------------------------------


def test_ensure_paper_vectors_embeds_once(make_paper: Callable[..., int]) -> None:
    ids = [make_paper(title=f"Test paper {c}", abstract=f"Synthetic abstract {c}.") for c in "ABC"]
    with session_scope(write=True) as s:
        assert ensure_paper_vectors(s, ids + [999_999], HASH) == 3  # unknown ids are skipped
    with session_scope(write=True) as s:
        assert ensure_paper_vectors(s, ids, HASH) == 0  # idempotent
    rows = _rows(model_id=HASH)
    assert set(rows) == set(ids)
    expected = HashingEmbedder().embed_passages(
        [paper_text(f"Test paper {c}", f"Synthetic abstract {c}.") for c in "ABC"]
    )
    for pid, c, vec in zip(ids, "ABC", expected, strict=True):
        row = rows[pid]
        assert row.model_id == HASH and row.dimension == 1024 and len(row.vector) == 4096
        assert row.content_hash == content_hash(paper_text(f"Test paper {c}", f"Synthetic abstract {c}."))
        stored = np.frombuffer(row.vector, dtype="<f4")
        np.testing.assert_array_equal(stored, vec)
        assert abs(float(np.linalg.norm(stored)) - 1.0) < 1e-6


def test_re_embeds_only_when_the_text_changes(make_paper: Callable[..., int]) -> None:
    a = make_paper(title="Test paper A", abstract="First abstract.")
    b = make_paper(title="Test paper B", abstract="Other abstract.")
    with session_scope(write=True) as s:
        ensure_paper_vectors(s, [a, b], HASH)
    before = _rows(model_id=HASH)
    with session_scope(write=True) as s:
        s.get(models.Paper, a).abstract = "Revised abstract with new content."
    with session_scope(write=True) as s:
        assert ensure_paper_vectors(s, [a, b], HASH) == 1
    after = _rows(model_id=HASH)
    assert after[a].content_hash == content_hash(
        paper_text("Test paper A", "Revised abstract with new content.")
    )
    assert after[a].vector != before[a].vector and after[a].id == before[a].id  # updated in place
    assert after[b].vector == before[b].vector and after[b].created_at == before[b].created_at
    assert len(after) == 2


def test_each_model_keeps_its_own_vectors(make_paper: Callable[..., int], spy: SpyEmbedder) -> None:
    pid = make_paper(title="Test paper A")
    with session_scope(write=True) as s:
        assert ensure_paper_vectors(s, [pid], HASH) == 1
        assert ensure_paper_vectors(s, [pid], FAKE) == 1  # model change -> a separate vector
    with session_scope(write=True) as s:
        assert ensure_paper_vectors(s, [pid], FAKE) == 0
        assert ensure_paper_vectors(s, [pid], HASH) == 0
    with session_scope() as s:
        m_hash, ids_hash = get_vectors(s, "paper", [pid], HASH)
        m_fake, ids_fake = get_vectors(s, "paper", [pid], FAKE)
    assert ids_hash == ids_fake == [pid]
    assert m_hash.shape == (1, 1024) and m_fake.shape == (1, 8)


def test_ensure_vectors_query_mode_duplicates_and_chunks(spy: SpyEmbedder) -> None:
    with session_scope(write=True) as s:
        assert ensure_vectors(s, "project", [(7, "Test question?")], FAKE, query=True) == 1
        assert ensure_vectors(s, "chunk", [(1, "Chunk one"), (2, "Chunk two"), (1, "Chunk one")], FAKE) == 2
        assert ensure_vectors(s, "chunk", [], FAKE) == 0
    assert [c[0] for c in spy.calls] == ["queries", "passages"]
    assert spy.calls[1][1] == ["Chunk one", "Chunk two"]  # duplicate with the same text embedded once
    with session_scope(write=True) as s, pytest.raises(ValueError):
        ensure_vectors(s, "chunk", [(3, "one text"), (3, "another text")], FAKE)
    with session_scope(write=True) as s, pytest.raises(ValueError):
        ensure_vectors(s, "document", [(1, "x")], FAKE)  # not an embeddings entity type
    assert set(_rows("chunk", FAKE)) == {1, 2} and set(_rows("project", FAKE)) == {7}


def test_embedding_never_holds_the_write_lock(make_paper: Callable[..., int], spy: SpyEmbedder) -> None:
    pid = make_paper(title="Test paper A")
    with session_scope(write=True) as s:
        assert ensure_paper_vectors(s, [pid], FAKE) == 1
    assert spy.calls[-1][2] is False  # state was read in a read session; the lock came after embedding
    with session_scope(write=True) as s:
        assert ensure_vectors(s, "chunk", [(5, "Test chunk")], FAKE) == 1
    assert spy.calls[-1][2] is False


def test_ensure_sees_rows_inserted_by_the_same_uncommitted_session(spy: SpyEmbedder) -> None:
    with session_scope(write=True) as s:
        paper = models.Paper(
            canonical_title="Test paper uncommitted",
            title_norm="test paper uncommitted",
            ingested_via="arxiv_feed",
            first_seen_at=models.utcnow(),
        )
        s.add(paper)
        s.flush()
        assert ensure_paper_vectors(s, [paper.id], FAKE) == 1
        pid = paper.id
    assert spy.calls[-1][1] == ["Test paper uncommitted"]
    assert set(_rows(model_id=FAKE)) == {pid}


def test_fresh_sessions_commit_per_batch_and_keep_finished_batches(
    spy: SpyEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vectors_module, "WRITE_BATCH", 2)
    original = spy.embed_passages
    failed: list[bool] = []

    def flaky(texts: list[str]) -> np.ndarray:
        if len(spy.calls) == 2 and not failed:  # the third batch fails once (e.g. an interrupted job)
            failed.append(True)
            raise RuntimeError("embedding interrupted")
        return original(texts)

    monkeypatch.setattr(spy, "embed_passages", flaky)
    items = [(i, f"Test chunk {i}") for i in range(1, 6)]
    with pytest.raises(RuntimeError), session_scope(write=True) as s:
        ensure_vectors(s, "chunk", items, FAKE)
    assert [len(c[1]) for c in spy.calls] == [2, 2]
    assert [c[2] for c in spy.calls] == [False, False]  # each batch embedded with no write lock held
    assert set(_rows("chunk", FAKE)) == {1, 2, 3, 4}  # finished batches stay committed
    with session_scope(write=True) as s:
        assert ensure_vectors(s, "chunk", items, FAKE) == 1  # resumes with what is missing

    # inside a caller's transaction nothing is committed by ensure_*: a failure rolls everything back
    spy.calls.clear()
    with pytest.raises(RuntimeError), session_scope(write=True) as s:
        s.add(
            models.Embedding(
                entity_type="chunk",
                entity_id=99,
                model_id=FAKE,
                dimension=8,
                vector=b"\0" * 32,
                content_hash="h",
            )
        )
        s.flush()
        ensure_vectors(
            s, "chunk", [(10, "Test chunk 10"), (11, "Test chunk 11"), (12, "Test chunk 12")], FAKE
        )
        raise RuntimeError("caller fails after embedding")
    assert [len(c[1]) for c in spy.calls] == [3]  # one call, not split into commit batches
    assert set(_rows("chunk", FAKE)) == {1, 2, 3, 4, 5}


# ---- C3: model / dimension mismatches ------------------------------------------------------------


def test_requesting_an_unavailable_or_unknown_model_raises(make_paper: Callable[..., int]) -> None:
    pid = make_paper()
    with session_scope(write=True) as s:
        with pytest.raises(ValueError, match="not available"):
            # not downloaded in tests: get_embedder would hand out the hashing fallback instead
            ensure_paper_vectors(s, [pid], registry.FASTEMBED_MODEL_ID)
        with pytest.raises(ValueError):
            ensure_vectors(s, "paper", [(pid, "text")], "nope/model")
        with pytest.raises(ValueError):
            ensure_vectors(s, "paper", [(pid, "text")], "")
    assert _rows() == {}


def test_a_stored_vector_with_another_dimension_raises(make_paper: Callable[..., int]) -> None:
    pid = make_paper(title="Test paper A")
    with session_scope(write=True) as s:
        s.add(
            models.Embedding(
                entity_type="paper",
                entity_id=pid,
                model_id=HASH,
                dimension=3,
                vector=np.ones(3, dtype="<f4").tobytes(),
                content_hash="x" * 64,
            )
        )
    with session_scope(write=True) as s, pytest.raises(ValueError, match="dimension"):
        ensure_paper_vectors(s, [pid], HASH)
    with session_scope() as s, pytest.raises(ValueError, match="dimension"):
        get_vectors(s, "paper", [pid], HASH)


# ---- get_vectors -------------------------------------------------------------------------------------


def test_get_vectors_follows_the_requested_order_and_skips_missing_ids(spy: SpyEmbedder) -> None:
    items = [(i, f"Test chunk {i}") for i in (10, 11, 12, 13)]
    with session_scope(write=True) as s:
        ensure_vectors(s, "chunk", items, FAKE)
    with session_scope() as s:
        matrix, ids = get_vectors(s, "chunk", [13, 99, 10, 12, 10], FAKE)
        empty, none = get_vectors(s, "chunk", [], FAKE)
        other_type, other_ids = get_vectors(s, "paper", [10, 11], FAKE)
        unknown, unknown_ids = get_vectors(s, "chunk", [10], "test/never-stored")
    assert ids == [13, 10, 12]
    assert matrix.shape == (3, 8) and matrix.dtype == np.float32
    expected = spy._rows([f"Test chunk {i}" for i in ids])
    np.testing.assert_allclose(matrix, expected, rtol=1e-6)
    assert empty.shape == (0, 8) and none == []
    assert other_type.shape == (0, 8) and other_ids == []
    assert unknown.shape == (0, 0) and unknown_ids == []


# ---- delete_vectors -----------------------------------------------------------------------------


def test_delete_vectors_removes_every_model_for_the_entities(spy: SpyEmbedder) -> None:
    with session_scope(write=True) as s:
        ensure_vectors(s, "paper", [(1, "Test paper one"), (2, "Test paper two")], HASH)
        ensure_vectors(s, "paper", [(1, "Test paper one")], FAKE)
        ensure_vectors(s, "chunk", [(1, "Test chunk one")], FAKE)
    with session_scope(write=True) as s:
        assert delete_vectors(s, "paper", [1, 1, 42]) == 2  # HASH + FAKE rows of paper 1
    with session_scope(write=True) as s:
        assert delete_vectors(s, "paper", [1]) == 0
        assert delete_vectors(s, "paper", []) == 0
    assert set(_rows("paper")) == {2}
    assert set(_rows("chunk")) == {1}  # same id, other entity type: untouched


def test_content_hash_covers_the_model_instruction(spy: SpyEmbedder) -> None:
    """The stored hash is of what the model saw (prefix + text): a model that starts using a query
    instruction re-embeds the stored questions, while passages without a prefix keep their vectors."""
    with session_scope(write=True) as s:
        assert ensure_vectors(s, "project", [(1, "Test question")], FAKE, query=True) == 1
        assert ensure_vectors(s, "paper", [(7, "Test paper text")], FAKE) == 1
    assert _rows("project", FAKE)[1].content_hash == content_hash("Test question")
    spy.query_prefix = "Q: "  # type: ignore[attr-defined]
    with session_scope(write=True) as s:
        assert ensure_vectors(s, "project", [(1, "Test question")], FAKE, query=True) == 1  # re-embedded
        assert ensure_vectors(s, "paper", [(7, "Test paper text")], FAKE) == 0  # passages unchanged
        assert ensure_vectors(s, "project", [(1, "Test question")], FAKE, query=True) == 0
    assert _rows("project", FAKE)[1].content_hash == content_hash("Q: Test question")
    assert spy.calls[-1] == ("queries", ["Test question"], False)  # the embedder adds its own prefix
