"""Add scholarly metadata and parser provenance.

Revision ID: 20260805_0021
Revises: 20260804_0020
Create Date: 2026-08-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0021"
down_revision: str | None = "20260804_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "papers",
        sa.Column("is_open_access", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "papers",
        sa.Column(
            "open_access_status",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column("papers", sa.Column("open_access_url", sa.Text(), nullable=True))
    op.add_column(
        "papers",
        sa.Column("is_retracted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "papers",
        sa.Column(
            "retraction_status",
            sa.String(length=32),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column("papers", sa.Column("citation_count", sa.Integer(), nullable=True))
    op.add_column(
        "papers",
        sa.Column("influential_citation_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "papers",
        sa.Column("quality_signals", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "papers",
        sa.Column("external_metadata", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column(
        "papers",
        sa.Column("metadata_sources", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "document_assets",
        sa.Column("extraction_metadata", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("document_assets", "extraction_metadata")
    op.drop_column("papers", "metadata_sources")
    op.drop_column("papers", "external_metadata")
    op.drop_column("papers", "quality_signals")
    op.drop_column("papers", "influential_citation_count")
    op.drop_column("papers", "citation_count")
    op.drop_column("papers", "retraction_status")
    op.drop_column("papers", "is_retracted")
    op.drop_column("papers", "open_access_url")
    op.drop_column("papers", "open_access_status")
    op.drop_column("papers", "is_open_access")