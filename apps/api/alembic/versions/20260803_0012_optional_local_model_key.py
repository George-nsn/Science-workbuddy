"""Allow local model providers without an API Key.

Revision ID: 20260803_0012
Revises: 20260803_0011
Create Date: 2026-08-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0012"
down_revision: str | None = "20260803_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("model_configurations") as batch_op:
        batch_op.alter_column(
            "api_key_encrypted",
            existing_type=sa.Text(),
            nullable=True,
        )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM model_configurations WHERE api_key_encrypted IS NULL")
    )
    with op.batch_alter_table("model_configurations") as batch_op:
        batch_op.alter_column(
            "api_key_encrypted",
            existing_type=sa.Text(),
            nullable=False,
        )