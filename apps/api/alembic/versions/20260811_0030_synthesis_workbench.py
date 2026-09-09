"""Add chapter-based synthesis writing workbench.

Revision ID: 20260811_0030
Revises: 20260806_0029
Create Date: 2026-08-11
"""

# ruff: noqa: E501

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260811_0030"
down_revision: str | None = "20260806_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "synthesis_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("model_depth", sa.String(16), nullable=False, server_default="deep"),
        sa.Column("max_context_tokens", sa.Integer(), nullable=False, server_default="131072"),
        sa.Column("max_review_rounds", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("include_workbench_notes", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("allow_online_literature", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("template_profile", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("document_map", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("global_outline", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("global_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("glossary", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("citation_ledger", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("figure_manifest", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("manuscript_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("current_round", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "status IN ('draft','analyzing','writing','reviewing','completed','failed')",
            name="ck_synthesis_session_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_synthesis_sessions_project_updated", "synthesis_sessions", ["project_id", "updated_at"])
    op.create_index("ix_synthesis_sessions_project_id", "synthesis_sessions", ["project_id"])
    op.create_index("ix_synthesis_sessions_deleted_at", "synthesis_sessions", ["deleted_at"])

    op.create_table(
        "synthesis_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_ref_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("module_index", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("relations", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(24), nullable=False, server_default="ready"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "source_type IN ('workbench_note','uploaded_file','user_text')",
            name="ck_synthesis_source_type",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["synthesis_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "content_hash", name="uq_synthesis_source_hash"),
    )
    op.create_index("ix_synthesis_sources_session", "synthesis_sources", ["session_id", "created_at"])

    op.create_table(
        "synthesis_sections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("section_key", sa.String(96), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False, server_default=""),
        sa.Column("outline", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("draft_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("section_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("evidence_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["parent_id"], ["synthesis_sections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["synthesis_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "section_key", name="uq_synthesis_section_key"),
    )
    op.create_index("ix_synthesis_sections_session_order", "synthesis_sections", ["session_id", "ordinal"])

    op.create_table(
        "synthesis_review_rounds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=True),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(24), nullable=False),
        sa.Column("verdict", sa.String(24), nullable=False),
        sa.Column("feedback", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("affected_section_keys", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(24), nullable=False, server_default="completed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["section_id"], ["synthesis_sections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["synthesis_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "round_number", "scope", "section_id", name="uq_synthesis_review_scope"),
    )
    op.create_index("ix_synthesis_reviews_session_round", "synthesis_review_rounds", ["session_id", "round_number"])

    op.create_table(
        "synthesis_agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=True),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["section_id"], ["synthesis_sections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["synthesis_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_synthesis_agent_runs_session_round", "synthesis_agent_runs", ["session_id", "round_number"])

    op.create_table(
        "synthesis_literature",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(32), nullable=False, server_default="retrieved"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["synthesis_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "paper_id"),
        sa.UniqueConstraint("session_id", "paper_id", name="uq_synthesis_literature_paper"),
    )


def downgrade() -> None:
    op.drop_table("synthesis_literature")
    op.drop_index("ix_synthesis_agent_runs_session_round", table_name="synthesis_agent_runs")
    op.drop_table("synthesis_agent_runs")
    op.drop_index("ix_synthesis_reviews_session_round", table_name="synthesis_review_rounds")
    op.drop_table("synthesis_review_rounds")
    op.drop_index("ix_synthesis_sections_session_order", table_name="synthesis_sections")
    op.drop_table("synthesis_sections")
    op.drop_index("ix_synthesis_sources_session", table_name="synthesis_sources")
    op.drop_table("synthesis_sources")
    op.drop_index("ix_synthesis_sessions_deleted_at", table_name="synthesis_sessions")
    op.drop_index("ix_synthesis_sessions_project_id", table_name="synthesis_sessions")
    op.drop_index("ix_synthesis_sessions_project_updated", table_name="synthesis_sessions")
    op.drop_table("synthesis_sessions")
