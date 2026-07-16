"""work_item_comments — analyst discussion on a work item

Revision ID: 0051
Revises: 0050

Adds work_item_comments: an analyst comment thread on a work item.
work_item_id is SET NULL on work-item deletion so the comment survives as a
tombstone (matches the cross-record-nav convention for durable references);
engagement_id is CASCADE and indexed for engagement-scoped queries + audit
rollup. (work_item_id, created_at) is the hot read path for rendering a thread.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_item_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_work_item_comments_work_item_created",
        "work_item_comments",
        ["work_item_id", "created_at"],
    )
    op.create_index(
        "ix_work_item_comments_engagement",
        "work_item_comments",
        ["engagement_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_item_comments_engagement", table_name="work_item_comments")
    op.drop_index(
        "ix_work_item_comments_work_item_created", table_name="work_item_comments"
    )
    op.drop_table("work_item_comments")
