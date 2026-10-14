"""store.papers (SPEC §5): upsert, matching, merge rules, versions, provenance and dedup.

Records are built by hand from the real fixture payloads (the connectors' parsers belong to other
agents); purely synthetic records use "Test paper ..." titles and 2610.0000x arXiv ids.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import func, select

from radar import models
from radar.db import StrictTransactionError, session_scope
from radar.store.papers import (
    PaperRecord,
    UpsertResult,
    get_paper_by_identifier,
    latest_version_label,
    latest_versions,
    payload_hash,
    upsert_paper,
)

# ------------------------------------------------------------------------------------------------
# Records from the fixtures
# ------------------------------------------------------------------------------------------------

NONEXCLUSIVE = "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"
CC_BY = "http://creativecommons.org/licenses/by/4.0/"

# arxiv_feed_multi.xml: (base, version, announce_type, title, categories, rights, creators, abstract start)
FEED_ENTRIES: list[tuple[str, str, str, str, list[str], str, list[str], str]] = [
    (
        "2610.12545",
        "v1",
        "new",
        "AutoIndexer: Flexible-Order Decoding by Training Causal Models on Chains of Edits",
        ["cs.CL", "cs.AI", "cs.LG"],
        NONEXCLUSIVE,
        [
            "Shu Ishida",
            "Aliasghar Khani",
            "Tianyu Zhang",
            "The Cong Luong",
            "James Seale Smith",
            "Adam Gaier",
        ],
        "Autoregressive decoding is less suited to non-causally ordered data, e.g., cross-references or "
        "future dependencies, since their outputs cannot be revised post hoc, unlike iterative human reasoning.",
    ),
    (
        "2610.12569",
        "v1",
        "new",
        "SWE-Duel: Towards a Self-Scaling Arena for Coding Agents",
        ["cs.CL", "cs.SE"],
        CC_BY,
        ["Wei Minn", "Zhensu Sun", "Lwin Khin Shar", "David Lo"],
        "Static coding benchmarks are fixed at release.",
    ),
    (
        "2610.12495",
        "v1",
        "cross",
        "Kairos: Toward a Byte-Level Foundation Model for Every Modality",
        ["cs.LG", "cs.AI", "cs.CL"],
        NONEXCLUSIVE,
        ["Fabien Furfaro"],
        "Small language models trained on a single local machine leave little room for architectural trial "
        "and error.",
    ),
    (
        "2610.12496",
        "v1",
        "cross",
        "An Input-Dependent Readout Phase for Resonant Sequence Models",
        ["cs.LG", "cs.CL"],
        NONEXCLUSIVE,
        ["Shivesh Rane"],
        "Sequence models with a fixed-size state avoid the quadratic cost of attention, but face a design choice.",
    ),
    (
        "2405.01930",
        "v3",
        "replace",
        "OARelatedWork: A Large-Scale Dataset of Related Work Sections with Full-texts from Open Access Sources",
        ["cs.CL"],
        CC_BY,
        ["Martin Docekal", "Martin Fajcik", "Pavel Smrz"],
        "This paper introduces OARelatedWork: a dataset for related work generation from open-access sources.",
    ),
    (
        "2412.13844",
        "v2",
        "replace",
        "CRM: Retrieval Model with Controllable Condition",
        ["cs.IR", "cs.AI"],
        CC_BY,
        [
            "Chi Liu",
            "Jiangxia Cao",
            "Rui Huang",
            "Kuo Cai",
            "Weifeng Ding",
            "Qiang Luo",
            "Kun Gai",
            "Guorui Zhou",
        ],
        "Retrieval models for recommendation need to account for outcomes such as watch time while searching "
        "large item corpora efficiently.",
    ),
    (
        "2505.15738",
        "v3",
        "replace-cross",
        "Auditing Prompt Injection Defenses Before They Reach Users: What Strong Adversaries Reveal",
        ["cs.CR", "cs.AI", "cs.CL", "cs.LG"],
        NONEXCLUSIVE,
        ["Xiaoxue Yang", "Bozhidar Stevanoski", "Matthieu Meeus", "Yves-Alexandre de Montjoye"],
        "Large language models are increasingly deployed in user-facing products that handle sensitive "
        "personal data while interacting with untrusted external content.",
    ),
]
FEED_DAY = date(2026, 10, 12)  # <published>2026-10-12T00:00:00-04:00</published>


def feed_record(index: int, **overrides: Any) -> PaperRecord:
    base, version, announce, title, categories, rights, creators, abstract = FEED_ENTRIES[index]
    entry_id = f"oai:arXiv.org:{base}{version}"
    values: dict[str, Any] = dict(
        source="arxiv_feed",
        source_id=entry_id,
        title=title,
        abstract=abstract,
        authors=list(creators),
        publication_date=FEED_DAY,
        year=FEED_DAY.year,
        identifiers={"arxiv": entry_id},
        version_label=version,
        announce_type=announce,
        categories=list(categories),
        primary_category=categories[0],
        license=rights,
        landing_url=f"https://arxiv.org/abs/{base}",
        pdf_url=f"https://arxiv.org/pdf/{base}{version}",
        raw={"id": entry_id, "announce_type": announce, "published": "2026-10-12T00:00:00-04:00"},
    )
    values.update(overrides)
    return PaperRecord(**values)


SPECTER_TITLE = "SPECTER: Document-level Representation Learning using Citation-informed Transformers"
SPECTER_DOI = "10.18653/v1/2020.acl-main.207"


def openalex_record(
    work: dict[str, Any], *, version_label: str | None = "published", **overrides: Any
) -> PaperRecord:
    ids = work["ids"]
    identifiers = {"openalex": work["id"], "doi": work["doi"]}
    if "pmid" in ids:
        identifiers["pmid"] = ids["pmid"]
    best = work.get("best_oa_location") or {}
    primary = work["primary_location"]
    values: dict[str, Any] = dict(
        source="openalex",
        source_id=work["id"],
        title=work["title"],
        authors=[a["author"]["display_name"] for a in work["authorships"]],
        publication_date=date.fromisoformat(work["publication_date"]),
        year=work["publication_year"],
        identifiers=identifiers,
        version_label=version_label,
        venue=(primary.get("source") or {}).get("display_name"),
        paper_type=work["type"],
        cited_by_count=work["cited_by_count"],
        is_oa=work["open_access"]["is_oa"],
        oa_url=work["open_access"]["oa_url"],
        best_oa_pdf=best.get("pdf_url"),
        landing_url=primary.get("landing_page_url"),
        pdf_url=primary.get("pdf_url"),
        license=primary.get("license"),
        raw=work,
    )
    values.update(overrides)
    return PaperRecord(**values)


def s2_record(paper: dict[str, Any], **overrides: Any) -> PaperRecord:
    ext = paper["externalIds"]
    values: dict[str, Any] = dict(
        source="s2",
        source_id=paper["paperId"],
        title=paper["title"],
        abstract=paper.get("abstract"),
        authors=[a["name"] for a in paper["authors"]],
        year=paper["year"],
        identifiers={"s2": paper["paperId"], "arxiv": ext.get("ArXiv"), "doi": ext.get("DOI")},
        venue=paper.get("venue"),
        cited_by_count=paper.get("citationCount"),
        raw=paper,
    )
    values.update(overrides)
    return PaperRecord(**values)


def specter_arxiv_record(s2_paper: dict[str, Any]) -> PaperRecord:
    """SPECTER as an arXiv API record (2004.07180v1), built from the S2 batch and OpenAlex location values."""
    return PaperRecord(
        source="arxiv_api",
        source_id="http://arxiv.org/abs/2004.07180v1",
        title=s2_paper["title"],
        abstract=s2_paper["abstract"],
        authors=[a["name"] for a in s2_paper["authors"]],
        year=2020,
        identifiers={"arxiv": "http://arxiv.org/abs/2004.07180v1"},
        version_label="v1",
        landing_url="http://arxiv.org/abs/2004.07180",
        pdf_url="https://arxiv.org/pdf/2004.07180",
        raw={"id": "http://arxiv.org/abs/2004.07180v1"},
    )


SCHOLAR_INBOX_AUTHORS = [
    "Markus Flicke",
    "Glenn Angrabeit",
    "Madhav Iyengar",
    "Vitalii Protsenko",
    "Illia Shakun",
    "Jovan Cicvaric",
    "Bora Kargi",
    "Haoyu He",
    "Lukas Schuler",
    "Lewin Scholz",
    "Kavyanjali Agnihotri",
    "Yong Cao",
    "Andreas Geiger",
]


def scholar_inbox_arxiv_record(**overrides: Any) -> PaperRecord:
    """arxiv_api_query.xml entry http://arxiv.org/abs/2504.08385v2."""
    values: dict[str, Any] = dict(
        source="arxiv_api",
        source_id="http://arxiv.org/abs/2504.08385v2",
        title="Scholar Inbox: Personalized Paper Recommendations for Scientists",
        abstract=(
            "Scholar Inbox is a new open-access platform designed to address the challenges researchers face "
            "in staying current with the rapidly expanding volume of scientific literature."
        ),
        authors=list(SCHOLAR_INBOX_AUTHORS),
        publication_date=date(2025, 4, 11),
        year=2025,
        identifiers={"arxiv": "http://arxiv.org/abs/2504.08385v2"},
        version_label="v2",
        categories=["cs.CL", "cs.AI", "cs.IR"],
        primary_category="cs.CL",
        landing_url="https://arxiv.org/abs/2504.08385v2",
        pdf_url="https://arxiv.org/pdf/2504.08385v2",
        source_updated_at=datetime(2025, 6, 14, 16, 27, 19, tzinfo=UTC),
        raw={"id": "http://arxiv.org/abs/2504.08385v2", "updated": "2025-06-14T16:27:19Z"},
    )
    values.update(overrides)
    return PaperRecord(**values)


