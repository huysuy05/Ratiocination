"""Document processing and deletion (SPEC §10). Owner: docs.

Processing (job ``process_document``): extract -> sections -> chunks (the References section is not
chunked) -> local embeddings -> ``ready``; an :class:`ExtractionError` sets ``failed`` with a readable
reason and no text is invented. Embedding runs in a fresh session, so the write lock is not held while
vectors are computed. Job messages name documents by id, never by filename.

Deletion removes the bytes, sections, chunks, chunk vectors and private edges, and scrubs stored
recommendation evidence that points at the document (``{"deleted_document": true}``; ``draft_match``
reasons get a neutral text). Files are removed and the SQLite WAL is checkpointed only after the
deleting transaction commits, so a rollback never loses bytes and deleted text does not linger in
``radar.db-wal``. This package never imports network modules (SPEC §2).
"""

from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, delete, event, func, or_, select
from sqlalchemy.orm import Session

from radar import db
from radar.documents.chunking import chunk_text
from radar.documents.extract import ExtractionError, extract
from radar.documents.sections import detect_sections
from radar.documents.storage import KIND_BY_MIME, document_dir, remove_document_dir, resolve_path
from radar.embeddings.registry import get_embedder
from radar.embeddings.vectors import delete_vectors, ensure_vectors
from radar.errors import ApiError
from radar.models import (
    Document,
    DocumentChunk,
    DocumentSection,
    GraphEdge,
    Job,
    RecommendationItem,
    RecommendationSlate,
)

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

log = logging.getLogger(__name__)

EXTRACTION_VERSION = "pymupdf-1"
PROCESSING_STATES = ("uploaded", "processing")
INTERRUPTED_ERROR = "Processing was interrupted — delete and upload again"
UNEXPECTED_ERROR = "Processing failed unexpectedly — delete and upload again"
DELETED_DRAFT_TEXT = "Matched a private document that has since been deleted"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------ processing


def _set_failed(ctx: JobContext, document_id: int, message: str) -> None:
    with ctx.session(write=True) as session:
        doc = session.get(Document, document_id)
        if doc is not None:
            doc.status = "failed"
            doc.error = message


def _clear_derived(session: Session, document_id: int) -> None:
    """Remove a previous processing result (sections, chunks, chunk vectors)."""
    chunk_ids = list(
        session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id == document_id))
    )
    if chunk_ids:
        delete_vectors(session, "chunk", chunk_ids)
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    session.execute(delete(DocumentSection).where(DocumentSection.document_id == document_id))


