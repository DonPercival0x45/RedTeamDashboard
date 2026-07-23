"""Add 'stub' coverage status so placeholder tools don't falsely satisfy baseline.

Revision ID: 0064
Revises: 0063
"""
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE coverage_record_status ADD VALUE IF NOT EXISTS 'stub'"
    )


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value without a full type rebuild.
    # The 'stub' value is harmless if unused; leaving it is safer than
    # rebuilding the type under load.
    pass
