"""Add lifecycle governance and durable project memory.

Revision ID: 20260804_0017
Revises: 20260803_0016
Create Date: 2026-08-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0017"
down_revision: str | None = "20260803_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _soft_delete_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column(table_name, sa.Column("delete_reason", sa.String(length=500)))
    op.create_index(f"ix_{table_name}_deleted_at", table_name, ["deleted_at"])


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("trash_retention_days", sa.Integer(), nullable=False, server_default="30"),
    )
    for table_name in (
        "workbench_notes",
        "workbench_tasks",
        "workbench_attachments",
        "brainstorm_sessions",
        "research_runs",
    ):
        _soft_delete_columns(table_name)

    op.create_table(
        "brainstorm_session_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("summary_markdown", sa.Text(), nullable=False),
        sa.Column("summary_data", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["session_id"], ["brainstorm_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"], ["brainstorm_messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", name="uq_brainstorm_session_memory_session"),
    )
    op.create_index(
        "ix_brainstorm_session_memories_project",
        "brainstorm_session_memories",
        ["project_id", "updated_at"],
    )

    op.create_table(
        "project_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=48), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("statement_hash", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=48), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_session_id", sa.Uuid()),
        sa.Column("source_locator", sa.JSON(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_project_fact_confidence",
        ),
        sa.CheckConstraint(
            "importance >= 0 AND importance <= 1",
            name="ck_project_fact_importance",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'retracted')",
            name="ck_project_fact_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_session_id"], ["brainstorm_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "category",
            "statement_hash",
            "source_type",
            "source_id",
            name="uq_project_fact_source_statement",
        ),
    )
    op.create_index(
        "ix_project_facts_project_status",
        "project_facts",
        ["project_id", "status", "importance"],
    )
    op.create_index(
        "ix_project_facts_session",
        "project_facts",
        ["source_session_id", "created_at"],
    )
    op.create_index("ix_project_facts_deleted_at", "project_facts", ["deleted_at"])


def downgrade() -> None:
    op.drop_index("ix_project_facts_deleted_at", table_name="project_facts")
    op.drop_index("ix_project_facts_session", table_name="project_facts")
    op.drop_index("ix_project_facts_project_status", table_name="project_facts")
    op.drop_table("project_facts")
    op.drop_index(
        "ix_brainstorm_session_memories_project",
        table_name="brainstorm_session_memories",
    )
    op.drop_table("brainstorm_session_memories")
    for table_name in reversed(
        (
            "workbench_notes",
            "workbench_tasks",
            "workbench_attachments",
            "brainstorm_sessions",
            "research_runs",
        )
    ):
        op.drop_index(f"ix_{table_name}_deleted_at", table_name=table_name)
        op.drop_column(table_name, "delete_reason")
        op.drop_column(table_name, "deleted_at")
    op.drop_column("projects", "trash_retention_days")