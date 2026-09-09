"""Raise new brainstorm defaults to DeepSeek 1M context and max reasoning.

Revision ID: 20260806_0026
Revises: 20260805_0025
Create Date: 2026-08-06
"""

from collections.abc import Sequence

revision: str = "20260806_0026"
down_revision: str | None = "20260805_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The API explicitly persists max/1M for new sessions. Existing sessions retain
    # their chosen depth and context budget; no destructive SQLite table rewrite.
    pass


def downgrade() -> None:
    pass
