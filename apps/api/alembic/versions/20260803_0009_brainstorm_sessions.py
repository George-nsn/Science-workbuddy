"""Add versioned, auditable brainstorm sessions.

Revision ID: 20260803_0009
Revises: 20260802_0008
Create Date: 2026-08-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0009"
down_revision: str | None = "20260802_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "brainstorm_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("session_number", sa.Integer(), nullable=False),
        sa.Column("confirmation_round", sa.Integer(), nullable=False),
        sa.Column("allow_pubmed_search", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["rag_collections.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "session_number", name="uq_brainstorm_session_number"
        ),
    )
    op.create_index("ix_brainstorm_sessions_project_id", "brainstorm_sessions", ["project_id"])
    op.create_index(
        "ix_brainstorm_sessions_collection_id", "brainstorm_sessions", ["collection_id"]
    )
    op.create_index(
        "ix_brainstorm_sessions_project_status",
        "brainstorm_sessions",
        ["project_id", "status"],
    )
    op.create_table(
        "brainstorm_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["session_id"], ["brainstorm_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "sequence_number", name="uq_brainstorm_message_seq"
        ),
    )
    op.create_index(
        "ix_brainstorm_messages_session",
        "brainstorm_messages",
        ["session_id", "sequence_number"],
    )
    op.create_table(
        "brainstorm_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_filename", sa.String(length=512), nullable=True),
        sa.Column("source_media_type", sa.String(length=128), nullable=True),
        sa.Column("change_summary", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["parent_version_id"], ["brainstorm_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["brainstorm_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_id", "version_number", name="uq_brainstorm_version_number"
        ),
    )
    op.create_index(
        "ix_brainstorm_versions_session",
        "brainstorm_versions",
        ["session_id", "version_number"],
    )
    op.create_table(
        "brainstorm_agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("agent_name", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("model_provider", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=256), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["session_id"], ["brainstorm_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_brainstorm_agent_runs_session_turn",
        "brainstorm_agent_runs",
        ["session_id", "turn_number"],
    )
    op.create_table(
        "brainstorm_literature",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("tags_applied", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["session_id"], ["brainstorm_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id", "paper_id"),
        sa.UniqueConstraint(
            "session_id", "paper_id", name="uq_brainstorm_session_paper"
        ),
    )
    op.execute(
        "CREATE TRIGGER brainstorm_original_no_update "
        "BEFORE UPDATE ON brainstorm_versions "
        "WHEN OLD.kind = 'original' BEGIN "
        "SELECT RAISE(ABORT, 'original brainstorm version is immutable'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS brainstorm_original_no_update")
    op.drop_table("brainstorm_literature")
    op.drop_table("brainstorm_agent_runs")
    op.drop_table("brainstorm_versions")
    op.drop_table("brainstorm_messages")
    op.drop_table("brainstorm_sessions")
