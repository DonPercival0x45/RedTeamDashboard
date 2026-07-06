"""v1.4.0 (part 2): finding.group_key + partial unique index

Nessus-style ingest grouping (v1.4.0). Each tool wrapper declares a
category vocabulary and hits fold into ONE row per
``(engagement_id, group_key)`` with the per-hit records living inside
``data.items[]``. Old un-grouped rows keep ``group_key IS NULL`` and are
unaffected — this is a strictly additive change.

A **partial** unique index (``WHERE group_key IS NOT NULL``) enforces
the upsert invariant: two rows with the same non-null group_key in one
engagement is a bug. Nulls are exempt so the pre-existing per-hit rows
stay legal.

Revision ID: 0036
Revises: 0035
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("group_key", sa.String(length=200), nullable=True),
    )
    # Partial unique so pre-existing rows (group_key IS NULL) stay legal.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ix_findings_engagement_group_unique
        ON findings (engagement_id, group_key)
        WHERE group_key IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_findings_engagement_group_unique")
    op.drop_column("findings", "group_key")
