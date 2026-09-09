"""Add research workbench notes, tasks, and OCR attachments.

Revision ID: 20260803_0015
Revises: 20260803_0014
Create Date: 2026-08-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0015"
down_revision: str | None = "20260803_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    created_at, updated_at = _timestamps()
    op.create_table(
        "workbench_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False, server_default=""),
        created_at,
        updated_at,
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "entry_date",
            name="uq_workbench_note_project_date",
        ),
    )
    op.create_index("ix_workbench_notes_project_id", "workbench_notes", ["project_id"])
    op.create_index(
        "ix_workbench_notes_project_updated",
        "workbench_notes",
        ["project_id", "updated_at"],
    )

    created_at, updated_at = _timestamps()
    op.create_table(
        "workbench_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("work_date", sa.Date()),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="todo"),
        sa.Column(
            "priority",
            sa.String(length=16),
            nullable=False,
            server_default="medium",
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        created_at,
        updated_at,
        sa.CheckConstraint(
            "status IN ('todo', 'in_progress', 'done')",
            name="ck_workbench_task_status",
        ),
        sa.CheckConstraint(
            "priority IN ('low', 'medium', 'high')",
            name="ck_workbench_task_priority",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workbench_tasks_project_id", "workbench_tasks", ["project_id"])
    op.create_index(
        "ix_workbench_tasks_project_status",
        "workbench_tasks",
        ["project_id", "status"],
    )
    op.create_index(
        "ix_workbench_tasks_project_date",
        "workbench_tasks",
        ["project_id", "work_date"],
    )

    created_at, updated_at = _timestamps()
    op.create_table(
        "workbench_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "ocr_status",
            sa.String(length=24),
            nullable=False,
            server_default="skipped",
        ),
        sa.Column("ocr_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("ocr_error", sa.Text()),
        created_at,
        updated_at,
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["note_id"],
            ["workbench_notes.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "note_id",
            "content_hash",
            name="uq_workbench_attachment_hash",
        ),
    )
    op.create_index(
        "ix_workbench_attachments_project_id",
        "workbench_attachments",
        ["project_id"],
    )
    op.create_index(
        "ix_workbench_attachments_note_id",
        "workbench_attachments",
        ["note_id"],
    )
    op.create_index(
        "ix_workbench_attachments_project_note",
        "workbench_attachments",
        ["project_id", "note_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workbench_attachments_project_note",
        table_name="workbench_attachments",
    )
    op.drop_index(
        "ix_workbench_attachments_note_id",
        table_name="workbench_attachments",
    )
    op.drop_index(
        "ix_workbench_attachments_project_id",
        table_name="workbench_attachments",
    )
    op.drop_table("workbench_attachments")
    op.drop_index("ix_workbench_tasks_project_date", table_name="workbench_tasks")
    op.drop_index("ix_workbench_tasks_project_status", table_name="workbench_tasks")
    op.drop_index("ix_workbench_tasks_project_id", table_name="workbench_tasks")
    op.drop_table("workbench_tasks")
    op.drop_index("ix_workbench_notes_project_updated", table_name="workbench_notes")
    op.drop_index("ix_workbench_notes_project_id", table_name="workbench_notes")
    op.drop_table("workbench_notes")
