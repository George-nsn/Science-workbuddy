"""Add brainstorm provenance to workbench tasks.

Revision ID: 20260803_0016
Revises: 20260803_0015
Create Date: 2026-08-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0016"
down_revision: str | None = "20260803_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("workbench_tasks", sa.Column("source_kind", sa.String(length=32)))
    op.add_column("workbench_tasks", sa.Column("source_id", sa.Uuid()))
    op.add_column("workbench_tasks", sa.Column("source_key", sa.String(length=64)))
    op.add_column("workbench_tasks", sa.Column("source_section", sa.String(length=500)))
    op.create_index(
        "uq_workbench_task_source",
        "workbench_tasks",
        ["project_id", "source_kind", "source_id", "source_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_workbench_task_source", table_name="workbench_tasks")
    op.drop_column("workbench_tasks", "source_section")
    op.drop_column("workbench_tasks", "source_key")
    op.drop_column("workbench_tasks", "source_id")
    op.drop_column("workbench_tasks", "source_kind")