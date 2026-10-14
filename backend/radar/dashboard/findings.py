"""Main findings, quoted from abstracts (SPEC §14.3). Owner: dashboard.

Extractive on purpose: every finding is a sentence copied verbatim from a paper the user liked, so it
can be checked against the source and cannot be invented. A sentence scores

* +2 per kind of result cue ("we show/find/demonstrate…", "results indicate…", "outperforms",
  "surpasses", "improves … by", "reduces … by", "achieves", "state of the art", "significantly",
  "reveals"), at most +4;
* +1 for a contribution cue ("we propose/introduce/present…"); +1 when it contains a number;
* +1 when it is among the last two sentences (abstracts end with results), −1 for the first sentence;
* −1 for background phrasing ("recently", "has attracted", "remains challenging", "in this paper we
  study…"); −2 when shorter than 8 or longer than 60 words.

A paper's finding is its best sentence when that scores at least 2. :func:`findings` groups the
findings of the papers liked in the period by topic (``common.topic_keys``): at most 6 topics (most
liked papers first) and 4 findings per topic, ordered by score, then similarity to the user's work
(SPEC §14.5), then how recently the paper was liked.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.api.serializers import paper_summaries
from radar.dashboard.common import Liked, Scope, liked, topic_keys
from radar.dashboard.work import work_similarity
from radar.embeddings.registry import get_embedder
from radar.models import Paper
from radar.schemas import Finding, FindingGroup, TopicKey

PLACEHOLDER = "․"  # one dot leader: protects dots that do not end a sentence
ABBREVIATIONS = (
    "e.g.",
    "i.e.",
    "et al.",
    "etc.",
    "vs.",
    "cf.",
    "Fig.",
    "Figs.",
    "Eq.",
    "Eqs.",
    "Sec.",
    "No.",
    "approx.",
    "resp.",
    "Dr.",
    "Prof.",
    "Ref.",
    "Refs.",
)
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"“'])")
_WORD = re.compile(r"\S+")

RESULT_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("we show", re.compile(r"\bwe (?:show|find|found|demonstrate|prove|observe|report|establish)\b", re.I)),
    (
        "results indicate",
        re.compile(
            r"\b(?:results?|experiments?|evaluations?|analys[ie]s|findings) (?:show|indicate|suggest|"
            r"demonstrate|reveal|confirm)s?\b",
            re.I,
        ),
    ),
    ("outperforms", re.compile(r"\boutperform(?:s|ed|ing)?\b", re.I)),
    ("surpasses", re.compile(r"\bsurpass(?:es|ed|ing)?\b", re.I)),
    ("improves", re.compile(r"\bimprov(?:es|ed|ing|ements?)\b[^.]{0,80}?\b(?:by|over)\b", re.I)),
    ("reduces", re.compile(r"\breduc(?:es|ed|ing|tions?)\b[^.]{0,80}?\bby\b", re.I)),
    ("achieves", re.compile(r"\bachiev(?:es|ed|ing)\b", re.I)),
    ("state of the art", re.compile(r"\bstate[- ]of[- ]the[- ]art\b", re.I)),
    ("significantly", re.compile(r"\bsignificant(?:ly)?\b", re.I)),
    ("reveals", re.compile(r"\breveal(?:s|ed|ing)?\b", re.I)),
)
CONTRIBUTION_CUE = re.compile(r"\bwe (?:propose|introduce|present|develop|design)\b", re.I)
BACKGROUND_CUES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brecent(?:ly)?\b", re.I),
    re.compile(r"\bha(?:s|ve) (?:attracted|received|gained)\b", re.I),
    re.compile(r"\bremains? (?:challenging|open|unclear|difficult|an open)\b", re.I),
    re.compile(r"\bin this (?:paper|work|study),? we (?:study|consider|investigate|examine|explore)\b", re.I),
)
NUMBER = re.compile(r"\d")
MIN_WORDS, MAX_WORDS = 8, 60
MIN_SCORE = 2.0
MAX_TOPICS = 6
PER_TOPIC = 4
OTHER = TopicKey(kind="category", key="cat:other", label="Other papers", concept_id=None, category=None)


@dataclass(frozen=True)
class Quote:
    sentence: str
    score: float
    cues: list[str]


def split_sentences(text: str) -> list[str]:
    """Sentences of an abstract; dots in common abbreviations and decimals do not split."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return []
    for abbreviation in ABBREVIATIONS:
        clean = clean.replace(abbreviation, abbreviation.replace(".", PLACEHOLDER))
    clean = _DECIMAL.sub(PLACEHOLDER, clean)
    return [s.replace(PLACEHOLDER, ".").strip() for s in _SPLIT.split(clean) if s.strip()]