def synthetic(title: str, **overrides: Any) -> PaperRecord:
    values: dict[str, Any] = dict(
        source="arxiv_api",
        source_id=f"test:{title}",
        title=title,
        abstract=f"Synthetic abstract for {title}.",
        authors=["Ada Test", "Grace Example"],
        publication_date=date(2026, 10, 1),
        year=2026,
        raw={"title": title},
    )
    values.update(overrides)
    return PaperRecord(**values)


# ------------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------------


def upsert(record: PaperRecord) -> UpsertResult:
    with session_scope(write=True) as session:
        return upsert_paper(session, record)


def count(model: type[models.Base]) -> int:
    with session_scope() as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def db_snapshot() -> dict[str, list[tuple[Any, ...]]]:
    tables = (
        models.Paper,
        models.PaperIdentifier,
        models.PaperVersion,
        models.Author,
        models.PaperAuthor,
        models.SourceRecord,
        models.DuplicateCandidate,
        models.GraphEdge,
    )
    with session_scope() as session:
        return {
            model.__tablename__: [
                tuple(row) for row in session.execute(select(model.__table__).order_by(model.__table__.c.id))
            ]
            for model in tables
        }


def identifiers_of(paper_id: int) -> set[tuple[str, str, bool]]:
    with session_scope() as session:
        rows = session.execute(
            select(
                models.PaperIdentifier.scheme, models.PaperIdentifier.value, models.PaperIdentifier.is_primary
            ).where(models.PaperIdentifier.paper_id == paper_id)
        )
        return {(s, v, bool(p)) for s, v, p in rows}


