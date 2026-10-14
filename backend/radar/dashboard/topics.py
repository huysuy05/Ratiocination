"""Topics the user is leaning into (SPEC §14.2). Owner: dashboard.

A liked paper is one with a relevant or save action. For each topic (``common.topic_keys``) we
compare its share of the liked papers in the current period with its share in the previous period:

* ``leaning_in`` - at least 2 liked papers now and a growing share (largest growth first);
* ``fading`` - at least 2 liked papers before and a shrinking share;
* ``top`` - the most liked topics now;
* ``skipping`` - at least 4 swipes now, 75% or more of them "not relevant".

``weekly`` holds liked papers per week for the last 12 weeks (Monday-based, local time).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.dashboard.common import POSITIVE, SWIPES, Scope, topic_keys
from radar.models import Interaction
from radar.schemas import DashboardTopics, TopicKey, TopicStat

WEEKS = 12
LEAN_MIN = 2
SKIP_MIN_SWIPES = 4
SKIP_RATE = 0.75


def topics(session: Session, scope: Scope) -> DashboardTopics:
    ids = scope.project_ids or [-1]
    weeks = scope.week_starts(WEEKS)
    since = min(scope.previous_start, weeks[0])
    rows = session.execute(
        select(Interaction.timestamp, Interaction.action, Interaction.paper_id).where(
            Interaction.project_id.in_(ids),
            Interaction.undone_at.is_(None),
            Interaction.action.in_(SWIPES),
            Interaction.timestamp >= scope.utc(since),
            Interaction.timestamp < scope.utc(scope.end + timedelta(days=1)),
        )
    ).all()
    liked_now: set[int] = set()
    liked_before: set[int] = set()
    weekly_papers: dict[date, set[int]] = defaultdict(set)
    swipes_now: Counter[int] = Counter()
    rejects_now: Counter[int] = Counter()
    for timestamp, action, paper_id in rows:
        day = scope.local_day(timestamp)
        if action in POSITIVE:
            if scope.start <= day <= scope.end:
                liked_now.add(paper_id)
            elif scope.previous_start <= day <= scope.previous_end:
                liked_before.add(paper_id)
            if day >= weeks[0]:
                weekly_papers[day - timedelta(days=day.weekday())].add(paper_id)
        if scope.start <= day <= scope.end:
            swipes_now[paper_id] += 1
            rejects_now[paper_id] += action == "not_relevant"

    keys = topic_keys(
        session,
        sorted(liked_now | liked_before | set(swipes_now) | {p for s in weekly_papers.values() for p in s}),
    )
    count_now: Counter[str] = Counter()
    count_before: Counter[str] = Counter()
    weekly: dict[str, list[int]] = defaultdict(lambda: [0] * WEEKS)
    swiped: Counter[str] = Counter()
    rejected: Counter[str] = Counter()
    by_key: dict[str, TopicKey] = {}
    for pid in liked_now:
        if pid in keys:
            count_now[keys[pid].key] += 1
    for pid in liked_before:
        if pid in keys:
            count_before[keys[pid].key] += 1
    for index, week in enumerate(weeks):
        for pid in weekly_papers.get(week, ()):
            if pid in keys:
                weekly[keys[pid].key][index] += 1
    for pid, n in swipes_now.items():
        if pid in keys:
            swiped[keys[pid].key] += n
            rejected[keys[pid].key] += rejects_now[pid]
    for key in keys.values():
        by_key[key.key] = key

    n_now, n_before = len(liked_now), len(liked_before)
    stats: dict[str, TopicStat] = {}
    for key in set(count_now) | set(count_before) | set(swiped):
        share = count_now[key] / n_now if n_now else 0.0
        share_before = count_before[key] / n_before if n_before else 0.0
        stats[key] = TopicStat(
            **by_key[key].model_dump(),
            count=count_now[key],
            share=round(share, 4),
            count_before=count_before[key],
            share_before=round(share_before, 4),
            delta=round(share - share_before, 4),
            weekly=weekly[key],
            swiped=swiped[key],
            skip_rate=round(rejected[key] / swiped[key], 4) if swiped[key] else None,
        )
    values = list(stats.values())
    leaning = sorted(
        (s for s in values if s.count >= LEAN_MIN and s.delta > 0),
        key=lambda s: (-s.delta, -s.count, s.label),
    )[:5]
    fading = sorted(
        (s for s in values if s.count_before >= LEAN_MIN and s.delta < 0), key=lambda s: (s.delta, s.label)
    )[:5]
    top = sorted((s for s in values if s.count > 0), key=lambda s: (-s.count, s.label))[:8]
    skipping = sorted(
        (s for s in values if s.swiped >= SKIP_MIN_SWIPES and (s.skip_rate or 0) >= SKIP_RATE),
        key=lambda s: (-(s.skip_rate or 0), -s.swiped, s.label),
    )[:5]
    with_topic = sum(1 for pid in liked_now if pid in keys and keys[pid].kind == "topic")
    return DashboardTopics(
        period=scope.period(),
        weeks=weeks,
        n_positive=n_now,
        n_positive_before=n_before,
        leaning_in=leaning,
        fading=fading,
        top=top,
        skipping=skipping,
        coverage=round(with_topic / n_now, 4) if n_now else None,
    )


__all__ = ["topics"]
