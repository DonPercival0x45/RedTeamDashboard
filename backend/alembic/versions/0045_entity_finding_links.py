"""link persistent entities to source findings

Revision ID: 0045
Revises: 0044
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_finding_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
            "entity_id",
            "finding_id",
            name="uq_entity_finding_links_entity_finding",
        ),
    )
    op.create_index(
        "ix_entity_finding_links_entity_id",
        "entity_finding_links",
        ["entity_id"],
    )
    op.create_index(
        "ix_entity_finding_links_finding_id",
        "entity_finding_links",
        ["finding_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_entity_finding_links_finding_id",
        table_name="entity_finding_links",
    )
    op.drop_index(
        "ix_entity_finding_links_entity_id",
        table_name="entity_finding_links",
    )
    op.drop_table("entity_finding_links")