def versions_of(paper_id: int) -> list[models.PaperVersion]:
    with session_scope() as session:
        return list(
            session.scalars(
                select(models.PaperVersion)
                .where(models.PaperVersion.paper_id == paper_id)
                .order_by(models.PaperVersion.id)
            )
        )


def authors_of(paper_id: int) -> list[tuple[int, str, str, str]]:
    with session_scope() as session:
        rows = session.execute(
            select(
                models.PaperAuthor.author_id,
                models.Author.display_name,
                models.Author.name_norm,
                models.PaperAuthor.raw_name,
            )
            .join(models.Author, models.Author.id == models.PaperAuthor.author_id)
            .where(models.PaperAuthor.paper_id == paper_id)
            .order_by(models.PaperAuthor.position)
        )
        return [tuple(r) for r in rows]  # type: ignore[misc]


def paper(paper_id: int) -> models.Paper:
    with session_scope() as session:
        found = session.get(models.Paper, paper_id)
        assert found is not None
        return found


# ------------------------------------------------------------------------------------------------
# Pure helpers
# ------------------------------------------------------------------------------------------------


def test_payload_hash_matches_spec_formula() -> None:
    raw = {"b": [1, 2], "a": "Ünïcode", "when": date(2026, 10, 12)}
    expected = hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    assert payload_hash(raw) == expected
    assert payload_hash({"a": "Ünïcode", "b": [1, 2], "when": date(2026, 10, 12)}) == expected  # key order
    assert payload_hash(None) == payload_hash({}) == hashlib.sha256(b"{}").hexdigest()
    assert len(payload_hash({"x": 1})) == 64


@pytest.mark.parametrize(
    ("labels", "expected"),
    [
        (["v1", "v3", "v2", "published"], "v3"),
        (["published"], "published"),
        (["v10", "v9"], "v10"),
        ([], None),
        ([None, "other"], None),
    ],
)
def test_latest_version_label(labels: list[str | None], expected: str | None) -> None:
    assert latest_version_label(labels) == expected


def test_unknown_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown PaperRecord.source"):
        upsert(synthetic("Test paper Z", source="scholar_inbox"))
    assert count(models.Paper) == 0


def test_upsert_requires_a_write_session() -> None:
    with pytest.raises(StrictTransactionError):
        with session_scope() as session:
            upsert_paper(session, synthetic("Test paper read-only"))
    assert count(models.Paper) == 0


# ------------------------------------------------------------------------------------------------
# Feed ingestion (acceptance 1)
# ------------------------------------------------------------------------------------------------


def test_feed_entries_create_canonical_papers() -> None:
    results = [upsert(feed_record(i)) for i in range(len(FEED_ENTRIES))]
    assert all(r.created and not r.new_version and r.duplicate_candidate_of is None for r in results)
    assert len({r.paper_id for r in results}) == 7
    assert count(models.Paper) == 7
    assert count(models.PaperVersion) == 7
    assert count(models.SourceRecord) == 7

    new = paper(results[0].paper_id)
    assert new.canonical_title == FEED_ENTRIES[0][3]
    assert (
        new.title_norm == "autoindexer flexible order decoding by training causal models on chains of edits"
    )
    assert new.publication_date == FEED_DAY and new.year == 2026
    assert new.status == "active" and new.ingested_via == "arxiv_feed"
    assert new.primary_category == "cs.CL" and new.categories_json == ["cs.CL", "cs.AI", "cs.LG"]
    assert new.first_author_norm == "ishida"
    assert new.first_seen_at.tzinfo is not None
    assert set(new.field_provenance_json) >= {
        "title",
        "abstract",
        "authors",
        "categories",
        "publication_date",
    }
    assert new.field_provenance_json["title"]["source"] == "arxiv_feed"
    assert new.field_provenance_json["title"]["observed_at"].endswith("Z")
    assert identifiers_of(new.id) == {("arxiv", "2610.12545", True)}
    assert [a[1] for a in authors_of(new.id)] == FEED_ENTRIES[0][6]
    assert [a[2] for a in authors_of(new.id)][:2] == ["ishida", "khani"]  # authors.name_norm = surname
    (version,) = versions_of(new.id)
    assert (version.source, version.version_label, version.announce_type) == ("arxiv", "v1", "new")
    assert version.license == NONEXCLUSIVE and version.pdf_url == "https://arxiv.org/pdf/2610.12545v1"
    assert version.source_id == "oai:arXiv.org:2610.12545v1" and version.source_updated_at is None

    cross = paper(results[2].paper_id)
    assert cross.publication_date == FEED_DAY  # cross-lists contribute their date

    # replace / replace-cross announcements never set the publication date
    for i in (4, 5, 6):
        replaced = paper(results[i].paper_id)
        assert replaced.publication_date is None and replaced.year is None
        (version,) = versions_of(replaced.id)
        assert version.version_label == FEED_ENTRIES[i][1]
        assert version.announce_type == FEED_ENTRIES[i][2]
        assert version.source_updated_at == datetime(2026, 10, 12, tzinfo=UTC)


def test_ingesting_the_same_feed_twice_changes_nothing() -> None:
    first = [upsert(feed_record(i)) for i in range(len(FEED_ENTRIES))]
    before = db_snapshot()
    second = [upsert(feed_record(i)) for i in range(len(FEED_ENTRIES))]
    assert db_snapshot() == before
    for a, b in zip(first, second, strict=True):
        assert b == UpsertResult(
            paper_id=a.paper_id,
            created=False,
            changed_fields=[],
            new_version=False,
            duplicate_candidate_of=None,
        )


def test_replace_entry_with_explicit_announce_time() -> None:
    eastern = timezone(timedelta(hours=-4))
    result = upsert(feed_record(5, source_updated_at=datetime(2026, 10, 12, 0, 0, tzinfo=eastern)))
    (version,) = versions_of(result.paper_id)
    assert version.source_updated_at == datetime(2026, 10, 12, 4, 0, tzinfo=UTC)
    assert paper(result.paper_id).publication_date is None


