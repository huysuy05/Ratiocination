"""OpenAlex works (SPEC §6, §12, §13). Owner: enrich.

Single-work lookups (``GET /works/<id>``) cost $0; list/filter calls cost $0.0001 each and are used
for expansion, disruption counts and author works. The budget check, ``api_key`` and ``mailto`` are
added by ``radar.net.http``.
Shapes verified against ``tests/fixtures/openalex_*.json`` (2026-10-12): OpenAlex merges a preprint
and its published version into one work, listing the arXiv copy under ``locations`` (id
``pmh:oai:arXiv.org:<id>`` or ``doi:10.48550/arxiv.<id>``); abstracts come as an inverted index.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from radar.net.http import SourceUnavailable, get_http
from radar.store.identifiers import ARXIV_DOI_PREFIX, normalize_doi, normalize_identifier, parse_arxiv_id
from radar.store.papers import PaperRecord

log = logging.getLogger(__name__)

API_URL = "https://api.openalex.org/works"
AUTHORS_URL = "https://api.openalex.org/authors"
IDS_PER_CALL = 50
MAX_PER_PAGE = 200
MAX_FILTER_VALUES = 100
"""OpenAlex accepts at most 100 values in one OR filter (``cites:W1|W2|…``)."""
AUTHOR_FIELDS = "id,display_name,orcid,works_count,cited_by_count,summary_stats,last_known_institutions"
SELECT_FIELDS = ",".join(
    (
        "id",
        "doi",
        "ids",
        "title",
        "display_name",
        "publication_date",
        "publication_year",
        "authorships",
        "topics",
        "keywords",
        "referenced_works",
        "related_works",
        "cited_by_count",
        "open_access",
        "best_oa_location",
        "primary_location",
        "locations",
        "is_retracted",
        "type",
        "abstract_inverted_index",
    )
)
_ARXIV_SOURCE_NAMES = ("arxiv",)


@dataclass
class OpenAlexTopic:
    external_id: str
    label: str
    score: float | None
    subfield: str | None
    field: str | None
    domain: str | None


@dataclass
class OpenAlexAuthor:
    openalex_id: str
    display_name: str
    orcid: str | None
    works_count: int | None
    cited_by_count: int | None
    h_index: int | None
    institution: str | None


@dataclass
class OpenAlexWork:
    openalex_id: str
    doi: str | None
    arxiv_id: str | None
    title: str
    abstract: str | None
    publication_date: date | None
    year: int | None
    authors: list[str]
    author_ids: list[str | None]  # OpenAlex author ids ('A\\d+'), aligned with ``authors``
    topics: list[OpenAlexTopic]
    keywords: list[str]
    referenced_works: list[str]
    related_works: list[str]
    cited_by_count: int | None
    is_oa: bool | None
    oa_url: str | None
    best_oa_pdf: str | None
    is_retracted: bool
    type: str | None
    venue: str | None
    raw: dict[str, Any]

    def to_record(self) -> PaperRecord:
        """A ``PaperRecord`` (source ``openalex``). It creates no version row (arXiv versions come from
        arXiv; OpenAlex merges preprint and published versions into one work)."""
        identifiers = {"openalex": self.openalex_id}
        if self.doi:
            identifiers["doi"] = self.doi
        if self.arxiv_id:
            identifiers["arxiv"] = self.arxiv_id
        return PaperRecord(
            source="openalex",
            source_id=self.openalex_id,
            title=self.title,
            abstract=self.abstract,
            authors=list(self.authors),
            publication_date=reliable_date(self.publication_date),
            year=self.year,
            identifiers=identifiers,
            venue=self.venue,
            is_oa=self.is_oa,
            oa_url=self.oa_url,
            best_oa_pdf=self.best_oa_pdf,
            cited_by_count=self.cited_by_count,
            paper_type=self.type,
            status="retracted" if self.is_retracted else None,
            raw={
                "id": self.openalex_id,
                "doi": self.doi,
                "arxiv": self.arxiv_id,
                "type": self.type,
                "publication_date": self.publication_date.isoformat() if self.publication_date else None,
                "is_retracted": self.is_retracted,
                "topics": [[t.external_id, t.score] for t in self.topics],
                "referenced_works": len(self.referenced_works),
            },
        )


# ------------------------------------------------------------------------------------------------
# HTTP
# ------------------------------------------------------------------------------------------------


def _lookup_path(identifier: str) -> str:
    text = str(identifier or "").strip()
    lowered = text.lower()
    if lowered.startswith("doi:"):
        doi = normalize_doi(text[4:])
        if doi is None:
            raise ValueError("not a DOI")
        return f"doi:{doi}"
    if lowered.startswith("arxiv:"):
        parsed = parse_arxiv_id(text[6:])
        if parsed is None:
            raise ValueError("not an arXiv id")
        return f"doi:{ARXIV_DOI_PREFIX}{parsed[0].lower()}"
    normalized = normalize_identifier("openalex", text)
    if normalized is None:
        raise ValueError("expected 'doi:…', 'arxiv:…' or an OpenAlex W-id")
    return normalized[1]


def get_work(identifier: str, **ctx: Any) -> OpenAlexWork | None:
    """``'doi:10.x'`` | ``'W123'`` | ``'arxiv:2504.08385'``; 404 -> ``None``."""
    response = get_http().request(
        "GET",
        f"{API_URL}/{_lookup_path(identifier)}",
        provider="openalex",
        call_type="lookup",
        params={"select": SELECT_FIELDS},
        job_id=ctx.get("job_id"),
        project_id=ctx.get("project_id"),
    )
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise SourceUnavailable("openalex", status=response.status_code)
    return parse_work(response.json())


def _list(params: dict[str, Any], ctx: dict[str, Any]) -> list[OpenAlexWork]:
    response = get_http().request(
        "GET",
        API_URL,
        provider="openalex",
        call_type="list",
        params={**params, "select": SELECT_FIELDS},
        job_id=ctx.get("job_id"),
        project_id=ctx.get("project_id"),
    )
    if response.status_code != 200:
        raise SourceUnavailable("openalex", status=response.status_code)
    works = [parse_work(item) for item in response.json().get("results") or []]
    return [w for w in works if w is not None]


def get_works_by_ids(openalex_ids: list[str], **ctx: Any) -> list[OpenAlexWork]:
    """``filter=openalex:W1|W2`` (<=50 per call)."""
    ids: list[str] = []
    for raw in openalex_ids:
        normalized = normalize_identifier("openalex", raw)
        if normalized is not None and normalized[1] not in ids:
            ids.append(normalized[1])
    works: list[OpenAlexWork] = []
    for start in range(0, len(ids), IDS_PER_CALL):
        chunk = ids[start : start + IDS_PER_CALL]
        works.extend(_list({"filter": "openalex:" + "|".join(chunk), "per-page": len(chunk)}, ctx))
    return works


def get_citing_works(openalex_id: str, limit: int = 100, **ctx: Any) -> list[OpenAlexWork]:
    """``filter=cites:W...``, newest first (one page of up to 200)."""
    normalized = normalize_identifier("openalex", openalex_id)
    if normalized is None:
        raise ValueError("expected an OpenAlex W-id")
    per_page = max(1, min(int(limit), MAX_PER_PAGE))
    return _list(
        {"filter": f"cites:{normalized[1]}", "per-page": per_page, "sort": "publication_date:desc"}, ctx
    )


def _work_id(openalex_id: str) -> str:
    normalized = normalize_identifier("openalex", openalex_id)
    if normalized is None:
        raise ValueError("expected an OpenAlex W-id")
    return normalized[1]


def count_works(filter_value: str, **ctx: Any) -> int:
    """``meta.count`` of ``/works?filter=…`` (one list call, $0.0001; SPEC §13)."""
    response = get_http().request(
        "GET",
        API_URL,
        provider="openalex",
        call_type="list",
        params={"filter": filter_value, "per-page": 1, "select": "id"},
        job_id=ctx.get("job_id"),
        project_id=ctx.get("project_id"),
    )
    if response.status_code != 200:
        raise SourceUnavailable("openalex", status=response.status_code)
    count = (response.json().get("meta") or {}).get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise SourceUnavailable(
            "openalex", status=response.status_code, message="openalex: no count in response"
        )
    return count


def disruption_counts(openalex_id: str, references: list[str], **ctx: Any) -> tuple[int, int, int]:
    """``(N_F, N_B, N_R)`` for the CD index of ``openalex_id`` with ``references`` (SPEC §13).

    N_F: works citing W; N_B: works citing W and at least one reference (``cites:W,cites:R1|R2…`` - a
    comma is AND, a bar is OR); N_R: works citing at least one reference. ``references`` must hold
    1-100 W-ids (OpenAlex's OR limit). Three list calls.
    """
    work = _work_id(openalex_id)
    refs = [_work_id(r) for r in references]
    if not refs or len(refs) > MAX_FILTER_VALUES:
        raise ValueError("between 1 and 100 references are required")
    any_ref = "cites:" + "|".join(refs)
    n_f = count_works(f"cites:{work}", **ctx)
    n_b = count_works(f"cites:{work},{any_ref}", **ctx) if n_f else 0
    n_r = count_works(any_ref, **ctx)
    return n_f, n_b, n_r


def get_authors(author_ids: list[str], **ctx: Any) -> list[OpenAlexAuthor]:
    """``/authors?filter=openalex:A1|A2…`` (<=50 per call)."""
    ids: list[str] = []
    for raw in author_ids:
        short = _short_id(raw, "A")
        if short is not None and short not in ids:
            ids.append(short)
    authors: list[OpenAlexAuthor] = []
    for start in range(0, len(ids), IDS_PER_CALL):
        chunk = ids[start : start + IDS_PER_CALL]
        response = get_http().request(
            "GET",
            AUTHORS_URL,
            provider="openalex",
            call_type="list",
            params={"filter": "openalex:" + "|".join(chunk), "per-page": len(chunk), "select": AUTHOR_FIELDS},
            job_id=ctx.get("job_id"),
            project_id=ctx.get("project_id"),
        )
        if response.status_code != 200:
            raise SourceUnavailable("openalex", status=response.status_code)
        authors.extend(a for a in (parse_author(item) for item in response.json().get("results") or []) if a)
    return authors


def get_author_top_works(author_id: str, limit: int = 8, **ctx: Any) -> list[OpenAlexWork]:
    """The author's most-cited works (``filter=author.id:A…``, ``sort=cited_by_count:desc``)."""
    short = _short_id(author_id, "A")
    if short is None:
        raise ValueError("expected an OpenAlex A-id")
    per_page = max(1, min(int(limit), MAX_PER_PAGE))
    return _list({"filter": f"author.id:{short}", "sort": "cited_by_count:desc", "per-page": per_page}, ctx)


# ------------------------------------------------------------------------------------------------
# Parsing (pure)
# ------------------------------------------------------------------------------------------------


def parse_author(data: Any) -> OpenAlexAuthor | None:
    """An :class:`OpenAlexAuthor` from one OpenAlex author object, or ``None`` when unusable (pure)."""
    if not isinstance(data, dict):
        return None
    openalex_id = _short_id(data.get("id"), "A")
    name = " ".join(str(data.get("display_name") or "").split())
    if openalex_id is None or not name:
        return None
    stats = data.get("summary_stats") if isinstance(data.get("summary_stats"), dict) else {}
    institutions = data.get("last_known_institutions") or []
    institution = _name(institutions[0]) if institutions and isinstance(institutions, list) else None
    orcid = data.get("orcid")

    def count(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return OpenAlexAuthor(
        openalex_id=openalex_id,
        display_name=name,
        orcid=str(orcid).rsplit("/", 1)[-1] if orcid else None,
        works_count=count(data.get("works_count")),
        cited_by_count=count(data.get("cited_by_count")),
        h_index=count(stats.get("h_index")),
        institution=institution,
    )


def reliable_date(value: date | None) -> date | None:
    """Drop 1 January dates: OpenAlex (and S2) use YYYY-01-01 as a "sometime in YYYY" placeholder
    (the SPECTER fixture: 2020-01-01 vs arXiv v1 2020-04-15). Because the store keeps the EARLIEST
    known date, a placeholder would otherwise beat the real one. The year is kept separately."""
    if value is None or (value.month == 1 and value.day == 1):
        return None
    return value


def reconstruct_abstract(inverted_index: dict[str, Any] | None) -> str | None:
    """Rebuild abstract text from OpenAlex's inverted index (pure)."""
    if not isinstance(inverted_index, dict) or not inverted_index:
        return None
    positions: dict[int, str] = {}
    for word, places in inverted_index.items():
        if not isinstance(places, list):
            continue
        for place in places:
            if isinstance(place, int) and place >= 0:
                positions[place] = str(word)
    if not positions:
        return None
    text = " ".join(positions[i] for i in sorted(positions))
    return " ".join(text.split()) or None


def _short_id(value: Any, prefix: str) -> str | None:
    """``https://openalex.org/T10028`` -> ``T10028`` (any OpenAlex entity URL)."""
    if not value:
        return None
    tail = str(value).rstrip("/").rsplit("/", 1)[-1]
    return tail if tail[:1].upper() == prefix else None


def _name(entity: Any) -> str | None:
    if isinstance(entity, dict):
        value = entity.get("display_name")
        return str(value) if value else None
    return None


def _arxiv_id(data: dict[str, Any]) -> str | None:
    """The arXiv base id of a work, from its DOI or any arXiv location."""
    candidates: list[Any] = [data.get("doi")]
    for location in data.get("locations") or []:
        if isinstance(location, dict):
            candidates.extend((location.get("id"), location.get("landing_page_url"), location.get("doi")))
    for candidate in candidates:
        if not candidate:
            continue
        text = str(candidate)
        if text.lower().startswith("pmh:"):
            text = text[4:]
        if "arxiv" not in text.lower():
            continue
        if text.lower().startswith("doi:"):
            text = text[4:]
        parsed = parse_arxiv_id(text)
        if parsed is not None:
            return parsed[0]
    return None


def _venue(data: dict[str, Any]) -> str | None:
    location = data.get("primary_location")
    source = location.get("source") if isinstance(location, dict) else None
    name = _name(source)
    if name is None or any(word in name.lower() for word in _ARXIV_SOURCE_NAMES):
        return None  # "arXiv (Cornell University)" is a repository, not a venue
    return name


def _date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_work(data: Any) -> OpenAlexWork | None:
    """An :class:`OpenAlexWork` from one OpenAlex work object, or ``None`` when unusable (pure)."""
    if not isinstance(data, dict):
        return None
    openalex_id = _short_id(data.get("id"), "W")
    title = " ".join(str(data.get("title") or data.get("display_name") or "").split())
    if openalex_id is None or not title:
        return None
    doi = normalize_doi(data.get("doi"))
    if doi is not None and doi.startswith(ARXIV_DOI_PREFIX):
        doi = None  # arXiv DOIs are identifiers of scheme arxiv (SPEC §5)
    topics = []
    for item in data.get("topics") or []:
        if not isinstance(item, dict):
            continue
        topic_id = _short_id(item.get("id"), "T")
        label = _name(item)
        if topic_id is None or label is None:
            continue
        score = item.get("score")
        topics.append(
            OpenAlexTopic(
                external_id=topic_id,
                label=label,
                score=float(score) if isinstance(score, (int, float)) else None,
                subfield=_name(item.get("subfield")),
                field=_name(item.get("field")),
                domain=_name(item.get("domain")),
            )
        )
    topics.sort(key=lambda t: -(t.score or 0.0))
    authors: list[str] = []
    author_ids: list[str | None] = []
    for authorship in data.get("authorships") or []:
        if isinstance(authorship, dict):
            author = authorship.get("author")
            name = _name(author) or authorship.get("raw_author_name")
            if name:
                authors.append(" ".join(str(name).split()))
                author_ids.append(_short_id(author.get("id"), "A") if isinstance(author, dict) else None)
    open_access = data.get("open_access") if isinstance(data.get("open_access"), dict) else {}
    best = data.get("best_oa_location") if isinstance(data.get("best_oa_location"), dict) else {}
    publication_date = _date(data.get("publication_date"))
    year = data.get("publication_year")
    cited = data.get("cited_by_count")
    return OpenAlexWork(
        openalex_id=openalex_id,
        doi=doi,
        arxiv_id=_arxiv_id(data),
        title=title,
        abstract=reconstruct_abstract(data.get("abstract_inverted_index")),
        publication_date=publication_date,
        year=publication_date.year if publication_date else (year if isinstance(year, int) else None),
        authors=authors,
        author_ids=author_ids,
        topics=topics,
        keywords=[k for k in (_name(item) for item in data.get("keywords") or []) if k],
        referenced_works=[w for w in (_short_id(r, "W") for r in data.get("referenced_works") or []) if w],
        related_works=[w for w in (_short_id(r, "W") for r in data.get("related_works") or []) if w],
        cited_by_count=cited if isinstance(cited, int) and not isinstance(cited, bool) else None,
        is_oa=open_access.get("is_oa") if isinstance(open_access.get("is_oa"), bool) else None,
        oa_url=open_access.get("oa_url") or None,
        best_oa_pdf=best.get("pdf_url") or None,
        is_retracted=data.get("is_retracted") is True,
        type=str(data.get("type")) if data.get("type") else None,
        venue=_venue(data),
        raw=data,
    )
