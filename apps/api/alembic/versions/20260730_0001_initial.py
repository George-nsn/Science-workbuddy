"""Create the initial traceable literature schema.

Revision ID: 20260730_0001
Revises: None
Create Date: 2026-07-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260730_0001"
down_revision: str | None = None
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
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("default_language", sa.String(length=10), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "papers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pmid", sa.String(length=32), nullable=True),
        sa.Column("pmcid", sa.String(length=32), nullable=True),
        sa.Column("doi", sa.String(length=512), nullable=True),
        sa.Column("doi_normalized", sa.String(length=512), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("journal", sa.String(length=512), nullable=True),
        sa.Column("publication_year", sa.Integer(), nullable=True),
        sa.Column("authors", sa.JSON(), nullable=False),
        sa.Column("publication_types", sa.JSON(), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doi_normalized", name="uq_papers_doi_normalized"),
        sa.UniqueConstraint("pmcid", name="uq_papers_pmcid"),
        sa.UniqueConstraint("pmid", name="uq_papers_pmid"),
    )
    op.create_table(
        "embedding_models",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("revision", sa.String(length=128), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("query_prefix", sa.String(length=64), nullable=False),
        sa.Column("passage_prefix", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "revision", name="uq_embedding_models_name_revision"),
    )
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
    )
    op.create_table(
        "mesh_terms",
        sa.Column("descriptor_ui", sa.String(length=32), nullable=False),
        sa.Column("preferred_label", sa.Text(), nullable=False),
        sa.Column("tree_numbers", sa.JSON(), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("descriptor_ui"),
    )
    op.create_table(
        "document_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("evidence_depth", sa.String(length=32), nullable=False),
        sa.Column("parse_status", sa.String(length=32), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("media_type", sa.String(length=128), nullable=True),
        sa.Column("license_name", sa.String(length=256), nullable=True),
        sa.Column("access_url", sa.Text(), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=True),
        sa.Column("cloud_processing_allowed", sa.Boolean(), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", name="uq_document_assets_content_hash"),
    )
    op.create_index("ix_document_assets_paper_id", "document_assets", ["paper_id"])
    op.create_table(
        "paper_citations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["source_paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_paper_id", "target_paper_id", name="uq_paper_citation_edge"),
    )
    op.create_index("ix_paper_citations_source_paper_id", "paper_citations", ["source_paper_id"])
    op.create_index("ix_paper_citations_target_paper_id", "paper_citations", ["target_paper_id"])
    op.create_table(
        "paper_mesh",
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("descriptor_ui", sa.String(length=32), nullable=False),
        sa.Column("is_major_topic", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["descriptor_ui"], ["mesh_terms.descriptor_ui"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("paper_id", "descriptor_ui"),
    )
    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_claims_project_id", "claims", ["project_id"])
    op.create_table(
        "sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_path", sa.String(length=1024), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["asset_id"], ["document_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_id", "section_path", name="uq_sections_asset_path"),
    )
    op.create_index("ix_sections_asset_id", "sections", ["asset_id"])
    op.create_table(
        "chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("next_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("source_locator", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["next_chunk_id"], ["chunks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["previous_chunk_id"], ["chunks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_hash", "section_id", name="uq_chunks_section_content_hash"),
    )
    op.create_index("ix_chunks_section_id", "chunks", ["section_id"])
    op.create_index("ix_chunks_section_ordinal", "chunks", ["section_id", "ordinal"])
    op.create_table(
        "chunk_embeddings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_id"], ["embedding_models.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chunk_id", "model_id", name="uq_chunk_embeddings_chunk_model"),
    )
    op.create_index("ix_chunk_embeddings_chunk_id", "chunk_embeddings", ["chunk_id"])
    op.create_index("ix_chunk_embeddings_model_id", "chunk_embeddings", ["model_id"])
    op.create_table(
        "evidence_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("claim_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evidence_id", sa.String(length=128), nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("excerpt_char_start", sa.Integer(), nullable=False),
        sa.Column("excerpt_char_end", sa.Integer(), nullable=False),
        sa.Column("support_score", sa.Float(), nullable=True),
        sa.Column("mechanically_validated", sa.Boolean(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_id", "chunk_id", "relation", name="uq_evidence_link"),
        sa.UniqueConstraint("evidence_id"),
    )
    op.create_index("ix_evidence_links_chunk_id", "evidence_links", ["chunk_id"])
    op.create_index("ix_evidence_links_claim_id", "evidence_links", ["claim_id"])


def downgrade() -> None:
    op.drop_table("evidence_links")
    op.drop_table("chunk_embeddings")
    op.drop_table("chunks")
    op.drop_table("sections")
    op.drop_table("claims")
    op.drop_table("paper_mesh")
    op.drop_table("paper_citations")
    op.drop_table("document_assets")
    op.drop_table("mesh_terms")
    op.drop_table("jobs")
    op.drop_table("embedding_models")
    op.drop_table("papers")
    op.drop_table("projects")