# ------------------------------------------------------------------------------------------------
# One work through arXiv and its DOI (acceptance 2)
# ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("arxiv_first", [True, False])
def test_arxiv_and_doi_records_of_one_work_are_one_paper(
    load_fixture: Callable[[str], Any], arxiv_first: bool
) -> None:
    s2_paper = load_fixture("s2_batch.json")[0]
    arxiv = specter_arxiv_record(s2_paper)
    openalex = openalex_record(load_fixture("openalex_work_published.json"))
    s2 = s2_record(s2_paper)

    order = [arxiv, openalex] if arxiv_first else [openalex, arxiv]
    first, second = (upsert(r) for r in order)
    assert first.created and not second.created
    assert second.paper_id == first.paper_id  # title fallback: same title_norm, same year, same first author
    pid = first.paper_id
    third = upsert(s2)  # DOI match; its arXiv id and DOI are already attached
    assert third.paper_id == pid
    assert count(models.Paper) == 1
    assert third.duplicate_candidate_of is None
    assert count(models.DuplicateCandidate) == 0

    assert identifiers_of(pid) == {
        ("doi", SPECTER_DOI, True),
        ("arxiv", "2004.07180", True),
        ("openalex", "W3016913119", True),
        ("s2", "a3e4ceb42cbcd2c807d53aff90a8cb1f5ee3f031", True),
    }
    assert sorted((v.source, v.version_label) for v in versions_of(pid)) == [
        ("arxiv", "v1"),
        ("openalex", "published"),
    ]
    with session_scope() as session:
        assert latest_versions(session, [pid]) == {pid: "v1"}

    # everything again, in another order: nothing changes
    before = db_snapshot()
    for record in (s2, openalex, arxiv):
        again = upsert(record)
        assert again.paper_id == pid and not again.created and again.changed_fields == []
    assert db_snapshot() == before

    # lookups by any form of any identifier
    with session_scope() as session:
        for scheme, value in [
            ("doi", f"https://doi.org/{SPECTER_DOI.upper()}"),
            ("arxiv", "arXiv:2004.07180v3"),
            ("openalex", "https://openalex.org/W3016913119"),
            ("s2", "A3E4CEB42CBCD2C807D53AFF90A8CB1F5EE3F031"),
        ]:
            found = get_paper_by_identifier(session, scheme, value)
            assert found is not None and found.id == pid


def test_arxiv_doi_is_stored_as_arxiv_scheme(load_fixture: Callable[[str], Any]) -> None:
    arxiv = upsert(scholar_inbox_arxiv_record())
    work = load_fixture("openalex_work_arxiv.json")  # doi = https://doi.org/10.48550/arxiv.2504.08385
    via_openalex = upsert(openalex_record(work, version_label=None))
    via_s2 = upsert(s2_record(load_fixture("s2_batch.json")[1]))  # DOI 10.48550/arXiv.2504.08385
    assert via_openalex.paper_id == via_s2.paper_id == arxiv.paper_id
    assert not via_openalex.created and not via_s2.created
    assert identifiers_of(arxiv.paper_id) == {
        ("arxiv", "2504.08385", True),
        ("openalex", "W4414828500", True),
        ("s2", "54c06b1a1c13eab60eab9e8638eee03ff4d94827", True),
    }
    # preprint records without version info create no version row
    assert [(v.source, v.version_label) for v in versions_of(arxiv.paper_id)] == [("arxiv", "v2")]
    with session_scope() as session:
        found = get_paper_by_identifier(session, "doi", "10.48550/arXiv.2504.08385")
        assert found is not None and found.id == arxiv.paper_id
        assert get_paper_by_identifier(session, "doi", "10.48550/arxiv.2504.99999") is None
        assert get_paper_by_identifier(session, "doi", "not a doi") is None


# ------------------------------------------------------------------------------------------------
# Versions
# ------------------------------------------------------------------------------------------------


def test_new_arxiv_version_keeps_the_paper_and_its_publication_date() -> None:
    v1 = upsert(
        synthetic(
            "Test paper A",
            source="arxiv_feed",
            source_id="oai:arXiv.org:2610.00001v1",
            identifiers={"arxiv": "2610.00001v1"},
            version_label="v1",
            announce_type="new",
            publication_date=date(2026, 10, 1),
        )
    )
    v2_record = synthetic(
        "Test paper A",
        source="arxiv_feed",
        source_id="oai:arXiv.org:2610.00001v2",
        identifiers={"arxiv": "2610.00001v2"},
        version_label="v2",
        announce_type="replace",
        publication_date=date(2026, 10, 12),
        abstract="Synthetic revised abstract for Test paper A.",
    )
    v2 = upsert(v2_record)
    assert v2.paper_id == v1.paper_id and not v2.created
    assert v2.new_version is True
    assert v2.changed_fields == ["versions"]
    p = paper(v1.paper_id)
    assert p.publication_date == date(2026, 10, 1)  # replace entries never move it
    assert p.abstract == "Synthetic abstract for Test paper A."  # equal priority: first writer wins
    rows = {v.version_label: v for v in versions_of(p.id)}
    assert set(rows) == {"v1", "v2"}
    assert rows["v2"].source_updated_at == datetime(2026, 10, 12, tzinfo=UTC)
    assert rows["v2"].announce_type == "replace"

    again = upsert(v2_record)
    assert again.new_version is False and again.changed_fields == []
    # an older version arriving late is not "new"
    late_v1 = upsert(
        synthetic(
            "Test paper A",
            source="arxiv_api",
            source_id="http://arxiv.org/abs/2610.00001v1",
            identifiers={"arxiv": "2610.00001"},
            version_label="v1",
        )
    )
    assert late_v1.new_version is False
    with session_scope() as session:
        assert latest_versions(session, [p.id]) == {p.id: "v2"}


