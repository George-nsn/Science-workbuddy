"""Store embeddings as compact float32 BLOB values.

Revision ID: 20260805_0022
Revises: 20260805_0021
Create Date: 2026-08-05
"""
import json
import math
import struct
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0022"
down_revision: str | None = "20260805_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INSERT_TRIGGER = "trg_chunk_embeddings_validate_insert"
_UPDATE_TRIGGER = "trg_chunk_embeddings_validate_update"


def _decoded_json_vector(value: Any) -> list[float]:
    decoded = json.loads(value) if isinstance(value, str | bytes | bytearray) else value
    if not isinstance(decoded, list):
        raise ValueError("Existing embedding is not a JSON array")
    vector = [float(component) for component in decoded]
    if not all(math.isfinite(component) for component in vector):
        raise ValueError("Existing embedding contains a non-finite value")
    return vector


def _create_validation_triggers() -> None:
    op.execute(
        f"""
        CREATE TRIGGER {_INSERT_TRIGGER}
        BEFORE INSERT ON chunk_embeddings
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1 FROM embedding_models em WHERE em.id = NEW.model_id
        ) OR length(NEW.vector) != COALESCE((
            SELECT em.dimension * 4 FROM embedding_models em WHERE em.id = NEW.model_id
        ), -1)
        BEGIN
            SELECT RAISE(ABORT, 'compact vector dimension does not match embedding model');
        END
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {_UPDATE_TRIGGER}
        BEFORE UPDATE OF model_id, vector ON chunk_embeddings
        FOR EACH ROW
        WHEN NOT EXISTS (
            SELECT 1 FROM embedding_models em WHERE em.id = NEW.model_id
        ) OR length(NEW.vector) != COALESCE((
            SELECT em.dimension * 4 FROM embedding_models em WHERE em.id = NEW.model_id
        ), -1)
        BEGIN
            SELECT RAISE(ABORT, 'compact vector dimension does not match embedding model');
        END
        """
    )


def upgrade() -> None:
    op.add_column("chunk_embeddings", sa.Column("vector_blob", sa.LargeBinary(), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT ce.id, ce.vector, em.dimension "
            "FROM chunk_embeddings ce "
            "JOIN embedding_models em ON em.id = ce.model_id"
        )
    ).all()
    for embedding_id, stored_vector, dimension in rows:
        vector = _decoded_json_vector(stored_vector)
        if len(vector) != int(dimension):
            raise ValueError(
                f"Embedding {embedding_id} has dimension {len(vector)}, expected {dimension}"
            )
        blob = struct.pack(f"<{len(vector)}f", *vector)
        bind.execute(
            sa.text(
                "UPDATE chunk_embeddings SET vector_blob = :blob WHERE id = :embedding_id"
            ),
            {"blob": blob, "embedding_id": embedding_id},
        )

    with op.batch_alter_table("chunk_embeddings") as batch_op:
        batch_op.drop_column("vector")
        batch_op.alter_column(
            "vector_blob",
            new_column_name="vector",
            existing_type=sa.LargeBinary(),
            nullable=False,
        )
        batch_op.create_check_constraint(
            "ck_chunk_embeddings_vector_not_empty",
            "length(vector) > 0",
        )
    _create_validation_triggers()


def downgrade() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {_UPDATE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {_INSERT_TRIGGER}")
    op.add_column("chunk_embeddings", sa.Column("vector_json", sa.JSON(), nullable=True))
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT ce.id, ce.vector, em.dimension "
            "FROM chunk_embeddings ce "
            "JOIN embedding_models em ON em.id = ce.model_id"
        )
    ).all()
    for embedding_id, stored_vector, dimension in rows:
        blob = bytes(stored_vector)
        expected_bytes = int(dimension) * 4
        if len(blob) != expected_bytes:
            raise ValueError(
                f"Embedding {embedding_id} uses {len(blob)} bytes, expected {expected_bytes}"
            )
        vector = list(struct.unpack(f"<{dimension}f", blob))
        bind.execute(
            sa.text(
                "UPDATE chunk_embeddings SET vector_json = :vector "
                "WHERE id = :embedding_id"
            ),
            {"vector": json.dumps(vector), "embedding_id": embedding_id},
        )

    with op.batch_alter_table("chunk_embeddings") as batch_op:
        batch_op.drop_constraint(
            "ck_chunk_embeddings_vector_not_empty",
            type_="check",
        )
        batch_op.drop_column("vector")
        batch_op.alter_column(
            "vector_json",
            new_column_name="vector",
            existing_type=sa.JSON(),
            nullable=False,
        )
