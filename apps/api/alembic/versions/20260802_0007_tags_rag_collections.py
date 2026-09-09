"""Add paper tags, selectable RAG collections, and scoped graphs.

Revision ID: 20260802_0007
Revises: 20260802_0006
Create Date: 2026-08-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0007"
down_revision: str | None = "20260802_0006"
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


def rebuild_graph_fts() -> None:
    for trigger in ("graph_node_fts_au", "graph_node_fts_ad", "graph_node_fts_ai"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS graph_node_fts")
    op.execute(
        "CREATE VIRTUAL TABLE graph_node_fts USING fts5("
        "project_id UNINDEXED, scope_key UNINDEXED, node_key UNINDEXED, label, "
        "tokenize='porter unicode61')"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_ai AFTER INSERT ON graph_nodes BEGIN "
        "INSERT INTO graph_node_fts(project_id, scope_key, node_key, label) "
        "VALUES (new.project_id, new.scope_key, new.node_key, new.label); END"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_ad AFTER DELETE ON graph_nodes BEGIN "
        "DELETE FROM graph_node_fts WHERE project_id = old.project_id "
        "AND scope_key = old.scope_key AND node_key = old.node_key; END"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_au AFTER UPDATE OF label ON graph_nodes BEGIN "
        "DELETE FROM graph_node_fts WHERE project_id = old.project_id "
        "AND scope_key = old.scope_key AND node_key = old.node_key; "
        "INSERT INTO graph_node_fts(project_id, scope_key, node_key, label) "
        "VALUES (new.project_id, new.scope_key, new.node_key, new.label); END"
    )
    op.execute(
        "INSERT INTO graph_node_fts(project_id, scope_key, node_key, label) "
        "SELECT project_id, scope_key, node_key, label FROM graph_nodes"
    )


def upgrade() -> None:
    with op.batch_alter_table("project_papers") as batch_op:
        batch_op.add_column(
            sa.Column(
                "tags_manually_curated",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("normalized_name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "normalized_name", name="uq_tag_project_name"),
    )
    op.create_index("ix_tags_project_id", "tags", ["project_id"])
    op.create_index("ix_tags_project_kind", "tags", ["project_id", "kind"])
    op.create_table(
        "paper_tags",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("project_id", "paper_id", "tag_id"),
    )
    op.create_table(
        "rag_collections",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("vector_status", sa.String(length=32), nullable=False),
        sa.Column("graph_status", sa.String(length=32), nullable=False),
        sa.Column("vector_job_id", sa.String(length=128), nullable=True),
        *timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_rag_collection_project_name"),
    )
    op.create_index("ix_rag_collections_project_id", "rag_collections", ["project_id"])
    op.create_table(
        "collection_papers",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("paper_id", sa.Uuid(), nullable=False),
        *timestamps(),
        sa.ForeignKeyConstraint(
            ["collection_id"], ["rag_collections.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["paper_id"], ["papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("collection_id", "paper_id"),
    )

    with op.batch_alter_table("graph_nodes") as batch_op:
        batch_op.drop_index("ix_graph_nodes_type")
        batch_op.drop_constraint("uq_graph_node_key", type_="unique")
        batch_op.add_column(
            sa.Column(
                "scope_key",
                sa.String(length=128),
                server_default="project",
                nullable=False,
            )
        )
        batch_op.create_unique_constraint(
            "uq_graph_node_key", ["project_id", "scope_key", "node_key"]
        )
        batch_op.create_index(
            "ix_graph_nodes_type", ["project_id", "scope_key", "node_type"]
        )
    with op.batch_alter_table("graph_edges") as batch_op:
        batch_op.drop_index("ix_graph_edges_source")
        batch_op.drop_index("ix_graph_edges_target")
        batch_op.drop_constraint("uq_graph_edge", type_="unique")
        batch_op.add_column(
            sa.Column(
                "scope_key",
                sa.String(length=128),
                server_default="project",
                nullable=False,
            )
        )
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
        batch_op.create_index(
            "ix_graph_edges_source",
            ["project_id", "scope_key", "source_key", "relation"],
        )
        batch_op.create_index(
            "ix_graph_edges_target",
            ["project_id", "scope_key", "target_key", "relation"],
        )
    rebuild_graph_fts()


def downgrade() -> None:
    for trigger in ("graph_node_fts_au", "graph_node_fts_ad", "graph_node_fts_ai"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS graph_node_fts")
    with op.batch_alter_table("graph_edges") as batch_op:
        batch_op.drop_index("ix_graph_edges_source")
        batch_op.drop_index("ix_graph_edges_target")
        batch_op.drop_constraint("uq_graph_edge", type_="unique")
        batch_op.drop_column("scope_key")
        batch_op.create_unique_constraint(
            "uq_graph_edge",
            [
                "project_id",
                "source_key",
                "relation",
                "target_key",
                "evidence_chunk_id",
            ],
        )
        batch_op.create_index(
            "ix_graph_edges_source", ["project_id", "source_key", "relation"]
        )
        batch_op.create_index(
            "ix_graph_edges_target", ["project_id", "target_key", "relation"]
        )
    with op.batch_alter_table("graph_nodes") as batch_op:
        batch_op.drop_index("ix_graph_nodes_type")
        batch_op.drop_constraint("uq_graph_node_key", type_="unique")
        batch_op.drop_column("scope_key")
        batch_op.create_unique_constraint("uq_graph_node_key", ["project_id", "node_key"])
        batch_op.create_index("ix_graph_nodes_type", ["project_id", "node_type"])
    op.drop_table("collection_papers")
    op.drop_table("rag_collections")
    op.drop_table("paper_tags")
    op.drop_table("tags")
    with op.batch_alter_table("project_papers") as batch_op:
        batch_op.drop_column("tags_manually_curated")
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
    op.execute(
        "INSERT INTO graph_node_fts(project_id, node_key, label) "
        "SELECT project_id, node_key, label FROM graph_nodes"
    )
