"""Add durable worker processes, components, and operational events.

Revision ID: 0069
Revises: 0068
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_instances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("deployment", sa.String(length=255), nullable=True),
        sa.Column("version", sa.String(length=80), nullable=True),
        sa.Column("concurrency", sa.Integer(), server_default="1", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_worker_instances_heartbeat_at", "worker_instances", ["heartbeat_at"])
    op.create_index(
        "ix_worker_instances_role_heartbeat",
        "worker_instances",
        ["role", "heartbeat_at"],
    )

    op.create_table(
        "worker_components",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_instance_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slot", sa.Integer(), server_default="0", nullable=False),
        sa.Column("owner_token", sa.String(length=120), nullable=True),
        sa.Column("state", sa.String(length=24), server_default="starting", nullable=False),
        sa.Column("generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("restart_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("current_run_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["current_run_id"], ["playbook_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["worker_instance_id"], ["worker_instances.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_token"),
        sa.UniqueConstraint("worker_instance_id", "name", "slot", name="uq_worker_component_slot"),
    )
    op.create_index(
        "ix_worker_components_worker_instance_id", "worker_components", ["worker_instance_id"]
    )
    op.create_index("ix_worker_components_heartbeat", "worker_components", ["heartbeat_at"])
    op.create_index("ix_worker_components_current_run", "worker_components", ["current_run_id"])

    op.create_table(
        "worker_operational_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_instance_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("component", sa.String(length=80), nullable=True),
        sa.Column("slot", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("engagement_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("playbook_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["playbook_run_id"], ["playbook_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["worker_instance_id"], ["worker_instances.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_worker_operational_events_worker_instance_id",
        "worker_operational_events",
        ["worker_instance_id"],
    )
    op.create_index(
        "ix_worker_operational_events_event_type", "worker_operational_events", ["event_type"]
    )
    op.create_index("ix_worker_events_occurred", "worker_operational_events", ["occurred_at"])
    op.create_index(
        "ix_worker_events_severity_occurred",
        "worker_operational_events",
        ["severity", "occurred_at"],
    )
    op.create_index("ix_worker_events_run", "worker_operational_events", ["playbook_run_id"])

    op.create_index(
        "ix_playbook_runs_pending_fifo",
        "playbook_runs",
        ["created_at", "id"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_playbook_runs_running_engagement",
        "playbook_runs",
        ["engagement_id", "started_at"],
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index("ix_playbook_runs_running_engagement", table_name="playbook_runs")
    op.drop_index("ix_playbook_runs_pending_fifo", table_name="playbook_runs")
    op.drop_table("worker_operational_events")
    op.drop_table("worker_components")
    op.drop_table("worker_instances")
