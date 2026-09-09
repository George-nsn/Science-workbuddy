"""Add origin to literature archives for upload batches.

Revision ID: 20260804_0020
Revises: 20260804_0019
Create Date: 2026-08-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0020"
down_revision: str | None = "20260804_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "literature_archives",
        sa.Column(
            "origin",
            sa.String(length=24),
            nullable=False,
            server_default="manual",
        ),
    )


def downgrade() -> None:
    op.drop_column("literature_archives", "origin")