"""Pydantic v2 models mirroring docs/API.md v0.3 exactly (names, fields, literal enums, unions).

Conventions:

* datetimes are :data:`ISODateTime` (aware; normalized to UTC; serialized as ISO 8601 with ``Z``);
  naive datetimes are rejected;
* dates are ``YYYY-MM-DD`` (``datetime.date``);
* ``Reason`` is a discriminated union on ``code``, ``GraphEdgeEvidence`` on ``relation`` and
  ``WorkMatch`` on ``kind``;
* request models ignore unknown keys; response models ignore unknown keys when validating dicts
  (extra keys never reach the client);
* ``ProjectSettings`` holds every bound from API.md; partial settings in requests use
  :class:`ProjectSettingsPatch` and are validated again after merging (see ``api/projects.py``).
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from typing import Annotated, Any, Literal, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    field_validator,
    model_validator,
)

from radar.keywords import MAX_KEYWORDS, clean_keyword, dedupe_keywords, keyword_key
from radar.keywords import MAX_LENGTH as KEYWORD_MAX_LENGTH
from radar.keywords import MIN_LENGTH as KEYWORD_MIN_LENGTH

# ------------------------------------------------------------------------------------------------
# Base classes and scalar types
# ------------------------------------------------------------------------------------------------


class ApiModel(BaseModel):
    """Base for every API model."""

    model_config = ConfigDict(protected_namespaces=(), from_attributes=True, extra="ignore")


def _ensure_aware_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("datetime must be timezone-aware (UTC)")
    return value.astimezone(UTC)


def iso_z(value: dt.datetime) -> str:
    """Serialize an aware datetime as ISO 8601 in UTC with a ``Z`` suffix."""
    text = _ensure_aware_utc(value).isoformat()
    return text[:-6] + "Z" if text.endswith("+00:00") else text


ISODateTime = Annotated[
    dt.datetime,
    AfterValidator(_ensure_aware_utc),
    PlainSerializer(iso_z, return_type=str, when_used="json"),
]
"""Aware datetime, normalized to UTC, serialized as ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``."""

ISODate = dt.date

PaperState = Literal["relevant", "not_relevant", "saved"]
FeedbackAction = Literal["relevant", "not_relevant", "save", "open"]
FeedbackSource = Literal["deck", "detail", "library", "graph", "dashboard"]
SlateMode = Literal["daily", "more"]
JobStatus = Literal["queued", "running", "succeeded", "failed"]
JobKind = Literal[
    "daily",
    "ingest_feeds",
    "backfill",
    "enrich",
    "embed",
    "build_deck",
    "expand",
    "breakthroughs",
    "process_document",
    "download_model",
    "summarize_findings",
]
Relation = Literal["CITES", "SEMANTICALLY_SIMILAR", "HAS_TOPIC", "AUTHORED"]
FeatureName = Literal["question", "anchor", "keyword", "draft", "citation", "preference", "recency"]
FEATURE_NAMES: tuple[str, ...] = (
    "question",
    "anchor",
    "keyword",
    "draft",
    "citation",
    "preference",
    "recency",
)
PaperStatus = Literal["active", "stub", "retracted", "withdrawn"]
AuthMode = Literal["local", "accounts"]
OAuthProvider = Literal["google", "github"]
ErrorCode = Literal[
    "not_found",
    "validation_error",
    "conflict",
    "unresolvable_identifier",
    "source_unavailable",
    "budget_exceeded",
    "payload_too_large",
    "unsupported_media_type",
    "unauthenticated",
    "unauthorized",
    "forbidden",
    "invalid_credentials",
    "invalid_token",
    "email_taken",
    "email_unverified",
    "password_required",
    "not_allowed",
    "rate_limited",
    "quota_exceeded",
    "not_configured",
    "not_enough_papers",
    "not_implemented",
    "internal_error",
]

# ------------------------------------------------------------------------------------------------
# Shared types
# ------------------------------------------------------------------------------------------------


class Job(ApiModel):
    id: int
    kind: JobKind
    status: JobStatus
    project_id: int | None
    params: dict[str, Any]
    stats: dict[str, Any]
    progress: float | None  # 0..1 when known
    message: str | None
    # source_unavailable | budget_exceeded | egress_blocked | interrupted | internal_error | ...
    error_code: str | None
    error_message: str | None
    created_at: ISODateTime
    started_at: ISODateTime | None
    finished_at: ISODateTime | None


class Author(ApiModel):
    name: str
    position: int
    openalex_id: str | None = None


class TopicRef(ApiModel):
    """Sorted by score desc, at most 5; ``topics[0]`` is the primary topic."""

    concept_id: int
    label: str
    score: float | None


class PaperIdentifiers(ApiModel):
    """Primary values per scheme; arXiv DOIs appear only as ``arxiv``."""

    doi: str | None
    arxiv: str | None
    openalex: str | None
    s2: str | None


class PaperLinks(ApiModel):
    abs: str | None
    pdf: str | None
    doi: str | None
    oa: str | None


class PaperSummary(ApiModel):
    id: int
    title: str
    authors: list[Author]
    abstract: str | None
    publication_date: ISODate | None
    year: int | None
    primary_category: str | None
    categories: list[str]
    venue: str | None
    identifiers: PaperIdentifiers
    latest_version: str | None  # "v3" (highest N), else "published", else null
    links: PaperLinks
    topics: list[TopicRef]
    status: PaperStatus
    is_oa: bool | None
    cited_by_count: int | None
    cd: float | None  # stored disruption (CD) index, when computed
    read_at: ISODateTime | None  # the current user's read mark


class ExpansionStatus(ApiModel):
    status: Literal["none", "queued", "running", "done", "failed"]
    job_id: int | None
    last_expanded_at: ISODateTime | None
    message: str | None


# ---- Reasons (discriminated by ``code``) --------------------------------------------------------

AnchorKind = Literal["seed", "relevant", "saved"]


class AnchorSimilarityEvidence(ApiModel):
    anchor_paper_id: int
    anchor_title: str
    anchor_kind: AnchorKind
    cosine: float


class AnchorEdgeEvidence(ApiModel):
    anchor_paper_id: int
    anchor_title: str
    anchor_kind: AnchorKind
    direction: Literal["cites", "cited_by"]


class DraftEvidence(ApiModel):
    document_id: int
    chunk_id: int
    filename: str
    section: str
    page: int | None
    snippet: str


class DeletedDocumentEvidence(ApiModel):
    deleted_document: Literal[True]


class QuestionEvidence(ApiModel):
    cosine: float


class PreferenceEvidence(ApiModel):
    n_labels: int
    probability: float


class CategoryEvidence(ApiModel):
    category: str


class RecentEvidence(ApiModel):
    days: int


class KeywordMatchEvidence(ApiModel):
    keyword: str  # the project keyword as the user wrote it
    field: Literal["title", "abstract"]
    text: str  # the paper's own words that matched (e.g. "Conformal Predictions")


class KeywordEvidence(ApiModel):
    matches: list[KeywordMatchEvidence]  # title matches first; each keyword at most once


class QuestionReason(ApiModel):
    code: Literal["question"] = "question"
    kind: Literal["model"] = "model"
    text: str
    evidence: QuestionEvidence
    contribution: float | None = None


class SimilarToAnchorReason(ApiModel):
    code: Literal["similar_to_anchor"] = "similar_to_anchor"
    kind: Literal["model"] = "model"
    text: str
    evidence: AnchorSimilarityEvidence
    contribution: float | None = None


class DraftMatchReason(ApiModel):
    code: Literal["draft_match"] = "draft_match"
    kind: Literal["model"] = "model"
    text: str
    evidence: DraftEvidence | DeletedDocumentEvidence
    contribution: float | None = None


class AnchorEdgeReason(ApiModel):
    code: Literal["cites_anchor", "cited_by_anchor"]
    kind: Literal["metadata"] = "metadata"
    text: str
    evidence: AnchorEdgeEvidence
    contribution: float | None = None


class PreferenceReason(ApiModel):
    code: Literal["preference"] = "preference"
    kind: Literal["model"] = "model"
    text: str
    evidence: PreferenceEvidence
    contribution: float | None = None


class CategoryReason(ApiModel):
    code: Literal["category"] = "category"
    kind: Literal["metadata"] = "metadata"
    text: str
    evidence: CategoryEvidence
    contribution: float | None = None


class RecentReason(ApiModel):
    code: Literal["recent"] = "recent"
    kind: Literal["metadata"] = "metadata"
    text: str
    evidence: RecentEvidence
    contribution: float | None = None


class KeywordReason(ApiModel):
    code: Literal["keyword"] = "keyword"
    kind: Literal["metadata"] = "metadata"
    text: str
    evidence: KeywordEvidence
    contribution: float | None = None


Reason = Annotated[
    Union[
        QuestionReason,
        SimilarToAnchorReason,
        DraftMatchReason,
        AnchorEdgeReason,
        PreferenceReason,
        CategoryReason,
        RecentReason,
        KeywordReason,
    ],
    Field(discriminator="code"),
]
"""Discriminated on ``code``; the card shows ``reasons[:3]`` (SPEC §10)."""


class FeatureScore(ApiModel):
    value: float | None
    raw: float | None
    weight: float
    contribution: float
    available: bool


class FeatureScores(ApiModel):
    """All seven keys; score = sum of contributions (decks built before keywords existed report
    ``keyword`` as unavailable with weight 0)."""

    question: FeatureScore
    anchor: FeatureScore
    keyword: FeatureScore
    draft: FeatureScore
    citation: FeatureScore
    preference: FeatureScore
    recency: FeatureScore


class RecommendationItem(ApiModel):
    slate_id: int
    paper: PaperSummary
    rank: int  # 1-based
    score: float
    features: FeatureScores
    reasons: list[Reason]
    state: PaperState | None  # current project state; null = not acted on yet
    last_interaction_id: int | None


# ------------------------------------------------------------------------------------------------
# Accounts
# ------------------------------------------------------------------------------------------------

Email = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=3, max_length=320)]
Password = Annotated[str, StringConstraints(min_length=10, max_length=256)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
OneTimeToken = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=200)]
TimeZoneName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class AuthProviders(ApiModel):
    password: bool
    google: bool
    github: bool


class AuthConfig(ApiModel):
    mode: AuthMode
    providers: AuthProviders
    allowlist: bool  # only some emails may sign up
    email_delivery: bool  # SMTP configured
    password_signup: bool  # email + password sign-up is open (off on a public server without SMTP)
    email_verification: bool  # new password accounts confirm their email before they get a session


class UserPreferences(ApiModel):
    auto_expand: bool = True
    timezone: str | None = None  # IANA zone; null = server default


class UserPreferencesPatch(ApiModel):
    auto_expand: bool | None = None
    timezone: TimeZoneName | None = None

    @field_validator("timezone", mode="after")
    @classmethod
    def _valid_zone(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown time zone") from exc
        return v


class Identity(ApiModel):
    provider: OAuthProvider
    email: str | None
    created_at: ISODateTime


class User(ApiModel):
    id: int
    email: str | None
    email_verified: bool
    display_name: str
    avatar_url: str | None
    is_admin: bool
    has_password: bool
    identities: list[Identity]
    preferences: UserPreferences
    created_at: ISODateTime


class Me(ApiModel):
    mode: AuthMode
    user: User


NextPath = Annotated[str, StringConstraints(max_length=500)]


class SignUpRequest(ApiModel):
    email: Email
    password: Password
    display_name: DisplayName | None = None
    next: NextPath | None = None  # in-app path the verification link continues to


class VerificationPending(ApiModel):
    """``202`` answer of a sign-up that must confirm its email first (no session yet)."""

    verification_required: Literal[True] = True
    email: str


class SignInRequest(ApiModel):
    email: Email
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class ForgotPasswordRequest(ApiModel):
    email: Email


class ResetPasswordRequest(ApiModel):
    token: OneTimeToken
    password: Password


class VerifyEmailRequest(ApiModel):
    token: OneTimeToken
    # Required (401 password_required) when the link is opened outside the browser that signed up.
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None


class ResendVerificationRequest(ApiModel):
    """Signed out: the address to send a new link to (always 202). Signed in: omit the body."""

    email: Email | None = None
    next: NextPath | None = None


class MeUpdate(ApiModel):
    display_name: DisplayName | None = None
    preferences: UserPreferencesPatch | None = None


class PasswordChangeRequest(ApiModel):
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    new_password: Password


class AccountDeleteRequest(ApiModel):
    confirm_email: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, max_length=320)]


class RevokedSessions(ApiModel):
    revoked: int


# ------------------------------------------------------------------------------------------------
# Projects
# ------------------------------------------------------------------------------------------------

CATEGORY_PATTERN = r"^[a-z-]+(\.[A-Za-z-]+)?$"

ArxivCategory = Annotated[str, StringConstraints(pattern=CATEGORY_PATTERN)]
TimeWindowDays = Annotated[int, Field(ge=1, le=90)]
SlateSize = Annotated[int, Field(ge=5, le=50)]
ArxivCategories = Annotated[list[ArxivCategory], Field(max_length=20)]


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


class ProjectSettings(ApiModel):
    """Per-project settings; bounds are enforced with 422 ``validation_error`` (API.md)."""

    arxiv_categories: ArxivCategories = Field(default_factory=list)
    time_window_days: TimeWindowDays = 14
    slate_size: SlateSize = 20

    @field_validator("arxiv_categories", mode="after")
    @classmethod
    def _unique_categories(cls, v: list[str]) -> list[str]:
        return _dedupe(v)

    @classmethod
    def from_stored(cls, stored: dict[str, Any] | None) -> ProjectSettings:
        """Validate a stored ``projects.settings_json`` (missing keys take defaults; unknown keys are
        ignored so settings written by older versions still load)."""
        return cls.model_validate({**cls().model_dump(), **(stored or {})})


class ProjectSettingsPatch(ApiModel):
    """``Partial<ProjectSettings>``: only keys present are applied (merged, then re-validated)."""

    arxiv_categories: ArxivCategories | None = None
    time_window_days: TimeWindowDays | None = None
    slate_size: SlateSize | None = None


class ProjectStats(ApiModel):
    seeds: int
    documents: int
    relevant: int
    saved: int
    not_relevant: int
    remaining_in_deck: int
    last_deck_at: ISODateTime | None


class Project(ApiModel):
    id: int
    name: str
    research_question: str
    description: str
    keywords: list[str]
    settings: ProjectSettings
    active: bool
    created_at: ISODateTime
    updated_at: ISODateTime
    stats: ProjectStats


ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ResearchQuestion = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]


def _clean_keyword(value: Any) -> Any:
    return clean_keyword(value) if isinstance(value, str) else value


def _keyword_has_words(value: str) -> str:
    if not keyword_key(value):
        raise ValueError("A keyword needs at least one letter or digit")
    return value


Keyword = Annotated[
    str,
    StringConstraints(min_length=KEYWORD_MIN_LENGTH, max_length=KEYWORD_MAX_LENGTH),
    BeforeValidator(_clean_keyword),
    AfterValidator(_keyword_has_words),
]
"""One keyword or phrase: trimmed, inner whitespace collapsed, 2–60 characters, ≥ 1 letter or digit."""

Keywords = Annotated[list[Keyword], Field(max_length=MAX_KEYWORDS), AfterValidator(dedupe_keywords)]
"""≤ 12 keywords; duplicates (case-, hyphen- and plural-insensitive) are dropped, first one kept."""


class ProjectCreate(ApiModel):
    name: ProjectName
    research_question: ResearchQuestion
    description: Description = ""
    keywords: Keywords = Field(default_factory=list)
    active: bool = True
    settings: ProjectSettingsPatch | None = None


class ProjectUpdate(ApiModel):
    name: ProjectName | None = None
    research_question: ResearchQuestion | None = None
    description: Description | None = None
    keywords: Keywords | None = None
    active: bool | None = None
    settings: ProjectSettingsPatch | None = None

    @field_validator(
        "name", "research_question", "description", "keywords", "active", "settings", mode="before"
    )
    @classmethod
    def _not_null(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("must not be null")
        return v


# ------------------------------------------------------------------------------------------------
# Seeds
# ------------------------------------------------------------------------------------------------


class Seed(ApiModel):
    paper: PaperSummary
    weight: float
    added_via: Literal["doi", "arxiv", "openalex", "search"]
    created_at: ISODateTime


class SeedCreate(ApiModel):
    """Exactly one of ``identifier`` / ``paper_id``; weight 0.1-2 (default 1)."""

    identifier: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] | None
    ) = None
    paper_id: Annotated[int, Field(ge=1)] | None = None
    weight: Annotated[float, Field(ge=0.1, le=2.0)] = 1.0

    @model_validator(mode="after")
    def _exactly_one(self) -> SeedCreate:
        if (self.identifier is None) == (self.paper_id is None):
            raise ValueError("provide exactly one of identifier or paper_id")
        return self


class SeedCreated(ApiModel):
    seed: Seed
    created: bool  # false if already a seed
    enrich_job: Job | None


# ------------------------------------------------------------------------------------------------
# Feed and feedback
# ------------------------------------------------------------------------------------------------


class Slate(ApiModel):
    id: int
    project_id: int
    generated_at: ISODateTime
    mode: SlateMode
    ranker_version: str
    embedding_model: str
    pool_size: int
    items: list[RecommendationItem]  # ordered by rank, each with its current state
    remaining: int  # items with state === null at response time
    message: str | None


class FeedResponse(ApiModel):
    status: Literal["needs_setup", "building", "ready", "empty", "not_built"]
    slate: Slate | None
    job: Job | None  # queued/running build_deck job, if any
    last_job: Job | None  # most recent build_deck job of any status
    message: str | None


class RebuildRequest(ApiModel):
    mode: SlateMode = "daily"
    prepare: bool = False


class FeedbackContext(ApiModel):
    source: FeedbackSource | None = None


class FeedbackRequest(ApiModel):
    paper_id: int
    action: FeedbackAction
    slate_id: int | None = None
    context: FeedbackContext | None = None


class FeedbackTriggered(ApiModel):
    expansion_job_id: int | None


class FeedbackResponse(ApiModel):
    interaction_id: int
    paper_id: int
    state: PaperState | None
    triggered: FeedbackTriggered


class UndoRequest(ApiModel):
    interaction_id: int | None = None  # default: latest non-undone, non-open interaction


class UndoResponse(ApiModel):
    undone_interaction_id: int
    paper_id: int
    state: PaperState | None
    last_interaction_id: int | None


# ------------------------------------------------------------------------------------------------
# Papers and library
# ------------------------------------------------------------------------------------------------


class DisruptionDetail(ApiModel):
    cd: float | None  # null when undefined (no references or no citations)
    n_citing: int  # N_F
    n_i: int
    n_j: int
    n_k: int
    refs_used: int
    refs_total: int | None
    computed_at: ISODateTime
    label: Literal["disruptive", "mixed", "consolidating", "undefined"]
    text: str


class ProvenanceEntry(ApiModel):
    source: str
    observed_at: ISODateTime


class PaperCounts(ApiModel):
    references: int | None
    citations: int | None


class PaperDetail(PaperSummary):
    provenance: dict[str, ProvenanceEntry]
    counts: PaperCounts
    state: PaperState | None  # null without project_id
    last_interaction_id: int | None
    recommendation: RecommendationItem | None  # latest slate item for ?project_id
    expansion: ExpansionStatus
    disruption: DisruptionDetail | None


class LibraryItem(ApiModel):
    paper: PaperSummary
    state: PaperState
    updated_at: ISODateTime
    last_interaction_id: int | None


class LibraryPage(ApiModel):
    items: list[LibraryItem]
    next_cursor: str | None
    total: int


class SearchResults(ApiModel):
    items: list[PaperSummary]


class ReadRequest(ApiModel):
    read: bool


class ReadMark(ApiModel):
    paper_id: int
    read_at: ISODateTime | None


# ------------------------------------------------------------------------------------------------
# Knowledge graph
# ------------------------------------------------------------------------------------------------


class ScholarSummary(ApiModel):
    id: int
    openalex_id: str
    display_name: str
    orcid: str | None
    works_count: int | None
    cited_by_count: int | None
    h_index: int | None
    institution: str | None
    analyzed_at: ISODateTime | None


NodeType = Literal["work", "concept", "scholar", "author", "project"]


class GraphNode(ApiModel):
    id: str  # "work:12" | "concept:5" | "scholar:3" | "author:12:0" | "project:1"
    type: NodeType
    label: str
    paper: PaperSummary | None = None  # works
    scholar: ScholarSummary | None = None  # scholars
    state: PaperState | None = None  # works, with a project
    seed: bool = False
    is_center: bool = False
    weight: float | None = None


class CitesEdgeEvidence(ApiModel):
    relation: Literal["CITES"] = "CITES"
    contexts: list[str]
    intents: list[str]
    is_influential: bool | None


class HasTopicEdgeEvidence(ApiModel):
    relation: Literal["HAS_TOPIC"] = "HAS_TOPIC"
    score: float | None
    subfield: str | None
    field: str | None


class SemanticallySimilarEdgeEvidence(ApiModel):
    relation: Literal["SEMANTICALLY_SIMILAR"] = "SEMANTICALLY_SIMILAR"
    model_id: str
    cosine: float


class AuthoredEdgeEvidence(ApiModel):
    relation: Literal["AUTHORED"] = "AUTHORED"
    position: int | None


GraphEdgeEvidence = Annotated[
    Union[CitesEdgeEvidence, HasTopicEdgeEvidence, SemanticallySimilarEdgeEvidence, AuthoredEdgeEvidence],
    Field(discriminator="relation"),
]
"""Discriminated on ``relation``."""


class GraphEdge(ApiModel):
    id: str  # "e:<db id>" or "a:<paper_id>:<position>" for name-only author edges
    source: str  # CITES: citing -> cited; AUTHORED: author -> work
    target: str
    relation: Relation
    verified: bool
    confidence: float | None  # SEMANTICALLY_SIMILAR: cosine; HAS_TOPIC: topic score
    provenance: str  # "s2" | "openalex" | "local/knn" | "paper_authors"
    evidence: GraphEdgeEvidence | None


GroupKey = Literal[
    "references", "citations", "similar", "topics", "authors", "author_works", "seeds", "relevant", "saved"
]


class GraphGroup(ApiModel):
    """``count`` = total known; ``node_ids`` = shown."""

    key: GroupKey
    label: str
    node_ids: list[str]
    count: int


class Neighborhood(ApiModel):
    center: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    groups: list[GraphGroup]
    expansion: ExpansionStatus
    notices: list[str]


class ProjectGraph(ApiModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    groups: list[GraphGroup]
    notices: list[str]


class ExpandResponse(ApiModel):
    job: Job | None
    expansion: ExpansionStatus
    message: str | None


class BreakthroughWork(ApiModel):
    paper: PaperSummary
    cd: float | None
    n_citing: int | None
    is_breakthrough: bool


class ScholarBreakthroughs(ApiModel):
    scholar: ScholarSummary
    position: int
    works: list[BreakthroughWork]  # by cd desc, nulls last


class Breakthroughs(ApiModel):
    status: Literal["none", "queued", "running", "done", "failed"]
    job: Job | None
    analyzed_at: ISODateTime | None
    paper: DisruptionDetail | None
    scholars: list[ScholarBreakthroughs]
    message: str | None


class ScholarDetail(ApiModel):
    scholar: ScholarSummary
    works: list[BreakthroughWork]


# ------------------------------------------------------------------------------------------------
# Documents (private)
# ------------------------------------------------------------------------------------------------


class DocumentOutlineItem(ApiModel):
    heading: str
    page_start: int | None
    n_chunks: int


class DocumentInfo(ApiModel):
    id: int
    project_id: int
    filename: str
    mime_type: str
    size_bytes: int
    status: Literal["uploaded", "processing", "ready", "failed"]
    error: str | None
    n_sections: int
    n_chunks: int
    n_pages: int | None
    created_at: ISODateTime
    outline: list[DocumentOutlineItem]
    job_id: int | None  # from its latest process_document job
    progress: float | None
    message: str | None


class DocumentAccepted(ApiModel):
    document: DocumentInfo
    job: Job


class DocumentDeletedCounts(ApiModel):
    file: bool
    sections: int
    chunks: int
    embeddings: int
    edges: int
    scrubbed_items: int


class DocumentDeleted(ApiModel):
    deleted: DocumentDeletedCounts


# ------------------------------------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------------------------------------


class Period(ApiModel):
    start: ISODate
    end: ISODate
    days: int
    bucket: Literal["day", "week"]
    tz: str


class ActivityTotals(ApiModel):
    swiped: int
    relevant: int
    not_relevant: int
    saved: int
    opened: int
    read: int
    decks: int


class ActivityPoint(ApiModel):
    date: ISODate  # bucket start
    swiped: int
    positive: int


class AllTimeTotals(ApiModel):
    swiped: int
    read: int
    library: int


class ProjectActivity(ApiModel):
    project_id: int
    name: str
    swiped: int
    positive: int


class DashboardActivity(ApiModel):
    period: Period
    totals: ActivityTotals
    previous: ActivityTotals
    all_time: AllTimeTotals
    series: list[ActivityPoint]
    positive_rate: float | None
    positive_rate_before: float | None
    streak_days: int
    by_project: list[ProjectActivity]


class TopicKey(ApiModel):
    kind: Literal["topic", "category"]
    key: str  # "concept:<id>" | "cat:<code>"
    label: str
    concept_id: int | None
    category: str | None


class TopicStat(TopicKey):
    count: int
    share: float
    count_before: int
    share_before: float
    delta: float
    weekly: list[int]  # 12 values, oldest first, aligned with DashboardTopics.weeks
    swiped: int
    skip_rate: float | None


class DashboardTopics(ApiModel):
    period: Period
    weeks: list[ISODate]
    n_positive: int
    n_positive_before: int
    leaning_in: list[TopicStat]
    fading: list[TopicStat]
    top: list[TopicStat]
    skipping: list[TopicStat]
    coverage: float | None  # share of liked papers with an OpenAlex topic


class Finding(ApiModel):
    paper: PaperSummary
    sentence: str
    cues: list[str]
    score: float
    similarity: float | None  # to the user's work (SPEC §14.5)
    action: Literal["relevant", "save"]
    acted_at: ISODateTime


class FindingGroup(ApiModel):
    topic: TopicKey
    findings: list[Finding]


class SummaryPaperRef(ApiModel):
    id: int
    title: str


class SummaryPoint(ApiModel):
    text: str
    papers: list[SummaryPaperRef]


class SummaryTheme(ApiModel):
    title: str
    points: list[SummaryPoint]


class FindingsSummary(ApiModel):
    id: int
    created_at: ISODateTime
    model: str
    n_papers: int
    themes: list[SummaryTheme]
    dropped_points: int  # points removed because they cited nothing valid
    stale: bool  # liked papers changed since it was written


class AiStatus(ApiModel):
    available: bool
    model: str | None
    remaining_today: int
    job: Job | None


class DashboardFindings(ApiModel):
    period: Period
    n_papers: int
    n_with_findings: int
    groups: list[FindingGroup]
    summary: FindingsSummary | None
    ai: AiStatus


class QuestionMatch(ApiModel):
    kind: Literal["question"] = "question"


class DocumentMatch(ApiModel):
    kind: Literal["document"] = "document"
    document_id: int
    filename: str
    section: str
    page: int | None
    snippet: str


WorkMatch = Annotated[Union[QuestionMatch, DocumentMatch], Field(discriminator="kind")]


class WorkPaper(ApiModel):
    paper: PaperSummary
    similarity: float
    match: WorkMatch


class WorkQuartile(ApiModel):
    lo: float
    hi: float
    n: int
    positive_rate: float | None


class WorkCorrelation(ApiModel):
    r: float
    n: int
    strength: Literal["none", "weak", "moderate", "strong"]
    text: str


class WorkSection(ApiModel):
    document_id: int
    filename: str
    section_id: int
    heading: str
    n_supporting: int
    best: WorkPaper | None


class WorkDriftPoint(ApiModel):
    week: ISODate
    mean_similarity: float | None
    n: int


class WorkProject(ApiModel):
    project_id: int
    name: str
    has_documents: bool
    threshold: float
    closest: list[WorkPaper]
    quartiles: list[WorkQuartile]  # 4 groups, or [] with < 8 swipes
    correlation: WorkCorrelation | None
    sections: list[WorkSection]
    drift: list[WorkDriftPoint]  # 12 weeks, oldest first


class DashboardWork(ApiModel):
    period: Period
    projects: list[WorkProject]


class SummaryRequest(ApiModel):
    days: Literal[7, 30, 90, 365] = 30
    project_id: int | None = None
    tz: str | None = Field(None, max_length=64)  # IANA zone of the period (as on the dashboard)


# ------------------------------------------------------------------------------------------------
# Jobs, settings, health
# ------------------------------------------------------------------------------------------------


class RunJobRequest(ApiModel):
    kind: JobKind
    project_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class BudgetInfo(ApiModel):
    provider: str
    daily_budget_usd: float
    spent_today_usd: float
    remaining_today_usd: float | None
    stop_fraction: float
    stopped: bool
    source: Literal["headers", "ledger"]


class EmbedderInfo(ApiModel):
    model_id: str
    dim: int
    available: bool
    reason: str | None


class EmbeddingSettings(ApiModel):
    default_model: str
    active_model: str
    fallback_active: bool
    model_cached: bool
    download_job: Job | None
    available: list[EmbedderInfo]


class AiSummarySettings(ApiModel):
    configured: bool
    model: str | None


class SchedulerSettings(ApiModel):
    enabled: bool
    next_run_at: ISODateTime | None
    cron: str
    timezone: str


class OpenAlexStatus(ApiModel):
    has_key: bool
    budget: BudgetInfo


class SemanticScholarStatus(ApiModel):
    has_key: bool
    breaker_until: ISODateTime | None


class BreakerStatus(ApiModel):
    breaker_until: ISODateTime | None


class SmtpStatus(ApiModel):
    configured: bool


class OAuthStatus(ApiModel):
    google: bool
    github: bool


class ConnectorsStatus(ApiModel):
    openalex: OpenAlexStatus
    semantic_scholar: SemanticScholarStatus
    arxiv: BreakerStatus
    smtp: SmtpStatus
    oauth: OAuthStatus


class AdminSettings(ApiModel):
    scheduler: SchedulerSettings
    connectors: ConnectorsStatus
    users: int
    last_daily: Job | None


class SystemSettings(ApiModel):
    offline: bool
    privacy_disclosure: str  # SPEC §16 text
    embedding: EmbeddingSettings
    ai_summary: AiSummarySettings
    admin: AdminSettings | None  # only for admins
    version: str


class SettingsUpdate(ApiModel):
    scheduler_enabled: bool | None = None


class Health(ApiModel):
    status: Literal["ok"]
    version: str
    db: Literal["sqlite", "postgresql"]
    offline: bool
    embedder: str
    auth: AuthMode
    time: ISODateTime


# ------------------------------------------------------------------------------------------------
# Wrappers and errors
# ------------------------------------------------------------------------------------------------


class JobAccepted(ApiModel):
    """``202 { job }`` for long-running work."""

    job: Job


class FieldError(ApiModel):
    path: str  # dotted, e.g. "settings.slate_size"
    message: str


class ValidationErrorDetails(ApiModel):
    fields: list[FieldError]


class JobConflictDetails(ApiModel):
    job: Job


class DocumentConflictDetails(ApiModel):
    document_id: int


class RetryAfterDetails(ApiModel):
    retry_after_seconds: int


ErrorDetails = Union[
    ValidationErrorDetails, JobConflictDetails, DocumentConflictDetails, RetryAfterDetails, dict[str, Any]
]


class ErrorBody(ApiModel):
    code: ErrorCode
    message: str
    request_id: str
    details: ErrorDetails | None = None  # omitted from responses when absent


class ErrorEnvelope(ApiModel):
    error: ErrorBody
