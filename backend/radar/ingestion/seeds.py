"""Seed resolution for ``POST /api/projects/{id}/seeds`` (SPEC §1, §6, API.md). Owner: enrich.

Known papers resolve without network; otherwise arXiv API (arXiv ids) or OpenAlex. The lookup runs
outside any transaction and the result is stored in one short write transaction (SPEC §2.1).
Errors: :class:`radar.errors.UnresolvableIdentifier` (no source knows the identifier, 422);
``SourceUnavailable``/``BudgetExceeded``/``EgressBlocked`` propagate (the router answers 503).
"""

from __future__ import annotations

import logging
from typing import Any

from radar.connectors import arxiv_api, openalex
from radar.db import session_scope
from radar.errors import UnresolvableIdentifier
from radar.net.http import SourceUnavailable
from radar.store.identifiers import parse_identifier
from radar.store.papers import PaperRecord, get_paper_by_identifier, upsert_paper

log = logging.getLogger(__name__)


def resolve_seed(identifier: str, **ctx: Any) -> tuple[int, str]:
    """``(paper_id, scheme)`` for a DOI, arXiv id/URL or OpenAlex id typed by the user."""
    parsed = parse_identifier(identifier)
    if parsed is None:
        raise UnresolvableIdentifier("Enter a DOI, an arXiv id or URL, or an OpenAlex id")
    scheme, value = parsed
    with session_scope() as session:
        known = get_paper_by_identifier(session, scheme, value)
        if known is not None:
            return known.id, scheme
    record = fetch_record(scheme, value, **ctx)
    if record is None:
        raise UnresolvableIdentifier("No source knows this identifier yet")
    with session_scope(write=True) as session:
        return upsert_paper(session, record).paper_id, scheme


def fetch_record(scheme: str, value: str, **ctx: Any) -> PaperRecord | None:
    """Look an identifier up at its sources (network). ``None`` when no source knows it."""
    if scheme == "arxiv":
        arxiv_error: SourceUnavailable | None = None
        try:
            records = arxiv_api.query_by_ids([value], **ctx)
            if records:
                return records[0]
        except SourceUnavailable as exc:  # e.g. the arXiv API rate limit; try OpenAlex instead
            log.info("seed lookup: arXiv API unavailable (%s); trying OpenAlex", exc)
            arxiv_error = exc
        work = openalex.get_work(f"arxiv:{value}", **ctx)
        if work is not None:
            return work.to_record()
        if arxiv_error is not None:
            raise arxiv_error  # unknown to OpenAlex *and* arXiv unreachable: ask to retry later
        return None
    if scheme == "doi":
        work = openalex.get_work(f"doi:{value}", **ctx)
        return work.to_record() if work is not None else None
    if scheme == "openalex":
        work = openalex.get_work(value, **ctx)
        return work.to_record() if work is not None else None
    return None
