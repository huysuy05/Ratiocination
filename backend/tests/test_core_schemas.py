"""API schemas mirror docs/API.md v0.3 (SPEC §2: schemas.py)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from radar import schemas as S

API_TYPES = [
    "Job", "Author", "TopicRef", "PaperSummary", "ExpansionStatus", "AnchorSimilarityEvidence",
    "AnchorEdgeEvidence", "DraftEvidence", "DeletedDocumentEvidence", "Reason", "FeatureScore",
    "FeatureScores", "RecommendationItem", "AuthConfig", "UserPreferences", "Identity", "User", "Me",
    "SignUpRequest", "SignInRequest", "ForgotPasswordRequest", "ResetPasswordRequest", "VerifyEmailRequest",
    "MeUpdate", "PasswordChangeRequest", "AccountDeleteRequest", "ProjectSettings", "ProjectStats", "Project",
    "ProjectCreate", "ProjectUpdate", "Seed", "SeedCreate", "SeedCreated", "Slate", "FeedResponse",
    "RebuildRequest", "FeedbackRequest", "FeedbackResponse", "UndoRequest", "UndoResponse", "DisruptionDetail",
    "PaperDetail", "LibraryItem", "LibraryPage", "ReadMark", "ScholarSummary", "GraphNode", "GraphEdgeEvidence",
    "GraphEdge", "GraphGroup", "Neighborhood", "ProjectGraph", "ExpandResponse", "BreakthroughWork",
    "ScholarBreakthroughs", "Breakthroughs", "DocumentOutlineItem", "DocumentInfo", "DocumentAccepted",
    "DocumentDeleted", "Period", "ActivityTotals", "ActivityPoint", "DashboardActivity", "TopicKey",
    "TopicStat", "DashboardTopics", "Finding", "FindingGroup", "SummaryPoint", "FindingsSummary",
    "DashboardFindings", "WorkMatch", "WorkPaper", "WorkSection", "WorkProject", "DashboardWork",
    "SummaryRequest", "RunJobRequest", "BudgetInfo", "EmbedderInfo", "SystemSettings", "SettingsUpdate",
    "Health", "ErrorEnvelope", "JobAccepted", "PaperState", "FeedbackAction", "SlateMode", "JobStatus",
    "JobKind", "Relation", "FeatureName", "ErrorCode", "AnchorKind", "AuthMode", "OAuthProvider",
    "ISODateTime", "ISODate",
]  # fmt: skip

REMOVED_TYPES = [
    "Confidence", "CalibrationSnapshot", "CalibrationSummary", "CostSummary", "ProjectTopic", "TopicsUpdate",
    "ConceptHit", "EdgeFeedbackRequest", "PaperVersion", "SlotType", "ConfidenceLabel", "PrivacyMode",
]  # fmt: skip


def test_every_api_type_exists_and_removed_ones_are_gone() -> None:
    assert [name for name in API_TYPES if not hasattr(S, name)] == []
    assert [name for name in REMOVED_TYPES if hasattr(S, name)] == []


def _job(**overrides: Any) -> dict[str, Any]:
    job = {
        "id": 1,
        "kind": "build_deck",
        "status": "queued",
        "project_id": 3,
        "params": {"mode": "daily"},
        "stats": {},
        "progress": None,
        "message": None,
        "error_code": None,
        "error_message": None,
        "created_at": datetime(2026, 10, 12, 1, 2, 3, tzinfo=UTC),
        "started_at": None,
        "finished_at": None,
    }
    job.update(overrides)
    return job


def test_datetimes_serialize_as_utc_with_z() -> None:
    eastern = timezone(timedelta(hours=-4))
    job = S.Job.model_validate(_job(created_at=datetime(2026, 10, 12, 8, 0, 0, tzinfo=eastern)))
    data = json.loads(job.model_dump_json())
    assert data["created_at"] == "2026-10-12T12:00:00Z"
    job = S.Job.model_validate(_job(created_at=datetime(2026, 10, 12, 8, 0, 0, 5, tzinfo=UTC)))
    assert json.loads(job.model_dump_json())["created_at"] == "2026-10-12T08:00:00.000005Z"
    job = S.Job.model_validate(_job(created_at="2026-10-12T08:00:00Z"))
    assert job.created_at == datetime(2026, 10, 12, 8, tzinfo=UTC)
    assert S.iso_z(datetime(2026, 1, 2, tzinfo=UTC)) == "2026-01-02T00:00:00Z"


def test_naive_datetimes_rejected() -> None:
    with pytest.raises(ValidationError):
        S.Job.model_validate(_job(created_at=datetime(2026, 10, 12, 8, 0)))


def test_dates_serialize_as_iso_date() -> None:
    point = S.ActivityPoint(date=date(2026, 10, 12), swiped=3, positive=1)
    assert json.loads(point.model_dump_json())["date"] == "2026-10-12"


ANCHOR = {"anchor_paper_id": 2, "anchor_title": "Test paper A", "anchor_kind": "seed"}
REASONS: list[dict[str, Any]] = [
    {"code": "question", "kind": "model", "evidence": {"cosine": 0.71}},
    {
        "code": "similar_to_anchor",
        "kind": "model",
        "evidence": {**ANCHOR, "anchor_kind": "relevant", "cosine": 0.8},
    },
    {
        "code": "draft_match",
        "kind": "model",
        "evidence": {
            "document_id": 1,
            "chunk_id": 2,
            "filename": "test.txt",
            "section": "Introduction",
            "page": None,
            "snippet": "Synthetic snippet",
        },
    },
    {"code": "draft_match", "kind": "model", "evidence": {"deleted_document": True}},
    {"code": "cites_anchor", "kind": "metadata", "evidence": {**ANCHOR, "direction": "cites"}},
    {
        "code": "cited_by_anchor",
        "kind": "metadata",
        "evidence": {**ANCHOR, "anchor_kind": "saved", "direction": "cited_by"},
    },
    {"code": "preference", "kind": "model", "evidence": {"n_labels": 4, "probability": 0.7}},
    {"code": "category", "kind": "metadata", "evidence": {"category": "cs.IR"}},
    {"code": "recent", "kind": "metadata", "evidence": {"days": 2}},
]


@pytest.mark.parametrize("reason", REASONS, ids=[f"{r['code']}-{i}" for i, r in enumerate(REASONS)])
def test_reason_union_round_trip(reason: dict[str, Any]) -> None:
    payload = {**reason, "text": "Template text", "contribution": 0.1}
    adapter = TypeAdapter(S.Reason)
    model = adapter.validate_python(payload)
    assert adapter.dump_python(model, mode="json") == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"code": "unknown_code", "kind": "model", "text": "t", "evidence": None, "contribution": None},
        {"code": "exploration", "kind": "model", "text": "t", "evidence": None, "contribution": None},
        {"code": "recent", "kind": "model", "text": "t", "evidence": {"days": 2}, "contribution": None},
        {
            "code": "similar_to_anchor",
            "kind": "model",
            "text": "t",
            "evidence": {**ANCHOR, "anchor_kind": "pinned", "cosine": 1},
        },
        {"code": "question", "kind": "model", "text": "t", "evidence": None, "contribution": None},
    ],
)
def test_reason_union_rejects_invalid(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(S.Reason).validate_python(payload)


def test_reason_defaults_fill_kind() -> None:
    assert S.CategoryReason(text="x", evidence={"category": "cs.IR"}).model_dump() == {
        "code": "category",
        "kind": "metadata",
        "text": "x",
        "evidence": {"category": "cs.IR"},
        "contribution": None,
    }


@pytest.mark.parametrize(
    "evidence",
    [
        {"relation": "CITES", "contexts": ["…"], "intents": [], "is_influential": None},
        {"relation": "HAS_TOPIC", "score": 0.9, "subfield": None, "field": "Computer Science"},
        {"relation": "SEMANTICALLY_SIMILAR", "model_id": "local/hashing-v1", "cosine": 0.5},
        {"relation": "AUTHORED", "position": 0},
    ],
)
def test_graph_edge_evidence_union(evidence: dict[str, Any]) -> None:
    adapter = TypeAdapter(S.GraphEdgeEvidence)
    assert adapter.dump_python(adapter.validate_python(evidence), mode="json") == evidence


@pytest.mark.parametrize("relation", ["USER_SAVED", "SAME_WORK_AS"])
def test_graph_edge_evidence_rejects_unknown_relation(relation: str) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(S.GraphEdgeEvidence).validate_python({"relation": relation, "matched_on": "title"})


def test_work_match_union() -> None:
    adapter = TypeAdapter(S.WorkMatch)
    assert isinstance(adapter.validate_python({"kind": "question"}), S.QuestionMatch)
    document = adapter.validate_python(
        {
            "kind": "document",
            "document_id": 1,
            "filename": "d.md",
            "section": "Intro",
            "page": None,
            "snippet": "s",
        }
    )
    assert isinstance(document, S.DocumentMatch)
    with pytest.raises(ValidationError):
        adapter.validate_python({"kind": "seed"})


def test_feature_scores_require_all_seven() -> None:
    score = {"value": 0.5, "raw": 0.1, "weight": 0.2, "contribution": 0.1, "available": True}
    full = {name: score for name in S.FEATURE_NAMES}
    assert S.FEATURE_NAMES == ("question", "anchor", "keyword", "draft", "citation", "preference", "recency")
    assert set(S.FeatureScores.model_validate(full).model_dump()) == set(S.FEATURE_NAMES)
    partial = dict(full)
    partial.pop("draft")
    with pytest.raises(ValidationError):
        S.FeatureScores.model_validate(partial)


def test_project_settings_defaults_and_validation() -> None:
    assert S.ProjectSettings().model_dump() == {
        "arxiv_categories": [],
        "time_window_days": 14,
        "slate_size": 20,
    }
    assert S.ProjectSettings(arxiv_categories=["cs.IR", "cs.DL", "cs.IR"]).arxiv_categories == [
        "cs.IR",
        "cs.DL",
    ]
    for bad in ({"slate_size": 4}, {"time_window_days": 91}, {"arxiv_categories": ["CS.IR"]}):
        with pytest.raises(ValidationError):
            S.ProjectSettings.model_validate(bad)
    assert (
        S.ProjectSettings.from_stored({"slate_size": 10, "fdr_target": 0.2}).slate_size == 10
    )  # old keys ignored
    assert S.ProjectSettings.from_stored(None) == S.ProjectSettings()


def test_seed_create_exactly_one_of() -> None:
    assert S.SeedCreate(identifier=" 10.48550/arXiv.2504.08385 ").identifier == "10.48550/arXiv.2504.08385"
    assert S.SeedCreate(paper_id=3, weight=2).weight == 2
    for bad in (
        {},
        {"identifier": "x", "paper_id": 1},
        {"identifier": "   "},
        {"paper_id": 1, "weight": 0.05},
    ):
        with pytest.raises(ValidationError):
            S.SeedCreate.model_validate(bad)


def test_project_update_rejects_explicit_null_but_allows_omission() -> None:
    assert S.ProjectUpdate.model_validate({}).model_fields_set == set()
    with pytest.raises(ValidationError):
        S.ProjectUpdate.model_validate({"name": None})


def test_request_defaults() -> None:
    assert S.RebuildRequest().model_dump() == {"mode": "daily", "prepare": False}
    assert S.UndoRequest().interaction_id is None
    assert S.RunJobRequest(kind="daily").params == {}
    assert S.SummaryRequest().model_dump() == {"days": 30, "project_id": None, "tz": None}
    with pytest.raises(ValidationError):
        S.FeedbackRequest.model_validate({"paper_id": 1, "action": "pin"})
    with pytest.raises(ValidationError):
        S.SummaryRequest.model_validate({"days": 14})
    assert S.FeedbackRequest.model_validate(
        {"paper_id": 1, "action": "open", "context": {"source": "dashboard"}}
    )


def test_account_requests() -> None:
    signup = S.SignUpRequest.model_validate({"email": "  Ada@Example.ORG ", "password": "correct horse"})
    assert signup.email == "ada@example.org" and signup.display_name is None
    for bad in (
        {"email": "ada@example.org", "password": "short"},
        {"email": "ada@example.org", "password": "x" * 257},
        {"email": "", "password": "correct horse"},
    ):
        with pytest.raises(ValidationError):
            S.SignUpRequest.model_validate(bad)
    assert S.MeUpdate.model_validate({"preferences": {"timezone": "Europe/Paris"}}).preferences is not None
    with pytest.raises(ValidationError):
        S.MeUpdate.model_validate({"preferences": {"timezone": "Mars/Olympus"}})
    with pytest.raises(ValidationError):
        S.ResetPasswordRequest.model_validate({"token": "short", "password": "correct horse"})


def test_error_envelope_model() -> None:
    env = S.ErrorEnvelope.model_validate(
        {
            "error": {
                "code": "validation_error",
                "message": "Validation failed",
                "request_id": "abc",
                "details": {"fields": [{"path": "settings.slate_size", "message": "too big"}]},
            }
        }
    )
    assert isinstance(env.error.details, S.ValidationErrorDetails)
    limited = S.ErrorEnvelope.model_validate(
        {
            "error": {
                "code": "rate_limited",
                "message": "x",
                "request_id": "r",
                "details": {"retry_after_seconds": 30},
            }
        }
    )
    assert isinstance(limited.error.details, S.RetryAfterDetails)
    with pytest.raises(ValidationError):
        S.ErrorEnvelope.model_validate({"error": {"code": "teapot", "message": "x", "request_id": "r"}})


def test_job_accepted_and_health_shapes() -> None:
    accepted = S.JobAccepted(job=S.Job.model_validate(_job()))
    assert set(json.loads(accepted.model_dump_json())) == {"job"}
    health = S.Health(
        status="ok",
        version="0.1.0",
        db="sqlite",
        offline=True,
        embedder="x",
        auth="local",
        time=datetime.now(UTC),
    )
    assert set(health.model_dump()) == {"status", "version", "db", "offline", "embedder", "auth", "time"}
