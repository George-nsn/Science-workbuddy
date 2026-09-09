"""Scope graph FTS rows by project.

Revision ID: 20260802_0006
Revises: 20260802_0005
Create Date: 2026-08-02
"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260802_0006"
down_revision: str | None = "20260802_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for trigger in ("graph_node_fts_au", "graph_node_fts_ad", "graph_node_fts_ai"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS graph_node_fts")
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


def downgrade() -> None:
    for trigger in ("graph_node_fts_au", "graph_node_fts_ad", "graph_node_fts_ai"):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    op.execute("DROP TABLE IF EXISTS graph_node_fts")
    op.execute(
        "CREATE VIRTUAL TABLE graph_node_fts USING fts5("
        "node_key UNINDEXED, label, tokenize='porter unicode61')"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_ai AFTER INSERT ON graph_nodes BEGIN "
        "INSERT INTO graph_node_fts(node_key, label) VALUES (new.node_key, new.label); END"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_ad AFTER DELETE ON graph_nodes BEGIN "
        "DELETE FROM graph_node_fts WHERE node_key = old.node_key; END"
    )
    op.execute(
        "CREATE TRIGGER graph_node_fts_au AFTER UPDATE OF label ON graph_nodes BEGIN "
        "DELETE FROM graph_node_fts WHERE node_key = old.node_key; "
        "INSERT INTO graph_node_fts(node_key, label) VALUES (new.node_key, new.label); END"
    )
    op.execute(
        "INSERT INTO graph_node_fts(node_key, label) SELECT node_key, label FROM graph_nodes"
    )
