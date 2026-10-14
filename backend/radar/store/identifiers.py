"""Identifier normalization (SPEC §5). Pure functions. Owner: store.

Normalized forms (what ``paper_identifiers.value`` stores):

* ``doi``      - lowercased bare DOI ``10.<registrant>/<suffix>``; arXiv DOIs (``10.48550/arxiv.*``) are
  never stored as DOIs: :func:`normalize_identifier` maps them to ``('arxiv', base)``;
* ``arxiv``    - base id without version: ``2504.08385`` or ``hep-th/9901001`` / ``math.GT/0309136``
  (archive lowercased, subject class case kept);
* ``openalex`` - ``W`` + digits;
* ``s2``       - the 40-character lowercase hex Semantic Scholar paperId (CorpusId is never stored);
* ``pmid``     - digits.

Every function accepts ``None`` and arbitrary text and never raises on bad input; unrecognized input
gives ``None``.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote

__all__ = [
    "ARXIV_DOI_PREFIX",
    "IDENTIFIER_SCHEMES",
    "normalize_doi",
    "normalize_identifier",
    "normalize_title",
    "parse_arxiv_id",
    "parse_identifier",
    "surname_norm",
    "version_number",
]

IDENTIFIER_SCHEMES: tuple[str, ...] = ("doi", "arxiv", "openalex", "s2", "pmid")
"""Stored identifier schemes, in matching priority order (SPEC §5)."""

ARXIV_DOI_PREFIX = "10.48550/arxiv."
"""Normalized prefix of arXiv's DataCite DOIs; such DOIs are stored as scheme ``arxiv``."""

# ------------------------------------------------------------------------------------------------
# DOI
# ------------------------------------------------------------------------------------------------

_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")
# URL prefixes (the rest is percent-decoded) and plain prefixes; input is lowercased first.
_DOI_URL_PREFIXES: tuple[str, ...] = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "https://www.doi.org/",
    "http://www.doi.org/",
    "doi.org/",
    "dx.doi.org/",
)
_DOI_TEXT_PREFIXES: tuple[str, ...] = ("doi:", "doi ")


def _strip_doi_trailing(text: str) -> str:
    """Strip trailing ``. , ;`` and an unbalanced trailing ``)`` (a DOI may end in a balanced ``)``)."""
    while text:
        last = text[-1]
        if last in ".,;":
            text = text[:-1]
        elif last == ")" and text.count(")") > text.count("("):
            text = text[:-1]
        else:
            break
    return text


def normalize_doi(s: str | None) -> str | None:
    """Lowercased bare DOI (``10.x/...``) or ``None`` when ``s`` is not a DOI (SPEC §5).

    Strips whitespace, lowercases, removes ``https://doi.org/``, ``http://doi.org/``,
    ``https://dx.doi.org/``, ``http://dx.doi.org/`` (also ``www.`` and scheme-less forms; the rest of
    a URL is percent-decoded), ``doi:`` and ``doi `` prefixes, strips trailing ``.,;)`` (``)`` only
    when unbalanced) and requires ``^10\\.\\d{4,9}/\\S+$``.
    """
    if s is None:
        return None
    text = str(s).strip().lower()
    for _ in range(4):  # prefixes may be stacked, e.g. "doi: https://doi.org/10..."
        for prefix in _DOI_URL_PREFIXES:
            if text.startswith(prefix):
                text = unquote(text[len(prefix) :]).strip()
                break
        else:
            for prefix in _DOI_TEXT_PREFIXES:
                if text.startswith(prefix):
                    text = text[len(prefix) :].strip()
                    break
            else:
                break
    text = _strip_doi_trailing(text)
    if not _DOI_RE.fullmatch(text):
        return None
    return text


# ------------------------------------------------------------------------------------------------
# arXiv
# ------------------------------------------------------------------------------------------------

_ARXIV_PREFIX_RE = re.compile(
    r"(?:"
    r"(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html|format|ps)/"
    r"|oai:arxiv\.org:"
    r"|(?:(?:https?://)?(?:dx\.|www\.)?doi\.org/|doi:\s*)?10\.48550/arxiv\."
    r"|arxiv:\s*"
    r")",
    re.IGNORECASE,
)
_ARXIV_ID_RE = re.compile(
    r"(?:(?P<new>\d{4}\.\d{4,5})"
    r"|(?P<archive>[A-Za-z][A-Za-z\-]*)(?P<subject>\.[A-Za-z]{2})?/(?P<number>\d{7}))"
    r"(?P<version>[vV]\d+)?"
)


def parse_arxiv_id(s: str | None) -> tuple[str, str | None] | None:
    """``(base, version)`` from any arXiv id/URL/DOI/OAI form, or ``None`` (SPEC §5).

    Accepted forms include ``2504.08385``, ``2504.08385v2``, ``arXiv:2504.08385v2``,
    ``https://arxiv.org/abs/2504.08385v2``, ``http://arxiv.org/pdf/2504.08385v2.pdf``,
    ``oai:arXiv.org:2504.08385v1``, ``10.48550/arXiv.2504.08385`` (also as a doi.org URL or ``doi:``),
    ``hep-th/9901001v2``, ``math.GT/0309136`` and ``https://arxiv.org/abs/hep-th/9901001``. New-style
    ids are ``\\d{4}\\.\\d{4,5}``; old-style ``archive(.SC)?/\\d{7}`` with the archive lowercased and the
    subject class case kept. The version is returned as ``'vN'`` (lowercase ``v``) or ``None``.
    """
    if s is None:
        return None
    text = str(s).strip()
    prefix = _ARXIV_PREFIX_RE.match(text)
    if prefix:
        text = text[prefix.end() :]
    text = re.split(r"[?#]", text, maxsplit=1)[0].rstrip("/")
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    match = _ARXIV_ID_RE.fullmatch(text)
    if match is None:
        return None
    if match.group("new"):
        base = match.group("new")
    else:
        base = f"{match.group('archive').lower()}{match.group('subject') or ''}/{match.group('number')}"
    version = match.group("version")
    return base, (version.lower() if version else None)


