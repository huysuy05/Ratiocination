"""Activity: what the user swiped, opened and read (SPEC §14.1). Owner: dashboard.

Counts use non-undone interactions of the projects in scope, bucketed by local day (or by local week
starting Monday for the 365-day view). Read marks belong to the user; with a project filter only
marks on papers that project touched count. The streak counts consecutive local days with at least
one swipe, ending today (or yesterday when today has none yet).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar.dashboard.common import POSITIVE, SWIPES, Scope
from radar.models import Interaction, PaperRead, Project, ProjectPaperState, RecommendationSlate
from radar.schemas import ActivityPoint, ActivityTotals, AllTimeTotals, DashboardActivity, ProjectActivity

STREAK_LOOKBACK = timedelta(days=400)


def _rate(positive: int, swiped: int) -> float | None:
    return round(positive / swiped, 4) if swiped else None


def _bucket(scope: Scope, day: date) -> date:
    return day - timedelta(days=day.weekday()) if scope.bucket == "week" else day


def streak(days_with_swipes: set[date], today: date) -> int:
    """Consecutive days with a swipe ending today, or yesterday when today has none yet."""
    day = today if today in days_with_swipes else today - timedelta(days=1)
    count = 0
    while day in days_with_swipes:
        count += 1
        day -= timedelta(days=1)
    return count


def _scoped_reads(session: Session, scope: Scope) -> list[tuple[int, object]]:
    query = select(PaperRead.paper_id, PaperRead.read_at).where(PaperRead.user_id == scope.user_id)
    if scope.project_filter is not None:
        touched = select(Interaction.paper_id).where(Interaction.project_id == scope.project_filter)
        query = query.where(PaperRead.paper_id.in_(touched))
    return list(session.execute(query).all())


def activity(session: Session, scope: Scope) -> DashboardActivity:
    ids = scope.project_ids or [-1]
    rows = session.execute(
        select(Interaction.timestamp, Interaction.action, Interaction.paper_id, Interaction.project_id).where(
            Interaction.project_id.in_(ids),
            Interaction.undone_at.is_(None),
            Interaction.timestamp >= scope.utc(scope.previous_start),
            Interaction.timestamp < scope.utc(scope.end + timedelta(days=1)),
        )
    ).all()
    now_counts: Counter[str] = Counter()
    before_counts: Counter[str] = Counter()
    opened_now: set[int] = set()
    opened_before: set[int] = set()
    series: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    by_project: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for timestamp, action, paper_id, project_id in rows:
        day = scope.local_day(timestamp)
        current = day >= scope.start
        counts = now_counts if current else before_counts
        if action == "open":
            (opened_now if current else opened_before).add(paper_id)
            continue
        counts[action] += 1
        if current and action in SWIPES:
            point = series[_bucket(scope, day)]
            point[0] += 1
            point[1] += action in POSITIVE
            by_project[project_id][0] += 1
            by_project[project_id][1] += action in POSITIVE

    reads = _scoped_reads(session, scope)
    read_now = sum(1 for _, at in reads if scope.start <= scope.local_day(at) <= scope.end)  # type: ignore[arg-type]
    read_before = sum(
        1
        for _, at in reads
        if scope.previous_start <= scope.local_day(at) <= scope.previous_end  # type: ignore[arg-type]
    )
    decks = dict(
        session.execute(
            select(RecommendationSlate.generated_at >= scope.utc(scope.start), func.count())
            .where(
                RecommendationSlate.project_id.in_(ids),
                RecommendationSlate.generated_at >= scope.utc(scope.previous_start),
            )
            .group_by(RecommendationSlate.generated_at >= scope.utc(scope.start))
        ).all()
    )

    def totals(counts: Counter[str], opened: set[int], read: int, deck_count: int) -> ActivityTotals:
        return ActivityTotals(
            swiped=sum(counts[a] for a in SWIPES),
            relevant=counts["relevant"],
            not_relevant=counts["not_relevant"],
            saved=counts["save"],
            opened=len(opened),
            read=read,
            decks=deck_count,
        )

    current = totals(now_counts, opened_now, read_now, int(decks.get(True, 0)))
    previous = totals(before_counts, opened_before, read_before, int(decks.get(False, 0)))

    all_swiped = session.scalar(
        select(func.count()).where(
            Interaction.project_id.in_(ids), Interaction.undone_at.is_(None), Interaction.action.in_(SWIPES)
        )
    )
    library = session.scalar(
        select(func.count()).where(
            ProjectPaperState.project_id.in_(ids), ProjectPaperState.state.in_(("relevant", "saved"))
        )
    )
    swipe_days = {
        scope.local_day(ts)
        for (ts,) in session.execute(
            select(Interaction.timestamp).where(
                Interaction.project_id.in_(ids),
                Interaction.undone_at.is_(None),
                Interaction.action.in_(SWIPES),
                Interaction.timestamp >= scope.utc(scope.end - STREAK_LOOKBACK),
            )
        )
    }

    buckets: list[date] = []
    day = _bucket(scope, scope.start)
    while day <= scope.end:
        buckets.append(day)
        day += timedelta(days=7 if scope.bucket == "week" else 1)
    names = dict(session.execute(select(Project.id, Project.name).where(Project.id.in_(ids))).all())
    return DashboardActivity(
        period=scope.period(),
        totals=current,
        previous=previous,
        all_time=AllTimeTotals(swiped=int(all_swiped or 0), read=len(reads), library=int(library or 0)),
        series=[ActivityPoint(date=b, swiped=series[b][0], positive=series[b][1]) for b in buckets],
        positive_rate=_rate(current.relevant + current.saved, current.swiped),
        positive_rate_before=_rate(previous.relevant + previous.saved, previous.swiped),
        streak_days=streak(swipe_days, scope.end),
        by_project=[
            ProjectActivity(project_id=pid, name=names.get(pid, ""), swiped=counts[0], positive=counts[1])
            for pid, counts in sorted(by_project.items(), key=lambda kv: -kv[1][0])
        ],
    )


__all__ = ["activity", "streak"]
