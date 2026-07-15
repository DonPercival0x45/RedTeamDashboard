"""non-destructive entity duplicate management

Revision ID: 0048
Revises: 0047
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("entities", sa.Column("suppressed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "entities",
        sa.Column(
            "suppressed_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("entities", sa.Column("suppression_reason", sa.Text()))
    op.add_column(
        "entities",
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
    )
    op.create_index("ix_entities_suppressed_at", "entities", ["suppressed_at"])

    op.create_table(
        "entity_groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "engagement_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("engagements.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "canonical_entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="SET NULL"),
        ),
        sa.Column("label", sa.String(300)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
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
    op.create_index("ix_entity_groups_engagement_id", "entity_groups", ["engagement_id"])

    op.create_table(
        "entity_group_members",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entity_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "entity_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "added_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("entity_id", name="uq_entity_group_members_entity_id"),
    )
    op.create_index("ix_entity_group_members_group_id", "entity_group_members", ["group_id"])


def downgrade() -> None:
    op.drop_index("ix_entity_group_members_group_id", table_name="entity_group_members")
    op.drop_table("entity_group_members")
    op.drop_index("ix_entity_groups_engagement_id", table_name="entity_groups")
    op.drop_table("entity_groups")
    op.drop_index("ix_entities_suppressed_at", table_name="entities")
    op.drop_column("entities", "row_version")
    op.drop_column("entities", "suppression_reason")
    op.drop_column("entities", "suppressed_by_user_id")
    op.drop_column("entities", "suppressed_at")
