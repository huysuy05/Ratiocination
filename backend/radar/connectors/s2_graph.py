"""Semantic Scholar references and citations (SPEC §12). Owner: graph.

``GET /graph/v1/paper/{id}/references`` and ``/citations`` with ``fields=contexts,intents,
isInfluential,paperId,externalIds,title,year,abstract,authors,venue`` (fixtures
``s2_references.json``/``s2_citations.json``). Each link carries the neighbour as a stub
:class:`~radar.store.papers.PaperRecord` plus the citation contexts and intents S2 extracted. Without
``S2_API_KEY`` the shared pool often answers 429; ``radar.net.http`` then opens its breaker and
callers fall back to OpenAlex.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from radar.connectors.s2_papers import paper_record
from radar.net.http import SourceUnavailable, get_http
from radar.store.papers import PaperRecord

log = logging.getLogger(__name__)

API_URL = "https://api.semanticscholar.org/graph/v1/paper"
FIELDS = "contexts,intents,isInfluential,paperId,externalIds,title,year,abstract,authors,venue"
MAX_LIMIT = 1000


@dataclass
class S2Link:
    record: PaperRecord
    contexts: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    is_influential: bool | None = None


def parse_links(payload: Any, side: str) -> list[S2Link]:
    """Links from a references (``side='citedPaper'``) or citations (``'citingPaper'``) page (pure)."""
    links: list[S2Link] = []
    for item in (payload or {}).get("data") or [] if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        record = paper_record(item.get(side), stub=True)
        if record is None:
            continue
        influential = item.get("isInfluential")
        links.append(
            S2Link(
                record=record,
                contexts=[" ".join(str(c).split()) for c in item.get("contexts") or [] if c],
                intents=[str(i) for i in item.get("intents") or [] if i],
                is_influential=influential if isinstance(influential, bool) else None,
            )
        )
    return links


def _get(paper_id: str, edge: str, limit: int, ctx: dict[str, Any]) -> Any:
    response = get_http().request(
        "GET",
        f"{API_URL}/{quote(paper_id, safe=':')}/{edge}",
        provider="semantic_scholar",
        call_type="query",
        params={"fields": FIELDS, "limit": max(1, min(int(limit), MAX_LIMIT))},
        job_id=ctx.get("job_id"),
        project_id=ctx.get("project_id"),
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise SourceUnavailable("semantic_scholar", status=response.status_code)
    return response.json()


def get_references(paper_id: str, limit: int = 100, **ctx: Any) -> list[S2Link] | None:
    """Works cited by ``paper_id`` (``ARXIV:…``, ``DOI:…`` or an S2 paperId); ``None`` when S2 does not
    know the paper."""
    payload = _get(paper_id, "references", limit, ctx)
    return None if payload is None else parse_links(payload, "citedPaper")


def get_citations(paper_id: str, limit: int = 100, **ctx: Any) -> list[S2Link] | None:
    """Works citing ``paper_id``; ``None`` when S2 does not know the paper."""
    payload = _get(paper_id, "citations", limit, ctx)
    return None if payload is None else parse_links(payload, "citingPaper")


__all__ = ["S2Link", "get_citations", "get_references", "parse_links"]