def score_sentence(sentence: str, index: int, total: int) -> Quote:
    cues = [name for name, pattern in RESULT_CUES if pattern.search(sentence)]
    score = min(4.0, 2.0 * len(cues))
    if CONTRIBUTION_CUE.search(sentence):
        cues.append("we propose")
        score += 1
    if NUMBER.search(sentence):
        score += 1
    if total > 1 and index >= total - 2:
        score += 1
    if index == 0 and total > 1:
        score -= 1
    if any(pattern.search(sentence) for pattern in BACKGROUND_CUES):
        score -= 1
    words = len(_WORD.findall(sentence))
    if words < MIN_WORDS or words > MAX_WORDS:
        score -= 2
    return Quote(sentence=sentence, score=score, cues=cues)


def best_finding(abstract: str | None) -> Quote | None:
    """The abstract's most finding-like sentence, or ``None`` below ``MIN_SCORE``."""
    sentences = split_sentences(abstract or "")
    if not sentences:
        return None
    scored = [score_sentence(s, i, len(sentences)) for i, s in enumerate(sentences)]
    best = max(scored, key=lambda q: (q.score, -sentences.index(q.sentence)))
    return best if best.score >= MIN_SCORE else None


@dataclass(frozen=True)
class Findings:
    groups: list[FindingGroup]
    n_papers: int
    n_with_findings: int
    liked: dict[int, Liked]  # the period's liked papers, most recent first


def findings(session: Session, scope: Scope) -> Findings:
    """Quoted findings of the papers liked in the period, grouped by topic."""
    now = liked(session, scope, scope.start, scope.end)
    abstracts = dict(
        session.execute(select(Paper.id, Paper.abstract).where(Paper.id.in_(list(now) or [-1]))).all()
    )
    quotes = {pid: q for pid in now if (q := best_finding(abstracts.get(pid))) is not None}
    by_project: dict[int, list[int]] = defaultdict(list)
    for pid in quotes:
        by_project[now[pid].project_id].append(pid)
    similarity = work_similarity(session, dict(by_project), get_embedder().model_id) if quotes else {}
    keys = topic_keys(session, list(now))

    liked_per_topic: dict[str, int] = defaultdict(int)
    for pid in now:
        liked_per_topic[keys[pid].key if pid in keys else OTHER.key] += 1
    members: dict[str, list[int]] = defaultdict(list)
    for pid in quotes:
        members[keys[pid].key if pid in keys else OTHER.key].append(pid)
    topic_of = {k.key: k for k in keys.values()} | {OTHER.key: OTHER}
    chosen = sorted(members, key=lambda key: (-liked_per_topic[key], -len(members[key]), topic_of[key].label))
    chosen = chosen[:MAX_TOPICS]

    def order(pid: int) -> tuple[float, float, float]:
        sim = similarity.get((now[pid].project_id, pid))
        return (-quotes[pid].score, -(sim if sim is not None else -1.0), -now[pid].at.timestamp())

    picked = {key: sorted(members[key], key=order)[:PER_TOPIC] for key in chosen}
    summaries = paper_summaries(
        session, [pid for ids in picked.values() for pid in ids], user_id=scope.user_id
    )
    groups = []
    for key in chosen:
        items = [
            Finding(
                paper=summaries[pid],
                sentence=quotes[pid].sentence,
                cues=quotes[pid].cues,
                score=quotes[pid].score,
                similarity=similarity.get((now[pid].project_id, pid)),
                action=now[pid].action,  # type: ignore[arg-type]
                acted_at=now[pid].at,
            )
            for pid in picked[key]
            if pid in summaries
        ]
        if items:
            groups.append(FindingGroup(topic=topic_of[key], findings=items))
    return Findings(groups=groups, n_papers=len(now), n_with_findings=len(quotes), liked=now)


__all__ = [
    "MIN_SCORE",
    "Findings",
    "Quote",
    "best_finding",
    "findings",
    "score_sentence",
    "split_sentences",
]
