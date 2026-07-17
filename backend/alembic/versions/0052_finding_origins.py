"""finding_origins — explicit run→finding lineage

Revision ID: 0052
Revises: 0051

Replaces the fragile ``finding.details["thread_id"]`` JSON convention with an
explicit link table, so findings can be filtered by the run (graph thread) that
produced them and Status can link "Produced N findings" to exact rows.

A finding can have many origins (a grouped finding folds items from multiple
runs) and a run produces many findings — both directions are queried.
``finding_id`` CASCADEs (origin dies with the finding); ``agent_execution_id``
is SET NULL (the origin survives as a tombstone if the execution row is
pruned). ``thread_id`` is the durable run handle (matches the langgraph
checkpointer thread + the worker run envelope).

Idempotency: a unique constraint on (finding_id, thread_id, source_tool) makes
re-processing the same run idempotent (ON CONFLICT DO NOTHING at the write
site) while still preserving distinct origins across runs. Postgres treats a
NULL source_tool as distinct in a unique constraint — acceptable; virtually
every finding carries a source tool.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finding_origins",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "agent_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_tool", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_finding_origins_thread_id", "finding_origins", ["thread_id"])
    op.create_index("ix_finding_origins_finding_id", "finding_origins", ["finding_id"])
    op.create_unique_constraint(
        "uq_finding_origins_finding_thread_tool",
        "finding_origins",
        ["finding_id", "thread_id", "source_tool"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_finding_origins_finding_thread_tool", "finding_origins", type_="unique"
    )
    op.drop_index("ix_finding_origins_finding_id", table_name="finding_origins")
    op.drop_index("ix_finding_origins_thread_id", table_name="finding_origins")
    op.drop_table("finding_origins")
