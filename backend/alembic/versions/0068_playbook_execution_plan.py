"""persist authoritative playbook execution plans

Revision ID: 0068
Revises: 0067
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE playbook_runs ADD COLUMN plan_snapshot JSONB")
    op.execute("ALTER TABLE playbook_runs ADD COLUMN plan_sha256 VARCHAR(64)")
    op.execute("ALTER TABLE playbook_runs ADD COLUMN planned_at TIMESTAMPTZ")
    op.execute("CREATE INDEX ix_playbook_runs_plan_sha256 ON playbook_runs (plan_sha256)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_playbook_runs_plan_sha256")
    op.execute("ALTER TABLE playbook_runs DROP COLUMN IF EXISTS planned_at")
    op.execute("ALTER TABLE playbook_runs DROP COLUMN IF EXISTS plan_sha256")
    op.execute("ALTER TABLE playbook_runs DROP COLUMN IF EXISTS plan_snapshot")
