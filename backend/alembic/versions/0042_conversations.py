"""finding conversations — persisted AI assistant bubbles

Phase 2 of the finding pane adds a finding-scoped chatbot rail. Store the
conversation and message bubbles so reloads retain context and Phase 3 can hang
consent-gated action payloads off assistant messages.

Revision ID: 0042
Revises: 0041
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(200), nullable=True),
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
    op.create_index("ix_conversations_engagement_id", "conversations", ["engagement_id"])
    op.create_index("ix_conversations_finding_id", "conversations", ["finding_id"])
    op.create_index(
        "ix_conversations_created_by_user_id",
        "conversations",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_conversations_finding_created_by_updated",
        "conversations",
        ["finding_id", "created_by_user_id", sa.text("updated_at DESC")],
    )

    op.create_table(
        "conversation_messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("action_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_conversation_messages_conversation_id",
        "conversation_messages",
        ["conversation_id"],
    )
    op.create_index("ix_conversation_messages_role", "conversation_messages", ["role"])
    op.create_index(
        "ix_conversation_messages_execution_id",
        "conversation_messages",
        ["execution_id"],
    )
    op.create_index(
        "ix_conversation_messages_created_at",
        "conversation_messages",
        ["created_at"],
    )
    op.create_index(
        "ix_conversation_messages_conversation_created",
        "conversation_messages",
        ["conversation_id", sa.text("created_at ASC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_conversation_messages_conversation_created",
        table_name="conversation_messages",
    )
    op.drop_index("ix_conversation_messages_created_at", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_execution_id", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_role", table_name="conversation_messages")
    op.drop_index(
        "ix_conversation_messages_conversation_id",
        table_name="conversation_messages",
    )
    op.drop_table("conversation_messages")

    op.drop_index("ix_conversations_finding_created_by_updated", table_name="conversations")
    op.drop_index("ix_conversations_created_by_user_id", table_name="conversations")
    op.drop_index("ix_conversations_finding_id", table_name="conversations")
    op.drop_index("ix_conversations_engagement_id", table_name="conversations")
    op.drop_table("conversations")
