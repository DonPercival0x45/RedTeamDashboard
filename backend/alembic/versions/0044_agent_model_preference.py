"""per-user per-engagement agent model preference

Adds the ``agent_model_preference`` table backing Settings > Configurations.
Each row pins which LLM model a specific analyst wants their Strategic /
Tactical / Correlate agent to use on one engagement. Resolution at run
time chains: preference row -> users.default_model -> agent's hardcoded
default. Keys are still resolved from the analyst's ephemeral BYO cache
independently.

Revision ID: 0044
Revises: 0043
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_model_preference",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_role",
            postgresql.ENUM(
                name="agent_name",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("model", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "engagement_id",
            "agent_role",
            name="uq_agent_model_pref_user_engagement_role",
        ),
    )
    op.create_index(
        "ix_agent_model_pref_user_engagement",
        "agent_model_preference",
        ["user_id", "engagement_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_model_pref_user_engagement",
        table_name="agent_model_preference",
    )
    op.drop_table("agent_model_preference")
