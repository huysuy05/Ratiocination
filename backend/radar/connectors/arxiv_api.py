"""arXiv API queries (SPEC §7). Owner: ingest.

``https://export.arxiv.org/api/query`` — used for seed lookups (``id_list``) and the per-category
backfill of new projects. arXiv's terms allow one request every 3 s on one connection; the shared
HttpClient enforces that interval for ``export.arxiv.org``. Format verified against
``tests/fixtures/arxiv_api_query.xml`` (2026-10-12): ``published`` is the v1 date, ``updated`` this
version's date; ``arxiv:doi`` / ``arxiv:journal_ref`` appear for published works.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from radar.connectors._atom import NS, clean_categories, https, parse_datetime, parse_xml, text
from radar.net.http import SourceUnavailable, get_http
from radar.store.identifiers import parse_arxiv_id
from radar.store.papers import PaperRecord

log = logging.getLogger(__name__)

API_URL = "https://export.arxiv.org/api/query"
IDS_PER_REQUEST = 50
MAX_RESULTS_CAP = 2000  # arXiv's maximum slice size per request

_WITHDRAWN_RE = re.compile(r"\bwithdrawn\b", re.I)


def query_by_ids(ids: list[str], **ctx: Any) -> list[PaperRecord]:
    """Look up arXiv ids (``id_list``).

    Accepts any form :func:`parse_arxiv_id` understands; versions are dropped so the answer is the
    latest version. Unparseable ids are skipped. Requests go in chunks of 50 ids.
    """
    bases: list[str] = []
    for raw in ids:
        parsed = parse_arxiv_id(raw)
        if parsed is None:
            log.info("arXiv lookup: skipped an unparseable id")
            continue
        if parsed[0] not in bases:
            bases.append(parsed[0])
    records: list[PaperRecord] = []
    for start in range(0, len(bases), IDS_PER_REQUEST):
        chunk = bases[start : start + IDS_PER_REQUEST]
        params = {"id_list": ",".join(chunk), "start": 0, "max_results": len(chunk)}
        records.extend(_query(params, ctx))
    return records


def query_recent(
    category: str, since: date, until: date, max_results: int = 200, *, start: int = 0, **ctx: Any
) -> list[PaperRecord]:
    """``cat:{c} AND submittedDate:[...]`` sorted by submittedDate descending.

    ``start`` is the offset into that result list, so a caller pages through a busy category with
    ``start=0, max_results, 2 * max_results, …`` (the backfill does this when OAI-PMH is unavailable).
    A page shorter than ``max_results`` is the last one.
    """
    (cat,) = clean_categories([category])
    if since > until:
        raise ValueError("since must not be after until")
    params = {
        "search_query": f"cat:{cat} AND submittedDate:[{since:%Y%m%d}0000 TO {until:%Y%m%d}2359]",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": max(0, int(start)),
        "max_results": min(max(1, int(max_results)), MAX_RESULTS_CAP),
    }
    return _query(params, ctx)


def _query(params: dict[str, Any], ctx: dict[str, Any]) -> list[PaperRecord]:
    response = get_http().request(
        "GET",
        API_URL,
        provider="arxiv",
        call_type="query",
        params=params,
        job_id=ctx.get("job_id"),
        project_id=ctx.get("project_id"),
    )
    if response.status_code != 200:
        raise SourceUnavailable("arxiv", status=response.status_code)
    return parse_api_response(response.content)


def parse_api_response(xml: bytes) -> list[PaperRecord]:
    """Parse an arXiv API Atom response (pure). Error entries (bad ids/queries) are skipped."""
    root = parse_xml(xml)
    records: list[PaperRecord] = []
    for entry in root.findall("atom:entry", NS):
        record = _entry_record(entry)
        if record is not None:
            records.append(record)
        else:
            log.warning("arXiv API: skipped an entry without a valid id or title (error entry?)")
    return records


def _entry_record(entry: ET.Element) -> PaperRecord | None:
    parsed = parse_arxiv_id(text(entry, "atom:id"))
    title = text(entry, "atom:title")
    if parsed is None or not title or title.lower() == "error":
        return None
    base, version = parsed
    published = parse_datetime(text(entry, "atom:published"))
    updated = parse_datetime(text(entry, "atom:updated"))
    categories: list[str] = []
    for node in entry.findall("atom:category", NS):
        term = (node.get("term") or "").strip()
        if term and term not in categories:
            categories.append(term)
    primary_node = entry.find("arxiv:primary_category", NS)
    primary = (primary_node.get("term") or "").strip() if primary_node is not None else ""
    primary = primary or (categories[0] if categories else "")
    if primary and primary not in categories:
        categories.insert(0, primary)
    authors = [name for name in (text(a, "atom:name") for a in entry.findall("atom:author", NS)) if name]
    landing = pdf = None
    for link in entry.findall("atom:link", NS):
        href = link.get("href")
        if not href:
            continue
        if link.get("title") == "pdf":
            pdf = https(href)
        elif link.get("rel") == "alternate" and landing is None:
            landing = https(href)
    doi = text(entry, "arxiv:doi")
    journal_ref = text(entry, "arxiv:journal_ref")
    comment = text(entry, "arxiv:comment")

    identifiers = {"arxiv": base}
    if doi:
        identifiers["doi"] = doi
    source_id = f"{base}{version or ''}"
    return PaperRecord(
        source="arxiv_api",
        source_id=source_id,
        title=title,
        abstract=text(entry, "atom:summary"),
        authors=authors,
        publication_date=published.date() if published else None,
        year=published.year if published else None,
        identifiers=identifiers,
        version_label=version,
        categories=categories,
        primary_category=primary or None,
        venue=journal_ref,
        landing_url=landing or f"https://arxiv.org/abs/{source_id}",
        pdf_url=pdf or f"https://arxiv.org/pdf/{source_id}",
        status="withdrawn" if comment and _WITHDRAWN_RE.search(comment) else None,
        source_updated_at=updated,
        raw={
            "id": source_id,
            "published": published.isoformat() if published else None,
            "updated": updated.isoformat() if updated else None,
            "primary_category": primary or None,
            "categories": categories,
            "doi": doi,
            "journal_ref": journal_ref,
            "comment": comment,
        },
    )