def test_version_inferred_from_arxiv_id_and_validated() -> None:
    inferred = upsert(synthetic("Test paper B", identifiers={"arxiv": "2610.00002v4"}))
    assert [(v.source, v.version_label) for v in versions_of(inferred.paper_id)] == [("arxiv", "v4")]
    # labels that do not fit the source create no row; version_label is never NULL
    bad = upsert(synthetic("Test paper C", identifiers={"arxiv": "2610.00003"}, version_label="published"))
    assert versions_of(bad.paper_id) == []
    s2 = upsert(
        synthetic(
            "Test paper D",
            source="s2",
            source_id="d" * 40,
            identifiers={"s2": "d" * 40},
            version_label="v2",
        )
    )
    assert versions_of(s2.paper_id) == []
    published = upsert(
        synthetic(
            "Test paper E",
            source="openalex",
            source_id="W100000000005",
            identifiers={"doi": "10.0000/test.e", "openalex": "W100000000005"},
            version_label="Published",
        )
    )
    assert [(v.source, v.version_label) for v in versions_of(published.paper_id)] == [
        ("openalex", "published")
    ]
    with pytest.raises(ValueError):
        upsert(synthetic("Test paper E2", source="crossref", source_id="10.0000/x"))  # removed in v0.3
    with session_scope() as session:
        sources = set(session.scalars(select(models.PaperVersion.source)))
        labels = list(session.scalars(select(models.PaperVersion.version_label)))
    assert sources <= {"arxiv", "openalex", "s2"}
    assert all(labels)


def test_existing_version_rows_are_completed_not_overwritten() -> None:
    first = upsert(
        synthetic(
            "Test paper F",
            identifiers={"arxiv": "2610.00006v1"},
            version_label="v1",
            license=None,
            pdf_url="https://arxiv.org/pdf/2610.00006v1",
        )
    )
    second = upsert(
        synthetic(
            "Test paper F",
            source="arxiv_feed",
            source_id="oai:arXiv.org:2610.00006v1",
            identifiers={"arxiv": "2610.00006v1"},
            version_label="v1",
            announce_type="new",
            license=CC_BY,
            pdf_url="https://example.invalid/other.pdf",
        )
    )
    assert second.paper_id == first.paper_id and second.changed_fields == ["versions"]
    (version,) = versions_of(first.paper_id)
    assert version.license == CC_BY and version.announce_type == "new"
    assert version.pdf_url == "https://arxiv.org/pdf/2610.00006v1"


# ------------------------------------------------------------------------------------------------
# Merge rules
# ------------------------------------------------------------------------------------------------


def test_field_priorities_and_provenance(load_fixture: Callable[[str], Any]) -> None:
    s2_paper = load_fixture("s2_batch.json")[0]
    work = load_fixture("openalex_work_published.json")

    created = upsert(s2_record(s2_paper))  # lowest priority for everything
    pid = created.paper_id
    p = paper(pid)
    assert p.venue == "Annual Meeting of the Association for Computational Linguistics"
    assert p.cited_by_count == 946 and p.field_provenance_json["cited_by_count"]["source"] == "s2"

    merged = upsert(openalex_record(work))
    assert merged.paper_id == pid
    p = paper(pid)
    assert p.cited_by_count == 471  # openalex > s2
    assert p.venue == "Annual Meeting of the Association for Computational Linguistics (ACL)"  # openalex > s2
    assert p.paper_type == "conference-paper"
    assert p.is_oa is True and p.oa_url == "https://www.aclweb.org/anthology/2020.acl-main.207.pdf"
    assert p.publication_date == date(2020, 1, 1) and p.year == 2020
    assert p.abstract == s2_paper["abstract"]  # never replaced by an empty value
    assert p.field_provenance_json["venue"]["source"] == "openalex"
    assert {"cited_by_count", "venue", "paper_type", "is_oa", "oa_url", "best_oa_pdf"} <= set(
        merged.changed_fields
    )
    # same names, different formatting ("Daniel S. Weld" both times) -> no rebuild
    assert "authors" not in merged.changed_fields

    # a lower-priority source never overrides
    before = db_snapshot()
    again = upsert(s2_record(s2_paper))
    assert again.changed_fields == [] and db_snapshot() == before


def test_title_and_abstract_priority_arxiv_wins(load_fixture: Callable[[str], Any]) -> None:
    s2_paper = load_fixture("s2_batch.json")[1]  # Scholar Inbox, abstract from S2
    first = upsert(s2_record(s2_paper))
    p = paper(first.paper_id)
    assert p.abstract == s2_paper["abstract"]
    arxiv = upsert(scholar_inbox_arxiv_record())
    assert arxiv.paper_id == first.paper_id
    p = paper(first.paper_id)
    assert p.abstract.startswith("Scholar Inbox is a new open-access platform")  # arXiv > s2
    assert p.abstract != s2_paper["abstract"]
    assert p.field_provenance_json["abstract"]["source"] == "arxiv_api"
    assert p.primary_category == "cs.CL" and p.categories_json == ["cs.CL", "cs.AI", "cs.IR"]
    assert "abstract" in arxiv.changed_fields and "authors" in arxiv.changed_fields
    assert [a[1] for a in authors_of(p.id)] == SCHOLAR_INBOX_AUTHORS  # "M. Iyengar" replaced
    # S2 again cannot take the abstract or authors back
    assert upsert(s2_record(s2_paper)).changed_fields == []


