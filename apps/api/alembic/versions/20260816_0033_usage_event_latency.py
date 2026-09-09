"""Track model-call latency on usage events.

Revision ID: 20260816_0033
Revises: 20260816_0032
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0033"
down_revision: str | None = "20260816_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("usage_events") as batch_op:
        batch_op.add_column(
            sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("usage_events") as batch_op:
        batch_op.drop_column("latency_ms")
