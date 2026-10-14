"""BibTeX export of a project's library (docs/API.md "Papers and library"). Owner: library.

One entry per library paper (relevant and/or saved), oldest label first. Keys are
``<first-author surname><year><first title word>`` (ASCII, deduplicated with a/b/c suffixes). arXiv
papers without a venue are ``@misc`` with ``eprint``/``archivePrefix``/``primaryClass``; papers with a
venue are ``@article`` with ``journal``. Field values escape BibTeX's special characters; titles are
wrapped in an extra brace pair so capitalization survives styles.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from radar.api.deps import OwnerId, get_project_or_404, split_csv
from radar.api.serializers import paper_summaries
from radar.db import session_scope
from radar.models import ProjectPaperState
from radar.schemas import PaperSummary
from radar.store.identifiers import surname_norm
from radar.store.states import LIBRARY_STATES

router = APIRouter(prefix="/api", tags=["exports"])

_SPECIAL = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_STOPWORDS = frozenset({"a", "an", "the", "on", "of", "for", "in", "to", "and", "with", "towards", "toward"})


def escape(value: str) -> str:
    """Escape BibTeX/LaTeX special characters (letters with accents are kept as UTF-8)."""
    return "".join(_SPECIAL.get(ch, ch) for ch in " ".join(str(value).split()))


def _ascii(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^A-Za-z0-9]", "", folded)


def base_key(paper: PaperSummary) -> str:
    surname = _ascii(surname_norm(paper.authors[0].name)) if paper.authors else ""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", paper.title) if w.lower() not in _STOPWORDS]
    first = _ascii(words[0]).lower() if words else "paper"
    year = str(paper.year) if paper.year else "nd"
    return f"{(surname or 'anon').lower()}{year}{first}"


def entry(paper: PaperSummary, key: str) -> str:
    fields: list[tuple[str, str]] = [("title", "{" + escape(paper.title) + "}")]
    if paper.authors:
        fields.append(("author", " and ".join(escape(a.name) for a in paper.authors)))
    if paper.year:
        fields.append(("year", str(paper.year)))
    arxiv = paper.identifiers.arxiv
    if paper.venue:
        kind = "article"
        fields.append(("journal", escape(paper.venue)))
    else:
        kind = "misc"
    if arxiv:
        fields += [("eprint", arxiv), ("archivePrefix", "arXiv")]
        if paper.primary_category:
            fields.append(("primaryClass", paper.primary_category))
    if paper.identifiers.doi:
        fields.append(("doi", escape(paper.identifiers.doi)))
    url = paper.links.abs or paper.links.doi
    if url:
        fields.append(("url", url))
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields)
    return f"@{kind}{{{key},\n{body}\n}}\n"


def bibtex(papers: list[PaperSummary]) -> str:
    """BibTeX text for ``papers`` in order, with unique keys."""
    bases = [base_key(p) for p in papers]
    totals = Counter(bases)
    seen: Counter[str] = Counter()
    entries: list[str] = []
    for paper, base in zip(papers, bases, strict=True):
        key = base
        if totals[base] > 1:
            key = f"{base}{chr(ord('a') + seen[base])}" if seen[base] < 26 else f"{base}{seen[base]}"
        seen[base] += 1
        entries.append(entry(paper, key))
    return "\n".join(entries)


@router.get(
    "/exports/bibtex",
    response_class=Response,
    responses={200: {"content": {"text/x-bibtex": {}}, "description": "BibTeX file"}},
)
def export_bibtex(
    project_id: int, owner_id: OwnerId, states: str = Query("relevant,saved", max_length=100)
) -> Response:
    """``200 text/x-bibtex`` with ``Content-Disposition: attachment; filename="ratiocination-<id>.bib"``."""
    wanted = split_csv(states, allowed=LIBRARY_STATES, param="states", default=list(LIBRARY_STATES))
    with session_scope() as session:
        get_project_or_404(session, project_id, owner_id)
        ids = list(
            session.scalars(
                select(ProjectPaperState.paper_id)
                .where(ProjectPaperState.project_id == project_id, ProjectPaperState.state.in_(wanted))
                .order_by(ProjectPaperState.updated_at, ProjectPaperState.paper_id)
            )
        )
        summaries = paper_summaries(session, ids)
    text = bibtex([summaries[i] for i in ids if i in summaries])
    return Response(
        content=text.encode("utf-8"),
        media_type="text/x-bibtex; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="ratiocination-{project_id}.bib"'},
    )
