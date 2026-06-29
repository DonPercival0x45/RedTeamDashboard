"""integrations table + roadmap_suggestions.source

v0.5.0 QoL — bidirectional Discord wiring.

- ``integrations`` table holds one config row per external system the
  tenant wires up (Discord first; Slack/Teams later). Single-tenant by
  design: at most one row per ``type``.
- ``roadmap_suggestions.source`` tracks where the row came from so the
  outbound Discord webhook can skip Discord-originated rows (loop
  prevention).

Revision ID: 0020
Revises: 0019
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "type",
            sa.Enum("discord", name="integration_type"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.add_column(
        "roadmap_suggestions",
        sa.Column(
            "source",
            sa.String(40),
            nullable=False,
            server_default="ui",
        ),
    )


def downgrade() -> None:
    op.drop_column("roadmap_suggestions", "source")
    op.drop_table("integrations")
    op.execute("DROP TYPE IF EXISTS integration_type")
