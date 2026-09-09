"""Add SQLite persistent cache and lightweight knowledge graph.

Revision ID: 20260802_0005
Revises: 20260802_0004
Create Date: 2026-08-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0005"
down_revision: str | None = "20260802_0004"
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
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column(
                "retrieval_revision",
                sa.Integer(),
                server_default="1",
                nullable=False,
            )
        )
    op.create_table(
        "cache_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("cache_key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        *timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("namespace", "cache_key", name="uq_cache_namespace_key"),
    )
    op.create_index("ix_cache_entries_expires_at", "cache_entries", ["expires_at"])
    op.create_table(
        "graph_nodes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("node_key", sa.String(length=256), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("properties", sa.JSON(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "node_key", name="uq_graph_node_key"),
    )
    op.create_index("ix_graph_nodes_project_id", "graph_nodes", ["project_id"])
    op.create_index("ix_graph_nodes_type", "graph_nodes", ["project_id", "node_type"])
    op.create_table(
        "graph_edges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("source_key", sa.String(length=256), nullable=False),
        sa.Column("relation", sa.String(length=64), nullable=False),
        sa.Column("target_key", sa.String(length=256), nullable=False),
        sa.Column("evidence_chunk_id", sa.Uuid(), nullable=True),
        sa.Column("provenance", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["evidence_chunk_id"], ["chunks.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "source_key",
            "relation",
            "target_key",
            "evidence_chunk_id",
            name="uq_graph_edge",
        ),
    )
    op.create_index("ix_graph_edges_project_id", "graph_edges", ["project_id"])
    op.create_index(
        "ix_graph_edges_source",
        "graph_edges",
        ["project_id", "source_key", "relation"],
    )
    op.create_index(
        "ix_graph_edges_target",
        "graph_edges",
        ["project_id", "target_key", "relation"],
    )
    op.execute(
        "CREATE VIRTUAL TABLE graph_node_fts USING fts5("
        "project_id UNINDEXED, node_key UNINDEXED, label, tokenize='porter unicode61')"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_ai AFTER INSERT ON graph_nodes BEGIN "
        "INSERT INTO graph_node_fts(project_id, node_key, label) "
        "VALUES (new.project_id, new.node_key, new.label); END"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_ad AFTER DELETE ON graph_nodes BEGIN "
        "DELETE FROM graph_node_fts WHERE project_id = old.project_id "
        "AND node_key = old.node_key; END"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_au AFTER UPDATE OF label ON graph_nodes BEGIN "
        "DELETE FROM graph_node_fts WHERE project_id = old.project_id "
        "AND node_key = old.node_key; "
        "INSERT INTO graph_node_fts(project_id, node_key, label) "
        "VALUES (new.project_id, new.node_key, new.label); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS graph_node_fts_au")
    op.execute("DROP TRIGGER IF EXISTS graph_node_fts_ad")
    op.execute("DROP TRIGGER IF EXISTS graph_node_fts_ai")
    op.execute("DROP TABLE IF EXISTS graph_node_fts")
    op.drop_table("graph_edges")
    op.drop_table("graph_nodes")
    op.drop_table("cache_entries")
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("retrieval_revision")
