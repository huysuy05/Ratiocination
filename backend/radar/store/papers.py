"""Paper records, upsert, merge and dedup (SPEC §6; v0.2 §5 rules). Owner: store.

``upsert_paper`` is the single entry point that turns one source's view of a work (a
:class:`PaperRecord`) into canonical rows. It runs inside a write session
(``session_scope(write=True)``) and is idempotent: upserting the same record twice changes nothing.

Matching (first hit wins): (1) DOI, (2) arXiv base id, (3) OpenAlex, then S2 (then PMID);
(3b) records without any valid identifier reuse the paper of an earlier ``source_records`` row with
the same ``(source, source_id)``; (4) title fallback - equal ``title_norm`` AND |year diff| <= 1 AND
equal first-author surname merges into that paper; any other equal-title paper (or more than one
mergeable paper) means a NEW paper plus a pending ``duplicate_candidates`` row and an unverified
``SAME_WORK_AS`` edge (provenance ``title_match``); (5) identifiers that belong to different existing
papers: the record attaches to the highest-priority match and every other matched paper gets a
duplicate candidate and an unverified ``SAME_WORK_AS`` edge (provenance ``identifier``). Nothing
ambiguous is ever merged automatically.

Merge rules: never replace a non-empty value with an empty one; conflicting non-empty values are
decided by per-field source priority (:data:`FIELD_PRIORITY`), equal priority keeps the existing
value (first writer wins; a value with unknown provenance ranks lowest); ``publication_date`` is the
earliest date known (``year`` follows it; arXiv feed entries contribute their date only for
announce types new/cross); authors are rebuilt only when the normalized author list changes;
``field_provenance_json[field] = {source, observed_at}`` records the record source that set a value.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from radar.models import (
    Author,
    DuplicateCandidate,
    Paper,
    PaperAuthor,
    PaperIdentifier,
    PaperVersion,
    SourceRecord,
    utcnow,
)
from radar.store.graph import upsert_edge
from radar.store.identifiers import (
    IDENTIFIER_SCHEMES,
    normalize_identifier,
    normalize_title,
    parse_arxiv_id,
    surname_norm,
    version_number,
)

log = logging.getLogger(__name__)

__all__ = [
    "FIELD_PRIORITY",
    "SOURCE_CLASS",
    "PaperRecord",
    "UpsertResult",
    "attach_openalex_author_ids",
    "get_paper_by_identifier",
    "latest_version_label",
    "latest_versions",
    "payload_hash",
    "upsert_paper",
]


@dataclass
class PaperRecord:
    """One source's view of a work (SPEC §5)."""

    source: str  # 'arxiv_feed'|'arxiv_api'|'arxiv_oai'|'openalex'|'s2'
    source_id: str
    title: str
    abstract: str | None = None
    authors: list[str] = field(default_factory=list)
    publication_date: date | None = None
    year: int | None = None
    identifiers: dict[str, str] = field(default_factory=dict)  # scheme -> raw value
    version_label: str | None = None  # 'v2' for arXiv; 'published' for DOI records of published works
    announce_type: str | None = None  # new|cross|replace|replace-cross
    categories: list[str] = field(default_factory=list)
    primary_category: str | None = None
    venue: str | None = None
    license: str | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    is_oa: bool | None = None
    oa_url: str | None = None
    best_oa_pdf: str | None = None
    cited_by_count: int | None = None
    paper_type: str | None = None
    status: str | None = None  # 'retracted'|'withdrawn'|'stub'
    source_updated_at: datetime | None = None
    raw: dict[str, Any] | None = None


@dataclass
class UpsertResult:
    paper_id: int
    created: bool
    changed_fields: list[str]
    new_version: bool
    duplicate_candidate_of: int | None


# ------------------------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------------------------

SOURCE_CLASS: dict[str, str] = {
    "arxiv_feed": "arxiv",
    "arxiv_api": "arxiv",
    "arxiv_oai": "arxiv",  # OAI-PMH arXivRaw harvest (backfill), same metadata as the arXiv API
    "openalex": "openalex",
    "s2": "s2",
}
"""PaperRecord.source -> source class (also ``paper_versions.source``)."""

