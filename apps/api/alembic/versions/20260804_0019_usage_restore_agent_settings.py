"""Add usage analytics and brainstorm advanced settings.

Revision ID: 20260804_0019
Revises: 20260804_0018
Create Date: 2026-08-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0019"
down_revision: str | None = "20260804_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "brainstorm_sessions",
        sa.Column(
            "model_depth",
            sa.String(length=16),
            nullable=False,
            server_default="balanced",
        ),
    )
    op.add_column(
        "brainstorm_sessions",
        sa.Column("agent_background", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("research_run_id", sa.Uuid(), nullable=True),
        sa.Column("turn_number", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=256), nullable=True),
        sa.Column("model_depth", sa.String(length=16), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "token_count_estimated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column(
            "cost_source",
            sa.String(length=24),
            nullable=False,
            server_default="unavailable",
        ),
        sa.Column("cache_level", sa.String(length=16), nullable=True),
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
        sa.CheckConstraint(
            "event_type IN ('model', 'retrieval')",
            name="ck_usage_event_type",
        ),
        sa.ForeignKeyConstraint(["message_id"], ["brainstorm_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["brainstorm_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_events_project_created",
        "usage_events",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_usage_events_session_turn",
        "usage_events",
        ["session_id", "turn_number"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_events_session_turn", table_name="usage_events")
    op.drop_index("ix_usage_events_project_created", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_column("brainstorm_sessions", "agent_background")
    op.drop_column("brainstorm_sessions", "model_depth")