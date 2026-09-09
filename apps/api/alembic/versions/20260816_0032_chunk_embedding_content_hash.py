"""Track the chunk content hash used to build each embedding.

Revision ID: 20260816_0032
Revises: 20260812_0031
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260816_0032"
down_revision: str | None = "20260812_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("chunk_embeddings") as batch_op:
        batch_op.add_column(
            sa.Column("content_hash", sa.String(64), nullable=False, server_default="")
        )
    # Backfill from the owning chunk; every embedding row has a chunk via FK
    # cascade, so the column is fully populated afterwards.
    op.execute(
        "UPDATE chunk_embeddings SET content_hash = "
        "(SELECT chunks.content_hash FROM chunks WHERE chunks.id = chunk_embeddings.chunk_id)"
    )


def downgrade() -> None:
    with op.batch_alter_table("chunk_embeddings") as batch_op:
        batch_op.drop_column("content_hash")
