"""findings.observed_at — separate from created_at (ingestion time)

Tracks when the issue was *actually* observed in a scan, vs when the
dashboard ingested it (``created_at``). Set by the Burp / Nessus importers
from the scan export's own timestamp, and editable by the analyst on a
finding. Nullable: old rows pre-date the column, and not every finding
has a scan-side date (manual finds, OSINT pulls).

Revision ID: 0024
Revises: 0023
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("findings", "observed_at")
