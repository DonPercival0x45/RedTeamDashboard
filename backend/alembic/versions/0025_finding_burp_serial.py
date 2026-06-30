"""findings.burp_serial_number — dedup key for the Burp Pro XML importer

Burp's ``<serialNumber>`` is a stable per-issue identifier in an Issue
Export. The importer stamps it on each Finding so re-imports of the
same export (or a re-scan that re-emits the same serials) skip rows
that were already created. Indexed for the dedup lookup.

Nullable because every Finding from a non-Burp source has no serial.
The unique constraint is composite (engagement_id, burp_serial_number)
so two engagements can each carry a finding with the same Burp serial
without collision.

Revision ID: 0025
Revises: 0024
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("burp_serial_number", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_findings_burp_serial",
        "findings",
        ["engagement_id", "burp_serial_number"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_findings_burp_serial", table_name="findings")
    op.drop_column("findings", "burp_serial_number")
