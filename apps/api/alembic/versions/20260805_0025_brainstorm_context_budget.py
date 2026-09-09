"""Add per-session model context budget and deep defaults.

Revision ID: 20260805_0025
Revises: 20260805_0024
Create Date: 2026-08-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0025"
down_revision: str | None = "20260805_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "brainstorm_sessions",
        sa.Column(
            "max_context_tokens",
            sa.Integer(),
            nullable=False,
            server_default="131072",
        ),
    )


def downgrade() -> None:
    op.drop_column("brainstorm_sessions", "max_context_tokens")
