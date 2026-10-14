"""Project keywords (SPEC §10): normalisation, whole-word/phrase matching and the ``keyword`` value.

Pure Python (stdlib only, no radar imports): the API schemas use it to de-duplicate keywords and
radar.ranking to match them, so both always agree.

Matching rules (the same for keywords and paper text):

* Unicode NFKC, then case-folding ("Conformal" = "conformal", full-width letters = ASCII);
* hyphens, dashes, slashes and underscores count as spaces ("self-supervised" = "self supervised");
* a token is a run of letters and digits, plus a trailing ``+`` or ``#`` ("C++" and "C#" stay distinct);
* light plural handling on tokens of 4+ characters: ``-ies`` -> ``-y``, ``-es`` after s/x/z/ch/sh is
  dropped, a final ``-s`` is dropped unless the word ends in ``-ss``, ``-us`` or ``-is``
  ("networks" = "network", "LLMs" = "LLM", "classes" = "class"; "analysis" and "loss" stay);
* a keyword matches when its tokens appear consecutively in the text: whole words and phrases only
  ("graph" never matches "paragraph"; "neural network" matches "neural networks" but not "neural
  ranking network").

Feature value (``keyword_value``): every matched keyword counts once - :data:`TITLE_WEIGHT` when it is
in the title, else :data:`ABSTRACT_WEIGHT` when it is only in the abstract - and the sum ``r`` saturates
as ``1 - exp(-r)``: one title match 0.63, two 0.86, three 0.95; one abstract-only match 0.39.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

MAX_KEYWORDS = 12
MIN_LENGTH = 2
MAX_LENGTH = 60
TITLE_WEIGHT = 1.0
ABSTRACT_WEIGHT = 0.5
MATCH_TEXT_CHARS = 80

_SEPARATORS = re.compile(r"[\-\u2010-\u2015\u2212\u2043\ufe58\ufe63\uff0d/\\_]")
_TOKEN = re.compile(r"[^\W_]+[+#]*")
_SPACES = re.compile(r"\s+")


def _stem(token: str) -> str:
    if len(token) < 4 or not token.isalpha():
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith(("sses", "xes", "zes", "ches", "shes")):
        return token[:-2]
    if token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _prepare(text: str) -> str:
    """NFKC text with every separator character replaced by one space (offsets are preserved)."""
    return _SEPARATORS.sub(" ", unicodedata.normalize("NFKC", text or ""))


def tokens(text: str) -> list[str]:
    """Normalised, case-folded, lightly de-pluralised tokens of ``text``."""
    return [_stem(m.group(0).casefold()) for m in _TOKEN.finditer(_prepare(text))]


def keyword_key(keyword: str) -> str:
    """The match key of a keyword: its normalised tokens joined by single spaces ("" = matches nothing).

    Two keywords with the same key match exactly the same papers, so keys de-duplicate a keyword list.
    """
    return " ".join(tokens(keyword))


def clean_keyword(value: str) -> str:
    """A keyword as stored: surrounding whitespace trimmed, inner whitespace collapsed to one space."""
    return _SPACES.sub(" ", unicodedata.normalize("NFC", value or "")).strip()


def dedupe_keywords(values: Iterable[str]) -> list[str]:
    """First occurrence of every match key, in order (case-, hyphen- and plural-insensitive)."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = keyword_key(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out


@dataclass(frozen=True)
class KeywordMatch:
    """One matched keyword: where (``title`` or ``abstract``) and the exact words of the paper."""

    keyword: str
    field: str  # 'title' | 'abstract'
    text: str


class _Indexed:
    """Tokens of one text with the character span of each token (in the NFKC-normalised text)."""

    def __init__(self, text: str) -> None:
        self.text = unicodedata.normalize("NFKC", text or "")
        found = [(m.start(), m.end(), _stem(m.group(0).casefold())) for m in _TOKEN.finditer(_prepare(text))]
        self.spans = [(start, end) for start, end, _ in found]
        self.joined = " " + " ".join(token for _, _, token in found) + " "
        # character offset in ``joined`` -> token index
        self._starts: dict[int, int] = {}
        offset = 1
        for index, (_, _, token) in enumerate(found):
            self._starts[offset] = index
            offset += len(token) + 1

    def find(self, key: str) -> str | None:
        """The paper's own words for the first occurrence of ``key`` (whole tokens), else ``None``."""
        if not key:
            return None
        position = self.joined.find(" " + key + " ")
        if position < 0:
            return None
        first = self._starts[position + 1]
        last = first + key.count(" ")
        words = self.text[self.spans[first][0] : self.spans[last][1]]
        words = " ".join(words.split())
        return words if len(words) <= MATCH_TEXT_CHARS else words[: MATCH_TEXT_CHARS - 1] + "…"


def match_keywords(keywords: Sequence[str], title: str | None, abstract: str | None) -> list[KeywordMatch]:
    """Keywords found in the title (first) or else in the abstract, in keyword order within each field.

    Each keyword appears at most once: a title match wins over an abstract match.
    """
    if not keywords:
        return []
    in_title = _Indexed(title or "")
    in_abstract: _Indexed | None = None
    title_hits: list[KeywordMatch] = []
    abstract_hits: list[KeywordMatch] = []
    for keyword in dedupe_keywords(keywords):
        key = keyword_key(keyword)
        words = in_title.find(key)
        if words is not None:
            title_hits.append(KeywordMatch(keyword, "title", words))
            continue
        if in_abstract is None:
            in_abstract = _Indexed(abstract or "")
        words = in_abstract.find(key)
        if words is not None:
            abstract_hits.append(KeywordMatch(keyword, "abstract", words))
    return title_hits + abstract_hits


def keyword_raw(matches: Sequence[KeywordMatch]) -> float:
    """``Σ`` of :data:`TITLE_WEIGHT` per title match and :data:`ABSTRACT_WEIGHT` per abstract-only match."""
    return sum(TITLE_WEIGHT if m.field == "title" else ABSTRACT_WEIGHT for m in matches)


def keyword_value(raw: float) -> float:
    """Saturating value in [0, 1): ``1 - exp(-raw)``."""
    return 1.0 - math.exp(-max(0.0, float(raw)))


__all__ = [
    "ABSTRACT_WEIGHT",
    "MAX_KEYWORDS",
    "MAX_LENGTH",
    "MIN_LENGTH",
    "TITLE_WEIGHT",
    "KeywordMatch",
    "clean_keyword",
    "dedupe_keywords",
    "keyword_key",
    "keyword_raw",
    "keyword_value",
    "match_keywords",
    "tokens",
]
