"""v0.11.0 Tools tab foundation

Creates two tables that back the Tools feature (Settings → Tools):

- ``tools`` — the tool catalog. One row per registered tool. Analysts
  upload Python (and, later, shell) into the ``analyst`` lane; admins
  register OCI binary images into the ``admin`` lane. Every row carries
  its manifest as JSONB and a ``status`` gate (draft → approved →
  revoked) that determines whether an engagement can invoke it.

- ``tool_invocations`` — one row per run. v0.11.0 doesn't wire the
  runtime yet; the table lands now so v0.12.0 (Python invocation
  runtime) is a code-only change, no follow-up migration.

Immutable audit trail expectations mirror ``audit_log``: no updates to
``tool_invocations`` once ``completed_at`` is stamped (enforced service-
side, not by a DB trigger — we don't need the append-only guarantee
here that ``audit_log`` requires).

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-01
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tool_kind = sa.Enum("python", "shell", "binary", name="tool_kind")
    tool_lane = sa.Enum("analyst", "admin", name="tool_lane")
    tool_status = sa.Enum(
        "draft", "approved", "revoked", name="tool_status"
    )
    tool_task_kind = sa.Enum(
        "enum", "scan", "exploit", name="tool_task_kind"
    )
    tool_invocation_status = sa.Enum(
        "queued",
        "running",
        "completed",
        "failed",
        "timeout",
        name="tool_invocation_status",
    )

    op.create_table(
        "tools",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("kind", tool_kind, nullable=False),
        sa.Column("lane", tool_lane, nullable=False),
        # 'passive' | 'active' | 'destructive' — reuses the runtime risk
        # vocabulary from Approval so agent lease gating stays consistent.
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("task_kind", tool_task_kind, nullable=False),
        sa.Column("status", tool_status, nullable=False, server_default="draft"),
        sa.Column("manifest", JSONB, nullable=False),
        # Blob path (analyst lane) or OCI image tag (admin binary lane).
        # Nullable while a Python row is validating and hasn't persisted
        # yet — the API path enforces non-null before flipping status.
        sa.Column("artifact_ref", sa.String(500), nullable=True),
        # Filled by the validator layers (AST warnings, static checks,
        # LLM verdict once v0.13 lands). Stored as JSON so v0.13 can grow
        # the field without a migration.
        sa.Column("validation", JSONB, nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column(
            "created_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "approved_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", "version", name="uq_tools_name_version"),
    )
    op.create_index("ix_tools_kind", "tools", ["kind"])
    op.create_index("ix_tools_lane", "tools", ["lane"])
    op.create_index("ix_tools_status", "tools", ["status"])

    op.create_table(
        "tool_invocations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tool_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tools.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("tool_version", sa.Integer, nullable=False),
        sa.Column(
            "engagement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "invoker_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("args", JSONB, nullable=False, server_default="{}"),
        sa.Column("runtime_ref", sa.String(300), nullable=True),
        sa.Column(
            "status",
            tool_invocation_status,
            nullable=False,
            server_default="queued",
        ),
        sa.Column("exit_code", sa.Integer, nullable=True),
        sa.Column("stdout", sa.Text, nullable=True),
        sa.Column("stderr", sa.Text, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_tool_invocations_engagement_id",
        "tool_invocations",
        ["engagement_id"],
    )
    op.create_index(
        "ix_tool_invocations_tool_id", "tool_invocations", ["tool_id"]
    )
    op.create_index(
        "ix_tool_invocations_status", "tool_invocations", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_tool_invocations_status", table_name="tool_invocations")
    op.drop_index("ix_tool_invocations_tool_id", table_name="tool_invocations")
    op.drop_index(
        "ix_tool_invocations_engagement_id", table_name="tool_invocations"
    )
    op.drop_table("tool_invocations")

    op.drop_index("ix_tools_status", table_name="tools")
    op.drop_index("ix_tools_lane", table_name="tools")
    op.drop_index("ix_tools_kind", table_name="tools")
    op.drop_table("tools")

    for enum_name in (
        "tool_invocation_status",
        "tool_task_kind",
        "tool_status",
        "tool_lane",
        "tool_kind",
    ):
        op.execute(f"DROP TYPE IF EXISTS {enum_name}")
