"""add durable playbook step executions and evidence artifacts

Revision ID: 0067
Revises: 0066
Create Date: 2026-07-25
"""

from __future__ import annotations

from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE playbook_runs ADD COLUMN worker_id VARCHAR(100)")
    op.execute("ALTER TABLE playbook_runs ADD COLUMN worker_heartbeat_at TIMESTAMPTZ")
    op.execute("CREATE INDEX ix_playbook_runs_worker_id ON playbook_runs (worker_id)")
    op.execute(
        "CREATE INDEX ix_playbook_runs_worker_heartbeat_at ON playbook_runs (worker_heartbeat_at)"
    )

    op.execute(
        "CREATE TYPE playbook_step_execution_status AS ENUM "
        "('running', 'succeeded', 'failed', 'stub', 'cancelled')"
    )
    op.execute(
        """
        CREATE TABLE playbook_step_executions (
            id UUID PRIMARY KEY,
            playbook_run_id UUID NOT NULL
                REFERENCES playbook_runs(id) ON DELETE CASCADE,
            playbook_step_id UUID
                REFERENCES playbook_steps(id) ON DELETE SET NULL,
            sort_order INT NOT NULL,
            tool_slug VARCHAR(120) NOT NULL,
            target TEXT NOT NULL,
            transport VARCHAR(20) NOT NULL,
            attempt INT NOT NULL DEFAULT 1,
            status playbook_step_execution_status NOT NULL DEFAULT 'running',
            arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,
            duration_ms INT,
            error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_playbook_step_execution_attempt
                UNIQUE (playbook_run_id, playbook_step_id, target, attempt)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_playbook_step_executions_run ON playbook_step_executions (playbook_run_id)"
    )
    op.execute(
        "CREATE INDEX ix_playbook_step_executions_run_status "
        "ON playbook_step_executions (playbook_run_id, status)"
    )

    op.execute(
        """
        CREATE TABLE evidence_artifacts (
            id UUID PRIMARY KEY,
            engagement_id UUID NOT NULL
                REFERENCES engagements(id) ON DELETE CASCADE,
            playbook_run_id UUID
                REFERENCES playbook_runs(id) ON DELETE CASCADE,
            playbook_step_execution_id UUID
                REFERENCES playbook_step_executions(id) ON DELETE CASCADE,
            finding_id UUID REFERENCES findings(id) ON DELETE SET NULL,
            kind VARCHAR(40) NOT NULL DEFAULT 'tool_output',
            source_tool VARCHAR(120) NOT NULL,
            target TEXT NOT NULL,
            payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            sha256 VARCHAR(64) NOT NULL,
            size_bytes INT NOT NULL,
            truncated BOOLEAN NOT NULL DEFAULT FALSE,
            redacted BOOLEAN NOT NULL DEFAULT TRUE,
            captured_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_evidence_artifacts_step_execution
                UNIQUE (playbook_step_execution_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_evidence_artifacts_engagement ON evidence_artifacts (engagement_id)"
    )
    op.execute("CREATE INDEX ix_evidence_artifacts_run ON evidence_artifacts (playbook_run_id)")
    op.execute("CREATE INDEX ix_evidence_artifacts_finding ON evidence_artifacts (finding_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS evidence_artifacts")
    op.execute("DROP TABLE IF EXISTS playbook_step_executions")
    op.execute("DROP TYPE IF EXISTS playbook_step_execution_status")
    op.execute("DROP INDEX IF EXISTS ix_playbook_runs_worker_heartbeat_at")
    op.execute("DROP INDEX IF EXISTS ix_playbook_runs_worker_id")
    op.execute("ALTER TABLE playbook_runs DROP COLUMN IF EXISTS worker_heartbeat_at")
    op.execute("ALTER TABLE playbook_runs DROP COLUMN IF EXISTS worker_id")
