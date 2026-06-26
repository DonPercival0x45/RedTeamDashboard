"""Add ``needs_review`` to the ``finding_status`` enum.

Reserved for the upcoming confirmation-tool-run flow (Piece B of the
phase-based-validation arc): when an analyst clicks Validate on a
manual-tier finding and the backend dispatches a follow-up tool run, a
failed or dead-target confirmation drops the row to ``needs_review``
instead of promoting it to ``validated``. No code writes this value yet
in Piece A; the schema lands now so the column accepts it the moment
the writer ships.

Idempotent via ``ADD VALUE IF NOT EXISTS`` so reruns + back-fills against
older heads are safe.

Revision ID: 0016_finding_status_needs_review
Revises: 0015_entities_table
"""
from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE finding_status ADD VALUE IF NOT EXISTS 'needs_review'")


def downgrade() -> None:
    # Postgres doesn't support removing enum values without rebuilding the
    # type. We deliberately leave the value in place on downgrade — same
    # pattern as 0012_agent_trigger_lease_provision.
    pass
