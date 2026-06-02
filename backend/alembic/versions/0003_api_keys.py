"""api_keys: per-deployment API key auth surface

A small table that replaces the dev-time X-User-Id header in production. The
kit's installer mints the first ``admin`` key after this migration runs; that
key can then issue more scoped keys via POST /api-keys.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-01
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        # SHA-256 hex digest (64 chars). Indexed unique so auth is one row lookup.
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column(
            "scope",
            sa.Enum("viewer", "cli", "admin", name="api_key_scope"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "revoked_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
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


def downgrade() -> None:
    op.drop_table("api_keys")
    op.execute("DROP TYPE IF EXISTS api_key_scope")
