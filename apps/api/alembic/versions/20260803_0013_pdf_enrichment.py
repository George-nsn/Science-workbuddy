"""Track local PDF summary provenance.

Revision ID: 20260803_0013
Revises: 20260803_0012
Create Date: 2026-08-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260803_0013"
down_revision: str | None = "20260803_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("papers") as batch_op:
        batch_op.add_column(sa.Column("abstract_source", sa.String(length=64)))


def downgrade() -> None:
    with op.batch_alter_table("papers") as batch_op:
        batch_op.drop_column("abstract_source")