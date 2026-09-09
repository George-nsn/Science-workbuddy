"""Add bilingual retrieval indexes and reproducibility fields.

Revision ID: 20260802_0004
Revises: 20260801_0003
Create Date: 2026-08-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260802_0004"
down_revision: str | None = "20260801_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sqlite = op.get_bind().dialect.name == "sqlite"
    if sqlite:
        op.execute(
            "CREATE VIRTUAL TABLE chunk_fts_english USING fts5("
            "chunk_id UNINDEXED, text, tokenize='porter unicode61')"
        )
        op.execute(
            "CREATE VIRTUAL TABLE chunk_fts_simple USING fts5("
            "chunk_id UNINDEXED, text, tokenize='unicode61 remove_diacritics 2')"
        )
        op.execute(
            "CREATE VIRTUAL TABLE paper_fts USING fts5("
            "paper_id UNINDEXED, title, abstract, journal, "
            "tokenize='porter unicode61')"
        )
        op.execute(
            "CREATE VIRTUAL TABLE mesh_fts USING fts5("
            "descriptor_ui UNINDEXED, label, tokenize='porter unicode61')"
        )
        for table in ("chunk_fts_english", "chunk_fts_simple"):
            op.execute(
                f"CREATE TRIGGER {table}_ai AFTER INSERT ON chunks BEGIN "
                f"INSERT INTO {table}(chunk_id, text) VALUES (new.id, new.text); END"
            )
            op.execute(
                f"CREATE TRIGGER {table}_ad AFTER DELETE ON chunks BEGIN "
                f"DELETE FROM {table} WHERE chunk_id = old.id; END"
            )
            op.execute(
                f"CREATE TRIGGER {table}_au AFTER UPDATE OF text ON chunks BEGIN "
                f"DELETE FROM {table} WHERE chunk_id = old.id; "
                f"INSERT INTO {table}(chunk_id, text) VALUES (new.id, new.text); END"
            )
            op.execute(f"INSERT INTO {table}(chunk_id, text) SELECT id, text FROM chunks")
        op.execute(
            "CREATE TRIGGER paper_fts_ai AFTER INSERT ON papers BEGIN "
            "INSERT INTO paper_fts(paper_id, title, abstract, journal) "
            "VALUES (new.id, new.title, new.abstract, new.journal); END"
        )
        op.execute(
            "CREATE TRIGGER paper_fts_ad AFTER DELETE ON papers BEGIN "
            "DELETE FROM paper_fts WHERE paper_id = old.id; END"
        )
        op.execute(
            "CREATE TRIGGER paper_fts_au AFTER UPDATE OF title, abstract, journal ON papers "
            "BEGIN DELETE FROM paper_fts WHERE paper_id = old.id; "
            "INSERT INTO paper_fts(paper_id, title, abstract, journal) "
            "VALUES (new.id, new.title, new.abstract, new.journal); END"
        )
        op.execute(
            "INSERT INTO paper_fts(paper_id, title, abstract, journal) "
            "SELECT id, title, abstract, journal FROM papers"
        )
        op.execute(
            "CREATE TRIGGER mesh_fts_ai AFTER INSERT ON mesh_terms BEGIN "
            "INSERT INTO mesh_fts(descriptor_ui, label) "
            "VALUES (new.descriptor_ui, new.preferred_label); END"
        )
        op.execute(
            "CREATE TRIGGER mesh_fts_ad AFTER DELETE ON mesh_terms BEGIN "
            "DELETE FROM mesh_fts WHERE descriptor_ui = old.descriptor_ui; END"
        )
        op.execute(
            "CREATE TRIGGER mesh_fts_au AFTER UPDATE OF preferred_label ON mesh_terms "
            "BEGIN DELETE FROM mesh_fts WHERE descriptor_ui = old.descriptor_ui; "
            "INSERT INTO mesh_fts(descriptor_ui, label) "
            "VALUES (new.descriptor_ui, new.preferred_label); END"
        )
        op.execute(
            "INSERT INTO mesh_fts(descriptor_ui, label) "
            "SELECT descriptor_ui, preferred_label FROM mesh_terms"
        )
    else:
        op.execute(
            "CREATE INDEX ix_chunks_text_simple_fts ON chunks USING gin "
            "(to_tsvector('simple'::regconfig, coalesce(text, ''::text)))"
        )
        op.execute(
            "CREATE INDEX ix_papers_metadata_english_fts ON papers USING gin "
            "(to_tsvector('english'::regconfig, "
            "coalesce(title, ''::text) || ' '::text || "
            "coalesce(abstract, ''::text) || ' '::text || "
            "coalesce(journal, ''::text)))"
        )
        op.execute(
            "CREATE INDEX ix_mesh_terms_label_english_fts ON mesh_terms USING gin "
            "(to_tsvector('english'::regconfig, preferred_label))"
        )
    op.create_index("ix_chunks_previous_chunk_id", "chunks", ["previous_chunk_id"])
    op.create_index("ix_chunks_next_chunk_id", "chunks", ["next_chunk_id"])
    op.add_column(
        "research_runs",
        sa.Column(
            "retrieval_config",
            sa.JSON(),
            server_default=sa.text("'{}'" if sqlite else "'{}'::json"),
            nullable=False,
        ),
    )
    op.add_column(
        "research_runs",
        sa.Column(
            "retrieval_trace",
            sa.JSON(),
            server_default=sa.text("'{}'" if sqlite else "'{}'::json"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("research_runs", "retrieval_trace")
    op.drop_column("research_runs", "retrieval_config")
    op.drop_index("ix_chunks_next_chunk_id", table_name="chunks")
    op.drop_index("ix_chunks_previous_chunk_id", table_name="chunks")
    if op.get_bind().dialect.name == "sqlite":
        for trigger in (
            "chunk_fts_english_ai", "chunk_fts_english_ad", "chunk_fts_english_au",
            "chunk_fts_simple_ai", "chunk_fts_simple_ad", "chunk_fts_simple_au",
            "paper_fts_ai", "paper_fts_ad", "paper_fts_au",
            "mesh_fts_ai", "mesh_fts_ad", "mesh_fts_au",
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        for table in ("chunk_fts_english", "chunk_fts_simple", "paper_fts", "mesh_fts"):
            op.execute(f"DROP TABLE IF EXISTS {table}")
    else:
        op.execute("DROP INDEX IF EXISTS ix_mesh_terms_label_english_fts")
        op.execute("DROP INDEX IF EXISTS ix_papers_metadata_english_fts")
        op.execute("DROP INDEX IF EXISTS ix_chunks_text_simple_fts")
