import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from science_buddy.domain.enums import (
    AssetSource,
    ClaimStatus,
    EvidenceDepth,
    EvidenceRelation,
    JobStatus,
    ParseStatus,
)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    default_language: Mapped[str] = mapped_column(String(10), default="zh-CN", nullable=False)
    retrieval_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    trash_retention_days: Mapped[int] = mapped_column(Integer, default=30, nullable=False)


class WorkbenchNote(TimestampMixin, Base):
    __tablename__ = "workbench_notes"
    __table_args__ = (
        UniqueConstraint("project_id", "entry_date", name="uq_workbench_note_project_date"),
        Index("ix_workbench_notes_project_updated", "project_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delete_reason: Mapped[str | None] = mapped_column(String(500))


class WorkbenchTask(TimestampMixin, Base):
    __tablename__ = "workbench_tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'done')",
            name="ck_workbench_task_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_workbench_task_priority",
        ),
        Index("ix_workbench_tasks_project_status", "project_id", "status"),
        Index("ix_workbench_tasks_project_date", "project_id", "work_date"),
        Index(
            "uq_workbench_task_source",
            "project_id",
            "source_kind",
            "source_id",
            "source_key",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    work_date: Mapped[date | None] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="todo", nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="medium", nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_kind: Mapped[str | None] = mapped_column(String(32))
    source_id: Mapped[uuid.UUID | None] = mapped_column()
    source_key: Mapped[str | None] = mapped_column(String(64))
    source_section: Mapped[str | None] = mapped_column(String(500))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delete_reason: Mapped[str | None] = mapped_column(String(500))


class WorkbenchAttachment(TimestampMixin, Base):
    __tablename__ = "workbench_attachments"
    __table_args__ = (
        UniqueConstraint("note_id", "content_hash", name="uq_workbench_attachment_hash"),
        Index("ix_workbench_attachments_project_note", "project_id", "note_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    note_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workbench_notes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    ocr_status: Mapped[str] = mapped_column(String(24), default="skipped", nullable=False)
    ocr_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ocr_error: Mapped[str | None] = mapped_column(Text)
    attachment_kind: Mapped[str] = mapped_column(
        String(32), default="uploaded_image", nullable=False
    )
    generator: Mapped[str | None] = mapped_column(String(128))
    source_table_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    source_table_markdown: Mapped[str | None] = mapped_column(Text)
    render_spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    render_revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delete_reason: Mapped[str | None] = mapped_column(String(500))


class ModelConfiguration(TimestampMixin, Base):
    __tablename__ = "model_configurations"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_model_configuration_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(256), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(2048))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)


class WebSearchConfiguration(TimestampMixin, Base):
    __tablename__ = "web_search_configurations"
    __table_args__ = (CheckConstraint("id = 1", name="ck_web_search_configuration_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider: Mapped[str] = mapped_column(String(32), default="tavily", nullable=False)
    base_url: Mapped[str] = mapped_column(
        String(2048), default="https://api.tavily.com", nullable=False
    )
    api_key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    search_depth: Mapped[str] = mapped_column(String(16), default="advanced", nullable=False)


class CacheEntry(TimestampMixin, Base):
    __tablename__ = "cache_entries"
    __table_args__ = (
        UniqueConstraint("namespace", "cache_key", name="uq_cache_namespace_key"),
        Index("ix_cache_entries_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphNode(TimestampMixin, Base):
    __tablename__ = "graph_nodes"
    __table_args__ = (
        UniqueConstraint("project_id", "scope_key", "node_key", name="uq_graph_node_key"),
        Index("ix_graph_nodes_type", "project_id", "scope_key", "node_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(128), default="project", nullable=False)
    node_key: Mapped[str] = mapped_column(String(256), nullable=False)
    node_type: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class GraphEdge(TimestampMixin, Base):
    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "scope_key",
            "source_key",
            "relation",
            "target_key",
            "evidence_key",
            name="uq_graph_edge",
        ),
        Index("ix_graph_edges_source", "project_id", "scope_key", "source_key", "relation"),
        Index("ix_graph_edges_target", "project_id", "scope_key", "target_key", "relation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(128), default="project", nullable=False)
    source_key: Mapped[str] = mapped_column(String(256), nullable=False)
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    target_key: Mapped[str] = mapped_column(String(256), nullable=False)
    evidence_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL"), nullable=True
    )
    evidence_key: Mapped[str] = mapped_column(String(36), default="", nullable=False)
    provenance: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)


class GraphEntityMention(TimestampMixin, Base):
    __tablename__ = "graph_entity_mentions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "scope_key",
            "node_key",
            "chunk_id",
            "char_start",
            "char_end",
            name="uq_graph_entity_mention_span",
        ),
        Index("ix_graph_mentions_node", "project_id", "scope_key", "node_key"),
        Index("ix_graph_mentions_chunk", "chunk_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    node_key: Mapped[str] = mapped_column(String(256), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False
    )
    mention_text: Mapped[str] = mapped_column(Text, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor: Mapped[str] = mapped_column(String(128), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)


class GraphCommunity(TimestampMixin, Base):
    __tablename__ = "graph_communities"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "scope_key",
            "level",
            "community_key",
            name="uq_graph_community_key",
        ),
        Index("ix_graph_communities_scope", "project_id", "scope_key", "level"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    community_key: Mapped[str] = mapped_column(String(128), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_community_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("graph_communities.id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class GraphCommunityMember(TimestampMixin, Base):
    __tablename__ = "graph_community_members"
    __table_args__ = (
        UniqueConstraint("community_id", "node_key", name="uq_graph_community_member"),
        Index("ix_graph_community_members_node", "node_key"),
    )

    community_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("graph_communities.id", ondelete="CASCADE"), primary_key=True
    )
    node_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), default="member", nullable=False)
    centrality: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)


class GraphCommunityReport(TimestampMixin, Base):
    __tablename__ = "graph_community_reports"
    __table_args__ = (
        UniqueConstraint("community_id", "report_version", name="uq_graph_report_version"),
        Index("ix_graph_reports_community", "community_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    community_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("graph_communities.id", ondelete="CASCADE"), nullable=False
    )
    report_version: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    report_text: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_bundle: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    generated_by: Mapped[str] = mapped_column(String(128), nullable=False)


class GraphPathAudit(TimestampMixin, Base):
    __tablename__ = "graph_path_audits"
    __table_args__ = (
        Index("ix_graph_path_audits_query", "project_id", "scope_key", "query_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_node_key: Mapped[str] = mapped_column(String(256), nullable=False)
    target_node_key: Mapped[str] = mapped_column(String(256), nullable=False)
    path_nodes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    path_edges: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    path_score: Mapped[float] = mapped_column(Float, nullable=False)
    provenance_bundle: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class ProjectPaper(TimestampMixin, Base):
    __tablename__ = "project_papers"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    tags_manually_curated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class LiteratureArchive(TimestampMixin, Base):
    __tablename__ = "literature_archives"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "normalized_name",
            name="uq_literature_archive_project_name",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    origin: Mapped[str] = mapped_column(String(24), default="manual", nullable=False)


class ArchivePaper(TimestampMixin, Base):
    __tablename__ = "archive_papers"
    __table_args__ = (Index("ix_archive_papers_paper", "paper_id"),)

    archive_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("literature_archives.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )


class Tag(TimestampMixin, Base):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("project_id", "normalized_name", name="uq_tag_project_name"),
        Index("ix_tags_project_kind", "project_id", "kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)


class PaperTag(TimestampMixin, Base):
    __tablename__ = "paper_tags"
    __table_args__ = (
        Index("ix_paper_tags_project_paper", "project_id", "paper_id"),
        Index("ix_paper_tags_project_tag", "project_id", "tag_id"),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    origin: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)


class RagCollection(TimestampMixin, Base):
    __tablename__ = "rag_collections"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_rag_collection_project_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vector_status: Mapped[str] = mapped_column(String(32), default="not_requested", nullable=False)
    graph_status: Mapped[str] = mapped_column(String(32), default="not_requested", nullable=False)
    vector_job_id: Mapped[str | None] = mapped_column(String(128))


class CollectionPaper(TimestampMixin, Base):
    __tablename__ = "collection_papers"
    __table_args__ = (Index("ix_collection_papers_paper", "paper_id"),)

    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rag_collections.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )


class BrainstormSession(TimestampMixin, Base):
    __tablename__ = "brainstorm_sessions"
    __table_args__ = (
        UniqueConstraint("project_id", "session_number", name="uq_brainstorm_session_number"),
        Index("ix_brainstorm_sessions_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rag_collections.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    session_number: Mapped[int] = mapped_column(Integer, nullable=False)
    confirmation_round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    allow_pubmed_search: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    model_processing_allowed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    workflow: Mapped[str] = mapped_column(String(24), default="classic", nullable=False)
    phase: Mapped[str] = mapped_column(String(32), default="conversation", nullable=False)
    allow_web_search: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    plan_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    model_depth: Mapped[str] = mapped_column(String(16), default="max", nullable=False)
    max_context_tokens: Mapped[int] = mapped_column(
        Integer, default=1000000, nullable=False
    )
    agent_background: Mapped[str] = mapped_column(Text, default="", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delete_reason: Mapped[str | None] = mapped_column(String(500))


class BrainstormSessionMemory(TimestampMixin, Base):
    __tablename__ = "brainstorm_session_memories"
    __table_args__ = (
        UniqueConstraint("session_id", name="uq_brainstorm_session_memory_session"),
        Index("ix_brainstorm_session_memories_project", "project_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"), nullable=False
    )
    source_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brainstorm_messages.id", ondelete="CASCADE"), nullable=False
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    summary_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    summary_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ProjectFact(TimestampMixin, Base):
    __tablename__ = "project_facts"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_project_fact_confidence"),
        CheckConstraint("importance >= 0 AND importance <= 1", name="ck_project_fact_importance"),
        CheckConstraint(
            "status IN ('active', 'superseded', 'retracted')",
            name="ck_project_fact_status",
        ),
        UniqueConstraint(
            "project_id",
            "category",
            "statement_hash",
            "source_type",
            "source_id",
            name="uq_project_fact_source_statement",
        ),
        Index("ix_project_facts_project_status", "project_id", "status", "importance"),
        Index("ix_project_facts_session", "source_session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    statement_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(48), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE")
    )
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ProjectFactRelation(TimestampMixin, Base):
    __tablename__ = "project_fact_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_fact_id",
            "relation_type",
            "target_fact_id",
            name="uq_project_fact_relation",
        ),
        Index("ix_project_fact_relation_source", "source_fact_id", "relation_type"),
        Index("ix_project_fact_relation_target", "target_fact_id", "relation_type"),
        CheckConstraint(
            "relation_type IN ('conflicts_with', 'supersedes', 'synonym_of')",
            name="ck_project_fact_relation_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_fact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_facts.id", ondelete="CASCADE"), nullable=False
    )
    target_fact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("project_facts.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class MemoryDerivedIndex(TimestampMixin, Base):
    """Replaceable semantic index derived from durable memory entities."""

    __tablename__ = "memory_derived_indexes"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "model_name",
            name="uq_memory_derived_entity_model",
        ),
        Index("ix_memory_derived_project_type", "project_id", "entity_type"),
        CheckConstraint(
            "entity_type IN "
            "('project_fact', 'research_step', 'verified_claim', 'failed_route')",
            name="ck_memory_derived_entity_type",
        ),
        CheckConstraint("length(vector) > 0", name="ck_memory_derived_vector_not_empty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(24), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    model_name: Mapped[str] = mapped_column(String(512), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class BrainstormMessage(TimestampMixin, Base):
    __tablename__ = "brainstorm_messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence_number", name="uq_brainstorm_message_seq"),
        Index("ix_brainstorm_messages_session", "session_id", "sequence_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_name: Mapped[str | None] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class BrainstormVersion(TimestampMixin, Base):
    __tablename__ = "brainstorm_versions"
    __table_args__ = (
        UniqueConstraint("session_id", "version_number", name="uq_brainstorm_version_number"),
        Index("ix_brainstorm_versions_session", "session_id", "version_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"), nullable=False
    )
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brainstorm_versions.id", ondelete="RESTRICT")
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(512))
    source_media_type: Mapped[str | None] = mapped_column(String(128))
    change_summary: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )


class BrainstormAgentRun(TimestampMixin, Base):
    __tablename__ = "brainstorm_agent_runs"
    __table_args__ = (
        Index("ix_brainstorm_agent_runs_session_turn", "session_id", "turn_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"), nullable=False
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class UsageEvent(TimestampMixin, Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('model', 'retrieval')",
            name="ck_usage_event_type",
        ),
        Index("ix_usage_events_project_created", "project_id", "created_at"),
        Index("ix_usage_events_session_turn", "session_id", "turn_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brainstorm_messages.id", ondelete="SET NULL")
    )
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE")
    )
    turn_number: Mapped[int | None] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(256))
    model_depth: Mapped[str | None] = mapped_column(String(16))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    token_count_estimated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    cost_source: Mapped[str] = mapped_column(String(24), default="unavailable", nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    cache_level: Mapped[str | None] = mapped_column(String(16))


class BrainstormLiterature(TimestampMixin, Base):
    __tablename__ = "brainstorm_literature"
    __table_args__ = (
        UniqueConstraint("session_id", "paper_id", name="uq_brainstorm_session_paper"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    tags_applied: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class JournalMetric(TimestampMixin, Base):
    __tablename__ = "journal_metrics"
    __table_args__ = (
        UniqueConstraint("openalex_source_id", name="uq_journal_metric_openalex_source"),
        Index("ix_journal_metrics_issn_l", "issn_l"),
        CheckConstraint(
            "percentile IS NULL OR (percentile >= 0 AND percentile <= 1)",
            name="ck_journal_metric_percentile",
        ),
        CheckConstraint(
            "importance_score >= 0 AND importance_score <= 1",
            name="ck_journal_metric_importance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    openalex_source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    issn_l: Mapped[str | None] = mapped_column(String(16))
    issn: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_source: Mapped[str] = mapped_column(String(32), nullable=False)
    two_year_mean_citedness: Mapped[float | None] = mapped_column(Float)
    h_index: Mapped[int | None] = mapped_column(Integer)
    i10_index: Mapped[int | None] = mapped_column(Integer)
    open_quartile: Mapped[str | None] = mapped_column(String(16))
    percentile: Mapped[float | None] = mapped_column(Float)
    comparison_count: Mapped[int | None] = mapped_column(Integer)
    quartile_basis: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    importance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    metric_updated_date: Mapped[str | None] = mapped_column(String(64))
    metric_note: Mapped[str] = mapped_column(Text, nullable=False)


class Paper(TimestampMixin, Base):
    __tablename__ = "papers"
    __table_args__ = (
        UniqueConstraint("pmid", name="uq_papers_pmid"),
        UniqueConstraint("pmcid", name="uq_papers_pmcid"),
        UniqueConstraint("doi_normalized", name="uq_papers_doi_normalized"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    pmid: Mapped[str | None] = mapped_column(String(32))
    pmcid: Mapped[str | None] = mapped_column(String(32))
    doi: Mapped[str | None] = mapped_column(String(512))
    doi_normalized: Mapped[str | None] = mapped_column(String(512))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    abstract_source: Mapped[str | None] = mapped_column(String(64))
    journal: Mapped[str | None] = mapped_column(String(512))
    publication_year: Mapped[int | None] = mapped_column(Integer)
    authors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    publication_types: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    is_open_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    open_access_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    open_access_url: Mapped[str | None] = mapped_column(Text)
    is_retracted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retraction_status: Mapped[str] = mapped_column(
        String(32), default="unknown", nullable=False
    )
    citation_count: Mapped[int | None] = mapped_column(Integer)
    influential_citation_count: Mapped[int | None] = mapped_column(Integer)
    journal_metric_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_metrics.id", ondelete="SET NULL"), index=True
    )
    quality_signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    external_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metadata_sources: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class PaperIdentifier(TimestampMixin, Base):
    __tablename__ = "paper_identifiers"
    __table_args__ = (
        UniqueConstraint("scheme", "normalized_value", name="uq_paper_identifier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scheme: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(512), nullable=False)


class SearchRun(TimestampMixin, Base):
    __tablename__ = "search_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    providers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    total_results: Mapped[int] = mapped_column(Integer, nullable=False)


class ResearchRun(TimestampMixin, Base):
    __tablename__ = "research_runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    retrieval_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    model_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(256), nullable=False)
    retrieval_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    retrieval_trace: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    facts_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    facts_published_by: Mapped[str | None] = mapped_column(String(64))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delete_reason: Mapped[str | None] = mapped_column(String(500))


class ResearchPlanSnapshot(TimestampMixin, Base):
    __tablename__ = "research_plan_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "research_run_id", "version_number", name="uq_research_plan_version"
        ),
        Index("ix_research_plan_run", "research_run_id", "version_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_plan_snapshots.id", ondelete="RESTRICT")
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class ResearchRound(TimestampMixin, Base):
    __tablename__ = "research_rounds"
    __table_args__ = (
        UniqueConstraint("research_run_id", "round_number", name="uq_research_round"),
        Index("ix_research_round_run", "research_run_id", "round_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ResearchActionAudit(TimestampMixin, Base):
    __tablename__ = "research_action_audits"
    __table_args__ = (
        UniqueConstraint(
            "research_run_id",
            "round_number",
            "action_key",
            name="uq_research_action_key",
        ),
        Index("ix_research_action_run_round", "research_run_id", "round_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action_key: Mapped[str] = mapped_column(String(96), nullable=False)
    action_type: Mapped[str] = mapped_column(String(48), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    target_claim_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    decision_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class ResearchClaimTarget(TimestampMixin, Base):
    __tablename__ = "research_claim_targets"
    __table_args__ = (
        UniqueConstraint("research_run_id", "claim_key", name="uq_research_claim_key"),
        Index("ix_research_claim_run_status", "research_run_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    claim_key: Mapped[str] = mapped_column(String(96), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[float] = mapped_column(Float, nullable=False)
    falsifiable_prediction: Mapped[str] = mapped_column(Text, nullable=False)
    required_evidence_types: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    contradicting_evidence_ids: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    unresolved_reason: Mapped[str | None] = mapped_column(Text)
    verification_label: Mapped[str | None] = mapped_column(String(24))
    verification_confidence: Mapped[float | None] = mapped_column(Float)
    verification_model: Mapped[str | None] = mapped_column(String(512))
    verification_version: Mapped[str | None] = mapped_column(String(64))
    verification_checks: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class SufficiencyAssessment(TimestampMixin, Base):
    __tablename__ = "sufficiency_assessments"
    __table_args__ = (
        UniqueConstraint("research_run_id", "round_number", name="uq_sufficiency_round"),
        Index("ix_sufficiency_run", "research_run_id", "round_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    sufficient: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stop_reason: Mapped[str] = mapped_column(Text, nullable=False)


class ResearchStepMemory(TimestampMixin, Base):
    __tablename__ = "research_step_memories"
    __table_args__ = (
        CheckConstraint(
            "(research_run_id IS NOT NULL AND brainstorm_session_id IS NULL) OR "
            "(research_run_id IS NULL AND brainstorm_session_id IS NOT NULL)",
            name="ck_research_step_root",
        ),
        UniqueConstraint("workflow_id", "step_number", name="uq_research_step_number"),
        Index("ix_research_steps_run_round", "research_run_id", "round_number"),
        Index("ix_research_steps_session", "brainstorm_session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE")
    )
    brainstorm_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE")
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_summary: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    proposed_actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    executed_actions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    new_claims: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    resolved_claims: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    unresolved_claims: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    supersedes_step_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_step_memories.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_model: Mapped[str | None] = mapped_column(String(512))


class RetrievalRouteMemory(TimestampMixin, Base):
    __tablename__ = "retrieval_route_memories"
    __table_args__ = (
        CheckConstraint(
            "status IN ('empty', 'failed', 'rejected')",
            name="ck_retrieval_route_memory_status",
        ),
        CheckConstraint(
            "result_count >= 0",
            name="ck_retrieval_route_memory_result_count",
        ),
        Index(
            "ix_retrieval_route_memory_project_created",
            "project_id",
            "created_at",
        ),
        Index(
            "ix_retrieval_route_memory_workflow",
            "workflow_id",
            "round_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    workflow_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    workflow_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("research_runs.id", ondelete="CASCADE")
    )
    brainstorm_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("brainstorm_sessions.id", ondelete="CASCADE")
    )
    synthesis_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("synthesis_sessions.id", ondelete="CASCADE")
    )
    round_number: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_reason: Mapped[str] = mapped_column(Text, nullable=False)
    retry_worthy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    retry_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    route_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class DocumentAsset(TimestampMixin, Base):
    __tablename__ = "document_assets"
    __table_args__ = (
        UniqueConstraint("paper_id", "content_hash", name="uq_document_assets_paper_content_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source: Mapped[AssetSource] = mapped_column(
        Enum(AssetSource, native_enum=False, length=32), nullable=False
    )
    evidence_depth: Mapped[EvidenceDepth] = mapped_column(
        Enum(EvidenceDepth, native_enum=False, length=32), nullable=False
    )
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(ParseStatus, native_enum=False, length=32), default=ParseStatus.PENDING
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(128))
    license_name: Mapped[str | None] = mapped_column(String(256))
    access_url: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str | None] = mapped_column(String(64))
    cloud_processing_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )

    paper: Mapped[Paper] = relationship()


class Section(TimestampMixin, Base):
    __tablename__ = "sections"
    __table_args__ = (
        UniqueConstraint("asset_id", "section_path", name="uq_sections_asset_path"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_assets.id", ondelete="CASCADE"), index=True, nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE")
    )
    section_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class Chunk(TimestampMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("content_hash", "section_id", name="uq_chunks_section_content_hash"),
        Index("ix_chunks_section_ordinal", "section_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    section_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sections.id", ondelete="CASCADE"), index=True, nullable=False
    )
    previous_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL")
    )
    next_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class StructuredEvidenceObject(TimestampMixin, Base):
    __tablename__ = "structured_evidence_objects"
    __table_args__ = (
        UniqueConstraint(
            "asset_id",
            "object_type",
            "content_hash",
            "source_key",
            name="uq_structured_evidence_object",
        ),
        Index("ix_structured_evidence_asset_type", "asset_id", "object_type"),
        Index("ix_structured_evidence_chunk", "parent_chunk_id"),
        CheckConstraint(
            "object_type IN ('figure', 'table', 'equation', 'caption', 'table_cell')",
            name="ck_structured_evidence_object_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    asset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_assets.id", ondelete="CASCADE"), nullable=False
    )
    parent_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL")
    )
    parent_object_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("structured_evidence_objects.id", ondelete="CASCADE")
    )
    object_type: Mapped[str] = mapped_column(String(24), nullable=False)
    label: Mapped[str | None] = mapped_column(String(256))
    text_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    bounding_box: Mapped[dict[str, float]] = mapped_column(JSON, default=dict, nullable=False)
    cell_range: Mapped[str | None] = mapped_column(String(64))
    source_key: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    extraction_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_eligible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class EmbeddingModel(TimestampMixin, Base):
    __tablename__ = "embedding_models"
    __table_args__ = (
        UniqueConstraint("name", "revision", name="uq_embedding_models_name_revision"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    revision: Mapped[str] = mapped_column(String(128), nullable=False)
    dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    query_prefix: Mapped[str] = mapped_column(String(64), default="query: ", nullable=False)
    passage_prefix: Mapped[str] = mapped_column(String(64), default="passage: ", nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ChunkEmbedding(TimestampMixin, Base):
    __tablename__ = "chunk_embeddings"
    __table_args__ = (
        UniqueConstraint("chunk_id", "model_id", name="uq_chunk_embeddings_chunk_model"),
        CheckConstraint("length(vector) > 0", name="ck_chunk_embeddings_vector_not_empty"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("embedding_models.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_hash: Mapped[str] = mapped_column(
        String(64), default="", nullable=False
    )


_validate_vector_insert = DDL(  # type: ignore[no-untyped-call]
    """
    CREATE TRIGGER trg_chunk_embeddings_validate_insert
    BEFORE INSERT ON chunk_embeddings
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM embedding_models em WHERE em.id = NEW.model_id
    ) OR length(NEW.vector) != COALESCE((
        SELECT em.dimension * 4 FROM embedding_models em WHERE em.id = NEW.model_id
    ), -1)
    BEGIN
        SELECT RAISE(ABORT, 'compact vector dimension does not match embedding model');
    END
    """
).execute_if(dialect="sqlite")

_validate_vector_update = DDL(  # type: ignore[no-untyped-call]
    """
    CREATE TRIGGER trg_chunk_embeddings_validate_update
    BEFORE UPDATE OF model_id, vector ON chunk_embeddings
    FOR EACH ROW
    WHEN NOT EXISTS (
        SELECT 1 FROM embedding_models em WHERE em.id = NEW.model_id
    ) OR length(NEW.vector) != COALESCE((
        SELECT em.dimension * 4 FROM embedding_models em WHERE em.id = NEW.model_id
    ), -1)
    BEGIN
        SELECT RAISE(ABORT, 'compact vector dimension does not match embedding model');
    END
    """
).execute_if(dialect="sqlite")

event.listen(ChunkEmbedding.__table__, "after_create", _validate_vector_insert)
event.listen(ChunkEmbedding.__table__, "after_create", _validate_vector_update)


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=32), default=JobStatus.QUEUED, nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class SynthesisSession(TimestampMixin, Base):
    __tablename__ = "synthesis_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','analyzing','writing','reviewing','completed','failed')",
            name="ck_synthesis_session_status",
        ),
        Index("ix_synthesis_sessions_project_updated", "project_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    model_depth: Mapped[str] = mapped_column(String(16), default="deep", nullable=False)
    max_context_tokens: Mapped[int] = mapped_column(Integer, default=131072, nullable=False)
    max_review_rounds: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    include_workbench_notes: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_online_literature: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    template_profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    document_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    global_outline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    global_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    glossary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    citation_ledger: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    figure_manifest: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list, nullable=False
    )
    manuscript_markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    current_round: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class SynthesisSource(TimestampMixin, Base):
    __tablename__ = "synthesis_sources"
    __table_args__ = (
        UniqueConstraint("session_id", "content_hash", name="uq_synthesis_source_hash"),
        CheckConstraint(
            "source_type IN ('workbench_note','uploaded_file','user_text')",
            name="ck_synthesis_source_type",
        ),
        Index("ix_synthesis_sources_session", "session_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("synthesis_sessions.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref_id: Mapped[uuid.UUID | None] = mapped_column()
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    module_index: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    relations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="ready", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class SynthesisSection(TimestampMixin, Base):
    __tablename__ = "synthesis_sections"
    __table_args__ = (
        UniqueConstraint("session_id", "section_key", name="uq_synthesis_section_key"),
        Index("ix_synthesis_sections_session_order", "session_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("synthesis_sessions.id", ondelete="CASCADE"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("synthesis_sections.id", ondelete="CASCADE")
    )
    section_key: Mapped[str] = mapped_column(String(96), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    level: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, default="", nullable=False)
    outline: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    draft_markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    section_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)


class SynthesisReviewRound(TimestampMixin, Base):
    __tablename__ = "synthesis_review_rounds"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "round_number", "scope", "section_id",
            name="uq_synthesis_review_scope",
        ),
        Index("ix_synthesis_reviews_session_round", "session_id", "round_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("synthesis_sessions.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("synthesis_sections.id", ondelete="CASCADE")
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    feedback: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    affected_section_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="completed", nullable=False)


class SynthesisAgentRun(TimestampMixin, Base):
    __tablename__ = "synthesis_agent_runs"
    __table_args__ = (
        Index("ix_synthesis_agent_runs_session_round", "session_id", "round_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("synthesis_sessions.id", ondelete="CASCADE"), nullable=False
    )
    section_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("synthesis_sections.id", ondelete="CASCADE")
    )
    round_number: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)


class SynthesisLiterature(TimestampMixin, Base):
    __tablename__ = "synthesis_literature"
    __table_args__ = (
        UniqueConstraint("session_id", "paper_id", name="uq_synthesis_literature_paper"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("synthesis_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    query: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(String(32), default="retrieved", nullable=False)


class PaperCitation(TimestampMixin, Base):
    __tablename__ = "paper_citations"
    __table_args__ = (
        UniqueConstraint("source_paper_id", "target_paper_id", name="uq_paper_citation_edge"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    source_paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    target_paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_name: Mapped[str] = mapped_column(String(64), nullable=False)


class MeSHTerm(TimestampMixin, Base):
    __tablename__ = "mesh_terms"

    descriptor_ui: Mapped[str] = mapped_column(String(32), primary_key=True)
    preferred_label: Mapped[str] = mapped_column(Text, nullable=False)
    tree_numbers: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class PaperMeSH(TimestampMixin, Base):
    __tablename__ = "paper_mesh"

    paper_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True
    )
    descriptor_ui: Mapped[str] = mapped_column(
        ForeignKey("mesh_terms.descriptor_ui", ondelete="CASCADE"), primary_key=True
    )
    is_major_topic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[ClaimStatus] = mapped_column(
        Enum(ClaimStatus, native_enum=False, length=32), default=ClaimStatus.DRAFT
    )


class EvidenceLink(TimestampMixin, Base):
    __tablename__ = "evidence_links"
    __table_args__ = (
        UniqueConstraint("claim_id", "chunk_id", "relation", name="uq_evidence_link"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    claim_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), index=True, nullable=False
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="CASCADE"), index=True, nullable=False
    )
    evidence_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    relation: Mapped[EvidenceRelation] = mapped_column(
        Enum(EvidenceRelation, native_enum=False, length=32), nullable=False
    )
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    excerpt_char_end: Mapped[int] = mapped_column(Integer, nullable=False)
    support_score: Mapped[float | None] = mapped_column(Float)
    mechanically_validated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
