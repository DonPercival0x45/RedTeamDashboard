"""auto_assess_enabled — token-saving kill-switch for background generation

Revision ID: 0053
Revises: 0052

Replaces nothing. Adds a per-engagement boolean (default true) that gates the
automatic background strategic generation:

  - the strategic watcher (finding.created -> StrategicAgent.analyze_finding),
  - auto-reassess (work-item resolve -> maybe_schedule_auto_reassess).

When false, neither fires, so no LLM tokens are spent on auto-generation while
an analyst is just evaluating an engagement. The manual "Analyze" button is
unaffected (explicit user action).
"""

from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE engagements "
        "ADD COLUMN IF NOT EXISTS auto_assess_enabled BOOLEAN "
        "NOT NULL DEFAULT TRUE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE engagements DROP COLUMN IF EXISTS auto_assess_enabled")