def test_authors_rebuilt_only_when_the_normalized_list_changes(load_fixture: Callable[[str], Any]) -> None:
    work = load_fixture("openalex_work_arxiv.json")  # "Iyengar, Madhav", "Protsenko, Vitalii", "Cao, Yong"
    created = upsert(openalex_record(work, version_label=None))
    before = authors_of(created.paper_id)
    assert [a[1] for a in before][2] == "Iyengar, Madhav"
    # the arXiv list has the same people as "First Last": no rebuild, provenance moves to arXiv
    arxiv = upsert(scholar_inbox_arxiv_record())
    assert "authors" not in arxiv.changed_fields
    assert authors_of(created.paper_id) == before
    assert paper(created.paper_id).field_provenance_json["authors"]["source"] == "arxiv_api"
    # an equal-priority arXiv source with a different list keeps the first writer's list
    changed = upsert(scholar_inbox_arxiv_record(authors=["Test Author Only"], source="arxiv_feed"))
    assert "authors" not in changed.changed_fields
    assert authors_of(created.paper_id) == before
    with session_scope() as session:
        author_rows = session.scalar(select(func.count()).select_from(models.Author))
    assert author_rows == len(before)  # no orphaned author rows


def test_publication_date_is_the_earliest_known() -> None:
    created = upsert(
        synthetic("Test paper G", identifiers={"arxiv": "2610.00007v1"}, publication_date=date(2026, 3, 2))
    )
    pid = created.paper_id
    earlier = upsert(
        synthetic(
            "Test paper G",
            source="openalex",
            source_id="W100000000007",
            identifiers={"openalex": "W100000000007", "arxiv": "2610.00007"},
            publication_date=date(2025, 12, 30),
            year=2025,
        )
    )
    assert earlier.paper_id == pid and {"publication_date", "year"} <= set(earlier.changed_fields)
    later = upsert(
        synthetic(
            "Test paper G",
            source="s2",
            source_id="b" * 40,
            identifiers={"s2": "b" * 40, "doi": "10.0000/test.g", "arxiv": "2610.00007"},
            publication_date=date(2026, 5, 1),
            year=2026,
        )
    )
    assert "publication_date" not in later.changed_fields
    p = paper(pid)
    assert p.publication_date == date(2025, 12, 30) and p.year == 2025
    assert p.field_provenance_json["publication_date"]["source"] == "openalex"


def test_year_only_records_keep_the_earliest_year() -> None:
    created = upsert(
        synthetic(
            "Test paper H",
            source="openalex",
            source_id="W100000000008",
            identifiers={"openalex": "W100000000008", "doi": "10.0000/test.h"},
            publication_date=None,
            year=2021,
        )
    )
    upsert(
        synthetic(
            "Test paper H",
            source="s2",
            source_id="h" * 40,
            identifiers={"s2": "e" * 40, "doi": "10.0000/test.h"},
            publication_date=None,
            year=2020,
        )
    )
    upsert(
        synthetic(
            "Test paper H",
            source="s2",
            source_id="f" * 40,
            identifiers={"s2": "f" * 40, "doi": "10.0000/test.h"},
            publication_date=None,
            year=2022,
        )
    )
    p = paper(created.paper_id)
    assert p.publication_date is None and p.year == 2020


def test_retraction_status_from_openalex(load_fixture: Callable[[str], Any]) -> None:
    work = load_fixture("openalex_work_retracted.json")
    record = openalex_record(work)  # is_retracted: true, no abstract
    record.status = None  # detected from the raw payload as well
    created = upsert(record)
    p = paper(created.paper_id)
    assert p.status == "retracted" and p.abstract is None
    assert p.field_provenance_json["status"]["source"] == "openalex"
    assert p.publication_date == date(1998, 2, 1)
    assert ("pmid", "9500320", True) in identifiers_of(p.id)

    # an OpenAlex record alone also marks a new paper retracted
    fresh = upsert(
        openalex_record(work, identifiers={"openalex": "W999999999999"}, title="Test paper retracted")
    )
    assert fresh.paper_id != p.id
    assert paper(fresh.paper_id).status == "retracted"


def test_retraction_is_sticky_and_stub_never_downgrades(load_fixture: Callable[[str], Any]) -> None:
    retracted = upsert(synthetic("Test paper I", identifiers={"arxiv": "2610.00009"}, status="retracted"))
    upsert(
        synthetic(
            "Test paper I", identifiers={"arxiv": "2610.00009"}, status=None, source="s2", source_id="9" * 40
        )
    )
    upsert(
        synthetic(
            "Test paper I",
            identifiers={"arxiv": "2610.00009"},
            status="stub",
            source="s2",
            source_id="8" * 40,
        )
    )
    upsert(synthetic("Test paper I", identifiers={"arxiv": "2610.00009"}, status="withdrawn"))
    assert paper(retracted.paper_id).status == "retracted"

    active = upsert(scholar_inbox_arxiv_record())
    stub = upsert(s2_record(load_fixture("s2_batch.json")[1], status="stub"))
    assert stub.paper_id == active.paper_id
    assert paper(active.paper_id).status == "active"
    assert "status" not in stub.changed_fields


def test_stub_becomes_active_only_through_arxiv_ingestion(load_fixture: Callable[[str], Any]) -> None:
    s2_paper = load_fixture("s2_batch.json")[1]
    stub = upsert(s2_record(s2_paper, status="stub"))
    assert paper(stub.paper_id).status == "stub"
    enriched = upsert(openalex_record(load_fixture("openalex_work_arxiv.json"), version_label=None))
    assert enriched.paper_id == stub.paper_id
    assert paper(stub.paper_id).status == "stub"  # enrichment does not promote graph stubs
    ingested = upsert(scholar_inbox_arxiv_record())
    assert ingested.paper_id == stub.paper_id and "status" in ingested.changed_fields
    assert paper(stub.paper_id).status == "active"


