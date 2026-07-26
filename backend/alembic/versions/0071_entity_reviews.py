"""Add durable entity review dispositions.

Revision ID: 0071
Revises: 0070
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0071"
down_revision: str | None = "0070"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    disposition = postgresql.ENUM(
        "kept", "excluded", name="entity_review_disposition", create_type=False
    )
    disposition.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "entity_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("engagement_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("normalized_value", sa.String(length=500), nullable=False),
        sa.Column("display_value", sa.String(length=500), nullable=False),
        sa.Column("disposition", disposition, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(["engagement_id"], ["engagements.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "engagement_id",
            "entity_type",
            "normalized_value",
            name="uq_entity_review_identity",
        ),
    )
    op.create_index("ix_entity_reviews_engagement_id", "entity_reviews", ["engagement_id"])
    op.create_index("ix_entity_reviews_disposition", "entity_reviews", ["disposition"])
    op.create_table(
        "entity_review_scope_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope_item_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["entity_review_id"], ["entity_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scope_item_id"], ["scope_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entity_review_scope_links_review_id",
        "entity_review_scope_links",
        ["entity_review_id"],
    )
    op.create_index(
        "ix_entity_review_scope_links_scope_id",
        "entity_review_scope_links",
        ["scope_item_id"],
    )
    op.create_index(
        "uq_entity_review_scope_link_active",
        "entity_review_scope_links",
        ["entity_review_id", "scope_item_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_table(
        "entity_review_finding_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entity_review_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("finding_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_exclusion", sa.String(length=32), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["entity_review_id"], ["entity_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entity_review_finding_links_review_id",
        "entity_review_finding_links",
        ["entity_review_id"],
    )
    op.create_index(
        "ix_entity_review_finding_links_finding_id",
        "entity_review_finding_links",
        ["finding_id"],
    )
    op.create_index(
        "uq_entity_review_finding_link_active",
        "entity_review_finding_links",
        ["entity_review_id", "finding_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("entity_review_finding_links")
    op.drop_table("entity_review_scope_links")
    op.drop_table("entity_reviews")
    postgresql.ENUM(name="entity_review_disposition").drop(op.get_bind(), checkfirst=True)
