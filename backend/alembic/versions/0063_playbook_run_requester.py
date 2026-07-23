"""Persist the analyst who requested a playbook run.

Revision ID: 0063
Revises: 0062
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "playbook_runs",
        sa.Column(
            "requested_by",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_playbook_runs_requested_by_users",
        "playbook_runs",
        "users",
        ["requested_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_playbook_runs_requested_by",
        "playbook_runs",
        ["requested_by"],
    )


def downgrade() -> None:
    op.drop_index("ix_playbook_runs_requested_by", table_name="playbook_runs")
    op.drop_constraint(
        "fk_playbook_runs_requested_by_users",
        "playbook_runs",
        type_="foreignkey",
    )
    op.drop_column("playbook_runs", "requested_by")
