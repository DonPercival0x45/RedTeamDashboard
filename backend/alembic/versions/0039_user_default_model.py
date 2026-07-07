"""v1.4.11: user default LLM provider + model

Per-analyst default model selection (roadmap #3 / #12). Lets an analyst
pin their preferred (provider, model) once on the Keys settings page so
the Start-a-run prompt pre-selects it on every engagement instead of
resetting to the hardcoded Anthropic default each run.

Nullable — users who never set one keep falling back to the built-in
default. No not-null / no default so existing rows backfill to NULL.

Revision ID: 0039
Revises: 0038
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0039"
down_revision = "0038"
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
