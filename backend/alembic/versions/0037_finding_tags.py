"""v1.4.7: finding.tags — free-form analyst tagging

Adds a JSONB list of free-form string tags to each finding so analysts
can correlate and filter across the findings table (the foundation
roadmap #1 "Correlate Findings via tagging" was really after).

``ARRAY(text)`` was considered but rejected in favour of JSONB to match
the rest of the schema (``details``, ``payload``, ``tool_args`` etc. are
all JSONB) and avoid SQLAlchemy ``MutableList`` change-tracking
complexity — the update path replaces the whole list, so plain JSONB is
simpler and consistent. Existing rows backfill to ``[]``.

Revision ID: 0037
Revises: 0036
"""
from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column(
            "tags",
            sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # GIN index so ``tags @> '["xss"]'`` containment lookups stay cheap
    # once a server-side tag filter lands.
    op.execute(
        "CREATE INDEX ix_findings_tags ON findings USING GIN (tags)"
    )


def downgrade() -> None:
    op.drop_index("ix_findings_tags", table_name="findings")
    op.drop_column("findings", "tags")
