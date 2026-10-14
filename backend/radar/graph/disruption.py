"""The CD (disruption) index of a work (SPEC §13). Owner: graph.

Funk & Owen-Smith (2017), as used by Wu, Wang & Evans (Nature 2019) and Park, Leahey & Funk (Nature
2023): for a focal work W with references R, every later work that cites W or any of R is

* ``i`` - cites W but none of R (W eclipsed its references), counted n_i;
* ``j`` - cites W and at least one of R (W consolidates its references), counted n_j;
* ``k`` - cites some of R but not W, counted n_k;

``CD = (n_i − n_j) / (n_i + n_j + n_k)`` in [−1, 1]: +1 is maximally disruptive, −1 maximally
consolidating. The counts come from three OpenAlex list calls ($0.0003): N_F (citing W), N_B (citing W
and any of R), N_R (citing any of R); then n_i = N_F − N_B, n_j = N_B, n_k = N_R − N_B. At most the
first 100 references are used (OpenAlex's OR limit). Counts are all-time (Park et al. use 5 years).

:func:`cd_index` is pure; :func:`compute` looks the work up (free) and makes the three calls (never in
a write transaction); :func:`store` writes the result on the paper.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from radar.connectors import openalex
from radar.models import Paper, utcnow

log = logging.getLogger(__name__)

MAX_REFERENCES = openalex.MAX_FILTER_VALUES
FRESH_FOR = timedelta(days=30)


@dataclass(frozen=True)
class CDResult:
    cd: float | None
    n_i: int
    n_j: int
    n_k: int
    refs_used: int
    refs_total: int

    @property
    def n_citing(self) -> int:
        return self.n_i + self.n_j


def cd_index(n_f: int, n_b: int, n_r: int, *, refs_used: int = 1, refs_total: int | None = None) -> CDResult:
    """The CD index from the three counts. Inputs are clamped to consistent values (``0 ≤ N_B ≤ N_F``,
    ``N_R ≥ N_B``); the index is ``None`` without references, without citing works, or with an empty
    denominator."""
    n_f = max(0, int(n_f))
    n_b = max(0, min(int(n_b), n_f))
    n_r = max(int(n_r), n_b)
    n_i, n_j, n_k = n_f - n_b, n_b, n_r - n_b
    total = n_i + n_j + n_k
    cd = None if refs_used <= 0 or n_f == 0 or total == 0 else (n_i - n_j) / total
    return CDResult(
        cd=cd,
        n_i=n_i,
        n_j=n_j,
        n_k=n_k,
        refs_used=refs_used,
        refs_total=refs_used if refs_total is None else refs_total,
    )


def is_fresh(computed_at: datetime | None, now: datetime | None = None) -> bool:
    return computed_at is not None and computed_at > (now or utcnow()) - FRESH_FOR


def compute(openalex_id: str, references: list[str], **ctx: Any) -> CDResult:
    """CD index of a work whose reference list is known (network: 0 or 3 list calls)."""
    refs = list(dict.fromkeys(references))
    used = refs[:MAX_REFERENCES]
    if not used:
        return cd_index(0, 0, 0, refs_used=0, refs_total=0)
    n_f, n_b, n_r = openalex.disruption_counts(openalex_id, used, **ctx)
    return cd_index(n_f, n_b, n_r, refs_used=len(used), refs_total=len(refs))


def store(session: Session, paper_id: int, result: CDResult, *, now: datetime | None = None) -> None:
    """Write ``result`` on the paper (write session)."""
    paper = session.get(Paper, paper_id)
    if paper is None:
        return
    paper.disruption_cd = result.cd
    paper.disruption_ni = result.n_i
    paper.disruption_nj = result.n_j
    paper.disruption_nk = result.n_k
    paper.disruption_refs = result.refs_used
    paper.disruption_refs_total = result.refs_total
    paper.disruption_at = now or utcnow()


__all__ = ["FRESH_FOR", "MAX_REFERENCES", "CDResult", "cd_index", "compute", "is_fresh", "store"]
