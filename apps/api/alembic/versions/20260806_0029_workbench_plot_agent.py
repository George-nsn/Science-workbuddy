"""Add provenance fields for locally generated workbench plots.

Revision ID: 20260806_0029
Revises: 20260806_0028
Create Date: 2026-08-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260806_0029"
down_revision: str | None = "20260806_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "workbench_attachments",
        sa.Column(
            "attachment_kind",
            sa.String(length=32),
            nullable=False,
            server_default="uploaded_image",
        ),
    )
    op.add_column(
        "workbench_attachments",
        sa.Column("generator", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "workbench_attachments",
        sa.Column("source_table_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "workbench_attachments",
        sa.Column("source_table_markdown", sa.Text(), nullable=True),
    )
    op.add_column(
        "workbench_attachments",
        sa.Column(
            "render_spec",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "workbench_attachments",
        sa.Column("caption", sa.Text(), nullable=True),
    )
    op.add_column(
        "workbench_attachments",
        sa.Column(
            "render_revision",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_workbench_attachments_source_table_hash",
        "workbench_attachments",
        ["source_table_hash"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workbench_attachments_source_table_hash",
        table_name="workbench_attachments",
    )
    op.drop_column("workbench_attachments", "caption")
    op.drop_column("workbench_attachments", "render_revision")
    op.drop_column("workbench_attachments", "render_spec")
    op.drop_column("workbench_attachments", "source_table_markdown")
    op.drop_column("workbench_attachments", "source_table_hash")
    op.drop_column("workbench_attachments", "generator")
    op.drop_column("workbench_attachments", "attachment_kind")