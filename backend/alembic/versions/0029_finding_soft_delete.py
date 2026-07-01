"""v0.10.0 finding soft-delete

Adds two nullable columns to ``findings`` so an analyst can retract a
finding without losing the row:

- ``deleted_at`` — nullable timestamp. NULL = alive. Set = hidden from
  the Findings list, Report, JSON export, and MCP surface.
- ``deleted_by_user_id`` — FK ``users.id`` (ON DELETE SET NULL) so a
  future admin recovery view can show *who* deleted it.

Partial index ``ix_findings_deleted_at`` on ``deleted_at`` speeds up the
future "recover" query without slowing down the hot list path (which
still uses ``ix_findings_engagement_id``).

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "findings",
        sa.Column("deleted_by_user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_findings_deleted_by_user_id",
        "findings",
        "users",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_findings_deleted_at",
        "findings",
        ["deleted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_findings_deleted_at", table_name="findings")
    op.drop_constraint("fk_findings_deleted_by_user_id", "findings", type_="foreignkey")
    op.drop_column("findings", "deleted_by_user_id")
    op.drop_column("findings", "deleted_at")
