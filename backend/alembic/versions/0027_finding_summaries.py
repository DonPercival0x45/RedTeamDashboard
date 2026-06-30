"""finding_summaries — immutable history of every saved summary

v0.7.0. Until now ``findings.summary`` was the single mutable narrative
field on a finding; every Save Summary click overwrote it. Two analysts
working the same engagement on different days couldn't see what the
other had previously written. This migration adds an immutable history
table — every Save Summary inserts a row, and ``findings.summary`` is
kept as a denormalized cache of the most recent body (so the Report
tab, JSON export, and MCP server can keep reading the latest narrative
without joining).

Indexed on ``(finding_id, created_at DESC)`` so the slide-over fetches
the per-finding history in one indexed scan.

Revision ID: 0027
Revises: 0026
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_summaries",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_finding_summaries_finding_created",
        "finding_summaries",
        ["finding_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_finding_summaries_finding_created", table_name="finding_summaries"
    )
    op.drop_table("finding_summaries")
