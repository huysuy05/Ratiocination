"""Template explanations built only from stored features and evidence (SPEC §10). Owner: rank.

A reason is emitted only when its statement is true for the card:

* model reasons (question, similar_to_anchor, draft_match, preference) need the feature's normalized
  value to be at least ``STRONG`` (the card is in the top part of today's candidates on that feature);
* ``cites_anchor``/``cited_by_anchor`` need a CITES edge to an anchor; ``keyword`` needs one of the
  project's keywords in the title or abstract (its evidence names each keyword, where it was found and
  the paper's own words); ``category`` needs a shared arXiv category; ``recent`` needs a date.

Order: by contribution (largest first; reasons without one last). The first three are the card pills;
when none of them is a metadata (fact) reason but one exists, the best metadata reason moves into the
third place, so every card shows at least one plain fact. At most ``MAX_REASONS`` are stored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STRONG = 0.6
MAX_REASONS = 5
KEYWORDS_NAMED = 3
PILLS = 3
ANCHOR_KIND_TEXT = {
    "seed": "your seed paper",
    "relevant": "which you marked relevant",
    "saved": "which you saved",
}


@dataclass
class ExplainInput:
    """Everything one card's reasons may use (values are normalized feature values in [0, 1])."""

    values: dict[str, float | None]
    contributions: dict[str, float]
    question_cosine: float | None = None
    anchor: dict[str, Any] | None = None  # {paper_id, title, kind, cosine}
    draft: dict[str, Any] | None = None  # {document_id, chunk_id, filename, section, page, snippet}
    citation: dict[str, Any] | None = None  # {paper_id, title, kind, direction}
    preference: dict[str, Any] | None = None  # {n_labels, probability}
    category: str | None = None
    age_days: int | None = None
    keywords: list[dict[str, Any]] | None = None  # [{keyword, field: 'title'|'abstract', text}], title first
    extra: dict[str, Any] = field(default_factory=dict)


def _strong(item: ExplainInput, name: str) -> bool:
    value = item.values.get(name)
    return value is not None and value >= STRONG


def _quote(title: str) -> str:
    title = " ".join(str(title or "").split())
    return f"“{title[:120]}{'…' if len(title) > 120 else ''}”"


def _kw(keyword: str) -> str:
    return f"‘{' '.join(str(keyword).split())}’"


def _names(keywords: list[str]) -> str:
    quoted = [_kw(k) for k in keywords]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def keyword_text(matches: list[dict[str, Any]], limit: int = KEYWORDS_NAMED) -> str:
    """``Mentions your keyword ‘x’ in the title`` - each named keyword with the field it was found in.

    Title matches come first; at most ``limit`` keywords are named and the rest are counted ("(+2
    more)"; the evidence lists all of them), so the sentence stays short enough for a card pill.
    """
    named = matches[:limit]
    hidden = len(matches) - len(named)
    groups = [
        f"{_names([m['keyword'] for m in named if m['field'] == field])} in the {field}"
        for field in ("title", "abstract")
        if any(m["field"] == field for m in named)
    ]
    noun = "keyword" if len(matches) == 1 else "keywords"
    more = f" (+{hidden} more)" if hidden else ""
    return f"Mentions your {noun} {' and '.join(groups)}{more}"


def _age_text(days: int) -> str:
    if days <= 0:
        return "Published today"
    if days == 1:
        return "Published yesterday"
    return f"Published {days} days ago"


def reasons(item: ExplainInput) -> list[dict[str, Any]]:
    """The ordered ``Reason`` dicts for one card (API.md shapes)."""
    out: list[dict[str, Any]] = []

    def add(code: str, kind: str, text: str, evidence: Any, contribution: float | None) -> None:
        out.append(
            {
                "code": code,
                "kind": kind,
                "text": text,
                "evidence": evidence,
                "contribution": None if contribution is None else round(float(contribution), 6),
            }
        )

    if _strong(item, "question") and item.question_cosine is not None:
        add(
            "question",
            "model",
            "Close to your research question",
            {"cosine": round(float(item.question_cosine), 4)},
            item.contributions.get("question"),
        )
    if _strong(item, "anchor") and item.anchor is not None:
        a = item.anchor
        add(
            "similar_to_anchor",
            "model",
            f"Similar to {_quote(a['title'])} ({ANCHOR_KIND_TEXT[a['kind']]})",
            {
                "anchor_paper_id": a["paper_id"],
                "anchor_title": a["title"],
                "anchor_kind": a["kind"],
                "cosine": round(float(a["cosine"]), 4),
            },
            item.contributions.get("anchor"),
        )
    if _strong(item, "draft") and item.draft is not None:
        d = item.draft
        page = f", p. {d['page']}" if d.get("page") else ""
        add(
            "draft_match",
            "model",
            f"Matches your document {_quote(d['filename'])}, section {_quote(d['section'])}{page}",
            {
                "document_id": d["document_id"],
                "chunk_id": d["chunk_id"],
                "filename": d["filename"],
                "section": d["section"],
                "page": d.get("page"),
                "snippet": d["snippet"],
            },
            item.contributions.get("draft"),
        )
    if item.citation is not None and (item.values.get("citation") or 0) > 0:
        c = item.citation
        cites = c["direction"] == "cites"
        add(
            "cites_anchor" if cites else "cited_by_anchor",
            "metadata",
            f"{'Cites' if cites else 'Cited by'} {_quote(c['title'])} ({ANCHOR_KIND_TEXT[c['kind']]})",
            {
                "anchor_paper_id": c["paper_id"],
                "anchor_title": c["title"],
                "anchor_kind": c["kind"],
                "direction": c["direction"],
            },
            item.contributions.get("citation"),
        )
    if item.keywords and item.values.get("keyword") is not None:
        add(
            "keyword",
            "metadata",
            keyword_text(item.keywords),
            {
                "matches": [
                    {"keyword": m["keyword"], "field": m["field"], "text": m["text"]} for m in item.keywords
                ]
            },
            item.contributions.get("keyword"),
        )
    if _strong(item, "preference") and item.preference is not None:
        p = item.preference
        n = int(p["n_labels"])
        add(
            "preference",
            "model",
            f"Fits the papers you marked relevant ({n} rating{'' if n == 1 else 's'})",
            {"n_labels": n, "probability": round(float(p["probability"]), 4)},
            item.contributions.get("preference"),
        )
    if item.category:
        add(
            "category",
            "metadata",
            f"Listed in {item.category}, one of your project's categories",
            {"category": item.category},
            None,
        )
    if item.age_days is not None:
        add(
            "recent",
            "metadata",
            _age_text(item.age_days),
            {"days": max(0, int(item.age_days))},
            item.contributions.get("recency"),
        )

    out.sort(key=lambda r: (r["contribution"] is None, -(r["contribution"] or 0.0)))
    pills = out[:PILLS]
    if pills and all(r["kind"] != "metadata" for r in pills):
        fact = next((r for r in out[PILLS:] if r["kind"] == "metadata"), None)
        if fact is not None:
            out.remove(fact)
            out.insert(min(PILLS - 1, len(out)), fact)
    return out[:MAX_REASONS]


__all__ = [
    "ANCHOR_KIND_TEXT",
    "KEYWORDS_NAMED",
    "MAX_REASONS",
    "PILLS",
    "STRONG",
    "ExplainInput",
    "keyword_text",
    "reasons",
]
