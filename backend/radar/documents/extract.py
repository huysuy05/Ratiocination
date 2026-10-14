"""Local text extraction (PyMuPDF for PDF; TXT/MD) (SPEC §10). Owner: docs.

PDF: every text line with its page and its largest font size (used for heading detection); blocks
become paragraphs. TXT/MD: UTF-8 text, line by line, blank lines separating paragraphs. Nothing is
ever invented: a password-protected PDF, a PDF without a text layer (scanned images) or undecodable
text raises :class:`ExtractionError`, whose message never contains document text.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

MIN_TEXT_CHARS = 20
"""Fewer extractable characters than this means "no text layer" (typically a scan)."""
_BOLD_FLAG = 16  # PyMuPDF span flag bit for bold


class ExtractionError(Exception):
    """A user-facing reason why a document has no usable text."""


@dataclass(frozen=True)
class Line:
    text: str
    char_start: int  # offsets into ExtractedText.text
    char_end: int
    page: int | None  # 1-based (PDF only)
    size: float | None  # largest font size on the line (PDF only)
    bold: bool = False
    block_start: bool = False  # first line of a paragraph/block


@dataclass
class ExtractedText:
    text: str
    lines: list[Line]
    n_pages: int | None
    kind: str  # 'pdf' | 'text' | 'markdown'
    _starts: list[int] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._starts = [line.char_start for line in self.lines]

    def page_at(self, offset: int) -> int | None:
        """The page of the line containing ``offset`` (``None`` for text files)."""
        if not self.lines:
            return None
        index = max(0, bisect.bisect_right(self._starts, offset) - 1)
        return self.lines[index].page


class _Builder:
    """Accumulates lines into one text with exact offsets."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.length = 0
        self.lines: list[Line] = []

    def separator(self, value: str) -> None:
        if self.length and value:
            self.parts.append(value)
            self.length += len(value)

    def add(self, text: str, *, page: int | None, size: float | None, bold: bool, block_start: bool) -> None:
        start = self.length
        self.parts.append(text)
        self.length += len(text)
        self.lines.append(Line(text, start, self.length, page, size, bold, block_start))

    def text(self) -> str:
        return "".join(self.parts)


def extract_pdf(path: Path) -> ExtractedText:
    try:
        document = pymupdf.open(path)
    except Exception:  # PyMuPDF raises several types for corrupt files
        raise ExtractionError("This PDF could not be opened (it may be damaged)") from None
    with document:
        if document.needs_pass:
            raise ExtractionError("This PDF is password-protected")
        builder = _Builder()
        n_pages = document.page_count
        for page_index in range(n_pages):
            page = document.load_page(page_index)
            data = page.get_text("dict", sort=True)
            for block in data.get("blocks", []):
                if block.get("type") != 0:
                    continue  # images
                first_in_block = True
                for line in block.get("lines", []):
                    spans = [s for s in line.get("spans", []) if s.get("text")]
                    text = "".join(s["text"] for s in spans).strip()
                    if not text:
                        continue
                    builder.separator("\n\n" if first_in_block else "\n")
                    size = max((round(float(s.get("size") or 0.0), 1) for s in spans), default=None)
                    bold = any(
                        (int(s.get("flags") or 0) & _BOLD_FLAG) or "bold" in str(s.get("font", "")).lower()
                        for s in spans
                    )
                    builder.add(text, page=page_index + 1, size=size, bold=bold, block_start=first_in_block)
                    first_in_block = False
    text = builder.text()
    if sum(len(line.text) for line in builder.lines) < MIN_TEXT_CHARS:
        raise ExtractionError("This PDF has no extractable text (it may contain only scanned images)")
    return ExtractedText(text=text, lines=builder.lines, n_pages=n_pages, kind="pdf")


def extract_plain(data: bytes, kind: str) -> ExtractedText:
    """TXT or Markdown (``kind`` = ``text`` | ``markdown``)."""
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ExtractionError("This text file is not UTF-8 encoded") from None
    builder = _Builder()
    paragraph_start = True
    for raw in decoded.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        text = raw.rstrip()
        if not text.strip():
            paragraph_start = True
            continue
        builder.separator("\n\n" if paragraph_start else "\n")
        builder.add(text, page=None, size=None, bold=False, block_start=paragraph_start)
        paragraph_start = False
    if sum(len(line.text.strip()) for line in builder.lines) < MIN_TEXT_CHARS:
        raise ExtractionError("This file contains no text")
    return ExtractedText(text=builder.text(), lines=builder.lines, n_pages=None, kind=kind)


def extract(path: Path, kind: str) -> ExtractedText:
    """Extract a stored upload; ``kind`` from ``storage.KIND_BY_MIME``."""
    if kind == "pdf":
        return extract_pdf(path)
    if kind in ("text", "markdown"):
        return extract_plain(path.read_bytes(), kind)
    raise ExtractionError("Unsupported document type")
