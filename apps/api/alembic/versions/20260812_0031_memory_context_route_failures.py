"""Unify memory context types and persist failed retrieval routes.

Revision ID: 20260812_0031
Revises: 20260811_0030
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260812_0031"
down_revision: str | None = "20260811_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_derived_indexes") as batch_op:
        batch_op.drop_constraint("ck_memory_derived_entity_type", type_="check")
        batch_op.create_check_constraint(
            "ck_memory_derived_entity_type",
            "entity_type IN "
            "('project_fact', 'research_step', 'verified_claim', 'failed_route')",
        )

    op.create_table(
        "retrieval_route_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_kind", sa.String(32), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=True),
        sa.Column("brainstorm_session_id", sa.Uuid(), nullable=True),
        sa.Column("synthesis_session_id", sa.Uuid(), nullable=True),
        sa.Column("round_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("tool", sa.String(64), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=False),
        sa.Column("retry_worthy", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("retry_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "route_metadata",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('empty', 'failed', 'rejected')",
            name="ck_retrieval_route_memory_status",
        ),
        sa.CheckConstraint(
            "result_count >= 0",
            name="ck_retrieval_route_memory_result_count",
        ),
        sa.ForeignKeyConstraint(
            ["brainstorm_session_id"],
            ["brainstorm_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"],
            ["research_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["synthesis_session_id"],
            ["synthesis_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_retrieval_route_memory_project_created",
        "retrieval_route_memories",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_retrieval_route_memory_workflow",
        "retrieval_route_memories",
        ["workflow_id", "round_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retrieval_route_memory_workflow",
        table_name="retrieval_route_memories",
    )
    op.drop_index(
        "ix_retrieval_route_memory_project_created",
        table_name="retrieval_route_memories",
    )
    op.drop_table("retrieval_route_memories")
    op.execute(
        "DELETE FROM memory_derived_indexes "
        "WHERE entity_type IN ('verified_claim', 'failed_route')"
    )
    with op.batch_alter_table("memory_derived_indexes") as batch_op:
        batch_op.drop_constraint("ck_memory_derived_entity_type", type_="check")
        batch_op.create_check_constraint(
            "ck_memory_derived_entity_type",
            "entity_type IN ('project_fact', 'research_step')",
        )
