"""Shared dashboard pieces (SPEC §14): the period scope and topic keys. Owner: dashboard.

* :class:`Scope` - the user, the projects in view (one, or all of the user's), the time zone and the
  current and previous periods as local dates (both ``days`` long, the current one ending today).
* :func:`topic_keys` - each paper's topic: its highest-confidence OpenAlex topic, else its primary
  arXiv category with a readable name (papers reach OpenAlex days after arXiv).
* :func:`liked` - each paper's latest positive action (relevant or save) in a range of local days.

Only non-undone interactions count anywhere on the dashboard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.config import get_settings
from radar.errors import ApiError, validation_error
from radar.models import Interaction, Paper, Project, User
from radar.schemas import Period, TopicKey
from radar.store.graph import paper_topics

DAYS = (7, 30, 90, 365)
SWIPES = ("relevant", "not_relevant", "save")
POSITIVE = ("relevant", "save")

ARXIV_NAMES: dict[str, str] = {
    "cs.AI": "Artificial Intelligence",
    "cs.AR": "Hardware Architecture",
    "cs.CC": "Computational Complexity",
    "cs.CE": "Computational Engineering",
    "cs.CG": "Computational Geometry",
    "cs.CL": "Computation and Language",
    "cs.CR": "Cryptography and Security",
    "cs.CV": "Computer Vision",
    "cs.CY": "Computers and Society",
    "cs.DB": "Databases",
    "cs.DC": "Distributed Computing",
    "cs.DL": "Digital Libraries",
    "cs.DM": "Discrete Mathematics",
    "cs.DS": "Data Structures and Algorithms",
    "cs.ET": "Emerging Technologies",
    "cs.FL": "Formal Languages",
    "cs.GL": "General Literature",
    "cs.GR": "Graphics",
    "cs.GT": "Game Theory",
    "cs.HC": "Human-Computer Interaction",
    "cs.IR": "Information Retrieval",
    "cs.IT": "Information Theory",
    "cs.LG": "Machine Learning",
    "cs.LO": "Logic in Computer Science",
    "cs.MA": "Multiagent Systems",
    "cs.MM": "Multimedia",
    "cs.MS": "Mathematical Software",
    "cs.NA": "Numerical Analysis",
    "cs.NE": "Neural and Evolutionary Computing",
    "cs.NI": "Networking",
    "cs.OH": "Other Computer Science",
    "cs.OS": "Operating Systems",
    "cs.PF": "Performance",
    "cs.PL": "Programming Languages",
    "cs.RO": "Robotics",
    "cs.SC": "Symbolic Computation",
    "cs.SD": "Sound",
    "cs.SE": "Software Engineering",
    "cs.SI": "Social and Information Networks",
    "cs.SY": "Systems and Control",
    "stat.AP": "Statistics Applications",
    "stat.CO": "Statistical Computation",
    "stat.ME": "Statistical Methodology",
    "stat.ML": "Machine Learning (Statistics)",
    "stat.TH": "Statistics Theory",
    "math.OC": "Optimization and Control",
    "math.ST": "Statistics Theory",
    "math.PR": "Probability",
    "math.NA": "Numerical Analysis",
    "eess.AS": "Audio and Speech Processing",
    "eess.IV": "Image and Video Processing",
    "eess.SP": "Signal Processing",
    "eess.SY": "Systems and Control",
    "econ.EM": "Econometrics",
    "econ.GN": "General Economics",
    "q-bio.BM": "Biomolecules",
    "q-bio.GN": "Genomics",
    "q-bio.NC": "Neurons and Cognition",
    "q-bio.QM": "Quantitative Methods",
    "q-fin.CP": "Computational Finance",
    "q-fin.ST": "Statistical Finance",
    "physics.comp-ph": "Computational Physics",
    "physics.soc-ph": "Physics and Society",
    "physics.data-an": "Data Analysis and Statistics",
    "quant-ph": "Quantum Physics",
    "cond-mat.stat-mech": "Statistical Mechanics",
    "cond-mat.dis-nn": "Disordered Systems and Neural Networks",
    "astro-ph.IM": "Astrophysics Instrumentation and Methods",
    "hep-th": "High Energy Physics - Theory",
    "hep-ph": "High Energy Physics - Phenomenology",
    "gr-qc": "General Relativity and Quantum Cosmology",
}


def category_label(code: str) -> str:
    name = ARXIV_NAMES.get(code)
    return f"{name} ({code})" if name else code


@dataclass(frozen=True)
class Scope:
    user_id: int
    project_ids: list[int]
    project_filter: int | None
    tz: ZoneInfo
    days: int
    start: date
    end: date
    previous_start: date
    previous_end: date

    @property
    def bucket(self) -> str:
        return "week" if self.days > 90 else "day"

    def utc(self, day: date) -> datetime:
        """Start of a local day as an aware UTC datetime."""
        return datetime.combine(day, time.min, tzinfo=self.tz).astimezone(UTC)

    def local_day(self, moment: datetime) -> date:
        return moment.astimezone(self.tz).date()

    def period(self) -> Period:
        return Period(start=self.start, end=self.end, days=self.days, bucket=self.bucket, tz=self.tz.key)

    def week_starts(self, weeks: int = 12) -> list[date]:
        """Mondays of the last ``weeks`` weeks, oldest first (the last one contains today)."""
        this_monday = self.end - timedelta(days=self.end.weekday())
        return [this_monday - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]


def _zone(name: str | None) -> ZoneInfo | None:
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def make_scope(session: Session, user_id: int, days: int, project_id: int | None, tz: str | None) -> Scope:
    """The scope of a dashboard request (422 for an unknown zone, 404 for another user's project)."""
    if days not in DAYS:
        raise validation_error([{"path": "days", "message": "Use 7, 30, 90 or 365"}])
    zone = _zone(tz)
    if tz and zone is None:
        raise validation_error([{"path": "tz", "message": "Unknown time zone"}])
    if zone is None:
        user = session.get(User, user_id)
        zone = _zone(dict(user.preferences_json or {}).get("timezone") if user else None)
    zone = zone or ZoneInfo(get_settings().timezone)
    if project_id is not None:
        project = session.get(Project, project_id)
        if project is None or project.owner_id != user_id:
            raise ApiError(404, "not_found", "Project not found")
        project_ids = [project_id]
    else:
        project_ids = list(
            session.scalars(select(Project.id).where(Project.owner_id == user_id).order_by(Project.id))
        )
    end = datetime.now(zone).date()
    start = end - timedelta(days=days - 1)
    previous_end = start - timedelta(days=1)
    return Scope(
        user_id=user_id,
        project_ids=project_ids,
        project_filter=project_id,
        tz=zone,
        days=days,
        start=start,
        end=end,
        previous_start=previous_end - timedelta(days=days - 1),
        previous_end=previous_end,
    )


@dataclass(frozen=True)
class Liked:
    paper_id: int
    project_id: int
    action: str  # 'relevant' | 'save'
    at: datetime


def liked(session: Session, scope: Scope, start: date, end: date) -> dict[int, Liked]:
    """Each paper's latest positive action from local day ``start`` to ``end``, most recent first."""
    rows = session.execute(
        select(Interaction.paper_id, Interaction.project_id, Interaction.action, Interaction.timestamp)
        .where(
            Interaction.project_id.in_(scope.project_ids or [-1]),
            Interaction.undone_at.is_(None),
            Interaction.action.in_(POSITIVE),
            Interaction.timestamp >= scope.utc(start),
            Interaction.timestamp < scope.utc(end + timedelta(days=1)),
        )
        .order_by(Interaction.timestamp.desc(), Interaction.id.desc())
    ).all()
    out: dict[int, Liked] = {}
    for paper_id, project_id, action, at in rows:
        out.setdefault(paper_id, Liked(paper_id, project_id, action, at))
    return out


def topic_keys(session: Session, paper_ids: list[int]) -> dict[int, TopicKey]:
    """The dashboard topic of each paper (OpenAlex topic, else primary arXiv category)."""
    ids = list(dict.fromkeys(paper_ids))
    if not ids:
        return {}
    topics = paper_topics(session, ids, limit=1)
    out: dict[int, TopicKey] = {}
    missing = [pid for pid in ids if not topics.get(pid)]
    categories = (
        dict(session.execute(select(Paper.id, Paper.primary_category).where(Paper.id.in_(missing))).all())
        if missing
        else {}
    )
    for pid in ids:
        hits = topics.get(pid)
        if hits:
            hit = hits[0]
            out[pid] = TopicKey(
                kind="topic",
                key=f"concept:{hit.concept_id}",
                label=hit.label,
                concept_id=hit.concept_id,
                category=None,
            )
        elif categories.get(pid):
            code = categories[pid]
            out[pid] = TopicKey(
                kind="category", key=f"cat:{code}", label=category_label(code), concept_id=None, category=code
            )
    return out


__all__ = [
    "ARXIV_NAMES",
    "DAYS",
    "POSITIVE",
    "SWIPES",
    "Liked",
    "Scope",
    "category_label",
    "liked",
    "make_scope",
    "topic_keys",
]
