"""unique open suggestion proposal keys

Revision ID: 0047
Revises: 0046
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_suggestions_open_proposal_key",
        "suggestions",
        ["engagement_id", "kind", "proposal_key"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND proposal_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_suggestions_open_proposal_key", table_name="suggestions")
