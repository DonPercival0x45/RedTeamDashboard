"""v1.4.11: user default LLM provider + model

Per-analyst default model selection (roadmap #3 / #12). Lets an analyst
pin their preferred (provider, model) once on the Keys settings page so
the Start-a-run prompt pre-selects it on every engagement instead of
resetting to the hardcoded Anthropic default each run.

Nullable — users who never set one keep falling back to the built-in
default. No not-null / no default so existing rows backfill to NULL.

Revision ID: 0040
Revises: 0039_tool_example_prompt
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# v1.13.0: renumbered 0039 -> 0040 during bundle A cherry-pick; 0039 was
# taken by tool_example_prompt (v1.11.0). Down-revision points at that
# so the chain stays linear.
revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("default_llm_provider", sa.String(60), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("default_llm_model", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "default_llm_model")
    op.drop_column("users", "default_llm_provider")
