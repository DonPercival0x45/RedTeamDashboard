"""command outbox and durable approval resume lineage

Revision ID: 0054
Revises: 0053
"""

from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE command_outbox_status AS ENUM ('pending', 'published', 'cancelled', 'failed')"
    )
    op.execute("CREATE TYPE processing_receipt_status AS ENUM ('processing', 'completed')")
    op.execute(
        """
        CREATE TABLE command_outbox (
            id UUID PRIMARY KEY,
            idempotency_key VARCHAR(255) NOT NULL UNIQUE,
            engagement_id UUID NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
            task_id UUID NULL REFERENCES tasks(id) ON DELETE CASCADE,
            thread_id VARCHAR(200) NULL,
            delivery_kind VARCHAR(30) NOT NULL,
            stream_name VARCHAR(300) NOT NULL,
            encoded_payload JSONB NOT NULL,
            status command_outbox_status NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NULL,
            next_attempt_at TIMESTAMPTZ NULL,
            published_at TIMESTAMPTZ NULL,
            cancelled_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_command_outbox_engagement_id ON command_outbox (engagement_id)")
    op.execute("CREATE INDEX ix_command_outbox_task_id ON command_outbox (task_id)")
    op.execute("CREATE INDEX ix_command_outbox_thread_id ON command_outbox (thread_id)")
    op.execute("CREATE INDEX ix_command_outbox_next_attempt_at ON command_outbox (next_attempt_at)")
    op.execute("CREATE INDEX ix_command_outbox_status ON command_outbox (status)")
    op.execute(
        """
        CREATE TABLE processing_receipts (
            delivery_id VARCHAR(500) PRIMARY KEY,
            kind VARCHAR(30) NOT NULL,
            engagement_id UUID NOT NULL REFERENCES engagements(id) ON DELETE CASCADE,
            thread_id VARCHAR(200) NULL,
            agent_execution_id UUID NULL,
            status processing_receipt_status NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            last_error TEXT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_processing_receipts_thread_id ON processing_receipts (thread_id)")
    op.execute(
        "CREATE INDEX ix_processing_receipts_agent_execution_id "
        "ON processing_receipts (agent_execution_id)"
    )
    op.execute("ALTER TABLE approvals ADD COLUMN tool_call_id VARCHAR(200) NULL")
    op.execute("ALTER TABLE approvals ADD COLUMN run_model JSONB NULL")
    op.execute("ALTER TABLE approvals ADD COLUMN run_context JSONB NULL")
    op.execute("ALTER TABLE approvals ADD COLUMN acting_user_id UUID NULL")
    op.execute(
        "ALTER TABLE approvals ADD CONSTRAINT fk_approvals_acting_user_id "
        "FOREIGN KEY (acting_user_id) REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX ix_approvals_acting_user_id ON approvals (acting_user_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_approvals_acting_user_id")
    op.execute("ALTER TABLE approvals DROP CONSTRAINT IF EXISTS fk_approvals_acting_user_id")
    op.execute("ALTER TABLE approvals DROP COLUMN IF EXISTS acting_user_id")
    op.execute("ALTER TABLE approvals DROP COLUMN IF EXISTS run_context")
    op.execute("ALTER TABLE approvals DROP COLUMN IF EXISTS run_model")
    op.execute("ALTER TABLE approvals DROP COLUMN IF EXISTS tool_call_id")
    op.execute("DROP TABLE IF EXISTS processing_receipts")
    op.execute("DROP TABLE IF EXISTS command_outbox")
    op.execute("DROP TYPE IF EXISTS processing_receipt_status")
    op.execute("DROP TYPE IF EXISTS command_outbox_status")
