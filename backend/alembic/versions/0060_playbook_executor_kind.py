"""v3 A4 — playbook_runs.executor_kind

Records which executor (internal in-process vs MCP out-of-process) the
worker should use to drive a playbook run. Existing rows default to
``internal`` — that's what they ran on before A4, and the migration
backfills them so ``NOT NULL`` doesn't break upgrade.

Per-step executor pick (via ``work_items.disposition``) lands with a
later convergence step; A4 v0 keeps the choice at the run level so the
API caller / analyst decides once.
"""
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE playbook_executor_kind AS ENUM ('internal', 'mcp')"
    )
    op.execute(
        "ALTER TABLE playbook_runs "
        "ADD COLUMN executor_kind playbook_executor_kind "
        "NOT NULL DEFAULT 'internal'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE playbook_runs DROP COLUMN IF EXISTS executor_kind")
    op.execute("DROP TYPE IF EXISTS playbook_executor_kind")
