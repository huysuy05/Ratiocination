"""ORM tables (SPEC v0.3 §4) and the ``UTCDateTime`` column type.

Conventions (SPEC §4, CONVENTIONS "Database rules"):

* integer primary key ``id`` everywhere; every timestamp column is :class:`UTCDateTime`;
* JSON columns are ``JSON(none_as_null=True)`` with callable defaults (``dict``/``list``) and are
  treated as immutable values (copy, modify, reassign - or ``flag_modified``);
* where a unique key would include a nullable owner/project column, ``0`` means "public/none";
* FKs marked "(cascade)" in the SPEC use ``ondelete="CASCADE"``; relationships use
  ``passive_deletes=True`` so the database performs the cascade;
* ``users.id = 1`` (:data:`LOCAL_USER_ID`) always exists: the only user in ``RADAR_AUTH=local`` mode;
  in accounts mode the first account claims it (SPEC §5.6).

Indexes beyond the SPEC's keys exist only to keep foreign-key cascades and common lookups fast.
"""

from __future__ import annotations

import datetime as dt
from datetime import UTC
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

LOCAL_USER_ID = 1
"""User 1: the local user, claimed by the first account in accounts mode (SPEC §5.6)."""


def utcnow() -> dt.datetime:
    """Current time as an aware UTC datetime."""
    return dt.datetime.now(UTC)