def test_withdrawn_status_applies() -> None:
    created = upsert(synthetic("Test paper J", identifiers={"arxiv": "2610.00010v1"}))
    result = upsert(synthetic("Test paper J", identifiers={"arxiv": "2610.00010v2"}, status="withdrawn"))
    assert "status" in result.changed_fields
    assert paper(created.paper_id).status == "withdrawn"


# ------------------------------------------------------------------------------------------------
# Identifiers
# ------------------------------------------------------------------------------------------------


def test_identifiers_primary_first_attached_never_reassigned() -> None:
    created = upsert(
        synthetic(
            "Test paper K",
            source="openalex",
            source_id="W100000000011",
            identifiers={"openalex": "W100000000011", "doi": "10.0000/test.k.preprint"},
        )
    )
    # a record from another source with another DOI for the same work: title fallback merges it
    merged = upsert(
        synthetic(
            "Test paper K",
            source="s2",
            source_id="10.0000/test.k.journal",
            identifiers={"doi": "10.0000/TEST.K.JOURNAL"},
            year=2026,
            version_label="published",
        )
    )
    assert merged.paper_id == created.paper_id and "identifiers" in merged.changed_fields
    assert identifiers_of(created.paper_id) == {
        ("openalex", "W100000000011", True),
        ("doi", "10.0000/test.k.preprint", True),
        ("doi", "10.0000/test.k.journal", False),
    }
    # exactly one primary per (paper, scheme)
    with session_scope() as session:
        rows = session.execute(
            select(models.PaperIdentifier.paper_id, models.PaperIdentifier.scheme, func.count())
            .where(models.PaperIdentifier.is_primary.is_(True))
            .group_by(models.PaperIdentifier.paper_id, models.PaperIdentifier.scheme)
        ).all()
    assert all(n == 1 for _, _, n in rows)


def test_unrecognized_identifiers_are_ignored() -> None:
    result = upsert(
        synthetic(
            "Test paper L",
            identifiers={
                "arxiv": "2610.00012",
                "doi": "not-a-doi",
                "s2": "CorpusId:1",
                "mag": "123",
                "pmid": "",
            },
        )
    )
    assert identifiers_of(result.paper_id) == {("arxiv", "2610.00012", True)}


def test_conflicting_identifiers_record_a_duplicate_candidate() -> None:
    preprint = upsert(synthetic("Test paper M (preprint)", identifiers={"arxiv": "2610.00013v1"}))
    journal = upsert(
        synthetic(
            "Test paper M (journal version)",
            source="s2",
            source_id="10.0000/test.m",
            identifiers={"doi": "10.0000/test.m"},
            version_label="published",
        )
    )
    assert preprint.paper_id != journal.paper_id
    # an OpenAlex record claiming both identifiers: DOI match wins, the arXiv id stays where it is
    record = synthetic(
        "Test paper M (journal version)",
        source="openalex",
        source_id="W100000000013",
        identifiers={"openalex": "W100000000013", "doi": "10.0000/test.m", "arxiv": "2610.00013"},
    )
    result = upsert(record)
    assert result.paper_id == journal.paper_id and not result.created
    assert result.duplicate_candidate_of == preprint.paper_id
    assert ("openalex", "W100000000013", True) in identifiers_of(journal.paper_id)
    assert identifiers_of(preprint.paper_id) == {("arxiv", "2610.00013", True)}
    low, high = sorted((preprint.paper_id, journal.paper_id))
    with session_scope() as session:
        (candidate,) = session.scalars(select(models.DuplicateCandidate)).all()
        assert (candidate.paper_a_id, candidate.paper_b_id) == (low, high)
        assert (candidate.reason, candidate.status, candidate.score) == ("identifier", "pending", 1.0)
        (edge,) = session.scalars(select(models.GraphEdge)).all()
        assert (edge.src_type, edge.src_id, edge.relation, edge.dst_type, edge.dst_id) == (
            "work",
            low,
            "SAME_WORK_AS",
            "work",
            high,
        )
        assert edge.verified is False and edge.provenance == "identifier"
        assert edge.evidence_json == {"matched_on": "arxiv"} and edge.confidence == 1.0
    before = db_snapshot()
    again = upsert(record)
    assert again.changed_fields == [] and again.duplicate_candidate_of == preprint.paper_id
    assert db_snapshot() == before


# ------------------------------------------------------------------------------------------------
# Title fallback
# ------------------------------------------------------------------------------------------------


def test_title_match_with_different_first_author_is_a_candidate_not_a_merge() -> None:
    existing = upsert(synthetic("Test paper N: A Study", identifiers={"arxiv": "2610.00014"}))
    record = synthetic(
        "Test paper N - a study",
        source="s2",
        source_id="10.0000/test.n",
        identifiers={"doi": "10.0000/test.n"},
        authors=["Other Person"],
    )
    result = upsert(record)
    assert result.created and result.paper_id != existing.paper_id
    assert result.duplicate_candidate_of == existing.paper_id
    with session_scope() as session:
        (candidate,) = session.scalars(select(models.DuplicateCandidate)).all()
        assert (candidate.reason, candidate.status, candidate.score) == ("title_match", "pending", 0.75)
        assert candidate.paper_a_id < candidate.paper_b_id
        (edge,) = session.scalars(select(models.GraphEdge)).all()
        assert edge.relation == "SAME_WORK_AS" and edge.provenance == "title_match" and not edge.verified
        assert edge.evidence_json == {"matched_on": "title"} and edge.confidence == 0.75
        assert edge.src_id < edge.dst_id
    before = db_snapshot()
    again = upsert(record)
    assert (
        again.paper_id == result.paper_id
        and again.changed_fields == []
        and again.duplicate_candidate_of is None
    )
    assert db_snapshot() == before


