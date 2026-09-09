"""Add reproducible literature discovery and retrieval indexes.

Revision ID: 20260801_0002
Revises: 20260730_0001
Create Date: 2026-08-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260801_0002"
down_revision: str | None = "20260730_0001"
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
    with op.batch_alter_table("document_assets") as batch_op:
        batch_op.drop_constraint("uq_document_assets_content_hash", type_="unique")
        batch_op.create_unique_constraint(
            "uq_document_assets_paper_content_hash",
            ["paper_id", "content_hash"],
        )
    op.create_table(
        "project_papers",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "paper_id"),
    )
    op.create_table(
        "paper_identifiers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scheme", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=False),
        sa.Column("normalized_value", sa.String(length=512), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scheme", "normalized_value", name="uq_paper_identifier"),
    )
    op.create_index("ix_paper_identifiers_paper_id", "paper_identifiers", ["paper_id"])
    op.create_table(
        "search_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("providers", sa.JSON(), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("result_snapshot", sa.JSON(), nullable=False),
        sa.Column("total_results", sa.Integer(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_search_runs_project_id", "search_runs", ["project_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "CREATE INDEX ix_chunks_text_fts ON chunks USING gin "
            "(to_tsvector('english'::regconfig, coalesce(text, ''::text)))"
        )
        op.execute(
            "CREATE INDEX ix_chunk_embeddings_vector_hnsw ON chunk_embeddings "
            "USING hnsw (vector vector_cosine_ops) WITH (m = 16, ef_construction = 128)"
        )
    with op.batch_alter_table("chunks") as batch_op:
        batch_op.create_check_constraint(
            "ck_chunks_valid_offsets", "char_start >= 0 AND char_end >= char_start"
        )
    with op.batch_alter_table("evidence_links") as batch_op:
        batch_op.create_check_constraint(
            "ck_evidence_links_valid_offsets",
            "excerpt_char_start >= 0 AND excerpt_char_end >= excerpt_char_start",
        )
    with op.batch_alter_table("paper_citations") as batch_op:
        batch_op.create_check_constraint(
            "ck_paper_citations_not_self", "source_paper_id <> target_paper_id"
        )


def downgrade() -> None:
    with op.batch_alter_table("paper_citations") as batch_op:
        batch_op.drop_constraint("ck_paper_citations_not_self", type_="check")
    with op.batch_alter_table("evidence_links") as batch_op:
        batch_op.drop_constraint("ck_evidence_links_valid_offsets", type_="check")
    with op.batch_alter_table("chunks") as batch_op:
        batch_op.drop_constraint("ck_chunks_valid_offsets", type_="check")
    op.execute("DROP INDEX IF EXISTS ix_chunk_embeddings_vector_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_chunks_text_fts")
    op.drop_table("search_runs")
    op.drop_table("paper_identifiers")
    op.drop_table("project_papers")
    with op.batch_alter_table("document_assets") as batch_op:
        batch_op.drop_constraint(
            "uq_document_assets_paper_content_hash", type_="unique"
        )
        batch_op.create_unique_constraint(
            "uq_document_assets_content_hash", ["content_hash"]
        )
