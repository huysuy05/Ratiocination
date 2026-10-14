"""Shared helpers for arXiv Atom documents (the RSS feed and the query API). Owner: ingest.

Pure functions only. XML comes from arXiv over HTTPS; Python's bundled expat (>= 2.6) limits entity
expansion, so ``xml.etree.ElementTree`` is safe enough for these documents.
"""

from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}

CATEGORY_RE = re.compile(r"^[a-z-]+(\.[A-Za-z-]+)?$")
"""arXiv category id, e.g. ``cs.IR``, ``math.GT``, ``hep-th`` (API.md ProjectSettings)."""

_NAME_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}


def parse_xml(xml: bytes) -> ET.Element:
    """Root element of an XML document; ``ValueError`` for malformed input."""
    try:
        return ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError(f"malformed arXiv XML: {exc}") from None


# TeX text-mode accents as arXiv metadata spells them ("Kivim\\"aki", "Erd\\H{o}s", "{\\'e}cole").
_SYMBOL_ACCENTS = {
    '"': "\u0308",
    "'": "\u0301",
    "`": "\u0300",
    "^": "\u0302",
    "~": "\u0303",
    "=": "\u0304",
    ".": "\u0307",
}
_LETTER_ACCENTS = {
    "u": "\u0306",
    "v": "\u030c",
    "H": "\u030b",
    "c": "\u0327",
    "k": "\u0328",
    "r": "\u030a",
    "d": "\u0323",
    "b": "\u0331",
}
_SPECIAL_LETTERS = {
    "ss": "\u00df",
    "aa": "\u00e5",
    "AA": "\u00c5",
    "ae": "\u00e6",
    "AE": "\u00c6",
    "oe": "\u0153",
    "OE": "\u0152",
    "o": "\u00f8",
    "O": "\u00d8",
    "l": "\u0142",
    "L": "\u0141",
    "i": "\u0131",
}
_BASE = r"(?:\\([ij])(?![A-Za-z])|([A-Za-z]))"
# Symbol accents take a bare or braced letter; letter-named accents (\\v, \\H, …) need braces, so math
# commands such as \\varphi, \\hat or \\rho are never touched. An outer {…} is consumed with the accent.
_SYMBOL_RE = re.compile(r"(\{)?\\([\"'`^~=.])\s*(?:\{\s*" + _BASE + r"\s*\}|" + _BASE + r")(?(1)\})")
_LETTER_RE = re.compile(r"(\{)?\\([uvHckrdb])\s*\{\s*" + _BASE + r"\s*\}(?(1)\})")
# Like TeX, a control word swallows the spaces after it ("Stra\\ss e" is "Straße").
_SPECIAL_RE = re.compile(r"(\{)?\\(ss|aa|AA|ae|AE|oe|OE|o|O|l|L|i)(?![A-Za-z])(?:\s*\{\}|\s+)?(?(1)\})")


def _accented(mark: str, dotless: str | None, letter: str | None) -> str:
    return unicodedata.normalize("NFC", (dotless or letter or "") + mark)


def detex(value: str) -> str:
    """Replace TeX text-mode accents and special letters with Unicode; leave everything else (math)."""
    if "\\" not in value:
        return value
    value = _SYMBOL_RE.sub(lambda m: _accented(_SYMBOL_ACCENTS[m[2]], m[3] or m[5], m[4] or m[6]), value)
    value = _LETTER_RE.sub(lambda m: _accented(_LETTER_ACCENTS[m[2]], m[3], m[4]), value)
    return _SPECIAL_RE.sub(lambda m: _SPECIAL_LETTERS[m[2]], value)


def collapse(value: str | None) -> str:
    """Unescape HTML entities, turn TeX accents into Unicode and collapse whitespace to single spaces."""
    if not value:
        return ""
    return " ".join(detex(html.unescape(value)).split())


def text(element: ET.Element, path: str) -> str | None:
    """Collapsed text of the first match of ``path`` (namespaced with :data:`NS`), or ``None``."""
    found = element.find(path, NS)
    if found is None or found.text is None:
        return None
    value = collapse(found.text)
    return value or None


def parse_datetime(value: str | None) -> datetime | None:
    """Aware datetime from an ISO 8601 timestamp (``Z`` or an offset); naive values are taken as UTC."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def split_creators(value: str | None) -> list[str]:
    """Author names from a ``dc:creator`` list such as ``"A Smith, B Jones and C Wu"``.

    Splits on commas and on a separating ``" and "``; a lone suffix (``Jr.``, ``III``) is joined back
    to the preceding name.
    """
    if not value:
        return []
    names: list[str] = []
    for part in collapse(value).split(","):
        # "(?:^|\s+)and\s+" also catches the Oxford comma ("…, and C. Author").
        for piece in re.split(r"(?:^|\s+)and\s+", part.strip()):
            piece = piece.strip()
            if not piece:
                continue
            if names and piece.lower() in _NAME_SUFFIXES:
                names[-1] = f"{names[-1]}, {piece}"
            else:
                names.append(piece)
    return names


def https(url: str | None) -> str | None:
    """arXiv sometimes emits ``http://`` links; store them as ``https://``."""
    if not url:
        return None
    return "https://" + url[len("http://") :] if url.startswith("http://") else url


def clean_categories(categories: list[str]) -> list[str]:
    """Validated, de-duplicated categories in their original order; ``ValueError`` on a bad id."""
    out: list[str] = []
    for raw in categories:
        category = str(raw).strip()
        if not CATEGORY_RE.match(category):
            raise ValueError(f"invalid arXiv category: {category!r}")
        if category not in out:
            out.append(category)
    return out
