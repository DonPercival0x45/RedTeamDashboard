"""initial schema: users, engagements, scope_items, findings, approvals, audit_log

Includes the BEFORE UPDATE/DELETE trigger that makes audit_log append-only at
the DB layer, plus a SECURITY DEFINER flush_engagement() helper that is the
only path allowed to remove audit rows (during a full engagement flush).

Revision ID: 0001
Revises:
Create Date: 2026-05-21
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --- Enum types (created once, reused via create_type=False) ---
engagement_status = postgresql.ENUM(
    "active", "archived", "flushed", name="engagement_status", create_type=False
)
scope_kind = postgresql.ENUM("domain", "cidr", "ip", "url", name="scope_kind", create_type=False)
finding_severity = postgresql.ENUM(
    "info", "low", "medium", "high", "critical", name="finding_severity", create_type=False
)
risk_level = postgresql.ENUM(
    "passive", "active", "destructive", name="risk_level", create_type=False
)
approval_status = postgresql.ENUM(
    "pending", "approved", "denied", "edited", "auto", name="approval_status", create_type=False
)
actor_type = postgresql.ENUM("user", "agent", "system", name="actor_type", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (
        engagement_status,
        scope_kind,
        finding_severity,
        risk_level,
        approval_status,
        actor_type,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True, index=True),
        sa.Column("display_name", sa.String(200)),
        sa.Column("entra_oid", sa.String(64), unique=True, index=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
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

    op.create_table(
        "engagements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(200), nullable=False, unique=True, index=True),
        sa.Column(
            "status",
            engagement_status,
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            index=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("flushed_at", sa.DateTime(timezone=True)),
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

    op.create_table(
        "scope_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", scope_kind, nullable=False),
        sa.Column("value", sa.String(500), nullable=False),
        sa.Column("is_exclusion", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("note", sa.String(500)),
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

    op.create_table(
        "findings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("severity", finding_severity, nullable=False, server_default="info"),
        sa.Column("summary", sa.Text),
        sa.Column(
            "details",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("source_tool", sa.String(120), index=True),
        sa.Column("target", sa.String(500), index=True),
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

    op.create_table(
        "approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("thread_id", sa.String(200), nullable=False, index=True),
        sa.Column("node", sa.String(120)),
        sa.Column("tool_name", sa.String(200), nullable=False),
        sa.Column(
            "tool_args",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("risk", risk_level, nullable=False),
        sa.Column(
            "scope_check",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("status", approval_status, nullable=False, server_default="pending"),
        sa.Column(
            "decided_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("decision_args", postgresql.JSONB),
        sa.Column("authorization_id", postgresql.UUID(as_uuid=True), index=True),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
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

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            index=True,
        ),
        sa.Column("actor_type", actor_type, nullable=False),
        sa.Column("actor_id", sa.String(200), index=True),
        sa.Column("event_type", sa.String(120), nullable=False, index=True),
        sa.Column(
            "payload",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
    )

    # --- Append-only enforcement on audit_log ---
    #
    # The trigger fires BEFORE UPDATE or DELETE. It checks a session-local
    # flag `app.audit_log_bypass`; only set inside flush_engagement(), which
    # runs as SECURITY DEFINER. Every other caller (including the app DB
    # user) gets a hard exception.
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION audit_log_immutable()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                IF current_setting('app.audit_log_bypass', true) = 'on' THEN
                    RETURN COALESCE(OLD, NEW);
                END IF;
                RAISE EXCEPTION 'audit_log is append-only (operation % not permitted)', TG_OP
                    USING ERRCODE = 'insufficient_privilege';
            END;
            $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER audit_log_immutable_trigger
            BEFORE UPDATE OR DELETE ON audit_log
            FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION flush_engagement(p_engagement_id uuid)
            RETURNS void
            LANGUAGE plpgsql
            SECURITY DEFINER
            SET search_path = pg_catalog, public
            AS $$
            BEGIN
                PERFORM set_config('app.audit_log_bypass', 'on', true);
                DELETE FROM audit_log WHERE engagement_id = p_engagement_id;
                DELETE FROM engagements WHERE id = p_engagement_id;
                PERFORM set_config('app.audit_log_bypass', 'off', true);
            END;
            $$;
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS flush_engagement(uuid);")
    op.execute("DROP TRIGGER IF EXISTS audit_log_immutable_trigger ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_immutable();")

    op.drop_table("audit_log")
    op.drop_table("approvals")
    op.drop_table("findings")
    op.drop_table("scope_items")
    op.drop_table("engagements")
    op.drop_table("users")

    bind = op.get_bind()
    for enum in (
        actor_type,
        approval_status,
        risk_level,
        finding_severity,
        scope_kind,
        engagement_status,
    ):
        enum.drop(bind, checkfirst=True)