def run_process_document(ctx: JobContext, document_id: int) -> dict[str, Any]:
    """Job ``process_document``: validate -> extract -> sections -> chunks -> local embeddings."""
    with ctx.session(write=True) as session:
        doc = session.get(Document, document_id)
        if doc is None:
            raise LookupError(f"document {document_id} not found")
        doc.status, doc.error = "processing", None
        storage_uri, mime_type = doc.storage_uri, doc.mime_type

    ctx.progress(0.1, f"Document {document_id}: extracting text")
    try:
        extracted = extract(resolve_path(storage_uri), KIND_BY_MIME[mime_type])
        ctx.progress(0.35, f"Document {document_id}: finding sections")
        sections = detect_sections(extracted)
        planned: list[tuple[int, Any]] = []  # (section ordinal, Chunk)
        for section in sections:
            if section.is_references:
                continue
            for piece in chunk_text(
                section.text, section.char_start, start_index=len(planned), page_at=extracted.page_at
            ):
                planned.append((section.ordinal, piece))
        if not planned:
            raise ExtractionError("No readable text was found outside the references")
    except ExtractionError as exc:
        _set_failed(ctx, document_id, str(exc))
        log.info("document %s failed extraction", document_id)
        return {"status": "failed", "error": str(exc)}
    except Exception:
        _set_failed(ctx, document_id, UNEXPECTED_ERROR)
        raise

    with ctx.session(write=True) as session:
        _clear_derived(session, document_id)
        section_rows: dict[int, DocumentSection] = {}
        for section in sections:
            row = DocumentSection(
                document_id=document_id,
                ordinal=section.ordinal,
                heading=section.heading,
                page_start=section.page_start,
                page_end=section.page_end,
                char_start=section.char_start,
                char_end=section.char_end,
                text=section.text,
                text_hash=_sha256(section.text),
                extraction_version=EXTRACTION_VERSION,
            )
            session.add(row)
            section_rows[section.ordinal] = row
        session.flush()
        chunk_rows = []
        for ordinal, piece in planned:
            row = DocumentChunk(
                document_id=document_id,
                section_id=section_rows[ordinal].id,
                chunk_index=piece.index,
                text=piece.text,
                token_count=piece.token_count,
                char_start=piece.char_start,
                char_end=piece.char_end,
                page=piece.page,
                text_hash=_sha256(piece.text),
            )
            session.add(row)
            chunk_rows.append(row)
        session.flush()
        items = [(row.id, row.text) for row in chunk_rows]
        doc = session.get(Document, document_id)
        if doc is not None:
            doc.n_pages = extracted.n_pages

    ctx.progress(0.6, f"Document {document_id}: embedding {len(items)} passages locally")
    emb = get_embedder()
    with ctx.session(write=True) as session:  # fresh session: embeds without holding the write lock
        embedded = ensure_vectors(session, "chunk", items, emb.model_id)

    with ctx.session(write=True) as session:
        doc = session.get(Document, document_id)
        if doc is not None:
            doc.status, doc.error = "ready", None
    ctx.progress(1.0, f"Document {document_id}: ready ({len(items)} passages)")
    return {
        "status": "ready",
        "sections": len(sections),
        "chunks": len(items),
        "embedded": embedded,
        "model_id": emb.model_id,
        "pages": extracted.n_pages,
    }


# --------------------------------------------------------------------------------- job status for docs


def latest_document_jobs(session: Session, project_id: int) -> dict[int, Job]:
    """The latest ``process_document`` job per document of a project (params read in Python: no SQL
    over JSON, CONVENTIONS)."""
    out: dict[int, Job] = {}
    jobs = session.scalars(
        select(Job).where(Job.kind == "process_document", Job.project_id == project_id).order_by(Job.id)
    )
    for job in jobs:
        document_id = (job.params_json or {}).get("document_id")
        if isinstance(document_id, int):
            out[document_id] = job
    return out


def needs_reconcile(doc: Document, job: Job | None) -> bool:
    return doc.status in PROCESSING_STATES and job is not None and job.status == "failed"


def reconcile_documents(session: Session, project_id: int) -> int:
    """Mark documents stuck in uploaded/processing whose job failed (e.g. ``interrupted``) as failed."""
    jobs = latest_document_jobs(session, project_id)
    fixed = 0
    for doc in session.scalars(
        select(Document).where(Document.project_id == project_id, Document.status.in_(PROCESSING_STATES))
    ):
        job = jobs.get(doc.id)
        if needs_reconcile(doc, job):
            doc.status = "failed"
            doc.error = INTERRUPTED_ERROR if job.error_code == "interrupted" else UNEXPECTED_ERROR
            fixed += 1
    return fixed


# -------------------------------------------------------------------------------------------- deletion


def _scrub_evidence(session: Session, project_id: int, document_id: int) -> int:
    """Replace stored evidence that points at the document; returns the number of items changed."""
    items = session.scalars(
        select(RecommendationItem)
        .join(RecommendationSlate, RecommendationSlate.id == RecommendationItem.slate_id)
        .where(RecommendationSlate.project_id == project_id)
    )
    changed_items = 0
    for item in items:
        changed = False
        reasons = []
        for reason in item.explanation_json or []:
            evidence = reason.get("evidence") if isinstance(reason, dict) else None
            if isinstance(evidence, dict) and evidence.get("document_id") == document_id:
                reason = {**reason, "evidence": {"deleted_document": True}}
                if reason.get("code") == "draft_match":
                    reason["text"] = DELETED_DRAFT_TEXT
                changed = True
            reasons.append(reason)
        features = dict(item.feature_scores_json or {})
        for name, feature in list(features.items()):
            evidence = feature.get("evidence") if isinstance(feature, dict) else None
            if isinstance(evidence, dict) and evidence.get("document_id") == document_id:
                features[name] = {**feature, "evidence": {"deleted_document": True}}
                changed = True
        if changed:
            item.explanation_json = reasons  # reassign: JSON values are immutable (CONVENTIONS)
            item.feature_scores_json = features
            changed_items += 1
    return changed_items


