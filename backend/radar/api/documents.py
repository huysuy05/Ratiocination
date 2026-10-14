"""Private documents: upload, list, status, delete (docs/API.md "Documents (private)"). Owner: docs.

Uploads are validated (size, type by extension and content, the user's storage quota), stored
privately and processed by the ``process_document`` job, which is queued in the same short write
transaction that creates the row and dispatched after commit. Status reads repair documents stuck in
``uploaded``/``processing`` whose job failed (e.g. interrupted by a restart). Filenames are private:
never logged. Every route acts on the signed-in user's documents (others are a 404).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar.api.deps import OwnerId, get_project_or_404
from radar.api.serializers import job_out
from radar.config import get_settings
from radar.db import session_scope
from radar.documents import service
from radar.documents.storage import read_limited, remove_document_dir, save_upload, validate_upload
from radar.errors import ApiError
from radar.jobs.runner import submit_job
from radar.models import Document, DocumentChunk, DocumentSection, Job
from radar.schemas import (
    DocumentAccepted,
    DocumentDeleted,
    DocumentDeletedCounts,
    DocumentInfo,
    DocumentOutlineItem,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["documents"])


def _own_document(session: Session, document_id: int, owner_id: int) -> Document:
    doc = session.get(Document, document_id)
    if doc is None or doc.owner_id != owner_id:
        raise ApiError(404, "not_found", "Document not found")
    return doc


def _info(session: Session, doc: Document, job: Job | None) -> DocumentInfo:
    sections = session.execute(
        select(DocumentSection.id, DocumentSection.heading, DocumentSection.page_start)
        .where(DocumentSection.document_id == doc.id)
        .order_by(DocumentSection.ordinal)
    ).all()
    counts = dict(
        session.execute(
            select(DocumentChunk.section_id, func.count())
            .where(DocumentChunk.document_id == doc.id)
            .group_by(DocumentChunk.section_id)
        ).all()
    )
    outline = [
        DocumentOutlineItem(heading=heading, page_start=page_start, n_chunks=int(counts.get(section_id, 0)))
        for section_id, heading, page_start in sections
    ]
    return DocumentInfo(
        id=doc.id,
        project_id=doc.project_id,
        filename=doc.filename,
        mime_type=doc.mime_type,
        size_bytes=doc.size_bytes,
        status=doc.status,
        error=doc.error,
        n_sections=len(sections),
        n_chunks=sum(int(c) for c in counts.values()),
        n_pages=doc.n_pages,
        created_at=doc.created_at,
        outline=outline,
        job_id=job.id if job is not None else None,
        progress=job.progress if job is not None else None,
        message=job.message if job is not None else None,
    )


def _reconcile(project_id: int) -> None:
    """Repair stuck documents; opens a write transaction only when something needs fixing."""
    with session_scope() as session:
        jobs = service.latest_document_jobs(session, project_id)
        stuck = [
            doc
            for doc in session.scalars(
                select(Document).where(
                    Document.project_id == project_id, Document.status.in_(service.PROCESSING_STATES)
                )
            )
            if service.needs_reconcile(doc, jobs.get(doc.id))
        ]
    if stuck:
        with session_scope(write=True) as session:
            service.reconcile_documents(session, project_id)


@router.get("/projects/{project_id}/documents", response_model=list[DocumentInfo])
def list_documents(project_id: int, owner_id: OwnerId) -> list[DocumentInfo]:
    """``200 DocumentInfo[]`` (newest first)."""
    with session_scope() as session:
        get_project_or_404(session, project_id, owner_id)
    _reconcile(project_id)
    with session_scope() as session:
        jobs = service.latest_document_jobs(session, project_id)
        docs = session.scalars(
            select(Document).where(Document.project_id == project_id).order_by(Document.id.desc())
        )
        return [_info(session, doc, jobs.get(doc.id)) for doc in docs]


@router.post("/projects/{project_id}/documents", response_model=DocumentAccepted, status_code=202)
def upload_document(project_id: int, owner_id: OwnerId, file: UploadFile = File(...)) -> DocumentAccepted:
    """``202 DocumentAccepted``; 409 ``conflict`` (``details.document_id``); 413; 415."""
    settings = get_settings()
    with session_scope() as session:
        get_project_or_404(session, project_id, owner_id)
        used = session.scalar(
            select(func.coalesce(func.sum(Document.size_bytes), 0)).where(Document.owner_id == owner_id)
        )
    data = read_limited(file.file, settings.max_upload_mb * 1024 * 1024)
    upload = validate_upload(file.filename, data)
    if int(used or 0) + upload.size_bytes > settings.user_storage_mb * 1024 * 1024:
        raise ApiError(
            413,
            "quota_exceeded",
            f"Your documents would exceed {settings.user_storage_mb} MB; delete some first",
        )

    def duplicate_of() -> int | None:
        with session_scope() as session:
            return session.scalar(
                select(Document.id).where(
                    Document.project_id == project_id, Document.checksum == upload.checksum
                )
            )

    existing = duplicate_of()
    if existing is not None:
        raise ApiError(
            409, "conflict", "This file is already in the project", details={"document_id": existing}
        )

    document_id: int | None = None
    try:
        with session_scope(write=True) as session:
            doc = Document(
                owner_id=owner_id,
                project_id=project_id,
                filename=upload.filename,
                mime_type=upload.mime_type,
                size_bytes=upload.size_bytes,
                checksum=upload.checksum,
                storage_uri="",
                status="uploaded",
            )
            session.add(doc)
            session.flush()
            document_id = doc.id
            doc.storage_uri = save_upload(doc.id, upload)
            job_id = submit_job(
                "process_document",
                project_id=project_id,
                params={"document_id": doc.id},
                session=session,
                owner_id=owner_id,
            )
    except IntegrityError:  # a concurrent upload of the same file won the unique(project_id, checksum) race
        if document_id is not None:
            remove_document_dir(document_id)
        raise ApiError(
            409, "conflict", "This file is already in the project", details={"document_id": duplicate_of()}
        ) from None
    except BaseException:
        if document_id is not None:
            remove_document_dir(document_id)
        raise

    with session_scope() as session:
        doc = session.get(Document, document_id)
        job = session.get(Job, job_id)
        if doc is None or job is None:  # pragma: no cover - deleted concurrently
            raise ApiError(404, "not_found", "Document not found")
        return DocumentAccepted(document=_info(session, doc, job), job=job_out(job))


@router.get("/documents/{document_id}/status", response_model=DocumentInfo)
def document_status(document_id: int, owner_id: OwnerId) -> DocumentInfo:
    """``200 DocumentInfo``."""
    with session_scope() as session:
        project_id = _own_document(session, document_id, owner_id).project_id
    _reconcile(project_id)
    with session_scope() as session:
        doc = session.get(Document, document_id)
        if doc is None:  # pragma: no cover - deleted concurrently
            raise ApiError(404, "not_found", "Document not found")
        return _info(session, doc, service.latest_document_jobs(session, project_id).get(document_id))


@router.delete("/documents/{document_id}", response_model=DocumentDeleted)
def delete_document(document_id: int, owner_id: OwnerId) -> DocumentDeleted:
    """``200 DocumentDeleted``; 409 ``conflict`` while ``uploaded``/``processing``."""
    with session_scope() as session:
        project_id = _own_document(session, document_id, owner_id).project_id
    _reconcile(project_id)
    with session_scope(write=True) as session:
        _own_document(session, document_id, owner_id)
        counts = service.delete_document(session, document_id)
    return DocumentDeleted(deleted=DocumentDeletedCounts(**counts))
