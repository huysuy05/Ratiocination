"""Upload validation and storage under ``<data>/uploads/<document_id>/`` (SPEC §10). Owner: docs.

One directory per document, so the same bytes uploaded to two projects never share a file (deleting
one cannot break the other). Directories are created 0700 and files 0600: uploads are private.
Paths are derived only from the document id and the content hash — never from the user's filename —
and resolved paths must stay inside the uploads directory.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from radar.config import get_settings
from radar.errors import ApiError, validation_error

SUPPORTED: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}
KIND_BY_MIME: dict[str, str] = {"application/pdf": "pdf", "text/plain": "text", "text/markdown": "markdown"}
_READ_CHUNK = 1024 * 1024
_FORBIDDEN = set('/\\:*?"<>|')


@dataclass(frozen=True)
class AcceptedUpload:
    filename: str  # sanitized display name (private: never logged)
    extension: str
    mime_type: str
    size_bytes: int
    checksum: str  # sha256 hex
    data: bytes


def sanitize_filename(name: str | None) -> str:
    """A safe display name: basename only, printable, no path characters, at most 200 chars."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    text = unicodedata.normalize("NFKC", base)
    text = "".join(c for c in text if c.isprintable() and c not in _FORBIDDEN)
    text = " ".join(text.split()).strip(" .")
    if len(text) > 200:
        stem, ext = os.path.splitext(text)
        text = stem[: 200 - len(ext)] + ext
    return text or "document"


def read_limited(stream: BinaryIO, max_bytes: int) -> bytes:
    """Read an upload stream, failing with 413 as soon as it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(_READ_CHUNK, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise ApiError(413, "payload_too_large", f"Files can be at most {max_bytes // (1024 * 1024)} MB")
    return b"".join(chunks)


def validate_upload(filename: str | None, data: bytes) -> AcceptedUpload:
    """Check type (by extension *and* content) and size; 415/422 ``ApiError`` otherwise."""
    name = sanitize_filename(filename)
    extension = os.path.splitext(name)[1].lower()
    mime = SUPPORTED.get(extension)
    if mime is None:
        raise ApiError(415, "unsupported_media_type", "Upload a PDF, a .txt or a .md file")
    if not data:
        raise validation_error([{"path": "file", "message": "The file is empty"}], "The file is empty")
    if mime == "application/pdf":
        if b"%PDF" not in data[:1024]:  # the PDF header may follow a few junk bytes
            raise ApiError(415, "unsupported_media_type", "This file is not a valid PDF")
    else:
        if b"\x00" in data or b"%PDF" in data[:1024]:
            raise ApiError(415, "unsupported_media_type", "This file is not plain text")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ApiError(415, "unsupported_media_type", "Text files must be UTF-8 encoded") from None
    return AcceptedUpload(
        filename=name,
        extension=extension,
        mime_type=mime,
        size_bytes=len(data),
        checksum=hashlib.sha256(data).hexdigest(),
        data=data,
    )


def document_dir(document_id: int) -> Path:
    return get_settings().uploads_dir / str(int(document_id))


def save_upload(document_id: int, upload: AcceptedUpload) -> str:
    """Write the bytes atomically to ``uploads/<id>/<sha256><ext>``; returns the relative storage URI."""
    settings = get_settings()
    settings.uploads_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory = document_dir(document_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    target = directory / f"{upload.checksum}{upload.extension}"
    temporary = directory / f".{upload.checksum}.part"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(upload.data)
    os.replace(temporary, target)
    return target.relative_to(settings.data_dir).as_posix()


def resolve_path(storage_uri: str) -> Path:
    """Absolute path of a stored upload; ``ValueError`` if it would leave the uploads directory."""
    settings = get_settings()
    uploads = settings.uploads_dir.resolve()
    path = (settings.data_dir / storage_uri).resolve()
    if not path.is_relative_to(uploads):
        raise ValueError("storage path outside the uploads directory")
    return path


def remove_document_dir(document_id: int) -> bool:
    """Delete the document's upload directory; ``True`` when something was removed."""
    directory = document_dir(document_id)
    if not directory.exists():
        return False
    shutil.rmtree(directory)
    return True
