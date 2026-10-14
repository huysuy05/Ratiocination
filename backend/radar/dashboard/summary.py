"""AI summary of the findings of a user's liked papers (SPEC §14.4). Owner: dashboard.

Optional: available when ``ANTHROPIC_API_KEY`` is set. The input is up to 25 papers liked in the
period that have an abstract, most recently liked first, sent as ``[k] Title\\nAbstract`` with each
abstract cut at 1 500 characters. Only these public titles and abstracts leave the server - never
project names, questions, documents, names or emails.

Claude answers through the forced ``report_findings`` tool and every point must cite the bracket
numbers it relies on. The reply is checked before it is stored: cites outside 1..k are removed,
points left without cites are dropped (``dropped_points``), bracket numbers in the text are removed
and cites become paper ids. The latest summary for (user, project, days) is shown with ``stale`` when
its input papers differ from the current ones.

This is the one ``radar.dashboard`` module that may import ``radar.net``, ``radar.connectors`` and
``radar.jobs`` (SPEC §2).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import models
from radar.api.serializers import job_out
from radar.config import get_settings
from radar.connectors import anthropic
from radar.dashboard.common import Liked, Scope, liked, make_scope
from radar.errors import ApiError
from radar.jobs.runner import ACTIVE_STATUSES
from radar.models import Job, Paper, utcnow
from radar.schemas import AiStatus, FindingsSummary, SummaryPaperRef, SummaryPoint, SummaryTheme

if TYPE_CHECKING:
    from radar.jobs.runner import JobContext

KIND = "summarize_findings"
MAX_PAPERS = 25
MIN_PAPERS = 3
ABSTRACT_CHARS = 1500
MAX_THEMES = 6
MAX_POINTS = 4
TITLE_CHARS = 80
POINT_CHARS = 400
MAX_TOKENS = 1500
NOT_CONFIGURED = "AI summaries are off on this server (ANTHROPIC_API_KEY is not set)"
TOOL_NAME = "report_findings"
TOOL_DESCRIPTION = "Report the main findings of the numbered abstracts, grouped into themes."
SYSTEM_PROMPT = (
    "You summarize what a set of research papers found, for a researcher who marked them as relevant. "
    "You are given numbered abstracts. Use only these abstracts: never add facts, numbers or claims from "
    "outside them. Group the main findings into at most 6 themes with at most 4 points each. Each point "
    "states a finding in one or two plain sentences and lists in `cites` the numbers of every abstract it "
    "relies on; do not write the numbers in the text. Prefer concrete results (what was shown, measured "
    "or found) over descriptions of methods. When abstracts disagree, say so in a point that cites both "
    "sides. Answer with the report_findings tool."
)
INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "themes": {
            "type": "array",
            "maxItems": MAX_THEMES,
            "items": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "maxLength": TITLE_CHARS,
                        "description": "A short theme name",
                    },
                    "points": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_POINTS,
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string",
                                    "maxLength": POINT_CHARS,
                                    "description": "One finding in plain words",
                                },
                                "cites": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {"type": "integer", "minimum": 1},
                                    "description": "Numbers of the abstracts this point relies on",
                                },
                            },
                            "required": ["text", "cites"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["title", "points"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["themes"],
    "additionalProperties": False,
}
_BRACKETS = re.compile(r"\s*\[\d+(?:\s*[,;–-]\s*\d+)*\]")


# ------------------------------------------------------------------------------------------------
# Input
# ------------------------------------------------------------------------------------------------


def clip(value: Any, limit: int) -> str:
    """Whitespace-normalized text cut at a word boundary to ``limit`` characters (with an ellipsis)."""
    text = " ".join(value.split()) if isinstance(value, str) else ""
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.8:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def inputs(session: Session, scope: Scope, now: dict[int, Liked] | None = None) -> list[tuple[int, str, str]]:
    """``(paper_id, title, abstract)`` sent to Claude: liked papers with an abstract, newest first."""
    now = liked(session, scope, scope.start, scope.end) if now is None else now
    rows = {
        pid: (title, abstract)
        for pid, title, abstract in session.execute(
            select(Paper.id, Paper.canonical_title, Paper.abstract).where(Paper.id.in_(list(now) or [-1]))
        ).all()
    }
    out: list[tuple[int, str, str]] = []
    for pid in now:  # most recently liked first
        title, abstract = rows.get(pid, (None, None))
        if not abstract or not abstract.strip():
            continue
        out.append((pid, " ".join(str(title or "").split()), clip(abstract, ABSTRACT_CHARS)))
        if len(out) == MAX_PAPERS:
            break
    return out


def user_message(items: list[tuple[int, str, str]]) -> str:
    return "\n\n".join(f"[{k}] {title}\n{abstract}" for k, (_, title, abstract) in enumerate(items, 1))


def not_enough(n: int) -> ApiError:
    return ApiError(
        409,
        "not_enough_papers",
        f"An AI summary needs at least {MIN_PAPERS} liked papers with abstracts in this period (found {n})",
    )


# ------------------------------------------------------------------------------------------------
# Output
# ------------------------------------------------------------------------------------------------


def _point_text(value: Any) -> str:
    return clip(_BRACKETS.sub("", value), POINT_CHARS) if isinstance(value, str) else ""


def validate(tool_input: Any, paper_ids: list[int]) -> tuple[list[dict[str, Any]], int]:
    """``(themes, dropped_points)`` with themes as stored: ``[{title, points: [{text, cites}]}]``,
    cites mapped to paper ids."""
    k = len(paper_ids)
    raw_themes = tool_input.get("themes") if isinstance(tool_input, dict) else None
    themes: list[dict[str, Any]] = []
    dropped = 0
    for theme in raw_themes if isinstance(raw_themes, list) else []:
        if len(themes) == MAX_THEMES or not isinstance(theme, dict):
            continue
        points: list[dict[str, Any]] = []
        raw_points = theme.get("points")
        for point in raw_points if isinstance(raw_points, list) else []:
            point = point if isinstance(point, dict) else {}
            text = _point_text(point.get("text"))
            cites = point.get("cites")
            valid = sorted(
                {
                    c
                    for c in (cites if isinstance(cites, list) else [])
                    if isinstance(c, int) and not isinstance(c, bool) and 1 <= c <= k
                }
            )
            if not text or not valid:
                dropped += 1
                continue
            if len(points) < MAX_POINTS:
                points.append({"text": text, "cites": [paper_ids[c - 1] for c in valid]})
        title = clip(theme.get("title"), TITLE_CHARS)
        if title and points:
            themes.append({"title": title, "points": points})
    return themes, dropped


# ------------------------------------------------------------------------------------------------
# Dashboard state
# ------------------------------------------------------------------------------------------------


def _today_start() -> datetime:
    return utcnow().astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def jobs_today(session: Session, user_id: int) -> int:
    """Summaries the user requested today (UTC), whatever their outcome."""
    return len(
        session.scalars(
            select(Job.id).where(Job.kind == KIND, Job.owner_id == user_id, Job.created_at >= _today_start())
        ).all()
    )


def jobs_today_all(session: Session) -> int:
    """Summaries all users together requested today (UTC), whatever their outcome."""
    return int(
        session.scalar(select(func.count(Job.id)).where(Job.kind == KIND, Job.created_at >= _today_start()))
        or 0
    )


def remaining_today(session: Session, user_id: int) -> int:
    """Summaries the user may still request today: the smaller of what is left of the per-user
    (``RADAR_SUMMARIES_PER_DAY``) and the server-wide (``RADAR_SUMMARIES_GLOBAL_PER_DAY``) quota."""
    settings = get_settings()
    own = settings.summaries_per_day - jobs_today(session, user_id)
    everyone = settings.summaries_global_per_day - jobs_today_all(session)
    return max(0, min(own, everyone))


def seconds_until_tomorrow() -> int:
    """Seconds until the next UTC midnight, when the daily quotas reset (``Retry-After``)."""
    return max(1, int((_today_start() + timedelta(days=1) - utcnow()).total_seconds()))


def check_quota(session: Session, user_id: int) -> None:
    """429 ``quota_exceeded`` when the user or the whole server has used today's summaries.

    Called by the API inside the write transaction that inserts the job, so parallel requests are
    counted one after the other and cannot pass either cap together (SPEC §14.4).
    """
    settings = get_settings()
    retry = {"retry_after_seconds": seconds_until_tomorrow()}
    if jobs_today(session, user_id) >= settings.summaries_per_day:
        raise ApiError(
            429,
            "quota_exceeded",
            f"You can request {settings.summaries_per_day} AI summaries per day; try again tomorrow",
            retry,
        )
    if jobs_today_all(session) >= settings.summaries_global_per_day:
        raise ApiError(
            429,
            "quota_exceeded",
            "This server has used all of today's AI summaries; try again tomorrow",
            retry,
        )


def _jobs(session: Session, scope: Scope, *, active_only: bool = False) -> list[Job]:
    stmt = select(Job).where(Job.kind == KIND, Job.owner_id == scope.user_id)
    stmt = stmt.where(
        Job.project_id.is_(None) if scope.project_filter is None else Job.project_id == scope.project_filter
    )
    if active_only:
        stmt = stmt.where(Job.status.in_(ACTIVE_STATUSES))
    rows = session.scalars(stmt.order_by(Job.created_at.desc(), Job.id.desc()).limit(50)).all()
    return [job for job in rows if dict(job.params_json or {}).get("days") == scope.days]


def active_job(session: Session, scope: Scope) -> Job | None:
    jobs = _jobs(session, scope, active_only=True)
    return jobs[0] if jobs else None


def latest(session: Session, scope: Scope, current_ids: list[int]) -> FindingsSummary | None:
    """The newest stored summary for (user, project, days), ``stale`` against ``current_ids``."""
    table = models.FindingsSummary
    row = session.scalars(
        select(table)
        .where(
            table.user_id == scope.user_id,
            table.project_id == (scope.project_filter or 0),
            table.days == scope.days,
        )
        .order_by(table.created_at.desc(), table.id.desc())
        .limit(1)
    ).first()
    if row is None:
        return None
    content = dict(row.content_json or {})
    stored = [t for t in content.get("themes") or [] if isinstance(t, dict)]
    cited = {pid for t in stored for p in t.get("points") or [] for pid in p.get("cites") or []}
    titles = dict(
        session.execute(
            select(Paper.id, Paper.canonical_title).where(Paper.id.in_(sorted(cited) or [-1]))
        ).all()
    )
    themes = [
        SummaryTheme(
            title=str(t.get("title") or ""),
            points=[
                SummaryPoint(
                    text=str(p.get("text") or ""),
                    papers=[
                        SummaryPaperRef(id=pid, title=titles[pid])
                        for pid in p.get("cites") or []
                        if pid in titles
                    ],
                )
                for p in t.get("points") or []
            ],
        )
        for t in stored
    ]
    paper_ids = list(row.paper_ids_json or [])
    return FindingsSummary(
        id=row.id,
        created_at=row.created_at,
        model=row.model,
        n_papers=len(paper_ids),
        themes=themes,
        dropped_points=int(content.get("dropped_points") or 0),
        stale=set(paper_ids) != set(current_ids),
    )


def ai_status(session: Session, scope: Scope, summary_at: datetime | None) -> AiStatus:
    """Whether summaries are on, today's remaining requests and the job worth showing: a running one,
    or one newer than the latest summary (e.g. a failure)."""
    settings = get_settings()
    if not settings.ai_summary_configured:
        return AiStatus(available=False, model=None, remaining_today=0, job=None)
    job = next(
        (
            j
            for j in _jobs(session, scope)
            if j.status in ACTIVE_STATUSES or summary_at is None or j.created_at > summary_at
        ),
        None,
    )
    return AiStatus(
        available=True,
        model=settings.summary_model,
        remaining_today=remaining_today(session, scope.user_id),
        job=job_out(job) if job is not None else None,
    )


# ------------------------------------------------------------------------------------------------
# Job
# ------------------------------------------------------------------------------------------------


def run_summarize_findings(
    ctx: JobContext, user_id: int, days: int = 30, tz: str | None = None
) -> dict[str, Any]:
    """Job ``summarize_findings``; the scope project is ``ctx.project_id`` (``None`` = all projects).

    ``user_id`` is a parameter so that identical requests of different users never share a job (the
    dedupe key covers params, not the owner); it must be the job's owner.
    """
    if ctx.owner_id is not None and ctx.owner_id != user_id:
        raise ApiError(403, "forbidden", "A summary can only be requested for your own papers")
    settings = get_settings()
    if not settings.ai_summary_configured:
        raise ApiError(409, "not_configured", NOT_CONFIGURED)
    ctx.progress(0.05, "Collecting the abstracts of your liked papers")
    with ctx.session(write=False) as session:
        scope = make_scope(session, user_id, days, ctx.project_id, tz)
        items = inputs(session, scope)
    if len(items) < MIN_PAPERS:
        raise not_enough(len(items))

    ctx.progress(0.2, f"Claude is reading {len(items)} abstracts")
    call = anthropic.call_tool(
        model=settings.summary_model,
        system=SYSTEM_PROMPT,
        user=user_message(items),
        tool_name=TOOL_NAME,
        tool_description=TOOL_DESCRIPTION,
        input_schema=INPUT_SCHEMA,
        max_tokens=MAX_TOKENS,
    )
    paper_ids = [pid for pid, _, _ in items]
    themes, dropped = validate(call.input, paper_ids)
    if not themes:
        raise ApiError(
            502, "source_unavailable", "The AI summary had no points that cite the abstracts; try again later"
        )

    ctx.progress(0.95, "Saving the summary")
    with ctx.session() as session:
        row = models.FindingsSummary(
            user_id=user_id,
            project_id=ctx.project_id or 0,
            days=days,
            paper_ids_json=paper_ids,
            content_json={"themes": themes, "dropped_points": dropped},
            model=call.model or settings.summary_model,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
        )
        session.add(row)
        session.flush()
        summary_id = row.id
    return {
        "summary_id": summary_id,
        "n_papers": len(items),
        "themes": len(themes),
        "points": sum(len(t["points"]) for t in themes),
        "dropped_points": dropped,
        "input_tokens": call.input_tokens,
        "output_tokens": call.output_tokens,
    }


__all__ = [
    "INPUT_SCHEMA",
    "MIN_PAPERS",
    "SYSTEM_PROMPT",
    "active_job",
    "ai_status",
    "check_quota",
    "clip",
    "inputs",
    "jobs_today",
    "jobs_today_all",
    "latest",
    "not_enough",
    "remaining_today",
    "run_summarize_findings",
    "seconds_until_tomorrow",
    "user_message",
    "validate",
]
