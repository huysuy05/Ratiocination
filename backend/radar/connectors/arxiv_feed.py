"""arXiv category Atom feeds (SPEC §7). Owner: ingest.

``https://rss.arxiv.org/atom/<cat>+<cat>…`` lists the latest announcement (new submissions,
cross-lists and replacements). An empty feed (weekends, holidays) is valid. Entry format verified
against ``tests/fixtures/arxiv_feed_multi.xml`` (2026-10-12).
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from radar.connectors._atom import (
    NS,
    clean_categories,
    https,
    parse_datetime,
    parse_xml,
    split_creators,
    text,
)
from radar.net.http import SourceUnavailable, get_http
from radar.store.identifiers import parse_arxiv_id
from radar.store.papers import PaperRecord

log = logging.getLogger(__name__)

FEED_URL = "https://rss.arxiv.org/atom/"
MAX_CATEGORIES_PER_REQUEST = 10
ANNOUNCE_TYPES = ("new", "cross", "replace", "replace-cross")

# "arXiv:2610.13277v1 Announce Type: new \nAbstract: <text>"
_ABSTRACT_MARKER = re.compile(r"^.*?\bAbstract:\s*", re.S)


@dataclass
class FeedResult:
    updated: str | None  # feed-level <updated> (the watermark)
    records: list[PaperRecord] = field(default_factory=list)
    raw_count: int = 0


def fetch_feed(categories: list[str], **ctx: Any) -> FeedResult:
    """GET ``https://rss.arxiv.org/atom/{cats joined by '+'}`` (<=10 categories per request).

    ``ctx`` may carry ``job_id``/``project_id`` for the cost ledger. Raises ``ValueError`` for no
    categories, more than 10, or an invalid id; ``SourceUnavailable`` for a non-200 answer.
    """
    cats = clean_categories(categories)
    if not cats:
        raise ValueError("fetch_feed needs at least one category")
    if len(cats) > MAX_CATEGORIES_PER_REQUEST:
        raise ValueError(f"at most {MAX_CATEGORIES_PER_REQUEST} categories per feed request")
    response = get_http().request(
        "GET",
        FEED_URL + "+".join(cats),
        provider="arxiv",
        call_type="feed",
        job_id=ctx.get("job_id"),
        project_id=ctx.get("project_id"),
    )
    if response.status_code != 200:
        raise SourceUnavailable("arxiv", status=response.status_code)
    return parse_feed(response.content)


def parse_feed(xml: bytes) -> FeedResult:
    """Parse an arXiv Atom feed (pure; an empty feed is valid)."""
    root = parse_xml(xml)
    entries = root.findall("atom:entry", NS)
    records: list[PaperRecord] = []
    for entry in entries:
        record = _entry_record(entry)
        if record is not None:
            records.append(record)
    if len(records) != len(entries):
        log.warning("arXiv feed: skipped %d unparseable entries", len(entries) - len(records))
    return FeedResult(updated=text(root, "atom:updated"), records=records, raw_count=len(entries))


def _abstract(summary: str | None) -> str | None:
    if not summary:
        return None
    abstract = _ABSTRACT_MARKER.sub("", summary, count=1).strip()
    return abstract or None


def _entry_record(entry: ET.Element) -> PaperRecord | None:
    parsed = parse_arxiv_id(text(entry, "atom:id"))
    title = text(entry, "atom:title")
    if parsed is None or not title:
        return None
    base, version = parsed
    announce_type = (text(entry, "arxiv:announce_type") or "").lower() or None
    if announce_type is not None and announce_type not in ANNOUNCE_TYPES:
        log.info("arXiv feed: unknown announce type %r", announce_type)
    categories: list[str] = []
    for node in entry.findall("atom:category", NS):
        term = (node.get("term") or "").strip()
        if term and term not in categories:
            categories.append(term)
    published = parse_datetime(text(entry, "atom:published"))
    landing = None
    for link in entry.findall("atom:link", NS):
        if link.get("rel") in (None, "alternate") and link.get("href"):
            landing = https(link.get("href"))
            break
    doi = text(entry, "arxiv:DOI") or text(entry, "arxiv:doi")
    journal_ref = text(entry, "arxiv:journal_reference") or text(entry, "arxiv:journal_ref")

    identifiers = {"arxiv": base}
    if doi:
        identifiers["doi"] = doi
    source_id = f"{base}{version or ''}"
    return PaperRecord(
        source="arxiv_feed",
        source_id=source_id,
        title=title,
        abstract=_abstract(text(entry, "atom:summary")),
        authors=split_creators(text(entry, "dc:creator")),
        publication_date=published.date() if published else None,
        year=published.year if published else None,
        identifiers=identifiers,
        version_label=version,
        announce_type=announce_type,
        categories=categories,
        primary_category=categories[0] if categories else None,
        venue=journal_ref,
        license=text(entry, "dc:rights"),
        landing_url=landing or f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{source_id}",
        source_updated_at=published,
        # Compact and stable: no feed-generation timestamps (they change on every regeneration), so
        # re-reading the same announcement yields the same payload hash (SPEC §19 test 1).
        raw={
            "id": source_id,
            "announce_type": announce_type,
            "published": published.isoformat() if published else None,
            "categories": categories,
            "doi": doi,
        },
    )
