"""agent_trigger += 'lease_provision' — Stage 3 lease policy executions.

Postgres enum extension so Strategic can write an AgentExecution row
for every per-lease policy LLM call. The new trigger value lets the
Costs tab and audit trail distinguish lease-mint decisions from
finding-analysis runs.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-24
"""
from __future__ import annotations

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres can't add enum values inside a transaction by default;
    # alembic wraps everything in one, so we need autocommit-friendly
    # syntax. IF NOT EXISTS makes the migration idempotent on re-runs.
    op.execute(
        "ALTER TYPE agent_trigger ADD VALUE IF NOT EXISTS 'lease_provision'"
    )


def downgrade() -> None:
    # Postgres has no DROP VALUE for enums. A real downgrade would
    # recreate the type without the value AND remap every column using
    # it. We accept this asymmetry — the value lingering in the type is
    # harmless if no code references it.
    pass
