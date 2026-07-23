"""v3 A5 — approve-before-run gate

Adds the analyst-in-the-loop pre-authorization mechanism the design
mandates for active playbooks (architecture-v2-plan §2b, architecture-
answers A2). Active playbooks emit runs in ``awaiting_approval`` status
that the worker deliberately does NOT claim; an analyst POSTs to
``/playbook-runs/{id}/approve`` to release the run into ``pending`` (where
A3c's SKIP LOCKED worker picks it up as usual).

Two changes:

1. Add ``awaiting_approval`` to the ``playbook_run_status`` enum. Postgres
   ALTER TYPE ADD VALUE runs outside a transaction — Alembic handles that.
2. Add ``approved_by`` / ``approved_at`` / ``approval_reason`` +
   ``rejected_by`` / ``rejected_at`` / ``rejection_reason`` columns on
   ``playbook_runs``. Nullable — pre-A5 rows never went through the gate,
   and neither do inactive-playbook runs.
"""
from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE must run outside a transaction; execute directly on the
    # connection with a fresh statement.
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE playbook_run_status ADD VALUE IF NOT EXISTS 'awaiting_approval'"
        )

    op.execute(
        """
        ALTER TABLE playbook_runs
            ADD COLUMN approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN approved_at TIMESTAMPTZ,
            ADD COLUMN approval_reason TEXT,
            ADD COLUMN rejected_by UUID REFERENCES users(id) ON DELETE SET NULL,
            ADD COLUMN rejected_at TIMESTAMPTZ,
            ADD COLUMN rejection_reason TEXT
        """
    )
    op.execute(
        "CREATE INDEX ix_playbook_runs_awaiting "
        "ON playbook_runs (engagement_id) "
        "WHERE status = 'awaiting_approval'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_playbook_runs_awaiting")
    op.execute(
        """
        ALTER TABLE playbook_runs
            DROP COLUMN IF EXISTS rejection_reason,
            DROP COLUMN IF EXISTS rejected_at,
            DROP COLUMN IF EXISTS rejected_by,
            DROP COLUMN IF EXISTS approval_reason,
            DROP COLUMN IF EXISTS approved_at,
            DROP COLUMN IF EXISTS approved_by
        """
    )
    # Postgres has no ALTER TYPE ... DROP VALUE. Rebuilding the enum here
    # would need to rewrite every row + break other refs. Downgrades in
    # dev accept a leftover enum member; production never downgrades enums.
