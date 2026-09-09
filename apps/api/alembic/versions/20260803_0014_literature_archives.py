"""Add named literature archives.

Revision ID: 20260803_0014
Revises: 20260803_0013
Create Date: 2026-08-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0014"
down_revision: str | None = "20260803_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "literature_archives",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "normalized_name",
            name="uq_literature_archive_project_name",
        ),
    )
    op.create_index(
        "ix_literature_archives_project_id",
        "literature_archives",
        ["project_id"],
    )
    op.create_table(
        "archive_papers",
        sa.Column("archive_id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["archive_id"],
            ["literature_archives.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("archive_id", "paper_id"),
    )
    op.create_index("ix_archive_papers_paper", "archive_papers", ["paper_id"])


def downgrade() -> None:
    op.drop_index("ix_archive_papers_paper", table_name="archive_papers")
    op.drop_table("archive_papers")
    op.drop_index(
        "ix_literature_archives_project_id",
        table_name="literature_archives",
    )
    op.drop_table("literature_archives")