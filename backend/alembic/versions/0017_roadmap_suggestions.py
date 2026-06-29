"""roadmap suggestions, users.is_admin, agent_name=planner

Adds the tenant-global "suggestion box" feature:
- ``roadmap_suggestions`` table for analyst-submitted ideas with agent pros/cons
- ``users.is_admin`` boolean for gating the approve/reject decision
- ``planner`` value on the ``agent_name`` enum so PlanningAgent calls show up
  in the Costs tab
- ``agent_executions.engagement_id`` becomes nullable — the planner is
  tenant-scoped, not engagement-scoped

Revision ID: 0017
Revises: 0016
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``ALTER TYPE ... ADD VALUE`` runs inside a transaction on PG12+; the new
    # value can't be used in the same transaction, which is fine because we
    # only persist ``planner``-tagged rows in subsequent app traffic.
    op.execute("ALTER TYPE agent_name ADD VALUE IF NOT EXISTS 'planner'")

    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Planner executions have no engagement_id — they are tenant-global.
    op.alter_column(
        "agent_executions",
        "engagement_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )

    op.create_table(
        "roadmap_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "agent_pros",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "agent_cons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("agent_summary", sa.Text(), nullable=True),
        sa.Column(
            "agent_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending_review",
                "approved",
                "rejected",
                name="roadmap_suggestion_status",
            ),
            nullable=False,
            server_default="pending_review",
        ),
        sa.Column(
            "reviewed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
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
    op.create_index(
        "ix_roadmap_suggestions_status",
        "roadmap_suggestions",
        ["status"],
    )
    op.create_index(
        "ix_roadmap_suggestions_author_user_id",
        "roadmap_suggestions",
        ["author_user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_roadmap_suggestions_author_user_id", table_name="roadmap_suggestions")
    op.drop_index("ix_roadmap_suggestions_status", table_name="roadmap_suggestions")
    op.drop_table("roadmap_suggestions")
    op.execute("DROP TYPE IF EXISTS roadmap_suggestion_status")

    # Best-effort: only flip engagement_id back to NOT NULL if no planner rows
    # are present. If a downgrade is run on a DB with planner traffic, the
    # operator has to clean up the orphan rows manually first.
    op.execute(
        "DELETE FROM agent_executions WHERE engagement_id IS NULL"
    )
    op.alter_column(
        "agent_executions",
        "engagement_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.drop_column("users", "is_admin")

    # Postgres can't remove an enum value (no ALTER TYPE DROP VALUE). The
    # 'planner' value remains in the agent_name enum — harmless if no rows
    # reference it.
