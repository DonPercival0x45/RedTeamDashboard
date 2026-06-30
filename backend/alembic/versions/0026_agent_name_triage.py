"""Add 'triage' to agent_name enum

v0.7.0 adds an AI Triage button on the Findings slide-over: the analyst
clicks it, the LLM writes a 2-4 sentence summary of the finding, and the
text drops into the Summary textarea (analyst then edits + saves manually).
Each call books an ``AgentExecution`` row so the Costs tab roll-up keeps
its single accounting view of LLM spend. The new agent value carries that
row.

Revision ID: 0026
Revises: 0025
"""

from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE agent_name ADD VALUE IF NOT EXISTS 'triage'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enum types short of recreating them.
    # Leave the symbol in place on downgrade.
    pass