# ------------------------------------------------------------------------------------------------
# Scheme normalization and free-text parsing
# ------------------------------------------------------------------------------------------------

_OPENALEX_RE = re.compile(
    r"(?:(?:https?://)?(?:api\.)?openalex\.org/(?:works/)?|openalex:\s*)?[Ww](?P<digits>\d+)/?",
    re.IGNORECASE,
)
_S2_RE = re.compile(
    r"(?:(?:https?://)?(?:www\.|api\.)?semanticscholar\.org/(?:paper/(?:[^/\s]+/)?|graph/v1/paper/))?"
    r"(?P<hex>[0-9a-fA-F]{40})/?"
)
_PMID_RE = re.compile(
    r"(?:(?:https?://)?(?:www\.)?(?:pubmed\.ncbi\.nlm\.nih\.gov/|ncbi\.nlm\.nih\.gov/pubmed/)|pmid:?\s*)?"
    r"(?P<digits>\d+)/?",
    re.IGNORECASE,
)


def normalize_identifier(scheme: str, raw: str | None) -> tuple[str, str] | None:
    """Normalized ``(scheme, value)`` for upsert and lookup; arXiv DOIs map to ``('arxiv', base)``.

    ``doi`` -> :func:`normalize_doi` (``10.48550/arxiv.*`` -> ``('arxiv', base)``); ``arxiv`` -> base
    without version; ``openalex`` -> ``W`` + digits (``https://openalex.org/`` stripped, ``W``
    uppercased); ``s2`` -> 40-char lowercase hex paperId (anything else, e.g. a CorpusId, -> ``None``);
    ``pmid`` -> digits. Schemes are case-insensitive; unknown schemes give ``None``.
    """
    if scheme is None or raw is None:
        return None
    key = str(scheme).strip().lower()
    text = str(raw).strip()
    if not text:
        return None
    if key == "doi":
        doi = normalize_doi(text)
        if doi is None:
            return None
        if doi.startswith(ARXIV_DOI_PREFIX):
            parsed = parse_arxiv_id(doi)
            return ("arxiv", parsed[0]) if parsed else None
        return ("doi", doi)
    if key == "arxiv":
        parsed = parse_arxiv_id(text)
        return ("arxiv", parsed[0]) if parsed else None
    if key == "openalex":
        match = _OPENALEX_RE.fullmatch(text)
        return ("openalex", f"W{match.group('digits')}") if match else None
    if key == "s2":
        match = _S2_RE.fullmatch(text)
        return ("s2", match.group("hex").lower()) if match else None
    if key == "pmid":
        match = _PMID_RE.fullmatch(text)
        if match is None or int(match.group("digits")) <= 0:
            return None
        return ("pmid", str(int(match.group("digits"))))
    return None


def parse_identifier(user_input: str | None) -> tuple[str, str] | None:
    """Detect a DOI / doi.org URL, arXiv id/URL/DOI or OpenAlex ``W\\d+``/URL in free user input.

    Returns the normalized ``(scheme, value)`` (arXiv DOIs come back as ``('arxiv', base)``), or
    ``None`` when the input is none of these.
    """
    if user_input is None:
        return None
    text = str(user_input).strip()
    if not text:
        return None
    if normalize_doi(text) is not None:
        return normalize_identifier("doi", text)
    parsed = parse_arxiv_id(text)
    if parsed is not None:
        return ("arxiv", parsed[0])
    match = _OPENALEX_RE.fullmatch(text)
    if match is not None:
        return ("openalex", f"W{match.group('digits')}")
    return None


def version_number(label: str | None) -> int | None:
    """``'vN'`` -> ``N``; anything else (``'published'``, ``None``, ...) -> ``None``."""
    if label is None:
        return None
    match = re.fullmatch(r"[vV](\d+)", str(label).strip())
    return int(match.group(1)) if match else None


# ------------------------------------------------------------------------------------------------
# Titles and names
# ------------------------------------------------------------------------------------------------

_TITLE_DROP = str.maketrans("", "", "{}$\\")
_NON_ALNUM = re.compile(r"[\W_]+")


def normalize_title(t: str) -> str:
    """NFKC -> remove ``{}$\\`` -> casefold -> non-alphanumerics to spaces -> collapse whitespace.

    "Alphanumeric" is Unicode-aware (``str.isalnum``), so non-Latin titles keep their letters. The
    casefolded text is NFKC-normalized once more so the result is stable under re-normalization.
    """
    if not t:
        return ""
    text = unicodedata.normalize("NFKC", str(t)).translate(_TITLE_DROP).casefold()
    text = unicodedata.normalize("NFKC", text)
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def surname_norm(name: str) -> str:
    """``'Last, First'`` -> ``'last'``; else the last token; accents stripped; casefolded."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKC", str(name)).strip()
    if "," in text:
        base = text.split(",", 1)[0]
    else:
        tokens = text.split()
        base = tokens[-1] if tokens else ""
    return " ".join(_strip_accents(base).casefold().split())