@pytest.mark.parametrize(
    ("overrides", "score"),
    [
        ({"publication_date": None, "year": 2021}, 0.75),  # same surname, years 5 apart
        ({"authors": [], "publication_date": None, "year": None}, 0.5),  # nothing to compare
        ({"authors": ["Zed Other"], "publication_date": date(2015, 1, 1), "year": 2015}, 0.5),
    ],
)
def test_title_match_requires_year_and_first_author(overrides: dict[str, Any], score: float) -> None:
    existing = upsert(synthetic("Test paper O", identifiers={"arxiv": "2610.00015"}))
    result = upsert(
        synthetic("Test paper O", source="s2", source_id="o" * 40, identifiers={"s2": "0" * 40}, **overrides)
    )
    assert result.created and result.duplicate_candidate_of == existing.paper_id
    with session_scope() as session:
        (candidate,) = session.scalars(select(models.DuplicateCandidate)).all()
        assert candidate.score == score


def test_title_match_merges_within_one_year() -> None:
    existing = upsert(
        synthetic("Test paper P", identifiers={"arxiv": "2610.00016"}, publication_date=date(2026, 1, 5))
    )
    merged = upsert(
        synthetic(
            "TEST PAPER P",
            source="s2",
            source_id="10.0000/test.p",
            identifiers={"doi": "10.0000/test.p"},
            authors=["Test, Ada", "Example, Grace"],
            publication_date=None,
            year=2027,
        )
    )
    assert merged.paper_id == existing.paper_id and not merged.created
    assert ("doi", "10.0000/test.p", True) in identifiers_of(existing.paper_id)
    assert count(models.DuplicateCandidate) == 0


def test_title_fallback_merges_only_an_unambiguous_match() -> None:
    a = upsert(synthetic("Test paper Q", identifiers={"arxiv": "2610.00017"}))
    # same title, year and first author: the SPEC rule merges (identifiers do not take part)
    b = upsert(synthetic("Test paper Q", identifiers={"arxiv": "2610.00018"}))
    assert b.paper_id == a.paper_id
    assert {v for _, v, _ in identifiers_of(a.paper_id)} == {"2610.00017", "2610.00018"}
    # another first author: a new paper and a candidate
    c = upsert(synthetic("Test paper Q", identifiers={"arxiv": "2610.00019"}, authors=["Other Writer"]))
    assert c.created and c.duplicate_candidate_of == a.paper_id
    # two equal titles, exactly one of them mergeable: merge into that one
    d = upsert(synthetic("Test paper Q", identifiers={"arxiv": "2610.00020"}, authors=["Ada Test"]))
    assert d.paper_id == a.paper_id and d.duplicate_candidate_of is None


def test_ambiguous_title_match_creates_a_new_paper_with_candidates() -> None:
    e_existing = upsert(
        synthetic("Test paper R", identifiers={"arxiv": "2610.00021"}, authors=["Same Person"])
    )
    with session_scope(write=True) as session:  # a second "Test paper R" by the same author (synthetic)
        twin = models.Paper(
            canonical_title="Test paper R",
            title_norm="test paper r",
            first_author_norm="person",
            year=2026,
            ingested_via="arxiv_api",
        )
        session.add(twin)
        session.flush()
        twin_id = twin.id
    f = upsert(
        synthetic(
            "Test paper R",
            source="s2",
            source_id="r" * 40,
            identifiers={"s2": "1" * 40},
            authors=["Same Person"],
        )
    )
    assert f.created and f.paper_id not in (e_existing.paper_id, twin_id)
    with session_scope() as session:
        pairs = {
            (c.paper_a_id, c.paper_b_id, c.score)
            for c in session.scalars(
                select(models.DuplicateCandidate).where(
                    (models.DuplicateCandidate.paper_a_id == f.paper_id)
                    | (models.DuplicateCandidate.paper_b_id == f.paper_id)
                )
            )
        }
    assert pairs == {
        (min(f.paper_id, e_existing.paper_id), max(f.paper_id, e_existing.paper_id), 1.0),
        (min(f.paper_id, twin_id), max(f.paper_id, twin_id), 1.0),
    }


def test_identifier_less_records_are_idempotent_via_source_records() -> None:
    record = synthetic(
        "Test paper S",
        source="s2",
        source_id="test-s2-no-ids",
        identifiers={},
        authors=[],
        year=None,
        publication_date=None,
    )
    first = upsert(record)
    before = db_snapshot()
    second = upsert(record)
    assert second.paper_id == first.paper_id and not second.created and second.changed_fields == []
    assert db_snapshot() == before
    # a changed payload of the same source record appends a source_records row, same paper
    third = upsert(
        synthetic(
            "Test paper S",
            source="s2",
            source_id="test-s2-no-ids",
            identifiers={},
            authors=[],
            year=None,
            publication_date=None,
            raw={"v": 2},
        )
    )
    assert third.paper_id == first.paper_id
    assert count(models.SourceRecord) == 2


def test_source_records_append_only_on_new_payload_hash(load_fixture: Callable[[str], Any]) -> None:
    work = load_fixture("openalex_work_arxiv.json")
    upsert(openalex_record(work, version_label=None))
    upsert(openalex_record(work, version_label=None))
    assert count(models.SourceRecord) == 1
    changed = dict(work, cited_by_count=1)
    upsert(openalex_record(changed, version_label=None))
    with session_scope() as session:
        rows = session.scalars(select(models.SourceRecord).order_by(models.SourceRecord.id)).all()
        assert [r.payload_hash for r in rows] == [payload_hash(work), payload_hash(changed)]
        assert {r.source for r in rows} == {"openalex"} and {r.source_id for r in rows} == {work["id"]}
        assert rows[0].payload_json["id"] == work["id"]
        assert rows[0].paper_id == rows[1].paper_id is not None
