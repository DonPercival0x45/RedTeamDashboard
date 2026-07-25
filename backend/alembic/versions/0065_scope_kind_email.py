"""Add email as a first-class engagement scope kind.

Revision ID: 0065
Revises: 0064
"""
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE scope_kind ADD VALUE IF NOT EXISTS 'email'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value without rebuilding the type and
    # every dependent column. Leaving the additive label is the safe rollback.
    pass
