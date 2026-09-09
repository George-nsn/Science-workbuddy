"""Add hybrid memory indexes, claim verification, and structured evidence objects.

Revision ID: 20260806_0028
Revises: 20260806_0027
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0028"
down_revision: str | None = "20260806_0027"
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
    op.add_column(
        "research_runs",
        sa.Column("facts_published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "research_runs",
        sa.Column("facts_published_by", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "research_claim_targets",
        sa.Column("verification_label", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "research_claim_targets",
        sa.Column("verification_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "research_claim_targets",
        sa.Column("verification_model", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "research_claim_targets",
        sa.Column("verification_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "research_claim_targets",
        sa.Column(
            "verification_checks",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_table(
        "project_fact_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_fact_id", sa.Uuid(), nullable=False),
        sa.Column("target_fact_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=24), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "relation_type IN ('conflicts_with', 'supersedes', 'synonym_of')",
            name="ck_project_fact_relation_type",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_fact_id"], ["project_facts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["target_fact_id"], ["project_facts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_fact_id",
            "relation_type",
            "target_fact_id",
            name="uq_project_fact_relation",
        ),
    )
    op.create_index(
        "ix_project_fact_relation_source",
        "project_fact_relations",
        ["source_fact_id", "relation_type"],
    )
    op.create_index(
        "ix_project_fact_relation_target",
        "project_fact_relations",
        ["target_fact_id", "relation_type"],
    )
    op.create_table(
        "memory_derived_indexes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=24), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=512), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "entity_type IN ('project_fact', 'research_step')",
            name="ck_memory_derived_entity_type",
        ),
        sa.CheckConstraint(
            "length(vector) > 0", name="ck_memory_derived_vector_not_empty"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "model_name",
            name="uq_memory_derived_entity_model",
        ),
    )
    op.create_index(
        "ix_memory_derived_project_type",
        "memory_derived_indexes",
        ["project_id", "entity_type"],
    )
    op.create_table(
        "structured_evidence_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("parent_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("parent_object_id", sa.Uuid(), nullable=True),
        sa.Column("object_type", sa.String(length=24), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("bounding_box", sa.JSON(), nullable=False),
        sa.Column("cell_range", sa.String(length=64), nullable=True),
        sa.Column("source_key", sa.String(length=128), nullable=False),
        sa.Column("source_locator", sa.JSON(), nullable=False),
        sa.Column("extraction_metadata", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_eligible", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.CheckConstraint(
            "object_type IN ('figure', 'table', 'equation', 'caption', 'table_cell')",
            name="ck_structured_evidence_object_type",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"], ["document_assets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_chunk_id"], ["chunks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["parent_object_id"], ["structured_evidence_objects.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "asset_id",
            "object_type",
            "content_hash",
            "source_key",
            name="uq_structured_evidence_object",
        ),
    )
    op.create_index(
        "ix_structured_evidence_asset_type",
        "structured_evidence_objects",
        ["asset_id", "object_type"],
    )
    op.create_index(
        "ix_structured_evidence_chunk",
        "structured_evidence_objects",
        ["parent_chunk_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_structured_evidence_chunk", table_name="structured_evidence_objects"
    )
    op.drop_index(
        "ix_structured_evidence_asset_type",
        table_name="structured_evidence_objects",
    )
    op.drop_table("structured_evidence_objects")
    op.drop_index("ix_memory_derived_project_type", table_name="memory_derived_indexes")
    op.drop_table("memory_derived_indexes")
    op.drop_index(
        "ix_project_fact_relation_target", table_name="project_fact_relations"
    )
    op.drop_index(
        "ix_project_fact_relation_source", table_name="project_fact_relations"
    )
    op.drop_table("project_fact_relations")
    op.drop_column("research_claim_targets", "verification_checks")
    op.drop_column("research_claim_targets", "verification_version")
    op.drop_column("research_claim_targets", "verification_model")
    op.drop_column("research_claim_targets", "verification_confidence")
    op.drop_column("research_claim_targets", "verification_label")
    op.drop_column("research_runs", "facts_published_by")
    op.drop_column("research_runs", "facts_published_at")