"""agent execution cancelled status

Revision ID: 0042_agent_execution_cancelled
Revises: 0041
Create Date: 2026-07-09
"""
from __future__ import annotations

from alembic import op

revision = "0042_agent_execution_cancelled"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE agent_execution_status ADD VALUE IF NOT EXISTS 'cancelled'")


def downgrade() -> None:
    # PostgreSQL cannot drop enum values safely without recreating the type.
    # Keep the value; old app versions simply won't emit it.
    pass
