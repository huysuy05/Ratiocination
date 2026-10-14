"""Paragraph-aware chunking: target 220 words, max 320, overlap 40 (SPEC §10). Owner: docs.

Chunks never cross a section boundary. Within a section a chunk ends, in order of preference, at a
paragraph break, then a sentence end, closest to the target (but not before 60% of it); with neither,
at the target. The next chunk starts ``overlap`` words earlier. A remainder of at most ``max_words``
words becomes the last chunk (no tiny trailing chunk). Offsets are absolute in the document text.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

TARGET_WORDS = 220
MAX_WORDS = 320
OVERLAP_WORDS = 40
TOKENS_PER_WORD = 1.3

_WORD_RE = re.compile(r"\S+")
_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class Chunk:
    index: int  # position within the document
    text: str
    char_start: int
    char_end: int
    token_count: int
    page: int | None


def chunk_text(
    text: str,
    base_offset: int = 0,
    *,
    start_index: int = 0,
    page_at: Callable[[int], int | None] | None = None,
    target: int = TARGET_WORDS,
    max_words: int = MAX_WORDS,
    overlap: int = OVERLAP_WORDS,
) -> list[Chunk]:
    """Chunk one section's text; ``base_offset`` is the section's offset in the document."""
    if not (0 <= overlap < target <= max_words):
        raise ValueError("need 0 <= overlap < target <= max_words")
    words = [(m.start(), m.end()) for m in _WORD_RE.finditer(text)]
    if not words:
        return []
    word_starts = [start for start, _ in words]
    paragraph_starts: set[int] = set()
    for match in _PARAGRAPH_BREAK_RE.finditer(text):
        # index of the first word at or after the break
        lo, hi = 0, len(words)
        while lo < hi:
            mid = (lo + hi) // 2
            if word_starts[mid] < match.end():
                lo = mid + 1
            else:
                hi = mid
        if lo < len(words):
            paragraph_starts.add(lo)
    sentence_cuts = {k + 1 for k, (_, end) in enumerate(words) if text[end - 1] in ".!?"}

    chunks: list[Chunk] = []
    i, n = 0, len(words)
    while i < n:
        if n - i <= max_words:
            cut = n
        else:
            low, high, goal = i + int(target * 0.6), i + max_words, i + target
            paragraph = [p for p in paragraph_starts if low <= p <= high]
            sentence = [s for s in sentence_cuts if low <= s <= high]
            if paragraph:
                cut = min(paragraph, key=lambda p: (abs(p - goal), p))
            elif sentence:
                cut = min(sentence, key=lambda s: (abs(s - goal), s))
            else:
                cut = goal
        start_char, end_char = words[i][0], words[cut - 1][1]
        absolute = base_offset + start_char
        chunks.append(
            Chunk(
                index=start_index + len(chunks),
                text=text[start_char:end_char],
                char_start=absolute,
                char_end=base_offset + end_char,
                token_count=round((cut - i) * TOKENS_PER_WORD),
                page=page_at(absolute) if page_at else None,
            )
        )
        if cut >= n:
            break
        i = max(cut - overlap, i + 1)
    return chunks
