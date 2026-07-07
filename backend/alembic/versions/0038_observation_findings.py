"""v1.4.8: observation ↔ finding links

Many-to-many join so an analyst can attach an observation to the
findings it supports ("this domain is hardened — see findings 1,2,3"),
and the finding slide-over can show the observations that reference it
back. Roadmap item #11.

The link table cascades both ways: deleting an observation or a finding
removes the link rows automatically, so no dangling refs. Composite PK
(observation_id, finding_id) makes repeated link calls idempotent at
the DB layer.

Revision ID: 0038
Revises: 0037
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "observation_findings",
        sa.Column(
            "observation_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("observations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("observation_id", "finding_id"),
    )
    op.create_index(
        "ix_observation_findings_finding_id",
        "observation_findings",
        ["finding_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_observation_findings_finding_id", table_name="observation_findings"
    )
    op.drop_table("observation_findings")
