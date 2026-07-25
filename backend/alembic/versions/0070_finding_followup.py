"""Add append-only finding remediation and retest tracking.

Revision ID: 0070
Revises: 0069
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    remediation_status = postgresql.ENUM(
        "acknowledged",
        "in_progress",
        "ready_for_retest",
        "client_reports_fixed",
        "accepted_risk",
        name="finding_remediation_status",
        create_type=False,
    )
    retest_outcome = postgresql.ENUM(
        "fixed",
        "partially_fixed",
        "not_fixed",
        "inconclusive",
        name="finding_retest_outcome",
        create_type=False,
    )
    remediation_status.create(op.get_bind(), checkfirst=True)
    retest_outcome.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "finding_remediation_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", remediation_status, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_finding_remediation_updates_finding_id", "finding_remediation_updates", ["finding_id"])
    op.create_index(
        "ix_finding_remediation_updates_timeline",
        "finding_remediation_updates",
        ["finding_id", "reported_at", "created_at"],
    )

    op.create_table(
        "finding_retests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", retest_outcome, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("performed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["performed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_finding_retests_finding_id", "finding_retests", ["finding_id"])
    op.create_index(
        "ix_finding_retests_timeline",
        "finding_retests",
        ["finding_id", "tested_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("finding_retests")
    op.drop_table("finding_remediation_updates")
    postgresql.ENUM(name="finding_retest_outcome").drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name="finding_remediation_status").drop(op.get_bind(), checkfirst=True)
