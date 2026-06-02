"""authorizations: per-(engagement, tool) session grants

A standing approval that lets the gate auto-approve in-scope calls to a tool
for an engagement instead of interrupting for a human. A partial unique index
enforces at most one *active* (revoked_at IS NULL) grant per (engagement, tool).

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authorizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("tool_name", sa.String(200), nullable=False, index=True),
        sa.Column(
            "granted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("note", sa.String(500)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "revoked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # At most one active grant per (engagement, tool).
    op.create_index(
        "uq_active_authorization",
        "authorizations",
        ["engagement_id", "tool_name"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_authorization", table_name="authorizations")
    op.drop_table("authorizations")
