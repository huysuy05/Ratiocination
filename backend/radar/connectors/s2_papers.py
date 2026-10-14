"""Semantic Scholar paper batch and record helper (SPEC §6, §12; optional source). Owner: enrich.

``POST /graph/v1/paper/batch`` takes up to 500 ids (``ARXIV:<id>``, ``DOI:<doi>`` or a paperId) and
returns a list aligned with the request, ``null`` for unknown ids (fixture ``s2_batch.json``). Without
``S2_API_KEY`` the shared pool rate-limits hard (429); ``radar.net.http`` then opens a breaker, so
callers must treat Semantic Scholar as optional.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from radar.net.http import SourceUnavailable, get_http
from radar.store.papers import PaperRecord

log = logging.getLogger(__name__)

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
BATCH_SIZE = 500
PAPER_FIELDS = (
    "paperId,externalIds,title,year,publicationDate,abstract,authors,venue,citationCount,openAccessPdf"
)


def get_papers_batch(ids: list[str], fields: list[str] | str, **ctx: Any) -> list[dict[str, Any] | None]:
    """POST ``/graph/v1/paper/batch`` (<=500); unknown ids are ``None``, positions preserved."""
    field_list = fields if isinstance(fields, str) else ",".join(fields)
    out: list[dict[str, Any] | None] = []
    for start in range(0, len(ids), BATCH_SIZE):
        chunk = list(ids[start : start + BATCH_SIZE])
        response = get_http().request(
            "POST",
            BATCH_URL,
            provider="semantic_scholar",
            call_type="batch",
            params={"fields": field_list},
            json={"ids": chunk},
            timeout=60.0,
            job_id=ctx.get("job_id"),
            project_id=ctx.get("project_id"),
        )
        if response.status_code != 200:
            raise SourceUnavailable("semantic_scholar", status=response.status_code)
        data = response.json()
        if not isinstance(data, list) or len(data) != len(chunk):
            raise SourceUnavailable("semantic_scholar", message="semantic_scholar batch: unexpected answer")
        out.extend(item if isinstance(item, dict) else None for item in data)
    return out


def paper_record(item: dict[str, Any] | None, *, stub: bool = False) -> PaperRecord | None:
    """A ``PaperRecord`` (source ``s2``) from a Semantic Scholar paper object (pure).

    ``stub=True`` marks papers first seen as citation neighbours (graph expansion, SPEC §12).
    """
    if not isinstance(item, dict):
        return None
    paper_id = str(item.get("paperId") or "").strip().lower()
    title = " ".join(str(item.get("title") or "").split())
    if len(paper_id) != 40 or not title:
        return None
    external = item.get("externalIds") if isinstance(item.get("externalIds"), dict) else {}
    identifiers = {"s2": paper_id}
    for scheme, key in (("doi", "DOI"), ("arxiv", "ArXiv"), ("pmid", "PubMed")):
        value = external.get(key)
        if value:
            identifiers[scheme] = str(value)
    authors = [
        " ".join(str(a.get("name") or "").split())
        for a in item.get("authors") or []
        if isinstance(a, dict) and a.get("name")
    ]
    oa = item.get("openAccessPdf") if isinstance(item.get("openAccessPdf"), dict) else {}
    year = item.get("year")
    published = None
    if item.get("publicationDate"):
        try:
            published = date.fromisoformat(str(item["publicationDate"])[:10])
        except ValueError:
            published = None
        if published is not None and published.month == 1 and published.day == 1:
            published = None  # "sometime in YYYY" placeholder; see openalex.reliable_date
    cited = item.get("citationCount")
    return PaperRecord(
        source="s2",
        source_id=paper_id,
        title=title,
        abstract=" ".join(str(item.get("abstract") or "").split()) or None,
        authors=authors,
        publication_date=published,
        year=published.year if published else (year if isinstance(year, int) else None),
        identifiers=identifiers,
        venue=" ".join(str(item.get("venue") or "").split()) or None,
        is_oa=True if oa.get("url") else None,
        best_oa_pdf=oa.get("url") or None,
        cited_by_count=cited if isinstance(cited, int) and not isinstance(cited, bool) else None,
        status="stub" if stub else None,
        raw={"paperId": paper_id, "externalIds": {k: external[k] for k in sorted(external)}},
    )
