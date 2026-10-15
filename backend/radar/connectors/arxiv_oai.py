"""arXiv OAI-PMH harvesting (``arXivRaw``) for the project backfill (SPEC §7–8). Owner: ingest.

Why OAI-PMH: it is arXiv's interface for bulk metadata, and on 2026-10-12 it answered in seconds while
the query API throttled us (30 s hang, then HTTP 429). That is no longer true from a cloud host: on
2026-10-14 every ``ListRecords`` request from the Fly.io machine hung until the read timeout while the
query API answered in 3.6 s, so ``radar.ingestion.backfill`` treats this connector as the preferred
source and falls back to :mod:`radar.connectors.arxiv_api`. ``from``/``until`` select records by
*datestamp* (last change), so old papers whose metadata changed in the window come back too; the
backfill filters on the v1 date and the project's categories. Format verified against
``tests/fixtures/arxiv_oai_raw_page.xml``: ``<version version="vN"><date>RFC 2822</date>`` history,
space-separated ``<categories>`` (first = primary), a plain ``<authors>`` string, optional
``<doi>``, ``<journal-ref>``, ``<comments>``, ``<license>``.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from radar.connectors._atom import collapse, parse_xml, split_creators
from radar.net.http import SourceUnavailable, get_http
from radar.store.identifiers import parse_arxiv_id, version_number
from radar.store.papers import PaperRecord

log = logging.getLogger(__name__)

OAI_URL = "https://oaipmh.arxiv.org/oai"
NS_OAI = "http://www.openarchives.org/OAI/2.0/"
NS_RAW = "http://arxiv.org/OAI/arXivRaw/"
DEFAULT_MAX_PAGES = 30
"""A page holds up to ~1300 records; 30 pages covers a busy group (cs) for well over two weeks."""

PAGE_TIMEOUT_S = 60.0
"""Read timeout for one ``ListRecords`` page. A healthy page answers in seconds; from a cloud host
arXiv throttles bulk harvesting and the connection simply hangs (measured from Fly.io on 2026-10-14:
``Identify`` 200 in 0.3 s, ``ListRecords`` timed out at 90 s *and* at 120 s), so waiting longer buys
nothing - the backfill falls back to the arXiv query API instead."""

PAGE_MAX_RETRIES = 1
"""Retries per page (instead of the client's 3): a throttled endpoint costs 2 attempts, not 4, so the
fallback starts after ~2 min instead of ~8."""

_GROUP_ARCHIVES = frozenset({"cs", "math", "stat", "q-bio", "q-fin", "eess", "econ"})
_WITHDRAWN_RE = re.compile(r"\bwithdrawn\b", re.I)


@dataclass
class OaiPage:
    records: list[PaperRecord] = field(default_factory=list)
    resumption_token: str | None = None
    raw_count: int = 0


def set_for_category(category: str) -> str:
    """OAI set covering an arXiv category: ``cs.IR`` -> ``cs``; ``hep-th`` -> ``physics:hep-th``."""
    archive = category.split(".", 1)[0]
    return archive if archive in _GROUP_ARCHIVES else f"physics:{archive}"


def harvest(
    set_spec: str, since: date, until: date, *, max_pages: int = DEFAULT_MAX_PAGES, **ctx: Any
) -> Iterator[OaiPage]:
    """Yield ``ListRecords`` pages for ``set_spec`` changed between ``since`` and ``until`` (inclusive).

    Follows resumption tokens up to ``max_pages`` (a warning is logged when the cap cuts the harvest
    short). ``noRecordsMatch`` yields one empty page. Raises ``SourceUnavailable`` for a non-200 answer
    and for a page that keeps timing out (:data:`PAGE_TIMEOUT_S`, :data:`PAGE_MAX_RETRIES`), and
    ``ValueError`` for an OAI-PMH protocol error.
    """
    if since > until:
        raise ValueError("since must not be after until")
    params: dict[str, Any] = {
        "verb": "ListRecords",
        "metadataPrefix": "arXivRaw",
        "set": set_spec,
        "from": since.isoformat(),
        "until": until.isoformat(),
    }
    for page_number in range(1, max_pages + 1):
        response = get_http().request(
            "GET",
            OAI_URL,
            provider="arxiv",
            call_type="list",
            params=params,
            timeout=PAGE_TIMEOUT_S,
            max_retries=PAGE_MAX_RETRIES,
            job_id=ctx.get("job_id"),
            project_id=ctx.get("project_id"),
        )
        if response.status_code != 200:
            raise SourceUnavailable("arxiv", status=response.status_code)
        page = parse_oai_page(response.content)
        yield page
        if not page.resumption_token:
            return
        if page_number == max_pages:
            log.warning("OAI-PMH harvest of set %s stopped at the %d-page cap", set_spec, max_pages)
            return
        params = {"verb": "ListRecords", "resumptionToken": page.resumption_token}


def parse_oai_page(xml: bytes) -> OaiPage:
    """Parse one ``ListRecords`` response (pure). Deleted records are skipped."""
    root = parse_xml(xml)
    error = root.find(f"{{{NS_OAI}}}error")
    if error is not None:
        code = error.get("code") or "unknown"
        if code == "noRecordsMatch":
            return OaiPage()
        raise ValueError(f"OAI-PMH error: {code}")
    listing = root.find(f"{{{NS_OAI}}}ListRecords")
    if listing is None:
        raise ValueError("OAI-PMH response without ListRecords")
    nodes = listing.findall(f"{{{NS_OAI}}}record")
    records: list[PaperRecord] = []
    for node in nodes:
        header = node.find(f"{{{NS_OAI}}}header")
        if header is not None and header.get("status") == "deleted":
            continue
        raw = node.find(f".//{{{NS_RAW}}}arXivRaw")
        record = _raw_record(raw) if raw is not None else None
        if record is not None:
            records.append(record)
    token_node = listing.find(f"{{{NS_OAI}}}resumptionToken")
    token = (token_node.text or "").strip() if token_node is not None and token_node.text else ""
    return OaiPage(records=records, resumption_token=token or None, raw_count=len(nodes))


def _raw_text(element: ET.Element, tag: str) -> str | None:
    node = element.find(f"{{{NS_RAW}}}{tag}")
    if node is None or node.text is None:
        return None
    value = collapse(node.text)
    return value or None


def _rfc2822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _raw_record(raw: ET.Element) -> PaperRecord | None:
    parsed = parse_arxiv_id(_raw_text(raw, "id"))
    title = _raw_text(raw, "title")
    if parsed is None or not title:
        return None
    base = parsed[0]
    versions: list[tuple[int, str, datetime | None]] = []
    for node in raw.findall(f"{{{NS_RAW}}}version"):
        label = (node.get("version") or "").strip().lower()
        number = version_number(label)
        if number is None:
            continue
        versions.append((number, label, _rfc2822(_raw_text(node, "date"))))
    versions.sort()
    first = versions[0] if versions else None
    latest = versions[-1] if versions else None
    v1_date = first[2] if first is not None and first[0] == 1 else None
    latest_label = latest[1] if latest is not None else None

    categories = (_raw_text(raw, "categories") or "").split()
    comments = _raw_text(raw, "comments")
    doi = _raw_text(raw, "doi")
    journal_ref = _raw_text(raw, "journal-ref")
    identifiers = {"arxiv": base}
    if doi:
        identifiers["doi"] = doi.split()[0]  # occasionally several DOIs separated by spaces
    source_id = f"{base}{latest_label or ''}"
    return PaperRecord(
        source="arxiv_oai",
        source_id=source_id,
        title=title,
        abstract=_raw_text(raw, "abstract"),
        authors=split_creators(_raw_text(raw, "authors")),
        publication_date=v1_date.date() if v1_date else None,
        year=v1_date.year if v1_date else None,
        identifiers=identifiers,
        version_label=latest_label,
        categories=categories,
        primary_category=categories[0] if categories else None,
        venue=journal_ref,
        license=_raw_text(raw, "license"),
        landing_url=f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{source_id}",
        status="withdrawn" if comments and _WITHDRAWN_RE.search(comments) else None,
        source_updated_at=latest[2] if latest is not None else None,
        raw={
            "id": base,
            "versions": [[label, when.isoformat() if when else None] for _, label, when in versions],
            "categories": categories,
            "doi": doi,
            "journal_ref": journal_ref,
            "comments": comments,
        },
    )
