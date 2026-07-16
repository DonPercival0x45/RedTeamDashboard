"""work_items structured target refs (scope_item_id, entity_id)

Revision ID: 0050
Revises: 0049

Gives WorkItem a concrete, in-scope target so strategist-generated queue items
are actionable/dispatchable instead of prose-only. Mirrors the existing
CoverageItem.scope_item_id pattern. Both nullable, SET NULL on target deletion.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "work_items",
        sa.Column(
            "scope_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("scope_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "work_items",
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_work_items_scope_item_id", "work_items", ["scope_item_id"])
    op.create_index("ix_work_items_entity_id", "work_items", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_work_items_entity_id", table_name="work_items")
    op.drop_index("ix_work_items_scope_item_id", table_name="work_items")
    op.drop_column("work_items", "entity_id")
    op.drop_column("work_items", "scope_item_id")
