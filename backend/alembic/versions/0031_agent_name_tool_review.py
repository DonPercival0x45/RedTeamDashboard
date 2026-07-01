"""v0.13.0 add 'tool_review' to agent_name enum

Adds the new AgentName value used by the tool-upload LLM safety review
service (:mod:`app.services.tool_llm_review`). Every review emits one
``AgentExecution`` row so the Costs tab can attribute its spend the
same way it already handles Strategic / Tactical / Planner / Triage.

Postgres ``ADD VALUE`` cannot run inside a transaction block; we
disable the wrapper for the upgrade.

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-01
"""
from __future__ import annotations

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE agent_name ADD VALUE IF NOT EXISTS 'tool_review'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a full type
    # rename dance. Down-migration is a no-op; the enum value simply
    # becomes unused if we back out.
    pass
