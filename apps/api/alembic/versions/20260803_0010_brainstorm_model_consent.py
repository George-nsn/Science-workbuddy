"""Add explicit brainstorm model-processing consent.

Revision ID: 20260803_0010
Revises: 20260803_0009
Create Date: 2026-08-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0010"
down_revision: str | None = "20260803_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("brainstorm_sessions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "model_processing_allowed",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("brainstorm_sessions") as batch_op:
        batch_op.drop_column("model_processing_allowed")