_TEXT_PRIORITY = ("arxiv", "openalex", "s2")
_VENUE_PRIORITY = ("openalex", "s2", "arxiv")
_METRIC_PRIORITY = ("openalex", "s2", "arxiv")

FIELD_PRIORITY: dict[str, tuple[str, ...]] = {
    "title": _TEXT_PRIORITY,
    "abstract": _TEXT_PRIORITY,
    "authors": _TEXT_PRIORITY,
    "categories": _TEXT_PRIORITY,
    "primary_category": _TEXT_PRIORITY,
    "venue": _VENUE_PRIORITY,
    "paper_type": _VENUE_PRIORITY,
    "cited_by_count": _METRIC_PRIORITY,
    "is_oa": _METRIC_PRIORITY,
    "oa_url": _METRIC_PRIORITY,
    "best_oa_pdf": _METRIC_PRIORITY,
}
"""Per-field source-class priority, best first (SPEC §5). Unlisted classes rank below listed ones."""

_STATUSES = ("active", "stub", "retracted", "withdrawn")
_FEED_DATED_ANNOUNCE_TYPES = ("new", "cross")
_MAX_TITLE_CANDIDATES = 10
_TITLE_SCAN_LIMIT = 200
_IDENTIFIER_SCORE = 1.0

# Simple scalar columns merged by priority: field name -> Paper attribute.
_SCALAR_FIELDS: tuple[tuple[str, str], ...] = (
    ("abstract", "abstract"),
    ("venue", "venue"),
    ("paper_type", "paper_type"),
    ("cited_by_count", "cited_by_count"),
    ("is_oa", "is_oa"),
    ("oa_url", "oa_url"),
    ("best_oa_pdf", "best_oa_pdf"),
    ("primary_category", "primary_category"),
    ("categories", "categories_json"),
)
_STRING_LIMITS = {"paper_type": 64, "primary_category": 64}


# ------------------------------------------------------------------------------------------------
# Small pure helpers
# ------------------------------------------------------------------------------------------------


