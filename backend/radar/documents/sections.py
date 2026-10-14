"""Heading detection and sections (SPEC §10). Owner: docs.

A line is a heading when it is short (≤ 90 characters, ≤ 12 words, contains a letter, does not end
like a sentence) and either
* (PDF) its font is at least 1.15 × the median body size, or
* it matches the numbered/known-heading pattern (Abstract, 2 Related Work, 4.1 Results, …), or
* (Markdown) it is a ``#`` heading.
Consecutive heading lines of one block (a two-line heading) are merged. Text before the first heading
becomes the "Front matter" section; a document without headings is one section "Document". Sections
with an empty body are dropped. A "References"/"Bibliography" section is flagged (it is not chunked).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from radar.documents.extract import ExtractedText, Line

KNOWN_HEADING_RE = re.compile(
    r"^(\d+(\.\d+)*\.?\s+)?(abstract|introduction|related work|background|methods?|methodology|approach|"
    r"experiments?|results|discussion|conclusions?|limitations|references|appendix)\b",
    re.I,
)
MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
REFERENCES_RE = re.compile(r"^(\d+(\.\d+)*\.?\s+)?(references|bibliography)\b", re.I)
MAX_HEADING_CHARS = 90
MAX_HEADING_WORDS = 12
SIZE_RATIO = 1.15


@dataclass(frozen=True)
class Section:
    ordinal: int
    heading: str
    page_start: int | None
    page_end: int | None
    char_start: int  # body start (after the heading lines), offsets into ExtractedText.text
    char_end: int
    text: str
    is_references: bool


def median_body_size(lines: list[Line]) -> float | None:
    """Median font size of body-like lines (≥ 40 characters), else of all sized lines."""
    body = [line.size for line in lines if line.size and len(line.text) >= 40]
    sizes = body or [line.size for line in lines if line.size]
    return float(statistics.median(sizes)) if sizes else None


def _heading_shape(text: str) -> bool:
    return (
        0 < len(text) <= MAX_HEADING_CHARS
        and len(text.split()) <= MAX_HEADING_WORDS
        and any(c.isalpha() for c in text)
        and not text.endswith((".", ",", ";"))
    )


def is_heading(line: Line, kind: str, body_size: float | None) -> bool:
    text = line.text.strip()
    if kind == "markdown":
        return MARKDOWN_HEADING_RE.match(text) is not None
    if not _heading_shape(text):
        return False
    if KNOWN_HEADING_RE.match(text):
        return True
    return bool(kind == "pdf" and body_size and line.size and line.size >= SIZE_RATIO * body_size)


def heading_text(line: Line, kind: str) -> str:
    text = line.text.strip()
    if kind == "markdown":
        match = MARKDOWN_HEADING_RE.match(text)
        if match:
            return match.group(2).strip()
    return " ".join(text.split())


def detect_sections(doc: ExtractedText) -> list[Section]:
    """Split ``doc`` into sections (see module docstring)."""
    body_size = median_body_size(doc.lines) if doc.kind == "pdf" else None
    flags = [is_heading(line, doc.kind, body_size) for line in doc.lines]

    # Group heading lines: a heading continues on the next line when that line is also a heading line
    # of the same block (PDF two-line headings).
    groups: list[tuple[int, int]] = []  # (first line index, last line index)
    index = 0
    while index < len(doc.lines):
        if not flags[index]:
            index += 1
            continue
        last = index
        while (
            doc.kind == "pdf"
            and last + 1 < len(doc.lines)
            and flags[last + 1]
            and not doc.lines[last + 1].block_start
        ):
            last += 1
        groups.append((index, last))
        index = last + 1

    def make(ordinal: int, heading: str, start: int, end: int) -> Section | None:
        body = doc.text[start:end]
        if not body.strip():
            return None
        leading = len(body) - len(body.lstrip())
        trailing = len(body) - len(body.rstrip())
        start, end = start + leading, end - trailing
        return Section(
            ordinal=ordinal,
            heading=heading[:500],
            page_start=doc.page_at(start),
            page_end=doc.page_at(max(start, end - 1)),
            char_start=start,
            char_end=end,
            text=doc.text[start:end],
            is_references=REFERENCES_RE.match(heading) is not None,
        )

    sections: list[Section] = []
    if not groups:
        only = make(0, "Document", 0, len(doc.text))
        return [only] if only else []
    first_start = doc.lines[groups[0][0]].char_start
    front = make(0, "Front matter", 0, first_start)
    if front:
        sections.append(front)
    for position, (first, last) in enumerate(groups):
        heading = " ".join(heading_text(doc.lines[i], doc.kind) for i in range(first, last + 1))
        body_start = doc.lines[last].char_end
        body_end = (
            doc.lines[groups[position + 1][0]].char_start if position + 1 < len(groups) else len(doc.text)
        )
        section = make(len(sections), heading, body_start, body_end)
        if section:
            sections.append(section)
    return sections
