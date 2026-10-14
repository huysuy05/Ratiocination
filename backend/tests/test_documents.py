"""Private documents (SPEC §10, §17 test 4): validation, extraction, sections, chunking, processing,
reconciliation, deletion with evidence scrubbing, and project deletion."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import inspect, text

from radar.config import get_settings
from radar.db import get_engine, session_scope
from radar.documents.chunking import chunk_text
from radar.documents.extract import ExtractedText, ExtractionError, Line, extract_pdf, extract_plain
from radar.documents.sections import detect_sections
from radar.documents.storage import sanitize_filename, validate_upload
from radar.errors import ApiError
from radar.models import (
    Document,
    DocumentChunk,
    Embedding,
    Job,
    RecommendationItem,
    RecommendationSlate,
    utcnow,
)

PARAGRAPH = " ".join(f"Sentence {i} about calibrated research feeds." for i in range(18))  # ~108 words


def make_pdf(path: Path, pages: list[list[tuple[str, float]]]) -> bytes:
    doc = pymupdf.open()
    for blocks in pages:
        page = doc.new_page()
        y = 72.0
        for content, size in blocks:
            page.insert_textbox(pymupdf.Rect(72, y, 540, y + 420), content, fontsize=size, fontname="helv")
            y += (len(content) * size * 0.5 / 468 + 1) * size * 1.3 + 14
    doc.save(path)
    doc.close()
    return path.read_bytes()


def paper_pdf(tmp_path: Path, extra: str = "") -> bytes:
    return make_pdf(
        tmp_path / "paper.pdf",
        [
            [("Test paper A: Calibrated Research Feeds", 20), ("Abstract", 13), (PARAGRAPH + extra, 10)],
            [("1 Introduction", 13), (PARAGRAPH, 10), ("References", 13), ("[1] Test reference. 2024.", 10)],
        ],
    )


# ------------------------------------------------------------------------------------------ validation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\draft v2.pdf", "draft v2.pdf"),
        ("  ", "document"),
        (None, "document"),
        ("a" * 300 + ".pdf", "a" * 196 + ".pdf"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_validate_upload_checks_type_by_content():
    assert validate_upload("draft.md", b"# Draft\n\nText").mime_type == "text/markdown"
    for name, data, status in (
        ("draft.exe", b"MZ", 415),
        ("draft.pdf", b"not a pdf", 415),
        ("draft.txt", b"text\x00with nul", 415),
        ("draft.txt", "caf\u00e9".encode("latin-1"), 415),
        ("draft.txt", b"", 422),
    ):
        with pytest.raises(ApiError) as info:
            validate_upload(name, data)
        assert info.value.status == status


# -------------------------------------------------------------------------------------------- chunking


def test_chunks_are_exact_slices_with_overlap_and_no_tiny_tail():
    body = " ".join(f"w{i}" for i in range(1000))
    chunks = chunk_text(body, base_offset=500)
    words = [len(c.text.split()) for c in chunks]
    assert all(c.text == body[c.char_start - 500 : c.char_end - 500] for c in chunks)
    assert words[:-1] == [220] * (len(words) - 1) and words[-1] <= 320
    # 40 words overlap: chunk 2 starts with the last 40 words of chunk 1
    assert chunks[1].text.split()[:40] == chunks[0].text.split()[-40:]
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert chunks[0].token_count == round(220 * 1.3)


def test_chunks_prefer_paragraph_then_sentence_boundaries():
    para_a = " ".join(["alpha"] * 180)
    para_b = " ".join(["beta"] * 200)
    chunks = chunk_text(f"{para_a}\n\n{para_b}")
    assert chunks[0].text == para_a  # cut at the paragraph break (180 >= 60% of 220)
    sentences = " ".join(f"word{i}." if i % 50 == 49 else f"word{i}" for i in range(400))
    first = chunk_text(sentences)[0].text
    assert first.endswith(".") and len(first.split()) == 200  # the sentence end closest to 220


def test_short_text_is_one_chunk_and_bad_parameters_fail():
    assert [c.text for c in chunk_text("just a few words")] == ["just a few words"]
    assert chunk_text("   ") == []
    with pytest.raises(ValueError):
        chunk_text("x", target=10, max_words=5)


# -------------------------------------------------------------------------------------------- sections


def _plain(content: str, kind: str = "text") -> ExtractedText:
    return extract_plain(content.encode(), kind)


def test_markdown_and_text_sections():
    md = detect_sections(
        _plain(
            "# Title\n\nIntro text here long enough.\n\n## Method\n\nWe do things.\n\n## References\n\n[1] x",
            "markdown",
        )
    )
    assert [(s.heading, s.is_references) for s in md] == [
        ("Title", False),
        ("Method", False),
        ("References", True),
    ]
    txt = detect_sections(
        _plain(
            "Preamble words before any heading.\n\n2 Related Work\n\nPrior work text.\n\nConclusion\n\nDone."
        )
    )
    assert [s.heading for s in txt] == ["Front matter", "2 Related Work", "Conclusion"]
    none = detect_sections(_plain("Only a paragraph of text without headings at all."))
    assert [s.heading for s in none] == ["Document"]


def test_sentences_starting_with_heading_words_are_not_headings():
    sections = detect_sections(_plain("Results show that the method works well on every benchmark we tried."))
    assert [s.heading for s in sections] == ["Document"]


def test_pdf_sections_by_font_size_with_pages(tmp_path):
    pdf = tmp_path / "paper.pdf"
    paper_pdf(tmp_path)
    extracted = extract_pdf(pdf)
    assert extracted.n_pages == 2
    sections = detect_sections(extracted)
    # The 20 pt title is a heading too, but its body is empty (Abstract follows at once): dropped.
    assert [s.heading for s in sections] == ["Abstract", "1 Introduction", "References"]
    intro = next(s for s in sections if s.heading == "1 Introduction")
    assert intro.page_start == 2 and not intro.is_references
    assert next(s for s in sections if s.heading == "References").is_references


def test_pdf_without_text_or_with_password_fails_cleanly(tmp_path):
    scanned = pymupdf.open()
    scanned.new_page().draw_rect(pymupdf.Rect(50, 50, 200, 200), fill=(0, 0, 0))
    scanned.save(tmp_path / "scan.pdf")
    with pytest.raises(ExtractionError, match="no extractable text"):
        extract_pdf(tmp_path / "scan.pdf")
    locked = pymupdf.open()
    locked.new_page().insert_text((72, 72), "secret text", fontsize=12)
    locked.save(tmp_path / "locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="u", owner_pw="o")
    with pytest.raises(ExtractionError, match="password"):
        extract_pdf(tmp_path / "locked.pdf")


def test_page_at_maps_offsets_to_pages():
    lines = [Line("a", 0, 1, 1, None), Line("b", 3, 4, 2, None)]
    doc = ExtractedText(text="a\n\nb", lines=lines, n_pages=2, kind="pdf")
    assert [doc.page_at(o) for o in (0, 2, 3, 99)] == [1, 1, 2, 2]


# ----------------------------------------------------------------------------------------- API + jobs


def _upload(client, project_id: int, name: str, data: bytes):
    return client.post(
        f"/api/projects/{project_id}/documents", files={"file": (name, data, "application/octet-stream")}
    )


def test_upload_processes_pdf_locally(client, make_project, tmp_path):
    project_id = make_project()
    response = _upload(client, project_id, "draft.pdf", paper_pdf(tmp_path))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job"]["kind"] == "process_document" and body["job"]["params"] == {
        "document_id": body["document"]["id"]
    }
    status = client.get(f"/api/documents/{body['document']['id']}/status").json()
    assert status["status"] == "ready" and status["error"] is None
    assert status["n_pages"] == 2 and status["n_chunks"] >= 2 and "privacy_mode" not in status
    outline = {item["heading"]: item for item in status["outline"]}
    assert outline["References"]["n_chunks"] == 0 and outline["1 Introduction"]["page_start"] == 2
    with session_scope() as s:
        doc = s.get(Document, body["document"]["id"])
        chunk_ids = [c.id for c in s.query(DocumentChunk).filter_by(document_id=doc.id)]
        vectors = (
            s.query(Embedding)
            .filter(Embedding.entity_type == "chunk", Embedding.entity_id.in_(chunk_ids))
            .count()
        )
        assert vectors == len(chunk_ids) > 0
        path = get_settings().data_dir / doc.storage_uri
    assert path.parent.name == str(body["document"]["id"]) and path.suffix == ".pdf"
    assert (
        stat.S_IMODE(os.stat(path).st_mode) == 0o600 and stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    )
    listing = client.get(f"/api/projects/{project_id}/documents").json()
    assert [d["id"] for d in listing] == [body["document"]["id"]]


def test_duplicate_upload_conflicts_but_another_project_may_have_the_same_file(
    client, make_project, tmp_path
):
    a, b = make_project(), make_project()
    data = paper_pdf(tmp_path)
    first = _upload(client, a, "draft.pdf", data).json()["document"]["id"]
    again = _upload(client, a, "copy.pdf", data)
    assert again.status_code == 409 and again.json()["error"]["details"] == {"document_id": first}
    other = _upload(client, b, "draft.pdf", data)
    assert other.status_code == 202 and other.json()["document"]["id"] != first


def test_upload_limits_and_types(client, make_project, monkeypatch):
    project_id = make_project()
    assert _upload(client, project_id, "x.docx", b"PK..").status_code == 415
    monkeypatch.setenv("RADAR_MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    too_big = _upload(client, project_id, "big.txt", b"a" * (1024 * 1024 + 1))
    assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "payload_too_large"
    assert (
        client.post(
            "/api/projects/999/documents", files={"file": ("a.txt", b"hello there friend", "text/plain")}
        ).status_code
        == 404
    )


def test_scanned_pdf_is_visible_as_failed_and_deletable(client, make_project, tmp_path):
    project_id = make_project()
    scanned = pymupdf.open()
    scanned.new_page().draw_rect(pymupdf.Rect(50, 50, 200, 200), fill=(0, 0, 0))
    scanned.save(tmp_path / "scan.pdf")
    document_id = _upload(client, project_id, "scan.pdf", (tmp_path / "scan.pdf").read_bytes()).json()[
        "document"
    ]["id"]
    status = client.get(f"/api/documents/{document_id}/status").json()
    assert status["status"] == "failed" and "no extractable text" in status["error"]
    assert status["n_chunks"] == 0
    assert client.delete(f"/api/documents/{document_id}").status_code == 200


def test_processing_documents_cannot_be_deleted_and_interrupted_ones_are_repaired(client, make_project):
    project_id = make_project()
    with session_scope(write=True) as s:
        busy = Document(
            project_id=project_id,
            filename="a.txt",
            mime_type="text/plain",
            size_bytes=1,
            checksum="a" * 64,
            storage_uri="uploads/x/a.txt",
            status="processing",
        )
        stuck = Document(
            project_id=project_id,
            filename="b.txt",
            mime_type="text/plain",
            size_bytes=1,
            checksum="b" * 64,
            storage_uri="uploads/y/b.txt",
            status="processing",
        )
        s.add_all([busy, stuck])
        s.flush()
        s.add(
            Job(
                kind="process_document",
                status="failed",
                project_id=project_id,
                params_json={"document_id": stuck.id},
                dedupe_key="k" * 64,
                error_code="interrupted",
                retry_count=0,
            )
        )
        busy_id, stuck_id = busy.id, stuck.id
    conflict = client.delete(f"/api/documents/{busy_id}")
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "conflict"
    repaired = client.get(f"/api/documents/{stuck_id}/status").json()
    assert repaired["status"] == "failed" and repaired["error"].startswith("Processing was interrupted")


def test_delete_scrubs_evidence_and_leaves_no_trace(client, make_project, tmp_path):
    """SPEC §17 test 4: canaries in text, filename and heading are gone from every table and from
    the raw bytes of radar.db / radar.db-wal / uploads after DELETE."""
    project_id = make_project()
    content = f"# zqcanary03 heading\n\nThe draft says zqcanary01 many times. {PARAGRAPH}\n"
    document_id = _upload(client, project_id, "zqcanary02.md", content.encode()).json()["document"]["id"]
    assert client.get(f"/api/documents/{document_id}/status").json()["status"] == "ready"
    with session_scope(write=True) as s:
        chunk = s.query(DocumentChunk).filter_by(document_id=document_id).first()
        slate = RecommendationSlate(
            project_id=project_id,
            generated_at=utcnow(),
            mode="daily",
            ranker_version="test",
            embedding_model="local/hashing-v1",
            candidate_set_hash="h",
            pool_size=1,
            params_json={},
        )
        s.add(slate)
        s.flush()
        evidence = {
            "document_id": document_id,
            "chunk_id": chunk.id,
            "filename": "zqcanary02.md",
            "section": "zqcanary03 heading",
            "page": None,
            "snippet": "zqcanary01",
        }
        s.add(
            RecommendationItem(
                slate_id=slate.id,
                paper_id=1,
                rank=1,
                score=0.5,
                feature_scores_json={"draft": {"value": 1.0, "evidence": evidence}},
                explanation_json=[
                    {
                        "kind": "model",
                        "code": "draft_match",
                        "text": "Matches your document zqcanary02.md",
                        "evidence": evidence,
                        "contribution": 0.2,
                    }
                ],
            )
        )
        item_id = slate.id

    deleted = client.delete(f"/api/documents/{document_id}")
    assert deleted.status_code == 200, deleted.text
    counts = deleted.json()["deleted"]
    assert counts["file"] is True and counts["chunks"] >= 1 and counts["embeddings"] == counts["chunks"]
    assert counts["scrubbed_items"] == 1
    assert client.get(f"/api/documents/{document_id}/status").status_code == 404
    with session_scope() as s:
        item = s.query(RecommendationItem).filter_by(slate_id=item_id).one()
        assert item.explanation_json[0]["text"] == "Matched a private document that has since been deleted"
        assert item.explanation_json[0]["evidence"] == {"deleted_document": True}
        assert item.feature_scores_json["draft"]["evidence"] == {"deleted_document": True}

    # Every TEXT/JSON column of every table...
    engine = get_engine(write=False)
    with engine.connect() as conn:
        for table in inspect(engine).get_table_names():
            for row in conn.execute(text(f'SELECT * FROM "{table}"')):
                assert "zqcanary" not in repr(tuple(row)), table
    # ...and the raw bytes on disk.
    data_dir = get_settings().data_dir
    for name in ("radar.db", "radar.db-wal"):
        path = data_dir / name
        if path.exists():
            assert b"zqcanary" not in path.read_bytes(), name
    assert not (data_dir / "uploads" / str(document_id)).exists()


def test_project_delete_removes_all_private_data(client, make_project):
    project_id = make_project()
    other_id = make_project()
    document_id = _upload(client, project_id, "draft.md", f"# Draft\n\n{PARAGRAPH}".encode()).json()[
        "document"
    ]["id"]
    other_document_id = _upload(client, other_id, "other.md", f"# Other\n\n{PARAGRAPH}".encode()).json()[
        "document"
    ]["id"]
    with session_scope(write=True) as s:
        s.add(
            Embedding(
                entity_type="project",
                entity_id=project_id,
                model_id="local/hashing-v1",
                dimension=1,
                vector=b"\x00\x00\x80?",
                content_hash="h",
            )
        )
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    with session_scope() as s:
        assert [d.id for d in s.query(Document)] == [other_document_id]  # the other project is untouched
        assert s.query(DocumentChunk).filter_by(document_id=document_id).count() == 0
        assert s.query(Embedding).filter_by(entity_type="project", entity_id=project_id).count() == 0
        chunk_ids = [c.id for c in s.query(DocumentChunk)]
        assert s.query(Embedding).filter_by(entity_type="chunk").count() == len(chunk_ids)
    assert not (get_settings().data_dir / "uploads" / str(document_id)).exists()
    assert (get_settings().data_dir / "uploads" / str(other_document_id)).exists()


def test_documents_package_never_loads_network_modules():
    code = (
        "import sys, pkgutil, importlib, radar.documents as d\n"
        "[importlib.import_module(m.name) for m in pkgutil.walk_packages(d.__path__, 'radar.documents.')]\n"
        "bad = [m for m in sys.modules if m.startswith(('radar.net', 'radar.connectors', 'radar.ingestion', 'radar.jobs'))]\n"
        "print(bad); sys.exit(1 if bad else 0)\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ})
    assert result.returncode == 0, result.stdout + result.stderr
