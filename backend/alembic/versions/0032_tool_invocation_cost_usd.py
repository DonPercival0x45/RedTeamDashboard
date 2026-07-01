"""v0.15.0 add cost_usd to tool_invocations

Adds a nullable numeric column so the invocation orchestrator can
stamp per-run compute cost estimated from ``duration_seconds`` times
a runner-specific rate. LocalDocker sits at $0/sec (free local infra);
ACIRunner sits at ~$0.00002/sec (1 vCPU + 512 MiB region-average with
load-balancer overhead — pricing lookup itself slips to v0.17).

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tool_invocations",
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tool_invocations", "cost_usd")
