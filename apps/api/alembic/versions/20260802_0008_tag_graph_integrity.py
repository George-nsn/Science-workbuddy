"""Harden tag, collection, and graph indexes.

Revision ID: 20260802_0008
Revises: 20260802_0007
Create Date: 2026-08-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0008"
down_revision: str | None = "20260802_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_paper_tags_project_paper",
        "paper_tags",
        ["project_id", "paper_id"],
    )
    op.create_index(
        "ix_paper_tags_project_tag",
        "paper_tags",
        ["project_id", "tag_id"],
    )
    op.create_index(
        "ix_collection_papers_paper",
        "collection_papers",
        ["paper_id"],
    )
    with op.batch_alter_table("graph_edges") as batch_op:
        batch_op.drop_constraint("uq_graph_edge", type_="unique")
        batch_op.add_column(
            sa.Column("evidence_key", sa.String(length=36), server_default="", nullable=False)
        )
    op.execute(
        "UPDATE graph_edges SET evidence_key = "
        "COALESCE(CAST(evidence_chunk_id AS TEXT), '')"
    )
    with op.batch_alter_table("graph_edges") as batch_op:
        batch_op.create_unique_constraint(
            "uq_graph_edge",
            [
                "project_id",
                "scope_key",
                "source_key",
                "relation",
                "target_key",
                "evidence_key",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("graph_edges") as batch_op:
        batch_op.drop_constraint("uq_graph_edge", type_="unique")
        batch_op.drop_column("evidence_key")
        batch_op.create_unique_constraint(
            "uq_graph_edge",
            [
                "project_id",
                "scope_key",
                "source_key",
                "relation",
                "target_key",
                "evidence_chunk_id",
            ],
        )
    op.drop_index("ix_collection_papers_paper", table_name="collection_papers")
    op.drop_index("ix_paper_tags_project_tag", table_name="paper_tags")
    op.drop_index("ix_paper_tags_project_paper", table_name="paper_tags")