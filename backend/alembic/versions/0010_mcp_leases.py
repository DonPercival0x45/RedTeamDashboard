"""mcp_leases — per-task curated MCP surface lifecycle.

Stage 1 of the per-task MCP composition feature: Strategic mints a lease
per dispatched Task specifying the tools/context/prompts the Execution
Agent is allowed to use; the lease is the authoritative store for the
``X-Lease-Token`` the worker carries on its envelope.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-22
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_leases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "allowed_tools",
            postgresql.JSONB(),
            nullable=False,
        ),
        sa.Column(
            "context",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "prompt_keys",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "released_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'released', 'expired')",
            name="ck_mcp_leases_status",
        ),
    )
    op.create_index(
        "ix_mcp_leases_task_id",
        "mcp_leases",
        ["task_id"],
    )
    op.create_index(
        "ix_mcp_leases_engagement_status",
        "mcp_leases",
        ["engagement_id", "status"],
    )
    op.create_index(
        "ix_mcp_leases_expires_at",
        "mcp_leases",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_mcp_leases_expires_at", table_name="mcp_leases")
    op.drop_index("ix_mcp_leases_engagement_status", table_name="mcp_leases")
    op.drop_index("ix_mcp_leases_task_id", table_name="mcp_leases")
    op.drop_table("mcp_leases")