class UTCDateTime(TypeDecorator[dt.datetime]):
    """Aware-UTC datetime column (SPEC §4).

    Binding a naive datetime raises ``ValueError``; aware values are converted to UTC. Values read
    back are always aware UTC (SQLite stores them without an offset, so ``tzinfo=UTC`` is attached).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> dt.datetime | None:
        if value is None:
            return None
        if not isinstance(value, dt.datetime):
            raise TypeError("UTCDateTime columns accept datetime objects only")
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("naive datetime given to a UTCDateTime column; use datetime.now(UTC)")
        value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> dt.datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def JSONColumn() -> JSON:
    """The JSON column type used everywhere (``none_as_null=True``)."""
    return JSON(none_as_null=True)


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created() -> Any:
    return mapped_column(UTCDateTime(), nullable=False, default=utcnow)


def _updated() -> Any:
    return mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow)


# ------------------------------------------------------------------------------------------------
# Users and projects
# ------------------------------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)  # lowercase
    email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)  # Argon2id encoded hash
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = _created()
    last_login_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # {auto_expand: bool, timezone?: str}
    preferences_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)

    projects: Mapped[list[Project]] = relationship(back_populates="owner", passive_deletes=True)
    identities: Mapped[list[UserIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class UserIdentity(Base):
    """An OAuth identity (Google ``sub`` / GitHub numeric id) linked to a user (SPEC §5.6)."""

    __tablename__ = "user_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(16), nullable=False)  # 'google'|'github'
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[dt.datetime] = _created()
    last_login_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    user: Mapped[User] = relationship(back_populates="identities")


class AuthSession(Base):
    """A sign-in session; only ``sha256(token)`` is stored (SPEC §5.2)."""

    __tablename__ = "auth_sessions"
    __table_args__ = (UniqueConstraint("token_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    last_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)


class AuthToken(Base):
    """Single-use email verification / password reset token, stored hashed (SPEC §5.7)."""

    __tablename__ = "auth_tokens"
    __table_args__ = (UniqueConstraint("token_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)  # 'verify_email'|'reset_password'
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[dt.datetime] = _created()
    expires_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class OAuthState(Base):
    """Pending OAuth sign-in: state (hashed), PKCE verifier, nonce, return path (SPEC §5.5)."""

    __tablename__ = "oauth_states"
    __table_args__ = (UniqueConstraint("state_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    code_verifier: Mapped[str] = mapped_column(String(128), nullable=False)
    nonce: Mapped[str | None] = mapped_column(String(128), nullable=True)
    next_path: Mapped[str] = mapped_column(Text, nullable=False, default="/")
    link_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # set for "connect account"
    created_at: Mapped[dt.datetime] = _created()


class AppSetting(Base):
    """Global settings that are not per user (``scheduler_enabled``)."""

    __tablename__ = "app_settings"
    __table_args__ = (UniqueConstraint("key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSONColumn(), nullable=True)
    updated_at: Mapped[dt.datetime] = _updated()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default=LOCAL_USER_ID, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    research_question: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Keywords that upweight papers mentioning them (SPEC §10): ≤ 12 phrases, private like the question
    keywords_json: Mapped[list[str]] = mapped_column(JSONColumn(), nullable=False, default=list)
    # ProjectSettings (docs/API.md): arxiv_categories, time_window_days, slate_size
    settings_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()

    owner: Mapped[User] = relationship(back_populates="projects")
    seeds: Mapped[list[ProjectSeed]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    interactions: Mapped[list[Interaction]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    paper_states: Mapped[list[ProjectPaperState]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    slates: Mapped[list[RecommendationSlate]] = relationship(
        back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class ProjectSeed(Base):
    __tablename__ = "project_seeds"
    __table_args__ = (UniqueConstraint("project_id", "paper_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id"), nullable=False, index=True)
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    added_via: Mapped[str] = mapped_column(String(16), nullable=False)  # 'doi'|'arxiv'|'openalex'|'search'
    created_at: Mapped[dt.datetime] = _created()

    project: Mapped[Project] = relationship(back_populates="seeds")
    paper: Mapped[Paper] = relationship()


class Concept(Base):
    __tablename__ = "concepts"
    __table_args__ = (UniqueConstraint("ontology_source", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_label: Mapped[str] = mapped_column(Text, nullable=False)
    definition: Mapped[str | None] = mapped_column(Text, nullable=True)
    ontology_source: Mapped[str] = mapped_column(String(32), nullable=False)  # 'openalex_topic'
    # OpenAlex 'T\d+'
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    extra_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)


# ------------------------------------------------------------------------------------------------
# Papers
# ------------------------------------------------------------------------------------------------


class Paper(Base):
    __tablename__ = "papers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_title: Mapped[str] = mapped_column(Text, nullable=False)
    title_norm: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    first_author_norm: Mapped[str | None] = mapped_column(String(255), nullable=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paper_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 'active'|'stub'|'retracted'|'withdrawn'
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    primary_category: Mapped[str | None] = mapped_column(String(64), nullable=True)
    categories_json: Mapped[list[str]] = mapped_column(JSONColumn(), nullable=False, default=list)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_oa: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    oa_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    best_oa_pdf: Mapped[str | None] = mapped_column(Text, nullable=True)
    cited_by_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_seen_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    ingested_via: Mapped[str] = mapped_column(String(32), nullable=False)
    openalex_checked_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    # Disruption (CD) index from OpenAlex counts (SPEC §13); null until computed or when undefined
    disruption_cd: Mapped[float | None] = mapped_column(Float, nullable=True)
    disruption_ni: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disruption_nj: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disruption_nk: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disruption_refs: Mapped[int | None] = mapped_column(Integer, nullable=True)  # references used (<=100)
    disruption_refs_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    disruption_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    # {field: {source, observed_at}} only
    field_provenance_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()

    identifiers: Mapped[list[PaperIdentifier]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", passive_deletes=True
    )
    versions: Mapped[list[PaperVersion]] = relationship(
        back_populates="paper", cascade="all, delete-orphan", passive_deletes=True
    )
    paper_authors: Mapped[list[PaperAuthor]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PaperAuthor.position",
    )


class PaperIdentifier(Base):
    __tablename__ = "paper_identifiers"
    __table_args__ = (UniqueConstraint("scheme", "value"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheme: Mapped[str] = mapped_column(String(16), nullable=False)  # 'doi'|'arxiv'|'openalex'|'s2'|'pmid'
    value: Mapped[str] = mapped_column(String(255), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[dt.datetime] = _created()

    paper: Mapped[Paper] = relationship(back_populates="identifiers")


class PaperVersion(Base):
    __tablename__ = "paper_versions"
    __table_args__ = (UniqueConstraint("paper_id", "source", "version_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    version_label: Mapped[str] = mapped_column(String(32), nullable=False)  # 'v1','v2',...,'published'
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # 'arxiv'|'openalex'|'s2'
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    landing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    license: Mapped[str | None] = mapped_column(Text, nullable=True)
    announce_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source_updated_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    observed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)

    paper: Mapped[Paper] = relationship(back_populates="versions")


class Author(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    name_norm: Mapped[str] = mapped_column(String(255), nullable=False, index=True)


class PaperAuthor(Base):
    __tablename__ = "paper_authors"
    __table_args__ = (UniqueConstraint("paper_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_name: Mapped[str] = mapped_column(Text, nullable=False)
    # OpenAlex author of this position ('A\d+'), filled by enrichment when known
    openalex_author_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    paper: Mapped[Paper] = relationship(back_populates="paper_authors")
    author: Mapped[Author] = relationship()


class Scholar(Base):
    """An OpenAlex author (SPEC §12); linked to works by AUTHORED edges."""

    __tablename__ = "scholars"
    __table_args__ = (UniqueConstraint("openalex_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    openalex_id: Mapped[str] = mapped_column(String(32), nullable=False)  # 'A\d+'
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    orcid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    works_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cited_by_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    h_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_known_institution: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    analyzed_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


# ------------------------------------------------------------------------------------------------
# Private documents
# ------------------------------------------------------------------------------------------------


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("project_id", "checksum"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, default=LOCAL_USER_ID, index=True
    )
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)  # relative to the data dir
    # 'uploaded'|'processing'|'ready'|'failed'
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="uploaded")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    n_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()

    project: Mapped[Project] = relationship(back_populates="documents")
    sections: Mapped[list[DocumentSection]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentSection.ordinal",
    )
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentChunk.chunk_index",
    )


class DocumentSection(Base):
    __tablename__ = "document_sections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extraction_version: Mapped[str] = mapped_column(String(32), nullable=False)

    document: Mapped[Document] = relationship(back_populates="sections")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="section", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    section_id: Mapped[int] = mapped_column(
        ForeignKey("document_sections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    document: Mapped[Document] = relationship(back_populates="chunks")
    section: Mapped[DocumentSection] = relationship(back_populates="chunks")


# ------------------------------------------------------------------------------------------------
# Embeddings and graph
# ------------------------------------------------------------------------------------------------


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "model_id"),
        Index("ix_embeddings_model_id_entity_type", "model_id", "entity_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 'paper'|'chunk'|'project'
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # float32 LE, L2-normalized
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = _created()


class GraphEdge(Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "src_type",
            "src_id",
            "relation",
            "dst_type",
            "dst_id",
            "provenance",
            "owner_id",
            "project_id",
            name="uq_graph_edges_identity",
        ),
        Index("ix_graph_edges_dst", "dst_type", "dst_id", "relation"),
        Index("ix_graph_edges_project_id", "project_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 'work'|'concept'|'scholar'|'project'|'document'|'chunk'
    src_type: Mapped[str] = mapped_column(String(16), nullable=False)
    src_id: Mapped[int] = mapped_column(Integer, nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    dst_type: Mapped[str] = mapped_column(String(16), nullable=False)
    dst_id: Mapped[int] = mapped_column(Integer, nullable=False)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_json: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn(), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 = public
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 = none
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()


class PaperExpansion(Base):
    __tablename__ = "paper_expansions"
    __table_args__ = (UniqueConstraint("paper_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # 'none'|'queued'|'running'|'done'|'failed'
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_expanded_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)


# ------------------------------------------------------------------------------------------------
# Feedback, states, reads, slates, summaries
# ------------------------------------------------------------------------------------------------


class Interaction(Base):
    __tablename__ = "interactions"
    __table_args__ = (
        Index("ix_interactions_project_id_paper_id", "project_id", "paper_id"),
        Index("ix_interactions_project_id_timestamp", "project_id", "timestamp"),
        Index("ix_interactions_owner_id_timestamp", "owner_id", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=LOCAL_USER_ID)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)  # 'relevant'|'not_relevant'|'save'|'open'
    timestamp: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    slate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # {source?} - never evidence or text
    context_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    undone_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)

    project: Mapped[Project] = relationship(back_populates="interactions")


class ProjectPaperState(Base):
    """Current per-project state of a paper (SPEC §11).

    ``state``/``last_interaction_id`` are nullable so a row may persist after every interaction was
    undone; readers treat ``state IS NULL`` as "no state".
    """

    __tablename__ = "project_paper_states"
    __table_args__ = (UniqueConstraint("project_id", "paper_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 'relevant'|'not_relevant'|'saved'
    updated_at: Mapped[dt.datetime] = _updated()
    last_interaction_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    project: Mapped[Project] = relationship(back_populates="paper_states")


class PaperRead(Base):
    """A user's "read" mark on a paper, independent of projects (SPEC §11)."""

    __tablename__ = "paper_reads"
    __table_args__ = (UniqueConstraint("user_id", "paper_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    paper_id: Mapped[int] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    read_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class RecommendationSlate(Base):
    __tablename__ = "recommendation_slates"
    __table_args__ = (
        Index("ix_recommendation_slates_project_id_generated_at", "project_id", "generated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, default=LOCAL_USER_ID)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    generated_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # 'daily'|'more'
    ranker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    candidate_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    pool_size: Mapped[int] = mapped_column(Integer, nullable=False)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="slates")
    items: Mapped[list[RecommendationItem]] = relationship(
        back_populates="slate",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RecommendationItem.rank",
    )


class RecommendationItem(Base):
    __tablename__ = "recommendation_items"
    __table_args__ = (UniqueConstraint("slate_id", "paper_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slate_id: Mapped[int] = mapped_column(
        ForeignKey("recommendation_slates.id", ondelete="CASCADE"), nullable=False
    )
    paper_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    feature_scores_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    explanation_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn(), nullable=False, default=list)

    slate: Mapped[RecommendationSlate] = relationship(back_populates="items")


class FindingsSummary(Base):
    """An AI-written summary of the findings of a user's liked papers (SPEC §14.4)."""

    __tablename__ = "findings_summaries"
    __table_args__ = (Index("ix_findings_summaries_user_id_created_at", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 = all projects
    days: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_ids_json: Mapped[list[int]] = mapped_column(JSONColumn(), nullable=False, default=list)
    # {themes: [{title, points: [{text, cites: [paper_id]}]}], dropped_points}
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = _created()


# ------------------------------------------------------------------------------------------------
# Jobs, ingestion bookkeeping, cost ledger
# ------------------------------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index(
            "uq_jobs_active",
            "dedupe_key",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # 'queued'|'running'|'succeeded'|'failed'
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # null = system
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    worker_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[dt.datetime] = _created()
    started_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class IngestionWatermark(Base):
    __tablename__ = "ingestion_watermarks"
    __table_args__ = (UniqueConstraint("source", "query_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    query_key: Mapped[str] = mapped_column(String(255), nullable=False)
    watermark: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[dt.datetime] = _updated()


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("source", "source_id", "payload_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(255), nullable=False)
    paper_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)


class DuplicateCandidate(Base):
    __tablename__ = "duplicate_candidates"
    __table_args__ = (
        UniqueConstraint("paper_a_id", "paper_b_id"),
        CheckConstraint("paper_a_id < paper_b_id", name="ordered_pair"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    paper_a_id: Mapped[int] = mapped_column(Integer, nullable=False)
    paper_b_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    # 'pending'|'merged'|'rejected'
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created_at: Mapped[dt.datetime] = _created()


class CostLedger(Base):
    __tablename__ = "cost_ledger"
    __table_args__ = (Index("ix_cost_ledger_provider_timestamp", "provider", "timestamp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow)
    # 'arxiv'|'openalex'|'semantic_scholar'|'google'|'github'|'anthropic'
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # 'feed'|'query'|'lookup'|'list'|'search'|'content'|'batch'|'auth'|'generate'
    call_type: Mapped[str] = mapped_column(String(16), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)  # path only, never the query string
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # OpenAlex: {limit_usd, remaining_usd, remaining_requests, reset_seconds};
    # Anthropic: {model, input_tokens, output_tokens}
    meta_json: Mapped[dict[str, Any]] = mapped_column(JSONColumn(), nullable=False, default=dict)


__all__ = [
    "LOCAL_USER_ID",
    "AppSetting",
    "AuthSession",
    "AuthToken",
    "Author",
    "Base",
    "Concept",
    "CostLedger",
    "Document",
    "DocumentChunk",
    "DocumentSection",
    "DuplicateCandidate",
    "Embedding",
    "FindingsSummary",
    "GraphEdge",
    "IngestionWatermark",
    "Interaction",
    "Job",
    "OAuthState",
    "Paper",
    "PaperAuthor",
    "PaperExpansion",
    "PaperIdentifier",
    "PaperRead",
    "PaperVersion",
    "Project",
    "ProjectPaperState",
    "ProjectSeed",
    "RecommendationItem",
    "RecommendationSlate",
    "Scholar",
    "SourceRecord",
    "UTCDateTime",
    "User",
    "UserIdentity",
    "utcnow",
]