def _after_commit_cleanup(document_id: int) -> None:
    try:
        remove_document_dir(document_id)
    except OSError:
        log.warning("document %s: could not remove its upload directory", document_id)
    try:
        db.checkpoint_wal()
    except Exception:  # a busy reader may block a TRUNCATE checkpoint; it is retried on the next delete
        log.warning("WAL checkpoint after deleting document %s did not complete", document_id)


def _delete(session: Session, doc: Document, *, force: bool) -> dict[str, Any]:
    if not force and doc.status in PROCESSING_STATES:
        raise ApiError(409, "conflict", "This document is still being processed; try again when it is done")
    document_id, project_id = doc.id, doc.project_id
    chunk_ids = list(
        session.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id == document_id))
    )
    embeddings = delete_vectors(session, "chunk", chunk_ids) if chunk_ids else 0
    edge_filter = or_(
        and_(GraphEdge.src_type == "document", GraphEdge.src_id == document_id),
        and_(GraphEdge.dst_type == "document", GraphEdge.dst_id == document_id),
    )
    if chunk_ids:
        edge_filter = or_(
            edge_filter,
            and_(GraphEdge.src_type == "chunk", GraphEdge.src_id.in_(chunk_ids)),
            and_(GraphEdge.dst_type == "chunk", GraphEdge.dst_id.in_(chunk_ids)),
        )
    edges = session.execute(delete(GraphEdge).where(edge_filter)).rowcount or 0
    scrubbed = _scrub_evidence(session, project_id, document_id)
    sections = session.scalar(
        select(func.count()).select_from(DocumentSection).where(DocumentSection.document_id == document_id)
    )
    session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
    session.execute(delete(DocumentSection).where(DocumentSection.document_id == document_id))
    file_present = document_dir(document_id).exists()
    session.delete(doc)
    session.flush()
    event.listen(session, "after_commit", lambda _s: _after_commit_cleanup(document_id), once=True)
    log.info("document %s deleted (%d chunks)", document_id, len(chunk_ids))
    return {
        "file": file_present,
        "sections": int(sections or 0),
        "chunks": len(chunk_ids),
        "embeddings": int(embeddings),
        "edges": int(edges),
        "scrubbed_items": scrubbed,
    }


def delete_document(session: Session, document_id: int) -> dict[str, Any]:
    """Delete a document with full cascade and scrubbing (write session); returns ``DocumentDeleted`` counts.

    ``ApiError`` 404 for an unknown document and 409 while it is ``uploaded``/``processing``.
    """
    doc = session.get(Document, document_id)
    if doc is None:
        raise ApiError(404, "not_found", "Document not found")
    return _delete(session, doc, force=False)


def delete_project_private_data(session: Session, project_id: int) -> dict[str, Any]:
    """Delete every document of the project, the project vector and project-scoped edges (write
    session). Called before the project row is deleted (its FK cascades remove the rest)."""
    totals: dict[str, Any] = defaultdict(int)
    docs = list(session.scalars(select(Document).where(Document.project_id == project_id)))
    for doc in docs:
        for key, value in _delete(session, doc, force=True).items():
            totals[key] += int(value)
    totals["documents"] = len(docs)
    totals["project_vectors"] = delete_vectors(session, "project", [project_id])
    totals["project_edges"] = (
        session.execute(delete(GraphEdge).where(GraphEdge.project_id == project_id)).rowcount or 0
    )
    session.flush()
    return dict(totals)
