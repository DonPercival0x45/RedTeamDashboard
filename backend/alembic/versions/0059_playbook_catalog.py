"""v3 A3a — playbook catalog + execution records + coverage FK

Adds the playbook execution plane's persistence layer: catalog + steps +
per-run records. The runner service (``services/playbook/runner``) writes
these; A2's ``CoverageRecord`` remains the per-step coverage receipt (no
duplicate ``PlaybookStepRun`` table).

Three tables + one FK upgrade:

1. ``playbooks`` — catalog entry keyed by ``(slug, version)``.
2. ``playbook_steps`` — ordered tool steps; each step declares which
   methodology node_ids it satisfies on success.
3. ``playbook_runs`` — per-execution row with status + FindingsSummary
   counts (feeds the ``collection.job.completed`` milestone payload).
4. ``coverage_records.playbook_run_id`` was a plain UUID in 0056 pending A3
   — upgrade to a real nullable FK now that the target table exists.
"""
from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE playbook_run_status AS ENUM "
        "('pending', 'running', 'completed', 'partial', 'failed', 'cancelled')"
    )

    op.execute(
        """
        CREATE TABLE playbooks (
            id UUID PRIMARY KEY,
            slug VARCHAR(120) NOT NULL,
            version INT NOT NULL DEFAULT 1,
            name VARCHAR(200) NOT NULL,
            description TEXT,
            applies_to_asset_class VARCHAR(80) NOT NULL,
            active BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_playbooks_slug_version UNIQUE (slug, version)
        )
        """
    )
    op.execute("CREATE INDEX ix_playbooks_slug ON playbooks (slug)")

    op.execute(
        """
        CREATE TABLE playbook_steps (
            id UUID PRIMARY KEY,
            playbook_id UUID NOT NULL REFERENCES playbooks(id) ON DELETE CASCADE,
            sort_order INT NOT NULL DEFAULT 0,
            tool_slug VARCHAR(120) NOT NULL,
            args_template JSONB NOT NULL DEFAULT '{}'::jsonb,
            satisfies_node_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            description TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_playbook_steps_playbook ON playbook_steps (playbook_id)"
    )

    op.execute(
        """
        CREATE TABLE playbook_runs (
            id UUID PRIMARY KEY,
            engagement_id UUID NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
            playbook_id UUID NOT NULL REFERENCES playbooks(id) ON DELETE RESTRICT,
            status playbook_run_status NOT NULL DEFAULT 'pending',
            scope_subset JSONB NOT NULL DEFAULT '[]'::jsonb,
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ,
            steps_total INT NOT NULL DEFAULT 0,
            steps_succeeded INT NOT NULL DEFAULT 0,
            steps_failed INT NOT NULL DEFAULT 0,
            findings_new INT NOT NULL DEFAULT 0,
            findings_unvalidated INT NOT NULL DEFAULT 0,
            findings_high_severity INT NOT NULL DEFAULT 0,
            findings_total INT NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_playbook_runs_engagement ON playbook_runs (engagement_id)"
    )
    op.execute(
        "CREATE INDEX ix_playbook_runs_engagement_status "
        "ON playbook_runs (engagement_id, status)"
    )

    # coverage_records.playbook_run_id → real FK.
    op.execute(
        """
        ALTER TABLE coverage_records
            ADD CONSTRAINT fk_coverage_records_playbook_run
            FOREIGN KEY (playbook_run_id) REFERENCES playbook_runs(id)
            ON DELETE SET NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE coverage_records DROP CONSTRAINT IF EXISTS "
        "fk_coverage_records_playbook_run"
    )
    op.execute("DROP TABLE IF EXISTS playbook_runs")
    op.execute("DROP TABLE IF EXISTS playbook_steps")
    op.execute("DROP TABLE IF EXISTS playbooks")
    op.execute("DROP TYPE IF EXISTS playbook_run_status")
