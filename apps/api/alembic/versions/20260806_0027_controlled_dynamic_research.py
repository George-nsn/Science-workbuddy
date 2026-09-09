"""Add controlled dynamic research plans, actions, rounds, and append-only memory.

Revision ID: 20260806_0027
Revises: 20260806_0026
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0027"
down_revision: str | None = "20260806_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "research_plan_snapshots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("parent_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("plan_data", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["parent_snapshot_id"], ["research_plan_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["research_run_id"], ["research_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_run_id", "version_number", name="uq_research_plan_version"
        ),
    )
    op.create_index(
        "ix_research_plan_run",
        "research_plan_snapshots",
        ["research_run_id", "version_number"],
    )
    op.create_table(
        "research_rounds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["research_run_id"], ["research_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("research_run_id", "round_number", name="uq_research_round"),
    )
    op.create_index(
        "ix_research_round_run", "research_rounds", ["research_run_id", "round_number"]
    )
    op.create_table(
        "research_action_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("action_key", sa.String(length=96), nullable=False),
        sa.Column("action_type", sa.String(length=48), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("target_claim_ids", sa.JSON(), nullable=False),
        sa.Column("proposal", sa.JSON(), nullable=False),
        sa.Column("decision_score", sa.Float(), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["research_run_id"], ["research_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_run_id",
            "round_number",
            "action_key",
            name="uq_research_action_key",
        ),
    )
    op.create_index(
        "ix_research_action_run_round",
        "research_action_audits",
        ["research_run_id", "round_number"],
    )
    op.create_table(
        "research_claim_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("claim_key", sa.String(length=96), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Float(), nullable=False),
        sa.Column("falsifiable_prediction", sa.Text(), nullable=False),
        sa.Column("required_evidence_types", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("contradicting_evidence_ids", sa.JSON(), nullable=False),
        sa.Column("unresolved_reason", sa.Text(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["research_run_id"], ["research_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_run_id", "claim_key", name="uq_research_claim_key"
        ),
    )
    op.create_index(
        "ix_research_claim_run_status",
        "research_claim_targets",
        ["research_run_id", "status"],
    )
    op.create_table(
        "sufficiency_assessments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("verdict_data", sa.JSON(), nullable=False),
        sa.Column("sufficient", sa.Boolean(), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["research_run_id"], ["research_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_run_id", "round_number", name="uq_sufficiency_round"
        ),
    )
    op.create_index(
        "ix_sufficiency_run",
        "sufficiency_assessments",
        ["research_run_id", "round_number"],
    )
    op.create_table(
        "research_step_memories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("research_run_id", sa.Uuid(), nullable=True),
        sa.Column("brainstorm_session_id", sa.Uuid(), nullable=True),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(length=32), nullable=False),
        sa.Column("input_summary", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("proposed_actions", sa.JSON(), nullable=False),
        sa.Column("executed_actions", sa.JSON(), nullable=False),
        sa.Column("evidence_ids", sa.JSON(), nullable=False),
        sa.Column("new_claims", sa.JSON(), nullable=False),
        sa.Column("resolved_claims", sa.JSON(), nullable=False),
        sa.Column("unresolved_claims", sa.JSON(), nullable=False),
        sa.Column("supersedes_step_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column("embedding_model", sa.String(length=512), nullable=True),
        *timestamps(),
        sa.CheckConstraint(
            "(research_run_id IS NOT NULL AND brainstorm_session_id IS NULL) OR "
            "(research_run_id IS NULL AND brainstorm_session_id IS NOT NULL)",
            name="ck_research_step_root",
        ),
        sa.ForeignKeyConstraint(
            ["brainstorm_session_id"], ["brainstorm_sessions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["research_run_id"], ["research_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_step_id"], ["research_step_memories.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "step_number", name="uq_research_step_number"),
    )
    op.create_index(
        "ix_research_steps_run_round",
        "research_step_memories",
        ["research_run_id", "round_number"],
    )
    op.create_index(
        "ix_research_steps_session",
        "research_step_memories",
        ["brainstorm_session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_steps_session", table_name="research_step_memories")
    op.drop_index("ix_research_steps_run_round", table_name="research_step_memories")
    op.drop_table("research_step_memories")
    op.drop_index("ix_sufficiency_run", table_name="sufficiency_assessments")
    op.drop_table("sufficiency_assessments")
    op.drop_index("ix_research_claim_run_status", table_name="research_claim_targets")
    op.drop_table("research_claim_targets")
    op.drop_index("ix_research_action_run_round", table_name="research_action_audits")
    op.drop_table("research_action_audits")
    op.drop_index("ix_research_round_run", table_name="research_rounds")
    op.drop_table("research_rounds")
    op.drop_index("ix_research_plan_run", table_name="research_plan_snapshots")
    op.drop_table("research_plan_snapshots")