def payload_hash(raw: dict[str, Any] | None) -> str:
    """sha256 of ``json.dumps(raw or {}, sort_keys=True, separators=(',',':'), ensure_ascii=False, default=str)``."""
    text = json.dumps(raw or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def latest_version_label(labels: Iterable[str | None]) -> str | None:
    """SPEC §4.2 ``latest_version``: the ``vN`` label with the highest N, else ``'published'`` if
    present, else ``None``."""
    best: int | None = None
    published = False
    for label in labels:
        number = version_number(label)
        if number is not None:
            if best is None or number > best:
                best = number
        elif label == "published":
            published = True
    if best is not None:
        return f"v{best}"
    return "published" if published else None


def latest_versions(session: Session, paper_ids: Iterable[int]) -> dict[int, str | None]:
    """``latest_version`` for many papers in one query (papers without versions map to ``None``)."""
    ids = sorted({int(i) for i in paper_ids})
    if not ids:
        return {}
    labels: dict[int, list[str]] = defaultdict(list)
    for paper_id, label in session.execute(
        select(PaperVersion.paper_id, PaperVersion.version_label).where(PaperVersion.paper_id.in_(ids))
    ):
        labels[paper_id].append(label)
    return {pid: latest_version_label(labels.get(pid, ())) for pid in ids}


def _iso(value: datetime) -> str:
    text = value.astimezone(UTC).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text


def _aware(value: datetime | None) -> datetime | None:
    """Aware UTC datetime (naive values are taken as UTC)."""
    if value is None:
        return None
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_title(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_list(values: Iterable[Any] | None) -> list[str]:
    out: list[str] = []
    for value in values or ():
        text = " ".join(str(value or "").split())
        if text and text not in out:
            out.append(text)
    return out


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


_NON_ALNUM = re.compile(r"[\W_]+")


def _author_key(name: str) -> str:
    """Comparison key for author names: accents, case and punctuation ignored; "Last, First" reordered."""
    text = _strip_accents(unicodedata.normalize("NFKC", name)).casefold()
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first} {last}"
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _rank(field_name: str, source_class: str | None) -> int:
    order = FIELD_PRIORITY.get(field_name, ())
    if source_class in order:
        return order.index(source_class)
    return len(order)


def _source_class(source: str) -> str:
    try:
        return SOURCE_CLASS[source]
    except KeyError:
        raise ValueError(f"unknown PaperRecord.source {source!r}") from None


def _provenance_class(entry: Any) -> str | None:
    if isinstance(entry, dict):
        return SOURCE_CLASS.get(str(entry.get("source")))
    return None


# ------------------------------------------------------------------------------------------------
# Record interpretation
# ------------------------------------------------------------------------------------------------


@dataclass
class _Prepared:
    source_class: str
    identifiers: list[tuple[str, str]]
    title: str
    title_norm: str
    abstract: str | None
    authors: list[tuple[str, str]]  # (display name, raw name)
    categories: list[str]
    primary_category: str | None
    pub_date: date | None
    year: int | None
    status: str | None
    version_label: str | None
    scalars: dict[str, Any]


def _record_identifiers(record: PaperRecord) -> list[tuple[str, str]]:
    """Normalized, de-duplicated identifiers in matching order (doi, arxiv, openalex, s2, pmid)."""
    found: list[tuple[str, str]] = []
    for scheme, raw in (record.identifiers or {}).items():
        normalized = normalize_identifier(scheme, raw)
        if normalized is None:
            if raw:
                log.debug("ignored unrecognized %s identifier from %s", scheme, record.source)
            continue
        if len(normalized[1]) > 255:
            log.warning("ignored over-long %s identifier from %s", normalized[0], record.source)
            continue
        if normalized not in found:
            found.append(normalized)
    order = {scheme: i for i, scheme in enumerate(IDENTIFIER_SCHEMES)}
    return sorted(found, key=lambda item: order[item[0]])


def _record_dates(record: PaperRecord) -> tuple[date | None, int | None]:
    """The publication date/year this record contributes (feed replace entries contribute none)."""
    if record.source == "arxiv_feed" and (record.announce_type or "").strip().lower() not in (
        _FEED_DATED_ANNOUNCE_TYPES
    ):
        return None, None
    value = record.publication_date
    pub_date = value.date() if isinstance(value, datetime) else value
    if pub_date is not None:
        return pub_date, pub_date.year
    year = record.year if isinstance(record.year, int) and 0 < record.year < 10000 else None
    return None, year


def _raw_says_retracted(record: PaperRecord) -> bool:
    raw = record.raw
    if not isinstance(raw, dict):
        return False
    if record.source == "openalex":
        return raw.get("is_retracted") is True
    return False


def _record_status(record: PaperRecord) -> str | None:
    status = (record.status or "").strip().lower() or None
    if status not in _STATUSES:
        status = None
    if status != "retracted" and _raw_says_retracted(record):
        status = "retracted"
    return status


def _record_version_label(record: PaperRecord, source_class: str) -> str | None:
    label = _clean_text(record.version_label)
    if source_class == "arxiv":
        if label is None:
            # arXiv records carry the version in the id (e.g. oai:arXiv.org:2610.12545v1).
            for candidate in ((record.identifiers or {}).get("arxiv"), record.source_id):
                parsed = parse_arxiv_id(candidate) if candidate else None
                if parsed and parsed[1]:
                    label = parsed[1]
                    break
        number = version_number(label)
        return f"v{number}" if number is not None else None
    if label is not None and label.lower() == "published":
        return "published"
    return None


def _prepare(record: PaperRecord) -> _Prepared:
    source_class = _source_class(record.source)
    title = _clean_title(record.title)
    pub_date, year = _record_dates(record)
    categories = _clean_list(record.categories)
    primary = _clean_text(record.primary_category) or (categories[0] if categories else None)
    if primary is not None and primary not in categories:
        categories.insert(0, primary)
    authors = [(" ".join(str(name).split()), str(name).strip()) for name in record.authors or () if name]
    authors = [(display, raw) for display, raw in authors if display]
    scalars: dict[str, Any] = {
        "abstract": _clean_text(record.abstract),
        "venue": _clean_text(record.venue),
        "paper_type": _clean_text(record.paper_type),
        "cited_by_count": (
            record.cited_by_count
            if isinstance(record.cited_by_count, int) and not isinstance(record.cited_by_count, bool)
            else None
        ),
        "is_oa": record.is_oa if isinstance(record.is_oa, bool) else None,
        "oa_url": _clean_text(record.oa_url),
        "best_oa_pdf": _clean_text(record.best_oa_pdf),
        "primary_category": primary,
        "categories": categories or None,
    }
    for name, limit in _STRING_LIMITS.items():
        if isinstance(scalars.get(name), str):
            scalars[name] = scalars[name][:limit]
    return _Prepared(
        source_class=source_class,
        identifiers=_record_identifiers(record),
        title=title,
        title_norm=normalize_title(title),
        abstract=scalars["abstract"],
        authors=authors,
        categories=categories,
        primary_category=primary,
        pub_date=pub_date,
        year=year,
        status=_record_status(record),
        version_label=_record_version_label(record, source_class),
        scalars=scalars,
    )


# ------------------------------------------------------------------------------------------------
# Matching
# ------------------------------------------------------------------------------------------------


def _identifier_owners(session: Session, identifiers: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    if not identifiers:
        return {}
    clauses = [and_(PaperIdentifier.scheme == s, PaperIdentifier.value == v) for s, v in identifiers]
    rows = session.execute(
        select(PaperIdentifier.scheme, PaperIdentifier.value, PaperIdentifier.paper_id).where(or_(*clauses))
    )
    return {(scheme, value): paper_id for scheme, value, paper_id in rows}


def _paper_from_source_record(session: Session, record: PaperRecord) -> int | None:
    source_id = _clean_text(record.source_id)
    if source_id is None:
        return None
    paper_id = session.scalar(
        select(SourceRecord.paper_id)
        .join(Paper, Paper.id == SourceRecord.paper_id)
        .where(SourceRecord.source == record.source, SourceRecord.source_id == source_id[:255])
        .order_by(SourceRecord.id.desc())
        .limit(1)
    )
    return int(paper_id) if paper_id is not None else None


def _title_match(session: Session, prepared: _Prepared) -> tuple[int | None, list[tuple[int, float]]]:
    """``(paper id to merge into, [])`` or ``(None, [(candidate id, score), ...])`` (SPEC §5 step 4)."""
    if not prepared.title_norm:
        return None, []
    rows = session.execute(
        select(Paper.id, Paper.year, Paper.first_author_norm)
        .where(Paper.title_norm == prepared.title_norm)
        .order_by(Paper.id)
        .limit(_TITLE_SCAN_LIMIT)
    ).all()
    if not rows:
        return None, []
    surname = surname_norm(prepared.authors[0][0]) if prepared.authors else ""
    mergeable: list[int] = []
    scored: list[tuple[int, float]] = []
    for paper_id, year, first_author in rows:
        year_ok = prepared.year is not None and year is not None and abs(prepared.year - year) <= 1
        author_ok = bool(surname) and bool(first_author) and surname == first_author
        if year_ok and author_ok:
            mergeable.append(paper_id)
        scored.append((paper_id, 0.5 + (0.25 if year_ok else 0.0) + (0.25 if author_ok else 0.0)))
    if len(mergeable) == 1:
        return mergeable[0], []
    scored.sort(key=lambda item: (-item[1], item[0]))
    return None, scored[:_MAX_TITLE_CANDIDATES]


# ------------------------------------------------------------------------------------------------
# Writes
# ------------------------------------------------------------------------------------------------


def _current_authors(session: Session, paper_id: int) -> list[str]:
    return [
        display or raw
        for display, raw in session.execute(
            select(Author.display_name, PaperAuthor.raw_name)
            .join(Author, Author.id == PaperAuthor.author_id)
            .where(PaperAuthor.paper_id == paper_id)
            .order_by(PaperAuthor.position)
        )
    ]


def _write_authors(session: Session, paper: Paper, authors: list[tuple[str, str]]) -> None:
    """Replace the paper's author rows (authors are not deduplicated globally). OpenAlex author ids
    of the old rows carry over to new rows with the same surname."""
    old_rows = session.execute(
        select(PaperAuthor.author_id, PaperAuthor.raw_name, PaperAuthor.openalex_author_id).where(
            PaperAuthor.paper_id == paper.id
        )
    ).all()
    old_author_ids = [row[0] for row in old_rows]
    old_ids = [(surname_norm(raw), oa_id) for _, raw, oa_id in old_rows if oa_id]
    if old_author_ids:
        session.execute(delete(PaperAuthor).where(PaperAuthor.paper_id == paper.id))
        still_used = set(
            session.scalars(select(PaperAuthor.author_id).where(PaperAuthor.author_id.in_(old_author_ids)))
        )
        orphans = [a for a in old_author_ids if a not in still_used]
        if orphans:
            session.execute(delete(Author).where(Author.id.in_(orphans)))
    for position, (display, raw) in enumerate(authors):
        surname = surname_norm(display)
        author = Author(display_name=display, name_norm=surname[:255])
        carried = next((oa_id for name, oa_id in old_ids if name and name == surname), None)
        if carried is not None:
            old_ids.remove((surname, carried))
        session.add(author)
        session.add(
            PaperAuthor(
                paper_id=paper.id,
                author=author,
                position=position,
                raw_name=raw or display,
                openalex_author_id=carried,
            )
        )
    first = surname_norm(authors[0][0])[:255] if authors else ""
    paper.first_author_norm = first or None


def attach_openalex_author_ids(
    session: Session, paper_id: int, names: list[str], author_ids: list[str | None]
) -> int:
    """Store OpenAlex author ids on the paper's ``paper_authors`` rows (write session; SPEC §6).

    OpenAlex lists authors in byline order like arXiv, but formats names differently ("Iyengar,
    Madhav" vs "Madhav Iyengar"). Each id goes to the row at the same position when the surnames
    agree, else to the first unmatched row with that surname; ids without a match are skipped.
    Returns the number of rows changed.
    """
    rows = list(
        session.scalars(
            select(PaperAuthor).where(PaperAuthor.paper_id == paper_id).order_by(PaperAuthor.position)
        )
    )
    surnames = [surname_norm(row.raw_name) for row in rows]
    used: set[int] = set()
    changed = 0
    for index, (name, author_id) in enumerate(zip(names, author_ids, strict=False)):
        target = surname_norm(name)
        if not author_id or not target:
            continue
        if index < len(rows) and index not in used and surnames[index] == target:
            match: int | None = index
        else:
            match = next((i for i, s in enumerate(surnames) if i not in used and s == target), None)
        if match is None:
            continue
        used.add(match)
        if rows[match].openalex_author_id != author_id:
            rows[match].openalex_author_id = author_id
            changed += 1
    return changed


def _create_paper(
    session: Session, record: PaperRecord, prepared: _Prepared, now: datetime
) -> tuple[Paper, list[str]]:
    entry = {"source": record.source, "observed_at": _iso(now)}
    provenance: dict[str, Any] = {}
    changed: list[str] = []
    if prepared.title:
        provenance["title"] = entry
        changed.append("title")
    values: dict[str, Any] = {}
    for name, attr in _SCALAR_FIELDS:
        value = prepared.scalars.get(name)
        if not _is_empty(value):
            values[attr] = value
            provenance[name] = entry
            changed.append(name)
    if prepared.pub_date is not None:
        provenance["publication_date"] = entry
        changed.append("publication_date")
    if prepared.year is not None:
        provenance["year"] = entry
        changed.append("year")
    status = prepared.status if prepared.status in ("stub", "retracted", "withdrawn") else "active"
    if prepared.status is not None:
        provenance["status"] = entry
    if prepared.authors:
        provenance["authors"] = entry
        changed.append("authors")
    paper = Paper(
        canonical_title=prepared.title,
        title_norm=prepared.title_norm,
        publication_date=prepared.pub_date,
        year=prepared.year,
        status=status,
        categories_json=list(values.pop("categories_json", None) or []),
        first_seen_at=now,
        ingested_via=record.source,
        field_provenance_json=provenance,
        **values,
    )
    session.add(paper)
    session.flush()
    if prepared.authors:
        _write_authors(session, paper, prepared.authors)
    return paper, changed


def _merge_paper(
    session: Session, paper: Paper, record: PaperRecord, prepared: _Prepared, now: datetime
) -> list[str]:
    entry = {"source": record.source, "observed_at": _iso(now)}
    original = dict(paper.field_provenance_json or {})
    provenance = dict(original)
    changed: list[str] = []
    new_class = prepared.source_class

    def decide(name: str, current: Any, new: Any) -> bool:
        """True when ``new`` replaces ``current``; may upgrade provenance for an equal value."""
        if _is_empty(new):
            return False
        new_rank = _rank(name, new_class)
        current_rank = _rank(name, _provenance_class(provenance.get(name)))
        if _is_empty(current):
            return True
        if current == new:
            if new_rank < current_rank:
                provenance[name] = entry
            return False
        return new_rank < current_rank

    if decide("title", paper.canonical_title, prepared.title):
        paper.canonical_title = prepared.title
        paper.title_norm = prepared.title_norm
        provenance["title"] = entry
        changed.append("title")

    for name, attr in _SCALAR_FIELDS:
        new = prepared.scalars.get(name)
        if decide(name, getattr(paper, attr), new):
            setattr(paper, attr, list(new) if isinstance(new, list) else new)
            provenance[name] = entry
            changed.append(name)

    if prepared.authors:
        current_authors = _current_authors(session, paper.id)
        new_keys = [_author_key(display) for display, _ in prepared.authors]
        if [_author_key(name) for name in current_authors] == new_keys:
            if _rank("authors", new_class) < _rank("authors", _provenance_class(provenance.get("authors"))):
                provenance["authors"] = entry
        elif not current_authors or _rank("authors", new_class) < _rank(
            "authors", _provenance_class(provenance.get("authors"))
        ):
            _write_authors(session, paper, prepared.authors)
            provenance["authors"] = entry
            changed.append("authors")

    if prepared.pub_date is not None:
        if paper.publication_date is None or prepared.pub_date < paper.publication_date:
            paper.publication_date = prepared.pub_date
            provenance["publication_date"] = entry
            changed.append("publication_date")
            if paper.year != prepared.pub_date.year:
                paper.year = prepared.pub_date.year
                provenance["year"] = entry
                changed.append("year")
    elif prepared.year is not None and paper.publication_date is None:
        if paper.year is None or prepared.year < paper.year:
            paper.year = prepared.year
            provenance["year"] = entry
            changed.append("year")

    status = paper.status
    if prepared.status == "retracted":
        status = "retracted"
    elif prepared.status == "withdrawn" and status != "retracted":
        status = "withdrawn"
    elif prepared.status in (None, "active") and status == "stub" and new_class == "arxiv":
        status = "active"  # an arXiv ingestion record makes a graph stub a regular paper
    if status != paper.status:
        paper.status = status
        provenance["status"] = entry
        changed.append("status")

    if provenance != original:
        paper.field_provenance_json = provenance
    return changed


def _attach_identifiers(
    session: Session,
    paper: Paper,
    identifiers: list[tuple[str, str]],
    owners: dict[tuple[str, str], int],
) -> bool:
    """Attach the record's free identifiers; the first value attached per scheme becomes primary."""
    rows = session.execute(
        select(PaperIdentifier.scheme, PaperIdentifier.value, PaperIdentifier.is_primary).where(
            PaperIdentifier.paper_id == paper.id
        )
    ).all()
    present = {(scheme, value) for scheme, value, _ in rows}
    with_primary = {scheme for scheme, _, is_primary in rows if is_primary}
    added = False
    for scheme, value in identifiers:
        if (scheme, value) in present:
            continue
        owner = owners.get((scheme, value))
        if owner is not None and owner != paper.id:
            continue  # belongs to another paper: recorded as a duplicate candidate instead
        primary = scheme not in with_primary
        session.add(PaperIdentifier(paper_id=paper.id, scheme=scheme, value=value, is_primary=primary))
        with_primary.add(scheme)
        present.add((scheme, value))
        added = True
    return added


def _upsert_version(
    session: Session, paper: Paper, record: PaperRecord, prepared: _Prepared, created: bool, now: datetime
) -> tuple[bool, bool]:
    """Create or complete the record's version row. Returns ``(new_version, changed)``."""
    label = prepared.version_label
    if label is None:
        return False, False
    source = prepared.source_class
    source_updated_at = _aware(record.source_updated_at)
    if (
        source_updated_at is None
        and record.source == "arxiv_feed"
        and (record.announce_type or "").strip().lower() in ("replace", "replace-cross")
        and record.publication_date is not None
    ):
        announced = record.publication_date
        if isinstance(announced, datetime):
            source_updated_at = _aware(announced)
        else:
            source_updated_at = datetime.combine(announced, time(0), tzinfo=UTC)
    values: dict[str, Any] = {
        "source_id": (_clean_text(record.source_id) or "")[:255] or None,
        "landing_url": _clean_text(record.landing_url),
        "pdf_url": _clean_text(record.pdf_url),
        "license": _clean_text(record.license),
        "announce_type": (_clean_text(record.announce_type) or "")[:32] or None,
        "source_updated_at": source_updated_at,
    }
    rows = (
        session.execute(
            select(PaperVersion).where(PaperVersion.paper_id == paper.id, PaperVersion.source == source)
        )
        .scalars()
        .all()
    )
    existing = next((row for row in rows if row.version_label == label), None)
    if existing is None:
        previous = [n for n in (version_number(row.version_label) for row in rows) if n is not None]
        session.add(
            PaperVersion(paper_id=paper.id, version_label=label, source=source, observed_at=now, **values)
        )
        number = version_number(label)
        is_new = (
            not created
            and source == "arxiv"
            and number is not None
            and number > (max(previous) if previous else 0)
        )
        return is_new, True
    changed = False
    for name, value in values.items():
        if value is not None and getattr(existing, name) in (None, ""):
            setattr(existing, name, value)
            changed = True
    return False, changed


def _record_source(session: Session, record: PaperRecord, paper_id: int, now: datetime) -> None:
    digest = payload_hash(record.raw)
    source_id = (_clean_text(record.source_id) or "")[:255]
    exists = session.scalar(
        select(SourceRecord.id).where(
            SourceRecord.source == record.source,
            SourceRecord.source_id == source_id,
            SourceRecord.payload_hash == digest,
        )
    )
    if exists is not None:
        return
    payload = json.loads(json.dumps(record.raw or {}, ensure_ascii=False, default=str))
    session.add(
        SourceRecord(
            source=record.source,
            source_id=source_id,
            paper_id=paper_id,
            payload_json=payload,
            payload_hash=digest,
            observed_at=now,
        )
    )


def _record_duplicate(
    session: Session, paper_id: int, other_id: int, *, score: float, reason: str, matched_on: str
) -> None:
    """Pending duplicate candidate (kept as is when it exists) + unverified SAME_WORK_AS edge."""
    low, high = sorted((int(paper_id), int(other_id)))
    if low == high:
        return
    row = session.scalar(
        select(DuplicateCandidate).where(
            DuplicateCandidate.paper_a_id == low, DuplicateCandidate.paper_b_id == high
        )
    )
    if row is None:
        session.add(
            DuplicateCandidate(paper_a_id=low, paper_b_id=high, score=score, reason=reason, status="pending")
        )
    upsert_edge(
        session,
        src=("work", low),
        relation="SAME_WORK_AS",
        dst=("work", high),
        provenance=reason,
        verified=False,
        confidence=score,
        evidence={"matched_on": matched_on},
    )


# ------------------------------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------------------------------


def upsert_paper(session: Session, record: PaperRecord) -> UpsertResult:
    """Match (DOI, arXiv, OpenAlex, S2, title fallback), merge and record provenance (write session).

    Raises ``ValueError`` for an unknown ``record.source``. See the module docstring for the matching
    and merge rules. ``changed_fields`` names the paper fields set or changed (plus ``identifiers`` /
    ``versions`` when rows were added or completed); it is empty when nothing changed.
    ``new_version`` is true when an existing paper gained an arXiv ``vN`` newer than every arXiv
    version it had. ``duplicate_candidate_of`` is the other paper of a duplicate candidate detected by
    this upsert (title match or conflicting identifier), else ``None``.
    """
    prepared = _prepare(record)
    now = utcnow()

    owners = _identifier_owners(session, prepared.identifiers)
    target_id: int | None = None
    for ident in prepared.identifiers:
        if ident in owners:
            target_id = owners[ident]
            break
    conflicts: dict[int, str] = {}
    for scheme, value in prepared.identifiers:
        owner = owners.get((scheme, value))
        if owner is not None and owner != target_id and owner not in conflicts:
            conflicts[owner] = scheme

    if target_id is None and not prepared.identifiers:
        target_id = _paper_from_source_record(session, record)

    title_candidates: list[tuple[int, float]] = []
    if target_id is None:
        target_id, title_candidates = _title_match(session, prepared)

    created = target_id is None
    if target_id is None:
        paper, changed = _create_paper(session, record, prepared, now)
    else:
        found = session.get(Paper, target_id)
        if found is None:  # pragma: no cover - identifier rows cascade with their paper
            raise LookupError(f"paper {target_id} disappeared during upsert")
        paper = found
        changed = _merge_paper(session, paper, record, prepared, now)

    if _attach_identifiers(session, paper, prepared.identifiers, owners):
        changed.append("identifiers")
    new_version, version_changed = _upsert_version(session, paper, record, prepared, created, now)
    if version_changed:
        changed.append("versions")
    _record_source(session, record, paper.id, now)

    duplicate_of: int | None = None
    for other_id, scheme in conflicts.items():
        _record_duplicate(
            session, paper.id, other_id, score=_IDENTIFIER_SCORE, reason="identifier", matched_on=scheme
        )
        duplicate_of = duplicate_of if duplicate_of is not None else other_id
    for other_id, score in title_candidates:
        _record_duplicate(session, paper.id, other_id, score=score, reason="title_match", matched_on="title")
        duplicate_of = duplicate_of if duplicate_of is not None else other_id
    session.flush()
    if conflicts or title_candidates:
        log.info(
            "paper %s: duplicate candidates recorded (identifier=%d, title=%d)",
            paper.id,
            len(conflicts),
            len(title_candidates),
        )
    return UpsertResult(
        paper_id=paper.id,
        created=created,
        changed_fields=changed,
        new_version=new_version,
        duplicate_candidate_of=duplicate_of,
    )


def get_paper_by_identifier(session: Session, scheme: str, value: str) -> Paper | None:
    """The paper holding ``(scheme, value)`` after normalization, or ``None``.

    ``scheme`` is normalized like stored identifiers, so ``('doi', '10.48550/arXiv.2504.08385')``
    finds the paper with arXiv id ``2504.08385``.
    """
    normalized = normalize_identifier(scheme, value)
    if normalized is None:
        return None
    return session.scalar(
        select(Paper)
        .join(PaperIdentifier, PaperIdentifier.paper_id == Paper.id)
        .where(PaperIdentifier.scheme == normalized[0], PaperIdentifier.value == normalized[1])
        .limit(1)
    )
