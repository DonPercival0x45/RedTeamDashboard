"""engagement strategist persistence foundation

Revision ID: 0046
Revises: 0045
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0046"
down_revision = "0045"
branch_labels = None
depends_on = None


engagement_work_state = postgresql.ENUM(
    "active",
    "completion_review",
    "completed",
    name="engagement_work_state",
)
strategy_revision_state = postgresql.ENUM(
    "draft",
    "proposed",
    "current",
    "rejected",
    "superseded",
    name="strategy_revision_state",
)
objective_status = postgresql.ENUM(
    "planned",
    "active",
    "blocked",
    "completed",
    "deferred",
    "cancelled",
    name="objective_status",
)
objective_priority = postgresql.ENUM(
    "critical",
    "high",
    "medium",
    "low",
    name="objective_priority",
)
work_item_status = postgresql.ENUM(
    "ready",
    "in_progress",
    "blocked",
    "completed",
    "deferred",
    "cancelled",
    name="work_item_status",
)
work_item_priority = postgresql.ENUM(
    "critical",
    "high",
    "medium",
    "low",
    name="work_item_priority",
)
work_item_executor = postgresql.ENUM(
    "analyst",
    "finding_agent",
    "engagement_strategist",
    "tactical",
    "unassigned",
    name="work_item_executor",
)
work_item_resolution = postgresql.ENUM(
    "completed",
    "disproved",
    "not_applicable",
    "duplicate",
    "superseded",
    "unable_to_complete",
    name="work_item_resolution",
)
work_item_finding_relationship = postgresql.ENUM(
    "primary",
    "related",
    "produced_by",
    "blocks",
    name="work_item_finding_relationship",
)
work_item_result_state = postgresql.ENUM(
    "proposed",
    "accepted",
    "rejected",
    "superseded",
    name="work_item_result_state",
)
strategy_signal_status = postgresql.ENUM(
    "open",
    "incorporated",
    "dismissed",
    "superseded",
    name="strategy_signal_status",
)
coverage_category = postgresql.ENUM(
    "scope_review",
    "asset_discovery",
    "service_identification",
    "scanner_coverage",
    "finding_review",
    "evidence_collection",
    "reporting",
    name="coverage_category",
)
coverage_status = postgresql.ENUM(
    "not_started",
    "planned",
    "active",
    "covered",
    "blocked",
    "deferred",
    "accepted_gap",
    "not_applicable",
    name="coverage_status",
)
engagement_completion_action = postgresql.ENUM(
    "review_started",
    "approved",
    "reopened",
    name="engagement_completion_action",
)
conversation_context_type = postgresql.ENUM(
    "finding",
    "engagement",
    name="conversation_context_type",
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (
        engagement_work_state,
        strategy_revision_state,
        objective_status,
        objective_priority,
        work_item_status,
        work_item_priority,
        work_item_executor,
        work_item_resolution,
        work_item_finding_relationship,
        work_item_result_state,
        strategy_signal_status,
        coverage_category,
        coverage_status,
        engagement_completion_action,
        conversation_context_type,
    ):
        enum.create(bind, checkfirst=True)

    op.execute("ALTER TYPE agent_name ADD VALUE IF NOT EXISTS 'engagement_strategist'")
    op.execute("ALTER TYPE suggestion_kind ADD VALUE IF NOT EXISTS 'work_item'")
    op.execute("ALTER TYPE suggestion_kind ADD VALUE IF NOT EXISTS 'strategy_revision'")

    op.add_column(
        "engagements",
        sa.Column(
            "work_state",
            postgresql.ENUM(name="engagement_work_state", create_type=False),
            server_default="active",
            nullable=False,
        ),
    )
    op.add_column(
        "engagements",
        sa.Column("work_state_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_index("ix_engagements_work_state", "engagements", ["work_state"])

    op.create_table(
        "engagement_strategy_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            postgresql.ENUM(name="strategy_revision_state", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "based_on_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagement_strategy_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("summary", sa.String(300), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "structured",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "proposed_by_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("proposal_reason", sa.Text(), nullable=True),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
            "engagement_id",
            "version",
            name="uq_strategy_revisions_engagement_version",
        ),
    )
    op.create_index(
        "ix_engagement_strategy_revisions_engagement_id",
        "engagement_strategy_revisions",
        ["engagement_id"],
    )
    op.create_index(
        "ix_strategy_revisions_engagement_version",
        "engagement_strategy_revisions",
        ["engagement_id", "version"],
    )
    op.create_index(
        "ix_strategy_revisions_engagement_state",
        "engagement_strategy_revisions",
        ["engagement_id", "state"],
    )
    op.create_index(
        "uq_strategy_revisions_current_per_engagement",
        "engagement_strategy_revisions",
        ["engagement_id"],
        unique=True,
        postgresql_where=sa.text("state = 'current'"),
    )

    op.create_table(
        "engagement_objectives",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("success_criteria", sa.Text(), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="objective_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "priority",
            postgresql.ENUM(name="objective_priority", create_type=False),
            nullable=False,
        ),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "completed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
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
    )
    op.create_index(
        "ix_engagement_objectives_engagement_id",
        "engagement_objectives",
        ["engagement_id"],
    )
    op.create_index(
        "ix_engagement_objectives_engagement_status",
        "engagement_objectives",
        ["engagement_id", "status"],
    )
    op.create_index(
        "ix_engagement_objectives_engagement_order",
        "engagement_objectives",
        ["engagement_id", "display_order"],
    )

    op.create_table(
        "work_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "objective_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagement_objectives.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "parent_work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "acceptance_criteria",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="work_item_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "priority",
            postgresql.ENUM(name="work_item_priority", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "executor_type",
            postgresql.ENUM(name="work_item_executor", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "assigned_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("blocked_reason", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolution_outcome",
            postgresql.ENUM(name="work_item_resolution", create_type=False),
            nullable=True,
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "completed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
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
    )
    op.create_index("ix_work_items_engagement_id", "work_items", ["engagement_id"])
    op.create_index(
        "ix_work_items_engagement_status", "work_items", ["engagement_id", "status"]
    )
    op.create_index(
        "ix_work_items_engagement_priority", "work_items", ["engagement_id", "priority"]
    )
    op.create_index(
        "ix_work_items_engagement_updated", "work_items", ["engagement_id", "updated_at"]
    )
    op.create_index("ix_work_items_objective_id", "work_items", ["objective_id"])
    op.create_index("ix_work_items_assigned_user_id", "work_items", ["assigned_user_id"])

    op.create_table(
        "work_item_findings",
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "relationship",
            postgresql.ENUM(name="work_item_finding_relationship", create_type=False),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_work_item_findings_work_item_id", "work_item_findings", ["work_item_id"]
    )
    op.create_index(
        "ix_work_item_findings_finding_id", "work_item_findings", ["finding_id"]
    )

    op.create_table(
        "work_item_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "state",
            postgresql.ENUM(name="work_item_result_state", create_type=False),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "structured",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "proposed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "proposed_by_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "work_item_id",
            "revision",
            name="uq_work_item_results_work_item_revision",
        ),
    )
    op.create_index("ix_work_item_results_work_item_id", "work_item_results", ["work_item_id"])
    op.create_index(
        "ix_work_item_results_work_item_state", "work_item_results", ["work_item_id", "state"]
    )
    op.create_index(
        "uq_work_item_results_accepted_per_work_item",
        "work_item_results",
        ["work_item_id"],
        unique=True,
        postgresql_where=sa.text("state = 'accepted'"),
    )

    op.create_table(
        "strategy_signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_work_item_result_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_item_results.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("signal_type", sa.String(80), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(20), nullable=False),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "suggested_effect",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("dedup_key", sa.String(200), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="strategy_signal_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "source_finding_id IS NOT NULL OR source_work_item_id IS NOT NULL "
            "OR source_work_item_result_id IS NOT NULL OR source_execution_id IS NOT NULL",
            name="ck_strategy_signals_has_source",
        ),
    )
    op.create_index("ix_strategy_signals_engagement_id", "strategy_signals", ["engagement_id"])
    op.create_index(
        "ix_strategy_signals_engagement_status", "strategy_signals", ["engagement_id", "status"]
    )
    op.create_index(
        "ix_strategy_signals_engagement_dedup", "strategy_signals", ["engagement_id", "dedup_key"]
    )
    op.create_index(
        "uq_strategy_signals_active_result_type",
        "strategy_signals",
        ["source_work_item_result_id", "signal_type"],
        unique=True,
        postgresql_where=sa.text(
            "source_work_item_result_id IS NOT NULL AND status = 'open'"
        ),
    )
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION strategy_signals_validate_sources()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            DECLARE
                v_engagement_id uuid;
                v_work_item_id uuid;
            BEGIN
                IF NEW.source_work_item_id IS NOT NULL THEN
                    SELECT engagement_id INTO v_engagement_id
                    FROM work_items WHERE id = NEW.source_work_item_id;
                    IF v_engagement_id IS DISTINCT FROM NEW.engagement_id THEN
                        RAISE EXCEPTION 'strategy signal work item source is outside engagement'
                            USING ERRCODE = 'check_violation';
                    END IF;
                END IF;

                IF NEW.source_work_item_result_id IS NOT NULL THEN
                    SELECT wi.engagement_id, wi.id INTO v_engagement_id, v_work_item_id
                    FROM work_item_results wir
                    JOIN work_items wi ON wi.id = wir.work_item_id
                    WHERE wir.id = NEW.source_work_item_result_id;
                    IF v_engagement_id IS DISTINCT FROM NEW.engagement_id THEN
                        RAISE EXCEPTION 'strategy signal result source is outside engagement'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF NEW.source_work_item_id IS NOT NULL
                       AND NEW.source_work_item_id IS DISTINCT FROM v_work_item_id THEN
                        RAISE EXCEPTION 'strategy signal result/work item source mismatch'
                            USING ERRCODE = 'check_violation';
                    END IF;
                END IF;

                IF NEW.source_finding_id IS NOT NULL THEN
                    SELECT engagement_id INTO v_engagement_id
                    FROM findings WHERE id = NEW.source_finding_id;
                    IF v_engagement_id IS DISTINCT FROM NEW.engagement_id THEN
                        RAISE EXCEPTION 'strategy signal finding source is outside engagement'
                            USING ERRCODE = 'check_violation';
                    END IF;
                END IF;

                RETURN NEW;
            END;
            $$;
            """
        )
    )
    op.execute(
        """
        CREATE TRIGGER strategy_signals_validate_sources_trigger
        BEFORE INSERT OR UPDATE ON strategy_signals
        FOR EACH ROW EXECUTE FUNCTION strategy_signals_validate_sources();
        """
    )

    op.create_table(
        "coverage_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "objective_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagement_objectives.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "scope_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scope_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("target_kind", sa.String(40), nullable=False),
        sa.Column("target_key", sa.String(500), nullable=False),
        sa.Column(
            "activity_category",
            postgresql.ENUM(name="coverage_category", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="coverage_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "supporting_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "accepted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
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
            "engagement_id",
            "target_kind",
            "target_key",
            "activity_category",
            name="uq_coverage_items_engagement_target_category",
        ),
    )
    op.create_index("ix_coverage_items_engagement_id", "coverage_items", ["engagement_id"])
    op.create_index(
        "ix_coverage_items_engagement_status", "coverage_items", ["engagement_id", "status"]
    )
    op.create_index(
        "ix_coverage_items_engagement_category",
        "coverage_items",
        ["engagement_id", "activity_category"],
    )
    op.create_index(
        "ix_coverage_items_engagement_target",
        "coverage_items",
        ["engagement_id", "target_key"],
    )

    op.create_table(
        "engagement_checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "strategy_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagement_strategy_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("material_event_cursor", sa.DateTime(timezone=True), nullable=False),
        sa.Column("facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("narrative", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_engagement_checkpoints_engagement_id",
        "engagement_checkpoints",
        ["engagement_id"],
    )
    op.create_index(
        "ix_engagement_checkpoints_engagement_created",
        "engagement_checkpoints",
        ["engagement_id", "created_at"],
    )

    op.create_table(
        "engagement_completion_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "action",
            postgresql.ENUM(name="engagement_completion_action", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "from_work_state",
            postgresql.ENUM(name="engagement_work_state", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "to_work_state",
            postgresql.ENUM(name="engagement_work_state", create_type=False),
            nullable=False,
        ),
        sa.Column("readiness_hash", sa.String(64), nullable=True),
        sa.Column("readiness_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "accepted_exceptions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "strategy_revision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagement_strategy_revisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "prior_completion_decision_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagement_completion_decisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column(
            "decided_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "engagement_id",
            "idempotency_key",
            name="uq_completion_decisions_engagement_idempotency",
        ),
        sa.CheckConstraint(
            "(action IN ('review_started', 'approved') AND readiness_hash IS NOT NULL "
            "AND readiness_snapshot IS NOT NULL AND prior_completion_decision_id IS NULL) "
            "OR (action = 'reopened' AND readiness_hash IS NULL "
            "AND readiness_snapshot IS NULL AND prior_completion_decision_id IS NOT NULL "
            "AND reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="ck_completion_decisions_action_fields",
        ),
    )
    op.create_index(
        "ix_engagement_completion_decisions_engagement_id",
        "engagement_completion_decisions",
        ["engagement_id"],
    )
    op.create_index(
        "ix_completion_decisions_engagement_created",
        "engagement_completion_decisions",
        ["engagement_id", "created_at"],
    )
    op.create_index(
        "ix_engagement_completion_decisions_action",
        "engagement_completion_decisions",
        ["action"],
    )

    op.add_column(
        "tasks",
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_tasks_work_item_id", "tasks", ["work_item_id"])

    op.add_column("suggestions", sa.Column("proposal_key", sa.String(200), nullable=True))
    op.add_column("suggestions", sa.Column("context_hash", sa.String(64), nullable=True))
    op.add_column(
        "suggestions",
        sa.Column(
            "objective_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagement_objectives.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "suggestions",
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_suggestions_work_item_id", "suggestions", ["work_item_id"])
    op.create_index(
        "ix_suggestions_engagement_status_kind_proposal_key",
        "suggestions",
        ["engagement_id", "status", "kind", "proposal_key"],
    )

    op.add_column(
        "conversations",
        sa.Column(
            "context_type",
            postgresql.ENUM(name="conversation_context_type", create_type=False),
            server_default="finding",
            nullable=False,
        ),
    )
    op.alter_column("conversations", "finding_id", nullable=True)
    op.create_index("ix_conversations_context_type", "conversations", ["context_type"])
    op.create_check_constraint(
        "ck_conversations_context_consistency",
        "conversations",
        "(context_type = 'finding' AND finding_id IS NOT NULL) "
        "OR (context_type = 'engagement' AND finding_id IS NULL)",
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
                DELETE FROM engagement_completion_decisions WHERE engagement_id = p_engagement_id;
                DELETE FROM engagement_checkpoints WHERE engagement_id = p_engagement_id;
                DELETE FROM coverage_items WHERE engagement_id = p_engagement_id;
                DELETE FROM strategy_signals WHERE engagement_id = p_engagement_id;
                DELETE FROM work_item_results USING work_items
                    WHERE work_item_results.work_item_id = work_items.id
                    AND work_items.engagement_id = p_engagement_id;
                DELETE FROM work_item_findings USING work_items
                    WHERE work_item_findings.work_item_id = work_items.id
                    AND work_items.engagement_id = p_engagement_id;
                DELETE FROM work_items WHERE engagement_id = p_engagement_id;
                DELETE FROM engagement_objectives WHERE engagement_id = p_engagement_id;
                DELETE FROM engagement_strategy_revisions WHERE engagement_id = p_engagement_id;
                DELETE FROM conversations WHERE engagement_id = p_engagement_id;
                DELETE FROM engagements WHERE id = p_engagement_id;
                PERFORM set_config('app.audit_log_bypass', 'off', true);
            END;
            $$;
            """
        )
    )


def downgrade() -> None:
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

    op.drop_constraint("ck_conversations_context_consistency", "conversations", type_="check")
    op.drop_index("ix_conversations_context_type", table_name="conversations")
    op.execute("DELETE FROM conversations WHERE context_type = 'engagement'")
    op.alter_column("conversations", "finding_id", nullable=False)
    op.drop_column("conversations", "context_type")

    op.drop_index("ix_suggestions_engagement_status_kind_proposal_key", table_name="suggestions")
    op.drop_index("ix_suggestions_work_item_id", table_name="suggestions")
    op.drop_column("suggestions", "work_item_id")
    op.drop_column("suggestions", "objective_id")
    op.drop_column("suggestions", "context_hash")
    op.drop_column("suggestions", "proposal_key")

    op.drop_index("ix_tasks_work_item_id", table_name="tasks")
    op.drop_column("tasks", "work_item_id")

    op.drop_index(
        "ix_engagement_completion_decisions_action",
        table_name="engagement_completion_decisions",
    )
    op.drop_index(
        "ix_completion_decisions_engagement_created",
        table_name="engagement_completion_decisions",
    )
    op.drop_index(
        "ix_engagement_completion_decisions_engagement_id",
        table_name="engagement_completion_decisions",
    )
    op.drop_table("engagement_completion_decisions")

    op.drop_index(
        "ix_engagement_checkpoints_engagement_created",
        table_name="engagement_checkpoints",
    )
    op.drop_index(
        "ix_engagement_checkpoints_engagement_id",
        table_name="engagement_checkpoints",
    )
    op.drop_table("engagement_checkpoints")

    op.drop_index("ix_coverage_items_engagement_target", table_name="coverage_items")
    op.drop_index("ix_coverage_items_engagement_category", table_name="coverage_items")
    op.drop_index("ix_coverage_items_engagement_status", table_name="coverage_items")
    op.drop_index("ix_coverage_items_engagement_id", table_name="coverage_items")
    op.drop_table("coverage_items")

    op.execute(
        "DROP TRIGGER IF EXISTS strategy_signals_validate_sources_trigger "
        "ON strategy_signals"
    )
    op.execute("DROP FUNCTION IF EXISTS strategy_signals_validate_sources()")
    op.drop_index("uq_strategy_signals_active_result_type", table_name="strategy_signals")
    op.drop_index("ix_strategy_signals_engagement_dedup", table_name="strategy_signals")
    op.drop_index("ix_strategy_signals_engagement_status", table_name="strategy_signals")
    op.drop_index("ix_strategy_signals_engagement_id", table_name="strategy_signals")
    op.drop_table("strategy_signals")

    op.drop_index("uq_work_item_results_accepted_per_work_item", table_name="work_item_results")
    op.drop_index("ix_work_item_results_work_item_state", table_name="work_item_results")
    op.drop_index("ix_work_item_results_work_item_id", table_name="work_item_results")
    op.drop_table("work_item_results")

    op.drop_index("ix_work_item_findings_finding_id", table_name="work_item_findings")
    op.drop_index("ix_work_item_findings_work_item_id", table_name="work_item_findings")
    op.drop_table("work_item_findings")

    op.drop_index("ix_work_items_assigned_user_id", table_name="work_items")
    op.drop_index("ix_work_items_objective_id", table_name="work_items")
    op.drop_index("ix_work_items_engagement_updated", table_name="work_items")
    op.drop_index("ix_work_items_engagement_priority", table_name="work_items")
    op.drop_index("ix_work_items_engagement_status", table_name="work_items")
    op.drop_index("ix_work_items_engagement_id", table_name="work_items")
    op.drop_table("work_items")

    op.drop_index("ix_engagement_objectives_engagement_order", table_name="engagement_objectives")
    op.drop_index("ix_engagement_objectives_engagement_status", table_name="engagement_objectives")
    op.drop_index("ix_engagement_objectives_engagement_id", table_name="engagement_objectives")
    op.drop_table("engagement_objectives")

    op.drop_index(
        "uq_strategy_revisions_current_per_engagement",
        table_name="engagement_strategy_revisions",
    )
    op.drop_index(
        "ix_strategy_revisions_engagement_state",
        table_name="engagement_strategy_revisions",
    )
    op.drop_index(
        "ix_strategy_revisions_engagement_version",
        table_name="engagement_strategy_revisions",
    )
    op.drop_index(
        "ix_engagement_strategy_revisions_engagement_id",
        table_name="engagement_strategy_revisions",
    )
    op.drop_table("engagement_strategy_revisions")

    op.drop_index("ix_engagements_work_state", table_name="engagements")
    op.drop_column("engagements", "work_state_version")
    op.drop_column("engagements", "work_state")

    bind = op.get_bind()
    for enum in (
        conversation_context_type,
        engagement_completion_action,
        coverage_status,
        coverage_category,
        strategy_signal_status,
        work_item_result_state,
        work_item_finding_relationship,
        work_item_resolution,
        work_item_executor,
        work_item_priority,
        work_item_status,
        objective_priority,
        objective_status,
        strategy_revision_state,
        engagement_work_state,
    ):
        enum.drop(bind, checkfirst=True)

    # Existing PostgreSQL enum values added to agent_name/suggestion_kind are left in
    # place on downgrade; PostgreSQL does not support dropping enum values safely.
