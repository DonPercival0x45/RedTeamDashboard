"""add pending status for durable intelligence jobs

Revision ID: 0066
Revises: 0065
Create Date: 2026-07-25
"""
from __future__ import annotations

from alembic import op

revision = "0066"
down_revision = "0065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE agent_execution_status ADD VALUE IF NOT EXISTS 'pending'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely while rows may reference
    # them. Keeping the value is backward-compatible with the prior schema.
    pass
