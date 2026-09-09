"""Expand scholarly discovery sources and journal metric provenance.

Revision ID: 20260805_0024
Revises: 20260805_0023
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0024"
down_revision: str | None = "20260805_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "journal_metrics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("openalex_source_id", sa.String(length=64), nullable=False),
        sa.Column("issn_l", sa.String(length=16), nullable=True),
        sa.Column("issn", sa.JSON(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("metric_source", sa.String(length=32), nullable=False),
        sa.Column("two_year_mean_citedness", sa.Float(), nullable=True),
        sa.Column("h_index", sa.Integer(), nullable=True),
        sa.Column("i10_index", sa.Integer(), nullable=True),
        sa.Column("open_quartile", sa.String(length=16), nullable=True),
        sa.Column("percentile", sa.Float(), nullable=True),
        sa.Column("comparison_count", sa.Integer(), nullable=True),
        sa.Column("quartile_basis", sa.JSON(), nullable=False),
        sa.Column("importance_score", sa.Float(), nullable=False),
        sa.Column("metric_updated_date", sa.String(length=64), nullable=True),
        sa.Column("metric_note", sa.Text(), nullable=False),
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
        sa.CheckConstraint(
            "percentile IS NULL OR (percentile >= 0 AND percentile <= 1)",
            name="ck_journal_metric_percentile",
        ),
        sa.CheckConstraint(
            "importance_score >= 0 AND importance_score <= 1",
            name="ck_journal_metric_importance",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "openalex_source_id", name="uq_journal_metric_openalex_source"
        ),
    )
    op.create_index("ix_journal_metrics_issn_l", "journal_metrics", ["issn_l"])
    with op.batch_alter_table("papers") as batch:
        batch.add_column(sa.Column("journal_metric_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "fk_papers_journal_metric_id",
            "journal_metrics",
            ["journal_metric_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_papers_journal_metric_id", ["journal_metric_id"])


def downgrade() -> None:
    with op.batch_alter_table("papers") as batch:
        batch.drop_index("ix_papers_journal_metric_id")
        batch.drop_constraint("fk_papers_journal_metric_id", type_="foreignkey")
        batch.drop_column("journal_metric_id")
    op.drop_index("ix_journal_metrics_issn_l", table_name="journal_metrics")
    op.drop_table("journal_metrics")
