"""Add controlled web search and brainstorm plan workflow.

Revision ID: 20260804_0018
Revises: 20260804_0017
Create Date: 2026-08-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0018"
down_revision: str | None = "20260804_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_search_configurations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="tavily"),
        sa.Column(
            "base_url",
            sa.String(length=2048),
            nullable=False,
            server_default="https://api.tavily.com",
        ),
        sa.Column("api_key_encrypted", sa.Text(), nullable=False),
        sa.Column("max_results", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "search_depth",
            sa.String(length=16),
            nullable=False,
            server_default="advanced",
        ),
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
        sa.CheckConstraint("id = 1", name="ck_web_search_configuration_singleton"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column(
        "brainstorm_sessions",
        sa.Column("workflow", sa.String(length=24), nullable=False, server_default="classic"),
    )
    op.add_column(
        "brainstorm_sessions",
        sa.Column("phase", sa.String(length=32), nullable=False, server_default="conversation"),
    )
    op.add_column(
        "brainstorm_sessions",
        sa.Column("allow_web_search", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "brainstorm_sessions",
        sa.Column("plan_snapshot", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("brainstorm_sessions", "plan_snapshot")
    op.drop_column("brainstorm_sessions", "allow_web_search")
    op.drop_column("brainstorm_sessions", "phase")
    op.drop_column("brainstorm_sessions", "workflow")
    op.drop_table("web_search_configurations")