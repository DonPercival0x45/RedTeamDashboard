"""v3 B6 — per-engagement intelligence architecture.

Existing engagements remain legacy. New callers may explicitly create v3
engagements once they atomically select a methodology; the database default
stays legacy for backward-compatible non-API inserts and staged rollout.
"""
from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE engagement_intelligence_architecture "
        "AS ENUM ('legacy', 'v3')"
    )
    op.execute(
        "ALTER TABLE engagements "
        "ADD COLUMN intelligence_architecture "
        "engagement_intelligence_architecture NOT NULL DEFAULT 'legacy'"
    )
    op.execute(
        "ALTER TABLE engagements "
        "ADD COLUMN converted_to_v3_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "CREATE INDEX ix_engagements_intelligence_architecture "
        "ON engagements (intelligence_architecture)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_engagements_intelligence_architecture")
    op.execute("ALTER TABLE engagements DROP COLUMN IF EXISTS converted_to_v3_at")
    op.execute(
        "ALTER TABLE engagements "
        "DROP COLUMN IF EXISTS intelligence_architecture"
    )
    op.execute("DROP TYPE IF EXISTS engagement_intelligence_architecture")
