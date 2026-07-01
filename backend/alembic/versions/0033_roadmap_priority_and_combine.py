"""v0.16.0 roadmap_suggestions: priority + combined_into_id

Adds the two columns the feedback prioritization arc needs:

- ``priority INTEGER`` (nullable, CHECK 1..10) — analyst-set or
  LLM-set per-row priority. 1 = highest (intentionally inverted from
  the "high number = high priority" convention — memory-locked to
  avoid drift). NULL = unranked.
- ``combined_into_id UUID`` (nullable, FK to ``roadmap_suggestions.id``,
  ON DELETE SET NULL) — when set, this row was merged into another
  row and should be hidden from the default list. Preserves audit
  (row not deleted).

Partial index on ``priority`` speeds up the filter-by-priority path
that the frontend uses when the user picks 1-3 / 4-6 / 7-10 chips.

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "roadmap_suggestions",
        sa.Column("priority", sa.Integer, nullable=True),
    )
    op.add_column(
        "roadmap_suggestions",
        sa.Column("combined_into_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_roadmap_suggestions_combined_into_id",
        "roadmap_suggestions",
        "roadmap_suggestions",
        ["combined_into_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_roadmap_suggestions_priority_range",
        "roadmap_suggestions",
        "priority IS NULL OR (priority BETWEEN 1 AND 10)",
    )
    op.create_index(
        "ix_roadmap_suggestions_priority",
        "roadmap_suggestions",
        ["priority"],
        postgresql_where=sa.text("priority IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_roadmap_suggestions_priority",
        table_name="roadmap_suggestions",
    )
    op.drop_constraint(
        "ck_roadmap_suggestions_priority_range",
        "roadmap_suggestions",
        type_="check",
    )
    op.drop_constraint(
        "fk_roadmap_suggestions_combined_into_id",
        "roadmap_suggestions",
        type_="foreignkey",
    )
    op.drop_column("roadmap_suggestions", "combined_into_id")
    op.drop_column("roadmap_suggestions", "priority")
