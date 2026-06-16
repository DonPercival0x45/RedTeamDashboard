"""engagements: free-text description

Adds an optional ``description`` column set on the Nessus-style engagement
setup page (Phase 8c) — rules of engagement, objectives, notes.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-16
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("engagements", sa.Column("description", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("engagements", "description")
