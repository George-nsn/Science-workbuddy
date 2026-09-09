"""Add full deterministic GraphRAG communities and provenance.

Revision ID: 20260805_0023
Revises: 20260805_0022
Create Date: 2026-08-05
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0023"
down_revision: str | None = "20260805_0022"
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
        "graph_entity_mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("node_key", sa.String(length=256), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("mention_text", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("extractor", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "scope_key",
            "node_key",
            "chunk_id",
            "char_start",
            "char_end",
            name="uq_graph_entity_mention_span",
        ),
    )
    op.create_index(
        "ix_graph_mentions_node",
        "graph_entity_mentions",
        ["project_id", "scope_key", "node_key"],
    )
    op.create_index("ix_graph_mentions_chunk", "graph_entity_mentions", ["chunk_id"])

    op.create_table(
        "graph_communities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("community_key", sa.String(length=128), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("parent_community_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("algorithm", sa.String(length=128), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("stats", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["parent_community_id"], ["graph_communities.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "scope_key",
            "level",
            "community_key",
            name="uq_graph_community_key",
        ),
    )
    op.create_index(
        "ix_graph_communities_scope",
        "graph_communities",
        ["project_id", "scope_key", "level"],
    )
    op.create_table(
        "graph_community_members",
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("node_key", sa.String(length=256), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("centrality", sa.Float(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["community_id"], ["graph_communities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("community_id", "node_key"),
        sa.UniqueConstraint(
            "community_id", "node_key", name="uq_graph_community_member"
        ),
    )
    op.create_index(
        "ix_graph_community_members_node",
        "graph_community_members",
        ["node_key"],
    )
    op.create_table(
        "graph_community_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_id", sa.Uuid(), nullable=False),
        sa.Column("report_version", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("report_text", sa.Text(), nullable=False),
        sa.Column("evidence_bundle", sa.JSON(), nullable=False),
        sa.Column("generated_by", sa.String(length=128), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["community_id"], ["graph_communities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "community_id", "report_version", name="uq_graph_report_version"
        ),
    )
    op.create_index(
        "ix_graph_reports_community",
        "graph_community_reports",
        ["community_id"],
    )
    op.create_table(
        "graph_path_audits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("source_node_key", sa.String(length=256), nullable=False),
        sa.Column("target_node_key", sa.String(length=256), nullable=False),
        sa.Column("path_nodes", sa.JSON(), nullable=False),
        sa.Column("path_edges", sa.JSON(), nullable=False),
        sa.Column("path_score", sa.Float(), nullable=False),
        sa.Column("provenance_bundle", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_graph_path_audits_query",
        "graph_path_audits",
        ["project_id", "scope_key", "query_hash"],
    )


def downgrade() -> None:
    op.drop_table("graph_path_audits")
    op.drop_table("graph_community_reports")
    op.drop_table("graph_community_members")
    op.drop_table("graph_communities")
    op.drop_table("graph_entity_mentions")
