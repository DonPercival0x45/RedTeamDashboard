"""v3 shared contract: work-item disposition, engagement phase, coverage records

The single negotiated surface both v3 tracks code against (see
``architecture-v3-tracker.md`` PR 0). Additive only:

- ``work_items.disposition`` (NEW nullable column + enum) — the
  *how/where-it-gets-done* axis. Backfilled from the existing *who-runs-it*
  axis (``executor_type``); ``executor_type`` stays until Convergence retires
  the v1 agents.
- ``engagements.phase`` + ``baseline_completed_at`` — orthogonal to
  ``status`` and ``work_state``; baseline-complete flips ``phase`` only.
- ``coverage_records`` table — written by Track A (A2), read by Track B
  (B1/B2 coverage rollup + baseline-complete display). Schema-only here; the
  writing + baseline-complete computation lands in A2.

The three milestone event *names* + payload shapes live in code as
``app.engagement.milestones`` (the contract A6 emits and B3 consumes); no
stream wiring in this PR.
"""
from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- work-item disposition -------------------------------------------
    op.execute(
        "CREATE TYPE work_item_disposition AS ENUM "
        "('tool-backed', 'tool-backed-mcp', 'manual-local', 'build', "
        "'blocked', 'needs-decision', 'out-of-scope')"
    )
    op.execute(
        "ALTER TABLE work_items "
        "ADD COLUMN disposition work_item_disposition NULL"
    )
    # Backfill from the existing who-runs-it axis. ``tactical`` dispatches a
    # tool → tool-backed; the analyst/agent judgment executors → manual-local;
    # ``unassigned`` stays NULL for analyst triage. ``executor_type`` itself is
    # NOT dropped here — it retires at Convergence (C5) once v3 paths cover it.
    op.execute(
        """
        UPDATE work_items SET disposition = CASE
            WHEN executor_type = 'tactical' THEN 'tool-backed'::work_item_disposition
            WHEN executor_type = 'analyst' THEN 'manual-local'::work_item_disposition
            WHEN executor_type = 'finding_agent' THEN 'manual-local'::work_item_disposition
            WHEN executor_type = 'engagement_strategist' THEN 'manual-local'::work_item_disposition
            ELSE NULL
        END
        """
    )
    op.execute(
        "CREATE INDEX ix_work_items_engagement_disposition "
        "ON work_items (engagement_id, disposition)"
    )

    # --- engagement phase (orthogonal to status + work_state) ------------
    op.execute("CREATE TYPE engagement_phase AS ENUM ('baseline', 'exploration')")
    # NOT NULL DEFAULT 'baseline' — PG backfills existing rows with the default.
    op.execute(
        "ALTER TABLE engagements "
        "ADD COLUMN phase engagement_phase NOT NULL DEFAULT 'baseline'"
    )
    op.execute(
        "ALTER TABLE engagements ADD COLUMN baseline_completed_at TIMESTAMPTZ NULL"
    )
    op.execute("CREATE INDEX ix_engagements_phase ON engagements (phase)")

    # --- coverage records (schema only; A2 writes, B1/B2 read) -----------
    op.execute(
        "CREATE TYPE coverage_record_status AS ENUM "
        "('pending', 'attempted', 'satisfied', 'partial', 'failed')"
    )
    op.execute(
        "CREATE TYPE coverage_node_tier AS ENUM ('baseline', 'exploration')"
    )
    op.execute(
        """
        CREATE TABLE coverage_records (
            id UUID PRIMARY KEY,
            engagement_id UUID NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
            methodology_id UUID NULL,
            node_id VARCHAR(200) NOT NULL,
            node_tier coverage_node_tier NOT NULL,
            asset_class VARCHAR(80) NOT NULL,
            scope_subset JSONB NOT NULL DEFAULT '{}'::jsonb,
            status coverage_record_status NOT NULL DEFAULT 'pending',
            playbook_run_id UUID NULL,
            satisfied_at TIMESTAMPTZ NULL,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_coverage_records_eng_node "
        "ON coverage_records (engagement_id, node_id)"
    )
    op.execute(
        "CREATE INDEX ix_coverage_records_eng_tier_status "
        "ON coverage_records (engagement_id, node_tier, status)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS coverage_records")
    op.execute("DROP INDEX IF EXISTS ix_engagements_phase")
    op.execute("ALTER TABLE engagements DROP COLUMN IF EXISTS baseline_completed_at")
    op.execute("ALTER TABLE engagements DROP COLUMN IF EXISTS phase")
    op.execute("DROP TYPE IF EXISTS coverage_node_tier")
    op.execute("DROP TYPE IF EXISTS coverage_record_status")
    op.execute("DROP TYPE IF EXISTS engagement_phase")
    op.execute("DROP INDEX IF EXISTS ix_work_items_engagement_disposition")
    op.execute("ALTER TABLE work_items DROP COLUMN IF EXISTS disposition")
    op.execute("DROP TYPE IF EXISTS work_item_disposition")
